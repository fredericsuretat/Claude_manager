import subprocess
import threading
import time
import shutil
import os


class ExecutorService:
    def __init__(self, logger=None, enable_execution=True):
        self.logger = logger or print
        self.enable_execution = enable_execution
        self.last_call_ts = 0
        self.min_interval_sec = 2
        self.call_count = 0
        self.max_calls = 200
        self._login_in_progress = False

    def log(self, msg):
        if self.logger:
            self.logger(msg)
        else:
            print(msg)

    def login(self):
        self.log("[AUTH] Launching Claude login flow...")
        cmd = ["claude", "login"]
        terminals = [
            ["gnome-terminal", "--"], ["x-terminal-emulator", "-e"],
            ["konsole", "-e"], ["xfce4-terminal", "-e"], ["xterm", "-e"],
        ]
        for term in terminals:
            if shutil.which(term[0]):
                try:
                    subprocess.Popen(term + cmd)
                    self.log(f"[AUTH] Login opened in {term[0]}")
                    return True
                except Exception as e:
                    self.log(f"[AUTH] Failed terminal {term[0]}: {e}")
        subprocess.run(cmd)
        return True

    def _handle_auth(self, output: str) -> bool:
        if not output:
            return False
        if "Not logged in" in output or "Please run /login" in output:
            if not self._login_in_progress:
                self._login_in_progress = True
                self.login()
                return True
        return False

    def _check_rate_limit(self, text: str) -> bool:
        patterns = ["you've hit your limit", "you have hit your limit", "hit your limit", "usage limit", "rate limit"]
        lower = text.lower()
        return any(p in lower for p in patterns)

    def run_claude(self, prompt: str, model: str = None):
        if not self.enable_execution:
            self.log("[DRY_RUN] Claude blocked")
            return None

        now = time.time()
        if now - self.last_call_ts < self.min_interval_sec:
            self.log("[GUARD] Rate limit triggered")
            return None

        self.call_count += 1
        if self.call_count > self.max_calls:
            self.log("[BUDGET] Max calls reached")
            return None

        self.last_call_ts = now
        cmd = ["claude"]
        if model:
            cmd += ["--model", model]
        cmd.append(prompt)

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            combined = stdout + stderr

            if self._handle_auth(combined):
                self.log("[AUTH] Retrying after login...")
                self._login_in_progress = False
                return self.run_claude(prompt, model)

            if self._check_rate_limit(combined):
                self.log("[RATE_LIMIT] Claude a atteint sa limite d'utilisation")
                watcher = getattr(self, "watcher", None)
                if watcher:
                    watcher.on_rate_limit_detected(combined, prompt)

            if stderr:
                self.log(f"[Claude stderr] {stderr}")

            self.last_call_ts = time.time()
            return stdout

        except subprocess.TimeoutExpired:
            self.log("[Executor] Timeout — Claude n'a pas répondu dans les temps")
            return None
        except Exception as e:
            self.log(f"[Executor error] {e}")
            return None

    def run_async(self, prompt: str, model: str = None, callback=None):
        def _run():
            result = self.run_claude(prompt, model)
            if callback:
                callback(result)
        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    def get_status(self) -> dict:
        return {
            "enable_execution": self.enable_execution,
            "call_count": self.call_count,
            "max_calls": self.max_calls,
            "last_call_ts": self.last_call_ts,
        }
