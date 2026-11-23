"""Source-health fixes (2026-11-22): robots.txt wildcards, rate-limit cooldown, endpoint corrections, clearer failures.

All external APIs are mocked with responses shaped like the ones observed in the run history / live probes.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from src.config.settings import Settings
from src.core.dedup import InMemoryDeduplicator
from src.core.http import FetchError, HttpClient, RobotsDisallowedError, RobotsRules
from src.crawlers.base import COOLDOWN_CAP_S, CrawlContext, SourceStatus, cooldown_seconds
from src.crawlers.news.rss import NEWS_SOURCES, FeedSource, RSSNewsCrawler
from src.crawlers.products.producthunt import ProductHuntCrawler
from src.crawlers.registry import ACTION_REQUIRED, REGISTRY, specs_for
from src.crawlers.research.crawler import ResearchPaperCrawler
from src.crawlers.startups.yc import YC_OSS_ENDPOINTS as DEFAULT_ENDPOINTS
from src.database.models import SourceRunRow
from src.database.repository import Repository
from src.pipelines.ingestion import IngestionPipeline
from tests.conftest import HOURS, LONG_ARTICLE, NOW, arxiv_atom, rfc2822, rss

UA = "FrontierAtlasBot/1.0 (+https://github.com/<you>/ai-intelligence-platform; contact: you@example.com)"
# robots.txt rules as published by the sources on 2026-11-21 (relevant lines)
DECODER_ROBOTS = "User-agent: *\nDisallow: /wp-admin/\nDisallow: */embed/\nDisallow: /search/\nDisallow: */feed/\nDisallow: */feed/rss2/\nSitemap: https://the-decoder.com/sitemap_index.xml\n"
VB_ROBOTS = "User-agent: *\nDisallow: /*?s=\nDisallow: /search\nDisallow: /api/\n\nUser-agent: CCBot\nDisallow: /\n"


def ctx_for(http, settings, **kw):  # type: ignore[no-untyped-def]
    return CrawlContext(http=http, settings=settings, now=NOW, extras={"dedup": InMemoryDeduplicator()}, **kw)


async def collect(crawler, ctx):  # type: ignore[no-untyped-def]
    return [r async for r in crawler.crawl(ctx)]


def requested(mocked, pattern: str) -> int:  # type: ignore[no-untyped-def]
    return sum(len(v) for k, v in mocked.requests.items() if re.search(pattern, str(k[1])))


# ----------------------------------------------------------- robots.txt (RFC 9309)
@pytest.mark.parametrize(("url", "allowed"), [
    ("https://the-decoder.com/feed/", False),            # "*/feed/" -- ignored by urllib.robotparser
    ("https://the-decoder.com/ai-research/feed/", False),
    ("https://the-decoder.com/some-article-slug/", True),
    ("https://the-decoder.com/robots.txt", True),
])
def test_robots_wildcards_decoder(url: str, allowed: bool) -> None:
    assert RobotsRules(DECODER_ROBOTS.splitlines()).can_fetch(UA, url) is allowed


def test_wildcard_handling_does_not_depend_on_python_version(monkeypatch) -> None:
    """Root cause: urllib.robotparser before the recent CPython change (e.g. 3.12.16, 3.10) treats ``*`` literally, so
    the crawler requested The Decoder's disallowed feed and got HTTP 403. The project parser decides on its own."""
    import urllib.robotparser

    monkeypatch.setattr(urllib.robotparser, "RobotFileParser", None)  # prove it is not used
    assert RobotsRules(DECODER_ROBOTS.splitlines()).can_fetch(UA, "https://the-decoder.com/feed/") is False


@pytest.mark.parametrize(("url", "allowed"), [
    ("https://venturebeat.com/feed/", True), ("https://venturebeat.com/category/ai/feed/", True),
    ("https://venturebeat.com/?s=llm", False), ("https://venturebeat.com/api/x", False),
])
def test_robots_venturebeat_feed_allowed(url: str, allowed: bool) -> None:
    assert RobotsRules(VB_ROBOTS.splitlines()).can_fetch(UA, url) is allowed


