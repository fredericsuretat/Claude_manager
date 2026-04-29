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
from app.services.live_usage_service import LiveUsageService
from app.services.terminal_service import TerminalService

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
live_usage_svc = LiveUsageService(mobile_service=mobile_svc, logger=sync_logger)

# Cross-inject
executor_svc.watcher = watcher_svc
listener_svc.watcher = watcher_svc
watcher_svc.claude_usage = claude_usage_svc  # watcher notifie usage service lors d'un rate limit

# Terminal service (créé après ws_manager — initialisé dans startup)
terminal_svc: TerminalService = None  # type: ignore


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
        await self.broadcast_raw(json.dumps(data))

    async def broadcast_raw(self, msg: str):
        dead = []
        for ws in self._clients:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in self._clients:
                self._clients.remove(ws)


ws_manager = WsManager()


# ── Startup ──────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    global terminal_svc
    terminal_svc = TerminalService(
        ws_manager=ws_manager,
        watcher=watcher_svc,
        logger=sync_logger,
        loop=asyncio.get_event_loop(),
        live_usage=live_usage_svc,
    )
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
    live = live_usage_svc.get_cached()
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
            "live": live,  # session_pct, week_pct, session_reset_str, week_reset_str
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


# ── REST: Terminal (PTY) ─────────────────────────────────────────
@app.post("/api/terminal/start")
def terminal_start(body: dict = {}):
    autonomous = body.get("autonomous", False)
    rows = int(body.get("rows", 40))
    cols = int(body.get("cols", 220))
    return terminal_svc.start(autonomous=autonomous, rows=rows, cols=cols)


@app.post("/api/terminal/stop")
def terminal_stop():
    return terminal_svc.stop()


@app.post("/api/terminal/write")
def terminal_write(body: dict):
    data = body.get("data", "")
    return terminal_svc.write(data)


@app.post("/api/terminal/send")
def terminal_send(body: dict):
    text = body.get("text", "")
    return terminal_svc.send_line(text)


@app.post("/api/terminal/interrupt")
def terminal_interrupt():
    return terminal_svc.interrupt()


@app.post("/api/terminal/resize")
def terminal_resize(body: dict):
    rows = int(body.get("rows", 40))
    cols = int(body.get("cols", 220))
    rows = max(5, min(rows, 200))
    cols = max(20, min(cols, 500))
    terminal_svc.resize(rows, cols)
    return {"ok": True, "rows": rows, "cols": cols}


@app.get("/api/terminal/status")
def terminal_status():
    return terminal_svc.get_status() if terminal_svc else {"state": "idle", "alive": False}


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


@app.get("/api/claude-usage/live")
def get_live_usage():
    return live_usage_svc.get_cached()


@app.post("/api/claude-usage/live/refresh")
def refresh_live_usage():
    """Envoie /usage au terminal actif pour déclencher une capture."""
    result = terminal_svc.send_line("/usage") if terminal_svc else {"ok": False}
    cached = live_usage_svc.get_cached()
    return {"triggered": result.get("ok", False), **cached}


@app.get("/api/claude-usage/live/debug")
def debug_live_usage():
    """Retourne le buffer PTY brut (debug parser)."""
    return {"buf": live_usage_svc.get_debug_buf()}


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
    skip_permissions: bool = False


@app.post("/api/run")
def run_claude(req: RunRequest):
    result = executor_svc.run_claude(req.prompt, req.model, req.skip_permissions)
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


# ── REST: Scheduled notifications ─────────────────────────────────
import threading as _threading
import json as _json
from app.config import SCHEDULED_NOTIFS_FILE

_sched_lock = _threading.Lock()


def _load_scheduled() -> list:
    try:
        if SCHEDULED_NOTIFS_FILE.exists():
            return _json.loads(SCHEDULED_NOTIFS_FILE.read_text())
    except Exception:
        pass
    return []


def _save_scheduled(notifs: list):
    try:
        SCHEDULED_NOTIFS_FILE.write_text(_json.dumps(notifs, ensure_ascii=False, indent=2))
    except Exception as e:
        sync_logger(f"[NOTIF] Save failed: {e}")


_scheduled_notifs: list[dict] = _load_scheduled()


def _run_scheduler():
    while True:
        _threading.Event().wait(30)
        now = datetime.now()
        with _sched_lock:
            fired = []
            for n in _scheduled_notifs:
                try:
                    target = datetime.fromisoformat(n["at"])
                    if now >= target:
                        mobile_svc.notify(n.get("title", "CC"), n.get("message", ""), n.get("priority", 3))
                        sync_logger(f"[NOTIF] Notification planifiée envoyée: {n.get('title')}")
                        fired.append(n)
                except Exception:
                    fired.append(n)
            for n in fired:
                _scheduled_notifs.remove(n)
            if fired:
                _save_scheduled(_scheduled_notifs)


