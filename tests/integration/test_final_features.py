"""Sheets read-back verification, stored-record LLM enrichment, mid-run checkpoint recovery, GitHub ETag caching."""

from __future__ import annotations

import re

import pytest

from src.core.schemas import ProductRecord
from src.crawlers.products.huggingface import HuggingFaceProductCrawler
from src.crawlers.research.github import GitHubClient
from src.database.repository import Repository
from src.exports.google_sheets import GoogleSheetsExporter, SheetsError
from src.exports.tabs import Tab
from src.pipelines.ingestion import IngestionPipeline
from tests.conftest import NOW
from tests.unit.test_llm_orchestrator import ScriptedProvider, Sleeps

BASE = "https://sheets.googleapis.com/v4/spreadsheets"


async def test_sheets_verify_reads_back_tabs_rows_headers_and_sharing(mocked) -> None:  # type: ignore[no-untyped-def]
    tabs = [Tab("Startups", ["a", "b"], [[1, 2], [3, 4]]), Tab("News", ["x"], [])]
    mocked.get(re.compile(rf"{BASE}/SID\?.*fields=sheets.*"), payload={"sheets": [{"properties": {"title": "Startups"}}, {"properties": {"title": "News"}}]})
    mocked.get(re.compile(rf"{BASE}/SID/values:batchGet\?.*majorDimension=COLUMNS.*"), payload={"valueRanges": [{"values": [["a", "1", "3"]]}, {"values": [["x"]]}]})
    mocked.get(re.compile(rf"{BASE}/SID/values:batchGet\?(?!.*majorDimension).*"), payload={"valueRanges": [{"values": [["a", "b"]]}, {"values": [["x"]]}]})
    mocked.get(re.compile(r"https://www\.googleapis\.com/drive/v3/files/SID/permissions.*"), payload={"permissions": [{"type": "anyone", "role": "reader"}]})
    v = await GoogleSheetsExporter("tok").verify("SID", tabs)
    assert v["exactly_six_expected_tabs"] is True and v["rows_match"] is True  # (two expected tabs in this fixture)
    assert v["data_rows"] == {"Startups": 2, "News": 0} and v["headers_match"] == {"Startups": True, "News": True} and v["public_link_reader"] is True


