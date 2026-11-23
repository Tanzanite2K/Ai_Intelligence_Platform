"""HTTP client: retries, Retry-After, anti-bot detection, robots.txt, circuit breaker, backoff maths."""

import asyncio
import random
import re
import time

import pytest

from src.config.settings import Settings
from src.core.http import FetchError, HttpClient, RobotsDisallowedError, SourceBlockedError
from src.core.ratelimit import CircuitBreaker, CircuitOpenError, CircuitState, TokenBucket
from src.core.retry import backoff_delay, parse_retry_after

URL = "https://api.example.com/items"


async def test_retries_5xx_then_succeeds(http, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get(URL, status=503)
    mocked.get(URL, status=200, payload={"ok": True})
    assert await http.get_json(URL) == {"ok": True}


async def test_429_honours_retry_after(http, mocked, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)
        await real_sleep(0)

    monkeypatch.setattr("src.core.http.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("src.core.ratelimit.asyncio.sleep", fake_sleep)
    mocked.get(URL, status=429, headers={"Retry-After": "7"})
    mocked.get(URL, status=200, payload=[1])
    assert await http.get_json(URL) == [1]
    assert 7 in sleeps


async def test_cloudflare_challenge_reported_not_evaded(http, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get(URL, status=403, body="<html><title>Just a moment...</title><div id='challenge-platform'></div></html>")
    with pytest.raises(SourceBlockedError, match="anti-bot"):
        await http.get_text(URL)


async def test_cf_mitigated_header(http, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get(URL, status=200, headers={"cf-mitigated": "challenge"}, body="ok")
    with pytest.raises(SourceBlockedError):
        await http.get_text(URL)


async def test_robots_disallow(tmp_path, mocked) -> None:  # type: ignore[no-untyped-def]
    s = Settings(_env_file=None, database_url="sqlite+aiosqlite://", respect_robots_txt=True, default_domain_rps=1000)
    mocked.get("https://blocked.example.com/robots.txt", status=200, body="User-agent: *\nDisallow: /private")
    mocked.get("https://blocked.example.com/public", status=200, body="hello")
    async with HttpClient(s) as c:
        assert (await c.get_text("https://blocked.example.com/public")).text == "hello"
        with pytest.raises(RobotsDisallowedError):
            await c.get_text("https://blocked.example.com/private/x")


async def test_circuit_opens_after_failures(mocked) -> None:  # type: ignore[no-untyped-def]
    s = Settings(_env_file=None, database_url="sqlite+aiosqlite://", respect_robots_txt=False, http_max_retries=0, circuit_breaker_threshold=2, default_domain_rps=1000)
    pat = re.compile(r"https://down\.example\.com/.*")
    mocked.get(pat, status=404, repeat=True)
    async with HttpClient(s) as c:
        for _ in range(2):
            with pytest.raises(FetchError):
                await c.get_text("https://down.example.com/a")
        with pytest.raises(CircuitOpenError):
            await c.get_text("https://down.example.com/b")


def test_circuit_breaker_half_open() -> None:
    t = [0.0]
    cb = CircuitBreaker(threshold=1, cooldown_s=10, clock=lambda: t[0])
    cb.record_failure()
    assert cb.state is CircuitState.OPEN and not cb.allow()
    t[0] = 11
    assert cb.allow() and cb.state is CircuitState.HALF_OPEN
    cb.record_success()
    assert cb.state is CircuitState.CLOSED


def test_backoff_full_jitter_bounds() -> None:
    rng = random.Random(42)
    for attempt in range(8):
        d = backoff_delay(attempt, base=1.0, cap=30.0, rng=rng)
        assert 0.25 <= d <= min(30.0, 2**attempt)


def test_retry_after_parsing() -> None:
    assert parse_retry_after("12") == 12
    assert parse_retry_after("9999") == 120  # capped
    assert parse_retry_after(None) is None
    assert parse_retry_after("garbage") is None
    assert parse_retry_after("Wed, 21 Oct 2099 07:28:00 GMT") == 120


async def test_token_bucket_rate() -> None:
    b = TokenBucket(rate_per_s=50, burst=1)
    t0 = time.monotonic()
    for _ in range(6):
        await b.acquire()
    assert time.monotonic() - t0 >= 0.09  # 5 waits of 20 ms


async def test_proxy_denial_is_blocked_not_retried(http, mocked) -> None:  # type: ignore[no-untyped-def]
    import aiohttp

    mocked.get(URL, exception=aiohttp.ClientHttpProxyError(request_info=None, history=(), status=403, message="Forbidden"))  # type: ignore[arg-type]
    with pytest.raises(SourceBlockedError, match="proxy denied"):
        await http.get_text(URL)
    assert len(mocked.requests[("GET", __import__("yarl").URL(URL))]) == 1  # no retries on a policy denial
