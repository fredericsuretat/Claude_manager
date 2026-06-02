#!/usr/bin/env python3
"""Minimal stdio MCP server exposing the Memory Control Center endpoints
running on the local Claude Manager (FastAPI port 8765).

Wire it in ~/.claude/settings.json (or equivalent) as:

  "mcpServers": {
    "memory-cc": {
      "command": "/home/frederic/Documents/Dev/Claude_manager/venv/bin/python",
      "args": ["/home/frederic/Documents/Dev/Claude_manager/mcp_server/memory_cc_server.py"]
    }
  }

Protocol: JSON-RPC 2.0 over stdio (LSP-style with Content-Length headers
fallback, but Claude Code uses newline-delimited JSON by default). We
support both: try newline-delimited first; if the line starts with
"Content-Length:" we parse headers.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from typing import Any

BASE_URL = os.environ.get("MEMORY_CC_URL", "http://127.0.0.1:8765")
TIMEOUT = 10

SERVER_INFO = {"name": "memory-cc", "version": "1.0.0"}
PROTOCOL_VERSION = "2024-11-05"


def _http(method: str, path: str, params: dict | None = None, body: dict | None = None) -> Any:
    qs = ""
    if params:
        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        if clean:
            qs = "?" + urllib.parse.urlencode(clean)
    url = f"{BASE_URL}{path}{qs}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return {"error": f"HTTP {method} {url} failed: {e}"}
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw}


TOOLS = [
    {
        "name": "memex_skim",
        "description": (
            "Aperçu léger d'un .md : frontmatter + headings + 8 premières lignes utiles. "
            "À PRÉFÉRER à un Read complet quand on cherche juste à décider si le fichier est pertinent. "
            "Économie ~10× tokens vs Read."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "ID du root (ex: 'claude-global', 'project:Docker', 'repo:site-ateris')"},
                "rel": {"type": "string", "description": "Chemin relatif (ex: 'MEMORY.md', 'project_x402_lab.md')"},
                "body_lines": {"type": "integer", "description": "Nb de lignes de preview (default 8)", "default": 8},
            },
            "required": ["root", "rel"],
        },
    },
    {
        "name": "memex_section",
        "description": (
            "Lit UNE section markdown d'un .md (du heading donné jusqu'au prochain heading de niveau ≤). "
            "À UTILISER après un skim quand on sait quel ## on veut lire. Économie ~3-10× sur les gros .md."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string"},
                "rel": {"type": "string"},
                "heading": {"type": "string", "description": "Titre exact OU slug de la section"},
            },
            "required": ["root", "rel", "heading"],
        },
    },
    {
        "name": "memex_search_meta",
        "description": (
            "Recherche full-text mais SANS snippets : renvoie uniquement les fichiers + line + count. "
            "À UTILISER quand on veut savoir QUELS .md contiennent un mot. Économie ~3-5× vs /search complet."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string"},
                "limit": {"type": "integer", "default": 500},
            },
            "required": ["q"],
        },
    },
    {
        "name": "memex_search_headings",
        "description": (
            "Cherche un terme uniquement dans les titres ##/### de tous les .md indexés. "
            "Réponse à 'où est documenté X ?' en ~50 tokens."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string"},
                "limit": {"type": "integer", "default": 200},
            },
            "required": ["q"],
        },
    },
    {
        "name": "memex_index",
        "description": (
            "Parse un fichier index (MEMORY.md par défaut, ou CLAUDE.md) et renvoie liste structurée + "
            "détection des entries manquantes / .md orphelins."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string"},
                "name": {"type": "string", "default": "MEMORY.md"},
            },
            "required": ["root"],
        },
    },
    {
        "name": "memex_heatmap",
        "description": (
            "Top des .md les plus consultés via les endpoints memex (skim/section/read). "
            "Utile pour identifier les candidats à un meilleur découpage."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 30},
            },
        },
    },
    {
        "name": "memex_index_health",
        "description": (
            "Audit qualité de tous les MEMORY.md / CLAUDE.md : entries cassées, .md orphelins. "
            "Renvoie un rapport par root avec totaux."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "memex_tree",
        "description": "Liste tous les roots et leurs .md (vue d'ensemble du graphe mémoire).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_empty": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "memex_recent",
        "description": "Les .md récemment modifiés (par mtime), tous repos confondus.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 40}},
        },
    },
    {
        "name": "memex_create_memory",
        "description": (
            "Crée un nouveau fichier de mémoire (user/feedback/project/reference) avec "
            "frontmatter correctement formaté ET met à jour le MEMORY.md du root. "
            "À UTILISER quand l'utilisateur demande de sauvegarder un fait persistant. "
            "Plus sûr que d'écrire les fichiers à la main : garantit la structure."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Root id, en général 'claude:proj:-home-frederic-Documents-Docker'"},
                "slug": {"type": "string", "description": "Nom du fichier sans extension (ex: 'feedback_xyz')"},
                "name": {"type": "string", "description": "Titre court de la mémoire (champ name du frontmatter)"},
                "description": {"type": "string", "description": "Description courte (champ description + ligne MEMORY.md)"},
                "type": {"type": "string", "enum": ["user", "feedback", "project", "reference"]},
                "body": {"type": "string", "description": "Contenu Markdown (sans frontmatter, sera prepended)"},
            },
            "required": ["root", "slug", "name", "description", "type", "body"],
        },
    },
]


def _dispatch_tool(name: str, args: dict) -> Any:
    if name == "memex_skim":
        return _http("GET", "/api/memory-explorer/skim", params={"root": args.get("root"), "rel": args.get("rel"), "body_lines": args.get("body_lines", 8)})
    if name == "memex_section":
        return _http("GET", "/api/memory-explorer/section", params={"root": args.get("root"), "rel": args.get("rel"), "heading": args.get("heading")})
    if name == "memex_search_meta":
        return _http("GET", "/api/memory-explorer/search-meta", params={"q": args.get("q"), "limit": args.get("limit", 500)})
    if name == "memex_search_headings":
        return _http("GET", "/api/memory-explorer/search-headings", params={"q": args.get("q"), "limit": args.get("limit", 200)})
    if name == "memex_index":
        return _http("GET", "/api/memory-explorer/index", params={"root": args.get("root"), "name": args.get("name", "MEMORY.md")})
    if name == "memex_heatmap":
        return _http("GET", "/api/memory-explorer/heatmap", params={"limit": args.get("limit", 30)})
    if name == "memex_index_health":
        return _http("GET", "/api/memory-explorer/index-health")
    if name == "memex_tree":
        # tree endpoint ignores include_empty server-side (default False); kept for parity
        return _http("GET", "/api/memory-explorer/tree")
    if name == "memex_recent":
        return _http("GET", "/api/memory-explorer/recent", params={"limit": args.get("limit", 40)})
    if name == "memex_create_memory":
        return _http("POST", "/api/memory-explorer/create-memory", body={
            "root": args.get("root"),
            "slug": args.get("slug"),
            "name": args.get("name"),
            "description": args.get("description"),
            "type": args.get("type"),
            "body": args.get("body"),
            "index_name": args.get("index_name", "MEMORY.md"),
        })
    return {"error": f"Unknown tool: {name}"}


def _handle(req: dict) -> dict | None:
    """Return a JSON-RPC response dict, or None for notifications."""
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params") or {}

    # Notifications (no id) → no response
    is_notification = "id" not in req

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        }
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}
        try:
            payload = _dispatch_tool(tool_name, tool_args)
        except Exception as e:
            payload = {"error": str(e)}
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": text}],
                "isError": isinstance(payload, dict) and "error" in payload,
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if is_notification:
        return None
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def _read_message():
    """Read one JSON-RPC message from stdin. Supports both newline-delimited and
    LSP-style (Content-Length headers)."""
    line = sys.stdin.readline()
    if not line:
        return None
    stripped = line.strip()
    if not stripped:
        return _read_message()
    if stripped.lower().startswith("content-length:"):
        length = int(stripped.split(":", 1)[1].strip())
        # consume headers until blank
        while True:
            hdr = sys.stdin.readline()
            if not hdr or hdr.strip() == "":
                break
        body = sys.stdin.read(length)
        return json.loads(body)
    return json.loads(stripped)


def _write_message(msg: dict):
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    sys.stderr.write(f"[memory-cc] starting, backend={BASE_URL}\n")
    sys.stderr.flush()
    while True:
        try:
            req = _read_message()
        except Exception as e:
            sys.stderr.write(f"[memory-cc] parse error: {e}\n")
            continue
        if req is None:
            break
        resp = _handle(req)
        if resp is not None:
            _write_message(resp)


if __name__ == "__main__":
    main()
