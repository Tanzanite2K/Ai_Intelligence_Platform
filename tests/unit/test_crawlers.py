"""Crawlers against mocked APIs (synthetic fixtures shaped like the real responses)."""

from __future__ import annotations

import re

import pytest

from src.core.dedup import InMemoryDeduplicator
from src.core.schemas import PricingModel
from src.crawlers.base import CrawlContext, SourceStatus
from src.crawlers.jobs.boards import AshbyCrawler, GreenhouseCrawler, LeverCrawler, RemoteOKCrawler
from src.crawlers.jobs.taxonomy import is_ai_related, remote_from_text, role_family
from src.crawlers.news.rss import FeedSource, RSSNewsCrawler
from src.crawlers.products.huggingface import HuggingFaceProductCrawler
from src.crawlers.products.producthunt import ProductHuntCrawler
from src.crawlers.research.crawler import ResearchPaperCrawler
from src.crawlers.research.github import find_github_urls, normalize_github_url
from src.crawlers.startups.yc import YCStartupCrawler
from tests.conftest import HOURS, LONG_ARTICLE, NOW, arxiv_atom, iso, rfc2822, rss


async def collect(crawler, ctx):  # type: ignore[no-untyped-def]
    return [r async for r in crawler.crawl(ctx)]


def ctx_for(http, settings, **kw):  # type: ignore[no-untyped-def]
    return CrawlContext(http=http, settings=settings, now=NOW, extras={"dedup": InMemoryDeduplicator()}, **kw)