def test_robots_group_selection_longest_match_and_anchor() -> None:
    r = RobotsRules("User-agent: FrontierAtlasBot\nAllow: /a/b\nDisallow: /a\n\nUser-agent: *\nDisallow: /\n".splitlines())
    assert r.can_fetch(UA, "https://x.io/a/b/c") and not r.can_fetch(UA, "https://x.io/a/c") and r.can_fetch(UA, "https://x.io/z")
    assert not r.can_fetch("OtherBot/2", "https://x.io/z")  # falls back to the * group
    e = RobotsRules("User-agent: *\nDisallow: /*.pdf$\nAllow: /p\nDisallow: /p\n".splitlines())
    assert not e.can_fetch(UA, "https://x.io/f.pdf") and e.can_fetch(UA, "https://x.io/f.pdf?x=1")
    assert e.can_fetch(UA, "https://x.io/p")  # equal length: allow wins
    assert RobotsRules("User-agent: *\nDisallow:\n".splitlines()).can_fetch(UA, "https://x.io/any")


async def test_http_client_enforces_wildcard_robots(mocked) -> None:  # type: ignore[no-untyped-def]
    s = Settings(_env_file=None, database_url="sqlite+aiosqlite://", respect_robots_txt=True, default_domain_rps=1000, user_agent=UA)  # type: ignore[call-arg]
    mocked.get("https://the-decoder.com/robots.txt", body=DECODER_ROBOTS)
    async with HttpClient(s) as http:
        with pytest.raises(RobotsDisallowedError):
            await http.get_text("https://the-decoder.com/feed/")
    assert requested(mocked, r"the-decoder\.com/feed/") == 0  # the disallowed URL is never requested


# ----------------------------------------------------------- the_decoder: disabled, never requested
def test_the_decoder_disabled_and_excluded_from_runs() -> None:
    src = next(s for s in NEWS_SOURCES if s.key == "the_decoder")
    assert src.disabled_reason and "robots.txt" in src.disabled_reason
    assert REGISTRY["the_decoder"].disabled_reason == src.disabled_reason
    assert "the_decoder" not in {s.name for s in specs_for()} and "the_decoder" not in {s.name for s in specs_for("news")}
    from src.pipelines.tasks import app

    assert not any("the_decoder" in k for k in app.conf.beat_schedule)


