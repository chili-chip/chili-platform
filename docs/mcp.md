# MCP (Model Context Protocol)

Chili Platform exposes a **stateless** MCP endpoint on the same Django app as the REST API, using [django-stateless-mcp](https://github.com/Streamlined-Analytics/django-stateless-mcp) (MCP spec **2026-07-28**, WSGI-friendly).

## Enable locally

| Context | Default | How to reach `/mcp/` |
|---|---|---|
| `manage.py` / `npm test` (SQLite) | **On** | `http://127.0.0.1:8787/mcp/` only if you run a server; tests hit `/mcp/` in-process |
| `npm run dev` (pywrangler / D1) | **Off** | Set `MCP_ENABLED=true` in `.dev.vars` (see `.dev.vars.example`) |

1. Start the API: `npm run dev` → `http://127.0.0.1:8787`
2. Endpoint: **`POST http://127.0.0.1:8787/mcp/`** (streamable HTTP, one JSON-RPC request per POST)

Tools live in `src/app/mcp.py`. Add per-app tools by creating `mcp.py` in any installed app (same pattern as `admin.py`); `django_stateless_mcp` autodiscovers them at startup.

## Cursor MCP config

Add to `.cursor/mcp.json` (project) or Cursor **Settings → MCP**:

```json
{
  "mcpServers": {
    "chili-platform": {
      "url": "http://127.0.0.1:8787/mcp/",
      "headers": {
        "MCP-Protocol-Version": "2026-07-28"
      }
    }
  }
}
```

Start `npm run dev` before connecting. Cursor uses streamable HTTP against the running Worker dev server; there is no separate stdio process.

## Smoke check (curl)

```bash
curl -sS -X POST http://127.0.0.1:8787/mcp/ \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'MCP-Method: tools/list' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}'
```

You should see `platform_health` and `list_forum_categories` in the result.

## Security

The endpoint is intended for **local agent development**. Do not enable `MCP_ENABLED` in production unless you add authentication (for example `mcp_view(..., token_verifier=...)` from django-stateless-mcp) and restrict exposed tools.
