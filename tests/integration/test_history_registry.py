"""Phase 7D persistence (entity registry, resolution decisions, claims, evidence links) on a synthetic migrated copy."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from src.config.settings import Settings
from src.database.session import Database
from src.history.registry import entity_id, sync_registry
from src.main import main
from tests.integration.test_api_history import build


def q(path: Path, sql: str) -> list[tuple]:
    with sqlite3.connect(path) as c:
        return c.execute(sql).fetchall()


def test_registry_claims_and_evidence_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "copy.db"
    build(path)

    async def run() -> tuple[dict, dict]:
        db = Database(f"sqlite+aiosqlite:///{path.as_posix()}")
        a = await sync_registry(db)
        b = await sync_registry(db)
        await db.dispose()
        return a, b

    first, second = asyncio.run(run())
    assert first["entities_created"] == 2 and first["decisions_recorded"] >= 9  # Acme AI + OpenAI (news tag mapping)
    assert second.get("entities_created", 0) == 0 and second.get("decisions_recorded", 0) == 0 and second["evidence_links_created"] == 0
    acme = entity_id("Acme AI")
    assert q(path, f"select origin, first_observed_batch is not null from entities where entity_id='{acme}'") == [("discovered", 1)]
    tiers = dict(q(path, f"select claim_kind || ':' || object_key, support_tier from claims where subject_entity_id='{acme}'"))
    assert tiers["website:acme.ai"] == "conflicting" and tiers["hiring:Engineering"] == "single_source"
    assert any(v == "corroborated" for k, v in tiers.items() if k.startswith("news_coverage"))
    assert q(path, "select count(*) from claims c join entities e on e.entity_id=c.subject_entity_id where e.canonical_name='OpenAI'") == [(1,)]  # its own news story only
    assert q(path, "pragma foreign_key_check") == [] and q(path, "select count(*) from evidence_links where evidence_kind='syndicated'") == [(1,)]


def test_cli_refuses_when_history_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("src.main.get_settings", lambda: Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{tmp_path}/x.db", history_enabled=False))
    assert main(["sync-evidence"]) == 4 and "Refused" in capsys.readouterr().out
    assert main(["collect", "--vertical", "news"]) == 4
