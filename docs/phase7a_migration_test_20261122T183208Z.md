# Phase 7A migration test 20261122T183208Z: FAIL (4/16 steps)

Source: `C:\Users\User\OneDrive\Documents\Desktop\Infotact\ai-intelligence-platform\data\platform.db`  
Work folder: `C:\FrontierAtlas-test`

| # | Step | Result | Seconds |
| --- | --- | --- | --- |
| 1 | Pre-flight: work folder outside OneDrive and project; record live database checksum | PASS | 0.483 |
| 2 | Verified backup of the live database (online backup API) | PASS | 0.338 |
| 3 | Working copy identical to the backup; baseline fingerprint of the 7 existing tables | FAIL | 0.148 |
| 4 | Schema check: revision 0001 DDL == existing DDL, and ORM models == existing schema | FAIL | 0.263 |
| 5 | Safety: 'upgrade 0001' on the existing database is refused (tables exist) | FAIL | 0.109 |
| 6 | stamp 0001_baseline on the existing copy | FAIL | 0.248 |
| 7 | upgrade head (0002_history_tables): 12 new empty tables, existing tables unchanged | FAIL | 0.059 |
| 8 | upgrade head again is a no-op (idempotent) | FAIL | 0.054 |
| 9 | Constraint probes on a throw-away copy (idempotency, disappearance, corroboration, versions) | FAIL | 0.204 |
| 10 | downgrade 0001_baseline: history tables removed, existing tables unchanged | FAIL | 0.236 |
| 11 | Safety: 'downgrade base' (would drop production tables) is refused | FAIL | 0.249 |
| 12 | Re-upgrade head: identical history schema, existing tables unchanged | FAIL | 0.015 |
| 13 | Safety: Alembic refuses to run against the live data/platform.db without explicit approval | PASS | 0.055 |
| 14 | Backup restore to a new file: integrity ok, identical to the original | FAIL | 0.48 |
| 15 | Rollback by restore: pre-migration backup restored over a migrated copy | FAIL | 0.487 |
| 16 | Live database unchanged (SHA-256, size, modification time) | PASS | 0.045 |

Live database SHA-256 at start: `c916f9cb81a036537318db3b9223c563ef7e3bcd3618269362017180dc1badff`
Live database SHA-256 at end:   `c916f9cb81a036537318db3b9223c563ef7e3bcd3618269362017180dc1badff`
Backup `C:\FrontierAtlas-test\phase7a-20261122T183208Z\backup_pre_migration.db` SHA-256 `3920c9afcd97394901e97d045e05c78eca13d7409829c9729e6bde54451f050f`

Full details: `phase7a_migration_test_20261122T183208Z.json`
