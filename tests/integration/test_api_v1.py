"""/api/v1 tests against a SYNTHETIC fixture database (never the production DB).

The fixture DB is created in tmp_path, then the API opens it READ-ONLY exactly as in production.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.api.v1.entity_ids import decode_entity_id, encode_entity_id
from src.config import settings as cfg
from src.database.models import CanonicalEntityRow, DeadLetterRow, EntityMappingRow, RecordRow, SourceRunRow
from src.database.session import Database

NOW = datetime.now(UTC).replace(tzinfo=None)
ADMIN = "unit-test-admin-key"
FAKE_TOKEN = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789"  # assembled at runtime so secret scanners don't flag the source


def rec(i: int, rt: str, content: dict, *, source: str, url: str, published: datetime | None = None, entity: str | None = None,
        prov: dict | None = None, collected: datetime | None = None) -> RecordRow:
    return RecordRow(
        id=i, record_type=rt, schema_version="1.0", dedup_hash=f"d{i}", content_hash=f"c{i}", entity_name=entity, source_name=source,
        source_url=url, collected_at=collected or NOW - timedelta(hours=1), published_at=published, date_verified=published is not None,
        validation_status="VERIFIED", confidence=1.0, extraction_method="STRUCTURED_API",
        payload={"schemaVersion": "1.0", "recordType": rt, "source": {"name": source, "url": url}, "content": content,
                 "provenance": prov or {"extractionMethod": "STRUCTURED_API", "llmProvider": None, "confidence": 1.0, "validationStatus": "VERIFIED", "evidence": {}}},
    )


def mapping(i: int, raw: str, canon: str, url: str, method: str = "EXACT", review: bool = False) -> EntityMappingRow:
    return EntityMappingRow(id=i, original_name=raw, canonical_name=canon, match_method=method, confidence=0.5 if review else 1.0,
                            needs_review=review, entity_kind="ORGANIZATION", source_url=url, resolved_at=NOW)


def make_rows() -> list[RecordRow]:
    return [
    rec(1, "STARTUP", {"entityName": "Acme Vision", "rawName": "Acme Vision", "description": "Computer vision for retail", "website": "https://acme.example",
                       "location": "Berlin, Germany", "tags": ["Computer Vision", "B2B"]}, source="Y Combinator", url="https://yc.example/acme", entity="Acme Vision"),
    rec(2, "STARTUP", {"entityName": "Zeta Labs", "rawName": "zeta-labs", "description": "Speech models", "tags": ["Audio"], "location": "Paris"},
        source="Hugging Face Hub", url="https://hf.example/zeta", entity="Zeta Labs"),
    rec(3, "PRODUCT", {"productName": "AcmeCam", "startupName": "Acme Vision", "rawStartupName": "acme", "pricingModel": "FREEMIUM",
                       "description": "image-classification", "website": "https://hf.example/acme/acmecam"}, source="Hugging Face Hub", url="https://hf.example/acme/acmecam",
        entity="Acme Vision", prov={"extractionMethod": "STRUCTURED_API", "llmProvider": "gemini", "confidence": 0.9, "validationStatus": "VERIFIED",
                                    "evidence": {"pricingModel": "free to start; upgrade to Pro"}}),
    rec(4, "RESEARCH_PAPER", {"title": "Vision Transformers at Scale", "abstract": "We scale ViTs.", "authors": ["A. Author", "B. Writer"],
                              "github_url": "https://github.com/x/vit", "github_stars": 1500, "github_stars_source": "github_api", "paper_url": "https://arxiv.org/abs/1"},
        source="Hugging Face Papers", url="https://hf.example/papers/1", published=NOW - timedelta(days=40)),
    rec(5, "RESEARCH_PAPER", {"title": "Tiny Speech", "abstract": "Small audio model.", "authors": ["C. Third"], "github_stars": 12, "paper_url": "https://arxiv.org/abs/2"},
        source="Hugging Face Papers", url="https://hf.example/papers/2", published=NOW - timedelta(days=2)),
    rec(6, "RESEARCH_PAPER", {"title": "No Repo Paper", "abstract": "Theory only.", "authors": [], "github_stars": None, "paper_url": "https://arxiv.org/abs/3"},
        source="Hugging Face Papers", url="https://hf.example/papers/3", published=NOW - timedelta(days=400)),
    rec(7, "JOB", {"title": "ML Engineer", "company": "Acme Vision", "is_remote": True, "role_family": "Engineering", "location": "Remote - EU",
                   "job_url": "https://jobs.example/1", "description": "Build vision models"}, source="Ashby", url="https://jobs.example/1",
        published=NOW - timedelta(hours=3), entity="Acme Vision"),
    rec(8, "JOB", {"title": "Sales Lead", "company": "Zeta Labs", "is_remote": False, "role_family": "Sales", "location": "Paris",
                   "job_url": "https://jobs.example/2"}, source="Greenhouse", url="https://jobs.example/2", published=NOW - timedelta(hours=30), entity="Zeta Labs"),
    rec(9, "NEWS", {"title": "Acme Vision raises seed", "summary": "Funding news 100%_off", "author": "R. Porter", "article_url": "https://news.example/1",
                    "organizations": [], "word_count": 300}, source="TechCrunch", url="https://news.example/1", published=NOW - timedelta(hours=5)),
    ]


def make_maps() -> list[EntityMappingRow]:
    return [
    mapping(1, "Acme Vision", "Acme Vision", "https://yc.example/acme"),
    mapping(2, "acme", "Acme Vision", "https://hf.example/acme/acmecam", method="ALIAS"),
    mapping(3, "Acme Vision", "Acme Vision", "https://jobs.example/1"),
    mapping(4, "zeta-labs", "Zeta Labs", "https://hf.example/zeta", method="NEW"),
    mapping(5, "Zeta Labs", "Zeta Labs", "https://jobs.example/2"),
    mapping(6, "Zeta", "Zeta", "https://hf.example/zeta", method="REVIEW", review=True),
    # a pending-review case stored under the SAME canonical name as a verified organisation (the real "Cohere" situation)
    mapping(7, "Acme Vision", "Acme Vision", "https://jobs.example/2", method="REVIEW", review=True),
    ]


@pytest.fixture
def client(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    path = tmp_path / "fixture.db"
    import asyncio

    async def build() -> None:
        db = Database(f"sqlite+aiosqlite:///{path}")
        await db.create_all()
        async with db.session() as s:
            s.add_all([*make_rows(), *make_maps(), CanonicalEntityRow(id=1, canonical_name="Acme Vision", normalized_key="acme vision", domain="acme.example",
                                                         aliases=["Acme", "AcmeAI"], kind="ORGANIZATION", origin="SEED")])
            s.add_all([SourceRunRow(id=1, source="yc_startups", vertical="startups", started_at=NOW - timedelta(hours=2), finished_at=NOW - timedelta(hours=2) + timedelta(seconds=30),
                                    status="OK", fetched=10, stored=2, duplicates=0, rejected=0),
                       SourceRunRow(id=2, source="venturebeat_ai", vertical="news", started_at=NOW - timedelta(hours=1), status="FAILED",
                                    fetched=0, stored=0, duplicates=0, rejected=0, error="feed fetch failed: HTTP 429"),
                       SourceRunRow(id=3, source="producthunt", vertical="products", started_at=NOW - timedelta(minutes=30), status="INACCESSIBLE",
                                    fetched=0, stored=0, duplicates=0, rejected=0, error="access denied HTTP 401 token=" + FAKE_TOKEN),
                       DeadLetterRow(id=1, stage="llm.news_entities", key="https://news.example/1", payload={"title": "x"}, error="Gemini 503", attempts=1, created_at=NOW)])
        await db.dispose()

    asyncio.run(build())
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    monkeypatch.setenv("API_READ_ONLY", "true")
    monkeypatch.setenv("ADMIN_API_KEY", ADMIN)
    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "0")
    cfg.get_settings.cache_clear()
    from src.api.app import app

    with TestClient(app) as c:
        yield c
    cfg.get_settings.cache_clear()


def names(resp) -> list[str]:  # type: ignore[no-untyped-def]
    return [r["name"] for r in resp.json()["data"]]


# ---------------------------------------------------------------- search
def test_global_search_across_types_case_insensitive(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/search", params={"q": "ACME"})
    assert r.status_code == 200
    body = r.json()
    assert {x["type"] for x in body["data"]} == {"STARTUP", "PRODUCT", "JOB", "NEWS"}
    assert body["counts_by_type"] == {"startups": 1, "products": 1, "jobs": 1, "news": 1}
    assert body["meta"]["total"] == 4
    assert body["organisations"][0]["canonical_name"] == "Acme Vision"


def test_search_multi_term_is_and_and_covers_description_tags_authors(client) -> None:  # type: ignore[no-untyped-def]
    assert names(client.get("/api/v1/search", params={"q": "vision retail"})) == ["Acme Vision"]
    assert names(client.get("/api/v1/search", params={"q": "computer vision", "types": "startups"})) == ["Acme Vision"]  # tag
    assert names(client.get("/api/v1/search", params={"q": "b. writer"})) == ["Vision Transformers at Scale"]  # author


def test_search_escapes_like_wildcards(client) -> None:  # type: ignore[no-untyped-def]
    assert names(client.get("/api/v1/search", params={"q": "100%_off"})) == ["Acme Vision raises seed"]
    assert client.get("/api/v1/search", params={"q": "%"}).json()["meta"]["total"] == 1  # literal '%', not "match all"
    assert client.get("/api/v1/search", params={"q": "_"}).json()["meta"]["total"] == 1


def test_search_type_validation_and_empty(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/search", params={"types": "bogus"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_filter"
    r = client.get("/api/v1/search", params={"q": "nothing-matches-this"})
    assert r.status_code == 200 and r.json()["data"] == [] and r.json()["meta"]["total"] == 0


# ---------------------------------------------------------------- filters, sorting, pagination
def test_filters(client) -> None:  # type: ignore[no-untyped-def]
    assert names(client.get("/api/v1/startups", params={"category": "computer vision"})) == ["Acme Vision"]
    assert names(client.get("/api/v1/startups", params={"location": "paris"})) == ["Zeta Labs"]
    assert names(client.get("/api/v1/startups", params={"source": "Y Combinator"})) == ["Acme Vision"]
    assert names(client.get("/api/v1/products", params={"pricing_model": "FREEMIUM"})) == ["AcmeCam"]
    assert names(client.get("/api/v1/papers", params={"min_stars": 100})) == ["Vision Transformers at Scale"]
    assert names(client.get("/api/v1/jobs", params={"remote": "true"})) == ["ML Engineer"]
    assert names(client.get("/api/v1/jobs", params={"remote": "false"})) == ["Sales Lead"]
    assert names(client.get("/api/v1/jobs", params={"fresh_hours": 24})) == ["ML Engineer"]
    assert names(client.get("/api/v1/jobs", params={"category": "Sales"})) == ["Sales Lead"]
    assert names(client.get("/api/v1/products", params={"organisation": "acme"})) == ["AcmeCam"]
    since = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    assert names(client.get("/api/v1/papers", params={"date_from": since})) == ["Tiny Speech"]


def test_invalid_filters_rejected(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/papers", params={"date_from": "2026-12-01T00:00:00Z", "date_to": "2026-01-01T00:00:00Z"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_filter"
    assert client.get("/api/v1/products", params={"pricing_model": "CHEAP"}).status_code == 422
    assert client.get("/api/v1/papers", params={"min_stars": -1}).status_code == 422
    r = client.get("/api/v1/startups", params={"sort": "stars"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_sort"


def test_sorting(client) -> None:  # type: ignore[no-untyped-def]
    assert names(client.get("/api/v1/papers", params={"sort": "stars"})) == ["Vision Transformers at Scale", "Tiny Speech", "No Repo Paper"]  # nulls last
    assert names(client.get("/api/v1/papers", params={"sort": "newest"})) == ["Tiny Speech", "Vision Transformers at Scale", "No Repo Paper"]
    assert names(client.get("/api/v1/papers", params={"sort": "oldest"}))[0] == "No Repo Paper"
    assert names(client.get("/api/v1/startups", params={"sort": "name"})) == ["Acme Vision", "Zeta Labs"]


def test_pagination_metadata_and_limits(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/papers", params={"page_size": 2, "page": 2, "sort": "newest"}).json()
    assert r["meta"] == {"page": 2, "page_size": 2, "total": 3, "total_pages": 2}
    assert [x["name"] for x in r["data"]] == ["No Repo Paper"]
    assert client.get("/api/v1/papers", params={"page_size": 101}).status_code == 422
    assert client.get("/api/v1/papers", params={"page": 0}).status_code == 422
    assert client.get("/api/v1/papers", params={"page": 50}).json()["data"] == []


# ---------------------------------------------------------------- detail + provenance
def test_record_detail_provenance_and_related(client) -> None:  # type: ignore[no-untyped-def]
    d = client.get("/api/v1/products/3").json()["data"]
    prov = {f["key"]: f["provenance"] for f in d["fields"]}
    assert prov["pricingModel"] == "llm_validated" and prov["startupName"] == "entity_resolution" and prov["productName"] == "source"
    assert next(f for f in d["fields"] if f["key"] == "pricingModel")["evidence"] == "free to start; upgrade to Pro"
    assert d["resolved_organisation"]["canonical_name"] == "Acme Vision" and d["resolved_organisation"]["match_method"] == "ALIAS"
    assert {r["id"] for r in d["related"]} == {1, 7}
    p = client.get("/api/v1/papers/6").json()["data"]
    assert {f["key"]: f["provenance"] for f in p["fields"]}["github_stars"] == "missing"
    assert {f["key"]: f["provenance"] for f in client.get("/api/v1/papers/4").json()["data"]["fields"]}["github_stars"] == "enriched"
    assert p["resolved_organisation"] is None and p["related"] == []


def test_detail_type_mismatch_and_missing_are_404(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/startups/3")  # id 3 is a product
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert client.get("/api/v1/papers/9999").status_code == 404
    assert client.get("/api/v1/records/3").json()["data"]["type"] == "PRODUCT"
    assert client.get("/api/v1/jobs/abc").status_code == 422


# ---------------------------------------------------------------- organisations
def test_entity_ids_roundtrip() -> None:
    for n in ["Acme Vision", "Hugging Face/H4", "日本語 AI", "a" * 300]:
        assert decode_entity_id(encode_entity_id(n)) == n
    assert decode_entity_id("nope") is None and decode_entity_id("e_!!!") is None


def test_entities_list_profile_records_related(client) -> None:  # type: ignore[no-untyped-def]
    lst = client.get("/api/v1/entities").json()
    assert lst["meta"]["total"] == 3
    top = lst["data"][0]
    assert top["canonical_name"] == "Acme Vision" and top["record_count"] == 3 and top["origin"] == "SEED" and top["aliases"] == ["Acme", "AcmeAI"]
    assert top["review_record_count"] == 1  # the colliding review-mapped job is counted apart, never as a verified link
    assert client.get("/api/v1/entities", params={"origin": "SEED"}).json()["meta"]["total"] == 1
    assert {e["canonical_name"] for e in client.get("/api/v1/entities", params={"needs_review": "true"}).json()["data"]} == {"Zeta", "Acme Vision"}
    prof = client.get(f"/api/v1/entities/{top['id']}").json()["data"]
    assert prof["records_by_type"] == {"startups": 1, "products": 1, "jobs": 1}
    assert prof["review_records_by_type"] == {"jobs": 1}
    assert prof["original_names"] == ["Acme Vision", "acme"] and prof["domain"] == "acme.example"
    assert "papers" in prof["unavailable"]
    recs = client.get(f"/api/v1/entities/{top['id']}/records", params={"type": "jobs"}).json()
    assert [r["name"] for r in recs["data"]] == ["ML Engineer"]  # verified mappings only
    rev = client.get(f"/api/v1/entities/{top['id']}/records", params={"type": "jobs", "mapping": "review"}).json()
    assert [r["name"] for r in rev["data"]] == ["Sales Lead"]
    rel = client.get(f"/api/v1/entities/{top['id']}/related").json()
    assert rel["data"] == [] and rel["note"]
    assert client.get(f"/api/v1/entities/{encode_entity_id('Nobody')}").status_code == 404
    assert client.get("/api/v1/entities/not-an-id").status_code == 422


def test_reviews_are_read_only(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/reviews").json()
    assert r["meta"]["total"] == 2
    item = r["data"][0]
    assert item["original_name"] == "Zeta" and item["status"] == "PENDING" and item["source_record"]["name"] == "Zeta Labs"
    assert item["proposed_candidate"] is None and item["audit"] is None and "docs/entity_review.md" in item["note"]
    for action in ("approve", "reject", "defer"):
        assert client.post(f"/api/v1/reviews/{item['id']}/{action}").status_code in (404, 405)  # no write endpoints exist


# ---------------------------------------------------------------- operations + dashboard
def test_operations_and_secret_redaction(client) -> None:  # type: ignore[no-untyped-def]
    runs = client.get("/api/v1/operations/ingestion-runs").json()
    assert runs["meta"]["total"] == 3 and runs["data"][0]["source"] == "producthunt"
    assert runs["data"][-1]["duration_s"] == 30.0
    assert FAKE_TOKEN not in str(runs) and FAKE_TOKEN[:12] not in str(runs)
    assert client.get("/api/v1/operations/ingestion-runs", params={"status": "FAILED"}).json()["meta"]["total"] == 1
    health = {h["source"]: h for h in client.get("/api/v1/operations/source-health").json()["data"]}
    assert health["venturebeat_ai"]["rate_limited_runs"] == 1 and health["producthunt"]["access_denied_runs"] == 1
    assert health["yc_startups"]["status"] == "OK" and health["yc_startups"]["last_success_at"]
    dl = client.get("/api/v1/operations/dead-letters").json()
    assert dl["meta"]["total"] == 1 and dl["data"][0]["retry_supported"] is False


def test_dashboard_uses_real_aggregates(client) -> None:  # type: ignore[no-untyped-def]
    o = client.get("/api/v1/dashboard/overview").json()["data"]
    assert o["records"] == {"startups": 2, "products": 1, "papers": 3, "jobs": 2, "news": 1} and o["total_records"] == 9
    assert (o["organisations"], o["entity_mappings"], o["pending_reviews"], o["dead_letters"], o["failed_runs"]) == (3, 7, 2, 1, 2)
    a = client.get("/api/v1/dashboard/activity").json()
    assert a["meta"]["collection_days"] == 1 and "1 day" in a["note"]  # limitation reported, no fabricated series
    f = {x["type"]: x for x in client.get("/api/v1/dashboard/freshness").json()["data"]}
    assert f["jobs"]["within_24h"] == 1 and f["startups"]["undated"] == 2 and f["papers"]["older_than_365d"] == 1
    sc = client.get("/api/v1/dashboard/source-contribution").json()["data"]
    assert {"source": "Hugging Face Papers", "type": "papers", "records": 3} in sc
    rc = client.get("/api/v1/dashboard/recent", params={"limit": 1}).json()["data"]
    assert rc["papers"][0]["name"] == "Tiny Speech" and len(rc["runs"]) == 3


# ---------------------------------------------------------------- security
def test_admin_endpoints_require_key(client) -> None:  # type: ignore[no-untyped-def]
    assert client.post("/api/v1/admin/crawl/yc_startups").status_code == 401
    assert client.post("/api/v1/admin/crawl/yc_startups", headers={"X-Admin-Key": "wrong"}).status_code == 401
    assert client.post("/crawl/yc_startups").status_code == 401  # legacy route is protected too
    assert client.post("/api/v1/admin/crawl/nope", headers={"X-Admin-Key": ADMIN}).status_code == 404


def test_admin_disabled_without_configured_key(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("ADMIN_API_KEY")
    cfg.get_settings.cache_clear()
    r = client.post("/api/v1/admin/crawl/yc_startups", headers={"X-Admin-Key": "anything"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "admin_disabled"


def test_cors_allowlist(client) -> None:  # type: ignore[no-untyped-def]
    ok = client.options("/api/v1/search", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    bad = client.options("/api/v1/search", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert bad.headers.get("access-control-allow-origin") is None


def test_rate_limit(client) -> None:  # type: ignore[no-untyped-def]
    from src.api.v1.deps import RateLimiter

    client.app.state.limiter = RateLimiter(3)
    codes = [client.get("/api/v1/dashboard/overview").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    assert client.get("/health").status_code == 200  # limiter applies to /api/v1 only


def test_api_cannot_write_to_database(client) -> None:  # type: ignore[no-untyped-def]
    db = client.app.state.read_db
    assert "mode=ro" in db.url

    import asyncio

    from sqlalchemy import text

    async def try_write() -> str:
        async with db.engine.connect() as c:
            try:
                await c.execute(text("DELETE FROM records"))
                return "wrote"
            except Exception as e:  # noqa: BLE001
                return type(e).__name__

    assert asyncio.run(try_write()) == "OperationalError"


def test_errors_are_safe_json(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/search", params={"page_size": "x"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request" and "Traceback" not in r.text
    assert client.get("/api/v1/openapi-does-not-exist").json()["error"]["code"] == "http_error"
    assert client.get("/openapi.json").status_code == 200


def test_fixture_is_not_production_db(tmp_path) -> None:  # guard: tests never touch data/platform.db
    assert "data/platform.db" not in str(tmp_path)
    assert sqlite3.sqlite_version


def test_related_records_use_verified_mappings_only(client) -> None:  # type: ignore[no-untyped-def]
    d = client.get("/api/v1/jobs/7").json()["data"]
    assert {r["id"] for r in d["related"]} == {1, 3}  # job 8 is linked to Acme only by a pending-review mapping -> excluded


def test_review_audit_notes_and_candidate(client, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from src.api.v1 import relationships

    doc = tmp_path / "entity_review.md"
    doc.write_text(
        "# Review\n\n## REVIEW queue (1) - kept separate\n\n| Raw name | Closest existing entity | Why flagged | Evidence | Assessment |\n"
        "|---|---|---|---|---|\n| Zeta (HF org `zeta`) | Zeta Labs (YC) | fuzzy 90 / semantic 0.7 | no shared domain | **Uncertain**: needs a website check |\n\n## Other\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(relationships, "REVIEW_DOC", doc)
    items = {i["original_name"]: i for i in client.get("/api/v1/reviews").json()["data"]}
    z = items["Zeta"]
    assert z["proposed_candidate"] == "Zeta Labs (YC)" and z["reason"] == "fuzzy 90 / semantic 0.7"
    assert z["audit"]["assessment"] == "Uncertain: needs a website check"
    assert z["candidate_entity"]["canonical_name"] == "Zeta Labs" and z["candidate_entity"]["records"] == 2
    assert items["Acme Vision"]["audit"] is None and items["Acme Vision"]["shares_canonical_with"] == 3
    assert z["confidence"] == 0.5  # stored value, not invented


def test_review_doc_parser_handles_missing_file(tmp_path) -> None:
    from src.api.v1.relationships import review_doc_notes

    assert review_doc_notes(tmp_path / "missing.md") == {}


def test_relationships_overview(client) -> None:  # type: ignore[no-untyped-def]
    d = client.get("/api/v1/relationships/overview").json()
    o = d["data"]
    assert o["organisations"] == 3 and o["organisations_with_records"] == 2 and o["pending_reviews"] == 2
    assert o["organisations_by_linked_types"] == {"2": 1, "3": 1}
    assert {m["method"]: m["mappings"] for m in o["match_methods"]} == {"EXACT": 3, "REVIEW": 2, "ALIAS": 1, "NEW": 1}
    assert next(m for m in o["match_methods"] if m["method"] == "REVIEW")["max_confidence"] == 0.5
    assert o["top_organisations"][0] == {"id": encode_entity_id("Acme Vision"), "name": "Acme Vision",
                                         "records_by_type": {"startups": 1, "products": 1, "jobs": 1}, "records": 3}
    assert o["alias_merges"]["total"] == 2 and o["news_articles_with_validated_tags"] == 0
    assert "none are stored yet" in d["unavailable"]["co_mentions"]


def test_quality_summary(client) -> None:  # type: ignore[no-untyped-def]
    q = client.get("/api/v1/dashboard/quality").json()["data"]
    assert q["validation_status"]["papers"] == {"VERIFIED": 3}
    assert {x["type"]: x["date_verified"] for x in q["dates"]}["jobs"] == 2
