"""Phase 7B baseline backfill and restore helpers, on temporary databases only (never the live file)."""

from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
import sys
from argparse import Namespace
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


bf = _load("phase7b_backfill")
dr = _load("db_restore")


def migrated_db(tmp_path: Path, n: int = 3) -> Path:
    db = tmp_path / "copy.db"
    c = Config(str(ROOT / "alembic.ini"))
    c.set_main_option("script_location", str(ROOT / "migrations"))
    c.cmd_opts = Namespace(x=[f"db_url=sqlite+aiosqlite:///{db.resolve().as_posix()}"])
    c.skip_logging_config = True
    command.upgrade(c, "head")
    with sqlite3.connect(db) as con:
        for i in range(n):
            con.execute(
                "insert into records(record_type,schema_version,dedup_hash,content_hash,entity_name,source_name,source_url,collected_at,"
                "published_at,date_verified,validation_status,confidence,source_priority,extraction_method,payload,created_at,updated_at) "
                "values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("NEWS" if i % 2 else "STARTUP", "1.0", f"d{i}", f"c{i}", None, "TechCrunch", f"https://x/{i}", "2026-11-21 09:00:00.000000",
                 "2026-11-20 08:00:00.000000" if i % 2 else None, None, "VERIFIED", 1.0, 10, "STRUCTURED_API", f'{{"k": {i}}}',
                 "2026-11-21 09:00:00", "2026-11-21 09:00:00"),
            )
    return db


def test_backfill_labels_every_record_once_and_is_idempotent(tmp_path: Path) -> None:
    db = migrated_db(tmp_path)
    r = bf.run_backfill(db)
    assert r["inserted"] == {"ingestion_batches": 1, "record_observations": 3, "record_versions": 3}
    assert r["totals_in_transaction"]["newly_discovered"] == 0
    with sqlite3.connect(db) as c:
        assert c.execute("select trigger, status from ingestion_batches").fetchall() == [("baseline_backfill", "completed")]
        assert c.execute("select count(*) from record_observations where observation_origin='baseline_backfill'").fetchone()[0] == 3
        assert c.execute("select count(*) from record_versions where version_no=1 and valid_to is null and version_origin='baseline_backfill'").fetchone()[0] == 3
        assert c.execute("select count(*) from record_versions v join records r using(record_type, dedup_hash) where v.payload = r.payload").fetchone()[0] == 3
    again = bf.run_backfill(db)
    assert again["inserted"] == {"ingestion_batches": 0, "record_observations": 0, "record_versions": 0}


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    db = migrated_db(tmp_path)
    r = bf.run_backfill(db, dry_run=True)
    assert r["inserted"]["record_versions"] == 3 and not r["committed"]
    with sqlite3.connect(db) as c:
        assert c.execute("select count(*) from record_versions").fetchone()[0] == 0
        assert c.execute("select count(*) from ingestion_batches").fetchone()[0] == 0


def test_baseline_records_are_never_newly_discovered(tmp_path: Path) -> None:
    db = migrated_db(tmp_path)
    bf.run_backfill(db)
    with sqlite3.connect(db) as c:
        c.execute("insert into ingestion_batches(batch_id,trigger,scheduled_for,started_at,status) values ('live','daily_0730','2026-11-23','2026-11-23','running')")
        ins = "insert into record_observations(batch_id,source_name,record_type,dedup_hash,observed_at,content_hash,observation_origin) values (?,?,?,?,?,?,?)"
        c.execute(ins, ("live", "TechCrunch", "STARTUP", "d0", "2026-11-23", "c0", "live"))  # baseline record seen again
        c.execute(ins, ("live", "TechCrunch", "NEWS", "brand-new", "2026-11-23", "z", "live"))  # never seen before
        assert c.execute(bf.NEW_DISCOVERY_SQL).fetchall() == [("NEWS", "brand-new")]


def test_backfill_refuses_live_database_and_wrong_revision(tmp_path: Path) -> None:
    with pytest.raises(bf.BackfillRefused, match="live database"):
        bf.run_backfill(ROOT / "data" / "platform.db")
    plain = tmp_path / "plain.db"
    sqlite3.connect(plain).close()
    with pytest.raises(bf.BackfillRefused, match="revision"):
        bf.run_backfill(plain)


def test_restore_checks_hash_and_keeps_previous_file(tmp_path: Path) -> None:
    src = tmp_path / "cold.db"
    with sqlite3.connect(src) as c:
        c.execute("create table t(x)")
        c.execute("insert into t values (1)")
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    dst = tmp_path / "target.db"
    dst.write_bytes(b"old")
    r = dr.restore(src, dst, sha)
    assert r["ok"] and r["sha256"] == sha and Path(r["previous_file_kept_as"]).read_bytes() == b"old"
    with pytest.raises(dr.RestoreRefused, match="does not match"):
        dr.restore(src, tmp_path / "other.db", "0" * 64)
    with pytest.raises(dr.RestoreRefused, match="live database"):
        dr.restore(src, ROOT / "data" / "platform.db", sha)
