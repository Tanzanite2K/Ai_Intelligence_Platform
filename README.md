# AI Intelligence Ingestion Platform

An async ingestion pipeline for the **GraphOne / FrontierAtlas AI Engineer assessment**. It collects AI **startups, products, research papers (with live GitHub stars), jobs and news** from legitimate APIs and feeds. Publication dates are normalised and a provable 24-hour freshness window is enforced. Text is structured with a **Gemini → Groq → DeepSeek** fallback chain that keeps only evidence-backed values. Organisations are canonicalised, everything is stored idempotently, and the result is exported to a six-tab Google Sheet with CSV backups.

| Document | Contents |
|---|---|
| `architecture.pdf` | 3-page design: 500k scale strategy, 413/429 handling, distributed freshness and dedup, storage choices |
| `FINAL_REPORT.md` | evidence-generated results: counts, integrations, LLM checks, ER metrics, tests, Sheets link |
| `REQUIREMENTS_TRACEABILITY.md` | each assessment requirement → implementation → test → result → evidence |
| `docs/` | `data_audit.json`, `integration_report.md`, `llm_live_check.json`, `benchmarks.json` (synthetic), JUnit XML |

---

## Features

- **Bulk acquisition**
  - YC directory (yc-oss mirror of YC's public Algolia index, with Algolia fallback)
  - Hugging Face organisations, models and Spaces
  - Product Hunt GraphQL API
  - Hugging Face Papers (the successor of Papers with Code) plus the arXiv API, correlated with GitHub repositories
  - Star counts refreshed through the authenticated GitHub API, with ETag revalidation and rate-limit awareness
- **Fresh signals**
  - News: 8 publisher feeds (TechCrunch, VentureBeat, The Verge, MIT Technology Review, Ars Technica, OpenAI News, The Decoder, MarkTechPost), with full-text extraction
  - Jobs: 5 job-board APIs (Greenhouse, Ashby, Lever, RemoteOK, Himalayas)
  - Dates come from source-specific publication fields, never `updated_at` or crawl time. Relative dates are handled, and the 24-hour test accounts for each date's precision and is re-checked at export.
- **LLM engine**
  - Provider abstraction with a normalised error taxonomy and token-aware semantic chunking.
  - 413 / context overflow: the chunk is split and retried, never resent unchanged.
  - 429: the provider's `Retry-After` is honoured, otherwise exponential backoff with full jitter, then fallback.
  - Malformed JSON gets one repair retry.
  - Every accepted value needs a verbatim evidence quote; each extraction gets a confidence score and validation status.
- **Entity resolution**
  - Layers: normalisation (Unicode, case, punctuation, legal suffixes) → exact → alias (60-company seed plus learned aliases) → domain → guarded fuzzy → semantic → human review.
  - Platform handles (e.g. `meta-llama`) may confirm a known entity but never create one.
  - Every decision is written to the Entity Mapping Log; `rebuild-entities` re-applies the current rules to stored data.
- **Production engineering**
  - Shared `aiohttp` connection pool, per-domain rate limiting and circuit breakers, robots.txt checks, and anti-bot challenge detection (blocked sources are reported, never evaded).
  - Celery + Redis workers with `acks_late` (tasks are redelivered if a worker dies) and a dead-letter table.
  - Redis claim / lease / done markers plus a database `UNIQUE … ON CONFLICT` constraint guarantee each item is processed once.
  - Checkpoints are saved after every committed batch, so interrupted crawls resume.
  - Prometheus metrics and structured logs with secrets redacted.

## Architecture

```
Celery beat ─▶ Redis queue ─▶ crawler workers (asyncio/aiohttp, per-domain buckets, breakers, robots)
                                   │ claim URL (Redis SET NX lease)
                                   ▼
           normalise: canonical URL + hash · publication date → UTC · freshness gate
                                   ▼
           LLM engine (only for unstructured text): Gemini → Groq → DeepSeek, chunk, evidence check
                                   ▼
           entity resolution (exact/alias/domain/fuzzy+guards/semantic/review) → mapping log
                                   ▼
           PostgreSQL / SQLite: INSERT … ON CONFLICT (record_type, dedup_hash) · JSONB payload · checkpoints · DLQ
                                   ▼
           exports: Google Sheets (6 tabs, batched, quota-aware) + CSV · API/dashboard · Prometheus
```
Details and trade-offs: `architecture.pdf`.

## Technology stack
Python 3.12+ · asyncio · aiohttp · Pydantic v2 · SQLAlchemy 2 (asyncpg / aiosqlite) · PostgreSQL 16 · Redis 7 · Celery 5 · FastAPI · rapidfuzz · feedparser · selectolax · Playwright (optional renderer) · google-auth · Prometheus/Grafana · Docker Compose · pytest + aioresponses · ruff.

## Prerequisites
- Python **3.12 or 3.13**. On Windows, install it from python.org.
- Optional: Docker Desktop, for the PostgreSQL + Redis + Celery stack.
- API credentials (see the table below). Without them the pipeline still runs; the affected sources are reported as `SKIPPED`.

## Installation
```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
copy .env.example .env            # macOS/Linux: cp .env.example .env
```

## Environment configuration
`.env` is loaded automatically; a blank value (`KEY=`) means *not configured*. For local single-node runs, comment out `DATABASE_URL` and `REDIS_URL` to use SQLite (`data/platform.db`) with in-process dedup.

| Variable | Used for | Without it |
|---|---|---|
| `GITHUB_TOKEN` | live star counts (5,000 req/h) | 60 req/h; stars fall back to Hugging Face's cached count, labelled `github_stars_source=huggingface_papers` |
| `HF_TOKEN` | Hugging Face Hub API (higher limits) | frequent 429s with 80–120 s waits; crawls still resume |
| `PRODUCTHUNT_TOKEN` | Product Hunt GraphQL (the application's **Developer Token**; no OAuth redirect is used) | source `SKIPPED` |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | LLM tier 1 | LLM-only fields stay `null` |
| `GROQ_API_KEY`, `GROQ_MODEL` / `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL` | LLM fallback tiers 2 and 3 | that tier is skipped |
| `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_SHEET_ID` | Sheets export | CSV only |

Check every credential without printing secrets:
```bash
python scripts/verify_integrations.py        # writes docs/integration_report.md
```

## Database setup
- **SQLite (default):** created automatically by `python -m src.main init-db`, or on first use.
- **PostgreSQL:** set `DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db`; tables and indexes are created on start. The schema is versioned through `schemaVersion` in each JSONB payload.

## Docker setup
```bash
docker compose up -d --build postgres redis api worker beat prometheus grafana
docker compose exec api python -m src.main crawl --vertical all
docker compose up -d --scale worker=6        # horizontal scaling, no code change
# http://localhost:8000 dashboard · /metrics · Prometheus :9090 · Grafana :3000
```

## Running crawlers
```bash
python -m src.main crawl --vertical all                 # startups, products, research, news, jobs
python -m src.main crawl --vertical news                # run news and jobs right before exporting (24 h window)
python -m src.main crawl --source yc_startups --limit 25
python -m src.main refresh-stars                        # re-check stars of stored papers via the live GitHub API
python -m src.main enrich --limit 150                   # LLM pricing for stored products whose text states pricing
python -m src.main rebuild-entities                     # re-resolve stored records, regenerate the mapping log
python -m src.main report                               # counts, statuses, star sources, ER metrics
python scripts/audit_data.py                            # full integrity audit -> docs/data_audit.json
```
Sources: `yc_startups hf_orgs producthunt hf_products research_papers techcrunch_ai venturebeat_ai theverge_ai mit_tr_ai arstechnica_ai openai_news the_decoder marktechpost greenhouse ashby lever remoteok himalayas`.

**One-shot final run on Windows** (verify → tests → crawl → enrich → rebuild → fresh news/jobs → live LLM checks → audit → Sheets → `FINAL_REPORT.md`):
```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_final.ps1
```

## LLM configuration
The order is set by `providers_order` (default gemini → groq → deepseek). Input budgets per provider (`*_INPUT_TOKEN_BUDGET`) size the chunks; RPM limits (`*_RPM`) feed per-provider token buckets. `scripts/llm_live_check.py` exercises the real providers:
- direct extraction;
- forced fallback from an intentionally invalid primary key;
- forced chunking with a 1,500-token budget;
- a hallucination probe that must come back `null`.

## Google Sheets export
1. In Google Cloud, enable the **Google Sheets API** and the **Google Drive API** in the service account's project.
2. **Create an empty Google Sheet in your own Drive.** Share it with the service account's e-mail (`…@…iam.gserviceaccount.com`) as **Editor**, and put the ID from its URL into `GOOGLE_SHEET_ID`. Service accounts often have no Drive storage, so letting them create the file can fail with `storageQuotaExceeded`.
3. Run `python -m src.main export --sheets --share-public`. It writes exactly six tabs in batches, then reads the sheet back to verify tab names, row counts, headers and public link access (`docs/sheets_export.json`). CSV backups are always written to `data/exports/`.

## API endpoints

`python -m src.main serve` starts FastAPI on port 8000. Interactive docs: `http://localhost:8000/docs`.

**Public read API — `/api/v1`** (JSON; list responses are `{"data": [...], "meta": {page, page_size, total, total_pages}}`,
errors are `{"error": {code, message, details}}`; `page_size` defaults to 24, max 100):

| Area | Endpoints |
|---|---|
| Dashboard | `GET /dashboard/overview`, `/dashboard/activity`, `/dashboard/source-contribution`, `/dashboard/freshness`, `/dashboard/recent`, `/dashboard/source-health` |
| Search | `GET /search?q=&types=startups,products,papers,jobs,news&…filters&sort=` — returns results, per-type counts and matching organisations |
| Explorers | `GET /startups`, `/products`, `/papers`, `/jobs`, `/news` and `/{type}/{id}`; `GET /records/{id}` (any type) |
| Organisations | `GET /entities`, `/entities/{id}`, `/entities/{id}/records`, `/entities/{id}/related` |
| Entity review | `GET /reviews` (read-only queue; no decision endpoints) |
| Operations | `GET /operations/ingestion-runs`, `/operations/source-health`, `/operations/dead-letters` |
| Admin | `POST /admin/crawl/{source}` — requires `X-Admin-Key` |

Filters (only stored fields): `q` (case-insensitive; every term must match name, description, tags, authors,
organisation, location…), `source`, `date_from`, `date_to`, `category` (startup tag / product task / job role family),
`organisation`, `min_stars` (papers), `fresh_hours`, `location`, `pricing_model`, `remote`, `verified_dates`.
Sort: `newest` (default), `oldest`, `name`, `stars` (papers only). No relevance ranking is offered because the
search is substring matching, not a scored index.

Record detail returns every stored field with its provenance: `source`, `enriched` (live GitHub stars),
`entity_resolution` (canonical name differs from the raw source name), `llm_validated` (LLM-extracted and accepted only
with a verbatim evidence quote, shown alongside) or `missing`.

**Security.** The API opens SQLite **read-only** (`API_READ_ONLY=true`, the default), so the web tier cannot modify data.
Crawl triggers (`POST /api/v1/admin/crawl/{source}` and the legacy `POST /crawl/{source}`) require `ADMIN_API_KEY`
(constant-time comparison); without it they return 503. CORS is limited to `API_CORS_ORIGINS`; `/api/v1` is rate
limited per client IP (`API_RATE_LIMIT_PER_MINUTE`, in-process: correct for a single API worker). Errors never include
stack traces; secrets are redacted from run errors and dead-letter previews.

Legacy operational endpoints remain: `GET /health`, `/metrics`, `/stats`, `/records/{type}`, `/entity-mappings`, `/`.

## Web app (frontend)

`frontend/` is a Next.js + TypeScript app over the read-only `/api/v1` API (see `frontend/README.md`).
On Windows run **`RUN_APP.cmd`** from the project root: it checks Node.js 20.9+ (and prints install steps if it is
missing), starts the API on `127.0.0.1:8000` with the database opened read-only, starts the web app on
`127.0.0.1:3000` and opens the browser. `RUN_APP.cmd -Production` builds and serves the optimised bundle.
Running it again reuses healthy, up-to-date servers (no duplicates); another program on port 8000/3000 is
reported, never stopped. Close the two server windows to stop.

Pages: Overview · Explore (global search) · Startups · Products · Research papers · Jobs · News (explorers + record
detail pages with provenance) · Organisations (profiles from entity resolution) · Entity relationships · Data quality
(read-only review queue) · Ingestion monitor · Source health · Dead letter queue · Settings. The web app is read-only.

Local setup details, production deployment requirements and the security review: **`docs/DEPLOYMENT.md`**.
Verification results: `docs/WEB_APP_VERIFICATION.md`.

## Testing
```bash
pytest -q                                              # unit + integration (SQLite); Redis/PostgreSQL cases auto-skip
TEST_DATABASE_URL=postgresql+asyncpg://... TEST_REDIS_URL=redis://localhost:6379/1 pytest -q
ruff check src scripts tests
BENCH_DATABASE_URL=postgresql+asyncpg://... python scripts/benchmark.py   # synthetic load test
```
Coverage: configuration, DB operations, pagination, retries, fallback, 413, 429, JSON/schema validation, dates, freshness, GitHub metrics, ER, dedup, concurrency, checkpoint recovery and Sheets export. All external APIs are mocked, and fixtures are labelled synthetic.

## Troubleshooting
| Symptom | Fix |
|---|---|
| Product Hunt `401` / GraphQL `errors` | Use the application's **Developer Token** in `PRODUCTHUNT_TOKEN`, not the API key or secret |
| Hugging Face `429` with long waits | Set `HF_TOKEN`; re-run the same command, which resumes from the saved cursor |
| `GitHub rate limit exhausted` | Set `GITHUB_TOKEN`, then run `refresh-stars` |
| Sheets `storageQuotaExceeded` / `403` | Create the sheet yourself, share it with the service account as Editor, set `GOOGLE_SHEET_ID` |
| Sheets `SERVICE_DISABLED` | Enable the Sheets and Drive APIs in the service account's project |
| Gemini model `404` | Run `verify_integrations.py`; it lists Flash models available to your key |
| Jobs/News tab empty | The 24 h window is re-checked at export; run the news/jobs crawls immediately before exporting |
| `disk I/O error` on SQLite inside a synced folder (OneDrive) from a Linux VM | Use the DB from Windows directly, or point `DATABASE_URL` to a local path |

## Known limitations
- Employee counts exist only for YC companies; Hugging Face organisations leave the field `null`.
- Hugging Face models get `pricingModel=FREE` only with an open licence tag as evidence, and Spaces/models without a stated price stay `null`. The LLM fills pricing only where the text states it.
- Product Hunt does not expose the maker company separately; `startupName` is set only when the text names it.
- The guaranteed-fresh News/Jobs tabs naturally shrink as time passes; run those crawls right before submission.
- Qdrant and Neo4j are documented, optional projections and are not used by the ingestion pipeline itself.
- Benchmarks are synthetic and labelled as such; they are not production measurements.
