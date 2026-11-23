# Phase 7B Run A (rehearsal on a copy) 20261122T171614Z: PASS (18/18 steps)

Live database (read only): `C:\Users\User\OneDrive\Documents\Desktop\Infotact\ai-intelligence-platform\data\platform.db`  
Backups: `C:\FrontierAtlas-backups\phase7b-rehearsal-20261122T171614Z`  
Work folder: `C:\FrontierAtlas-test\phase7b-rehearsal-20261122T171614Z`

| # | Step | Result | Seconds |
| --- | --- | --- | --- |
| 1 | Pre-flight: folders outside OneDrive/project, live read-only facts, live not registered with Alembic | PASS | 0.534 |
| 2 | External backups in C:\FrontierAtlas-backups: cold byte-for-byte copy + online backup | PASS | 0.168 |
| 3 | Backup verification: integrity, 7-table fingerprints equal, planned row counts | PASS | 0.406 |
| 4 | Test restore of the cold copy with scripts/db_restore.py (SHA-256 + fingerprints) | PASS | 0.189 |
| 5 | Rehearsal target = copy of the cold backup (stands in for the live file) | PASS | 0.043 |
| 6 | alembic current (none) -> stamp 0001_baseline -> current | PASS | 0.296 |
| 7 | alembic upgrade 0002_history_tables -> 12 empty tables | PASS | 0.199 |
| 8 | Backfill dry run (rolled back): 1 batch, 6,708 + 6,708 rows, 0 newly discovered | PASS | 0.223 |
| 9 | Backfill (one transaction), labelled baseline_backfill | PASS | 0.162 |
| 10 | Backfill re-run writes 0 rows (idempotent) | PASS | 0.062 |
| 11 | Existing 7 tables unchanged: schema, row counts, row checksums, ORM comparison | PASS | 0.23 |
| 12 | History verification: counts, cross-checks with records, foreign keys, integrity | PASS | 0.217 |
| 13 | New-discovery rule: baseline record seen again = not new; unseen record = new (throw-away copy) | PASS | 0.078 |
| 14 | Read-only API on the migrated copy returns the same responses as on the original | PASS | 6.891 |
| 15 | Rollback rehearsal 1: downgrade 0001_baseline + stamp --purge base | PASS | 0.275 |
| 16 | Rollback rehearsal 2: full restore of the cold copy over a migrated copy (byte-identical) | PASS | 0.22 |
| 17 | Measurements: database size and backfill time | PASS | 0.047 |
| 18 | Live database unchanged (SHA-256, size, mtime) and still not registered with Alembic | PASS | 0.022 |

live_start: SHA-256 `67e9335b6b257682357a60476b7d27690dcacff730d7bc12544c80b3801ac16d`, 19185664 bytes

live_end: SHA-256 `67e9335b6b257682357a60476b7d27690dcacff730d7bc12544c80b3801ac16d`, 19185664 bytes

cold: SHA-256 `67e9335b6b257682357a60476b7d27690dcacff730d7bc12544c80b3801ac16d`, 19185664 bytes

online: SHA-256 `f54f5263af4a0aca0309f1522a2b714a4953bb662f53f6f3dad33ded1fb70f4e`, 19185664 bytes

Full details: `phase7b_rehearsal_20261122T171614Z.json`
