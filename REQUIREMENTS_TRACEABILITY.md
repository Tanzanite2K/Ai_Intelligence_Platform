# Requirements traceability

**Status key.** ✅ verified (implemented, tested, and where applicable confirmed on the live dataset) · 🟡 implemented and tested; the live step runs on the user machine via `scripts/run_final.ps1` and its result appears in `FINAL_REPORT.md` · 📄 design deliverable.

**Evidence.**
- Tests: `docs/test-results.xml` has 164 tests and 0 failures (Python 3.13; SQLite + PostgreSQL 16 + Redis 7). The same suite was run on Python 3.12 in the project directory.
- Live data: `docs/data_audit.json`. Synthetic load: `docs/benchmarks.json`.

| ID | Requirement (assessment PDF) | Implementation | Test(s) | Result | Evidence / limitation |
|---|---|---|---|---|---|
| R1.1 | Highly concurrent scraper | `src/core/http.py`, `pipelines/ingestion.py::run_many`, `pipelines/tasks.py` | `test_http_resilience.py`, `test_pipeline_e2e.py` | ✅ | 2,338 postings/s through the full pipeline (synthetic, 8 workers) |
| R1.2a | arXiv + Papers with Code | `crawlers/research/sources.py` (arXiv API, HF Papers = PwC successor) | `test_research_*` | ✅ | Live: 2,290 papers, all with an arXiv URL, title and authors |
| R1.2b | Correlate papers ↔ GitHub repos | `sources.py` (`githubRepo`, arXiv comment/abstract links) | `test_research_correlates_repo_and_live_stars` | ✅ | Live: 2,290/2,290 have a repository URL (only papers with code are kept) |
| R1.2c | **Current** GitHub stars | `crawlers/research/github.py`, `main.py::run_refresh_stars` | `test_research_*`, `test_refresh_stars_updates_cached_counts`, `test_github_etag_revalidation` | ✅ | Live: 2,290/2,290 re-checked through the authenticated GitHub API on 2026-11-21, 0 repos missing, 0 errors; `github_stars_source=github_api` |
| R1.3 | Scale to 500k by infrastructure only | Celery units, idempotent upserts, checkpoints, Redis limits | `test_storage_dedup.py`, `scripts/benchmark.py` | ✅ | 100k-row upsert at 3,640 rows/s; idempotent re-run inserted 0 (synthetic) |
| R1.4 | ≥1,000 startups / products / papers | `crawlers/startups/*`, `crawlers/products/*`, `crawlers/research/*` | `test_crawlers.py` | ✅ | Live: 2,492 startups · 1,800 products · 2,290 papers; 0 schema-invalid, 0 duplicate keys |
| R2.1 | 5 news + 5 job sources | `crawlers/news/rss.py` (8 feeds), `crawlers/jobs/boards.py` (5 boards) | `test_crawlers.py`, `test_producthunt_*` | ✅/🟡 | Live: 4 news feeds OK (VentureBeat returned 429, so 3 feeds were added); 4 boards OK, Lever slugs corrected. Refreshed by `run_final.ps1` |
| R2.2 | Guaranteed ≤24 h | `core/dates.py::FreshnessPolicy`, `exports/tabs.py` (re-check at export) | `test_freshness_policy`, e2e | ✅ | Live audit: 0 unverified dates in the exported set; publishedAt never equals collectedAt |
| R2.3 | Full text | `core/text.py`, `crawlers/news/rss.py` | `test_news_freshness_fulltext_*` | ✅ | Live: 12 of 13 news items have full text |
| R2.4 | Relative dates / missing meta tags | `core/dates.py` | 9 relative + 6 absolute + HTML tests | ✅ | |
| R2.5 | Heuristic for undated sources | `new_since_last_run`, `crawlers/fresh.py` | `test_news_heuristic_second_run` | ✅ | Exported only with `freshness_basis=new_since_last_run` |
| R2.6 | No duplicate processing across nodes | `core/dedup.py`, DB `UNIQUE` + `ON CONFLICT` | Redis multi-node tests, concurrent pipeline tests | ✅ | 8 simulated nodes × 100 URLs → exactly 100 claims |
| R3.1 | Gemini → Groq → DeepSeek fallback | `extraction/providers/*`, `orchestrator.py` | `test_fallback_*`, `test_real_providers_over_http_fallback_chain` | ✅ / 🟡 | Mocked and HTTP-mocked: verified. Live: `scripts/llm_live_check.py` (forced fallback from an invalid key). Groq/DeepSeek keys are not configured |
| R3.2 | No 413, dense content kept | `extraction/chunking.py`, bisect on 413 | chunking + `test_413_*` + re-split test | ✅ | No 413 payload is ever resent (asserted) |
| R3.3 | 429 backoff + jitter | `core/retry.py`, `orchestrator.py` | `test_429_*`, `test_backoff_full_jitter_bounds` | ✅ | |
| R3.4 | Structured JSON, no hallucination | `validators.py`, `_merge`, `pipelines/enrich.py` | `test_hallucinated_value_rejected_*`, `test_enrich_products_*` | ✅ | Fault injection: 2,000/2,000 extractions succeeded, 0 values without evidence |
| R4.1 | OpenAI variants → OpenAI | `entity_resolution/resolver.py` | `test_canonical_examples_from_assessment` | ✅ | |
| R4.2 | Seed of 50 | `entity_resolution/seed.py` (60) | `test_seed_has_at_least_50_*` | ✅ | |
| R4.3 | Mapping log raw → canonical | `entity_mappings`, `pipelines/maintenance.py` | `test_rebuild_entities_*`, e2e | ✅ | Live: 4,848 rows; 2,343 auto-resolved; 12 for human review |
| R4.x | Never merge different organisations | single-word-brand guard, domain-aware startup key | `test_live_false_merges_never_recur`, `test_same_name_different_companies_*` | ✅ | Live audit found 2 wrong merges out of 1,916 plus 21 YC same-name key collisions; fixed and re-resolved. Labelled set: 68/68, 0 false merges |
| R5.1 | Async operation | `core/http.py`, `core/browser.py` | crawler tests | ✅ | Playwright renderer not exercised in tests |
| R5.2 | Cloudflare/DataDome strategy | `detect_challenge`, policy | challenge / proxy-denial tests | ✅ / 📄 | Detected and reported; no evasion |
| R6 | Architecture ≤3 pages, 4 questions | `architecture.pdf` | – | 📄 | 3 pages, 11 pt body |
| S1–S4 | STARTUP, PRODUCT, RESEARCH_PAPER, JOB schemas | `core/schemas.py` | schema tests | ✅ | Live: 0 schema-invalid records |
| S5 | NEWS schema | `NewsRecord` | e2e | ✅ | Not specified in the PDF; designed analogously |
| D1 | Google Sheet, 6 tabs | `exports/google_sheets.py` (batching, quota retry, read-back `verify`) | `test_sheets_*` | 🟡 | Needs `GOOGLE_SHEET_ID` (a sheet shared with the service account); result in `docs/sheets_export.json` |
| D2 | README, `src/`, `architecture.pdf` | repo | – | ✅ | |
| ENG | Tests, lint, Docker | `tests/`, `ruff.toml`, `Dockerfile`, compose | 164 tests | ✅ / 🟡 | Docker images were not built in the sandbox |

## Defects found and fixed in this audit
1. Empty `.env` values were treated as credentials (Product Hunt 401) → blank now means not configured.
2. 21 YC startups were overwritten because two different companies shared a name (e.g. two "Contour"s) → the startup key now includes the organisation domain.
3. Fuzzy matching merged distinct one-word brands (Conntour→Contour, Datasite→DataSuite) → single-word-brand guard and review queue.
4. Hugging Face crawlers lost their resume cursor after a failed page → the cursor is now reset only when the listing is exhausted.
5. Product Hunt GraphQL `errors` returned with HTTP 200 were treated as success → now reported.
6. `GOOGLE_SERVICE_ACCOUNT_FILE` was missing its `.json` extension in `.env` → fixed.
7. SQLite journal deletion fails on the synced folder from Linux → data recovered with an integrity check; documented.
8. Earlier: robots cache, checkpoint race, "30+ days ago", "Data Scientist" role family.