async def test_disabled_feed_makes_no_request(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    c = RSSNewsCrawler(FeedSource("x", "X", "https://x.example/feed/", disabled_reason="robots.txt disallows the feed"))
    assert await collect(c, ctx_for(http, settings)) == []
    assert c.stats.status is SourceStatus.SKIPPED and "disabled" in c.stats.errors[0]
    assert not mocked.requests


# ----------------------------------------------------------- venturebeat_ai: endpoint + 429 handling
def test_venturebeat_uses_live_site_feed() -> None:
    src = next(s for s in NEWS_SOURCES if s.key == "venturebeat_ai")
    assert src.feed_url == "https://venturebeat.com/feed/"


async def test_long_retry_after_is_not_slept_in_run(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get("https://vb.example/feed/", status=429, headers={"Retry-After": "3600"}, repeat=True)
    t0 = asyncio.get_running_loop().time()
    with pytest.raises(FetchError) as ei:
        await http.get_text("https://vb.example/feed/")
    assert ei.value.status == 429 and ei.value.retry_after == 3600 and ei.value.rate_limited
    assert requested(mocked, r"vb\.example/feed/") == 1 and asyncio.get_running_loop().time() - t0 < 5


async def test_short_retry_after_is_retried_then_succeeds(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get("https://vb.example/ok/", status=429, headers={"Retry-After": "0"})
    mocked.get("https://vb.example/ok/", body="fine")
    assert (await http.get_text("https://vb.example/ok/")).text == "fine"


async def test_feed_429_sets_cross_run_cooldown(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get("https://vb.example/feed/", status=429, headers={"Retry-After": "7200"}, repeat=True)
    c = RSSNewsCrawler(FeedSource("vb", "VB", "https://vb.example/feed/"))
    await collect(c, ctx_for(http, settings))
    assert c.stats.status is SourceStatus.FAILED and "HTTP 429" in c.stats.errors[0] and "next attempt after" in c.stats.errors[0]
    assert c.next_checkpoint["rate_limit_strikes"] == 1
    assert datetime.fromisoformat(c.next_checkpoint["cooldown_until"]) == NOW + timedelta(seconds=7200)


def test_cooldown_is_exponential_and_capped() -> None:
    assert [cooldown_seconds(n, None) for n in (1, 2, 3, 4)] == [900, 1800, 3600, 7200]
    assert cooldown_seconds(30, None) == COOLDOWN_CAP_S and cooldown_seconds(1, 10**9) == COOLDOWN_CAP_S
    assert cooldown_seconds(1, 5000) == 5000  # the source's own Retry-After wins when longer


async def test_pipeline_honours_cooldown_then_recovers(repo: Repository, settings, http, mocked) -> None:  # type: ignore[no-untyped-def]
    feed = FeedSource("vb_test", "VB", "https://vb.example/feed/")
    await repo.save_checkpoint("vb_test", {"cooldown_until": (NOW + HOURS).isoformat(), "rate_limit_strikes": 2}, success=False, items=0)
    pipe = IngestionPipeline(repo, settings=settings, http=http, dedup=InMemoryDeduplicator(), clock=lambda: NOW)
    s1 = await pipe.run_crawler(RSSNewsCrawler(feed))
    assert s1.status == "SKIPPED" and "cooldown until" in s1.errors[0] and not mocked.requests  # no request during cooldown
    mocked.get(feed.feed_url, body=rss([{"title": "Fresh", "link": "https://vb.example/a", "pub": rfc2822(NOW - HOURS), "content": LONG_ARTICLE}]))
    later = NOW + 2 * HOURS
    pipe2 = IngestionPipeline(repo, settings=settings, http=http, dedup=InMemoryDeduplicator(), clock=lambda: later)
    s2 = await pipe2.run_crawler(RSSNewsCrawler(feed))
    assert s2.status == "OK"
    cp = await repo.get_checkpoint("vb_test")
    assert "cooldown_until" not in cp and "rate_limit_strikes" not in cp  # back-off reset after a normal answer
    async with repo.db.session() as s:
        runs = (await s.execute(select(SourceRunRow).where(SourceRunRow.source == "vb_test").order_by(SourceRunRow.id))).scalars().all()
    assert [r.status for r in runs] == ["OK"]  # the skip during cooldown made no request and wrote no run row


# ----------------------------------------------------------- arstechnica_ai: article-level failures
ARS = FeedSource("ars_test", "Ars", "https://ars.example/feed/")


def ars_feed() -> str:
    return rss([
        {"title": "Gone article", "link": "https://ars.example/gone/", "desc": "Short summary.", "pub": rfc2822(NOW - HOURS)},
        {"title": "Second article", "link": "https://ars.example/second/", "desc": "Short summary.", "pub": rfc2822(NOW - 2 * HOURS)},
    ])


async def test_article_404_is_a_warning_and_record_kept(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get(ARS.feed_url, body=ars_feed())
    mocked.get("https://ars.example/gone/", status=404)
    mocked.get("https://ars.example/second/", body=f"<article><p>{LONG_ARTICLE}</p></article>")
    c = RSSNewsCrawler(ARS)
    recs = await collect(c, ctx_for(http, settings))
    assert len(recs) == 2 and c.stats.status is SourceStatus.OK
    gone = next(r for r in recs if r.source.url.startswith("https://ars.example/gone"))
    assert gone.provenance.evidence.get("fullTextAvailable") == "false"
    assert c.stats.errors == ["warning: article https://ars.example/gone: FetchError (HTTP 404); stored with feed summary, fullTextAvailable=false"]


async def test_article_5xx_keeps_status_in_message_and_is_partial(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get(ARS.feed_url, body=ars_feed())
    mocked.get("https://ars.example/gone/", status=503, repeat=True)
    mocked.get("https://ars.example/second/", body=f"<article><p>{LONG_ARTICLE}</p></article>")
    c = RSSNewsCrawler(ARS)
    await collect(c, ctx_for(http, settings))
    assert c.stats.status is SourceStatus.PARTIAL and "HTTP 503" in c.stats.errors[0]  # previously only "FetchError"


async def test_article_429_pauses_remaining_article_fetches(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get(ARS.feed_url, body=ars_feed())
    mocked.get("https://ars.example/gone/", status=429, headers={"Retry-After": "900"}, repeat=True)
    mocked.get("https://ars.example/second/", body="<p>never requested</p>")
    c = RSSNewsCrawler(ARS)
    recs = await collect(c, ctx_for(http, settings))
    assert len(recs) == 2 and c.stats.status is SourceStatus.PARTIAL and "skipped for this run" in c.stats.errors[0]
    assert requested(mocked, r"ars\.example/second/") == 0


# ----------------------------------------------------------- yc_startups: endpoint
def test_yc_endpoints_exclude_nonexistent_industry_file() -> None:
    assert not any("/industries/" in u for u in DEFAULT_ENDPOINTS)
    assert set(DEFAULT_ENDPOINTS) == {f"https://yc-oss.github.io/api/tags/{t}.json" for t in ("artificial-intelligence", "generative-ai", "machine-learning", "ai")}


# ----------------------------------------------------------- producthunt: credential failure reported precisely
async def test_producthunt_invalid_token_reports_api_error_code(http, mocked, tmp_path) -> None:  # type: ignore[no-untyped-def]
    secret = "ph_dummy_value_that_must_not_leak_0123456789"
    s = Settings(_env_file=None, database_url="sqlite+aiosqlite://", respect_robots_txt=False, default_domain_rps=1000, producthunt_token=secret)  # type: ignore[call-arg]
    mocked.post("https://api.producthunt.com/v2/api/graphql", status=401,
                payload={"errors": [{"error": "invalid_oauth_token", "error_description": "Please supply a valid access token. Refer to our api documentation"}]})
    async with HttpClient(s) as h:
        c = ProductHuntCrawler(max_pages=1)
        assert await collect(c, CrawlContext(http=h, settings=s, now=NOW, extras={})) == []
    msg = c.stats.errors[0]
    assert c.stats.status is SourceStatus.INACCESSIBLE and "invalid_oauth_token" in msg and "Developer Token" in msg and "HTTP 401" in msg
    assert secret not in msg and requested(mocked, "producthunt") == 1  # no retries against a rejected token
    assert "Developer Token" in ACTION_REQUIRED["producthunt"]


# ----------------------------------------------------------- research_papers: GitHub quota
def _papers(mocked, n: int = 3) -> None:  # type: ignore[no-untyped-def]
    mocked.get(re.compile(r"https://huggingface\.co/api/daily_papers.*"), payload=[
        {"paper": {"id": f"2611.0010{i}", "title": f"Paper {i}", "authors": [{"name": "Z"}], "publishedAt": "2026-11-20T00:00:00.000Z",
                   "githubRepo": f"https://github.com/z/p{i}"}} for i in range(n)])
    mocked.get(re.compile(r"https://export\.arxiv\.org/.*"), body=arxiv_atom([]), repeat=True)


def _rate(mocked, limit: int, remaining: int, status: int = 200) -> None:  # type: ignore[no-untyped-def]
    reset = int(NOW.timestamp()) + 3600
    mocked.get("https://api.github.com/rate_limit", status=status,
               payload={"resources": {"core": {"limit": limit, "remaining": remaining, "reset": reset}}} if status == 200 else {"message": "Bad credentials"})


async def test_github_exhausted_quota_detected_before_any_repo_call(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    _papers(mocked)
    _rate(mocked, 60, 0)  # the 2026-11-21 signature: 60/h quota, nothing left
    c = ResearchPaperCrawler(hf_days=1, arxiv_max=200)
    assert await collect(c, ctx_for(http, settings)) == []
    assert requested(mocked, r"api\.github\.com/repos/") == 0
    assert c.github_deferred == 3 and c.stats.rejected["github_rate_limited_deferred"] == 3
    joined = " | ".join(c.stats.errors)
    assert joined.count("rate limit exhausted") == 1 and "60/h" in joined and "GITHUB_TOKEN" in joined and "3 papers deferred" in joined


async def test_github_rate_limit_reported_once_with_concurrency(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    _papers(mocked, 6)
    _rate(mocked, 5000, 5)
    mocked.get(re.compile(r"https://api\.github\.com/repos/.*"), status=403, body='{"message":"API rate limit exceeded"}', repeat=True,
               headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Limit": "5000", "X-RateLimit-Reset": str(int(NOW.timestamp()) + 99999)})
    c = ResearchPaperCrawler(hf_days=1, arxiv_max=200)
    await collect(c, ctx_for(http, settings))
    assert sum("rate limit exhausted" in e for e in c.stats.errors) == 1 and c.github_deferred == 6  # previously repeated 5x


async def test_github_secondary_limit_waits_retry_after_and_recovers(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    _papers(mocked, 1)
    _rate(mocked, 5000, 4000)
    mocked.get("https://api.github.com/repos/z/p0", status=403, headers={"Retry-After": "0", "X-RateLimit-Remaining": "3999"},
               body='{"message":"You have exceeded a secondary rate limit. Please wait a few minutes before you try again."}')
    mocked.get("https://api.github.com/repos/z/p0", payload={"html_url": "https://github.com/z/p0", "full_name": "z/p0", "stargazers_count": 12})
    c = ResearchPaperCrawler(hf_days=1, arxiv_max=200)
    recs = await collect(c, ctx_for(http, settings))
    assert len(recs) == 1 and recs[0].content.github_stars == 12 and c.stats.status is SourceStatus.OK


async def test_github_rejected_token_is_named(http, mocked) -> None:  # type: ignore[no-untyped-def]
    s = Settings(_env_file=None, database_url="sqlite+aiosqlite://", respect_robots_txt=False, default_domain_rps=1000, github_token="ghp_dummy_revoked")  # type: ignore[call-arg]
    _papers(mocked, 1)
    _rate(mocked, 0, 0, status=401)
    mocked.get("https://api.github.com/repos/z/p0", status=401, repeat=True)
    async with HttpClient(s) as h:
        c = ResearchPaperCrawler(hf_days=1, arxiv_max=200)
        await collect(c, CrawlContext(http=h, settings=s, now=NOW, extras={"dedup": InMemoryDeduplicator()}))
    assert any("GITHUB_TOKEN rejected" in e for e in c.stats.errors) and not any("ghp_dummy" in e for e in c.stats.errors)


async def test_github_unauthenticated_is_flagged(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    settings = settings.model_copy(update={"github_token": None})
    _papers(mocked, 1)
    _rate(mocked, 60, 60)
    mocked.get("https://api.github.com/repos/z/p0", payload={"html_url": "https://github.com/z/p0", "full_name": "z/p0", "stargazers_count": 1})
    c = ResearchPaperCrawler(hf_days=1, arxiv_max=200)
    assert len(await collect(c, ctx_for(http, settings))) == 1
    assert c.stats.status is SourceStatus.OK and any("GITHUB_TOKEN not set" in e for e in c.stats.errors)


# ----------------------------------------------------------- Source Health API
async def test_source_health_reports_disabled_and_action_required(repo: Repository) -> None:  # type: ignore[no-untyped-def]
    from src.api.v1.operations import source_health_rows

    t = datetime.now(UTC)
    for src, st, err in [("the_decoder", "INACCESSIBLE", "feed inaccessible: access denied HTTP 403"),
                         ("producthunt", "INACCESSIBLE", "access denied HTTP 401"), ("techcrunch_ai", "OK", None)]:
        await repo.log_run(source=src, vertical="NEWS", started_at=t, finished_at=t, status=st, fetched=0, stored=0, duplicates=0, rejected=0, error=err)
    async with repo.db.session() as s:
        rows = {h.source: h for h in await source_health_rows(s)}
    assert rows["the_decoder"].disabled_reason and rows["the_decoder"].health == "DISABLED"
    assert rows["the_decoder"].health_detail == rows["the_decoder"].disabled_reason and rows["the_decoder"].action_required is None
    assert rows["producthunt"].disabled_reason is None and "Developer Token" in (rows["producthunt"].action_required or "")
    assert rows["techcrunch_ai"].disabled_reason is None and rows["techcrunch_ai"].action_required is None
