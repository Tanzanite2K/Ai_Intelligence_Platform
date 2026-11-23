# FrontierAtlas — Final Submission Report (updated 2026-11-23 13:40 IST)

## 1. Summary
FrontierAtlas is an AI-intelligence platform: async crawlers → validation/dedup → (optional LLM) extraction → SQLite →
read-only FastAPI → Next.js app. Phase 7 added an auditable history and evidence layer. Everything listed as
"implemented" is tested (`FINAL_TEST_REPORT.md`); items "awaiting activation" need `AUTHORIZE PRODUCTION WRITES`.

## 2. Production database status (after activation, 2026-11-23 08:41 UTC)
- Path: `data\platform.db`. SHA-256 **`c153831df1ca7651ad1f98a10a76e01243748f5f02c4f3ae374ef375eda59c6c`**, Alembic `0002_history_tables`,
  integrity ok, 0 FK violations.
- Rows: records 6,767; record_observations 6,768; record_versions 6,768; presence_events 59; ingestion_batches 2 (baseline +
  `manual` completed); source_runs 52; entity_mappings 4,944 → 4,997; entities 2,602; entity_resolution_events 4,990; claims 8,673;
  evidence_links 8,750; canonical_entities 62; dead_letters 16.
- Production writes: Phase 7B (2026-11-22 17:28 UTC) and the authorized activation (2026-11-23 08:40–08:41 UTC). Nothing else.
- Checksum history: `67e9335b…` (before 7B) → `c916f9cb…` (after 7B) → `c153831d…` (after activation, current).

## 3. Architecture
Crawlers (`src/crawlers`, registry with per-domain rate limits, robots/cooldown handling) → `src/pipelines/ingestion.py`
(validation, dedup hash, checkpoints, dead letters, history hook) → SQLAlchemy 2 async / aiosqlite → FastAPI `/api/v1`
(`API_READ_ONLY=true`, DB opened `mode=ro`) → Next.js 16 / React 19 / TanStack Query / Zod / Recharts.

## 4. Sources
Startups: `yc_startups`, `hf_orgs` · Products: `hf_products`, `producthunt` (token rejected) · Research: `research_papers` (arXiv) ·
News: 8 RSS feeds (`the_decoder` disabled, `venturebeat_ai` last run failed) · Jobs: Greenhouse, Ashby, Lever, RemoteOK, Himalayas.

## 5. Phase 7A–7B (done, in production)
Alembic baseline + `0002_history_tables` (12 tables, 26 indexes), live-DB guard (`-x allow_live=yes`), baseline backfill
(1 batch, 6,708 observations + versions, origin `baseline_backfill`), verified backups in `C:\FrontierAtlas-backups`.

## 6. Phase 7C — history capture (implemented, awaiting activation)
`src/history/capture.py` `HistoryRecorder`: batch id = UUID5(trigger, scheduled slot) — completed slot refused, running slot
resumes; observations `ON CONFLICT DO NOTHING`; new version only when content hash changes (previous version closed);
metric snapshots (github_stars, employeeCount); presence: appeared / reappeared, and disappeared **only** after a complete
scan of a full-catalogue source (`yc_startups`) covering ≥80 % of the previous complete scan. CLI: `python -m src.main collect`
(refuses with exit 4 unless `HISTORY_ENABLED=true`).

## 7. Phase 7D — evidence and corroboration
Claims: builds_product, hiring, directory_listing, yc_batch, website, news_coverage. Source groups; aggregators syndicated;
same-story merge at Jaccard ≥ 0.6. Confidence: primary 0.60 / reported 0.45 / syndicated 0.30, +0.15 per extra independent
group, cap 0.95; conflicts capped 0.35; needs-review ×0.7. Co-mentions never become claims. Platform hosts (GitHub, Hugging Face,
YC, LinkedIn…) are never accepted as a company website. Persistence via `sync-evidence` (entity_id = uuid5 of compact key) into entities, resolution events, claims and evidence links; source groups are computed at read time, not stored.

## 8. Phase 7E — change feed and record history
APIs: `/records/{id}/history`, `/history/compare`, `/history/metrics`, `/history/coverage`, `/history/batches`,
`/history/changes`, `/operations/source-reliability`, `/entities/{id}/timeline`, `/entities/{id}/evidence`.
UI: record History tab, Change Feed, organisation Timeline/Evidence tabs, Source Reliability.

