# Phase 7B simulation on a copy 20261122T172605Z: PASS (13/13 steps)

Target: `C:\FrontierAtlas-test\phase7b-live-simulation-20261122T172605Z\data\platform.db`  
Backups: `C:\FrontierAtlas-test\phase7b-live-simulation-20261122T172605Z\backups\phase7b-live-20261122T172605Z` and in data\

| # | Step | Result | Seconds |
| --- | --- | --- | --- |
| 1 | Gate: Run A report PASS and the same backfill code version | PASS | 0.005 |
| 2 | No other writer: API/web ports closed, no journal files, exclusive-lock probe | PASS | 0.082 |
| 3 | Identity: path, SHA-256, not registered, 7 tables, fingerprints equal to Run A | PASS | 0.171 |
| 4 | Backups: cold byte-for-byte + online, outside OneDrive and in data\ | PASS | 0.183 |
| 5 | Backup verification: integrity, fingerprints, test restore (SHA-256) | PASS | 0.734 |
| 6 | alembic stamp 0001_baseline | PASS | 1.213 |
| 7 | alembic upgrade 0002_history_tables (12 empty tables) | PASS | 0.215 |
| 8 | Backfill dry run (rolled back) | PASS | 0.158 |
| 9 | Backfill: 6,708 records labelled baseline_backfill (one transaction) | PASS | 0.14 |
| 10 | Existing 7 tables: same schemas, row counts and row checksums | PASS | 0.25 |
| 11 | History tables, 0 newly discovered, foreign keys, integrity | PASS | 0.181 |
| 12 | Read-only API on the migrated database answers as before (before restart) | PASS | 6.162 |
| 13 | Final state recorded | PASS | 0.215 |

Writes to the target:
- 2026-11-22T17:26:07.788611+00:00 alembic stamp 0001_baseline
- 2026-11-22T17:26:08.113034+00:00 alembic upgrade 0002_history_tables
- 2026-11-22T17:26:08.328333+00:00 baseline backfill (dry run, rolled back)
- 2026-11-22T17:26:08.486563+00:00 baseline backfill (commit)
