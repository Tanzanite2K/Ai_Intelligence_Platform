# FrontierAtlas — local setup and production deployment requirements

This document covers the web application (FastAPI `/api/v1` + Next.js frontend) that sits on top of the ingestion
platform. **Nothing here has been deployed publicly.** Sections marked *production* are requirements, not a
description of a running system.

## 1. Components

| Component | Path | Runtime | Role |
|---|---|---|---|
| Ingestion pipeline | `src/crawlers`, `src/pipelines`, `src/extraction`, `src/entity_resolution` | Python 3.12+ | Collects, validates, resolves and stores records (CLI / Celery) |
| API | `src/api/app.py`, `src/api/v1/` | Python 3.12+, Uvicorn | Read-only JSON API over the database; admin-only crawl trigger |
| Web app | `frontend/` | Node.js 20.9+ (22 LTS recommended) | Next.js 16 UI; reads only the API |
| Database | `data/platform.db` (SQLite) or PostgreSQL | — | Single source of truth |

The web tier never writes: the API opens SQLite with `mode=ro` when `API_READ_ONLY=true` (default), exposes no
write endpoints except the admin crawl trigger, and the frontend holds no credentials.

## 2. Local setup (Windows)

Prerequisites: Python virtual environment in `.venv` with `requirements.txt` installed (see README → Installation),
`data\platform.db` present, Node.js 20.9+.

Install Node.js if needed: `winget install OpenJS.NodeJS.LTS` or the LTS installer from https://nodejs.org, then open a
**new** terminal and check `node -v`.

```bat
RUN_APP.cmd              :: development mode (hot reload)
RUN_APP.cmd -Production  :: optimised build, then next start
```

What `RUN_APP.cmd` (`scripts\run_app.ps1`) does, in order — it stops with a clear message at the first failing check:

1. Checks Node.js ≥ 20.9 (prints install instructions if missing or too old).
2. Checks `.venv\Scripts\python.exe` and `data\platform.db` exist.
3. Creates `frontend\.env.local` with `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000` if absent (no secrets).
4. Runs `npm ci` on first run (when `frontend\node_modules` is missing).
5. Allows one launcher at a time (a second double-click while one is still starting exits with a message).
6. Port 8000: if a healthy FrontierAtlas API started by RUN_APP is already listening and it started after the newest
   `src\**\*.py` change, it is **REUSED**. A FrontierAtlas API that is stale, unhealthy or not started by RUN_APP
   (so `API_READ_ONLY=true` cannot be confirmed) is stopped and **RESTARTED**; only processes positively identified
   as FrontierAtlas (`src.main serve` / `src.api.app` command line plus the API title or project path) are stopped.
   Any other program on the port is reported as a conflict and **never stopped**. Otherwise a window
   "FrontierAtlas API :8000" is opened (`API_HOST=127.0.0.1`, `API_READ_ONLY=true`, `PYTHONIOENCODING=utf-8`,
   `python -m src.main serve`); waits up to 45 s for `GET /health`, then checks `/api/v1/intelligence/brief` is 200.
