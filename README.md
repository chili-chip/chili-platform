# Chili Platform — Backend 🌶️

The serverless API for **Chili Platform**. Django runs on Cloudflare Workers with D1 (SQLite at the edge) and R2 for uploaded media.

---

## Features (this scaffold)

* JWT auth, custom user profiles
* Community forum (categories, posts, comments)
* Ops endpoints to migrate/seed D1
* Placeholder apps for store and marketplace

---

## API

Hosted docs (GitHub Pages): **[chili-chip.github.io/chili-platform](https://chili-chip.github.io/chili-platform/)** — overview, Swagger explorer, Redoc reference, and `openapi.yaml`. After merge, set **Settings → Pages → Source** to **GitHub Actions** if the first deploy is waiting on that. Preview locally with `npm run docs`.

| Method | Path | Auth |
|---|---|---|
| GET | `/api/health/` | public |
| POST | `/api/auth/register/` | public |
| POST | `/api/auth/token/` | public |
| POST | `/api/auth/token/refresh/` | public |
| GET/PUT | `/api/profiles/me/` | JWT |
| GET | `/api/profiles/<username>/` | public |
| CRUD | `/api/forum/categories/` | staff write |
| CRUD | `/api/forum/posts/` | JWT write |
| GET/POST | `/api/forum/posts/<id>/comments/` | JWT write |
| CRUD | `/api/forum/comments/` | JWT write |
| POST | `/api/_ops/migrate/` | `X-Ops-Token` |
| POST | `/api/_ops/seed/` | `X-Ops-Token` |

---

## Layout

```text
src/
├── index.py              # Cloudflare Worker entrypoint
├── manage.py
├── app/                  # Django project (settings, urls, ASGI/WSGI)
├── accounts/             # User + profile API
├── community/            # Forum API + seed command
├── store/                # Hardware store (scaffold)
└── marketplace/          # Digital store (scaffold)
wrangler.jsonc            # D1 `DB`, R2 `ASSETS_BUCKET`
pyproject.toml
```

The Worker serves Django through **WSGI** via `django_cf.DjangoCF`. D1's ORM is synchronous and Worker `fetch` is async, so `DJANGO_ALLOW_ASYNC_UNSAFE` is required. `app.asgi` remains available for tests.

---

## Local development

Prerequisites: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 20+ (Wrangler).

```bash
cp .dev.vars.example .dev.vars
npm install
uv sync
uv run python src/manage.py migrate
uv run python src/manage.py seed_forum
npm run collectstatic
npm run dev          # wrangler / pywrangler on http://localhost:8787
```

`uv run python src/manage.py migrate` only touches local SQLite. The Worker uses a **separate D1** database. Apply schema there while `npm run dev` is running:

```bash
curl -X POST http://localhost:8787/api/_ops/bootstrap/ \
  -H "X-Ops-Token: chili-dev-ops-token"
```

That migrates D1, seeds forum categories, and creates `admin` / `chili-dev-admin` from `.dev.vars`. Then sign in at `/admin/login/`.

Replace the placeholder `database_id` in `wrangler.jsonc` after `wrangler d1 create chili-platform`. Create the R2 bucket with `wrangler r2 bucket create chili-platform-assets`. Put `DJANGO_SECRET_KEY` via `uv run pywrangler secret put DJANGO_SECRET_KEY`.

---

## License

MIT. See `LICENSE`.
