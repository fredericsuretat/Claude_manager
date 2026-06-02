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


# ── REST: Memory Explorer (Memory Control Center) ─────────────────
from app.services.memory_explorer_service import MemoryExplorerService as _MemoryExplorerService
_memex_svc = _MemoryExplorerService()

# ── Stats tracker (compteurs + tokens économisés) ─────────────────
import threading as _memex_thr
from app.config import RUNTIME_DIR as _MEMEX_RT
_MEMEX_STATS_FILE = _MEMEX_RT / "memex_stats.json"
_memex_stats_lock = _memex_thr.Lock()

def _memex_load_stats() -> dict:
    try:
        if _MEMEX_STATS_FILE.exists():
            return json.loads(_MEMEX_STATS_FILE.read_text())
    except Exception:
        pass
    return {
        "calls": {},           # endpoint -> count
        "tokens_full": 0,      # tokens qu'auraient coûté des Read complets
        "tokens_actual": 0,    # tokens effectivement renvoyés
        "tokens_saved": 0,     # estimation économie
        "since": datetime.now().isoformat(),
        "files": {},           # "root::rel" -> {calls, tokens_saved, by_endpoint, last}
    }

_memex_stats: dict = _memex_load_stats()

def _memex_save_stats():
    try:
        _MEMEX_STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _MEMEX_STATS_FILE.write_text(json.dumps(_memex_stats, ensure_ascii=False, indent=2))
    except Exception as e:
        sync_logger(f"[MEMEX] save stats failed: {e}")

def _memex_track(endpoint: str, full_tokens: int = 0, actual_tokens: int = 0, file_key: str | None = None):
    with _memex_stats_lock:
        _memex_stats["calls"][endpoint] = _memex_stats["calls"].get(endpoint, 0) + 1
        _memex_stats["tokens_full"] += full_tokens
        _memex_stats["tokens_actual"] += actual_tokens
        saved = max(0, full_tokens - actual_tokens)
        _memex_stats["tokens_saved"] += saved
        # per-file heatmap
        if file_key:
            files = _memex_stats.setdefault("files", {})
            entry = files.setdefault(file_key, {"calls": 0, "tokens_saved": 0, "by_endpoint": {}, "last": None})
            entry["calls"] += 1
            entry["tokens_saved"] += saved
            entry["by_endpoint"][endpoint] = entry["by_endpoint"].get(endpoint, 0) + 1
            entry["last"] = datetime.now().isoformat()
        # Persist every call — stats JSON is tiny, restarts shouldn't lose data.
        _memex_save_stats()


@app.get("/api/memory-explorer/tree")
def memex_tree():
    return _memex_svc.tree()


@app.get("/api/memory-explorer/stats")
def memex_stats():
    return _memex_svc.stats()


@app.get("/api/memory-explorer/file")
def memex_get(root: str, rel: str):
    try:
        r = _memex_svc.read(root, rel)
        # track full reads (no token savings, but useful for heatmap to spot files
        # that should be skimmed/sectioned instead)
        _memex_track("read", 0, 0, file_key=f"{root}::{rel}")
        return r
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.put("/api/memory-explorer/file")
def memex_put(body: dict):
    root = body.get("root", "")
    rel = body.get("rel", "")
    content = body.get("content", "")
    try:
        result = _memex_svc.write(root, rel, content)
        sync_logger(f"[MEMEX] write {root}/{rel} ({result.get('size')} bytes)")
        return result
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/memory-explorer/file")
def memex_create(body: dict):
    root = body.get("root", "")
    rel = body.get("rel", "")
    content = body.get("content", "")
    try:
        result = _memex_svc.create(root, rel, content)
        if result.get("ok"):
            sync_logger(f"[MEMEX] create {root}/{rel}")
        return result
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.delete("/api/memory-explorer/file")
def memex_delete(root: str, rel: str):
    try:
        result = _memex_svc.delete(root, rel)
        if result.get("ok"):
            sync_logger(f"[MEMEX] delete {root}/{rel}")
        return result
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/memory-explorer/search")
def memex_search(q: str = "", limit: int = 200):
    return _memex_svc.search(q, max_results=limit)


@app.get("/api/memory-explorer/recent")
def memex_recent(limit: int = 40):
    return _memex_svc.recent(limit=limit)


@app.get("/api/memory-explorer/graph")
def memex_graph():
    return _memex_svc.graph()


@app.post("/api/memory-explorer/refresh")
def memex_refresh():
    _memex_svc.invalidate_cache()
    return {"ok": True}