7. Port 3000: a healthy FrontierAtlas Next.js server (this project's `frontend\node_modules\next`) in the requested
   mode is **REUSED** (production builds only if newer than the latest `frontend\src` change); a stale or wrong-mode
   one is restarted; another program is a conflict. Otherwise opens "FrontierAtlas Web :3000": `next dev` (or
   `npm run build` + `next start`) bound to 127.0.0.1; waits up to 3 min, prints a STARTED/REUSED/RESTARTED
   summary per service and opens the browser. Running RUN_APP.cmd again therefore never creates duplicates.

**Stopping:** close the two windows (or press Ctrl+C in each). There is no separate stop script.

Manual start (any OS):

```bash
# terminal 1 (project root)
API_HOST=127.0.0.1 .venv/bin/python -m src.main serve          # Windows PowerShell: $env:API_HOST="127.0.0.1"
# terminal 2
cd frontend && cp .env.example .env.local && npm ci && npm run dev
```

OneDrive: `frontend\node_modules` (~450 packages) and `frontend\.next` change often; exclude them from sync or keep
the project outside OneDrive. Do not run two database writers (e.g. a crawl and `rebuild-entities`) at the same time
against the OneDrive-hosted SQLite file; the read-only API is safe alongside one writer.

## 3. Configuration

API (`.env`, server-side only):

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/platform.db` | PostgreSQL (`postgresql+asyncpg://…`) for production |
| `API_HOST` / `API_PORT` | `0.0.0.0` / `8000` | **Set `API_HOST=127.0.0.1` locally**; in production bind privately behind the proxy |
| `API_READ_ONLY` | `true` | SQLite opened read-only; the API never runs DDL against SQLite |
| `API_CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Exact origins of the web app; `*` is ignored |
| `API_RATE_LIMIT_PER_MINUTE` | `240` | Per client IP on `/api/v1`; in-process (see limits) |
| `ADMIN_API_KEY` | unset | Unset = admin endpoints disabled (503). Generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |

Web app (`frontend/.env.local`): `NEXT_PUBLIC_API_BASE_URL` only. It is embedded in the browser bundle, so it must
never contain a secret. Provider keys (Gemini, GitHub, Google, Product Hunt, Hugging Face) stay in the backend `.env`.

## 4. Production deployment requirements (not yet deployed or verified)

1. **Database:** PostgreSQL 16 (supported and covered by the backend test suite) instead of SQLite on OneDrive. Run
   `python -m src.main init-db` once from an admin context; the API itself never creates tables in read-only mode.
   Give the API a database role with SELECT-only privileges.
2. **API process:** `uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --workers 1` behind a reverse proxy.
   With more than one worker or replica, replace the in-process rate limiter with a shared one (proxy or Redis).
3. **Web app:** `npm ci && npm run build && npm run start` (Node 20.9+), with `NEXT_PUBLIC_API_BASE_URL` set to the
   public API URL **at build time**.
4. **Reverse proxy / TLS:** terminate HTTPS; serve the web app and `/api/v1`; set `API_CORS_ORIGINS` to the exact
   public web origin. Restrict or remove public access to `/metrics`, `/stats`, `/entity-mappings`, `/records/*` and
   `/` (legacy operational routes, see §5). Add a Content-Security-Policy and HSTS at the proxy.
5. **Admin operations:** set `ADMIN_API_KEY` from a secret store; never expose it to the browser. Prefer running
   crawls/enrichment through the CLI or scheduler rather than the HTTP trigger.
6. **Scheduling:** Celery worker + beat with Redis (`docker-compose.yml`) for 15-minute news/jobs and daily bulk
   crawls — configured but never run end-to-end (Docker was unavailable).
7. **Monitoring:** scrape `/metrics` privately (Prometheus/Grafana config in `deploy/`); alert on FAILED/INACCESSIBLE
   runs and dead-letter growth.
8. **Authentication:** none is implemented. Public read access is by design for the intelligence data; add an
   identity-aware proxy or app authentication before exposing operations pages or before adding any write features
   (e.g. entity-review decisions).

## 5. Security review (2026-11-21)

Verified by tests (`tests/integration/test_api_security.py`, `tests/integration/test_api_v1.py`):

- Every state-changing route requires `X-Admin-Key` (constant-time comparison); only two exist:
  `POST /api/v1/admin/crawl/{source}` and the legacy `POST /crawl/{source}` (previously unauthenticated, now protected).
- Without `ADMIN_API_KEY` configured, admin routes return 503 (disabled), never open.
- No approve/reject/defer/retry/rebuild/enrich endpoints exist; all `/api/v1` read routes are GET-only.
- The API connection to SQLite is read-only (a `DELETE` through it fails).
- Search input is a bound parameter (SQL-injection probe returns 0 rows); `%`/`_` are escaped.
- Errors are JSON without stack traces; the admin key is never echoed; secrets are redacted from run errors and
  dead-letter previews; CORS rejects non-allowlisted origins; `/api/v1` is rate limited.
- Frontend: no secrets in env; responses validated with Zod; `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`.

Findings not changed in this phase (recommendations):

| # | Finding | Risk | Recommendation |
|---|---|---|---|
| 1 | Legacy `GET /entity-mappings` returns all 4,892 mappings unpaginated, outside the rate limiter | Bulk download / load | Remove or put behind admin/proxy in production; `/api/v1/entities` replaces it |
| 2 | `GET /metrics`, `/stats`, `/` (legacy ops page) are public and not rate limited | Information disclosure (counts, error strings) | Restrict at the proxy |
| 3 | `API_HOST` defaults to `0.0.0.0` | LAN exposure when started manually | Launcher sets `127.0.0.1`; consider changing the default |
| 4 | Rate limiter is per process | Ineffective with several workers | Shared limiter in production |
| 5 | No Content-Security-Policy | XSS defence-in-depth | Add CSP at the proxy (React escapes output; no `dangerouslySetInnerHTML` is used) |
| 6 | No user authentication | Operations pages are readable by anyone who can reach the app | Keep local, or add authentication before exposure |
