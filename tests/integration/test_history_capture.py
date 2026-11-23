"""Phase 7C incremental history capture: every scenario on temporary migrated databases (never the live file)."""

from __future__ import annotations

import asyncio
import importlib.util
import sqlite3
import sys
from argparse import Namespace
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config

from src.core.http import HttpClient
from src.core.schemas import RecordType, StartupRecord
from src.crawlers.base import BaseCrawler, CrawlContext
from src.database.repository import Repository
from src.database.session import Database
from src.history.capture import BatchAlreadyCompleted, HistoryRecorder, HistoryUnavailable, batch_id_for
from src.pipelines.ingestion import IngestionPipeline

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 11, 23, 2, 0, tzinfo=UTC)


def migrate(db: Path) -> None:
    c = Config(str(ROOT / "alembic.ini"))
    c.set_main_option("script_location", str(ROOT / "migrations"))
    c.cmd_opts = Namespace(x=[f"db_url=sqlite+aiosqlite:///{db.resolve().as_posix()}"])
    c.skip_logging_config = True
    command.upgrade(c, "head")


def startup(name: str, desc: str = "d", employees: int | None = None) -> StartupRecord:
    return StartupRecord.model_validate({
        "source": {"name": "Y Combinator", "url": f"https://www.ycombinator.com/companies/{name.lower().replace(' ', '-')}"},
        "collectedAt": T0, "content": {"entityName": name, "description": desc, "data": {"employeeCount": employees}},
        "provenance": {"extractionMethod": "STRUCTURED_API"}})


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[tuple[Path, Repository, HistoryRecorder]]:
    path = tmp_path / "copy.db"
    await asyncio.to_thread(migrate, path)  # Alembic runs its own event loop
    db = Database(f"sqlite+aiosqlite:///{path.as_posix()}")
    yield path, Repository(db), HistoryRecorder(db)
    await db.dispose()


def q(path: Path, sql: str, *a: Any) -> list[tuple]:
    with sqlite3.connect(path) as c:
        return c.execute(sql, a).fetchall()


async def store(repo: Repository, rec: HistoryRecorder, batch: str, recs: list[StartupRecord], at: datetime):  # type: ignore[no-untyped-def]
    await repo.bulk_upsert(recs, priority=10)
    return await rec.record(batch, recs, observed_at=at)


async def test_new_unchanged_and_modified_records(env) -> None:  # type: ignore[no-untyped-def]
    path, repo, rec = env
    b1, _ = await rec.open_batch("daily_0730", T0)
    s1 = await store(repo, rec, b1, [startup("Acme AI", "v1", 3)], T0)
    assert (s1.observed, s1.new, s1.modified, s1.unchanged) == (1, 1, 0, 0)
    await rec.close_batch(b1, "completed")
    b2, _ = await rec.open_batch("daily_0730", T0 + timedelta(days=1))
    s2 = await store(repo, rec, b2, [startup("Acme AI", "v1", 3)], T0 + timedelta(days=1))
    assert (s2.observed, s2.new, s2.modified, s2.unchanged) == (1, 0, 0, 1)
    b3, _ = await rec.open_batch("daily_0730", T0 + timedelta(days=2))
    s3 = await store(repo, rec, b3, [startup("Acme AI", "v2 - now with agents", 5)], T0 + timedelta(days=2))
    assert (s3.modified, s3.unchanged) == (1, 0) and "content.description" in s3.changed_fields
    vers = q(path, "select version_no, valid_to is null, changed_fields from record_versions order by version_no")
    assert [(v[0], v[1]) for v in vers] == [(1, 0), (2, 1)]  # v1 closed, v2 open; no version for the unchanged day
    assert "content.data" in vers[1][2]
    assert q(path, "select count(*) from record_observations")[0][0] == 3
    assert q(path, "select metric, value from metric_snapshots order by observed_at") == [("employeeCount", 3.0), ("employeeCount", 3.0), ("employeeCount", 5.0)]
    first, last = q(path, "select min(observed_at), max(observed_at) from record_observations")[0]
    assert first.startswith("2026-11-23") and last.startswith("2026-11-25")


