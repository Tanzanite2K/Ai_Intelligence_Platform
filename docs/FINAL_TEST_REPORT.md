# FrontierAtlas — Final Test Report (updated 2026-11-23 13:40 IST)

All results below come from real runs; artefacts are in `docs/`. The only production write is the authorized activation (§0);
every test run (§1–§4) left production at `c916f9cb…` before and after.

## 0. Production activation — EXECUTED 2026-11-23 08:40–08:41 UTC (authorized by the user)
Log: `docs/activation_execution_20261123T084016Z.log`; report: `docs/activation_execution_20261123T084016Z.json`
(`scripts/activation_execute.py` via `RUN_ACTIVATION_EXECUTE.cmd`). Outcome: **SUCCESS**, exit 0.
- Pre-write checks (all PASS): app stopped (ports 8000/3000 closed, no -wal/-journal, mtime stable); backup
  `C:\FrontierAtlas-backups\activation-20261123T083552Z` verified (cold = `c916f9cb…`, SHA256SUMS match, integrity ok, 0 FK on both);
  production SHA-256 = `c916f9cb…` (expected); `HISTORY_ENABLED=true`, `API_READ_ONLY=true`, database = `data\platform.db`.
- `collect --trigger manual --no-llm --concurrency 1 --source …×10` → exit 0; batch `f7d92ead-5c27-50c1-9910-c1d42602748e` (`manual`)
  **completed** 08:40:24–08:41:20 UTC. All 10 sources **OK**: techcrunch_ai 20 fetched/3 stored, theverge_ai 10/3, mit_tr_ai 10/0,
  arstechnica_ai 20/0, openai_news 1,422/0, greenhouse 1,993/3, ashby 1,810/8, lever 552/0, remoteok 100/0, himalayas 500/43.
- `sync-evidence` → exit 0.
- Tables modified (content digests): collect → records, record_observations, record_versions, presence_events, ingestion_batches,
  source_runs, crawl_checkpoints, entity_mappings; sync-evidence → entities, entity_resolution_events, claims, evidence_links.
  Schema unchanged. No other table changed.
- Counts before → after: records 6,708 → 6,767; record_observations 6,708 → 6,768; record_versions 6,708 → 6,768;
  presence_events 0 → 59; ingestion_batches 1 → 2; source_runs 42 → 52; entity_mappings 4,944 → 4,997; entities 0 → 2,602;
  entity_resolution_events 0 → 4,990; claims 0 → 8,673; evidence_links 0 → 8,750; dead_letters 16 → 16.
- After: integrity ok, 0 FK violations, revision `0002_history_tables`. LLM construction/extraction calls **0**; LLM hosts **none**;
  hosts contacted = the 10 sources only.
- Production SHA-256 after activation: **`c153831df1ca7651ad1f98a10a76e01243748f5f02c4f3ae374ef375eda59c6c`**.

## 0b. Live application after activation (2026-11-23 08:50–09:01 UTC, `RUN_APP.cmd`, dev mode)
- API started read-only (`"read_only": true`, `"admin_enabled": false`); web app http://127.0.0.1:3000 served every page with HTTP 200.
- API (GET only): health ok; coverage: 2 batches, 1 live batch, 1 collection day, emerging labels disabled (30-day rule);
  latest batch `f7d92ead-5c27-50c1-9910-c1d42602748e` `manual` completed; change feed (7 d): 60 = new 59 + modified 1;
  overview total 6,767; source reliability 2026-11-23: 10 sources × 1 OK run.
- Database (read-only query): revision `0002_history_tables`, integrity ok, 0 FK, records 6,767, ingestion_batches 2, presence
  `appeared` in the live batch 59, source_runs on 2026-11-23 = 10 OK, claims 8,673, evidence_links 8,750, entities 2,602;
  SHA-256 unchanged at `c153831d…` (the app does not write).
- Frontend (built-in browser, real API responses replayed because that browser blocks cross-port calls): Dashboard 6,767 records,
  Jobs 146, News 22, 52 runs, "API online"; Change Feed: New 59, Modified 1, 1 live batch, labels "Not yet"; Historical Changes:
  "1 recorded so far" and (after the fix) "23 Nov 2026 — 59 new"; Source Reliability: 2026-11-23 bar, 10 OK; Organisation Timeline
  and Evidence render (Alibaba Qwen had no new records in this batch). The organisation Evidence tab computes claims from records
  at request time; the persisted claims table is verified at database level.

