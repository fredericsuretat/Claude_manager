import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


from app.config import (
    APP_HISTORY_FILE,
    ARCHITECTURE_FILE,
    BUGS_FILE,
    CLEANED_HISTORY_FILE,
    COMMAND_STATS_FILE,
    ERROR_STATS_FILE,
    HABITS_FILE,
    LEGACY_HISTORY,
    LOGS_DIR,
    MERGED_HISTORY_FILE,
    RAW_DIR,
    SESSIONS_INDEX_FILE,
    STACK_FILE,
    ensure_dirs,
)



NOISE_COMMANDS = {"exit", "ls", "/logout", "clear", "pwd"}
SHORT_NOISE = {"ok", "go", "stp", "oui", "non", "test"}
STOPWORDS = {
    "est", "il", "possible", "de", "la", "le", "les", "comment", "tu", "peux",
    "je", "faire", "que", "et", "en", "un", "une", "des", "pour", "avec",
    "sur", "dans", "au", "aux", "du", "qui", "quoi", "où", "ou", "quand",
    "mes", "tes", "ses", "mon", "ton", "son", "leur", "leurs", "the", "and",
    "all", "app", "apps", "vrai", "vraie", "voir", "dire", "avoir", "être",
    "faut", "veux", "peut", "peux", "donne", "redonne", "utilisé", "faire",
    "cela", "ceci", "quoi", "comme", "plus", "moins", "bien", "juste",
}
BAD_WORDS = {"auht", "toutes", "truc", "machin", "qeu", "stp", "ok", "allez", "vasy", "vas"}

LEADING_PHRASE_PATTERNS = [
    r"^est[- ]il\s+possible\s+de\s+",
    r"^comment\s+je\s+peux\s+",
    r"^comment\s+",
    r"^peux[- ]tu\s+",
    r"^tu\s+peux\s+",
    r"^je\s+veux\s+",
    r"^je\s+voudrais\s+",
    r"^il\s+faut\s+",
    r"^go\s+",
]

ERROR_PATTERNS = [
    r"\berror\b",
    r"\bexception\b",
    r"\btraceback\b",
    r"\bnameerror\b",
    r"\bsyntaxerror\b",
    r"\btypeerror\b",
    r"\bvalueerror\b",
    r"\bkeyerror\b",
    r"\bindexerror\b",
    r"\battributeerror\b",
    r"\bmodule not found\b",
    r"\bcommand not found\b",
    r"\bpermission denied\b",
    r"\bfailed\b",
    r"\bnot found\b",
    r"\bauth\b",
    r"\blogout\b",
    r"\bunauthorized\b",
    r"\bforbidden\b",
]

TECH_PATTERNS = {
    "Python": r"\bpython\b|\.py\b|tkinter|pip\b",
    "VS Code": r"\bvscode\b|\bvisual studio code\b|\bcode\b",
    "Docker": r"\bdocker\b|\bcontainer\b|\bdocker-compose\b",
    "Node.js": r"\bnode\b|\bnpm\b|\byarn\b|\bpnpm\b",
    "Git": r"\bgit\b|\bcommit\b|\bbranch\b|\bmerge\b|\brebase\b",
    "CLI": r"\bbash\b|\bterminal\b|\bscript\b|\bcommand\b|\bshell\b|\bps1\b",
    "Claude": r"\bclaude\b|\bclaude code\b",
    "Notion": r"\bnotion\b",
    "Gmail": r"\bgmail\b",
    "Google Calendar": r"\bcalendar\b",
    "Google Drive": r"\bdrive\b",
    "PowerShell": r"\bpowershell\b|\.ps1\b",
    "Ansible": r"\bansible\b",
    "JSON": r"\bjson\b|jsonl",
    "Tkinter": r"\btkinter\b",
}

INTENT_PATTERNS = {
    "refactorisation": [r"factoriser", r"refactor", r"organiser le code", r"améliorer le code"],
    "suivi_modifs": [r"suivre.*modifs", r"modifs.*vscode", r"voir.*direct", r"suivre.*direct"],
    "lancement_local": [r"d[ée]marrer.*local", r"lancer.*app", r"toutes les app", r"mode local", r"parall[eè]le"],
    "authentification": [r"\bauth\b", r"\blogout\b", r"\blogin\b"],
    "exploration_fichiers": [r"\bls\b", r"exploration", r"parcourir", r"arborescence", r"fichier"],
    "optimisation_claude": [r"tokens", r"co[uû]t", r"context", r"mcp", r"optimiser claude", r"claude ai"],
    "debug_python": [r"\btraceback\b", r"\bnameerror\b", r"\bpython\b", r"\.py\b"],
    "pilotage_ui": [r"merge history", r"clean history", r"analyze", r"generate memory", r"run all", r"refresh advisor", r"result run all"],
}

