"""
Lecture locale de l'état d'usage Claude Code.

Sources :
- ~/.claude/.credentials.json  → type abonnement, tier, expiry OAuth
- ~/.claude.json               → oauthAccount (email, displayName)
- ~/.claude/stats-cache.json   → activité quotidienne locale (messages, sessions, tool calls)

Aucun appel à l'API Anthropic payante. Zéro token consommé.
L'API claude.ai/settings/usage est protégée — on s'appuie sur les fichiers locaux du CLI.
"""

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


CREDENTIALS_FILE = Path.home() / ".claude" / ".credentials.json"
CLAUDE_JSON_FILE  = Path.home() / ".claude.json"
STATS_CACHE_FILE  = Path.home() / ".claude" / "stats-cache.json"


def _load_json(path: Path, default=None):
    if not path.exists():
        return default or {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default or {}


class ClaudeUsageService:
    def __init__(self, mobile_service=None, logger=None):
        self.mobile = mobile_service
        self.logger = logger or print

        # State géré par le watcher (injecté depuis l'extérieur)
        self.rate_limited: bool = False
        self.reset_at: Optional[datetime] = None
        self._reset_notif_sent: bool = False
        self._reset_timer: Optional[threading.Timer] = None

    def log(self, msg: str):
        self.logger(msg)

    # ── Plan info ────────────────────────────────────────────────
    def get_plan_info(self) -> dict:
        creds = _load_json(CREDENTIALS_FILE)
        oauth = creds.get("claudeAiOauth", {})
        claude = _load_json(CLAUDE_JSON_FILE)
        account = claude.get("oauthAccount", {})

        expires_at_ms = oauth.get("expiresAt")
        expires_dt = None
        if expires_at_ms:
            try:
                expires_dt = datetime.fromtimestamp(int(expires_at_ms) / 1000)
            except Exception:
                pass

        return {
            "subscription_type": oauth.get("subscriptionType", "unknown"),
            "rate_limit_tier": oauth.get("rateLimitTier", "unknown"),
            "has_extra_usage": account.get("hasExtraUsageEnabled", False),
            "extra_disabled_reason": claude.get("cachedExtraUsageDisabledReason"),
            "email": account.get("emailAddress", ""),
            "display_name": account.get("displayName", ""),
            "org_role": account.get("organizationRole", ""),
            "oauth_expires": expires_dt.strftime("%d/%m/%Y %H:%M") if expires_dt else None,
            "oauth_valid": (expires_dt > datetime.now()) if expires_dt else None,
            # Explicit note: no API tokens ever consumed by this app
            "api_cost": "$0.00 — lecture fichiers locaux uniquement",
        }

    # ── Daily stats ───────────────────────────────────────────────
    def get_daily_stats(self, days: int = 30) -> list:
        stats = _load_json(STATS_CACHE_FILE)
        activity = stats.get("dailyActivity", [])
        # Sort and take last N days
        activity = sorted(activity, key=lambda x: x.get("date", ""))
        return activity[-days:]

    def get_today_stats(self) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        for entry in self.get_daily_stats(7):
            if entry.get("date") == today:
                return entry
        return {"date": today, "messageCount": 0, "sessionCount": 0, "toolCallCount": 0}

    def get_recent_stats(self) -> dict:
        """Stats des 7 derniers jours avec totaux."""
        entries = self.get_daily_stats(7)
        total_messages = sum(e.get("messageCount", 0) for e in entries)
        total_sessions = sum(e.get("sessionCount", 0) for e in entries)
        total_tools = sum(e.get("toolCallCount", 0) for e in entries)
        return {
            "days": entries,
            "total_messages_7d": total_messages,
            "total_sessions_7d": total_sessions,
            "total_tools_7d": total_tools,
            "last_computed_date": _load_json(STATS_CACHE_FILE).get("lastComputedDate"),
        }

    # ── Rate limit state ──────────────────────────────────────────
    def on_rate_limited(self, reset_dt: Optional[datetime]):
        """Appelé par le watcher quand un rate limit est détecté."""
        self.rate_limited = True
        self.reset_at = reset_dt
        self._reset_notif_sent = False

        if reset_dt and self.mobile:
            reset_str = reset_dt.strftime("%d/%m à %Hh%M")
            self.mobile.notify(
                "Quota Claude atteint",
                f"Limite atteinte. Reset le {reset_str}.\n"
                f"Notification programmée au moment du reset."
            )
            self.log(f"[USAGE] Rate limit — reset prévu {reset_str}")
            self._schedule_reset_notification(reset_dt)

    def _schedule_reset_notification(self, reset_dt: datetime):
        if self._reset_timer:
            self._reset_timer.cancel()

        delay = (reset_dt - datetime.now()).total_seconds()
        if delay < 0:
            delay = 0

        def _fire():
            self.rate_limited = False
            self._reset_notif_sent = True
            self.log("[USAGE] Reset atteint — notification envoyée")
            if self.mobile:
                today = self.get_today_stats()
                self.mobile.notify(
                    "Claude disponible",
                    f"Quota remis a zero. Tu peux reprendre.\n"
                    f"Aujourd'hui : {today.get('messageCount', 0)} messages, "
                    f"{today.get('sessionCount', 0)} sessions."
                )

        self._reset_timer = threading.Timer(delay, _fire)
        self._reset_timer.daemon = True
        self._reset_timer.start()

    def cancel_reset_timer(self):
        if self._reset_timer:
            self._reset_timer.cancel()
            self._reset_timer = None

    def on_rate_limit_cleared(self):
        self.rate_limited = False
        self.reset_at = None

    # ── Full status ───────────────────────────────────────────────
    def get_status(self) -> dict:
        plan = self.get_plan_info()
        today = self.get_today_stats()
        recent = self.get_recent_stats()

        remaining = None
        if self.reset_at:
            secs = int((self.reset_at - datetime.now()).total_seconds())
            if secs > 0:
                h, m = divmod(secs // 60, 60)
                remaining = f"{h}h{m:02d}m"

        return {
            "plan": plan,
            "today": today,
            "recent": recent,
            "rate_limited": self.rate_limited,
            "reset_at": self.reset_at.strftime("%d/%m %H:%M") if self.reset_at else None,
            "remaining_until_reset": remaining,
            "note": (
                "Usage local uniquement (stats-cache.json). "
                "L'API claude.ai/usage est protegee cote serveur. "
                "Ce service ne consomme aucun token."
            ),
        }
