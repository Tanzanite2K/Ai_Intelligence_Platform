"""Phase 7E/7D/7F history, evidence and change APIs against a SYNTHETIC migrated database opened read-only."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sqlite3
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from src.api.v1.entity_ids import encode_entity_id
from src.config import settings as cfg
from src.database.session import Database
from src.history.capture import HistoryRecorder

ROOT = Path(__file__).resolve().parents[2]
LIVE = datetime(2026, 11, 23, 2, 0, tzinfo=UTC)


def payload(rt: str, content: dict, evidence: dict | None = None) -> str:
    return json.dumps({"schemaVersion": "1.0", "recordType": rt, "source": {}, "content": content, "provenance": {"evidence": evidence or {}}})


RECORDS = [  # id, type, dedup, source, url, published, content, evidence
    (1, "STARTUP", "s1", "Y Combinator", "https://yc/acme", None, {"entityName": "Acme AI", "website": "https://acme.ai", "data": {"employeeCount": 4}}, {"batch": "Summer 2026"}),
    (2, "STARTUP", "s2", "Hugging Face Hub", "https://hf/acme", None, {"entityName": "Acme AI", "website": "https://acme-labs.com", "data": {}}, {}),
    (3, "PRODUCT", "p1", "Hugging Face Hub", "https://hf/acme/model", None, {"productName": "acme-7b", "startupName": "Acme AI"}, {}),
    (4, "JOB", "j1", "Ashby", "https://ashby/acme/1", "2026-11-20 10:00:00.000000", {"title": "ML Engineer", "company": "Acme AI", "role_family": "Engineering"}, {}),
    (5, "JOB", "j2", "Himalayas", "https://himalayas/acme/1", "2026-11-20 11:00:00.000000", {"title": "ML Engineer", "company": "Acme AI", "role_family": "Engineering"}, {}),
    (6, "NEWS", "n1", "TechCrunch", "https://tc/acme", "2026-11-20 09:00:00.000000", {"title": "Acme AI raises $20M seed round for agents", "organizations": ["Acme AI", "OpenAI"]}, {}),
    (7, "NEWS", "n2", "The Verge", "https://verge/acme", "2026-11-20 12:00:00.000000", {"title": "Acme AI raises $20M seed round for its agents", "organizations": ["Acme AI"]}, {}),
    (8, "RESEARCH_PAPER", "r1", "Hugging Face Papers", "https://hf/papers/1", "2026-11-10 00:00:00.000000", {"title": "A paper", "github_stars": 100, "github_stars_checked_at": "2026-11-21T10:00:00Z"}, {}),
]


def build(path: Path) -> None:
    c = Config(str(ROOT / "alembic.ini"))
    c.set_main_option("script_location", str(ROOT / "migrations"))
    c.cmd_opts = Namespace(x=[f"db_url=sqlite+aiosqlite:///{path.as_posix()}"])
    c.skip_logging_config = True
    command.upgrade(c, "head")
    with sqlite3.connect(path) as con:
        for i, rt, dh, src, url, pub, content, ev in RECORDS:
            con.execute("insert into records(id,record_type,schema_version,dedup_hash,content_hash,entity_name,source_name,source_url,collected_at,published_at,"
                        "validation_status,confidence,source_priority,extraction_method,payload) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (i, rt, "1.0", dh, f"c{i}", None, src, url, "2026-11-21 09:00:00.000000", pub, "VERIFIED", 1.0, 10, "STRUCTURED_API", payload(rt, content, ev)))
            for org in ([content.get("entityName") or content.get("startupName") or content.get("company")] if rt != "NEWS" else content["organizations"]):
                if org:
                    con.execute("insert into entity_mappings(original_name,canonical_name,match_method,confidence,needs_review,entity_kind,source_url,resolved_at) "
                                "values (?,?,?,?,?,?,?,?)", (org, org, "EXACT", 1.0, 0, "ORGANIZATION", url, "2026-11-21 09:00:00"))
        con.execute("insert into source_runs(source,vertical,started_at,finished_at,status,fetched,stored,duplicates,rejected) values "
                    "('yc_startups','startups','2026-11-21 09:00:00','2026-11-21 09:01:00','OK',10,2,0,0),('venturebeat_ai','news','2026-11-21 09:00:00',NULL,'FAILED',0,0,0,0)")
    spec = importlib.util.spec_from_file_location("phase7b_backfill", ROOT / "scripts" / "phase7b_backfill.py")
    bf = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(bf)  # type: ignore[union-attr]
    bf.run_backfill(path)
    # one live batch: Acme's YC profile changed (team size 4 -> 9), the paper's stars were re-sampled, and a new job appeared
    with sqlite3.connect(path) as con:
        con.execute("update records set content_hash='c1b', payload=? where id=1",
                    (payload("STARTUP", {"entityName": "Acme AI", "website": "https://acme.ai", "data": {"employeeCount": 9}}, {"batch": "Summer 2026"}),))
        con.execute("insert into records(id,record_type,schema_version,dedup_hash,content_hash,source_name,source_url,collected_at,published_at,validation_status,"
                    "confidence,source_priority,extraction_method,payload) values (9,'JOB','1.0','j3','c9','Ashby','https://ashby/acme/2','2026-11-23 02:00:00.000000',"
                    "'2026-11-22 08:00:00.000000','VERIFIED',1.0,10,'STRUCTURED_API',?)",
                    (payload("JOB", {"title": "Research Scientist", "company": "Acme AI", "role_family": "Research"}),))
        con.execute("insert into entity_mappings(original_name,canonical_name,match_method,confidence,needs_review,entity_kind,source_url,resolved_at) "
                    "values ('Acme AI','Acme AI','EXACT',1.0,0,'ORGANIZATION','https://ashby/acme/2','2026-11-23 02:00:00')")

    async def live() -> None:
        db = Database(f"sqlite+aiosqlite:///{path.as_posix()}")
        rec = HistoryRecorder(db)
        b, _ = await rec.open_batch("daily_0730", LIVE)
        rows = [{"source_name": "Y Combinator", "record_type": "STARTUP", "dedup_hash": "s1", "content_hash": "c1b", "published_at": None},
                {"source_name": "Hugging Face Papers", "record_type": "RESEARCH_PAPER", "dedup_hash": "r1", "content_hash": "c8", "published_at": None},
                {"source_name": "Ashby", "record_type": "JOB", "dedup_hash": "j3", "content_hash": "c9", "published_at": None}]
        await rec.record_rows(b, rows, observed_at=LIVE)
        await rec.close_batch(b, "completed", now=LIVE)
        await db.dispose()

    asyncio.run(live())


@pytest.fixture
def client(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    path = tmp_path / "fixture.db"
    build(path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    monkeypatch.setenv("API_READ_ONLY", "true")
    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "0")
    cfg.get_settings.cache_clear()
    from src.api.app import app

    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with TestClient(app) as c:
        yield c
    cfg.get_settings.cache_clear()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before  # read-only API: the file never changed


def test_record_history_versions_and_first_seen(client) -> None:  # type: ignore[no-untyped-def]
    h = client.get("/api/v1/records/1/history").json()["data"]
    assert [v["version"] for v in h["versions"]] == [1, 2] and h["versions"][0]["origin"] == "baseline_backfill"
    assert h["versions"][1]["changed_fields"] == ["content.data"] and h["versions"][1]["current"]
    assert h["first_seen"]["bound"] == "on_or_before" and h["in_baseline"] and not h["newly_discovered"]
    new = client.get("/api/v1/records/9/history").json()["data"]
    assert new["newly_discovered"] and new["first_seen"]["bound"] == "exact" and new["presence"][0]["event"] == "appeared"
    assert client.get("/api/v1/records/999/history").status_code == 404


def test_version_compare_and_metrics(client) -> None:  # type: ignore[no-untyped-def]
    d = client.get("/api/v1/records/1/history/compare").json()["data"]
    assert (d["from"]["version"], d["to"]["version"]) == (1, 2)
    assert {"field": "content.data.employeeCount", "change": "changed", "before": 4, "after": 9} in d["changes"]
    one = client.get("/api/v1/records/2/history/compare").json()
    assert one["data"]["changes"] == [] and "Only one version" in one["note"]
    m = client.get("/api/v1/records/8/history/metrics").json()
    pts = m["data"]["series"][0]["points"]
    assert [p["kind"] for p in pts] == ["baseline", "snapshot"] and [p["value"] for p in pts] == [100.0, 100.0]
    team = client.get("/api/v1/records/1/history/metrics").json()["data"]["series"][0]["points"]
    assert [p["value"] for p in team] == [4.0, 9.0]


def test_change_feed_batches_and_coverage(client) -> None:  # type: ignore[no-untyped-def]
    ch = client.get("/api/v1/history/changes", params={"days": 3650}).json()
    assert ch["meta"]["by_kind"] == {"modified": 1, "new": 1} and ch["meta"]["total"] == 2
    assert client.get("/api/v1/history/changes", params={"days": 3650, "kind": "new"}).json()["data"][0]["record_id"] == 9
    assert client.get("/api/v1/history/changes", params={"type": "bogus"}).status_code == 422
    b = client.get("/api/v1/history/batches").json()["data"]
    assert {x["trigger"] for x in b} == {"baseline_backfill", "daily_0730"}
    live = next(x for x in b if x["trigger"] == "daily_0730")
    assert (live["new"], live["modified"], live["observations"]) == (1, 1, 3)
    cov = client.get("/api/v1/history/coverage").json()
    assert cov["data"]["live_batches"] == 1 and not cov["data"]["emerging_labels_enabled"] and "30" in cov["note"]


def test_evidence_corroboration_conflicts_and_co_mentions(client) -> None:  # type: ignore[no-untyped-def]
    d = client.get(f"/api/v1/entities/{encode_entity_id('Acme AI')}/evidence").json()["data"]
    by = {(c["kind"], c["object"]): c for c in d["claims"]}
    eng = by[("hiring", "Engineering")]
    assert eng["support_tier"] == "single_source" and eng["syndicated_copies"] == 1  # aggregator repost is not a confirmation
    story = next(c for c in d["claims"] if c["kind"] == "news_coverage")
    assert story["support_tier"] == "corroborated" and story["evidence_count"] == 2  # two publishers, one story
    assert by[("website", "acme.ai")]["support_tier"] == "conflicting" and by[("website", "acme.ai")]["conflicting_values"] == ["acme-labs.com"]
    assert by[("builds_product", "acme-7b")]["support_tier"] == "single_source"
    assert d["co_mentions"] == [{"organisation": "OpenAI", "articles": 1}]
    assert not any(c["object"] == "OpenAI" for c in d["claims"])  # co-mentions never become relationships


def test_org_timeline(client) -> None:  # type: ignore[no-untyped-def]
    t = client.get(f"/api/v1/entities/{encode_entity_id('Acme AI')}/timeline").json()
    kinds = t["data"]["counts"]
    assert kinds["job_posted"] == 3 and kinds["record_changed"] == 1 and kinds["profile_listed"] == 2
    bases = {e["date_basis"] for e in t["data"]["events"]}
    assert {"published", "first_seen_on_or_before", "observed_change"} <= bases
    assert client.get(f"/api/v1/entities/{encode_entity_id('Nobody')}/timeline").status_code == 404


def test_reliability_and_historical_intelligence(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/operations/source-reliability", params={"days": 365}).json()
    rates = {s["source"]: s["success_rate"] for s in r["data"]}
    assert rates == {"venturebeat_ai": 0.0, "yc_startups": 1.0} and "single snapshot" in r["note"]
    ic = client.get("/api/v1/intelligence/changes").json()
    assert not ic["meta"]["emerging_labels_enabled"]
    sec = {s["id"]: s for s in ic["data"]["sections"]}
    assert sec["startup_discovery"]["series"] == [{"batch": "Summer 2026", "startups": 1}]
    assert sec["hiring"]["status"] == "insufficient_history"
    assert sec["new_records"]["status"] == "measured"


def test_platform_hosts_are_not_websites() -> None:
    from src.history.evidence import build_claims, domain

    assert domain("https://huggingface.co/acme") is None and domain("https://www.acme.ai/x") == "acme.ai"
    recs = [{"id": 1, "type": "STARTUP", "source": "Y Combinator", "url": "u1", "content": {"entityName": "A", "website": "https://a.ai"}, "evidence": {}},
            {"id": 2, "type": "STARTUP", "source": "Hugging Face Hub", "url": "u2", "content": {"entityName": "A", "website": "https://huggingface.co/a"}, "evidence": {}}]
    claims = build_claims("A", recs, needs_review=False)["claims"]
    web = [c for c in claims if c["kind"] == "website"]
    assert len(web) == 1 and web[0]["support_tier"] == "single_source" and not web[0]["conflicting_values"]
