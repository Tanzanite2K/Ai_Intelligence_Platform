"""Frontier Intelligence API against a SYNTHETIC fixture database opened read-only, as in production."""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.api.v1 import intelligence as I
from src.api.v1.entity_ids import encode_entity_id
from src.config import settings as cfg
from src.database.models import EntityMappingRow, RecordRow
from src.database.session import Database

COLLECTED = datetime(2026, 11, 21, 9, 30)
END = datetime(2026, 11, 19)
GAP_WEEK = (END - timedelta(days=50), END - timedelta(days=44))  # no papers published in this week


def row(i: int, rtype: str, content: dict, *, url: str, published: datetime | None = None, entity: str | None = None, source: str = "src") -> RecordRow:
    return RecordRow(id=i, record_type=rtype, schema_version="1.0", dedup_hash=f"d{i}", content_hash=f"c{i}", entity_name=entity, source_name=source,
                     source_url=url, collected_at=COLLECTED, published_at=published, date_verified=published is not None, validation_status="VERIFIED",
                     confidence=1.0, extraction_method="STRUCTURED_API", payload={"schemaVersion": "1.0", "recordType": rtype, "content": content, "provenance": {}})


def make_rows() -> tuple[list[RecordRow], list[EntityMappingRow]]:
    rows: list[RecordRow] = []
    maps: list[EntityMappingRow] = []
    i = 0
    for back in range(84):  # papers: 5/day; agents share 60% in the last 28 days vs 20% before
        d = END - timedelta(days=back)
        if GAP_WEEK[0] <= d <= GAP_WEEK[1]:
            continue
        for k in range(5):
            i += 1
            agent = k < (3 if back < 28 else 1)
            rows.append(row(i, "RESEARCH_PAPER", {"title": f"Paper {i}", "abstract": "An agentic system" if agent else "Mathematical reasoning study"},
                            url=f"https://arxiv.org/abs/{i}", published=d, source="Hugging Face Papers"))
    base = 10_000
    startups = [("Acme AI", "AI agents for sales", ["AI", "B2B"]), ("Visionly", "Computer vision for retail", ["Computer Vision"]),
                ("Medi", "Clinical copilots", ["Healthcare"]), ("Acme AI", "duplicate listing", ["AI"])]
    for j, (name, desc, tags) in enumerate(startups):
        url = f"https://yc.example/{j}"
        rows.append(row(base + j, "STARTUP", {"entityName": name, "description": desc, "tags": tags}, url=url, entity=name, source="Y Combinator"))
        maps.append(EntityMappingRow(original_name=name, canonical_name=name, match_method="EXACT", confidence=1.0, needs_review=False, entity_kind="ORGANIZATION", source_url=url, resolved_at=COLLECTED))
    for j, (org, prod, task) in enumerate([("Acme AI", "acme-chat", "text-generation"), ("Acme AI", "acme-tts", "text-to-speech"), ("Pending Co", "pc-1", "text-generation")]):
        url = f"https://huggingface.co/{prod}"
        rows.append(row(base + 100 + j, "PRODUCT", {"productName": prod, "description": task}, url=url, entity=org, source="Hugging Face Hub"))
        maps.append(EntityMappingRow(original_name=org, canonical_name=org, match_method="REVIEW" if org == "Pending Co" else "EXACT", confidence=0.5 if org == "Pending Co" else 1.0,
                                     needs_review=org == "Pending Co", entity_kind="ORGANIZATION", source_url=url, resolved_at=COLLECTED))
    for j, title in enumerate(["Robotics Engineer", "Account Executive"]):
        url = f"https://jobs.example/{j}"
        rows.append(row(base + 200 + j, "JOB", {"title": title, "role_family": "engineering", "company": "Acme AI"}, url=url, entity="Acme AI",
                        published=COLLECTED - timedelta(hours=3 + j), source="Ashby"))
        maps.append(EntityMappingRow(original_name="Acme AI", canonical_name="Acme AI", match_method="EXACT", confidence=1.0, needs_review=False, entity_kind="ORGANIZATION", source_url=url, resolved_at=COLLECTED))
    for j, orgs in enumerate([["Google", "Anthropic"], ["Google", "Anthropic", "Acme AI"], []]):
        url = f"https://news.example/{j}"
        rows.append(row(base + 300 + j, "NEWS", {"title": f"Story {j} on AI agents", "summary": "s", "organizations": orgs}, url=url,
                        published=COLLECTED - timedelta(hours=5), source="TechCrunch"))
        for o in orgs:
            maps.append(EntityMappingRow(original_name=o, canonical_name=o, match_method="EXACT", confidence=1.0, needs_review=False, entity_kind="ORGANIZATION", source_url=url, resolved_at=COLLECTED))
    return rows, maps


@pytest.fixture
def env(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    path = tmp_path / "intel.db"

    async def build() -> None:
        db = Database(f"sqlite+aiosqlite:///{path}")
        await db.create_all()
        rows, maps = make_rows()
        async with db.session() as s:
            s.add_all([*rows, *maps])
        await db.dispose()

    asyncio.run(build())
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    monkeypatch.setenv("API_READ_ONLY", "true")
    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "0")
    cfg.get_settings.cache_clear()
    I._cache["index"] = None
    from src.api.app import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, path
    I._cache["index"] = None
    cfg.get_settings.cache_clear()


