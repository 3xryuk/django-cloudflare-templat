# Django + Cloudflare Workers (`django-cf`) Template

A **Django** project template configured to run on **Cloudflare Python Workers** using **`django-cf`** (with support for **Cloudflare D1**, **R2**, **Durable Objects**, **Workers KV**, **Workers Static Assets**, and **Cloudflare Access**), managed entirely with **`uv`** and **`pywrangler`**.

---

## Project Structure

```text
.
├── manage.py                      # Root Django CLI wrapper (adds src/ to sys.path)
├── pyproject.toml                 # Project metadata and dependencies for uv & pywrangler
├── wrangler.jsonc                 # Cloudflare Worker configuration and bindings (D1, R2, KV, DO, Assets)
├── .env.example                   # Sample environment variables for local dev (.dev.vars) and Workers
└── src/
    ├── index.py                   # Cloudflare Worker entrypoint (Default, DjangoDO, CacheDurableObject)
    ├── manage.py                  # Django management utility inside src/
    └── hello_world/
        ├── settings.py            # Dual-mode settings (Cloudflare Workers / Local Dev)
        ├── urls.py                # Core routes, /healthz/, /favicon.ico, and serverless management endpoints
        ├── wsgi.py                # WSGI application
        ├── asgi.py                # ASGI application
        ├── core/                  # Core app (models, cache, admin, views, tests, migrations)
        ├── static/                # Source static files
        └── templates/             # HTML templates (compatible with Workers runtime)
```

---

## Local Development (`uv`)

1. Install dependencies and sync the environment:
   ```bash
   uv sync
   ```
2. Create a `.dev.vars` file from `.env.example` (optional):
   ```bash
   cp .env.example .dev.vars
   ```
3. Run the test suite:
   ```bash
   uv run python manage.py test hello_world
   ```
4. Collect static assets and run the local Django dev server:
   ```bash
   uv run python manage.py collectstatic --noinput
   uv run python manage.py runserver
   ```

---

## Deploying to Cloudflare Workers (`uv run pywrangler`)

`pywrangler` proxies all Wrangler commands while automatically syncing Python dependencies into `python_modules/` for the Cloudflare Workers runtime.

### 1. Create Cloudflare Resources

```bash
# Create a D1 database
uv run pywrangler d1 create django-cf-db

# Create an R2 bucket for media uploads and persistent cache (cache/)
uv run pywrangler r2 bucket create django-cf-media

# Create a KV namespace for edge-replicated cache
uv run pywrangler kv namespace create CACHE_KV
```

Update `database_id` and `kv_namespaces[0].id` in `wrangler.jsonc` with the generated IDs, then set production secrets:

```bash
uv run pywrangler secret put SECRET_KEY
uv run pywrangler secret put CF_MANAGEMENT_TOKEN
```

### 2. Collect Static Assets & Run / Deploy

```bash
# Collect static files into ./staticfiles/static for Workers Static Assets
uv run python manage.py collectstatic --noinput

# Run locally inside the Cloudflare Workers emulator
uv run pywrangler dev

# Deploy to Cloudflare Workers
uv run pywrangler deploy
```

### 3. Serverless Management Endpoints
Because Workers do not provide an interactive shell, the template exposes protected endpoints (requires an authenticated `superuser` session or the `X-CF-Management-Token` header):

- `GET /__run_migrations__/`: Runs `migrate` programmatically against Cloudflare D1.
- `POST /__create_admin__/`: Creates the initial superuser (`username`, `email`, `password`).
- `GET /healthz/`: Returns runtime status, active database engine, cache backend, cache tiers, and bytecode status.

---

## Multi-Tier Caching & V8 Memory Protection

To respect Cloudflare Workers' 128 MB V8 isolate memory limit, this template avoids Django's in-memory `LocMemCache` and disables Python bytecode generation:

1. **Bytecode Suppression (`__pycache__` / `.pyc`):**
   - `sys.dont_write_bytecode = True` and `PYTHONDONTWRITEBYTECODE=1` are enforced across `manage.py`, `src/manage.py`, `src/index.py`, `settings.py`, and `wrangler.jsonc`.
   - Local cache fallbacks write to `/tmp/django_cf_cache` (outside `src/`) so `src/` stays clean and excluded from Worker bundles.

2. **Multi-Tier `CACHES` Configuration (`src/hello_world/core/cache.py`):**
   - **`r2` (`R2Cache` — default):** Stores template fragments, rendered responses, and large query payloads in **Cloudflare R2** (`BUCKET`) with zero V8 RAM overhead.
   - **`do` (`DurableObjectCache`):** Provides strongly consistent state, Worker sessions (`SESSION_ENGINE` via `SESSION_CACHE_ALIAS="do"`), and atomic counters (`incr`/`decr`) via `CacheDurableObject` (`CACHE_DO`).
   - **`kv` (`CloudflareKVCache`):** Stores read-heavy, globally distributed configuration and feature flags via `CACHE_KV`.
   - **`db` (`DatabaseCache`):** Optional SQL table-backed cache on D1 or SQLite.

---

## Important Cloudflare D1 & Workers Notes

- **No Transaction Support:** Cloudflare D1 executes and commits every SQL statement immediately (`@transaction.atomic` is a no-op).
- **Pyodide Compatibility:** Packages added to `[project.dependencies]` in `pyproject.toml` must be compatible with Cloudflare Python Workers' WebAssembly / Pyodide runtime.
- **Dev Dependencies Isolation:** Development-only packages such as `django-browser-reload` live in `[dependency-groups.dev]` and are only loaded locally.