async def test_retry_of_the_same_batch_writes_nothing_twice(env) -> None:  # type: ignore[no-untyped-def]
    path, repo, rec = env
    b, resumed = await rec.open_batch("manual", T0)
    assert not resumed
    recs = [startup(f"Co {i}", "x") for i in range(5)]
    await store(repo, rec, b, recs, T0)
    again = await store(repo, rec, b, recs, T0 + timedelta(minutes=5))  # interrupted run replays the same items
    assert (again.observed, again.already_recorded, again.new, again.modified) == (0, 5, 0, 0)
    assert q(path, "select count(*) from record_observations")[0][0] == 5
    assert q(path, "select count(*) from record_versions")[0][0] == 5
    assert q(path, "select count(*) from presence_events where event='appeared'")[0][0] == 5


async def test_batch_identity_resume_and_completed_slot_protection(env) -> None:  # type: ignore[no-untyped-def]
    _, _, rec = env
    b1, r1 = await rec.open_batch("daily_0730", T0)
    b2, r2 = await rec.open_batch("daily_0730", T0)  # crashed run restarted for the same slot
    assert b1 == b2 == batch_id_for("daily_0730", T0) and (r1, r2) == (False, True)
    await rec.close_batch(b1, "completed")
    with pytest.raises(BatchAlreadyCompleted):
        await rec.open_batch("daily_0730", T0)
    b3, _ = await rec.open_batch("daily_0730", T0 + timedelta(days=1))
    assert b3 != b1


async def test_baseline_records_are_not_new(env) -> None:  # type: ignore[no-untyped-def]
    path, repo, rec = env
    await repo.bulk_upsert([startup("Old Co", "same")], priority=10)
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("phase7b_backfill", ROOT / "scripts" / "phase7b_backfill.py")
    bf = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(bf)  # type: ignore[union-attr]
    bf.run_backfill(path)  # baseline_backfill on this temporary copy
    b, _ = await rec.open_batch("daily_0730", T0)
    s = await store(repo, rec, b, [startup("Old Co", "same"), startup("Brand New Co", "fresh")], T0)
    assert (s.new, s.unchanged) == (1, 1)
    assert q(path, "select count(*) from record_versions")[0][0] == 2  # baseline v1 kept, no duplicate for unchanged Old Co
    new = q(path, bf.NEW_DISCOVERY_SQL)
    assert len(new) == 1 and q(path, "select count(*) from presence_events where event='appeared'")[0][0] == 1


async def test_disappearance_only_after_two_complete_scans(env) -> None:  # type: ignore[no-untyped-def]
    path, repo, rec = env
    names = [f"Co {i}" for i in range(10)]
    b1, _ = await rec.open_batch("daily_0730", T0)
    await store(repo, rec, b1, [startup(n) for n in names], T0)
    assert (await rec.record_scan(b1, "yc_startups", complete=True, items_listed=10))["complete"]
    assert (await rec.detect_disappearances(b1, "yc_startups"))["disappeared"] == 0  # nothing to compare with yet
    # incomplete scan (e.g. rate limited) with 9 of 10: no disappearance
    b2, _ = await rec.open_batch("daily_0730", T0 + timedelta(days=1))
    await store(repo, rec, b2, [startup(n) for n in names[:9]], T0 + timedelta(days=1))
    await rec.record_scan(b2, "yc_startups", complete=False, items_listed=9, reason="rate limited")
    assert (await rec.detect_disappearances(b2, "yc_startups"))["disappeared"] == 0
    # implausibly small "complete" scan: downgraded to incomplete
    b3, _ = await rec.open_batch("daily_0730", T0 + timedelta(days=2))
    await store(repo, rec, b3, [startup(n) for n in names[:3]], T0 + timedelta(days=2))
    sc = await rec.record_scan(b3, "yc_startups", complete=True, items_listed=3)
    assert not sc["complete"] and "80%" in sc["reason"]
    assert (await rec.detect_disappearances(b3, "yc_startups"))["disappeared"] == 0
    # complete scan with 9 of 10: exactly one disappearance, linked to the scan
    b4, _ = await rec.open_batch("daily_0730", T0 + timedelta(days=3))
    await store(repo, rec, b4, [startup(n) for n in names[:9]], T0 + timedelta(days=3))
    await rec.record_scan(b4, "yc_startups", complete=True, items_listed=9)
    assert (await rec.detect_disappearances(b4, "yc_startups"))["disappeared"] == 1
    assert q(path, "select count(*) from presence_events where event='disappeared' and scan_id is not null")[0][0] == 1
    # it comes back: reappeared
    b5, _ = await rec.open_batch("daily_0730", T0 + timedelta(days=4))
    s5 = await store(repo, rec, b5, [startup(n) for n in names], T0 + timedelta(days=4))
    assert s5.reappeared == 1