def get(c, path: str, status: int = 200):  # type: ignore[no-untyped-def]
    r = c.get(f"/api/v1/intelligence/{path}")
    assert r.status_code == status, r.text
    return r.json()


def test_coverage_measures_dates_gaps_and_granularity(env) -> None:  # type: ignore[no-untyped-def]
    c, _ = env
    d = get(c, "coverage")["data"]
    cats = {x["type"]: x for x in d["categories"]}
    p = cats["papers"]
    assert p["records"] == 77 * 5 and p["with_publication_date"] == p["records"] and p["missing_days"] == 7
    assert p["largest_gaps"][0] == {"from": GAP_WEEK[0].date().isoformat(), "to": GAP_WEEK[1].date().isoformat(), "days": 7}
    assert all(p["granularity"][g]["supported"] for g in ("day", "week"))
    assert cats["startups"]["with_publication_date"] == 0 and cats["startups"]["granularity"]["day"]["reason"] == "no publication dates"
    assert not cats["jobs"]["granularity"]["week"]["supported"]
    assert d["collection_days"] == ["2026-11-21"] and any("All records were collected on 2026-11-21" in n for n in d["notes"])


def test_topics_counts_filters_and_emerging(env) -> None:  # type: ignore[no-untyped-def]
    c, _ = env
    d = get(c, "topics")["data"]
    t = {x["id"]: x for x in d["topics"]}
    assert t["agents"]["by_type"]["papers"] == 28 * 3 + 49 * 1 and t["agents"]["by_type"]["startups"] == 1 and t["agents"]["by_type"]["news"] == 3
    assert t["agents"]["categories"] == 3 and t["agents"]["top_terms"][0]["term"] == "agentic"
    papers = d["emerging"]["by_type"]["papers"]
    assert papers["assessable"] and next(x for x in papers["topics"] if x["id"] == "agents")["status"] == "rising"
    assert not d["emerging"]["by_type"]["jobs"]["assessable"] and not d["emerging"]["by_type"]["startups"]["assessable"]
    f = get(c, f"topics?types=papers,startups&date_from={(END - timedelta(days=6)).date()}&date_to={END.date()}")["data"]
    assert f["records"] == 35 and f["undated_excluded"] == 4 and set(f["by_type"]) == {"papers", "startups"}
    assert get(c, "topics?types=bogus", 422)["error"]["code"] == "invalid_type"
    assert get(c, "topics?date_from=2026-11-02&date_to=2026-11-01", 422)["error"]["code"] == "invalid_range"


def test_topic_detail_records_orgs_and_pagination(env) -> None:  # type: ignore[no-untyped-def]
    c, _ = env
    r = get(c, "topics/agents?page_size=5&page=2")
    d, meta = r["data"], r["meta"]
    assert meta["total"] == 133 + 1 + 3 and meta["page"] == 2 and len(d["records"]) == 5
    assert all(x["matched_term"] for x in d["records"])
    verified = {o["name"]: o["records"] for o in d["organisations"]["verified"]}
    mentioned = {o["name"]: o["articles"] for o in d["organisations"]["mentioned"]}
    assert verified == {"Acme AI": 1} and mentioned == {"Google": 2, "Anthropic": 2, "Acme AI": 1}
    first = get(c, "topics/agents?page_size=3")["data"]["records"]
    assert [x["published"] for x in first] == sorted([x["published"] for x in first], reverse=True)
    tl = d["timeline"]  # the 7-day gap straddles two ISO weeks, so no week is empty -- reported as lower counts, not a gap
    assert tl["granularity"] == "week" and tl["gaps"] == [] and sum(b["records"] for b in tl["buckets"]) == 385 + 2 + 3
    assert sum(b["topic_records"] for b in tl["buckets"]) == 133 + 3
    assert get(c, "topics/nope", 404)["error"]["code"] == "not_found"


def test_pending_review_mapping_is_never_a_link(env) -> None:  # type: ignore[no-untyped-def]
    c, _ = env
    names = {o["name"] for o in get(c, "innovation-map?page_size=100")["data"]["organisations"]}
    assert "Pending Co" not in names
    sig = {x["type"]: x for x in get(c, "signals")["data"]["categories"]}
    assert sig["products"]["with_verified_organisation"] == 2 and sig["products"]["records"] == 3


def test_signals_distinguish_volume_from_growth(env) -> None:  # type: ignore[no-untyped-def]
    c, _ = env
    d = get(c, "signals")["data"]
    assert any("not that a market grew" in n for n in d["notes"])
    s = {x["type"]: x for x in d["categories"]}
    assert s["startups"]["records"] == 4 and s["startups"]["sources"] == {"Y Combinator": 4} and s["startups"]["collection_days"] == ["2026-11-21"]
    assert s["startups"]["top_organisations"][0] == {"name": "Acme AI", "id": encode_entity_id("Acme AI"), "records": 2}
    assert s["news"]["most_mentioned"][0]["name"] in ("Google", "Anthropic") and s["news"]["top_organisations"] == []