APP_ACTION_TO_COMMAND = {
    "merge_history": "fusion historique",
    "clean_history": "nettoyage historique",
    "analyze_history": "analyse historique",
    "generate_memory": "génération mémoire",
    "run_all": "pipeline complet",
    "refresh_advisor": "actualiser advisor",
}


@dataclass
class HistoryEntry:
    display: str
    timestamp: Optional[int]
    project: str
    session_id: str
    source_file: str
    event_type: str = "conversation"
    action: str = ""


class HistoryService:
    def __init__(self, logger=None):
        self.logger = logger or (lambda msg: None)
        ensure_dirs()

    def _log(self, msg: str) -> None:
        self.logger(msg)

    def discover_history_files(self) -> List[Path]:
        candidates: List[Path] = []

        if LEGACY_HISTORY.exists():
            candidates.append(LEGACY_HISTORY)

        if APP_HISTORY_FILE.exists():
            candidates.append(APP_HISTORY_FILE)

        for path in RAW_DIR.rglob("history*"):
            if path.is_file():
                candidates.append(path)

        filtered: List[Path] = []
        seen = set()
        for path in candidates:
            resolved = str(path.resolve())
            if path.resolve() == MERGED_HISTORY_FILE.resolve():
                continue
            if resolved not in seen:
                seen.add(resolved)
                filtered.append(path)

        return sorted(filtered)

    def parse_history_file(self, path: Path) -> List[HistoryEntry]:
        entries: List[HistoryEntry] = []
        text = path.read_text(encoding="utf-8", errors="replace")

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue

            try:
                obj = json.loads(line)
            except Exception:
                continue

            display = str(obj.get("display", "")).strip()
            action = str(obj.get("action", "")).strip()
            event_type = str(
                obj.get("event_type") or obj.get("type") or "conversation"
            ).strip() or "conversation"

            if not display and not action:
                continue

            effective_display = display or action

            entries.append(
                HistoryEntry(
                    display=effective_display,
                    timestamp=obj.get("timestamp"),
                    project=str(obj.get("project", "")).strip(),
                    session_id=str(obj.get("sessionId", "") or obj.get("session_id", "")).strip(),
                    source_file=str(path),
                    event_type=event_type,
                    action=action,
                )
            )

        return entries

    def merge_history(self) -> Dict:
        files = self.discover_history_files()
        all_entries: List[HistoryEntry] = []

        for file_path in files:
            parsed = self.parse_history_file(file_path)
            all_entries.extend(parsed)
            self._log(f"Loaded {len(parsed)} entries from {file_path}")

        all_entries.sort(key=lambda e: (e.timestamp or 0, e.session_id, e.display, e.source_file))
        MERGED_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

        with MERGED_HISTORY_FILE.open("w", encoding="utf-8") as fh:
            for entry in all_entries:
                fh.write(json.dumps(entry.__dict__, ensure_ascii=False) + "\n")

        source_breakdown = Counter(Path(e.source_file).name for e in all_entries)

        return {
            "files_found": len(files),
            "entries_merged": len(all_entries),
            "output": str(MERGED_HISTORY_FILE),
            "files": [str(p) for p in files],
            "source_breakdown": dict(source_breakdown),
        }

    def _normalize_for_dedup(self, text: str) -> str:
        normalized = text.lower().strip()
        normalized = re.sub(r"\s+", " ", normalized)
        normalized = re.sub(r"[\"'`]+", "", normalized)
        return normalized

    def clean_history(self) -> Dict:
        if not MERGED_HISTORY_FILE.exists():
            self.merge_history()

        entries = self.parse_history_file(MERGED_HISTORY_FILE)
        cleaned = []
        seen = set()
        dropped_noise = 0
        dropped_duplicates = 0

        for entry in entries:
            normalized = self._normalize_for_dedup(entry.display)

            if entry.event_type not in {"ui_action", "pipeline_result"}:
                if normalized in NOISE_COMMANDS or normalized in SHORT_NOISE:
                    dropped_noise += 1
                    continue

                if normalized in BAD_WORDS:
                    dropped_noise += 1
                    continue

                if len(normalized) <= 2:
                    dropped_noise += 1
                    continue

            if entry.event_type == "ui_action":
                dedup_key = (
                    entry.event_type,
                    entry.action or normalized,
                    entry.timestamp,
                )
            elif entry.event_type == "pipeline_result":
                dedup_key = (
                    entry.event_type,
                    entry.action or normalized,
                    entry.timestamp,
                )
            elif entry.event_type == "log":
                dedup_key = (
                    entry.event_type,
                    normalized,
                )
            else:
                dedup_key = (
                    normalized,
                    entry.project or "",
                    entry.session_id or "",
                    entry.event_type,
                )

            if dedup_key in seen:
                dropped_duplicates += 1
                continue

            seen.add(dedup_key)
            cleaned.append(entry.__dict__)

        save_json(CLEANED_HISTORY_FILE, cleaned)

        return {
            "entries_in": len(entries),
            "entries_out": len(cleaned),
            "dropped": dropped_noise + dropped_duplicates,
            "dropped_noise": dropped_noise,
            "dropped_duplicates": dropped_duplicates,
            "output": str(CLEANED_HISTORY_FILE),
        }

    def _preclean_text(self, text: str) -> str:
        lowered = text.lower().strip()
        for pattern in LEADING_PHRASE_PATTERNS:
            lowered = re.sub(pattern, "", lowered)
        lowered = re.sub(r"[^\wÀ-ÿ\s\-/\.]", " ", lowered)
        lowered = re.sub(r"\s+", " ", lowered).strip()
        return lowered

    def _extract_command_phrase(self, text: str, action: str = "", event_type: str = "") -> Optional[str]:
        if event_type in {"ui_action", "pipeline_result"} and action:
            return APP_ACTION_TO_COMMAND.get(action, action.replace("_", " "))

        lowered = self._preclean_text(text)

        special_patterns = [
            (r"factoriser.*code|organiser.*code|refactor", "factoriser code"),
            (r"suivre.*modifs.*vscode|suivre.*modifs", "suivre modifs"),
            (r"d[ée]marrer.*app.*local|lancer.*app.*local|toutes les app", "lancer applis local"),
            (r"optimiser.*claude|claude.*co[uû]t|claude.*context|claude.*mcp", "optimiser claude"),
            (r"logout|login|auth", "authentification"),
        ]

        for pattern, label in special_patterns:
            if re.search(pattern, lowered):
                return label

        words = re.findall(r"\b[a-zA-ZÀ-ÿ][a-zA-ZÀ-ÿ0-9_-]{2,}\b", lowered)
        filtered = [
            w for w in words
            if w not in STOPWORDS
            and w not in BAD_WORDS
            and not w.isdigit()
        ]

        if not filtered:
            return None

        return " ".join(filtered[:2]) if len(filtered) >= 2 else filtered[0]

    def _detect_intents(self, text: str, action: str = "", event_type: str = "") -> List[str]:
        found = []
        lowered = text.lower()

        if event_type in {"ui_action", "pipeline_result"}:
            found.append("pilotage_ui")
            if action in {"merge_history", "clean_history", "analyze_history", "generate_memory", "run_all", "refresh_advisor"}:
                found.append("optimisation_claude")

        for label, patterns in INTENT_PATTERNS.items():
            if any(re.search(pattern, lowered) for pattern in patterns):
                found.append(label)

        return sorted(set(found))

    def analyze_history(self) -> Dict:
        if not CLEANED_HISTORY_FILE.exists():
            self.clean_history()

        data = json.loads(CLEANED_HISTORY_FILE.read_text(encoding="utf-8"))

        commands = Counter()
        projects = Counter()
        sessions = defaultdict(list)
        errors = Counter()
        technologies = Counter()
        intents = Counter()
        event_types = Counter()
        source_breakdown = Counter()

        for item in data:
            display = item.get("display", "").strip()
            project = item.get("project", "").strip() or "(none)"
            session_id = item.get("session_id") or item.get("sessionId") or "(unknown)"
            action = item.get("action", "")
            event_type = item.get("event_type", "conversation")
            source_file = item.get("source_file", "")

            lowered = display.lower()

            phrase = self._extract_command_phrase(display, action=action, event_type=event_type)
            if phrase:
                commands[phrase] += 1

            projects[project] += 1
            sessions[session_id or "(unknown)"].append(item)
            event_types[event_type] += 1
            if source_file:
                source_breakdown[Path(source_file).name] += 1

            for pattern in ERROR_PATTERNS:
                if re.search(pattern, lowered):
                    errors[display[:120]] += 1
                    break

            for label, pattern in TECH_PATTERNS.items():
                if re.search(pattern, lowered) or re.search(pattern, project.lower()):
                    technologies[label] += 1

            for intent in self._detect_intents(display, action=action, event_type=event_type):
                intents[intent] += 1

        sessions_index = []
        for session_id, items in sessions.items():
            timestamps = [i.get("timestamp") for i in items if i.get("timestamp")]
            projects_in_session = sorted({i.get("project", "") for i in items if i.get("project")})
            sessions_index.append({
                "session_id": session_id,
                "message_count": len(items),
                "projects": projects_in_session,
                "first_timestamp": min(timestamps) if timestamps else None,
                "last_timestamp": max(timestamps) if timestamps else None,
                "preview": items[0].get("display", "")[:120] if items else "",
            })

        sessions_index.sort(key=lambda x: x["last_timestamp"] or 0, reverse=True)

        conversation_count = event_types.get("conversation", 0)
        app_count = sum(v for k, v in event_types.items() if k != "conversation")
        total_raw = len(self.parse_history_file(MERGED_HISTORY_FILE)) if MERGED_HISTORY_FILE.exists() else 0
        noise_ratio = round(1 - (len(data) / total_raw), 3) if total_raw else 0.0

        save_json(SESSIONS_INDEX_FILE, sessions_index)
        save_json(COMMAND_STATS_FILE, {
            "history_volume": {
                "entries_cleaned": len(data),
                "session_count": len(sessions_index),
                "entries_raw": total_raw,
                "noise_ratio": noise_ratio,
            },
            "source_breakdown": dict(source_breakdown),
            "event_type_breakdown": dict(event_types),
            "conversation_count": conversation_count,
            "app_event_count": app_count,
            "top_commands": commands.most_common(30),
            "top_projects": projects.most_common(20),
            "top_technologies": technologies.most_common(20),
            "top_intents": intents.most_common(20),
        })
        save_json(ERROR_STATS_FILE, {
            "top_errors": errors.most_common(20)
        })

        return {
            "sessions": len(sessions_index),
            "entries_cleaned": len(data),
            "source_breakdown": dict(source_breakdown),
            "event_type_breakdown": dict(event_types),
            "conversation_count": conversation_count,
            "app_event_count": app_count,
            "noise_ratio": noise_ratio,
            "top_commands": commands.most_common(10),
            "top_projects": projects.most_common(10),
            "top_errors": errors.most_common(10),
            "top_technologies": technologies.most_common(10),
            "top_intents": intents.most_common(10),
        }

    def generate_memory(self) -> Dict:
        stats = self.analyze_history()
        cleaned = json.loads(CLEANED_HISTORY_FILE.read_text(encoding="utf-8")) if CLEANED_HISTORY_FILE.exists() else []

        projects = [p for p, _ in stats["top_projects"][:10]]
        technologies = [t for t, _ in stats["top_technologies"][:10]]
        frequent_commands = [c for c, _ in stats["top_commands"][:15]]
        errors = [e for e, _ in stats["top_errors"][:10]]
        intents = [i for i, _ in stats["top_intents"][:10]]

        architecture_lines = [
            "# Architecture notes",
            "",
            "## Projects",
        ] + ([f"- {project}" for project in projects if project] or ["- None detected"])

        stack_lines = [
            "# Stack",
            "",
        ] + ([f"- {tech}" for tech in technologies] or ["- None detected"])

        bug_lines = [
            "# Errors",
            "",
        ] + ([f"- {err}" for err in errors] or ["No errors detected"])

        habit_lines = [
            "# Habits",
            "",
            "## Frequent commands",
        ] + ([f"- {cmd}" for cmd in frequent_commands] or ["- None detected"])

        if intents:
            habit_lines += ["", "## Inferred intents"] + [f"- {intent}" for intent in intents]

        notable_requests = []
        for item in cleaned:
            display = item.get("display", "").strip()
            event_type = item.get("event_type", "conversation")
            if event_type != "conversation":
                continue
            if len(display) < 25:
                continue
            lowered = display.lower()
            if lowered in NOISE_COMMANDS:
                continue
            notable_requests.append(f"- {display}")
            if len(notable_requests) >= 10:
                break

        if notable_requests:
            habit_lines += ["", "## Example requests"] + notable_requests

        ARCHITECTURE_FILE.write_text("\n".join(architecture_lines) + "\n", encoding="utf-8")
        STACK_FILE.write_text("\n".join(stack_lines) + "\n", encoding="utf-8")
        BUGS_FILE.write_text("\n".join(bug_lines) + "\n", encoding="utf-8")
        HABITS_FILE.write_text("\n".join(habit_lines) + "\n", encoding="utf-8")

        return {
            "memory_files_written": [
                str(ARCHITECTURE_FILE),
                str(STACK_FILE),
                str(BUGS_FILE),
                str(HABITS_FILE),
            ]
        }

    def run_all(self) -> Dict:
        merge = self.merge_history()
        clean = self.clean_history()
        analyze = self.analyze_history()
        memory = self.generate_memory()

        return {
            "merge": merge,
            "clean": clean,
            "analyze": analyze,
            "memory": memory,
            "files": {
                "cleaned_history": str(CLEANED_HISTORY_FILE),
                "sessions_index": str(SESSIONS_INDEX_FILE),
                "command_stats": str(COMMAND_STATS_FILE),
                "error_stats": str(ERROR_STATS_FILE),
            },
        }