# ── Roadmap endpoints (token-saving) ─────────────────────────────
@app.get("/api/memory-explorer/skim")
def memex_skim(root: str, rel: str, body_lines: int = 8):
    try:
        r = _memex_svc.skim(root, rel, body_lines=body_lines)
        if "approx_tokens_full" in r:
            _memex_track("skim", r["approx_tokens_full"], r["approx_tokens_skim"], file_key=f"{root}::{rel}")
        return r
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/memory-explorer/search-meta")
def memex_search_meta(q: str = "", limit: int = 500):
    r = _memex_svc.search_meta(q, max_results=limit)
    # heuristique: 1 search-meta économise ~50 tok par fichier (vs search avec snippets)
    saved = r.get("count", 0) * 50
    _memex_track("search-meta", saved, 0)
    return r


@app.get("/api/memory-explorer/search-headings")
def memex_search_headings(q: str = "", limit: int = 200):
    r = _memex_svc.search_headings(q, max_results=limit)
    # heuristique: chaque heading match évite ~200 tok de skim
    saved = r.get("count", 0) * 200
    _memex_track("search-headings", saved, 0)
    return r


@app.get("/api/memory-explorer/index")
def memex_index(root: str, name: str = "MEMORY.md"):
    r = _memex_svc.parse_index(root, name)
    _memex_track("index")
    return r


@app.get("/api/memory-explorer/section")
def memex_section(root: str, rel: str, heading: str):
    try:
        r = _memex_svc.read_section(root, rel, heading)
        if "approx_tokens" in r and "approx_tokens_full_file" in r:
            _memex_track("section", r["approx_tokens_full_file"], r["approx_tokens"], file_key=f"{root}::{rel}")
        return r
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/memory-explorer/stats-live")
def memex_stats_live():
    with _memex_stats_lock:
        snap = dict(_memex_stats)
        snap["total_calls"] = sum(snap["calls"].values())
    return snap


@app.post("/api/memory-explorer/stats-live/reset")
def memex_stats_reset():
    with _memex_stats_lock:
        _memex_stats["calls"] = {}
        _memex_stats["tokens_full"] = 0
        _memex_stats["tokens_actual"] = 0
        _memex_stats["tokens_saved"] = 0
        _memex_stats["files"] = {}
        _memex_stats["since"] = datetime.now().isoformat()
        _memex_save_stats()
    return {"ok": True}


@app.post("/api/memory-explorer/create-memory")
def memex_create_memory(body: dict):
    """Crée un .md typé (user/feedback/project/reference) avec frontmatter
    + met à jour le MEMORY.md du root. Body : {root, slug, name, description, type, body, index_name?}"""
    try:
        r = _memex_svc.create_memory(
            root_id=body.get("root", ""),
            slug=body.get("slug", ""),
            name=body.get("name", ""),
            description=body.get("description", ""),
            mtype=body.get("type", "project"),
            body=body.get("body", ""),
            index_name=body.get("index_name", "MEMORY.md"),
        )
        if r.get("ok"):
            sync_logger(f"[MEMEX] create_memory {r.get('root')}/{r.get('rel')} type={r.get('type')}")
        return r
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/memory-explorer/heatmap")
def memex_heatmap(limit: int = 30):
    """Top consulted .md files. Useful to identify candidates for better indexing."""
    with _memex_stats_lock:
        files = dict(_memex_stats.get("files", {}))
    items = []
    for key, entry in files.items():
        root, _, rel = key.partition("::")
        items.append({
            "root": root,
            "rel": rel,
            "calls": entry.get("calls", 0),
            "tokens_saved": entry.get("tokens_saved", 0),
            "by_endpoint": entry.get("by_endpoint", {}),
            "last": entry.get("last"),
        })
    items.sort(key=lambda x: (x["calls"], x["tokens_saved"]), reverse=True)
    return {"items": items[:limit], "total_tracked_files": len(items)}


@app.get("/api/memory-explorer/index-health")
def memex_index_health(include_claude_md: bool = False):
    """Aggregate parse_index() across all roots.

    By default only MEMORY.md is audited — CLAUDE.md is instruction text, not
    a link-format index, so auditing it would flag every .md as orphan.
    Pass ?include_claude_md=true to include them anyway (useful only if you
    use CLAUDE.md as an index).
    """
    tree = _memex_svc.tree(include_empty=True)
    reports = []
    total_missing = 0
    total_orphans = 0
    names = ["MEMORY.md"]
    if include_claude_md:
        names.append("CLAUDE.md")
    for root in tree.get("roots", []):
        root_id = root.get("id")
        for index_name in names:
            try:
                parsed = _memex_svc.parse_index(root_id, index_name)
            except Exception:
                continue
            if not parsed or parsed.get("error"):
                continue
            entries = parsed.get("entries", []) or []
            # Skip "indexes" with 0 entries — these are likely not really
            # index files but accidentally named files.
            if not entries:
                continue
            missing = parsed.get("missing", []) or []
            orphans = parsed.get("orphans", []) or []
            reports.append({
                "root": root_id,
                "root_label": root.get("label"),
                "index": index_name,
                "entries": len(entries),
                "missing": missing,
                "orphans": orphans,
                "missing_count": len(missing),
                "orphans_count": len(orphans),
            })
            total_missing += len(missing)
            total_orphans += len(orphans)
    return {
        "reports": reports,
        "totals": {
            "indexes": len(reports),
            "missing": total_missing,
            "orphans": total_orphans,
        },
    }


