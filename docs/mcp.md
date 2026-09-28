# MCP (Model Context Protocol)

Chili Platform exposes an MCP endpoint on the same Django app as the REST API, using **[django-mcp-server](https://github.com/gts360/django-mcp-server)** (streamable HTTP, WSGI-compatible with pywrangler).

## Enable locally

| Context | Default | Endpoint |
|---|---|---|
| `manage.py` / `npm test` (SQLite) | **On** | `/mcp` (in-process tests) |
| `npm run dev` (pywrangler / D1) | **Off** | Set `MCP_ENABLED=true` in `.dev.vars` |

1. Copy env: `cp .dev.vars.example .dev.vars` (includes `MCP_ENABLED=true` for pywrangler dev).
2. Start the API: `npm run dev` → `http://127.0.0.1:8787`
3. MCP URL: **`http://127.0.0.1:8787/mcp`** (no trailing slash)

MCP is mounted only when `MCP_ENABLED` is true. Production Workers default to off.

## Cursor connection

This repo includes **`.cursor/mcp.json`**:

```json
{
  "mcpServers": {
    "chili-platform": {
      "url": "http://127.0.0.1:8787/mcp"
    }
  }
}
```

Start `npm run dev` with `MCP_ENABLED=true` before connecting. Cursor uses streamable HTTP against the dev server (not stdio).

Optional stdio for other clients: `uv run python src/manage.py stdio_server` (see upstream README).

## Tools exposed

| Tool | Purpose |
|---|---|
| `platform_health` | Same payload as `GET /api/health/` |
| `get_server_instructions` | Server instructions (django-mcp-server built-in) |
| `query_data_collections` | **Read-only** MongoDB-style queries over registered model collections |
| `create_forum_post` | **Local write:** create a forum post (dev only; see below) |

Most model access is read-only via `query_data_collections`. One explicit write tool exists for forum smoke tests and agent demos.

### Local write: `create_forum_post`

**Dev only.** The tool refuses to run when `MCP_ENABLED` is false (production Workers default). It does not replace the authenticated REST API for normal clients.

| Argument | Default | Meaning |
|---|---|---|
| `title` | (required) | Post title (max 160 chars) |
| `content` | (required) | Post body |
| `category_slug` | `general` | Must match a seeded category (`seed_forum`) |

Author user: `MCP_FORUM_POST_AUTHOR_USERNAME` (default `admin` from settings). Ensure that user exists (ops bootstrap or local admin).

Example MCP `tools/call`:

```json
{
  "name": "create_forum_post",
  "arguments": {
    "title": "MCP test post",
    "content": "Hello from an MCP agent.",
    "category_slug": "general"
  }
}
```

Returns `id`, `slug`, `title`, `category_slug`, and `community_path` (e.g. `/community/post/42` for the Angular route).

### Collections (read-only queries)

**accounts**

| Collection | Scope |
|---|---|
| `user` | Public profile fields (no email, password, or Stripe ids) |

**community**

| Collection | Scope |
|---|---|
| `forumcategory` | All categories |
| `forumpost` | All posts |
| `forumcomment` | All comments |

**games**

| Collection | Scope |
|---|---|
| `game` | `released=True` only; **excludes** Bitsy `data` |

**store**

| Collection | Scope |
|---|---|
| `product` | Active products; Stripe catalog ids excluded |
| `productimage` | Images for active products (file field omitted) |
| `order` | Authenticated user's orders only; shipping + Stripe ids excluded |
| `orderitem` | Line items for authenticated user's orders |

**marketplace**

| Collection | Scope |
|---|---|
| `category` | Marketplace categories |
| `listing` | `published=True` listings |
| `listingtag` | Tags on published listings |
| `rating` | Game star ratings + comments |
| `purchase` | Authenticated user as buyer **or** seller; Stripe ids excluded |
| `connectedaccount` | Authenticated creator; Stripe account id hidden |
| `payout` | Authenticated creator payouts |
| `earning` | Authenticated creator earnings |

Authenticated collections return **empty results** when the MCP request has no logged-in user. Local MCP has **no auth** (`DJANGO_MCP_AUTHENTICATION_CLASSES` is empty), so agents only see public collections unless you add DRF/JWT auth to the MCP view.

Toolset classes live in each app's `mcp.py` (`accounts`, `community`, `games`, `store`, `marketplace`). Shared helpers: `src/app/mcp_helpers.py`.

List tools at runtime:

```bash
uv run python src/manage.py mcp_inspect
```

## Example query

After `initialize`, call `query_data_collections` with a collection name and pipeline, e.g. list active products:

```json
{
  "name": "query_data_collections",
  "arguments": {
    "collection": "product",
    "search_pipeline": [{"$match": {"is_active": true}}, {"$limit": 5}]
  }
}
```

See django-mcp-server docs for pipeline syntax (`$match`, `$sort`, `$limit`, `$project`, …).

## Smoke check (curl)

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

Expect `platform_health`, `query_data_collections`, and `get_server_instructions`.

## Security

MCP is for **local agent development** behind `MCP_ENABLED`. Do not enable on production Workers without `DJANGO_MCP_AUTHENTICATION_CLASSES` and a minimal tool/collection set. Ops routes (`/api/_ops/`) and Django admin are not exposed as MCP tools.
