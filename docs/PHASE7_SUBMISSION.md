# FrontierAtlas Phase 7 — submission notes (updated 2026-11-23 13:40 IST)

## Production database writes during Phase 7 (exact record)

| Phase | Wrote to production `data/platform.db`? | Evidence |
|---|---|---|
| 7A (Alembic baseline, history-table migration) | **No** — tested on copies outside OneDrive only | `docs/phase7a_migration_test_20261122T125920Z.md` |
| 7B (migration `0002_history_tables` + baseline backfill) | **Yes** — once, 2026-11-22 17:28 UTC, after verified backup in `C:\FrontierAtlas-backups` | `docs/phase7b_live_20261122T172812Z.md/.json` |
| 7C–7F (history capture, evidence, change feed, comparisons, UI) | **No** — code + tests only; tests run on temporary databases | `docs/FINAL_TEST_REPORT.md` |
| Final verification / Windows test runs | **No** — read-only; runner hashes the DB before and after | `docs/final_tests_windows_*.json` |
| Activation (first live collection + evidence sync) | **Yes** — 2026-11-23 08:40–08:41 UTC, authorized by the user | `docs/activation_execution_20261123T084016Z.log/.json` |

## Checksums (SHA-256 of `data/platform.db`) and which state they represent

| Hash | State |
|---|---|
| `67e9335b6b257682357a60476b7d27690dcacff730d7bc12544c80b3801ac16d` | **Before Phase 7B** (7 original tables, 19,185,664 bytes). Also the hash of the cold pre-7B backup `C:\FrontierAtlas-backups\phase7b-live-20261122T172812Z\platform.pre7b.cold.db`. |
| `c916f9cb81a036537318db3b9223c563ef7e3bcd3618269362017180dc1badff` | **After Phase 7B** (revision `0002_history_tables`, 12 history tables, baseline batch). 41,025,536 bytes, integrity ok. State through 7C–7F, all verification and up to activation. |
| `c153831df1ca7651ad1f98a10a76e01243748f5f02c4f3ae374ef375eda59c6c` | **After activation (current)**: 1 completed live batch, evidence persisted. |

No migration was added after 7B: 7C–7F use the 12 tables created by `0002_history_tables`.

## What runs today (read-only, on production data)
- Record **History** tab: `/{type}/{id}?tab=history` (versions, comparison, metrics, observations).
- **Change Feed**: `/intelligence/changes` (new / modified / disappeared / reappeared, batches, coverage).
- **Historical Changes** (7F): `/intelligence/comparisons`.
- Organisation **Timeline** and **Evidence** tabs: `/organisations/{id}?tab=timeline|evidence`.
- **Source Reliability**: `/operations/reliability`.

Production now holds the baseline plus 1 completed live batch (2026-11-23); emerging/trend labels stay off until 30 distinct
days of **completed** live batches exist (30-day rule).

## Gemini / LLM costs and `--no-llm`
- `collect` and `crawl` call the extraction LLM (Gemini `gemini-3.1-flash-lite`, configured in `src/config/settings.py`)
  for records that are new or whose content changed. These are billed per token by Google beyond any free-tier quota
  on the key; the cost scales with the number of new/changed records.
- `--no-llm` disables every LLM call: records are stored with rule-based extraction only. **No paid API calls are made**
  with `--no-llm` (crawling public RSS/ATS/API endpoints is free; GitHub/HF tokens are rate-limit tokens, not billed).
- Recommended: first activation runs with `--no-llm`; enable the LLM only after reviewing record volumes.

## Production activation — EXECUTED 2026-11-23 08:40 UTC (SUCCESS; see FINAL_TEST_REPORT §0)
Status 2026-11-23: preflight READY (`activation_preflight_20261123T074453Z`), backup re-verified, rollback rehearsed on a
disposable copy, dry run PASS (`activation_dryrun_20261123T080655Z`). Exact package: `FINAL_SUBMISSION_REPORT.md` §13.
1. Stop the app (`scripts\stop_app.ps1`), pause OneDrive, run the preflight backup/verification
   (`RUN_ACTIVATION_PREFLIGHT.cmd`, opens production read-only; writes and verifies two backups in `C:\FrontierAtlas-backups`).
2. Enable history: `HISTORY_ENABLED=true` in `.env` — **done by you 2026-11-23 07:49 UTC** (`collect` and `sync-evidence` refuse with exit 4 otherwise).
3. First **limited** collection (news + jobs, no LLM, no paid calls; rehearsed on a disposable copy: `docs/activation_dryrun_20261123T080655Z.json`).
   Excludes `marktechpost` (INACCESSIBLE in rehearsal), `venturebeat_ai` (last run failed), `producthunt` (token rejected) and `the_decoder` (disabled):
   ```
   .venv\Scripts\python.exe -m src.main collect --trigger manual --no-llm --concurrency 1 --source techcrunch_ai --source theverge_ai --source mit_tr_ai --source arstechnica_ai --source openai_news --source greenhouse --source ashby --source lever --source remoteok --source himalayas
   ```
4. Persist entities, resolution decisions, claims and evidence links: `.venv\Scripts\python.exe -m src.main sync-evidence`
5. Restart via `RUN_APP.cmd` (API stays `API_READ_ONLY=true`).

### Optional schedule (not created — needs separate approval)
- 07:30 IST: `.venv\Scripts\python.exe -m src.main collect --trigger daily_0730 --no-llm --source <each healthy source>`.
  Do **not** use `--vertical all` while `producthunt` (token rejected) or `marktechpost` (inaccessible) fail: any failed or
  inaccessible source makes the batch `partial`, and partial batches never count toward the 30-day rule. List the healthy
  sources explicitly (the 10 above, plus `yc_startups`, `hf_orgs`, `hf_products`, `research_papers` once each has been rehearsed).
- 13:30 IST — **one** command for both verticals. A batch is identified by trigger + scheduled slot; a second
  `collect --trigger news_jobs_1330` in the same slot is refused (`BatchAlreadyCompleted`), so news and jobs must be in
  the same invocation, using repeated `--source` (the CLI's `--vertical` takes a single value):
  ```
  .venv\Scripts\python.exe -m src.main collect --trigger news_jobs_1330 --no-llm --source techcrunch_ai --source theverge_ai --source mit_tr_ai --source arstechnica_ai --source openai_news --source greenhouse --source ashby --source lever --source remoteok --source himalayas
  ```

Caution: do not run `rebuild-entities` once history is enabled without review. It renames canonical entities, which can
change record keys and detach history.
