# FrontierAtlas — Known Limitations (updated 2026-11-23 13:40 IST)

## After activation (2026-11-23)
- History is live from 2026-11-23: 1 completed live batch (59 new records). Emerging/trend labels need 30 distinct days of
  completed batches; nothing before 2026-11-21 is reconstructed.
- Evidence is persisted (2,602 entities, 8,673 claims, 8,750 evidence links); all claims are single-source today.
- **No scheduled jobs exist.** Further batches run only when you run `collect` (documented 07:30 / 13:30 commands in
  `PHASE7_SUBMISSION.md`; no Windows task has been created).
- The API process must be restarted (`RUN_APP.cmd`) to show the new data; it was intentionally left stopped.

## Data and sources
- Corpus skew: 2,509 startups, 2,290 papers, 1,800 products, but only 93 jobs and 16 news items in the baseline.
- `producthunt`: token rejected (`invalid_oauth_token`) — needs a new Developer Token. `venturebeat_ai`: last run FAILED.
  `the_decoder`: disabled. `marktechpost`: INACCESSIBLE in the 2026-11-23 rehearsal. All four are excluded from the activation plan.
- A collection batch with any FAILED/INACCESSIBLE source closes as `partial` and does not count toward the 30-day rule.
- Job and news crawlers keep only items inside the 24-hour freshness window, so each collection adds few records
  (56 in the rehearsal); history accumulates only through regular daily runs.
- Startup and product records have no publication date; timelines place them at first-seen date, shown as "on or before".
- Disappearance is only inferred for full-catalogue sources (`yc_startups`) after a complete scan covering ≥80 % of the
  previous complete scan; RSS/ATS sources never produce "disappeared" events (absence from a feed is not evidence).
- Corroboration: all current claims are single-source (confidence 60 %). Independent corroboration needs more source groups.
- The `source_groups` table (created in 7B) is not written: source groups are computed at read time.
- `rebuild-entities` can rename canonical entities and detach history once history is enabled — do not run without review.

## Security (see FINAL_SUBMISSION_REPORT §12a)
- Credential rotation after the `.env` exposure is **not proven**: the Hugging Face token was created 2026-11-21 09:55 UTC and the
  GitHub token's expiry fits the same day; if the exposure was later, both still need rotating. Gemini key age is not visible via API.
- Product Hunt token is rejected (401); the source stays excluded until a valid Developer Token is set.
- Git history of the GitHub repository was not scanned from this session (no local `.git`, remote access not granted).
- Neo4j is configured in `.env` but not running and not used by the code; its password could not be verified against a server.
- `docker-compose.yml` keeps local-only dev credentials for Postgres (`aip/aip`) and anonymous Grafana.
- Older backups also exist inside the OneDrive project folder (`data/platform.backup-*.db`, `data/platform.pre7b-*.cold.db`);
  they are git-ignored and were not moved or deleted. The verified activation backups are outside OneDrive.

## Platform
- SQLite in a OneDrive folder: production writes need OneDrive paused and a single writer (preflight enforces no writer).
- Redis distributed dedup is implemented but its 3 tests are skipped without `TEST_REDIS_URL`.
- LLM extraction (Gemini `gemini-3.1-flash-lite`) is billed per token when enabled; activation plan uses `--no-llm`.
- Viewport checks (390×844, 375×812, 1920×1080) ran in the Claude desktop app's built-in browser, which blocks a page's
  calls to another localhost port. Pages were therefore rendered with the live API's real GET responses replayed through a
  fetch shim (data fetched from the running API; nothing mocked or edited). Screenshots were viewed during the session but not
  saved as files. Real-device testing (iOS Safari / Android Chrome) was not done.
- Minor visual notes: wide tables (Source Reliability, Ingestion) scroll horizontally on phones with no visible scroll cue;
  the Next.js dev-mode badge overlaps the bottom-left corner in dev mode only.
- The frontend runs in Next.js dev mode via `RUN_APP.cmd`; the production build is verified (`next build` PASS) but not served.
- Not deployed to a public host; runs locally (Docker files present, validated earlier in `docker_validation.json`).
