"""`python -m src.main collect` end-to-end on a temporary migrated database with a fake crawler (no network)."""

from __future__ import annotations

import asyncio
import sqlite3
from argparse import Namespace
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from src import main as M
from src.config.settings import Settings
from src.crawlers.registry import CrawlerSpec
from src.history.capture import BatchAlreadyCompleted
from tests.integration.test_history_capture import FakeYC, startup

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "copy.db"
    c = Config(str(ROOT / "alembic.ini"))
    c.set_main_option("script_location", str(ROOT / "migrations"))
    c.cmd_opts = Namespace(x=[f"db_url=sqlite+aiosqlite:///{path.as_posix()}"])
    c.skip_logging_config = True
    command.upgrade(c, "head")
    s = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{path.as_posix()}", history_enabled=True)
    monkeypatch.setattr(M, "get_settings", lambda: s)
    monkeypatch.setattr("src.pipelines.runtime.get_settings", lambda: s)
    recs = [startup("Alpha AI"), startup("Beta AI")]
    monkeypatch.setattr(M, "specs_for", lambda v, n: [CrawlerSpec("yc_startups", "startups", lambda: FakeYC(recs), priority=10)])
    return path


def test_collect_records_one_batch_and_refuses_a_completed_slot(temp_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = asyncio.run(M.run_collect("daily_0730", None, ["yc_startups"], use_llm=False, concurrency=1))
    assert out["status"] == "completed" and not out["resumed"]
    src = out["sources"][0]
    assert src["history"]["observed"] == 2 and src["history"]["new"] == 2 and src["history_scan"]["complete"]
    with sqlite3.connect(temp_db) as c:
        assert c.execute("select trigger, status from ingestion_batches").fetchall() == [("daily_0730", "completed")]
        assert c.execute("select count(*) from record_observations").fetchone()[0] == 2
        assert c.execute("select count(*) from catalogue_scans where complete").fetchone()[0] == 1
    with pytest.raises(BatchAlreadyCompleted):  # same slot again: refused, nothing written twice
        asyncio.run(M.run_collect("daily_0730", None, ["yc_startups"], use_llm=False, concurrency=1))
