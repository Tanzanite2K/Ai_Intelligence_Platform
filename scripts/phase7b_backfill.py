"""Phase 7B baseline backfill: copy every ``records`` row into the history tables, labelled ``baseline_backfill``.

One transaction. ``records`` and the other existing tables are only read. Re-running writes nothing (unique keys +
ON CONFLICT DO NOTHING). The label means "what FrontierAtlas held at the baseline (2026-11-21)"; it does NOT
reconstruct earlier versions or real first-seen dates.

Safety: refuses the project's live data/platform.db unless --allow-live is given (Run B only, not yet approved), and
refuses any database whose Alembic revision is not 0002_history_tables.

Usage:  python scripts/phase7b_backfill.py --db <path to a COPY> [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LIVE_DB = (ROOT / "data" / "platform.db").resolve()
REQUIRED_REVISION = "0002_history_tables"
BASELINE_BATCH_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "frontieratlas/baseline/2026-11-21"))
BASELINE_TRIGGER = "baseline_backfill"
BASELINE_SCHEDULED_FOR = "2026-11-21 00:00:00.000000"
BASELINE_NOTES = ("Baseline snapshot of records as stored on 2026-11-21. Not a reconstruction of earlier versions or "
                  "first-seen dates.")

# A record is newly discovered only if its EARLIEST observation is a live one. Baseline records therefore never are.
NEW_DISCOVERY_SQL = """
SELECT record_type, dedup_hash FROM record_observations
GROUP BY record_type, dedup_hash
HAVING MIN(CASE WHEN observation_origin = 'baseline_backfill' THEN 0 ELSE 1 END) = 1
"""

OBSERVATIONS_SQL = """
INSERT INTO record_observations (batch_id, source_name, record_type, dedup_hash, observed_at, content_hash,
                                 published_at, observation_origin)
SELECT :batch_id, source_name, record_type, dedup_hash, collected_at, content_hash, published_at, 'baseline_backfill'
FROM records WHERE 1 ORDER BY id
ON CONFLICT (batch_id, source_name, record_type, dedup_hash) DO NOTHING
"""

VERSIONS_SQL = """
INSERT INTO record_versions (record_type, dedup_hash, version_no, content_hash, payload, valid_from, valid_to,
                             batch_id, version_origin, changed_fields)
SELECT record_type, dedup_hash, 1, content_hash, payload, collected_at, NULL, :batch_id, 'baseline_backfill', NULL
FROM records WHERE 1 ORDER BY id
ON CONFLICT DO NOTHING
"""


class BackfillRefused(RuntimeError):
    pass


def code_version() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def run_backfill(db: Path, *, dry_run: bool = False, allow_live: bool = False) -> dict[str, Any]:
    db = db.resolve()
    if db == LIVE_DB and not allow_live:
        raise BackfillRefused(f"Refusing to backfill the live database {db}. Run B is not approved; use a copy.")
    if not db.exists():
        raise BackfillRefused(f"database not found: {db}")
    con = sqlite3.connect(db, isolation_level=None)
    try:
        con.execute("PRAGMA foreign_keys = ON")
        rev = con.execute("select version_num from alembic_version").fetchall() if con.execute(
            "select 1 from sqlite_master where type='table' and name='alembic_version'").fetchone() else []
        if [r[0] for r in rev] != [REQUIRED_REVISION]:
            raise BackfillRefused(f"database is at revision {rev or 'none'}, expected {REQUIRED_REVISION}")
        records = con.execute("select count(*) from records").fetchone()[0]
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute(
                "INSERT INTO ingestion_batches (batch_id, trigger, scheduled_for, started_at, finished_at, status, code_version, notes) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (batch_id) DO NOTHING",
                (BASELINE_BATCH_ID, BASELINE_TRIGGER, BASELINE_SCHEDULED_FOR, _now(), None, "running", code_version(), BASELINE_NOTES),
            )
            batch_new = con.execute("select changes()").fetchone()[0]
            con.execute(OBSERVATIONS_SQL, {"batch_id": BASELINE_BATCH_ID})
            obs_new = con.execute("select changes()").fetchone()[0]
            con.execute(VERSIONS_SQL, {"batch_id": BASELINE_BATCH_ID})
            ver_new = con.execute("select changes()").fetchone()[0]
            if batch_new:
                con.execute("UPDATE ingestion_batches SET status='completed', finished_at=? WHERE batch_id=?", (_now(), BASELINE_BATCH_ID))
            fk = con.execute("PRAGMA foreign_key_check").fetchall()
            if fk:
                raise BackfillRefused(f"foreign key violations: {fk[:5]}")
            totals = {
                "record_observations": con.execute("select count(*) from record_observations").fetchone()[0],
                "record_versions": con.execute("select count(*) from record_versions").fetchone()[0],
                "ingestion_batches": con.execute("select count(*) from ingestion_batches").fetchone()[0],
                "newly_discovered": len(con.execute(NEW_DISCOVERY_SQL).fetchall()),
            }
        except Exception:
            con.execute("ROLLBACK")
            raise
        con.execute("ROLLBACK" if dry_run else "COMMIT")
    finally:
        con.close()
    return {"db": str(db), "dry_run": dry_run, "batch_id": BASELINE_BATCH_ID, "records": records,
            "inserted": {"ingestion_batches": batch_new, "record_observations": obs_new, "record_versions": ver_new},
            "totals_in_transaction": totals, "committed": not dry_run, "code_version": code_version()}


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 7B baseline backfill (copies only unless Run B is approved)")
    ap.add_argument("--db", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-live", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    print(json.dumps(run_backfill(Path(a.db), dry_run=a.dry_run, allow_live=a.allow_live), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
