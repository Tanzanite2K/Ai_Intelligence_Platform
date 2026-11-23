"""News page API: validated organisation tags, facets, exact tag filtering, detail tags and related articles.

Runs against a SYNTHETIC fixture database in tmp_path, opened read-only exactly as in production.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.api.v1.entity_ids import encode_entity_id
from src.config import settings as cfg
from src.database.models import EntityMappingRow, RecordRow
from src.database.session import Database

NOW = datetime.now(UTC).replace(tzinfo=None)


def news(i: int, title: str, orgs: list[str] | None, *, hours: int, summary: str = "", source: str = "TechCrunch") -> RecordRow:
    url = f"https://news.example/{i}"
    content: dict = {"title": title, "summary": summary, "article_url": url, "author": "A. Writer", "word_count": 400}
    if orgs is not None:  # None = the key is missing entirely (older records)
        content["organizations"] = orgs
    return RecordRow(
        id=i,
        record_type="NEWS",
        schema_version="1.0",
        dedup_hash=f"d{i}",
        content_hash=f"c{i}",
        entity_name=None,
        source_name=source,
        source_url=url,
        collected_at=NOW - timedelta(hours=1),
        published_at=NOW - timedelta(hours=hours),
        date_verified=True,
        validation_status="VERIFIED",
        confidence=1.0,
        extraction_method="LLM_EXTRACTION",
        payload={
            "schemaVersion": "1.0",
            "recordType": "NEWS",
            "source": {"name": source, "url": url},
            "content": content,
            "provenance": {
                "extractionMethod": "LLM_EXTRACTION",
                "llmProvider": "gemini",
                "confidence": 0.9,
                "validationStatus": "VERIFIED",
                "evidence": {"organizations": "verbatim quotes"},
            },
        },
    )


def m(i: int, article: int, name: str, method: str = "EXACT", review: bool = False) -> EntityMappingRow:
    return EntityMappingRow(
        id=i,
        original_name=name,
        canonical_name=name,
        match_method=method,
        confidence=0.5 if review else 1.0,
        needs_review=review,
        entity_kind="ORGANIZATION",
        source_url=f"https://news.example/{article}",
        resolved_at=NOW,
    )


def make_rows() -> list[RecordRow]:
    return [
        news(101, "Google and Anthropic sign compute deal", ["Google", "Anthropic"], hours=1),
        news(102, "DeepMind ships a model; Meta responds", ["Google DeepMind", "Meta"], hours=2),
        news(103, "Baseten raises on Google Cloud", ["Google", "google", "Baseten"], hours=3),  # duplicate tag in two cases
        news(104, "Google rumours swirl", [], hours=4, summary="Everyone talks about Google, Meta and Anthropic"),  # mentions only, untagged
        news(105, "Old article without the tags field", None, hours=5, summary="Google"),
        news(106, "Anthropic and Meta at the summit", ["Anthropic", "Meta"], hours=6, source="VentureBeat"),
        news(107, "OpenAI review case", ["OpenAI"], hours=7),
        news(108, "Unicode tag", ["Zürich AI Labs"], hours=8),
    ]


def make_maps() -> list[EntityMappingRow]:
    return [
        m(1, 101, "Google"),
        m(2, 101, "Anthropic"),
        m(3, 102, "Google DeepMind"),
        m(4, 102, "Meta"),
        m(5, 103, "Google"),
        m(6, 103, "Baseten", "NEW"),
        m(7, 106, "Anthropic"),
        m(8, 106, "Meta"),
        m(9, 107, "OpenAI", "REVIEW", review=True),
    ]


@pytest.fixture
def client(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    path = tmp_path / "news.db"

    async def build() -> None:
        db = Database(f"sqlite+aiosqlite:///{path}")
        await db.create_all()
        async with db.session() as s:
            s.add_all([*make_rows(), *make_maps()])
        await db.dispose()

    asyncio.run(build())
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    monkeypatch.setenv("API_READ_ONLY", "true")
    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "0")
    cfg.get_settings.cache_clear()
    from src.api.app import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    cfg.get_settings.cache_clear()


def ids(client, **params) -> list[int]:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/news", params={"page_size": 100, **params})
    assert r.status_code == 200, r.text
    return [x["id"] for x in r.json()["data"]]


def test_facets_counts_are_computed_from_stored_tags(client) -> None:  # type: ignore[no-untyped-def]
    d = client.get("/api/v1/news/facets").json()["data"]
    assert (d["total"], d["tagged"], d["untagged"]) == (8, 6, 2)  # 104 (empty list) and 105 (missing key) are untagged
    orgs = {o["name"]: o["articles"] for o in d["organisations"]}
    assert orgs["Google"] == 2 and orgs["Anthropic"] == 2 and orgs["Meta"] == 2 and orgs["Baseten"] == 1 and orgs["Google DeepMind"] == 1
    assert "google" not in orgs  # duplicate-cased tag is counted once, under its first spelling
    assert next(o for o in d["organisations"] if o["name"] == "Google")["id"] == encode_entity_id("Google")


@pytest.mark.parametrize(
    ("org", "expected"),
    [
        ("Google", [101, 103]),
        ("Meta", [102, 106]),
        ("Anthropic", [101, 106]),
        ("Baseten", [103]),
        ("google", [101, 103]),
        ("GOOGLE", [101, 103]),
        ("  Google  ", [101, 103]),
        ("baseTEN", [103]),
        ("Google DeepMind", [102]),
        ("zürich ai labs", [108]),
    ],
)
def test_org_filter_is_exact_case_insensitive_tag_match(client, org, expected) -> None:  # type: ignore[no-untyped-def]
    got = ids(client, organisation=org, sort="newest")
    assert got == expected
    for x in client.get("/api/v1/news", params={"organisation": org}).json()["data"]:
        assert org.strip().casefold() in {t.casefold() for t in x["organizations"]}


@pytest.mark.parametrize("org", ["Goog", "AI", "Open", "DeepMind", "rumours", "Nobody"])
def test_org_filter_never_matches_substrings_or_article_text(client, org) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/news", params={"organisation": org})
    assert r.status_code == 200 and r.json()["data"] == [] and r.json()["meta"]["total"] == 0  # empty result, not an error


def test_text_mentions_do_not_count_as_tags(client) -> None:  # type: ignore[no-untyped-def]
    assert 104 not in ids(client, organisation="Google") and 105 not in ids(client, organisation="Google")


def test_clearing_filter_restores_all_articles(client) -> None:  # type: ignore[no-untyped-def]
    assert len(ids(client, organisation="Meta")) == 2
    assert len(ids(client)) == 8 and len(ids(client, organisation="")) == 8


def test_tagged_filter(client) -> None:  # type: ignore[no-untyped-def]
    assert sorted(ids(client, tagged="true")) == [101, 102, 103, 106, 107, 108]
    assert sorted(ids(client, tagged="false")) == [104, 105]
    assert ids(client, tagged="true", organisation="Anthropic", sort="oldest") == [106, 101]
    assert client.get("/api/v1/news", params={"tagged": "maybe"}).status_code == 422


def test_pagination_and_sorting_with_filter(client) -> None:  # type: ignore[no-untyped-def]
    p1 = client.get("/api/v1/news", params={"tagged": "true", "page_size": 4, "page": 1, "sort": "newest"}).json()
    p2 = client.get("/api/v1/news", params={"tagged": "true", "page_size": 4, "page": 2, "sort": "newest"}).json()
    assert p1["meta"] == {"page": 1, "page_size": 4, "total": 6, "total_pages": 2}
    assert [x["id"] for x in p1["data"]] == [101, 102, 103, 106] and [x["id"] for x in p2["data"]] == [107, 108]
    assert ids(client, organisation="google", sort="oldest") == [103, 101]
    assert ids(client, organisation="Meta", source="VentureBeat") == [106]


def test_listing_dedupes_tags_and_handles_missing_tags(client) -> None:  # type: ignore[no-untyped-def]
    by_id = {x["id"]: x for x in client.get("/api/v1/news", params={"page_size": 100}).json()["data"]}
    assert by_id[103]["organizations"] == ["Google", "Baseten"]
    assert by_id[104]["organizations"] == [] and by_id[105]["organizations"] == []


def test_detail_tags_match_listing_and_related_uses_verified_tags(client) -> None:  # type: ignore[no-untyped-def]
    listing = {x["id"]: x for x in client.get("/api/v1/news", params={"page_size": 100}).json()["data"]}
    for i in listing:
        d = client.get(f"/api/v1/news/{i}").json()["data"]
        assert [t["name"] for t in d["organisation_tags"]] == listing[i]["organizations"]
        assert d["resolved_organisation"] is None and d["name"] == listing[i]["name"] and d["source_url"] == f"https://news.example/{i}"
        assert d["freshness_hours"] is not None and d["provenance"]["validationStatus"] == "VERIFIED"
    d = client.get("/api/v1/news/101").json()["data"]
    assert {t["name"]: (t["mapped"], t["match_method"]) for t in d["organisation_tags"]} == {"Google": (True, "EXACT"), "Anthropic": (True, "EXACT")}
    assert d["organisation_tags"][0]["entity_id"] == encode_entity_id("Google")
    assert {r["id"] for r in d["related"]} == {103, 106}  # shares Google (103) and Anthropic (106); 102 "Google DeepMind" is a different org
    review = client.get("/api/v1/news/107").json()["data"]
    assert review["organisation_tags"][0]["needs_review"] is True and review["related"] == []  # a review mapping links nothing
    assert client.get("/api/v1/news/104").json()["data"]["organisation_tags"] == []
    assert client.get("/api/v1/news/105").json()["data"]["related"] == []  # missing tags field does not crash


def test_detail_errors(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/news/9999")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert client.get("/api/v1/news/abc").status_code == 422
    assert client.get("/api/v1/news/facets", params={"top": 0}).status_code == 422


def test_entity_related_news_uses_exact_tags(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get(f"/api/v1/entities/{encode_entity_id('Google')}/related")
    assert r.status_code == 200
    names = {x["name"] for x in r.json()["data"]}
    assert "Google DeepMind" not in names  # DeepMind never shares an article tag with Google here
    assert {"Anthropic", "Baseten"} <= names
