import asyncio
import json
import queue
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import (
    CLAUDE_MD_FILE, ARCHITECTURE_FILE, STACK_FILE, BUGS_FILE, HABITS_FILE,
    ANALYTICS_FILES, NTFY_TOPIC, ensure_dirs,
)
from app.services.executor_service import ExecutorService
from app.services.mobile_service import MobileService
from app.services.mobile_listener_service import MobileListenerService
from app.services.token_monitor_service import TokenMonitorService
from app.services.usage_parse_service import UsageParseService
from app.services.watcher_service import WatcherService
from app.services.claude_usage_service import ClaudeUsageService

# ── Init ────────────────────────────────────────────────────────
ensure_dirs()

app = FastAPI(title="Claude Control Web", version="1.0")

# ── Log queue (threads → async event loop) ──────────────────────
log_queue: queue.Queue = queue.Queue()
command_history: list[dict] = []


def sync_logger(msg: str):
    log_queue.put(msg)
    print(msg)


# ── Services ─────────────────────────────────────────────────────
mobile_svc = MobileService(topic=NTFY_TOPIC, logger=sync_logger)
executor_svc = ExecutorService(logger=sync_logger, enable_execution=True)
watcher_svc = WatcherService(mobile_service=mobile_svc, executor=executor_svc, logger=sync_logger)
listener_svc = MobileListenerService(
    topic=NTFY_TOPIC,
    executor=executor_svc,
    mobile_service=mobile_svc,
    logger=sync_logger,
)
token_svc = TokenMonitorService(logger=sync_logger)
usage_svc = UsageParseService()
claude_usage_svc = ClaudeUsageService(mobile_service=mobile_svc, logger=sync_logger)

# Cross-inject
executor_svc.watcher = watcher_svc
listener_svc.watcher = watcher_svc
watcher_svc.claude_usage = claude_usage_svc  # watcher notifie usage service lors d'un rate limit


def on_command(msg: str):
    command_history.insert(0, {"ts": datetime.now().isoformat(), "msg": msg})
    del command_history[50:]


listener_svc.set_command_callback(on_command)

# ── WebSocket manager ────────────────────────────────────────────
class WsManager:
    def __init__(self):
        self._clients: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._clients.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self._clients:
            self._clients.remove(ws)

    async def broadcast(self, data: dict):
        msg = json.dumps(data)
        dead = []
        for ws in self._clients:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.remove(ws)


ws_manager = WsManager()


# ── Startup ──────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    watcher_svc.start()
    listener_svc.start()
    asyncio.create_task(_log_broadcaster())
    asyncio.create_task(_status_broadcaster())
    sync_logger("[SERVER] Claude Control Web démarré sur http://localhost:8765")


async def _log_broadcaster():
    loop = asyncio.get_event_loop()
    while True:
        try:
            msg = await loop.run_in_executor(None, lambda: log_queue.get(timeout=0.1))
            await ws_manager.broadcast({"type": "log", "msg": msg})
        except Exception:
            await asyncio.sleep(0.05)


async def _status_broadcaster():
    while True:
        await asyncio.sleep(5)
        try:
            await ws_manager.broadcast({"type": "status", "data": _get_status()})
        except Exception:
            pass


def _get_status() -> dict:
    usage = claude_usage_svc.get_status()
    return {
        "watcher": watcher_svc.get_status(),
        "executor": executor_svc.get_status(),
        "listener": {"running": listener_svc.running, "topic": listener_svc.topic},
        "claude_usage": {
            "subscription": usage["plan"].get("subscription_type"),
            "rate_limit_tier": usage["plan"].get("rate_limit_tier"),
            "rate_limited": usage["rate_limited"],
            "reset_at": usage["reset_at"],
            "remaining": usage["remaining_until_reset"],
            "today": usage["today"],
        },
        "server_time": datetime.now().strftime("%H:%M:%S"),
    }


# ── WebSocket endpoint ───────────────────────────────────────────
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    await ws.send_text(json.dumps({"type": "status", "data": _get_status()}))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


# ── REST: Status ─────────────────────────────────────────────────
@app.get("/api/status")
def get_status():
    return _get_status()


# ── REST: Claude Usage ───────────────────────────────────────────
@app.get("/api/claude-usage")
def get_claude_usage():
    return claude_usage_svc.get_status()


