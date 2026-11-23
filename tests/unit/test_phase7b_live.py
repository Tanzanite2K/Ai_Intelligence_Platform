"""Phase 7B Run B code path, exercised on temporary copies only (never the live file)."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from src.database.session import Database

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):  # type: ignore[no-untyped-def]
    if name in sys.modules:
        return sys.modules[name]
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


live = _load("phase7b_live")
p7a = _load("phase7a_migration_test")
bf = _load("phase7b_backfill")


def small_db(tmp_path: Path) -> Path:
    d = tmp_path / "proj" / "data"
    d.mkdir(parents=True)
    db = d / "platform.db"

    async def go() -> None:
        x = Database(f"sqlite+aiosqlite:///{db.as_posix()}")
        await x.create_all()
        await x.dispose()

    asyncio.run(go())
    with sqlite3.connect(db) as c:
        for i in range(4):
            c.execute(
                "insert into records(record_type,schema_version,dedup_hash,content_hash,source_name,source_url,collected_at,validation_status,"
                "confidence,source_priority,extraction_method,payload) values (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("JOB" if i % 2 else "NEWS", "1.0", f"h{i}", f"c{i}", "Ashby", f"https://x/{i}", "2026-11-21 09:00:00.000000", "VERIFIED", 1.0, 10,
                 "STRUCTURED_API", "{}"),
            )
    return db


def fake_rehearsal(tmp_path: Path, db: Path) -> Path:
    rep = {"result": "PASS", "passed": 18, "steps_run": 18,
           "steps": [{"step": 1, "details": {"backfill_code_version": bf.code_version()}}, {"step": 3, "details": {"fingerprints": p7a.fingerprint(db)}}]}
    p = tmp_path / "rehearsal.json"
    p.write_text(json.dumps(rep), encoding="utf-8")
    return p


def run(tmp_path: Path, db: Path, **kw):  # type: ignore[no-untyped-def]
    return live.run_live(target=db, is_live=False, backup_root=tmp_path / "backups", docs_dir=tmp_path / "docs",
                         expect_sha256=kw.pop("sha", p7a.sha256_file(db)), rehearsal_report=fake_rehearsal(tmp_path, db), check_ports=False, **kw)


def report(tmp_path: Path) -> dict:
    return json.loads(next((tmp_path / "docs").glob("*.json")).read_text(encoding="utf-8"))


def test_wrong_checksum_stops_before_any_write(tmp_path: Path) -> None:
    db = small_db(tmp_path)
    before = p7a.sha256_file(db)
    assert run(tmp_path, db, sha="0" * 64) == 1
    assert p7a.sha256_file(db) == before
    r = report(tmp_path)
    assert r["live_writes"] == [] and "not modified" in r["state"]


def test_failure_after_a_write_preserves_state_and_restores_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = small_db(tmp_path)

    def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("injected failure during backfill")

    monkeypatch.setattr(live.LiveWriter, "backfill", boom)
    assert run(tmp_path, db) == 3
    assert p7a.revision(db) == "0002_history_tables"  # left exactly as the failure left it
    r = report(tmp_path)
    assert [w["operation"] for w in r["live_writes"]] == ["alembic stamp 0001_baseline", "alembic upgrade 0002_history_tables"]
    assert "Nothing was restored" in r["state"] and "db_restore.py" in r["restore_command_requires_your_approval"]
    assert list((tmp_path / "backups").glob("phase7b-live-*/platform.pre7b.cold.db"))  # the backup it would restore from


def test_writer_refuses_the_real_live_file_without_the_live_flag() -> None:
    with pytest.raises(bf.BackfillRefused):
        live.LiveWriter(ROOT / "data" / "platform.db", is_live=False, log=[]).backfill(dry_run=True)


def test_cli_refuses_without_confirmation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "argv", ["phase7b_live.py", "--rehearsal-report", str(tmp_path / "x.json")])
    assert live.main() == 2
