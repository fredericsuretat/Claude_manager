import shutil
from pathlib import Path

# Chemin vers le binaire claude — chercher dans les emplacements connus (nvm, etc.)
def _find_claude() -> str:
    candidates = [
        shutil.which("claude"),
        "/home/frederic/.nvm/versions/node/v22.22.0/bin/claude",
        "/usr/local/bin/claude",
        "/usr/bin/claude",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    return "claude"  # fallback, échouera si absent du PATH

CLAUDE_BIN = _find_claude()

BASE_DIR = Path.home() / ".claude"
CONFIG_DIR = BASE_DIR / "config"
MEMORY_DIR = BASE_DIR / "memory"
LOGS_DIR = BASE_DIR / "logs"
SESSIONS_DIR = LOGS_DIR / "sessions"
RAW_DIR = LOGS_DIR / "raw"
ANALYTICS_DIR = BASE_DIR / "analytics"
RUNTIME_DIR = BASE_DIR / "runtime"

LEGACY_HISTORY = BASE_DIR / "history.jsonl"
APP_HISTORY_FILE = LOGS_DIR / "app_history.jsonl"
MERGED_HISTORY_FILE = LOGS_DIR / "history.jsonl"

CLAUDE_MD_FILE = MEMORY_DIR / "claude.md"
ARCHITECTURE_FILE = MEMORY_DIR / "architecture.md"
STACK_FILE = MEMORY_DIR / "stack.md"
BUGS_FILE = MEMORY_DIR / "bugs.md"
HABITS_FILE = MEMORY_DIR / "habits.md"

CLEANED_HISTORY_FILE = ANALYTICS_DIR / "cleaned_history.json"
SESSIONS_INDEX_FILE = ANALYTICS_DIR / "sessions_index.json"
COMMAND_STATS_FILE = ANALYTICS_DIR / "command_stats.json"
ERROR_STATS_FILE = ANALYTICS_DIR / "error_stats.json"
ADVISOR_REPORT_FILE = ANALYTICS_DIR / "advisor_report.json"

MEMORY_FILES = [CLAUDE_MD_FILE, ARCHITECTURE_FILE, STACK_FILE, BUGS_FILE, HABITS_FILE]
ANALYTICS_FILES = [CLEANED_HISTORY_FILE, SESSIONS_INDEX_FILE, COMMAND_STATS_FILE, ERROR_STATS_FILE, ADVISOR_REPORT_FILE]

NTFY_TOPIC = "ntfyclaudetasknextmobilesurmonmobilesuretatfrederic"

SCHEDULED_NOTIFS_FILE = RUNTIME_DIR / "scheduled_notifs.json"
MCP_SETTINGS_FILE = BASE_DIR / "settings.json"
MCP_PROFILES_FILE = RUNTIME_DIR / "mcp_profiles.json"


def ensure_dirs():
    for p in [BASE_DIR, CONFIG_DIR, MEMORY_DIR, LOGS_DIR, SESSIONS_DIR, RAW_DIR, ANALYTICS_DIR, RUNTIME_DIR]:
        p.mkdir(parents=True, exist_ok=True)
    if not APP_HISTORY_FILE.exists():
        APP_HISTORY_FILE.touch()
    if not CLAUDE_MD_FILE.exists():
        CLAUDE_MD_FILE.write_text("- Work with minimal tokens.\n- Prefer short, actionable answers.\n", encoding="utf-8")
    for f in [ARCHITECTURE_FILE, STACK_FILE, BUGS_FILE, HABITS_FILE]:
        f.touch(exist_ok=True)