## 9. Phase 7F / E — real historical distinctions
`/intelligence/changes` + `/intelligence/comparisons`: 28-day windows anchored at the latest paper publication date;
YC batch series; hiring needs ≥14 publication days; metrics need ≥2 samples. Each section is labelled **measured**
(from dated records) or **descriptive** (current listings), with linked examples. Snapshot-based emerging labels need
30 days of *completed* live batches (only completed batches count — fixed in this phase). Undated records are shown as
"on or before" the baseline; nothing is projected or back-filled.

## 10. Bugs and issues fixed in the final phase
1. Duplicate React key on Intelligence Brief (org repeated across topics) — key includes position; both appearances kept.
2. 30-day rule counted partial/failed batches — now completed only (+ test).
3. Platform hosts could be read as company websites — guard + test.
4. Docs: 13:30 schedule used two `collect` calls for one slot — now one call with repeated `--source`.
5. Docs: checksum states, which phase wrote to production, Gemini cost / `--no-llm` clarified.
6. Windows migration test assumed a pre-7B database — now replays 7A on the pre-7B backup + post-7B check on a copy.
7. Activation source list: `marktechpost` made a rehearsal batch partial — removed.
8. **Placeholder credentials** (`GROQ_API_KEY`/`DEEPSEEK_API_KEY` set to one punctuation character) counted as configured,
   so an LLM-enabled run would send junk keys. `src/config/settings.py` now treats values without any letter/digit as unset
   (+8 tests). No effect on `--no-llm` runs.
9. **`.gitignore`** only ignored `data/*.db`; a database or backup copied elsewhere in the tree could be committed. Now `*.db`,
   `*.db-journal` and `backups/` are ignored anywhere (verified with `git check-ignore` in a scratch repo).
10. **Hard-coded Neo4j password** (`neo4j/change-me-please`) in `docker-compose.yml` (optional `graph` profile) — now read
    from `NEO4J_PASSWORD` in `.env`; if unset Neo4j refuses to start instead of using a known default. `.env.example` no longer
    suggests `change-me`.
12. **Historical Changes → "Newly discovered records"** rendered only its heading after activation: the API returns a per-day/type
    series but the page had no renderer for it. Added `NewRecords` in `frontend/src/components/history/intel-changes.tsx`; now shows
    "23 Nov 2026 — 59 new (jobs 53 · news 6)" (tsc + ESLint pass).
11. Check scripts left SQLite connections open (`with sqlite3.connect()` does not close) — fixed with `contextlib.closing`;
    preflight report now written as UTF-8 (the "—" was garbled).

## 11. Testing (details in `FINAL_TEST_REPORT.md`)
Complete Windows 11 run `RUN_FINAL_TESTS.cmd` (stamp 20261123T080531Z, after the last change, `.env` as configured now):
**OVERALL PASS** — pytest 415 passed / 3 skipped (Redis) / 0 failed; Ruff clean; 7A migration 16/16 on the pre-7B backup;
post-7B schema on a copy PASS; database safety PASS (production integrity, backup verification, rollback rehearsal);
security check PASS with findings (§12a); activation dry run PASS; tsc, ESLint, `next build` PASS; production unchanged.
Browser (2026-11-23 00:06–00:45 IST): all routes at 390×844, 375×812, 1920×1080 — no horizontal overflow, working mobile menu.

## 12. Safety controls and environment flags (verified in code)
| Flag / control | Current value | Actual behaviour |
|---|---|---|
| `API_READ_ONLY` | `true` (`.env`) | API opens SQLite with `mode=ro` (`src/api/app.py`); no DDL from the web tier. Applies to SQLite only (the Docker/Postgres stack is not read-only). |
| `ADMIN_API_KEY` | unset | the two POST routes (`/crawl/{source}`, `/api/v1/admin/crawl/{source}`) return 503 `admin_disabled` |
| `HISTORY_ENABLED` | `true` (`.env`, set by you 2026-11-23 07:49 UTC) | only read by the CLI: `collect` and `sync-evidence` run instead of refusing (exit 4). The API does not read it. Nothing runs automatically. |
| `FRESHNESS_WINDOW_HOURS` | `24` | job/news crawlers store only items published in the last 24 h (`src/crawlers/fresh.py`) |
| `RESPECT_ROBOTS_TXT` | `true` | robots.txt checked before every crawler request (`src/core/http.py`) |
| `--no-llm` | used in the plan | `pipeline_context(use_llm=False)` never builds an `LLMOrchestrator`; dry run: 0 LLM calls, 0 LLM hosts |
| Alembic live guard | on | migrations refuse `data/platform.db` without `-x allow_live=yes` |
| Scheduler | none | no script or module creates a Windows scheduled task (searched); the Celery beat schedule runs only if the Docker `beat` service is started |