# --------------------------------------------------------------- startups
async def test_yc_crawler_maps_fields_and_dedups(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    company = {"id": 1, "name": "Acme AI", "slug": "acme-ai", "website": "acme.ai", "one_liner": "Agents for X", "team_size": 12,
               "all_locations": "SF", "tags": ["Artificial Intelligence"], "batch": "W24", "url": "https://www.ycombinator.com/companies/acme-ai"}
    eps = ["https://yc-oss.github.io/api/tags/a.json", "https://yc-oss.github.io/api/tags/b.json"]
    mocked.get(eps[0], payload=[company, {"id": 2, "name": "", "slug": "x"}])
    mocked.get(eps[1], payload=[company])  # same company in two tags -> once
    c = YCStartupCrawler(endpoints=eps, use_algolia=False)
    recs = await collect(c, ctx_for(http, settings))
    assert len(recs) == 1
    d = recs[0].to_canonical()
    assert d["source"]["url"] == "https://www.ycombinator.com/companies/acme-ai"
    assert d["content"]["entityName"] == "Acme AI" and d["content"]["data"]["employeeCount"] == 12
    assert d["content"]["website"] == "https://acme.ai"
    assert c.stats.rejected["missing_name_or_slug"] == 1


async def test_yc_unreachable_reports_inaccessible(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get("https://yc-oss.github.io/api/tags/a.json", status=404)
    mocked.get("https://www.ycombinator.com/companies", status=403, body="<title>Just a moment...</title>")
    c = YCStartupCrawler(endpoints=["https://yc-oss.github.io/api/tags/a.json"])
    assert await collect(c, ctx_for(http, settings)) == []
    assert c.stats.status is SourceStatus.INACCESSIBLE


# --------------------------------------------------------------- products
async def test_hf_products_org_only_and_license_pricing(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get(re.compile(r"https://huggingface\.co/api/models\?.*"), payload=[
        {"id": "mistralai/Mistral-7B", "author": "mistralai", "tags": ["license:apache-2.0", "text-generation"], "pipeline_tag": "text-generation"},
        {"id": "someuser/cool-model", "author": "someuser", "tags": ["license:mit"]},
        {"id": "mistralai/Secret", "author": "mistralai", "tags": ["license:other"]},
    ])
    mocked.get("https://huggingface.co/api/organizations/mistralai/overview", payload={"name": "mistralai", "fullname": "Mistral AI_"})
    mocked.get("https://huggingface.co/api/organizations/someuser/overview", status=404)
    c = HuggingFaceProductCrawler(kinds=("models",), pages=1)
    recs = await collect(c, ctx_for(http, settings))
    assert [r.content.product_name for r in recs] == ["Mistral-7B", "Secret"]
    assert recs[0].content.pricing_model is PricingModel.FREE and "apache-2.0" in recs[0].provenance.evidence["pricingModel"]
    assert recs[1].content.pricing_model is None  # no evidence -> not guessed
    assert c.stats.rejected["author_not_organization"] == 1


async def test_producthunt_skipped_without_token(http, settings) -> None:  # type: ignore[no-untyped-def]
    c = ProductHuntCrawler()
    assert await collect(c, ctx_for(http, settings)) == []
    assert c.stats.status is SourceStatus.SKIPPED


# --------------------------------------------------------------- research
def test_github_url_normalisation() -> None:
    assert normalize_github_url("https://github.com/org/repo/tree/main/src") == "https://github.com/org/repo"
    assert normalize_github_url("https://github.com/org/repo.git") == "https://github.com/org/repo"
    assert normalize_github_url("https://github.com/topics/llm") is None
    assert find_github_urls("Code: https://github.com/a/b. Also https://github.com/a/b") == ["https://github.com/a/b"]


async def test_research_correlates_repo_and_live_stars(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get(re.compile(r"https://huggingface\.co/api/daily_papers\?date=2026-11-21.*"), payload=[
        {"paper": {"id": "2611.00001", "title": "Paper One", "authors": [{"name": "Ada L"}], "publishedAt": "2026-11-20T00:00:00.000Z",
                   "githubRepo": "https://github.com/ada/one", "githubStars": 10}},
    ])
    mocked.get(re.compile(r"https://huggingface\.co/api/daily_papers\?date=2026-11-20.*"), payload=[])
    mocked.get(re.compile(r"https://export\.arxiv\.org/api/query\?.*start=0.*"), body=arxiv_atom([
        {"id": "2611.00002", "title": "Paper Two", "authors": ["B. C", "D. E"], "published": "2026-11-19T18:00:00Z", "comment": "Code at https://github.com/bc/two"},
        {"id": "2611.00003", "title": "No Code Paper", "authors": ["F"], "published": "2026-11-19T17:00:00Z"},
        {"id": "2611.00001", "title": "Paper One", "authors": ["Ada L"], "published": "2026-11-20T00:00:00Z"},  # duplicate of HF
    ]))
    mocked.get(re.compile(r"https://export\.arxiv\.org/api/query\?.*start=200.*"), body=arxiv_atom([]))
    mocked.get("https://huggingface.co/api/papers/2611.00003", status=404)
    mocked.get("https://api.github.com/repos/ada/one", payload={"html_url": "https://github.com/ada/one", "full_name": "ada/one", "stargazers_count": 321},
               headers={"X-RateLimit-Remaining": "4999", "X-RateLimit-Reset": "1999999999", "ETag": '"abc"'})
    mocked.get("https://api.github.com/repos/bc/two", payload={"html_url": "https://github.com/bc/two", "full_name": "bc/two", "stargazers_count": 7})
    c = ResearchPaperCrawler(hf_days=2, arxiv_max=400)
    recs = await collect(c, ctx_for(http, settings))
    by_id = {r.content.arxiv_id: r for r in recs}
    assert set(by_id) == {"2611.00001", "2611.00002"}
    one = by_id["2611.00001"].content
    assert one.github_stars == 321 and one.github_stars_source == "github_api"  # live, not HF's cached 10
    two = by_id["2611.00002"].content
    assert two.github_url == "https://github.com/bc/two" and two.authors == ["B. C", "D. E"]
    assert "arxiv:comment" in by_id["2611.00002"].provenance.evidence["github_url"]
    assert c.stats.rejected["no_github_repo"] == 1


async def test_research_github_rate_limit_falls_back_to_cached_and_labels_it(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get(re.compile(r"https://huggingface\.co/api/daily_papers.*"), payload=[
        {"paper": {"id": "2611.00009", "title": "Paper Nine", "authors": [{"name": "Z"}], "publishedAt": "2026-11-20T00:00:00.000Z",
                   "githubRepo": "https://github.com/z/nine", "githubStars": 42}}])
    mocked.get(re.compile(r"https://export\.arxiv\.org/.*"), body=arxiv_atom([]))
    mocked.get("https://api.github.com/repos/z/nine", status=403, body='{"message":"API rate limit exceeded"}',
               headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(NOW.timestamp()) + 99999)})
    c = ResearchPaperCrawler(hf_days=1, arxiv_max=200)
    recs = await collect(c, ctx_for(http, settings))
    assert len(recs) == 1 and recs[0].content.github_stars == 42 and recs[0].content.github_stars_source == "huggingface_papers"
    assert recs[0].provenance.validation_status.value == "PARTIAL"
    assert any("rate limit" in e for e in c.stats.errors)


async def test_research_dead_repo_dropped(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get(re.compile(r"https://huggingface\.co/api/daily_papers.*"), payload=[
        {"paper": {"id": "2611.00010", "title": "Ten", "authors": [{"name": "Z"}], "publishedAt": "2026-11-20T00:00:00Z", "githubRepo": "https://github.com/gone/away"}}])
    mocked.get(re.compile(r"https://export\.arxiv\.org/.*"), body=arxiv_atom([]))
    mocked.get("https://api.github.com/repos/gone/away", status=404)
    c = ResearchPaperCrawler(hf_days=1, arxiv_max=200)
    assert await collect(c, ctx_for(http, settings)) == []
    assert c.stats.rejected["github_repo_not_found"] == 1


# ------------------------------------------------------------------- news
FEED = FeedSource("test_feed", "Test News", "https://news.example.com/feed")


async def test_news_freshness_fulltext_and_date_fallback(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get(FEED.feed_url, body=rss([
        {"title": "Fresh with content", "link": "https://news.example.com/a?utm_source=rss", "pub": rfc2822(NOW - 2 * HOURS), "content": LONG_ARTICLE},
        {"title": "Stale story", "link": "https://news.example.com/b", "pub": rfc2822(NOW - 30 * HOURS)},
        {"title": "Undated but page has JSON-LD", "link": "https://news.example.com/c"},
        {"title": "Undated everywhere", "link": "https://news.example.com/d"},
    ]))
    mocked.get("https://news.example.com/c", body=f'<script type="application/ld+json">{{"datePublished":"{iso(NOW - 5 * HOURS)}"}}</script><article>{LONG_ARTICLE}</article>')
    mocked.get("https://news.example.com/d", body=f"<article>{LONG_ARTICLE}</article>")
    c = RSSNewsCrawler(FEED)
    recs = await collect(c, ctx_for(http, settings))
    by_title = {r.content.title: r.content for r in recs}
    assert "Stale story" not in by_title and c.stats.rejected["freshness:stale"] == 1
    a = by_title["Fresh with content"]
    assert a.date_verified and a.article_url == "https://news.example.com/a" and a.word_count and a.word_count > 200
    cc = by_title["Undated but page has JSON-LD"]
    assert cc.date_verified and cc.published_date == NOW - 5 * HOURS and cc.date_source.startswith("jsonld")
    d = by_title["Undated everywhere"]
    assert d.date_verified is False and d.published_date is None  # kept, but excluded from guaranteed-fresh export
    assert c.freshness_counts == {"FRESH": 2, "STALE": 1, "UNVERIFIED": 1}


async def test_news_heuristic_second_run(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    """Undated item that appears above previously-seen items after a recent successful run is vouched fresh."""
    mocked.get(FEED.feed_url, body=rss([{"title": "Brand new undated", "link": "https://news.example.com/new"},
                                         {"title": "Old undated", "link": "https://news.example.com/old"}]))
    mocked.get("https://news.example.com/new", body="<p>x</p>")
    mocked.get("https://news.example.com/old", body="<p>x</p>")
    cp = {"last_success_at": (NOW - 3 * HOURS).isoformat(), "listing": ["https://news.example.com/old"]}
    c = RSSNewsCrawler(FEED)
    recs = await collect(c, ctx_for(http, settings, checkpoint=cp))
    srcs = {r.content.title: r.content.date_source for r in recs}
    assert srcs["Brand new undated"].startswith("heuristic:new-since-last-run")
    assert not srcs["Old undated"].startswith("heuristic")


async def test_news_duplicate_claim_across_workers(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    body = rss([{"title": "Shared", "link": "https://news.example.com/s", "pub": rfc2822(NOW - HOURS), "content": LONG_ARTICLE}])
    mocked.get(FEED.feed_url, body=body)
    mocked.get(FEED.feed_url, body=body)
    dedup = InMemoryDeduplicator()
    c1, c2 = RSSNewsCrawler(FEED), RSSNewsCrawler(FEED)
    r1 = await collect(c1, CrawlContext(http=http, settings=settings, now=NOW, extras={"dedup": dedup}))
    r2 = await collect(c2, CrawlContext(http=http, settings=settings, now=NOW, extras={"dedup": dedup}))
    assert len(r1) == 1 and r2 == [] and c2.stats.rejected["duplicate_claimed_elsewhere"] == 1


async def test_blocked_feed_reported(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get(FEED.feed_url, status=403, body="<html>captcha-delivery.com</html>")
    c = RSSNewsCrawler(FEED)
    assert await collect(c, ctx_for(http, settings)) == []
    assert c.stats.status is SourceStatus.INACCESSIBLE


# ------------------------------------------------------------------- jobs
async def test_greenhouse_uses_first_published_not_updated(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get(re.compile(r"https://boards-api\.greenhouse\.io/v1/boards/acme/jobs.*"), payload={"jobs": [
        {"id": 1, "title": "Research Engineer, Pretraining", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
         "first_published": iso(NOW - 3 * HOURS), "updated_at": iso(NOW - HOURS), "location": {"name": "San Francisco, CA"},
         "metadata": [{"name": "Location Type", "value": "On-Site"}], "company_name": "Acme AI"},
        {"id": 2, "title": "Account Executive", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/2",
         "first_published": iso(NOW - 400 * HOURS), "updated_at": iso(NOW - HOURS), "location": {"name": "Remote, US"}},
    ]})
    c = GreenhouseCrawler(boards={"acme": "Acme AI"})
    recs = await collect(c, ctx_for(http, settings))
    assert len(recs) == 1  # edited-but-old posting is NOT treated as fresh
    j = recs[0].content
    assert j.date == NOW - 3 * HOURS and j.role_family == "Research" and j.is_remote is False and j.company == "Acme AI"


async def test_ashby_and_lever(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get("https://api.ashbyhq.com/posting-api/job-board/openai", payload={"jobs": [
        {"id": "x", "title": "Software Engineer, Inference", "jobUrl": "https://jobs.ashbyhq.com/openai/x", "publishedAt": iso(NOW - HOURS),
         "isRemote": True, "isListed": True, "department": "Applied AI", "location": "San Francisco"},
        {"id": "y", "title": "Hidden", "jobUrl": "https://jobs.ashbyhq.com/openai/y", "publishedAt": iso(NOW), "isListed": False}]})
    mocked.get(re.compile(r"https://api\.lever\.co/v0/postings/mistral.*"), payload=[
        {"id": "l1", "text": "Solutions Architect", "hostedUrl": "https://jobs.lever.co/mistral/l1", "createdAt": int((NOW - 2 * HOURS).timestamp() * 1000),
         "workplaceType": "remote", "categories": {"location": "Paris", "department": "Sales"}}])
    a = await collect(AshbyCrawler(orgs={"openai": "OpenAI"}), ctx_for(http, settings))
    lv = await collect(LeverCrawler(companies={"mistral": "Mistral AI"}), ctx_for(http, settings))
    assert len(a) == 1 and a[0].content.is_remote is True and a[0].content.role_family == "Engineering"
    assert lv[0].content.role_family == "Sales" and lv[0].content.date == (NOW - 2 * HOURS).replace(microsecond=0)


async def test_remoteok_ai_filter(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get("https://remoteok.com/api", payload=[
        {"legal": "notice"},
        {"id": 1, "position": "Machine Learning Engineer", "company": "Foo", "epoch": int((NOW - HOURS).timestamp()), "url": "https://remoteok.com/remote-jobs/1", "tags": ["python"]},
        {"id": 2, "position": "Bookkeeper", "company": "Bar", "epoch": int((NOW - HOURS).timestamp()), "url": "https://remoteok.com/remote-jobs/2", "tags": ["finance"]},
    ])
    c = RemoteOKCrawler()
    recs = await collect(c, ctx_for(http, settings))
    assert [r.content.title for r in recs] == ["Machine Learning Engineer"] and c.stats.rejected["not_ai_related"] == 1


@pytest.mark.parametrize("title,fam", [("Research Engineer", "Research"), ("Senior Software Engineer", "Engineering"), ("Data Scientist", "Data"),
                                       ("Solutions Engineer", "Sales"), ("Product Manager, API", "Product"), ("Recruiter", "People"),
                                       ("Product Designer", "Design"), ("Chief of Staff", "Operations")])
def test_role_family(title: str, fam: str) -> None:
    assert role_family(title) == fam


def test_remote_and_ai_detection() -> None:
    assert remote_from_text("Remote - US") is True and remote_from_text("On-Site") is False and remote_from_text("Paris") is None
    assert is_ai_related("Senior LLM Engineer") and not is_ai_related("Barista")



async def test_hf_token_header_and_org_cache_persisted(tmp_path, mocked) -> None:  # type: ignore[no-untyped-def]
    from src.config.settings import Settings
    from src.core.http import HttpClient

    s = Settings(_env_file=None, database_url="sqlite+aiosqlite://", respect_robots_txt=False, default_domain_rps=1000,  # type: ignore[call-arg]
                 hf_token="hf_secret123", hf_org_cache_path=str(tmp_path / "c.json"))
    mocked.get(re.compile(r"https://huggingface\.co/api/models\?.*"), payload=[{"id": "acme/m1", "author": "acme", "tags": []}], repeat=True)
    mocked.get("https://huggingface.co/api/organizations/acme/overview", payload={"fullname": "Acme"})  # only ONCE
    async with HttpClient(s) as http:
        for _ in range(2):  # second run must use the on-disk cache (the mock would fail a second call)
            c = HuggingFaceProductCrawler(kinds=("models",), pages=1)
            recs = await collect(c, CrawlContext(http=http, settings=s, now=NOW, extras={}))
            assert len(recs) == 1
    sent = [r for k, v in mocked.requests.items() for r in v if "huggingface.co" in str(k[1])]
    assert all(r.kwargs["headers"]["Authorization"] == "Bearer hf_secret123" for r in sent)
    assert c.next_checkpoint["next_url:models"] is None  # completed run resets the resume cursor


async def test_hf_resumes_from_checkpoint_cursor(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    resume = "https://huggingface.co/api/models?cursor=PAGE7&limit=1000"
    mocked.get(resume, payload=[], headers={})
    c = HuggingFaceProductCrawler(kinds=("models",), pages=1)
    await collect(c, ctx_for(http, settings, checkpoint={"next_url:models": resume}))
    assert any(str(k[1]) == resume for k in mocked.requests)


async def test_producthunt_graphql_errors_are_not_success(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    from pydantic import SecretStr

    s = settings.model_copy(update={"producthunt_token": SecretStr("tok")})
    mocked.post("https://api.producthunt.com/v2/api/graphql", payload={"errors": [{"message": "invalid_oauth_token"}]})
    c = ProductHuntCrawler(max_pages=1)
    assert await collect(c, CrawlContext(http=http, settings=s, now=NOW, extras={})) == []
    assert c.stats.errors and "graphql errors" in c.stats.errors[0]


async def test_producthunt_maps_posts_and_paginates(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    from pydantic import SecretStr

    s = settings.model_copy(update={"producthunt_token": SecretStr("tok")})
    page = lambda i, nxt: {"data": {"posts": {"pageInfo": {"hasNextPage": nxt, "endCursor": f"c{i}"}, "edges": [  # noqa: E731
        {"node": {"id": str(i), "name": f"Tool {i}", "tagline": "AI notes", "description": "Free plan available", "url": f"https://www.producthunt.com/posts/tool-{i}",
                  "website": f"https://tool{i}.example"}}]}}}
    mocked.post("https://api.producthunt.com/v2/api/graphql", payload=page(1, True))
    mocked.post("https://api.producthunt.com/v2/api/graphql", payload=page(2, False))
    c = ProductHuntCrawler(max_pages=5)
    recs = await collect(c, CrawlContext(http=http, settings=s, now=NOW, extras={}))
    assert [r.content.product_name for r in recs] == ["Tool 1", "Tool 2"] and c.next_checkpoint["cursor:artificial-intelligence"] == "c2"
    assert recs[0].source.url == "https://www.producthunt.com/posts/tool-1" and recs[0].content.pricing_model is None
