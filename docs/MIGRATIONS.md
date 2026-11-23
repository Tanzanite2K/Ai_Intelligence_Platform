# Database migrations (Alembic) — Phase 7A

Status (2026-11-22): Alembic is set up and tested on copies only. The live `data/platform.db` has **not** been stamped
or upgraded, and history collection is off (`HISTORY_ENABLED=false`).

## Revisions

| Revision | What it does | Downgrade |
| --- | --- | --- |
| `0001_baseline` | Frozen copy of the 7 pre-Phase-7 tables. `upgrade` only builds a new, empty database and refuses if the tables exist; existing databases are marked with `alembic stamp 0001_baseline`. | Always refused (would drop production data) |
| `0002_history_tables` | 12 new, empty history tables with idempotency, partial-unique and CHECK constraints. No existing table is altered. | Drops only the 12 new tables |

## Safety rules built in

- `migrations/env.py` refuses `data/platform.db` unless `-x allow_live=yes` is passed. That flag needs explicit approval (Phase 7B).
- The target is always given explicitly: `alembic -x db_url=sqlite+aiosqlite:///C:/path/copy.db <command>`.
- History tables are not ORM models, so application start-up (`create_all`) can never create them.
- `tests/unit/test_migrations.py` fails if `src/database/models.py` changes without a matching migration.

## Re-running the copy test

Double-click `RUN_MIGRATION_TEST.cmd`. It reads the live database only through the SQLite online-backup API (read-only),
works in `C:\FrontierAtlas-test\phase7a-<stamp>\`, runs the 16 checks and the regression suite, and writes
`docs/phase7a_migration_test_<stamp>.json` / `.md` and `docs/test-results-phase7a-windows.xml`.

## Production procedure (Phase 7B, not yet approved)

1. Stop the API and web servers. 2. `scripts/backup_db.py`, copy the backup off OneDrive, record its SHA-256.
3. `alembic -x db_url=... -x allow_live=yes stamp 0001_baseline`. 4. `... upgrade head`. 5. Verify the 7 existing
tables' fingerprints are unchanged. Rollback: `downgrade 0001_baseline`, or restore the backup.
