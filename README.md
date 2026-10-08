# Chili Platform — Backend 🌶️

The serverless API for **Chili Platform**. Django runs on Cloudflare Workers with D1 (SQLite at the edge) and R2 for uploaded media.

---

## Features (this scaffold)

* JWT auth, custom user profiles
* Bitsy games saved from the creator, with cover images in R2
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
| POST | `/api/auth/logout/` | public (body is the refresh token) |
| POST | `/api/auth/verify-email/` | public |
| POST | `/api/auth/verify-email/resend/` | JWT |
| POST | `/api/auth/password/reset/` | public |
| POST | `/api/auth/password/reset/confirm/` | public |
| GET/PUT | `/api/profiles/me/` | JWT |
| POST | `/api/profiles/me/acceptance/` | JWT. Records terms, privacy, or seller terms. Does not backfill. |
| GET | `/api/profiles/<username>/` | public |
| GET | `/api/games/?username=` | public released games |
| GET | `/api/games/?released=false` | JWT (own projects) |
| POST | `/api/games/` | JWT |
| GET/PUT/PATCH/DELETE | `/api/games/<id>/` | released games are public; owner write; Bitsy data for owner or library |
| POST | `/api/games/<id>/release/` | JWT (owner). Does not list the game. |
| CRUD | `/api/forum/categories/` | staff write |
| CRUD | `/api/forum/posts/` | JWT write |
| GET/POST | `/api/forum/posts/<id>/comments/` | JWT write |
| CRUD | `/api/forum/comments/` | JWT write |
| CRUD | `/api/store/categories/` | public read, staff write |
| CRUD | `/api/store/delivery-options/` | public read (active only), staff write |
| CRUD | `/api/store/products/?category=&search=&min_price_cents=&max_price_cents=&in_stock=&ordering=` | public read, staff write |
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

### Store checkout

Staff add products in **Django admin** (`/admin/`) or `POST /api/store/products/`. Signed-in users buy with Stripe-hosted Checkout:

1. `POST /api/store/checkout/` with `{ "items": [{ "product": 1, "quantity": 1 }], "delivery_option": "eu-standard" }`. `delivery_option` is required once any delivery option is active; its fee (free above `free_over_cents`) is added to the order total and sent to Stripe as the shipping rate. Staff manage options in admin under **Delivery options**. A product can limit which options it ships with (`delivery_options`, empty means all), and the chosen option must suit every physical item. Products marked `is_digital` need no delivery: an order with only digital items takes no `delivery_option`, collects no address, and starts as not shipping.
2. Redirect the browser to `checkout_url`
3. Stripe collects payment + shipping address (test cards: `4242…`)
4. On return, `POST /api/store/checkout/confirm/` with `{ "session_id": "cs_test_…" }`
5. Stripe also POSTs `/api/store/stripe/webhook/` so abandoned/expired sessions restore stock

Prices live in the catalog (`price_cents`). The Worker talks to Stripe over HTTPS (Workers `fetch` on D1, urllib locally) so the official Stripe SDK is not bundled.

Put test-mode keys in `.dev.vars` (`STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`). For a deployed Worker: `uv run pywrangler secret put STRIPE_SECRET_KEY` and `uv run pywrangler secret put STRIPE_WEBHOOK_SECRET`. Forward webhooks locally with `stripe listen --forward-to localhost:8787/api/store/stripe/webhook/`.

### Marketplace

Creators list a released game (`price_cents` of `0`, or at least `100`). A project must be released first; listing is a separate request. Buyers claim free games or pay by card. The charge is on Chili's account: no destination, no `application_fee_amount`. Chili keeps 20% plus an estimate of card processing and credits the rest. Set that estimate with `MARKETPLACE_PROCESSING_FEE_BPS` and `MARKETPLACE_PROCESSING_FEE_FIXED_CENTS` from [stripe.com/pricing](https://stripe.com/pricing) for the charge currency and method. There is no default rate. Earnings can accrue before payout setup. After 7 days, `POST /api/marketplace/me/payouts/` transfers the cleared balance when it is at least €20 and the creator's `stripe_transfers` and `payouts` capabilities are `active`.

A signed-in user rates a listed game once, with `POST /api/marketplace/listings/<slug>/rating/` `{ "stars": 4, "comment": "Tight corridors." }`. `stars` is an integer from 1 to 5. `comment` is optional and at most 500 characters; a star-only rating omits it. The game must already be in their library (they released it, or the purchase is paid, refunded, or disputed). A second submission is rejected, and there is no edit. Listings include `rating_average`, `rating_count`, `my_rating`, and `reviews` (username, stars, and comment).

`POST /api/marketplace/me/account/` creates an Accounts v2 recipient with `dashboard: none`. `POST /api/marketplace/me/account-session/` returns a client secret for embedded onboarding, the notification banner, account management, and payouts. Refunds and disputes reduce unpaid earnings or reverse a transfer already sent. The store webhook verifies those events. Kit checkout is unchanged.

---

### Accounts and mail

