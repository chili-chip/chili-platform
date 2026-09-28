# MCP (Model Context Protocol)

Chili Platform exposes an MCP endpoint on the same Django app as the REST API, using **[django-mcp-server](https://github.com/gts360/django-mcp-server)** (streamable HTTP, WSGI-compatible with pywrangler).

## Enable locally

| Context | Default | Endpoint |
|---|---|---|
| `manage.py` / `npm test` (SQLite) | **On** | `/mcp` (in-process tests) |
| `npm run dev` (pywrangler / D1) | **Off** | Set `MCP_ENABLED=true` in `.dev.vars` |

1. Copy env: `cp .dev.vars.example .dev.vars` (includes `MCP_ENABLED=true` for pywrangler dev).
2. Start the API: `npm run dev` → `http://127.0.0.1:8787`
3. MCP URL: **`http://127.0.0.1:8787/mcp`** (no trailing slash — required by django-mcp-server)

Tools are declared in `src/community/mcp.py` (`MCPToolset`). Add more by creating `mcp.py` in any installed app; `mcp_server` autodiscovers them at startup (like `admin.py`).

Inspect registered tools:

```bash
uv run python src/manage.py mcp_inspect
```

## Cursor MCP config

Add to **`.cursor/mcp.json`** (project) or Cursor **Settings → MCP**:

```json
{
  "mcpServers": {
    "chili-platform": {
      "url": "http://127.0.0.1:8787/mcp"
    }
  }
}
```

Start `npm run dev` before connecting. Cursor talks streamable HTTP to the running dev server.

### Optional: stdio via manage.py

For clients that only support local stdio (e.g. some Claude Desktop setups), point at the project venv Python and `manage.py stdio_server` — see django-mcp-server README. Chili’s primary path for Cursor is the HTTP URL above.

## Smoke check (curl)

Initialize, then list tools:

```bash
curl -sS -X POST http://127.0.0.1:8787/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}'

curl -sS -X POST http://127.0.0.1:8787/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

Expect `platform_health`, `list_forum_categories`, and `get_server_instructions`.

## Settings (reference)

In `src/app/settings.py`:

- `MCP_ENABLED` — mount `/mcp` (default off on Workers, on for local SQLite)
- `DJANGO_MCP_GLOBAL_SERVER_CONFIG` — server name, instructions, `stateless: True` for Workers
- `DJANGO_MCP_AUTHENTICATION_CLASSES` — empty for local dev (no auth on MCP)

## Security

MCP is for **local agent development**. Production Workers keep `MCP_ENABLED` off by default. Before exposing MCP publicly, set `DJANGO_MCP_AUTHENTICATION_CLASSES` (for example DRF token or OAuth2 per django-mcp-server docs) and limit tools.
