"""
TerminalService — lance Claude dans un PTY et streame l'I/O via callbacks.

Principe :
- Un seul processus Claude actif à la fois (session)
- Output lu dans un thread dédié → callback on_output(data: str)
- Input envoyé via write(data)
- Détection automatique : idle (invite de commande), rate-limit, erreur
"""

import os
import re
import threading
import time
from datetime import datetime
from typing import Callable, Optional

import ptyprocess
from app.config import CLAUDE_BIN



RATE_LIMIT_RE = re.compile(
    r"(you.ve hit your limit|hit your limit|usage limit|rate limit)",
    re.IGNORECASE,
)
RESET_TIME_RE = re.compile(
    r"resets?\s+(\w{3})\s+(\d{1,2}),?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
    re.IGNORECASE,
)


class TerminalSession:
    def __init__(
        self,
        on_output: Callable[[str], None],
        on_state: Callable[[str, Optional[datetime]], None],
        logger: Callable[[str], None],
        autonomous: bool = False,
        rows: int = 40,
        cols: int = 220,
    ):
        self._proc: Optional[ptyprocess.PtyProcess] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._on_output = on_output
        self._on_state = on_state  # (state: str, reset_at: datetime|None)
        self._logger = logger
        self._autonomous = autonomous
        self._rows = rows
        self._cols = cols

        self.state = "idle"          # idle | running | waiting | rate_limited | dead
        self.started_at: Optional[datetime] = None
        self.last_output_at: Optional[datetime] = None
        self._output_buf = ""
        self._lock = threading.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────
    def start(self):
        if self._proc and self._proc.isalive():
            return

        cmd = [CLAUDE_BIN]
        if self._autonomous:
            cmd.append("--dangerously-skip-permissions")

        env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor"}
        env.pop("ANTHROPIC_API_KEY", None)  # never use API key

        self._proc = ptyprocess.PtyProcess.spawn(
            cmd, dimensions=(self._rows, self._cols), env=env
        )
        self.started_at = datetime.now()
        self.state = "running"
        self._logger(f"[TERMINAL] Session démarrée (pid {self._proc.pid})")
        self._on_state("running", None)

        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def stop(self):
        if self._proc and self._proc.isalive():
            try:
                self._proc.terminate(force=True)
            except Exception:
                pass
        self.state = "dead"
        self._on_state("dead", None)
        self._logger("[TERMINAL] Session arrêtée")

    def is_alive(self) -> bool:
        return bool(self._proc and self._proc.isalive())

    # ── I/O ──────────────────────────────────────────────────────
    def write(self, data: str):
        """Envoie du texte à Claude (frappe clavier)."""
        if not self.is_alive():
            return
        try:
            self._proc.write(data.encode("utf-8", errors="replace"))
        except Exception as e:
            self._logger(f"[TERMINAL] Write error: {e}")

    def send_line(self, text: str):
        """Envoie une ligne + Enter (carriage return, comme xterm.js)."""
        self.write(text + "\r")

    def send_interrupt(self):
        """Ctrl+C."""
        self.write("\x03")

    def resize(self, rows: int, cols: int):
        """Redimensionne le PTY pour correspondre au terminal du navigateur."""
        self._rows = rows
        self._cols = cols
        if self._proc and self._proc.isalive():
            try:
                self._proc.setwinsize(rows, cols)
            except Exception:
                pass

    # ── Reader loop ───────────────────────────────────────────────
    def _read_loop(self):
        while self._proc and self._proc.isalive():
            try:
                data = self._proc.read(4096)
                if data:
                    text = data.decode("utf-8", errors="replace")
                    self.last_output_at = datetime.now()
                    self._on_output(text)
                    self._analyze(text)
            except EOFError:
                break
            except Exception as e:
                if self._proc and self._proc.isalive():
                    self._logger(f"[TERMINAL] Read error: {e}")
                break

        if self.state not in ("dead", "rate_limited"):
            self.state = "dead"
            self._on_state("dead", None)
            self._logger("[TERMINAL] Session terminée (processus mort)")

    def _analyze(self, text: str):
        """Détecte rate-limit, idle, etc. dans le flux de sortie."""
        if RATE_LIMIT_RE.search(text):
            reset_dt = self._parse_reset(text)
            self.state = "rate_limited"
            self._on_state("rate_limited", reset_dt)
            self._logger(f"[TERMINAL] Rate limit détecté — reset : {reset_dt}")

    @staticmethod
    def _parse_reset(text: str) -> Optional[datetime]:
        MONTHS = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
                  "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
        m = RESET_TIME_RE.search(text)
        if not m:
            return None
        month_s, day_s, hour_s, min_s, ampm = m.groups()
        month = MONTHS.get(month_s.lower())
        if not month:
            return None
        hour = int(hour_s)
        minute = int(min_s) if min_s else 0
        if ampm.lower() == "pm" and hour != 12:
            hour += 12
        elif ampm.lower() == "am" and hour == 12:
            hour = 0
        now = datetime.now()
        try:
            dt = datetime(now.year, month, int(day_s), hour, minute)
            if dt < now:
                dt = datetime(now.year + 1, month, int(day_s), hour, minute)
            return dt
        except ValueError:
            return None

    def get_status(self) -> dict:
        return {
            "state": self.state,
            "alive": self.is_alive(),
            "pid": self._proc.pid if self._proc else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_output_at": self.last_output_at.isoformat() if self.last_output_at else None,
        }