Registration checks the password with Django's validators and stores a PBKDF2-SHA256 hash (`WorkerPBKDF2PasswordHasher`, 100,000 iterations) so the hash finishes inside one Worker request. Older `salted_sha256` hashes still verify and are upgraded on the next successful login.

A new account is not email-verified. Login still returns tokens. Until the address is verified, the API rejects forum writes, game create/update (including release and delete), marketplace listing writes, and store or marketplace checkout. `POST /api/auth/verify-email/resend/` sends another link to the signed-in user. Password reset is `POST /api/auth/password/reset/` then `POST /api/auth/password/reset/confirm/`. A successful reset marks the email verified.

Refresh tokens rotate. The previous refresh token is blacklisted in D1 by the `rest_framework_simplejwt.token_blacklist` tables, which `POST /api/_ops/migrate/` (and bootstrap) already apply. `POST /api/auth/logout/` with `{ "refresh": "..." }` revokes that refresh token.

Mail is sent with [Cloudflare Email Sending](https://developers.cloudflare.com/email-service/) over HTTPS (`POST https://api.cloudflare.com/client/v4/accounts/{account_id}/email/sending/send`). Messages are plain text, so the verify and reset links stay on one line.

| Setting | Where | Purpose |
|---|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | Worker secret | Your Cloudflare account id |
| `CLOUDFLARE_EMAIL_API_TOKEN` | Worker secret | API token with the **Email Sending: Edit** permission |
| `EMAIL_FROM` | `wrangler.jsonc` var | Sender address on an onboarded domain (default in the repo: `noreply@chilichip.eu`) |

Set the secrets with `uv run pywrangler secret put <NAME>`. Do not commit them.

`FRONTEND_BASE_URL` is not a secret. It is the Pages origin used in the links (default `http://localhost:4200`). Set it to the public site before relying on the emails.

One-time Cloudflare setup (the domain must use Cloudflare DNS, and Email Sending needs the Workers Paid plan):

1. In the Cloudflare dashboard go to **Compute** > **Email Service** > **Email Sending** and choose **Onboard Domain**. Pick `chilichip.eu`. Cloudflare adds MX, SPF, DKIM and DMARC records on the `cf-bounce` subdomain. Wait until the domain shows as verified (usually 5 to 15 minutes).
2. Create an API token (**My Profile** > **API Tokens** > **Create Token**) with the **Email Sending: Edit** permission, scoped to your account.
3. Set `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_EMAIL_API_TOKEN` as Worker secrets, and make sure `EMAIL_FROM` in `wrangler.jsonc` is an address on the onboarded domain.
4. Deploy, register a test account, and check that the verification email arrives. The token does not expire on a schedule, so there is nothing to renew.

On the deployed Worker, if any of those three settings are missing, verification and reset return `503` `{"detail": "Mail is not configured."}` and the token is not in the response. If Cloudflare rejects a message, bounces it, or cannot be reached, they return `502` `{"detail": "Could not send email."}`. Local development prints the message to stdout instead. `manage.py` uses Django's console email backend unless `EMAIL_BACKEND` is set. `npm run dev` (wrangler dev) loads `.dev.vars`, which sets `EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend`; a production deploy does not load that file. The verify or reset link is in the terminal that is running wrangler or `manage.py`, not in the JSON response. With the Cloudflare settings present and no console override, the Worker sends through Cloudflare Email Sending.

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
wrangler.jsonc            # D1 `DB`, R2 `ASSETS_BUCKET`
pyproject.toml
```

The Worker serves Django through **WSGI** via `django_cf.DjangoCF`. D1's ORM is synchronous and Worker `fetch` is async, so `DJANGO_ALLOW_ASYNC_UNSAFE` is required. `app.asgi` remains available for tests.

---

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

`uv run python src/manage.py migrate` only touches local SQLite. The Worker uses a **separate D1** database.

That migrates D1, seeds forum categories and sample products, and creates `admin` / `chili-dev-admin` from `.dev.vars`. Then sign in at `/admin/login/` to add or edit store products.

Deploy with `npm run deploy`. It first runs `wrangler d1 migrations apply DB --remote`, which applies any new SQL file in `migrations/` to the production D1 database, and only then deploys the Worker. Deploying code whose models expect a column that D1 does not have yet makes every query on that table fail with a 500. If you deploy another way (for example Workers Builds from git), run `npm run d1:apply` before the new code goes live.

Replace the placeholder `database_id` in `wrangler.jsonc` after `wrangler d1 create chili-platform`. Create the R2 bucket with `wrangler r2 bucket create chili-platform-assets`. Put `DJANGO_SECRET_KEY` via `uv run pywrangler secret put`. Set CORS, `DJANGO_ALLOWED_HOSTS`, the four checkout return URLs, and `PUBLIC_BASE_URL` to the real hosts at deploy time. Leave this file on localhost with the placeholder id so local dev still works.

`npm run dev` records `WRANGLER_COMMAND=dev` and boots with those local values. `npm run deploy` records `WRANGLER_COMMAND=deploy` and the Worker refuses to start while the dev secret key, the dev ops token, a localhost or `*` host, or the placeholder database id is still in effect.

---

## License

MIT. See `LICENSE`.
