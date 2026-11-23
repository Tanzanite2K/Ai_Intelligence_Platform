# FrontierAtlas — Demo Script (~10 minutes)

Start: double-click `RUN_APP.cmd` → web app http://localhost:3000, API http://localhost:8000/docs.
Everything below is read-only and works on the current production data.

| # | Time | Screen | Say / show |
|---|---|---|---|
| 1 | 0:00 | `/` Overview | One platform ingesting AI startups, products, papers, jobs and news: 6,708 verified records from YC, Hugging Face, arXiv, ATS boards and RSS. |
| 2 | 0:45 | `/explore` | One search across all five record types. |
| 3 | 1:30 | `/papers/2272` | Record detail: normalised fields, source link, provenance. |
| 4 | 2:15 | `/organisations` → Alibaba Qwen | Entity resolution: 62 curated organisations, 4,944 mappings; aliases (Qwen, Qwen Team…). |
| 5 | 3:00 | Org → **Evidence** tab | Evidence-backed claims: each claim lists its records; confidence from independent source groups (all single-source today → 60 %). Co-mentions never become claims. |
| 6 | 3:45 | Org → **Timeline** tab | Dated events link to records; undated startups/products marked "on or before" baseline — no invented dates. |
| 7 | 4:30 | `/intelligence` Brief | Topic leaders, signals. (Same org may appear under two topics — intentional.) |
| 8 | 5:15 | `/intelligence/trends` → LLM | Trend radar from publication dates of stored records. |
| 9 | 6:00 | `/intelligence/comparisons` | 28-day windows anchored at latest paper date; "measured" vs "descriptive"; emerging labels withheld (30-day rule). |
| 10 | 6:45 | `/intelligence/changes` | Change Feed: baseline 21 Nov 2026, 0 live batches → "no changes yet". Explain batches, versions only on content change, disappearance only after complete catalogue scans. |
| 11 | 7:30 | `/startups/13288?tab=history` | Record history: v1 baseline, observations, compare versions, metrics. |
| 12 | 8:15 | `/operations/sources` → `/operations/reliability` | Source health with actionable fixes (Product Hunt token), reliability per day. |
| 13 | 9:00 | `/operations/dead-letters`, `/operations/ingestion` | Failure handling and run monitor. |
| 14 | 9:30 | http://localhost:8000/docs | Read-only versioned API (`/api/v1/...`), typed errors (404 `not_found`, 422 `invalid_request`). |

If the first collection has been run and approved before the demo, step 10 instead shows one completed live batch and
the new items (rehearsal: ~60), and the Evidence tab shows persisted claims.

Close: production safety — read-only API, Alembic-managed schema with live-DB guard, verified backups, history activation
pending explicit authorization (see `FINAL_SUBMISSION_REPORT.md` § Activation).
