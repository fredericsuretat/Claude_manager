import requests


class MobileService:
    def __init__(self, topic: str, logger=None):
        self.logger = logger or print
        self.topic = topic
        self.ntfy_url = f"https://ntfy.sh/{topic}"

    def log(self, msg):
        self.logger(msg)

    @staticmethod
    def _header_safe(text: str) -> str:
        cleaned = text.encode("latin-1", errors="ignore").decode("latin-1")
        return " ".join(cleaned.split())  # collapse whitespace + strip

    def notify(self, title: str, message: str, priority: int = 3):
        try:
            requests.post(
                self.ntfy_url,
                data=message.encode("utf-8"),
                headers={
                    "Title": self._header_safe(title),
                    "Priority": str(priority),
                    "Content-Type": "text/plain; charset=utf-8",
                },
                timeout=5
            )
        except Exception as e:
            self.log(f"[MOBILE ERROR] {e}")
