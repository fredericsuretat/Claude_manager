import json
import time
from pathlib import Path
from typing import Any, Dict, List


class TokenMonitorService:
    def __init__(self, logger=None):
        self.logger = logger or (lambda msg: None)
        self.user_claude_file = Path.home() / ".claude.json"
        self.history_file = Path.home() / ".claude" / "analytics" / "token_monitor_history.json"

    def _load_json(self, path: Path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _save_json(self, path: Path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _load_history(self) -> List[Dict[str, Any]]:
        data = self._load_json(self.history_file, [])
        return data if isinstance(data, list) else []

    def _save_history(self, rows: List[Dict[str, Any]]) -> None:
        self._save_json(self.history_file, rows[-200:])

    def _has_metrics(self, data: Dict[str, Any]) -> bool:
        return any([
            float(data.get("lastCost", 0) or 0) > 0,
            int(data.get("lastTotalInputTokens", 0) or 0) > 0,
            int(data.get("lastTotalOutputTokens", 0) or 0) > 0,
        ])

    def _choose_best_project(self, projects: Dict[str, Any]) -> tuple:
        scored = []
        for key, data in projects.items():
            if not isinstance(data, dict) or not self._has_metrics(data):
                continue
            cost = float(data.get("lastCost", 0) or 0)
            input_tokens = int(data.get("lastTotalInputTokens", 0) or 0)
            output_tokens = int(data.get("lastTotalOutputTokens", 0) or 0)
            cache_create = int(data.get("lastTotalCacheCreationInputTokens", 0) or 0)
            cache_read = int(data.get("lastTotalCacheReadInputTokens", 0) or 0)
            duration = int(data.get("lastDuration", 0) or 0)
            score = int(cost * 100000) + input_tokens + output_tokens + cache_create + cache_read + duration
            scored.append((score, key, data))
        if not scored:
            return "(no recorded run)", {}
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1], scored[0][2]

    def _build_snapshot(self) -> Dict[str, Any]:
        user_data = self._load_json(self.user_claude_file, {})
        projects = user_data.get("projects", {})
        if not isinstance(projects, dict):
            projects = {}
        project_key, project_data = self._choose_best_project(projects)
        model_usage = project_data.get("lastModelUsage", {}) or {}
        model_names = list(model_usage.keys())
        top_model = model_names[0] if model_names else "(unknown)"
        input_tokens = int(project_data.get("lastTotalInputTokens", 0) or 0)
        output_tokens = int(project_data.get("lastTotalOutputTokens", 0) or 0)
        cache_create = int(project_data.get("lastTotalCacheCreationInputTokens", 0) or 0)
        cache_read = int(project_data.get("lastTotalCacheReadInputTokens", 0) or 0)
        return {
            "timestamp": int(time.time()),
            "project_key": project_key,
            "last_cost": float(project_data.get("lastCost", 0) or 0),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_create_tokens": cache_create,
            "cache_read_tokens": cache_read,
            "total_tokens": input_tokens + output_tokens + cache_create + cache_read,
            "duration_ms": int(project_data.get("lastDuration", 0) or 0),
            "top_model": top_model,
            "model_usage": model_usage,
        }

    def _compute_drift(self, snap: Dict[str, Any]) -> Dict[str, Any]:
        if snap["project_key"] == "(no recorded run)":
            return {"score": 0, "level": "UNKNOWN", "reasons": ["Aucun run Claude enregistré"]}
        score = 0
        reasons = []
        if snap["total_tokens"] > 100000:
            score += 3
            reasons.append("Total tokens très élevé")
        elif snap["total_tokens"] > 50000:
            score += 2
            reasons.append("Total tokens élevé")
        if snap["last_cost"] > 0.10:
            score += 3
            reasons.append("Coût élevé")
        elif snap["last_cost"] > 0.03:
            score += 1
            reasons.append("Coût notable")
        if snap["duration_ms"] > 120000:
            score += 1
            reasons.append("Run long")
        level = "LOW" if score <= 2 else "MEDIUM" if score <= 5 else "HIGH"
        return {"score": score, "level": level, "reasons": reasons}

    def capture(self) -> Dict[str, Any]:
        snap = self._build_snapshot()
        drift = self._compute_drift(snap)
        history = self._load_history()
        if snap["project_key"] != "(no recorded run)" and snap["total_tokens"] > 0:
            row = {k: snap[k] for k in ["timestamp", "project_key", "last_cost", "input_tokens",
                                         "output_tokens", "cache_create_tokens", "cache_read_tokens",
                                         "total_tokens", "duration_ms", "top_model"]}
            comparable = {k: v for k, v in row.items() if k != "timestamp"}
            if not history or {k: history[-1].get(k) for k in comparable} != comparable:
                history.append(row)
                self._save_history(history)
        return {
            "snapshot": snap,
            "drift": drift,
            "history": history[-30:],
        }
