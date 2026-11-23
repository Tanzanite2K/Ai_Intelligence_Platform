# Phase 7A migration test 20261122T184526Z: PASS (16/16 steps)

Source: `C:\FrontierAtlas-backups\phase7b-live-20261122T172812Z\platform.pre7b.cold.db`  
Work folder: `C:\FrontierAtlas-test`

| # | Step | Result | Seconds |
| --- | --- | --- | --- |
| 1 | Pre-flight: work folder outside OneDrive and project; record live database checksum | PASS | 0.619 |
| 2 | Verified backup of the live database (online backup API) | PASS | 0.198 |
| 3 | Working copy identical to the backup; baseline fingerprint of the 7 existing tables | PASS | 0.122 |
| 4 | Schema check: revision 0001 DDL == existing DDL, and ORM models == existing schema | PASS | 0.29 |
| 5 | Safety: 'upgrade 0001' on the existing database is refused (tables exist) | PASS | 0.122 |
| 6 | stamp 0001_baseline on the existing copy | PASS | 0.237 |
| 7 | upgrade head (0002_history_tables): 12 new empty tables, existing tables unchanged | PASS | 0.356 |
| 8 | upgrade head again is a no-op (idempotent) | PASS | 0.311 |
| 9 | Constraint probes on a throw-away copy (idempotency, disappearance, corroboration, versions) | PASS | 0.252 |
| 10 | downgrade 0001_baseline: history tables removed, existing tables unchanged | PASS | 0.269 |
| 11 | Safety: 'downgrade base' (would drop production tables) is refused | PASS | 0.288 |
| 12 | Re-upgrade head: identical history schema, existing tables unchanged | PASS | 0.573 |
| 13 | Safety: Alembic refuses to run against the live data/platform.db without explicit approval | PASS | 0.001 |
| 14 | Backup restore to a new file: integrity ok, identical to the original | PASS | 0.572 |
| 15 | Rollback by restore: pre-migration backup restored over a migrated copy | PASS | 0.686 |
| 16 | Live database unchanged (SHA-256, size, modification time) | PASS | 0.025 |

Live database SHA-256 at start: `67e9335b6b257682357a60476b7d27690dcacff730d7bc12544c80b3801ac16d`
Live database SHA-256 at end:   `67e9335b6b257682357a60476b7d27690dcacff730d7bc12544c80b3801ac16d`
Backup `C:\FrontierAtlas-test\phase7a-20261122T184526Z\backup_pre_migration.db` SHA-256 `f54f5263af4a0aca0309f1522a2b714a4953bb662f53f6f3dad33ded1fb70f4e`

Full details: `phase7a_migration_test_20261122T184526Z.json`
