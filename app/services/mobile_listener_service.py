import json
import requests
import threading
import time


class MobileListenerService:
    def __init__(self, topic: str, executor, mobile_service, logger=None):
        self.executor = executor
        self.mobile = mobile_service
        self.logger = logger or print
        self.topic = topic
        self.url = f"https://ntfy.sh/{topic}/json?poll=1"
        self.running = False
        self.last_id = None
        self._on_command_cb = None

    def set_command_callback(self, fn):
        self._on_command_cb = fn

    def log(self, msg):
        self.logger(msg)

    def start(self):
        if self.running:
            return
        self.running = True
        self._skip_backlog()
        threading.Thread(target=self._loop, daemon=True).start()
        self.log("[MOBILE] Listener started")

    def _skip_backlog(self):
        """Advance cursor to latest message so we don't replay the ntfy cache on startup."""
        try:
            r = requests.get(self.url, timeout=10)
            if r.status_code == 200:
                for line in r.text.strip().split("\n"):
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                        eid = event.get("id")
                        if eid:
                            self.last_id = eid
                    except Exception:
                        pass
            if self.last_id:
                self.log(f"[MOBILE] Backlog skipped, cursor at {self.last_id}")
        except Exception as e:
            self.log(f"[MOBILE] Backlog skip failed: {e}")

    def stop(self):
        self.running = False

    def _loop(self):
        while self.running:
            try:
                url = self.url
                if self.last_id:
                    url += f"&since={self.last_id}"

                r = requests.get(url, timeout=10)

                if r.status_code == 200:
                    for line in r.text.strip().split("\n"):
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                        except Exception:
                            continue
                        event_id = event.get("id")
                        if event_id:
                            self.last_id = event_id
                        # Skip if the notification title looks like one sent by CC itself
                        title = event.get("title", "")
                        if any(title.startswith(p) for p in self._SELF_PREFIXES):
                            continue
                        message = event.get("message", "").strip()
                        if message:
                            self.handle_command(message)

            except Exception as e:
                try:
                    self.log(f"[MOBILE ERROR] {e}")
                except Exception:
                    print(f"[MOBILE ERROR] {e}")

            time.sleep(5)

    # Messages originating from the CC app itself — ignore as commands
    _SELF_PREFIXES = (
        "💤", "🚀", "📊", "🤖", "❌", "✅", "⚠️", "🔐", "🚫", "⏳",
        "Quota", "Claude a termin", "Claude relancé", "Quota remis",
        "[SERVER]", "[WATCHER]", "[MOBILE]", "[USAGE]", "[LIVE USAGE]",
    )

    def handle_command(self, msg: str):
        # Ignore messages that look like outbound notifications (prevents ntfy echo loop)
        if any(msg.startswith(p) for p in self._SELF_PREFIXES):
            return
        # Ignore very short or very long messages that aren't commands
        if len(msg) < 2 or len(msg) > 500:
            return

        self.log(f"[MOBILE CMD] {msg}")
        if self._on_command_cb:
            try:
                self._on_command_cb(msg)
            except Exception:
                pass

        cmd = msg.lower().strip()

        if cmd == "status":
            status = self.executor.get_status() if hasattr(self.executor, "get_status") else {}
            self.mobile.notify("📊 Status", str(status))
            return

        if cmd.startswith("run "):
            prompt = msg[4:]
            self.mobile.notify("🚀 Claude", f"Running: {prompt}")
            output = self.executor.run_claude(prompt)
            if output:
                self.mobile.notify("✅ Résultat", output[:4000])
            else:
                self.mobile.notify("⚠️ Claude", "No output / blocked")
            return

        if cmd == "login":
            self.executor.login()
            self.mobile.notify("🔐 Claude", "Login launched")
            return

        if cmd.startswith("go"):
            prompt = msg[2:].strip() or None
            watcher = getattr(self, "watcher", None)
            if watcher:
                watcher.launch_autonomous(prompt)
            else:
                self.mobile.notify("⚠️ Watcher", "Watcher non disponible")
            return

        if cmd == "cancel":
            watcher = getattr(self, "watcher", None)
            if watcher:
                watcher.cancel_restart()
            return

        if cmd == "watcher":
            watcher = getattr(self, "watcher", None)
            if watcher:
                self.mobile.notify("📊 Watcher", str(watcher.get_status()))
            return

        # Don't reflect unknown messages back to ntfy — would create a feedback loop
        # (app sends notification → listener picks it up → sends "Unknown" → loop)
        self.log(f"[MOBILE] Commande inconnue ignorée: {msg[:80]}")
