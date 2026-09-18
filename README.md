# Chili Platform — Backend 🌶️

The serverless API and edge execution engine for **Chili Platform**. Built with Django running directly on Cloudflare Workers at the edge, handling user authentication, marketplace transactions, hardware store logistics, and game release processing.

---

## ✨ Features

* **Store & Cart Management:** Order processing for the vgc zero console and accessories.
* **Community APIs:** Discussions, devlogs, comments, and direct creator messaging.
* **Marketplace Engine:** Digital game distribution, release uploads, license management, and metadata catalog.
* **Edge Processing:** Serverless execution powered by Cloudflare Workers, D1 database, and R2 object storage.

---

## 🛠️ Tech Stack & Infrastructure

* **Framework:** Python 3.12+, Django (ASGI)
* **Serverless Edge Engine:** Cloudflare Workers (`workers.asgi` adapter)
* **Database:** Cloudflare D1 (Edge SQLite) or Cloudflare Hyperdrive (PostgreSQL connection pooler)
* **Object Storage:** Cloudflare R2 (S3-compatible storage for ROMs, release builds, and media)
* **Cache & Key-Value:** Cloudflare KV / Durable Objects
* **Queue & Async Workflows:** Cloudflare Queues & Workflows
* **Package Manager:** `uv`

---

## 🏗️ Directory Structure

```text
backend/
├── src/
│   ├── app/                 # Core Django configuration & entrypoints
│   │   ├── settings.py
│   │   ├── asgi.py
│   │   └── urls.py
│   ├── store/               # Chilichip hardware store API
│   ├── community/           # Forum & creator devlog API
│   ├── marketplace/         # Digital game distribution API
│   ├── builds/              # Game build ingestion & file handling
│   └── index.py             # Cloudflare Worker entrypoint class
├── wrangler.jsonc           # Cloudflare Workers bindings (D1, R2, KV, Queues)
├── pyproject.toml
└── uv.lock
```

---

## 🚀 Development Setup

### Prerequisites

* **Python**: `3.12+`
* **uv**: `curl -LsSf https://astral.sh/uv/install.sh | sh`
* **Node.js**: `v20.x` or higher (for Cloudflare Wrangler CLI)
* **Wrangler CLI**: `npm install -g wrangler`

### Installation & Local Development

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-org/chili-platform-backend.git
   cd chili-platform-backend
   ```

2. **Sync Python dependencies:**
   ```bash
   uv sync
   ```

3. **Run local Cloudflare Workers emulation:**
   ```bash
   uv run wrangler dev
   ```
   The local API server will start at `http://localhost:8787`.

4. **Database Migrations (Cloudflare D1 Local):**
   ```bash
   uv run wrangler d1 migrations apply DB --local
   ```

---

## ☁️ Deployment

### Deploy to Cloudflare Workers

1. **Authenticate Wrangler:**
   ```bash
   wrangler login
   ```

2. **Apply Remote Database Migrations:**
   ```bash
   uv run wrangler d1 migrations apply DB --remote
   ```

3. **Deploy Worker:**
   ```bash
   uv run wrangler deploy
   ```

---

## 📜 License

Distributed under the MIT License. See `LICENSE` for details.