_threading.Thread(target=_run_scheduler, daemon=True).start()


@app.get("/api/notifications/scheduled")
def get_scheduled():
    with _sched_lock:
        return {"notifications": list(_scheduled_notifs)}


@app.post("/api/notifications/schedule")
def schedule_notification(body: dict):
    title = body.get("title", "CC")
    message = body.get("message", "")
    at = body.get("at", "")
    priority = body.get("priority", 3)
    if not at or not message:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "at et message requis"}, status_code=400)
    try:
        datetime.fromisoformat(at)
    except ValueError:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "format at invalide (ISO 8601)"}, status_code=400)
    entry = {"title": title, "message": message, "at": at, "priority": priority}
    with _sched_lock:
        _scheduled_notifs.append(entry)
        _save_scheduled(_scheduled_notifs)
    sync_logger(f"[NOTIF] Planifiée pour {at}: {title}")
    return {"ok": True, "scheduled": entry}


@app.delete("/api/notifications/scheduled/{idx}")
def delete_scheduled(idx: int):
    with _sched_lock:
        if 0 <= idx < len(_scheduled_notifs):
            removed = _scheduled_notifs.pop(idx)
            _save_scheduled(_scheduled_notifs)
            return {"ok": True, "removed": removed}
    from fastapi.responses import JSONResponse
    return JSONResponse({"error": "Index invalide"}, status_code=404)


# ── REST: Service self-management ─────────────────────────────────
import subprocess as _subprocess

SERVICE_NAME = "claude-control"


@app.get("/api/service/status")
def service_status():
    try:
        r = _subprocess.run(
            ["sudo", "-n", "/usr/bin/systemctl", "status", SERVICE_NAME, "--no-pager"],
            capture_output=True, text=True, timeout=5,
        )
        active = "Active: active" in r.stdout
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        return {"active": active, "lines": lines[-15:], "returncode": r.returncode}
    except Exception as e:
        return {"active": False, "lines": [str(e)], "returncode": -1}


@app.post("/api/service/restart")
def service_restart():
    try:
        _subprocess.run(
            ["sudo", "-n", "/usr/bin/systemctl", "reset-failed", SERVICE_NAME],
            capture_output=True, timeout=5,
        )
        r = _subprocess.run(
            ["sudo", "-n", "/usr/bin/systemctl", "restart", SERVICE_NAME],
            capture_output=True, text=True, timeout=15,
        )
        sync_logger(f"[SERVICE] restart → code {r.returncode}")
        return {"ok": r.returncode == 0, "stderr": r.stderr.strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/service/logs")
def service_logs():
    try:
        r = _subprocess.run(
            ["sudo", "-n", "/usr/bin/journalctl", "-u", SERVICE_NAME,
             "-n", "100", "--no-pager", "--output=short"],
            capture_output=True, text=True, timeout=5,
        )
        return {"lines": r.stdout.splitlines()}
    except Exception as e:
        return {"lines": [str(e)]}


# ── REST: MCP Control ─────────────────────────────────────────────
from app.services.mcp_service import McpService as _McpService
_mcp_svc = _McpService()


@app.get("/api/mcp/status")
def mcp_status():
    return _mcp_svc.get_status()


@app.post("/api/mcp/enable")
def mcp_enable(body: dict):
    name = body.get("name", "").strip()
    config = body.get("config", {})
    if not name:
        return JSONResponse({"error": "name requis"}, status_code=400)
    return _mcp_svc.enable_server(name, config)


@app.post("/api/mcp/disable")
def mcp_disable(body: dict):
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "name requis"}, status_code=400)
    return _mcp_svc.disable_server(name)


@app.post("/api/mcp/profile/apply")
def mcp_apply_profile(body: dict):
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "name requis"}, status_code=400)
    return _mcp_svc.apply_profile(name)


@app.post("/api/mcp/profile/save")
def mcp_save_profile(body: dict):
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "name requis"}, status_code=400)
    return _mcp_svc.save_profile(name)


@app.delete("/api/mcp/profile/{name}")
def mcp_delete_profile(name: str):
    return _mcp_svc.delete_profile(name)


# ── Static files ───────────────────────────────────────────────────
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
def index():
    return FileResponse(str(static_dir / "index.html"))