## 0c. Google Sheets export (authorized 2026-11-23 14:34 IST) — PASS
Log `docs/sheets_export_20261123T090558Z.log`, report `docs/sheets_export_run_20261123T090558Z.json`, exporter result `docs/sheets_export.json`.
Service-account file present, sheet ID configured; `python -m src.main export --sheets` exit 0 (09:06:00–09:06:23 UTC).
Sheet read back after writing: exactly the 6 expected tabs, headers match, no public link. Rows (sheet = database, export-time rule):
Startups 2,509; Products 1,800; Research Papers 2,290 (papers with GitHub stars); Jobs 54 and News 6 (published in the last 24 h —
by design, Jobs/News tabs contain only fresh items); Entity Mapping Log 4,997. Duplicate or empty record URLs: 0 in every tab.
Production SHA-256 unchanged (`c153831d…`). Sheet: https://docs.google.com/spreadsheets/d/<sheet-id-redacted>

## 1. Complete Windows 11 verification — `RUN_FINAL_TESTS.cmd`, stamp 20261123T080531Z — **OVERALL PASS**
One uninterrupted run after the last code change, with `.env` as it is now (`HISTORY_ENABLED=true`, `API_READ_ONLY=true`).
Python 3.13.12, Node v22.17.1, Windows NT 10.0.26300. Full timestamped output: `final_tests_windows_20261123T080531Z.log`
(each step logs its UTC start time); summary: `final_tests_windows_20261123T080531Z.json`.

| # | Step (UTC start) | Result | Evidence |
|---|---|---|---|
| 1 | Backend pytest (08:05:31) | **PASS** | 418 collected: **415 passed, 3 skipped, 0 failed** — `test-results-final-windows-20261123T080531Z.xml`. Skips: Redis tests (no `TEST_REDIS_URL`). Tests build `Settings(_env_file=None)`, so `.env` flags cannot change them. |
| 2 | Ruff `src tests scripts migrations` | **PASS** | "All checks passed!" |
| 3 | Migration 7A path on the **pre-7B backup** copy | **PASS 16/16** | source `C:\FrontierAtlas-backups\phase7b-live-20261122T172812Z\platform.pre7b.cold.db` (`67e9335b…`), copied to `C:\FrontierAtlas-test` |
| 4 | Post-7B schema on a **copy** of production | **PASS** | revision `0002_history_tables` = head; `upgrade head` no-op; ORM == schema; integrity ok; 0 FK |
| 5 | Database safety (`database_safety_20261123T080621Z.json`) | **PASS** | production integrity ok, 0 FK, hash = expected post-7B hash, no -wal/-journal; latest backup verified (§2); rollback rehearsal PASS (§3) |
| 6 | Security check (`security_check_20261123T080631Z.json`) | **PASS_WITH_FINDINGS** | secret scan clean; GitHub, Hugging Face, Gemini accepted; Product Hunt **rejected (401)**; Neo4j configured but not running (see FINAL_SUBMISSION_REPORT §Security) |
| 7 | Activation dry run on a disposable copy (`activation_dryrun_20261123T080655Z.json`) | **PASS** | §4 |
| 8 | TypeScript `tsc --noEmit` | **PASS** | isolated copy `C:\FrontierAtlas-test\fe-build-20261123T080531Z` |
| 9 | ESLint | **PASS** | exit 0 |
| 10 | `next build` (production build) | **PASS** | exit 0 |
| — | Production `data/platform.db` | **unchanged** | `c916f9cb…` before and after |

### Runs today (for transparency)
- 20261123T080034Z — FAIL in two steps, both fixed, then the whole suite was repeated:
  - database safety: Windows refused to rename the rehearsal file because the check script left SQLite connections open
    (`with sqlite3.connect()` commits but does not close). Fixed with `contextlib.closing` (also in the preflight and dry run).
  - security check returned non-zero for a rejected Product Hunt token; now reported as a finding (it is not needed by the
    approved plan); only a leaked secret fails the step.