def test_timeline_buckets_gaps_and_unsupported(env) -> None:  # type: ignore[no-untyped-def]
    c, _ = env
    d = get(c, "timeline?type=papers&granularity=day&topic=agents")["data"]
    assert sum(b["records"] for b in d["buckets"]) == 385 and d["supported"]
    assert d["gaps"] == [{"from": GAP_WEEK[0].date().isoformat(), "to": GAP_WEEK[1].date().isoformat(), "buckets": 7}]
    assert all(b["records"] == 0 and b["topic_share"] is None for b in d["buckets"] if not b["has_records"])
    last = d["buckets"][-1]
    assert last["records"] == 5 and last["topic_records"] == 3 and last["topic_share"] == 0.6
    j = get(c, "timeline?type=jobs&granularity=week")["data"]
    assert j["supported"] is False and "at least 4 weeks" in j["reason"]
    s = get(c, "timeline?type=startups&granularity=month")["data"]
    assert s["supported"] is False and s["buckets"] == [] and s["undated_excluded"] == 4
    get(c, "timeline?granularity=year", 422)
    get(c, "timeline?topic=nope", 404)


def test_innovation_map_semantics_and_filters(env) -> None:  # type: ignore[no-untyped-def]
    c, _ = env
    r = get(c, "innovation-map?page_size=2")
    rows, meta = r["data"]["organisations"], r["meta"]
    assert rows[0]["name"] == "Acme AI" and rows[0]["by_type"] == {"startups": 2, "products": 2, "jobs": 2} and rows[0]["news_mentions"] == 1
    assert rows[0]["id"] == encode_entity_id("Acme AI") and meta["total"] >= 4 and len(rows) == 2
    assert "not a partnership" in r["data"]["semantics"]["mentioned"] and "no organisation affiliation" in r["data"]["semantics"]["papers"]
    m = {o["name"]: o for o in get(c, "innovation-map?relation=mentioned&page_size=50")["data"]["organisations"]}
    assert set(m) == {"Google", "Anthropic", "Acme AI"} and {x["name"]: x["articles"] for x in m["Google"]["co_mentioned_with"]} == {"Anthropic": 2, "Acme AI": 1}
    assert [o["name"] for o in get(c, "innovation-map?q=vision")["data"]["organisations"]] == ["Visionly"]
    t = get(c, "innovation-map?topic=speech_audio")["data"]
    assert [o["name"] for o in t["organisations"]] == ["Acme AI"] and t["organisations"][0]["by_type"] == {"products": 1}
    mx = get(c, "innovation-map")["data"]["matrix"]
    assert len(mx["cells"]) == len(mx["organisations"]) and all(len(r) == len(mx["topics"]) for r in mx["cells"])


def test_brief_every_insight_is_evidenced(env) -> None:  # type: ignore[no-untyped-def]
    c, _ = env
    items = get(c, "brief")["data"]
    assert items[0]["id"] == "coverage"
    ids = {x["id"] for x in items}
    assert "research-rising-agents" in ids and "news-mentions" in ids
    for it in items:
        assert it["observation"] and it["confidence"]["level"] in ("high", "medium", "low") and it["confidence"]["explanation"]
        assert set(it) >= {"metrics", "organisations", "topics", "period", "evidence"} and "basis" in it["period"]
        assert it["evidence"]["explore_href"].startswith("/")
        for rec in it["evidence"]["records"]:  # every linked record exists and opens by its id
            assert c.get(f"/api/v1/{rec['type']}/{rec['id']}").status_code == 200
        assert not any(w in it["observation"].lower() for w in ("will grow", "predict", "forecast", "market size"))
    news = next(x for x in items if x["id"] == "news-mentions")
    assert "co-mention" in news["observation"] and news["confidence"]["level"] == "low"
    rising = next(x for x in items if x["id"] == "research-rising-agents")
    assert rising["evidence"]["records"] and all(r["type"] == "papers" for r in rising["evidence"]["records"])


def test_catalog_and_read_only_and_cache_invalidation(env) -> None:  # type: ignore[no-untyped-def]
    c, path = env
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    cat = get(c, "topics-catalog")
    assert len(cat["data"]) == 20 and cat["meta"]["fields"]["papers"] == ["title", "abstract"]
    built = get(c, "coverage")["meta"]["index_built_at"]
    for p in ("topics", "signals", "innovation-map", "brief", "timeline"):
        get(c, p)
    assert get(c, "coverage")["meta"]["index_built_at"] == built  # cached
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before  # the API never wrote
    con = sqlite3.connect(path)  # another process adds a record -> fingerprint changes -> index rebuilt
    con.execute("UPDATE records SET updated_at = '2030-01-01 00:00:00' WHERE id = 1")
    con.commit()
    con.close()
    assert get(c, "coverage")["meta"]["index_built_at"] != built