@app.get("/api/claude-usage/plan")
def get_plan():
    return claude_usage_svc.get_plan_info()


@app.get("/api/claude-usage/stats")
def get_stats():
    return claude_usage_svc.get_recent_stats()


# ── REST: Watcher ────────────────────────────────────────────────
@app.post("/api/watcher/autonomous")
def launch_autonomous(body: dict = {}):
    prompt = body.get("prompt") or None
    watcher_svc.launch_autonomous(prompt)
    return {"ok": True}


@app.post("/api/watcher/cancel")
def cancel_restart():
    watcher_svc.cancel_restart()
    return {"ok": True}


# ── REST: Executor ────────────────────────────────────────────────
class RunRequest(BaseModel):
    prompt: str
    model: Optional[str] = None


@app.post("/api/run")
def run_claude(req: RunRequest):
    result = executor_svc.run_claude(req.prompt, req.model)
    return {"output": result or "", "ok": result is not None}


# ── REST: Mobile / Notify ─────────────────────────────────────────
class NotifyRequest(BaseModel):
    title: str
    message: str
    priority: int = 3


@app.post("/api/notify")
def send_notification(req: NotifyRequest):
    mobile_svc.notify(req.title, req.message, req.priority)
    return {"ok": True}


@app.post("/api/listener/start")
def start_listener():
    listener_svc.start()
    return {"ok": True}


@app.post("/api/listener/stop")
def stop_listener():
    listener_svc.stop()
    return {"ok": True}


@app.get("/api/commands")
def get_commands():
    return {"commands": command_history}


@app.post("/api/commands/simulate")
def simulate_command(body: dict):
    msg = body.get("message", "").strip()
    if msg:
        listener_svc.handle_command(msg)
    return {"ok": True}


# ── REST: Memory files ─────────────────────────────────────────────
MEMORY_MAP = {
    "claude_md": CLAUDE_MD_FILE,
    "architecture": ARCHITECTURE_FILE,
    "stack": STACK_FILE,
    "bugs": BUGS_FILE,
    "habits": HABITS_FILE,
}


@app.get("/api/memory")
def get_memory_index():
    return {
        key: {
            "exists": path.exists(),
            "size": path.stat().st_size if path.exists() else 0,
            "name": path.name,
        }
        for key, path in MEMORY_MAP.items()
    }


@app.get("/api/memory/{key}")
def get_memory_file(key: str):
    path = MEMORY_MAP.get(key)
    if not path:
        return JSONResponse({"error": "Unknown key"}, status_code=404)
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return {"key": key, "content": content, "path": str(path)}


@app.put("/api/memory/{key}")
def save_memory_file(key: str, body: dict):
    path = MEMORY_MAP.get(key)
    if not path:
        return JSONResponse({"error": "Unknown key"}, status_code=404)
    content = body.get("content", "")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    sync_logger(f"[MEMORY] {path.name} sauvegardé ({len(content)} chars)")
    return {"ok": True, "size": len(content)}


# ── REST: History pipeline ─────────────────────────────────────────
def _get_history_service():
    from app.services.history_service import HistoryService
    return HistoryService(logger=sync_logger)


@app.post("/api/history/merge")
def history_merge():
    return _get_history_service().merge_history()


@app.post("/api/history/clean")
def history_clean():
    return _get_history_service().clean_history()


@app.post("/api/history/analyze")
def history_analyze():
    return _get_history_service().analyze_history()


@app.post("/api/history/memory")
def history_memory():
    return _get_history_service().generate_memory()


@app.post("/api/history/all")
def history_all():
    return _get_history_service().run_all()


# ── REST: Optimization ─────────────────────────────────────────────
@app.get("/api/optimization")
def get_optimization():
    from app.services.optimization_service import OptimizationService
    return OptimizationService(logger=sync_logger).build_recommendations()


# ── REST: Tokens ───────────────────────────────────────────────────
@app.get("/api/tokens")
def get_tokens():
    return token_svc.capture()


@app.post("/api/tokens/parse")
def parse_tokens(body: dict):
    text = body.get("text", "")
    return usage_svc.parse(text)


# ── Static files ───────────────────────────────────────────────────
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
def index():
    return FileResponse(str(static_dir / "index.html"))