## 12a. Security audit (no secret value was printed, logged or stored)
- **Secret scan** of the whole working tree (including all docs and logs) for token patterns and for the literal `.env`
  secret values: **clean** (0 findings).
- **Git:** this folder is not a Git repository (no `.git`), so local history cannot be checked. The remote
  `github.com/Tanzanite2K/Ai_Intelligence_Platform` (named in `USER_AGENT`) could not be inspected from this session
  (access not granted). **Action:** run a history scan (e.g. `gitleaks detect --log-opts="--all"`) on your clone; if any
  secret appears, rotate it and rewrite history (`git filter-repo`) — rotation is what matters, since forks/clones keep old commits.
- **Ignore rules:** `.env`, `.env.*`, `credentials/`, service-account JSON, keys, all `*.db`, backups → ignored (verified).
  `.dockerignore` excludes `.env`, `credentials/`, `data/`.
- **Credential validity (read-only, non-billed calls):** GitHub accepted (fine-grained, expires 2026-12-21); Hugging Face
  accepted (fine-grained, **created 2026-11-21 09:55 UTC**); Gemini accepted; **Product Hunt rejected (401 invalid_oauth_token)**;
  Groq/DeepSeek unset. **Rotation cannot be proven** from here: that needs the old values. The HF creation date (and a GitHub
  expiry consistent with a 30-day token created the same day) means these two were **not** replaced after 2026-11-21 —
  if the exposure happened later, rotate them. Gemini shows no creation date: confirm in Google AI Studio that the old key
  is deleted. The Google service-account key file (in `credentials/`, dated 2026-11-21) should also be rotated if it was exposed.
- **Neo4j:** `NEO4J_URL`/`NEO4J_PASSWORD` are set in `.env`, but nothing in `src/` uses Neo4j, nothing listens on port 7687,
  and the `neo4j` driver is not installed — so "the running database accepts the new password" **cannot be verified: there is
  no running Neo4j**. When you start the `graph` profile, it now takes the password from `.env`.
- Local-only dev defaults remain in `docker-compose.yml` (Postgres `aip/aip`, Grafana anonymous) — acceptable for a local stack, not for deployment.

## 13. Activation — EXECUTED 2026-11-23 08:40 UTC after `AUTHORIZE PRODUCTION WRITES` (results: FINAL_TEST_REPORT §0)
Outcome SUCCESS: batch `manual` completed, all 10 sources OK, only the expected tables changed, 0 LLM calls, integrity ok, 0 FK.
The plan below is kept as the record of what was approved.

**State now (security gate 2026-11-23 08:32 UTC, `docs/security_gate_20261123T083206Z.log`):** preflight READY again
(`docs/activation_preflight_20261123T083231Z.md`, app stopped, ports closed); new verified backup
`C:\FrontierAtlas-backups\activation-20261123T083231Z` (cold = `c916f9cb…`, integrity ok, 0 FK; online integrity ok, row counts =
production); the earlier `activation-20261123T074453Z` backup is equally valid (same hash). `HISTORY_ENABLED=true`; `API_READ_ONLY=true`.

**Before running:** app stopped (`scripts\stop_app.ps1`; ports 8000/3000 closed), OneDrive paused, production hash still
`c916f9cb…` (re-run `RUN_ACTIVATION_PREFLIGHT.cmd` if anything changed — it creates a fresh verified backup).

**Exact commands** (project folder):
```
.venv\Scripts\python.exe -m src.main collect --trigger manual --no-llm --concurrency 1 --source techcrunch_ai --source theverge_ai --source mit_tr_ai --source arstechnica_ai --source openai_news --source greenhouse --source ashby --source lever --source remoteok --source himalayas
.venv\Scripts\python.exe -m src.main sync-evidence
```
Then `RUN_APP.cmd`, resume OneDrive.

