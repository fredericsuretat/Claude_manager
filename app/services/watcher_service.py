import re
import subprocess
import shutil
import threading
import time
from datetime import datetime

RATE_LIMIT_PATTERNS = [
    r"you've hit your limit",
    r"you have hit your limit",
    r"hit your limit",
    r"usage limit",
    r"rate limit",
]

RESET_TIME_PATTERN = re.compile(
    r"resets?\s+(\w{3})\s+(\d{1,2}),?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
    re.IGNORECASE,
)

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_reset_datetime(text: str):
    m = RESET_TIME_PATTERN.search(text)
    if not m:
        return None
    month_s, day_s, hour_s, min_s, ampm = m.groups()
    month = MONTHS.get(month_s.lower())
    if not month:
        return None
    day = int(day_s)
    hour = int(hour_s)
    minute = int(min_s) if min_s else 0
    if ampm.lower() == "pm" and hour != 12:
        hour += 12
    elif ampm.lower() == "am" and hour == 12:
        hour = 0
    now = datetime.now()
    try:
        reset_dt = datetime(now.year, month, day, hour, minute)
        if reset_dt < now:
            reset_dt = datetime(now.year + 1, month, day, hour, minute)
        return reset_dt
    except ValueError:
        return None


class WatcherService:
    def __init__(self, mobile_service, executor=None, logger=None):
        self.mobile = mobile_service
        self.executor = executor
        self.logger = logger or print

        self.state = "unknown"
        self.rate_limited = False
        self.reset_at: datetime | None = None
        self.auto_prompt: str | None = None
        self._restart_timer: threading.Timer | None = None

        self.running = False
        self.check_interval = 30
        self._last_process_count = 0
        self.on_state_change = None

    def log(self, msg):
        if self.logger:
            self.logger(msg)

    def start(self):
        if self.running:
            return
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()
        self.log("[WATCHER] Démarré")

    def stop(self):
        self.running = False
        if self._restart_timer:
            self._restart_timer.cancel()
        self.log("[WATCHER] Arrêté")

    def _loop(self):
        while self.running:
            try:
                self._check_claude_process()
            except Exception as e:
                self.log(f"[WATCHER ERROR] {e}")
            time.sleep(self.check_interval)

    def _check_claude_process(self):
        procs = self._find_claude_processes()
        count = len(procs)
        prev_state = self.state

        if count == 0:
            if self.state == "running":
                self.state = "idle"
                self.log("[WATCHER] Claude inactif")
                self.mobile.notify("💤 Claude libre", "Claude a terminé et attend des ordres.")
        else:
            if self.state in ("idle", "unknown", "rate_limited") and not self.rate_limited:
                self.state = "running"
                self.log(f"[WATCHER] Claude actif ({count} process)")

        self._last_process_count = count
        if self.on_state_change and self.state != prev_state:
            self.on_state_change(self.state)

    def _find_claude_processes(self):
        try:
            result = subprocess.run(
                ["pgrep", "-f", "claude"],
                capture_output=True, text=True, timeout=5
            )
            return [p.strip() for p in result.stdout.splitlines() if p.strip()]
        except Exception:
            return []

    def on_rate_limit_detected(self, output_text: str, pending_prompt: str = None):
        if self.rate_limited:
            return
        self.rate_limited = True
        self.state = "rate_limited"
        self.auto_prompt = pending_prompt
        reset_dt = parse_reset_datetime(output_text)
        self.reset_at = reset_dt

        if reset_dt:
            reset_str = reset_dt.strftime("%d/%m à %Hh%M")
            self.mobile.notify(
                "Quota Claude atteint",
                f"Limite atteinte. Reset le {reset_str}.\nRelance auto + notification programmees."
            )
            self.log(f"[WATCHER] Rate limit — reset prévu : {reset_str}")
            self._schedule_restart(reset_dt)
        else:
            self.mobile.notify(
                "Quota Claude atteint",
                "Limite atteinte. Heure de reset non parsee — relance manuelle requise."
            )

        # Notify usage service so it schedules the reset notification independently
        claude_usage = getattr(self, "claude_usage", None)
        if claude_usage:
            claude_usage.on_rate_limited(reset_dt)

        if self.on_state_change:
            self.on_state_change(self.state)

    def on_rate_limit_cleared(self):
        self.rate_limited = False
        if self.state == "rate_limited":
            self.state = "idle"

    def _schedule_restart(self, reset_dt: datetime):
        if self._restart_timer:
            self._restart_timer.cancel()
        delay = (reset_dt - datetime.now()).total_seconds() + 90
        if delay <= 0:
            delay = 5
        self.log(f"[WATCHER] Relance auto dans {int(delay)}s")
        self._restart_timer = threading.Timer(delay, self._auto_restart)
        self._restart_timer.daemon = True
        self._restart_timer.start()

    def schedule_restart_at(self, reset_dt: datetime, prompt: str = None):
        self.reset_at = reset_dt
        self.auto_prompt = prompt
        self._schedule_restart(reset_dt)

    def cancel_restart(self):
        if self._restart_timer:
            self._restart_timer.cancel()
            self._restart_timer = None
        self.reset_at = None
        self.log("[WATCHER] Relance annulée")
        self.mobile.notify("❌ Relance annulée", "La relance automatique a été annulée.")

    def _auto_restart(self):
        self.rate_limited = False
        self.state = "running"
        self.log("[WATCHER] Relance automatique déclenchée")
        self.mobile.notify("🚀 Claude relancé", "Le quota est revenu. Claude repart en mode autonome.")
        if self.auto_prompt and self.executor:
            self.executor.run_async(self.auto_prompt)
        else:
            self._open_claude_terminal()
        self.reset_at = None
        self.auto_prompt = None
        self._restart_timer = None
        if self.on_state_change:
            self.on_state_change(self.state)

    def launch_autonomous(self, prompt: str = None):
        self.log("[WATCHER] Lancement mode autonome")
        self.mobile.notify("🤖 Mode autonome", "Lancement de Claude en mode autonome...")
        if prompt and self.executor:
            self.executor.run_async(prompt)
        else:
            self._open_claude_terminal(autonomous=True)

    def _open_claude_terminal(self, autonomous: bool = False):
        flags = ["--dangerously-skip-permissions"] if autonomous else []
        cmd = ["claude"] + flags
        terminals = [
            ["gnome-terminal", "--"],
            ["x-terminal-emulator", "-e"],
            ["konsole", "-e"],
            ["xfce4-terminal", "-e"],
            ["xterm", "-e"],
        ]
        for term in terminals:
            if shutil.which(term[0]):
                try:
                    subprocess.Popen(term + cmd)
                    self.log(f"[WATCHER] Claude ouvert dans {term[0]}")
                    return
                except Exception as e:
                    self.log(f"[WATCHER] Terminal {term[0]} échoué: {e}")
        subprocess.Popen(cmd)

    def get_status(self) -> dict:
        remaining = None
        if self.reset_at:
            secs = int((self.reset_at - datetime.now()).total_seconds())
            if secs > 0:
                h, m = divmod(secs // 60, 60)
                remaining = f"{h}h{m:02d}m"
        return {
            "state": self.state,
            "rate_limited": self.rate_limited,
            "reset_at": self.reset_at.strftime("%d/%m %H:%M") if self.reset_at else None,
            "remaining": remaining,
            "auto_prompt": (self.auto_prompt[:60] + "…") if self.auto_prompt and len(self.auto_prompt) > 60 else self.auto_prompt,
            "claude_processes": self._last_process_count,
        }