# ── Roadmap metadata (consommé par l'UI) ─────────────────────────
MEMEX_ROADMAP = [
    {
        "id": "skim",
        "title": "1. Skim — aperçu léger",
        "status": "done",
        "endpoint": "GET /api/memory-explorer/skim?root=&rel=",
        "why": "Décider si un .md vaut la peine d'être lu en entier, sans payer 2000 tokens à chaque vérif.",
        "returns": "frontmatter + headings + 8 premières lignes utiles",
        "savings": "~10x tokens vs read complet",
    },
    {
        "id": "search-meta",
        "title": "2. Search meta-only",
        "status": "done",
        "endpoint": "GET /api/memory-explorer/search-meta?q=",
        "why": "Identifier QUELS fichiers contiennent un mot sans payer les snippets.",
        "returns": "[{root, rel, name, line, count}] — pas de snippet",
        "savings": "~3-5x tokens vs /search",
    },
    {
        "id": "index",
        "title": "3. Index parser (MEMORY.md)",
        "status": "done",
        "endpoint": "GET /api/memory-explorer/index?root=&name=MEMORY.md",
        "why": "Vue structurée de l'index + détection orphelins/liens cassés.",
        "returns": "[{title, file, hook, exists}] + missing[] + orphans[]",
        "savings": "Maintenance qualité de l'index (qui guide tout le reste)",
    },
    {
        "id": "section",
        "title": "4. Section read",
        "status": "done",
        "endpoint": "GET /api/memory-explorer/section?root=&rel=&heading=",
        "why": "Lire UNE section ## précise au lieu du fichier entier.",
        "returns": "Contenu de la section jusqu'au prochain heading de niveau ≤",
        "savings": "~3-10x sur les gros .md structurés",
    },
    {
        "id": "search-headings",
        "title": "5. Search headings (bonus)",
        "status": "done",
        "endpoint": "GET /api/memory-explorer/search-headings?q=",
        "why": "Localiser un sujet via les titres ## sans lire les contenus.",
        "returns": "[{root, rel, name, heading, level}]",
        "savings": "Réponse à 'où est documenté X ?' en ~50 tokens",
    },
    {
        "id": "heatmap",
        "title": "6. Heatmap usage",
        "status": "done",
        "endpoint": "GET /api/memory-explorer/heatmap",
        "why": "Identifier les .md les plus sollicités → candidats pour meilleur découpage / index plus riche.",
        "returns": "[{root, rel, calls, tokens_saved, by_endpoint, last}]",
        "savings": "Méta-outil : améliore le ROI des autres endpoints",
    },
    {
        "id": "index-health",
        "title": "7. Index health audit",
        "status": "done",
        "endpoint": "GET /api/memory-explorer/index-health",
        "why": "Détecter MEMORY.md cassés (liens morts) ou .md orphelins (non indexés) à travers tous les repos.",
        "returns": "{reports[], totals: {indexes, missing, orphans}}",
        "savings": "Maintenance proactive de la qualité du graphe mémoire",
    },
    {
        "id": "mcp-wrapper",
        "title": "8. MCP wrapper",
        "status": "done",
        "endpoint": "stdio MCP server: memory-cc",
        "why": "Claude Code peut appeler skim/section/index/search-meta nativement, sans curl.",
        "returns": "10 tools : memex_skim, memex_section, memex_search_meta, memex_search_headings, memex_index, memex_heatmap, memex_index_health, memex_tree, memex_recent, memex_create_memory",
        "savings": "Suppression du tax curl + JSON parsing manuel à chaque appel",
    },
    {
        "id": "create-memory",
        "title": "9. Create memory (atomic)",
        "status": "done",
        "endpoint": "POST /api/memory-explorer/create-memory",
        "why": "Créer un .md typé + maj du MEMORY.md en UN seul appel — garantit structure frontmatter + index cohérent.",
        "returns": "{ok, rel, root, type, index_updated, index_path, size}",
        "savings": "Réduit la friction de sauvegarde → plus de mémoires capturées → meilleur contexte futur",
    },
]


@app.get("/api/memory-explorer/roadmap")
def memex_roadmap():
    return {"items": MEMEX_ROADMAP}


# ── Static files ───────────────────────────────────────────────────
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
def index():
    return FileResponse(str(static_dir / "index.html"))