**Tables that may be modified** (measured by content digests in the rehearsal; anything else fails the rehearsal):
- `collect`: records, record_observations, record_versions, presence_events, ingestion_batches, source_runs,
  crawl_checkpoints, entity_mappings; possibly dead_letters. Not touched: schema, metric_snapshots, catalogue_scans
  (no full-catalogue source), canonical_entities, seen_items, source_groups, source_reliability_daily.
- `sync-evidence`: entities, entity_resolution_events, claims, evidence_links.

**Expected changes** (rehearsal 08:06 UTC; live numbers depend on what is published in the 24 h before the run):
records +~60, record_observations +~61, record_versions +~61, presence_events (appeared) +~60, entity_mappings +~54,
source_runs +10, ingestion_batches +1 (`manual`, expected `completed`); entities +~2,600, entity_resolution_events +~4,990,
claims +~8,670, evidence_links +~8,750. No deletes. Transactions: each flush commits records, then history, separately; a
history failure leaves that flush's records committed, marks the source FAILED and the batch `partial` (excluded from the
30-day rule; the next batch re-derives versions from the records table); `sync-evidence` is two
idempotent transactions (re-runnable).

**Paid API calls:** none (`--no-llm`; all 10 sources are public endpoints).

**Verify after:** integrity + FK checks; `/api/v1/history/coverage` → 1 completed live batch; Change Feed lists new items.

**Rollback** (tested on a disposable copy, §3 of FINAL_TEST_REPORT), app stopped:
```
.venv\Scripts\python.exe scripts\db_restore.py --from C:\FrontierAtlas-backups\activation-20261123T083231Z\platform.pre-activation.cold.db --to data\platform.db --expect-sha256 c916f9cb81a036537318db3b9223c563ef7e3bcd3618269362017180dc1badff --allow-live
```
It refuses while ports 8000/3000 are open or -wal/-journal files exist, keeps the activated file as
`data\platform.failed-<stamp>.db` (never deleted), and verifies the restored hash and integrity. Then set `HISTORY_ENABLED=false`.

## 14. Files (final phase, including 2026-11-23)
Created: `scripts/activation_execute.py`, `scripts/run_activation_execute.ps1`, `RUN_ACTIVATION_EXECUTE.cmd`, `RUN_SECURITY_GATE.cmd`, `scripts/run_security_gate.ps1`, `scripts/activation_preflight.py`, `RUN_ACTIVATION_PREFLIGHT.cmd`, `scripts/activation_dryrun.py`, `RUN_ACTIVATION_DRYRUN.cmd`,
`scripts/post7b_schema_check.py`, `scripts/run_migration_checks.ps1`, `RUN_MIGRATION_CHECKS.cmd`, `scripts/run_final_tests.ps1`,
`RUN_FINAL_TESTS.cmd`, `scripts/database_safety_check.py`, `scripts/security_check.py`, `tests/integration/test_collect_cli.py`,
`tests/unit/test_settings_placeholders.py`, `docs/FINAL_SUBMISSION_REPORT.md`, `FINAL_TEST_REPORT.md`, `FINAL_DEMO_SCRIPT.md`,
`KNOWN_LIMITATIONS.md`.
Modified: `src/config/settings.py`, `.gitignore`, `.env.example`, `docker-compose.yml`, `frontend/src/components/intelligence/brief.tsx`,
`src/api/v1/history.py`, `src/history/evidence.py`, `tests/integration/test_api_history.py`, `tests/integration/test_history_capture.py`,
`docs/PHASE7_SUBMISSION.md`. Not modified: `.env`, `credentials/`, any database or backup.

## 15. Limitations
See `KNOWN_LIMITATIONS.md`.

## 16. Demo
See `FINAL_DEMO_SCRIPT.md`.

## 17. Readiness verdict
**Ready for submission. The history layer is activated** (1 completed live batch, evidence persisted) and the API remains read-only. Credential rotation for GitHub/Hugging Face (and the Product
Hunt token) needs your action; it does not block activation (no credential is used by the approved plan).