class TerminalService:
    """Gestionnaire de session PTY — une session active à la fois."""

    def __init__(self, ws_manager, watcher=None, logger=None, loop=None, live_usage=None):
        self._ws = ws_manager
        self._watcher = watcher
        self._logger = logger or print
        self._loop = loop  # event loop FastAPI, stocké à la création
        self._session: Optional[TerminalSession] = None
        self._live_usage = live_usage  # LiveUsageService — alimenté par le flux PTY

    def _broadcast_output(self, data: str):
        """Envoie le flux PTY à tous les clients WebSocket (thread → async)."""
        import asyncio, json
        # Alimenter le live usage parser avec le flux brut
        if self._live_usage:
            try:
                self._live_usage.feed(data)
            except Exception:
                pass
        msg = json.dumps({"type": "terminal_output", "data": data})
        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
        try:
            asyncio.run_coroutine_threadsafe(self._ws.broadcast_raw(msg), loop)
        except Exception:
            pass

    def _on_state_change(self, state: str, reset_dt):
        import asyncio, json
        msg = json.dumps({"type": "terminal_state", "state": state,
                          "reset_at": reset_dt.strftime("%d/%m %H:%M") if reset_dt else None})
        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
        try:
            asyncio.run_coroutine_threadsafe(self._ws.broadcast_raw(msg), loop)
        except Exception:
            pass

        # Notify watcher
        if self._watcher and state == "rate_limited":
            self._watcher.on_rate_limit_detected(reset_dt=reset_dt)

    def _auto_usage(self):
        """Envoie /usage 8s après démarrage, puis Escape pour fermer le modal."""
        import time
        time.sleep(8)
        if self._session and self._session.is_alive():
            self._session.send_line("/usage")
            self._logger("[TERMINAL] Auto /usage envoyé")
            time.sleep(3)  # laisser le temps au modal de s'afficher
            if self._session and self._session.is_alive():
                self._session.write("\x1b")  # ESC pour fermer le modal /usage
                self._logger("[TERMINAL] Auto ESC envoyé (fermeture /usage)")

    def start(self, autonomous: bool = False, rows: int = 40, cols: int = 220):
        if self._session and self._session.is_alive():
            self._logger("[TERMINAL] Session déjà active")
            return {"ok": False, "error": "Session already running"}

        self._session = TerminalSession(
            on_output=self._broadcast_output,
            on_state=self._on_state_change,
            logger=self._logger,
            autonomous=autonomous,
            rows=rows,
            cols=cols,
        )
        self._session.start()
        # Auto-send /usage after Claude boots to populate the usage bar
        threading.Thread(target=self._auto_usage, daemon=True).start()
        return {"ok": True, "pid": self._session._proc.pid if self._session._proc else None}

    def resize(self, rows: int, cols: int):
        if self._session:
            self._session.resize(rows, cols)

    def stop(self):
        if self._session:
            self._session.stop()
            self._session = None
        return {"ok": True}

    def write(self, data: str):
        if not self._session or not self._session.is_alive():
            return {"ok": False, "error": "No active session"}
        self._session.write(data)
        return {"ok": True}

    def send_line(self, text: str):
        if not self._session or not self._session.is_alive():
            return {"ok": False, "error": "No active session"}
        self._session.send_line(text)
        return {"ok": True}

    def interrupt(self):
        if self._session:
            self._session.send_interrupt()
        return {"ok": True}

    def get_status(self) -> dict:
        if self._session:
            return self._session.get_status()
        return {"state": "idle", "alive": False, "pid": None,
                "started_at": None, "last_output_at": None}
