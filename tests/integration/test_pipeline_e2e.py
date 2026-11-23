"""End-to-end: mocked sources -> pipeline (ER + LLM enrichment) -> DB -> CSV/Sheets export."""

from __future__ import annotations

import csv
import json
import re
from datetime import timedelta

from src.config.settings import Settings
from src.core.dedup import InMemoryDeduplicator
from src.crawlers.jobs.boards import AshbyCrawler
from src.crawlers.news.rss import FeedSource, RSSNewsCrawler
from src.crawlers.products.huggingface import HuggingFaceProductCrawler
from src.crawlers.startups.yc import YCStartupCrawler
from src.database.repository import Repository
from src.exports.csv_export import write_csvs
from src.exports.google_sheets import GoogleSheetsExporter
from src.exports.tabs import build_tabs
from src.extraction.orchestrator import LLMOrchestrator
from src.pipelines.ingestion import IngestionPipeline
from tests.conftest import HOURS, LONG_ARTICLE, NOW, iso, rfc2822, rss
from tests.unit.test_llm_orchestrator import ScriptedProvider, Sleeps

FEED = FeedSource("e2e_feed", "E2E News", "https://news.example.com/feed")


def mock_sources(mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get("https://yc-oss.github.io/api/tags/ai.json", payload=[
        {"id": 1, "name": "OpenAI, Inc.", "slug": "openai", "website": "https://openai.com", "team_size": 3000, "url": "https://www.ycombinator.com/companies/openai"},
        {"id": 2, "name": "Acme Robotics Inc.", "slug": "acme", "website": "https://acme-robotics.io", "team_size": 9, "url": "https://www.ycombinator.com/companies/acme"},
    ], repeat=True)
    mocked.get(re.compile(r"https://huggingface\.co/api/models\?.*"), payload=[
        {"id": "mistralai/Mistral-7B", "author": "mistralai", "tags": ["license:apache-2.0"]},
        {"id": "acme/atlas-app", "author": "acme", "tags": [], "cardData": {"short_description": "Atlas is free to start; upgrade to the Pro plan for $20/month."}},
    ], repeat=True)
    mocked.get("https://huggingface.co/api/organizations/mistralai/overview", payload={"fullname": "Mistral AI"}, repeat=True)
    mocked.get("https://huggingface.co/api/organizations/acme/overview", payload={"fullname": "ACME ROBOTICS"}, repeat=True)
    mocked.get("https://api.ashbyhq.com/posting-api/job-board/openai", payload={"jobs": [
        {"id": "1", "title": "Research Scientist", "jobUrl": "https://jobs.ashbyhq.com/openai/1", "publishedAt": iso(NOW - 2 * HOURS), "isRemote": False},
        {"id": "2", "title": "Old Role", "jobUrl": "https://jobs.ashbyhq.com/openai/2", "publishedAt": iso(NOW - 72 * HOURS)}]}, repeat=True)
    mocked.get(FEED.feed_url, body=rss([
        {"title": "OpenAI and Anthropic news", "link": "https://news.example.com/a", "pub": rfc2822(NOW - HOURS), "content": LONG_ARTICLE},
        {"title": "Undated", "link": "https://news.example.com/u"}]), repeat=True)
    mocked.get("https://news.example.com/u", body="<p>nothing</p>", repeat=True)


def llm_script(req):  # type: ignore[no-untyped-def]
    if "pricing" in req.user.lower() and "free to start" in req.user:
        return {"fields": {"pricing_model": {"value": "FREEMIUM", "evidence": "free to start; upgrade to the Pro plan for $20/month"}}}
    if "organisations" in req.user or "organizations" in req.user:
        return {"fields": {"organizations": {"value": ["OpenAI", "Anthropic"], "evidence": "OpenAI and Anthropic announced"}}}
    return {"fields": {}}


async def run_all(pipe: IngestionPipeline):  # type: ignore[no-untyped-def]
    out = []
    out.append(await pipe.run_crawler(YCStartupCrawler(endpoints=["https://yc-oss.github.io/api/tags/ai.json"], use_algolia=False), priority=10))
    out.append(await pipe.run_crawler(HuggingFaceProductCrawler(kinds=("models",), pages=1), priority=5))
    out.append(await pipe.run_crawler(AshbyCrawler(orgs={"openai": "OpenAI"}), priority=10))
    out.append(await pipe.run_crawler(RSSNewsCrawler(FEED), priority=10))
    return out


async def test_end_to_end_with_llm_enrichment_and_exports(repo: Repository, settings: Settings, http, mocked, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mock_sources(mocked)
    orch = LLMOrchestrator([ScriptedProvider("gemini", llm_script)], settings, sleep=Sleeps())
    pipe = IngestionPipeline(repo, settings=settings, http=http, orchestrator=orch, dedup=InMemoryDeduplicator(), clock=lambda: NOW)
    summaries = await run_all(pipe)
    assert [s.status for s in summaries] == ["OK"] * 4
    counts = await repo.count_by_type()
    assert counts == {"STARTUP": 2, "PRODUCT": 2, "JOB": 1, "NEWS": 2}

    startups = {r["content"]["entityName"]: r for r in await repo.fetch_records("STARTUP")}
    assert set(startups) == {"OpenAI", "Acme Robotics"}  # entity resolution applied
    assert startups["OpenAI"]["content"]["rawName"] == "OpenAI, Inc."

    products = {r["content"]["productName"]: r for r in await repo.fetch_records("PRODUCT")}
    atlas = products["atlas-app"]
    assert atlas["content"]["startupName"] == "Acme Robotics"  # 'ACME ROBOTICS' -> learned canonical from the YC run
    assert atlas["content"]["pricingModel"] == "FREEMIUM" and atlas["provenance"]["llmProvider"] == "gemini"
    assert "upgrade to the Pro plan" in atlas["provenance"]["evidence"]["pricingModel"]
    assert products["Mistral-7B"]["content"]["startupName"] == "Mistral AI"

    news = {r["content"]["title"]: r for r in await repo.fetch_records("NEWS")}
    assert news["OpenAI and Anthropic news"]["content"]["organizations"] == ["OpenAI", "Anthropic"]

    # exports (window evaluated relative to the fixture clock)
    tabs = await build_tabs(repo, now=NOW + timedelta(minutes=5))
    rows = {t.title: t for t in tabs}
    assert [t.title for t in tabs] == ["Startups", "Products", "Research Papers", "Jobs", "News", "Entity Mapping Log"]
    assert len(rows["Jobs"].rows) == 1 and len(rows["News"].rows) == 1  # undated news excluded from guaranteed-fresh tab
    assert rows["Startups"].columns[:7] == ["schemaVersion", "recordType", "source.name", "source.url", "content.entityName", "content.data.employeeCount", "collectedAt"]
    assert len(rows["Entity Mapping Log"].rows) >= 6
    later = {t.title: t for t in await build_tabs(repo, now=NOW + timedelta(hours=30))}
    assert later["Jobs"].rows == [] and later["News"].rows == []  # freshness re-checked at export time

    paths = write_csvs(tabs, tmp_path / "exp")
    with paths["Startups"].open() as f:
        data = list(csv.DictReader(f))
    assert {d["content.entityName"] for d in data} == {"OpenAI", "Acme Robotics"}
    assert all(d["source.url"].startswith("https://") for d in data)

    # second run: idempotent, nothing inserted, jobs/news not re-processed
    again = await run_all(pipe)
    assert sum(s.inserted for s in again) == 0
    assert (await repo.count_by_type()) == counts
    assert again[2].rejected.get("duplicate_claimed_elsewhere", 0) >= 1
    await orch.close()


async def test_two_workers_share_dedup_no_double_processing(repo: Repository, settings: Settings, http, mocked) -> None:  # type: ignore[no-untyped-def]
    mock_sources(mocked)
    shared = InMemoryDeduplicator()  # stands in for Redis; Redis semantics are tested in test_storage_dedup
    p1 = IngestionPipeline(repo, settings=settings, http=http, dedup=shared, clock=lambda: NOW)
    p2 = IngestionPipeline(repo, settings=settings, http=http, dedup=shared, clock=lambda: NOW)
    import asyncio

    s1, s2 = await asyncio.gather(p1.run_crawler(RSSNewsCrawler(FEED)), p2.run_crawler(RSSNewsCrawler(FEED)))
    assert s1.emitted + s2.emitted == 2  # two distinct articles, each processed by exactly one worker
    assert (await repo.count_by_type())["NEWS"] == 2


async def test_sheets_exporter_batches_and_retries_quota(repo: Repository, mocked) -> None:  # type: ignore[no-untyped-def]
    from src.exports.tabs import Tab

    tabs = [Tab(t, ["a", "b"], [[i, f"https://x.com/{i}"] for i in range(5)]) for t in
            ("Startups", "Products", "Research Papers", "Jobs", "News", "Entity Mapping Log")]
    base = "https://sheets.googleapis.com/v4/spreadsheets"
    mocked.post(base, payload={"spreadsheetId": "SID"})
    mocked.post(re.compile(rf"{base}/SID/values/.*:clear"), payload={}, repeat=True)
    mocked.post(f"{base}/SID/values:batchUpdate", status=429, headers={"Retry-After": "1"})
    mocked.post(f"{base}/SID/values:batchUpdate", payload={}, repeat=True)
    sleeps = Sleeps()
    ex = GoogleSheetsExporter("tok", batch_rows=2, sleep=sleeps)
    url = await ex.export(tabs, spreadsheet_id=None, title="T")
    assert url == "https://docs.google.com/spreadsheets/d/SID" and sleeps.calls == [1.0]
    create = next(v for k, v in mocked.requests.items() if k[0] == "POST" and str(k[1]) == base)[0]
    assert len(create.kwargs["json"]["sheets"]) == 6  # exactly six tabs
    writes = [c for k, v in mocked.requests.items() if str(k[1]).endswith("values:batchUpdate") for c in v]
    assert len(writes) == 6 * 3 + 1  # 6 rows (header+5) per tab in batches of 2 = 3 writes per tab, +1 retried 429
    assert json.dumps(writes[-1].kwargs["json"]).count("https://x.com/") >= 1


async def test_api_endpoints(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from src.config import settings as cfg

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("API_READ_ONLY", "false")  # empty fresh DB: the server must create the tables itself
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    cfg.get_settings.cache_clear()
    from src.api.app import app

    with TestClient(app) as c:
        assert c.get("/health").json() == {"status": "ok"}
        assert c.get("/stats").json()["records"] == {}
        assert c.get("/records/STARTUP").json() == []
        assert "aip_http_requests_total" in c.get("/metrics").text or c.get("/metrics").status_code == 200
        assert "AI Intelligence Ingestion" in c.get("/").text
        assert c.post("/crawl/nope").status_code == 503  # crawl trigger is admin-only; disabled without ADMIN_API_KEY
    cfg.get_settings.cache_clear()


async def test_source_with_no_output_and_errors_is_never_reported_ok(repo: Repository, settings: Settings, http, mocked) -> None:  # type: ignore[no-untyped-def]
    import aiohttp

    mocked.get(re.compile(r"https://api\.lever\.co/.*"), exception=aiohttp.ClientHttpProxyError(request_info=None, history=(), status=403, message="x"), repeat=True)  # type: ignore[arg-type]
    from src.crawlers.jobs.boards import LeverCrawler

    s = await IngestionPipeline(repo, settings=settings, http=http, clock=lambda: NOW).run_crawler(LeverCrawler(companies={"a": "A", "b": "B"}))
    assert s.status == "INACCESSIBLE" and s.inserted == 0
    runs = await repo.fetch_runs()
    assert runs[-1]["status"] == "INACCESSIBLE" and "denied" in runs[-1]["error"]


async def test_refresh_stars_updates_cached_counts(tmp_path, monkeypatch, mocked) -> None:  # type: ignore[no-untyped-def]
    from src.config import settings as cfg
    from src.core.schemas import ResearchPaperRecord
    from src.database.session import Database
    from src.main import run_refresh_stars

    url = f"sqlite+aiosqlite:///{tmp_path}/s.db"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("RESPECT_ROBOTS_TXT", "false")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    cfg.get_settings.cache_clear()
    db = Database(url)
    await db.create_all()
    rec = ResearchPaperRecord.model_validate({"source": {"name": "Hugging Face Papers", "url": "https://huggingface.co/papers/2611.00001"},
        "provenance": {"extractionMethod": "STRUCTURED_API", "validationStatus": "PARTIAL"},
        "content": {"title": "Paper P", "authors": ["A"], "paper_url": "https://arxiv.org/abs/2611.00001", "published_date": NOW,
                    "arxiv_id": "2611.00001", "github_url": "https://github.com/a/p", "github_stars": 10, "github_stars_source": "huggingface_papers",
                    "github_stars_checked_at": NOW}})
    await Repository(db).bulk_upsert([rec], priority=10)
    mocked.get("https://api.github.com/repos/a/p", payload={"html_url": "https://github.com/a/p", "full_name": "a/p", "stargazers_count": 999})
    out = await run_refresh_stars()
    assert out["updated_live"] == 1 and out["token"] is False
    c = (await Repository(db).fetch_records("RESEARCH_PAPER"))[0]["content"]
    assert (c["github_stars"], c["github_stars_source"]) == (999, "github_api")
    await db.dispose()
    cfg.get_settings.cache_clear()