async def test_streams_never_produce_disappearances(env) -> None:  # type: ignore[no-untyped-def]
    _, _, rec = env
    b, _ = await rec.open_batch("manual", T0)
    assert not (await rec.record_scan(b, "techcrunch_ai", complete=True, items_listed=5))["recorded"]
    assert (await rec.detect_disappearances(b, "greenhouse"))["disappeared"] == 0


async def test_unmigrated_database_is_refused(tmp_path: Path) -> None:
    db = Database(f"sqlite+aiosqlite:///{(tmp_path / 'plain.db').as_posix()}")
    await db.create_all()
    with pytest.raises(HistoryUnavailable):
        await HistoryRecorder(db).open_batch("manual", T0)
    await db.dispose()


class FakeYC(BaseCrawler):
    name, source_name, vertical = "yc_startups", "Y Combinator", RecordType.STARTUP

    def __init__(self, recs: list[StartupRecord]) -> None:
        super().__init__()
        self.recs = recs

    async def crawl(self, ctx: CrawlContext) -> AsyncIterator[StartupRecord]:  # type: ignore[override]
        for r in self.recs:
            self.stats.fetched += 1
            self.stats.emitted += 1
            yield r


async def test_pipeline_writes_history_only_with_a_batch(env, settings) -> None:  # type: ignore[no-untyped-def]
    path, repo, rec = env
    async with HttpClient(settings) as http:
        plain = IngestionPipeline(repo, settings=settings, http=http, clock=lambda: T0)
        await plain.run_crawler(FakeYC([startup("Quiet Co")]), priority=10)
        assert q(path, "select count(*) from record_observations")[0][0] == 0  # history off: nothing written
        b, _ = await rec.open_batch("manual", T0)
        pipe = IngestionPipeline(repo, settings=settings, http=http, clock=lambda: T0, history=rec, history_batch=b)
        summary = await pipe.run_crawler(FakeYC([startup("Quiet Co"), startup("Loud Co")]), priority=10)
    assert summary.extra["history"]["observed"] == 2 and summary.extra["history_scan"]["complete"]
    assert q(path, "select count(*) from record_observations where batch_id=?", b)[0][0] == 2


async def test_only_completed_batches_count_towards_the_30_day_rule(env) -> None:  # type: ignore[no-untyped-def]
    from src.api.v1.history import coverage

    path, repo, rec = env
    b1, _ = await rec.open_batch("daily_0730", T0, now=T0)
    await rec.close_batch(b1, "completed")
    b2, _ = await rec.open_batch("daily_0730", T0 + timedelta(days=1), now=T0 + timedelta(days=1))
    await rec.close_batch(b2, "partial")  # a partial day does not count as reliable collection
    async with repo.db.sessionmaker() as s:
        cov = (await coverage(s))["data"]
    assert cov["live_batches"] == 2 and cov["live_collection_days"] == 1 and not cov["emerging_labels_enabled"]
