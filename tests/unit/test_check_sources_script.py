"""Regression tests for scripts/check_sources.py (read-only live diagnostic).

2026-11-22: the VentureBeat and Ars Technica checks crashed with
``TypeError: result() got multiple values for argument 'status'`` before recording any HTTP outcome.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from src.config.settings import Settings
from src.core.dedup import InMemoryDeduplicator
from src.core.http import HttpClient
from src.crawlers.base import CrawlContext
from tests.conftest import HOURS, LONG_ARTICLE, NOW, rfc2822, rss

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("check_sources", ROOT / "scripts" / "check_sources.py")
cs = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(cs)  # type: ignore[union-attr]

VB_FEED = "https://venturebeat.com/feed/"
ARS_FEED = "https://arstechnica.com/ai/feed/"
FEED_BODY = rss([{"title": "Fresh story", "link": "https://venturebeat.com/orchestration/x/", "pub": rfc2822(NOW - HOURS), "content": LONG_ARTICLE},
                 {"title": "Older story", "link": "https://venturebeat.com/technology/y/", "pub": rfc2822(NOW - 72 * HOURS), "content": LONG_ARTICLE}])


def ctx_for(http, settings):  # type: ignore[no-untyped-def]
    return CrawlContext(http=http, settings=settings, extras={"dedup": InMemoryDeduplicator()})


def urls(mocked) -> list[str]:  # type: ignore[no-untyped-def]
    return [str(k[1]) for k in mocked.requests]


def test_result_accepts_a_status_detail_key() -> None:
    r = cs.result("PASS", status="OK", http_status=200)  # this raised TypeError before the fix
    assert r == {"verdict": "PASS", "status": "OK", "http_status": 200}


# --------------------------------------------------------------- VentureBeat
async def test_venturebeat_pass_reports_actual_response(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get("https://venturebeat.com/robots.txt", body="User-agent: *\nDisallow: /*?s=\n")
    mocked.get(VB_FEED, body=FEED_BODY, headers={"Content-Type": "application/rss+xml; charset=UTF-8"})
    r = await cs.check_feed(http, "venturebeat_ai", ctx_for(http, settings))
    assert r["verdict"] == "PASS" and r["crawler_status"] == "OK" and r["feed_items"] == 2
    fr = r["feed_response"]
    assert fr["http_status"] == 200 and fr["content_type"].startswith("application/rss+xml") and "<rss" in fr["body_excerpt"]
    assert r["robots_http_status"] == 200 and r["project_allows"] is True
    assert r["requests_made"] == ["https://venturebeat.com/robots.txt", VB_FEED]  # exactly one feed request, no articles


async def test_venturebeat_429_is_a_fail_with_real_status(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get("https://venturebeat.com/robots.txt", body="User-agent: *\n")
    mocked.get(VB_FEED, status=429, headers={"Retry-After": "3600"}, repeat=True)
    r = await cs.check_feed(http, "venturebeat_ai", ctx_for(http, settings))
    assert r["verdict"] == "RATE LIMITED" and r["crawler_status"] == "FAILED"
    assert r["feed_response"]["http_status"] == 429 and r["feed_response"]["retry_after_s"] == 3600
    assert any("HTTP 429" in e for e in r["errors"]) and r["cooldown_until"]


async def test_feed_200_without_items_is_not_a_pass(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get("https://venturebeat.com/robots.txt", status=404)
    mocked.get(VB_FEED, body="<html><body>Just a moment...</body></html>")
    r = await cs.check_feed(http, "venturebeat_ai", ctx_for(http, settings))
    assert r["verdict"] == "FAIL" and r["feed_response"]["http_status"] == 200 and r["feed_items"] == 0


# --------------------------------------------------------------- Ars Technica
async def test_ars_reports_feed_and_failing_article_status(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get("https://arstechnica.com/robots.txt", body="User-agent: *\nDisallow: /search/\n")
    mocked.get(ARS_FEED, body=FEED_BODY)
    mocked.get(cs.ARS_FAILING_ARTICLE, status=404, body="<html><title>Page not found</title></html>")
    r = await cs.check_ars(http, ctx_for(http, settings), probe_article=True)
    assert r["verdict"] == "PASS" and r["feed_response"]["http_status"] == 200
    assert r["failing_article"]["http_status"] == 404 and "Page not found" in r["failing_article"]["body_excerpt"]


async def test_ars_feed_error_is_reported_not_raised(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get("https://arstechnica.com/robots.txt", status=403, body="denied")  # anti-bot answer on robots.txt
    mocked.get(ARS_FEED, status=503, repeat=True)
    mocked.get(cs.ARS_FAILING_ARTICLE, status=503, repeat=True)
    r = await cs.check_ars(http, ctx_for(http, settings), probe_article=True)
    assert r["verdict"] == "FAIL" and r["feed_response"]["http_status"] == 503
    assert r["robots_http_status"] == 403 and "failing_article" not in r  # no article probe after a failed feed
    assert not any("meta-reconsiders" in str(k[1]) for k in mocked.requests)


# --------------------------------------------------------------- The Decoder: never contacted
async def test_the_decoder_disabled_makes_no_request_at_all(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    r = await cs.check_feed(http, "the_decoder", ctx_for(http, settings))
    assert r["verdict"] == "DISABLED (manual)" and "robots.txt" in r["reason"] and r["requests_made"] == []
    assert urls(mocked) == []  # neither the feed nor robots.txt


# --------------------------------------------------------------- Product Hunt: credential status kept
async def test_producthunt_still_credential_required(mocked) -> None:  # type: ignore[no-untyped-def]
    s = Settings(_env_file=None, database_url="sqlite+aiosqlite://", respect_robots_txt=False, default_domain_rps=1000, producthunt_token="x" * 43)  # type: ignore[call-arg]
    mocked.post("https://api.producthunt.com/v2/api/graphql", status=401, payload={"errors": [{"error": "invalid_oauth_token", "error_description": "Please supply a valid access token."}]})
    async with HttpClient(s) as h:
        r = await cs.check_producthunt(h, s)
    assert r["verdict"] == "CREDENTIAL REQUIRED" and r["http_status"] == 401 and "invalid_oauth_token" in r["detail"] and "x" * 43 not in str(r)


@pytest.mark.parametrize("name", cs.SOURCES)
def test_every_source_has_a_check(name: str) -> None:
    assert name in {"venturebeat_ai", "producthunt", "the_decoder", "arstechnica_ai", "research_papers", "yc_startups"}