async def test_sheets_storage_quota_error_is_actionable(mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.post(BASE, status=403, body='{"error":{"message":"The user\'s Drive storage quota has been exceeded.","status":"PERMISSION_DENIED","reason":"storageQuotaExceeded"}}')
    with pytest.raises(SheetsError, match="GOOGLE_SHEET_ID"):
        await GoogleSheetsExporter("tok", sleep=Sleeps()).export([Tab("Startups", ["a"], [])], spreadsheet_id=None, title="T")


async def test_enrich_products_uses_llm_only_with_pricing_cue(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from src.config import settings as cfg
    from src.database.session import Database
    from src.pipelines import enrich

    url = f"sqlite+aiosqlite:///{tmp_path}/e.db"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg.get_settings.cache_clear()
    db = Database(url)
    await db.create_all()

    def prod(name: str, desc: str) -> ProductRecord:
        return ProductRecord.model_validate({"source": {"name": "PH", "url": f"https://www.producthunt.com/posts/{name}"}, "collectedAt": NOW,
                                             "content": {"productName": name, "description": desc}, "provenance": {"extractionMethod": "STRUCTURED_API"}})

    await Repository(db).bulk_upsert([prod("atlas", "Atlas is free to start; upgrade to the Pro plan for $20/month."), prod("plain", "text-generation")])
    calls: list[str] = []

    def script(req):  # type: ignore[no-untyped-def]
        calls.append(req.user)
        return {"fields": {"pricing_model": {"value": "FREEMIUM", "evidence": "free to start; upgrade to the Pro plan for $20/month"}}}

    monkeypatch.setattr(enrich, "build_providers", lambda s: [ScriptedProvider("gemini", script)])
    monkeypatch.chdir(tmp_path)
    out = await enrich.run_enrich_products()
    assert out["with_pricing_cue"] == 1 and out["records_updated"] == 1 and len(calls) == 1  # no LLM call for cue-less text
    rows = {r["content"]["productName"]: r for r in await Repository(db).fetch_records("PRODUCT")}
    assert rows["atlas"]["content"]["pricingModel"] == "FREEMIUM" and rows["atlas"]["provenance"]["llmProvider"] == "gemini"
    assert rows["plain"]["content"]["pricingModel"] is None
    assert (tmp_path / "docs" / "llm_enrichment.json").exists()
    await db.dispose()
    cfg.get_settings.cache_clear()


async def test_checkpoint_survives_crash_mid_run_and_resumes(repo: Repository, settings, http, mocked) -> None:  # type: ignore[no-untyped-def]
    page1 = "https://huggingface.co/api/models?sort=likes&direction=-1&limit=1000&full=true"
    page2 = "https://huggingface.co/api/models?cursor=P2"
    mocked.get(page1, payload=[{"id": f"acme/m{i}", "author": "acme", "tags": []} for i in range(3)], headers={"Link": f'<{page2}>; rel="next"'})
    mocked.get(page2, status=500, repeat=True)  # page 2 keeps failing -> run ends early
    mocked.get("https://huggingface.co/api/organizations/acme/overview", payload={"fullname": "Acme"}, repeat=True)
    pipe = IngestionPipeline(repo, settings=settings, http=http, flush_size=2, clock=lambda: NOW)
    await pipe.run_crawler(HuggingFaceProductCrawler(kinds=("models",), pages=2))
    cp = await repo.get_checkpoint("hf_products")
    assert cp["next_url:models"] == page2  # resume point persisted
    mocked.get(page2, payload=[], headers={})
    mocked.requests.clear()
    await pipe.run_crawler(HuggingFaceProductCrawler(kinds=("models",), pages=2))
    fetched = [str(k[1]) for k in mocked.requests if "api/models" in str(k[1])]
    assert fetched and fetched[0] == page2  # second run resumed instead of starting from page 1


async def test_github_etag_revalidation(http, mocked) -> None:  # type: ignore[no-untyped-def]
    url = "https://api.github.com/repos/a/b"
    mocked.get(url, payload={"html_url": "https://github.com/a/b", "full_name": "a/b", "stargazers_count": 5}, headers={"ETag": '"v1"', "X-RateLimit-Remaining": "100"})
    mocked.get(url, status=304, headers={"X-RateLimit-Remaining": "100"})
    gh = GitHubClient(http, "tok")
    first = await gh.repo_metrics("https://github.com/a/b")
    second = await gh.repo_metrics("https://github.com/a/b")
    assert first and second and second.stars == 5
    sent = mocked.requests[("GET", __import__("yarl").URL(url))]
    assert sent[1].kwargs["headers"]["If-None-Match"] == '"v1"'


async def test_rebuild_entities_splits_wrong_merge_and_keeps_same_org_merged(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from src.core.schemas import JobRecord, StartupRecord
    from src.database.session import Database
    from src.pipelines.maintenance import rebuild_entities

    url = f"sqlite+aiosqlite:///{tmp_path}/m.db"
    db = Database(url)
    await db.create_all()
    repo = Repository(db)

    def st(raw: str, canon: str, slug: str, site: str) -> StartupRecord:
        return StartupRecord.model_validate({"source": {"name": "Y Combinator", "url": f"https://www.ycombinator.com/companies/{slug}"},
                                             "content": {"entityName": canon, "rawName": raw, "website": site}, "provenance": {"extractionMethod": "STRUCTURED_API"}})

    # state produced by the old rules: 'Conntour' wrongly stored as 'Contour'
    await repo.bulk_upsert([st("Contour", "Contour", "contour", "https://contour.dev"), st("Conntour", "Contour", "conntour", "https://conntour.ai")], priority=10)
    job = JobRecord.model_validate({"source": {"name": "Himalayas", "url": "https://himalayas.app/j/1"}, "provenance": {"extractionMethod": "STRUCTURED_API"},
                                    "content": {"title": "AI Specialist", "company": "DataSuite", "raw_company": "Datasite", "date": NOW, "role_family": "Other",
                                                "job_url": "https://himalayas.app/j/1"}})
    await repo.bulk_upsert([job])
    out = await rebuild_entities(url)
    names = sorted(r["content"]["entityName"] for r in await repo.fetch_records("STARTUP"))
    assert names == ["Conntour", "Contour"]  # split back into two companies
    assert (await repo.fetch_records("JOB"))[0]["content"]["company"] == "Datasite"
    assert out["entity_mapping_log"]["needs_review"] >= 1 and out["STARTUP"]["after"] == 2
    log = await repo.fetch_mappings()
    assert any(m["originalName"] == "Conntour" and m["matchMethod"] == "REVIEW" for m in log)
    assert not any(m["originalName"] == "Datasite" and m["canonicalName"] == "DataSuite" for m in log)  # superseded decision removed
    await db.dispose()
