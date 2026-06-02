# memory-cc — MCP server for Memory Control Center

Stdio MCP server that proxies the Memory Control Center endpoints (running
on the local Claude Manager FastAPI service, port 8765) into native MCP
tools that Claude Code can call without curl.

## Tools exposed

| Tool | Endpoint | Use |
|---|---|---|
| `memex_skim` | `/skim` | Aperçu d'un .md (~10× moins de tokens qu'un Read) |
| `memex_section` | `/section` | Lit une section ## précise |
| `memex_search_meta` | `/search-meta` | Recherche sans snippets |
| `memex_search_headings` | `/search-headings` | Recherche dans les titres |
| `memex_index` | `/index` | Parse MEMORY.md + détecte cassures |
| `memex_heatmap` | `/heatmap` | Top fichiers consultés |
| `memex_index_health` | `/index-health` | Audit qualité de tous les MEMORY.md |
| `memex_tree` | `/tree` | Vue d'ensemble |
| `memex_recent` | `/recent` | .md récemment modifiés |

## Wire it in

Merge `claude-settings-snippet.json` into `~/.claude/settings.json`
(under the `mcpServers` key). Restart Claude Code.

Pre-req : le service `claude-control.service` doit tourner (port 8765).

## Test manuel

```bash
(
  echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}'
  echo '{"jsonrpc":"2.0","method":"notifications/initialized"}'
  echo '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
) | /home/frederic/Documents/Dev/Claude_manager/venv/bin/python \
    /home/frederic/Documents/Dev/Claude_manager/mcp_server/memory_cc_server.py
```
