"""Phase 7A: Alembic baseline and history-table migrations (temporary databases only; never the live file)."""

from __future__ import annotations

import asyncio
import sqlite3
from argparse import Namespace
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from src.config.settings import Settings
from src.database.session import Database

ROOT = Path(__file__).resolve().parents[2]
BASELINE = {"canonical_entities", "crawl_checkpoints", "dead_letters", "entity_mappings", "records", "seen_items", "source_runs"}
HISTORY = {
    "catalogue_scans", "claims", "entities", "entity_resolution_events", "evidence_links", "ingestion_batches",
    "metric_snapshots", "presence_events", "record_observations", "record_versions", "source_groups", "source_reliability_daily",
}


def cfg(db: Path) -> Config:
    c = Config(str(ROOT / "alembic.ini"))
    c.set_main_option("script_location", str(ROOT / "migrations"))
    c.cmd_opts = Namespace(x=[f"db_url=sqlite+aiosqlite:///{db.resolve().as_posix()}"])
    c.skip_logging_config = True
    return c


def tables(db: Path) -> set[str]:
    with sqlite3.connect(db) as c:
        return {r[0] for r in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")}


def ddl(db: Path, names: set[str]) -> dict[str, list[str]]:
    with sqlite3.connect(db) as c:
        return {t: [" ".join(s.split()) for (s,) in c.execute("select sql from sqlite_master where tbl_name=? and sql is not null order by type desc, name", (t,))] for t in sorted(names)}


def version(db: Path) -> str:
    with sqlite3.connect(db) as c:
        return c.execute("select version_num from alembic_version").fetchone()[0]


def create_all_db(path: Path) -> None:
    async def go() -> None:
        db = Database(f"sqlite+aiosqlite:///{path.as_posix()}")
        await db.create_all()
        await db.dispose()

    asyncio.run(go())


def test_upgrade_downgrade_reupgrade_on_fresh_database(tmp_path: Path) -> None:
    db = tmp_path / "fresh.db"
    command.upgrade(cfg(db), "head")
    assert tables(db) == BASELINE | HISTORY | {"alembic_version"}
    assert version(db) == "0002_history_tables"
    head = ddl(db, HISTORY)
    command.downgrade(cfg(db), "0001_baseline")
    assert tables(db) == BASELINE | {"alembic_version"}
    command.upgrade(cfg(db), "head")
    assert ddl(db, HISTORY) == head


def test_baseline_revision_matches_the_orm_schema(tmp_path: Path) -> None:
    """Fails if src/database/models.py changes without a migration: revision 0001 is a frozen copy of today's schema."""
    via_migration, via_models = tmp_path / "m.db", tmp_path / "o.db"
    command.upgrade(cfg(via_migration), "0001_baseline")
    create_all_db(via_models)
    assert tables(via_models) == BASELINE
    assert ddl(via_migration, BASELINE) == ddl(via_models, BASELINE)


def test_existing_database_is_stamped_not_upgraded(tmp_path: Path) -> None:
    db = tmp_path / "existing.db"
    create_all_db(db)
    with sqlite3.connect(db) as c:
        c.execute("insert into source_runs(source,vertical,started_at,status,fetched,stored,duplicates,rejected) values ('s','news','2026-11-21',  'OK',1,1,0,0)")
    with pytest.raises(RuntimeError, match="stamp 0001_baseline"):
        command.upgrade(cfg(db), "0001_baseline")
    command.stamp(cfg(db), "0001_baseline")
    command.upgrade(cfg(db), "head")
    assert HISTORY <= tables(db)
    with sqlite3.connect(db) as c:
        assert c.execute("select count(*) from source_runs").fetchone()[0] == 1


def test_downgrade_below_baseline_is_refused(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    command.upgrade(cfg(db), "head")
    with pytest.raises(RuntimeError, match="cannot be downgraded"):
        command.downgrade(cfg(db), "base")
    assert BASELINE <= tables(db)


def test_live_database_is_refused() -> None:
    live = ROOT / "data" / "platform.db"
    with pytest.raises(Exception) as e:
        command.current(cfg(live))
    assert type(e.value).__name__ == "LiveDatabaseRefused"


def test_retry_cannot_duplicate_an_observation(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    command.upgrade(cfg(db), "head")
    with sqlite3.connect(db) as c:
        c.execute("insert into ingestion_batches(batch_id,trigger,scheduled_for,started_at,status) values ('b','daily_0730','2026-11-23','2026-11-23','running')")
        row = ("b", "TechCrunch", "NEWS", "h", "2026-11-23", "c", "live")
        sql = "insert into record_observations(batch_id,source_name,record_type,dedup_hash,observed_at,content_hash,observation_origin) values (?,?,?,?,?,?,?)"
        c.execute(sql, row)
        c.execute(sql + " on conflict do nothing", row)
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(sql, row)
        assert c.execute("select count(*) from record_observations").fetchone()[0] == 1


def test_disappearance_requires_a_catalogue_scan(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    command.upgrade(cfg(db), "head")
    with sqlite3.connect(db) as c:
        c.execute("insert into ingestion_batches(batch_id,trigger,scheduled_for,started_at,status) values ('b','daily_0730','2026-11-23','2026-11-23','running')")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            c.execute("insert into presence_events(record_type,dedup_hash,source_name,event,batch_id,observed_at) values ('JOB','h','ashby','disappeared','b','2026-11-23')")


def test_history_is_disabled_by_default() -> None:
    assert Settings(_env_file=None).history_enabled is False
