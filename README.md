# Chili Platform — Backend 🌶️

The serverless API for **Chili Platform**. Django runs on Cloudflare Workers with D1 (SQLite at the edge) and R2 for uploaded media.

---

## Features (this scaffold)

* JWT auth, custom user profiles
* Bitsy games saved from the creator, with cover images in R2
* Owner Bitsy assistant on Workers AI (`AI` binding)
* Community forum (categories, posts, comments)
* Hardware store: catalog, Stripe Checkout (test mode), order tracking
* Game marketplace: listings, card purchases, creator payouts
* Ops endpoints to migrate/seed D1

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
| GET | `/api/games/?username=` | public released games |
| GET | `/api/games/?released=false` | JWT (own projects) |
| POST | `/api/games/` | JWT |
| GET/PUT/PATCH/DELETE | `/api/games/<id>/` | released games are public; owner write; Bitsy data for owner or library |
| POST | `/api/games/<id>/release/` | JWT (owner). Does not list the game. |
| POST | `/api/games/<id>/assist/` | JWT (owner). Workers AI edit of the saved Bitsy text. |
| CRUD | `/api/forum/categories/` | staff write |
| CRUD | `/api/forum/posts/` | JWT write |
| GET/POST | `/api/forum/posts/<id>/comments/` | JWT write |
| CRUD | `/api/forum/comments/` | JWT write |
| CRUD | `/api/store/products/` | public read, staff write |
| POST | `/api/store/checkout/` | JWT |
| POST | `/api/store/checkout/confirm/` | JWT |
| GET | `/api/store/orders/` | JWT (own orders) |
| POST | `/api/store/stripe/webhook/` | Stripe signature |
| GET | `/api/marketplace/listings/` | public |
| POST | `/api/marketplace/listings/` | JWT (own games) |
| POST | `/api/marketplace/listings/<slug>/rating/` | JWT (one rating, library only) |
| POST | `/api/marketplace/listings/<slug>/checkout/` | JWT |
| GET | `/api/marketplace/library/` | JWT |
| GET | `/api/marketplace/me/` | JWT (creator sales) |
| POST | `/api/_ops/migrate/` | `X-Ops-Token` |
| POST | `/api/_ops/seed/` | `X-Ops-Token` |

### Games

The Bitsy creator saves a project with `POST /api/games/` `{ "title", "data" }` and `PUT /api/games/<id>/`. A project is private (`released: false`) and cannot be sold. `POST /api/games/<id>/release/` turns it into a game that can be sold and keeps the Bitsy `data`. Release does not create a listing. `GET /api/games/?username=` returns that profile's released games. `GET /api/games/?username=<you>&released=false` returns your projects. Bitsy `data` is returned to the owner and to a buyer who has the game in their library. `in_library` is true for that buyer (paid, refunded, or disputed) and for the owner of a released game.

`PATCH /api/games/<id>/` with `{ "cover": "data:image/png;base64,..." }` stores the PNG through the same media storage as product images (R2 on the Worker, local disk in development). The response `cover` field is a media URL. The data URL is not written to the database.

`POST /api/games/<id>/assist/` is owner-only. The body is `{ "message", "history" }`. The Worker loads the saved Bitsy `data` for that game and does not trust a document sent by the browser. It calls Workers AI (`workers.env.AI.run`) and asks for JSON: a short `reply` and `data`, the full Bitsy document. The binding in `wrangler.jsonc` is named `AI`. The model id is the `ASSISTANT_MODEL` var, default `@cf/qwen/qwen2.5-coder-32b-instruct`, so it can change without a code change.

A result is returned with `data` only when that text has a title and at least one room. The creator applies it in the editor, and the existing autosave writes it. This endpoint does not save the game. If the model output fails that check, the response is `reply` and `error` and the game is left unchanged. If the project does not fit in the prompt, the response is an error and the game is left unchanged. Local `manage.py` has no `AI` binding and returns "The assistant is unavailable." `npm run dev` (pywrangler) is where the chat works, using the account's Workers AI.

### Store checkout

Staff add products in **Django admin** (`/admin/`) or `POST /api/store/products/`. Signed-in users buy with Stripe-hosted Checkout:

1. `POST /api/store/checkout/` with `{ "items": [{ "product": 1, "quantity": 1 }] }`
2. Redirect the browser to `checkout_url`
3. Stripe collects payment + shipping address (test cards: `4242…`)
4. On return, `POST /api/store/checkout/confirm/` with `{ "session_id": "cs_test_…" }`
5. Stripe also POSTs `/api/store/stripe/webhook/` so abandoned/expired sessions restore stock