- 20261123T080531Z — **PASS**, definitive. (Earlier passing runs, 2026-11-22, predate today's changes and are superseded.)

## 2. Latest backup verification — `C:\FrontierAtlas-backups\activation-20261123T074453Z` (outside OneDrive)
| File | SHA-256 | Checks |
|---|---|---|
| `platform.pre-activation.cold.db` | `c916f9cb…1badff` (= production, = SHA256SUMS.txt) | integrity ok, 0 FK |
| `platform.pre-activation.online.db` | `3920c9af…451f050f` (= SHA256SUMS.txt) | integrity ok, 0 FK, row counts = production, revision `0002_history_tables` |

## 3. Rollback rehearsal (disposable copy `C:\FrontierAtlas-test\rollback-20261123T080621Z`)
1. Copied the online backup; changed it with the real `sync-evidence` (+2,580 entities, +4,937 resolution events, +8,642 claims,
   +8,697 evidence links). 2. Restored with `scripts/db_restore.py` from the cold backup: restored SHA-256 = `c916f9cb…`
   (= backup), integrity ok, the changed file kept as `platform.failed-20261123T080630Z.db` (never deleted).
3. `db_restore` refused the live `data\platform.db` without `--allow-live` before any file operation. → **PASS**.

## 4. Activation dry run (disposable copy; exact approved commands; 10 sources)
`collect --trigger manual --no-llm --concurrency 1 --source …×10` then `sync-evidence`: both exit 0; batch `manual`
**completed**. Per-table content digests (catch UPDATEs, not only INSERTs):
- `collect` modified only: records, record_observations, record_versions, presence_events, ingestion_batches, source_runs,
  crawl_checkpoints, entity_mappings. Row deltas: records +60, observations +61, versions +61 (one existing record changed),
  presence +60, entity_mappings +54, source_runs +10, ingestion_batches +1. Schema unchanged.
- `sync-evidence` modified only: entities +2,602, entity_resolution_events +4,991, claims +8,674, evidence_links +8,751.
- LLM provider construction/extraction calls: **0**; LLM hosts contacted: **none**; hosts contacted = only the 10 sources.
- Source availability (all **OK**): techcrunch_ai 20 fetched/3 stored, theverge_ai 10/3, mit_tr_ai 10/0, arstechnica_ai 20/0,
  openai_news 1,422/0, greenhouse 1,994/3, ashby 1,810/9, lever 552/0, remoteok 100/0, himalayas 500/43
  (most items are older than the 24-hour freshness window and are rejected as stale by design).
- Copy integrity ok, 0 FK violations; production hash unchanged.

## 5. Phase 7C–7F coverage (in the 415 passing tests)
| File | Tests | Covers |
|---|---|---|
| `integration/test_history_capture.py` | 9 | batches (UUID5 slot, completed slot refused, running resumes), observations idempotent, versions only on content change, metric snapshots, appeared/reappeared, disappearance only after complete full-catalogue scan ≥80 %, 30-day rule counts **completed** batches only |
| `integration/test_api_history.py` | 7 | record history/compare/metrics, coverage, batches, change feed, source reliability, evidence; platform hosts (github.com, huggingface.co…) never treated as a company website |
| `integration/test_history_registry.py` | 2 | entity/claim/evidence persistence (`sync-evidence`), stable uuid5 entity ids |
| `integration/test_collect_cli.py` | 1 | `collect` end-to-end on a temp DB: completed batch, observations, catalogue scan; re-run of same slot raises `BatchAlreadyCompleted` |
| `unit/test_migrations.py`, `test_phase7b_*` | 17 | Alembic env guards, backfill, live-run gates |
| `unit/test_settings_placeholders.py` (new) | 8 | punctuation-only placeholder credentials (`KEY=-`) are treated as unset; real values kept |

## 6. Browser / API verification (2026-11-23 00:06–00:45 IST; UI code unchanged since)
**Desktop, Claude in Chrome (2026-11-23 00:06–00:10 IST, live API):** all 22 sidebar routes + detail routes rendered with an
`h1`, correct empty/error states ("Record not found", "Page not found"); no duplicate-key warning on the Brief; only console
exceptions were Chrome-extension messaging errors. API GETs: `/health`, `/api/v1/history/coverage|batches|changes`,
`/operations/source-reliability`, `/intelligence/changes`, `/records/{id}/history|compare|metrics`,
`/entities/{id}/timeline|evidence` → 200; unknown record 404 `not_found`; invalid `days` 422 `invalid_request`.

**Viewports 390×844, 375×812, 1920×1080 (Claude desktop built-in browser, 2026-11-23 00:30–00:45 IST).** Chrome was not
reachable at that time. The built-in browser blocks a page's requests to another localhost port, so each page was rendered with
the **real responses of the running API** (47 GET responses fetched top-level from `localhost:8000`, all HTTP 200) replayed into
the page by a fetch shim. Nothing was mocked or edited; the shim and its storage were removed afterwards. 29–33 route/tab states per
viewport (all sidebar pages, a trend topic, record details incl. History tabs, organisation Timeline/Evidence):
- Horizontal overflow: **none** at any size (document width = viewport; scrollbar-only difference at 1920).
- Navigation: at phone sizes the sidebar is hidden and an "Open navigation" button opens a dialog with all 22 links; choosing
  a link navigates and closes it; Escape closes it. At 1920 the sidebar is visible and the menu button hidden.
- Tables: every wide table sits in a horizontal scroll container (the one unwrapped Ingestion table is 318–448 px and fits).
- Charts: every Recharts surface fits inside the viewport.
- Change Feed, Historical Changes, Source Reliability, record History, organisation Timeline and Evidence: render fully,
  no "API unavailable", no stuck loading.
- Screenshots were captured and reviewed in-session (not saved to files). Visual notes (not defects): wide tables scroll
  without a visible cue on phones; the Next.js dev badge overlaps the bottom-left corner in dev mode.


