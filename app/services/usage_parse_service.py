import re


class UsageParseService:
    def parse(self, text: str) -> dict:
        raw = text or ""
        data = {
            "context_used": None, "context_limit": None, "context_percent": None,
            "sonnet_percent": None, "haiku_percent": None, "cache_hit": None,
            "warnings": [],
        }
        m = re.search(r"Tokens:\s*([\d.]+)k?\s*/\s*([\d.]+)k?\s*\((\d+)%\)", raw, re.I)
        if m:
            used = float(m.group(1))
            limit = float(m.group(2))
            if "k" in m.group(0).lower():
                used *= 1000
                limit *= 1000
            data["context_used"] = int(used)
            data["context_limit"] = int(limit)
            data["context_percent"] = int(m.group(3))
        m = re.search(r"sonnet:\s*(\d+)%", raw, re.I)
        if m:
            data["sonnet_percent"] = int(m.group(1))
        m = re.search(r"haiku:\s*(\d+)%", raw, re.I)
        if m:
            data["haiku_percent"] = int(m.group(1))
        m = re.search(r"cache hit:\s*(\d+)%", raw, re.I)
        if m:
            data["cache_hit"] = int(m.group(1))
        if data["context_percent"] is not None:
            if data["context_percent"] >= 80:
                data["warnings"].append("Contexte très élevé → nouvelle session recommandée.")
            elif data["context_percent"] >= 65:
                data["warnings"].append("Contexte élevé → attention à la dérive.")
        if data["sonnet_percent"] is not None and data["sonnet_percent"] >= 80:
            data["warnings"].append("Sonnet dominant → utiliser Haiku pour tâches simples.")
        if data["cache_hit"] is not None and data["cache_hit"] < 70:
            data["warnings"].append("Cache faible → coût plus élevé possible.")
        if not data["warnings"]:
            data["warnings"].append("Pas d'alerte majeure.")
        return data