Prices live in the catalog (`price_cents`). The Worker talks to Stripe over HTTPS (Workers `fetch` on D1, urllib locally) so the official Stripe SDK is not bundled.

Put test-mode keys in `.dev.vars` (`STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`). For a deployed Worker: `uv run pywrangler secret put STRIPE_SECRET_KEY` and `uv run pywrangler secret put STRIPE_WEBHOOK_SECRET`. Forward webhooks locally with `stripe listen --forward-to localhost:8787/api/store/stripe/webhook/`.

### Marketplace

Creators list a released game (`price_cents` of `0`, or at least `100`). A project must be released first; listing is a separate request. Buyers claim free games or pay by card. The charge is on Chili's account: no destination, no `application_fee_amount`. Chili keeps 20% plus an estimate of card processing and credits the rest. Set that estimate with `MARKETPLACE_PROCESSING_FEE_BPS` and `MARKETPLACE_PROCESSING_FEE_FIXED_CENTS` from [stripe.com/pricing](https://stripe.com/pricing) for the charge currency and method. There is no default rate. Earnings can accrue before payout setup. After 7 days, `POST /api/marketplace/me/payouts/` transfers the cleared balance when it is at least $20 and the creator's `stripe_transfers` and `payouts` capabilities are `active`.

A signed-in user rates a listed game once, with `POST /api/marketplace/listings/<slug>/rating/` `{ "stars": 4, "comment": "Tight corridors." }`. `stars` is an integer from 1 to 5. `comment` is optional and at most 500 characters; a star-only rating omits it. The game must already be in their library (they released it, or the purchase is paid, refunded, or disputed). A second submission is rejected, and there is no edit. Listings include `rating_average`, `rating_count`, `my_rating`, and `reviews` (username, stars, and comment).

`POST /api/marketplace/me/account/` creates an Accounts v2 recipient with `dashboard: none`. `POST /api/marketplace/me/account-session/` returns a client secret for embedded onboarding, the notification banner, account management, and payouts. Refunds and disputes reduce unpaid earnings or reverse a transfer already sent. The store webhook verifies those events. Kit checkout is unchanged.

---

## Layout

```text
src/
├── index.py              # Cloudflare Worker entrypoint
├── manage.py
├── app/                  # Django project (settings, urls, ASGI/WSGI)
├── accounts/             # User + profile API
├── community/            # Forum API + seed command
├── games/                # Bitsy projects + cover images
├── store/                # Hardware store + Stripe Checkout
└── marketplace/          # Game listings, purchases, creator payouts
wrangler.jsonc            # D1 `DB`, R2 `ASSETS_BUCKET`, Workers AI `AI`
pyproject.toml
```

The Worker serves Django through **WSGI** via `django_cf.DjangoCF`. D1's ORM is synchronous and Worker `fetch` is async, so `DJANGO_ALLOW_ASYNC_UNSAFE` is required. `app.asgi` remains available for tests.

---

## MCP (agents)

Cursor and other MCP clients can call tools on the running dev API via **[django-mcp-server](https://github.com/gts360/django-mcp-server)** at `http://127.0.0.1:8787/mcp`. Project config: **`.cursor/mcp.json`**. Model read tools and limits: **[docs/mcp.md](docs/mcp.md)**.

## Local development

Prerequisites: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 20+ (Wrangler). Stripe test keys if you want a live Checkout redirect.

```bash
cp .dev.vars.example .dev.vars
npm install
uv sync
uv run python src/manage.py migrate
uv run python src/manage.py seed_forum
uv run python src/manage.py seed_store
npm run collectstatic
npm run test
npm run dev          # wrangler / pywrangler on http://localhost:8787
```

`uv run python src/manage.py migrate` only touches local SQLite. The Worker uses a **separate D1** database. Apply schema there while `npm run dev` is running:

```bash
curl -X POST http://localhost:8787/api/_ops/bootstrap/ \
  -H "X-Ops-Token: chili-dev-ops-token"
```

That migrates D1, seeds forum categories and sample products, and creates `admin` / `chili-dev-admin` from `.dev.vars`. Then sign in at `/admin/login/` to add or edit store products.

Replace the placeholder `database_id` in `wrangler.jsonc` after `wrangler d1 create chili-platform`. Create the R2 bucket with `wrangler r2 bucket create chili-platform-assets`. Put `DJANGO_SECRET_KEY` via `uv run pywrangler secret put DJANGO_SECRET_KEY`.

---

## License

MIT. See `LICENSE`.
