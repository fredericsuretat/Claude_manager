"""
LiveUsageService — parse les stats d'usage Claude depuis le flux PTY du terminal CC.

Les données `/usage` vivent en mémoire de la session Claude active.
On ne peut pas les lire depuis un nouveau processus.
Ce service est alimenté via feed() par le TerminalService qui stream le PTY.
Alertes ntfy à ≥90% et 100% de session.
"""

import re
import threading
import time
from datetime import datetime
from typing import Callable, Optional

ANSI_ESC = re.compile(
    r'\x1b\[[\d;?]*[a-zA-Z]'               # CSI sequences  (ESC [ ... letter)
    r'|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)'  # OSC sequences  (ESC ] ... BEL/ST)
    r'|\x1b[()][0-9A-Za-z]'                # Charset select (ESC ( 0 etc.)
    r'|\x1b[=>M78\[\]]'                    # Single-char escapes
    r'|\x07|\r'                            # BEL + CR
)

PCT_RE = re.compile(r'(\d+)\s*%')


class LiveUsageService:
    WARN_THRESHOLD = 90   # % session avant alerte

    def __init__(self, mobile_service=None, logger: Optional[Callable] = None):
        self.mobile = mobile_service
        self.logger = logger or print

        self._cache: Optional[dict] = None
        self._cache_ts: float = 0
        self._lock = threading.Lock()
        self._warned_pcts: set = set()
        self._pty_buf = ""   # buffer cumulé du flux PTY

    def log(self, msg: str):
        self.logger(msg)

    def start_background(self):
        """Plus de polling autonome — service alimenté par feed()."""
        pass

    # ── Alimentation par le flux PTY ──────────────────────────────
    def feed(self, raw_text: str):
        """Appelé par TerminalService à chaque chunk reçu du PTY."""
        self._pty_buf += raw_text
        # Garder seulement les 8 000 derniers chars pour éviter la croissance infinie
        if len(self._pty_buf) > 8000:
            self._pty_buf = self._pty_buf[-8000:]
        clean = ANSI_ESC.sub("", self._pty_buf)
        if re.search(r'[Cc]urrent\s*session', clean) or re.search(r'[Cc]urrent\s*week', clean):
            result = self._parse(clean)
            if result:
                with self._lock:
                    self._cache = result
                    self._cache_ts = time.time()
                self._pty_buf = ""  # reset pour la prochaine capture
                self._check_alerts(result)
                self.log(
                    f"[LIVE USAGE] Session {result.get('session_pct', '?')}% · "
                    f"Week {result.get('week_pct', '?')}%"
                )

    # ── Public API ─────────────────────────────────────────────────
    def get_cached(self) -> dict:
        with self._lock:
            return dict(self._cache) if self._cache else {}

    def get_live(self) -> dict:
        """Retourne le cache — le rafraîchissement se fait via feed()."""
        return self.get_cached()

    def get_debug_buf(self) -> str:
        """Retourne le buffer PTY brut nettoyé des ANSI (debug)."""
        return ANSI_ESC.sub("", self._pty_buf)

    def get_or_cached(self) -> dict:
        return self.get_cached()

    # ── Alert logic ───────────────────────────────────────────────
    def _check_alerts(self, data: dict):
        pct = data.get("session_pct")
        if pct is None:
            return

        if pct >= 100 and 100 not in self._warned_pcts:
            self._warned_pcts.add(100)
            self._warned_pcts.discard(90)
            reset = data.get("session_reset_str", "")
            msg = f"Session épuisée (100%).{' Reset : ' + reset if reset else ''}"
            if self.mobile:
                self.mobile.notify("Claude — Quota atteint 🚫", msg, priority=5)
            self.log(f"[LIVE USAGE] 🚫 100% — ntfy envoyé")

        elif pct >= self.WARN_THRESHOLD and 90 not in self._warned_pcts:
            self._warned_pcts.add(90)
            if self.mobile:
                self.mobile.notify(
                    "Claude — Quota presque atteint ⚠️",
                    f"Session à {pct}% — proche de la limite.",
                    priority=4,
                )
            self.log(f"[LIVE USAGE] ⚠️ {pct}% — ntfy envoyé")

        elif pct < self.WARN_THRESHOLD:
            self._warned_pcts.discard(90)
            self._warned_pcts.discard(100)

    def on_rate_limited(self, reset_dt: Optional[datetime]):
        """Appelé depuis l'extérieur (watcher / terminal) pour forcer l'état 100%."""
        reset_str = reset_dt.strftime("%H:%Mh") if reset_dt else None
        fake = {
            "session_pct": 100,
            "week_pct": None,
            "session_reset_str": reset_str,
            "week_reset_str": None,
            "captured_at": datetime.now().isoformat(),
            "source": "watcher",
        }
        with self._lock:
            self._cache = fake
            self._cache_ts = time.time()
        self._check_alerts(fake)

    # ── PTY capture ────────────────────────────────────────────────

    # ── Parser ─────────────────────────────────────────────────────
    def _parse(self, text: str) -> Optional[dict]:
        """
        Parse multi-line /usage output. Header and percentage are on separate lines:
          Current session
          ████░░ 80%
          Resets 2:10pm

          Current week
          ██████ 95%
        """
        session_pct = None
        week_pct = None
        session_reset_str = None
        week_reset_str = None
        context = None  # 'session' | 'week'

        for line in text.split("\n"):
            s = line.strip()
            if not s:
                continue

            if re.search(r'current\s*session', s, re.IGNORECASE):
                context = 'session'
                # percentage might be on same line
                m = PCT_RE.search(s)
                if m and session_pct is None:
                    session_pct = int(m.group(1))
                continue

            if re.search(r'current\s*week', s, re.IGNORECASE):
                context = 'week'
                m = PCT_RE.search(s)
                if m and week_pct is None:
                    week_pct = int(m.group(1))
                continue

            # Percentage line (progress bar + number)
            m = PCT_RE.search(s)
            if m and context:
                pct = int(m.group(1))
                if context == 'session' and session_pct is None:
                    session_pct = pct
                elif context == 'week' and week_pct is None:
                    week_pct = pct

            # Reset time line
            if re.search(r'reset', s, re.IGNORECASE):
                reset = self._parse_reset_str(s)
                if reset:
                    if context == 'session' and session_reset_str is None:
                        session_reset_str = reset
                    elif context == 'week' and week_reset_str is None:
                        week_reset_str = reset

        if session_pct is None and week_pct is None:
            return None

        return {
            "session_pct": session_pct,
            "week_pct": week_pct,
            "session_reset_str": session_reset_str,
            "week_reset_str": week_reset_str,
            "captured_at": datetime.now().isoformat(),
            "source": "pty",
        }

    @staticmethod
    def _parse_reset_str(text: str) -> Optional[str]:
        # "Resets3:20am" or "Resets 2:10pm" or "Resets at 2:10pm"
        m = re.search(r'Resets?\s*(?:at\s*)?(\d{1,2}):(\d{2})\s*(am|pm)', text, re.IGNORECASE)
        if m:
            return f"{m.group(1)}:{m.group(2)}{m.group(3).lower()}"

        # "Resets11pm" or "Resets 11pm" (hour only)
        m = re.search(r'Resets?\s*(?:at\s*)?(\d{1,2})\s*(am|pm)', text, re.IGNORECASE)
        if m:
            return f"{m.group(1)}h{m.group(2).lower()}"

        # "ResetsMay5,11pm" or "Resets Saturday, May 4 at 9:00am"
        # Use month names to anchor the match reliably
        m = re.search(
            r'Resets?\s*(?:\w+[,\s]+)?'
            r'(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
            r'Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
            r'\s*(\d{1,2})[,\s]+(\d{1,2}(?::\d{2})?)\s*(am|pm)',
            text, re.IGNORECASE,
        )
        if m:
            return f"{m.group(1)} {m.group(2)} {m.group(3)}{m.group(4).lower()}"

        return None
