"""
McpService — lit et écrit la section mcpServers de ~/.claude/settings.json
et gère des profils sauvegardés.
"""

import json
import threading
from pathlib import Path

from app.config import MCP_SETTINGS_FILE, MCP_PROFILES_FILE

DEFAULT_PROFILES = {
    "MINIMAL": {},
    "DEV": {},
    "PERSONAL": {},
}

_lock = threading.Lock()


def _load_settings() -> dict:
    try:
        if MCP_SETTINGS_FILE.exists():
            return json.loads(MCP_SETTINGS_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_settings(data: dict):
    MCP_SETTINGS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _load_profiles() -> dict:
    try:
        if MCP_PROFILES_FILE.exists():
            return json.loads(MCP_PROFILES_FILE.read_text())
    except Exception:
        pass
    return dict(DEFAULT_PROFILES)


def _save_profiles(profiles: dict):
    MCP_PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
    MCP_PROFILES_FILE.write_text(json.dumps(profiles, indent=2, ensure_ascii=False))


class McpService:
    def get_status(self) -> dict:
        with _lock:
            settings = _load_settings()
            servers = settings.get("mcpServers", {})
            profiles = _load_profiles()
            return {
                "servers": servers,
                "server_count": len(servers),
                "profiles": {name: list(cfg.keys()) for name, cfg in profiles.items()},
            }

    def enable_server(self, name: str, config: dict) -> dict:
        with _lock:
            settings = _load_settings()
            servers = settings.setdefault("mcpServers", {})
            servers[name] = config
            _save_settings(settings)
            return {"ok": True, "name": name}

    def disable_server(self, name: str) -> dict:
        with _lock:
            settings = _load_settings()
            servers = settings.get("mcpServers", {})
            if name in servers:
                del servers[name]
                settings["mcpServers"] = servers
                _save_settings(settings)
                return {"ok": True, "name": name}
            return {"ok": False, "error": "Serveur introuvable"}

    def apply_profile(self, profile_name: str) -> dict:
        with _lock:
            profiles = _load_profiles()
            if profile_name not in profiles:
                return {"ok": False, "error": f"Profil inconnu: {profile_name}"}
            settings = _load_settings()
            settings["mcpServers"] = dict(profiles[profile_name])
            _save_settings(settings)
            return {"ok": True, "profile": profile_name, "servers": list(profiles[profile_name].keys())}

    def save_profile(self, name: str) -> dict:
        with _lock:
            settings = _load_settings()
            servers = settings.get("mcpServers", {})
            profiles = _load_profiles()
            profiles[name] = dict(servers)
            _save_profiles(profiles)
            return {"ok": True, "name": name, "servers": list(servers.keys())}

    def delete_profile(self, name: str) -> dict:
        with _lock:
            if name in DEFAULT_PROFILES:
                return {"ok": False, "error": "Profil par défaut non supprimable"}
            profiles = _load_profiles()
            if name not in profiles:
                return {"ok": False, "error": "Profil introuvable"}
            del profiles[name]
            _save_profiles(profiles)
            return {"ok": True, "name": name}
