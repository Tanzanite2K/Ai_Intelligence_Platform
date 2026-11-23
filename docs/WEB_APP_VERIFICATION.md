# Web application verification record — 2026-11-21

Scope: FastAPI `/api/v1` + Next.js frontend (phases 1–5). All checks below were run on 2026-11-21 against either the
user's project files (synced copy, Linux VM, Python 3.12) or a **read-only copy of the real database**
(SHA-256 `535917b5e9dde69ebb8f1a399c8e354453efc1a7fde963da84e77af6aa593a72`, unchanged before and after).
Nothing was deployed; no crawl, enrichment, retry or entity rebuild was run.

## Quality gates (user's project)

| Gate | Result |
|---|---|
| Backend tests (`pytest tests`) | **249 passed, 3 skipped** |
| Ruff (`src scripts tests`) | All checks passed |
| Secret scan (`scripts/scan_secrets.py`) | clean, 0 findings (4 `.env` secret values checked) |
| Frontend typecheck (`tsc --noEmit`, strict) | passed |
| Frontend lint (`eslint .`) | 0 problems |
| Frontend production build (`next build`) | passed — 22 routes (16 static, 6 dynamic) |

## Browser integration suites (Chrome headless, production build, real data via the API)

| Suite | Result | Covers |
|---|---|---|
| Phase 3 — discovery | 50 / 50 | Explorer totals/first rows = API, filters → server-side params, pagination, debounced search, detail pages for every type, provenance/evidence, not-found handling, mobile |
| Phase 4 — entities | 35 / 35 | Organisation explorer/profile vs API, verified-only links (Cohere collision), relationships page, 12 review cards with audit notes, no action buttons, clickable organisations |
| Phase 5 — operations & integration | 29 / 29 | Ingestion monitor stats (42 runs, success rate), status/source filters, source health (18 sources, never-succeeded), dead letters (16, details, stage filter), settings, responsive matrix, error states |
| Dashboard regression | no console errors | Overview at desktop/mobile, dark/light |
| API down | 6 / 6 pages | Every tested page shows "API unavailable" with Retry and the topbar shows "API offline" |

Responsive matrix: all 16 representative routes at **1440, 1024, 768 and 390 px** — no horizontal page overflow;
sidebar visible ≥ 1024 px, drawer navigation below.

Error handling verified: HTTP 429 → "Too many requests"; HTTP 500 → error state with Retry; malformed JSON →
"Unexpected response" (Zod contract check); 404 record/organisation → "not found"; invalid filter → API message.

## Performance (local, single process, real data: 6,708 records, 4,892 mappings)

Page load to network-idle (production build, desktop): 0.76 – 1.36 s for all 16 routes.

API latency, 7 requests each:

| Endpoint | Median ms | Max ms |
|---|---:|---:|
| `/health` | 1 | 11 |
| `/api/v1/dashboard/overview` | 11 | 13 |
| `/api/v1/dashboard/activity` | 17 | 24 |
| `/api/v1/dashboard/freshness` | 8 | 11 |
| `/api/v1/dashboard/recent?limit=6` | 36 | 38 |
| `/api/v1/dashboard/source-contribution` | 13 | 13 |
| `/api/v1/dashboard/quality` | 21 | 21 |
| `/api/v1/search?q=agent` | 218 | 257 |
| `/api/v1/search?q=open%20source%20model&page=3` | 258 | 316 |
| `/api/v1/search` | 17 | 21 |
| `/api/v1/startups` | 14 | 19 |
| `/api/v1/startups?category=Robotics` | 22 | 46 |
| `/api/v1/products?organisation=qwen` | 25 | 28 |
| `/api/v1/papers?sort=stars&min_stars=100` | 39 | 41 |
| `/api/v1/jobs?remote=true` | 9 | 10 |
| `/api/v1/news` | 6 | 6 |
| `/api/v1/papers/2467` | 5 | 5 |
| `/api/v1/entities` | 46 | 66 |
| `/api/v1/entities?q=ai` | 43 | 50 |
| `/api/v1/entities/e_T3BlbkFJ` | 21 | 31 |
| `/api/v1/entities/e_T3BlbkFJ/records?type=jobs` | 8 | 9 |
| `/api/v1/relationships/overview` | 62 | 146 |
| `/api/v1/reviews?page_size=100` | 285 | 368 |
| `/api/v1/operations/ingestion-runs` | 4 | 5 |
| `/api/v1/operations/source-health` | 7 | 12 |
| `/api/v1/operations/dead-letters` | 4 | 6 |

Keyword search scans the stored JSON (no full-text index): ~0.2–0.3 s at this size; it will grow linearly with data.
The relationship overview originally took 33 s on the real database (SQLite query-plan issue) and was rewritten in
phase 4 (now ~0.06 s).

## Launcher (`RUN_APP.cmd`)

Executed on the user's Windows machine (2026-11-22) by double-clicking `RUN_APP.cmd`:
- Run 1 with a manually started API and dev server already on 8000/3000: API identified as FrontierAtlas but not
  launcher-started -> RESTARTED (only its two python.exe processes stopped; new window logs `"read_only": true`);
  web dev server -> REUSED.
- Run 2: API REUSED (same PID), web REUSED; no new windows or processes.
- `/docs`, `/api/v1/intelligence/brief` and the other intelligence endpoints 200; `:3000/intelligence` 200.
Not exercised on Windows: a cold start of the web server (port 3000 free) and the conflict path (unrelated program
on a port).

## Not verified (production)

TLS/reverse proxy, PostgreSQL in production, multiple API workers (rate limiter is per process), Celery/Redis
scheduling, Docker, authentication (none implemented), public exposure, load beyond a single local user.
See `docs/DEPLOYMENT.md` for requirements and the security review.
