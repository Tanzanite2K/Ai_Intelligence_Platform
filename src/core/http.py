"""Resilient async HTTP client used by every crawler.

Features: shared connection pool (``TCPConnector`` limits), global concurrency
semaphore (backpressure), per-domain token buckets, per-domain circuit
breakers, retries with exponential back-off + full jitter, ``Retry-After``
honouring, robots.txt enforcement, and explicit detection of anti-bot
challenges. A challenged/blocked source raises ``SourceBlockedError`` so the
pipeline *reports* it as inaccessible instead of pretending to extract data.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from src.config.settings import Settings, get_settings
from src.core.ratelimit import CircuitOpenError, CircuitRegistry, DomainLimiters
from src.core.retry import backoff_delay, parse_retry_after
from src.core.urls import domain_of
from src.monitoring.logging import get_logger
from src.monitoring.metrics import CIRCUIT_OPEN, HTTP_LATENCY, HTTP_REQUESTS

log = get_logger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 524}
_CHALLENGE_MARKERS = (
    "cf-browser-verification",
    "challenge-platform",
    "Just a moment...",
    "Attention Required! | Cloudflare",
    "captcha-delivery.com",  # DataDome
    "datadome",
    "px-captcha",  # PerimeterX
    "Please enable JS and disable any ad blocker",
)


class FetchError(RuntimeError):
    def __init__(self, message: str, *, url: str, status: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.url, self.status, self.retry_after = url, status, retry_after

    @property
    def rate_limited(self) -> bool:
        return self.status == 429


class SourceBlockedError(FetchError):
    """Anti-bot challenge or hard 401/403 — do NOT try to evade; report and move on."""


class RobotsDisallowedError(FetchError):
    pass


class CircuitOpenFetchError(FetchError, CircuitOpenError):
    """Raised instead of calling a domain whose breaker is open (catchable as either type)."""


@dataclass(slots=True)
class FetchResult:
    url: str
    final_url: str
    status: int
    text: str
    headers: dict[str, str]
    elapsed_s: float

    def json(self) -> Any:
        import json

        return json.loads(self.text)


def detect_challenge(status: int, headers: dict[str, str], body: str) -> str | None:
    h = {k.lower(): v for k, v in headers.items()}
    if h.get("cf-mitigated") == "challenge":
        return "cloudflare-challenge"
    if "x-datadome" in h or "x-dd-b" in h:
        if status in (401, 403, 405):
            return "datadome-block"
    if status in (403, 429, 503):
        head = body[:5000]
        for m in _CHALLENGE_MARKERS:
            if m.lower() in head.lower():
                return f"challenge:{m}"
    return None


@dataclass(slots=True)
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow, pattern)


class RobotsRules:
    """robots.txt per RFC 9309: product-token group selection, ``*`` / ``$`` wildcards, longest match wins.

    The stdlib ``urllib.robotparser`` treats ``*`` literally, so a rule such as ``Disallow: */feed/``
    (The Decoder) was silently ignored and the crawler requested a disallowed URL.
    """

    def __init__(self, lines: list[str]) -> None:
        self.groups: list[_Group] = []
        cur: _Group | None = None
        in_agents = False
        for raw in lines:
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, val = (x.strip() for x in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if cur is None or not in_agents:
                    cur = _Group()
                    self.groups.append(cur)
                cur.agents.append(val.lower())
                in_agents = True
            elif key in ("allow", "disallow") and cur is not None:
                in_agents = False
                if val:  # an empty Disallow means "allow everything" and adds no rule
                    cur.rules.append((key == "allow", val))
            else:  # crawl-delay, sitemap, ...: end the run of user-agent lines
                in_agents = False

    @staticmethod
    def _regex(pattern: str) -> re.Pattern[str]:
        anchored = pattern.endswith("$")
        body = re.escape(pattern[:-1] if anchored else pattern).replace(r"\*", ".*")
        return re.compile(body + ("$" if anchored else ""))

    def _rules_for(self, user_agent: str) -> list[tuple[bool, str]]:
        token = user_agent.split("/", 1)[0].split()[0].lower() if user_agent.strip() else "*"
        named = [g for g in self.groups if token in g.agents]
        chosen = named or [g for g in self.groups if "*" in g.agents]
        return [r for g in chosen for r in g.rules]

    def can_fetch(self, user_agent: str, url: str) -> bool:
        from urllib.parse import urlsplit

        p = urlsplit(url)
        path = (p.path or "/") + (f"?{p.query}" if p.query else "")
        if path == "/robots.txt":
            return True
        best: tuple[int, bool] | None = None  # (pattern length, allow)
        for allow, pat in self._rules_for(user_agent):
            if self._regex(pat).match(path):
                cand = (len(pat), allow)
                if best is None or cand[0] > best[0] or (cand[0] == best[0] and allow):
                    best = cand
        return True if best is None else best[1]


class RobotsCache:
    def __init__(self, client: HttpClient, ttl_s: float = 3600) -> None:
        self._client = client
        self._ttl = ttl_s
        self._cache: dict[str, tuple[float, RobotsRules | None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def allowed(self, url: str, user_agent: str) -> bool:
        from urllib.parse import urlsplit

        p = urlsplit(url)
        origin = f"{p.scheme}://{p.netloc}"
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            cached = self._cache.get(origin)
            parser = cached[1] if cached else None
            if cached is None or time.monotonic() - cached[0] > self._ttl:
                parser = await self._load(origin)
                self._cache[origin] = (time.monotonic(), parser)
        return True if parser is None else parser.can_fetch(user_agent, url)

    async def _load(self, origin: str) -> RobotsRules | None:
        try:
            res = await self._client.request("GET", origin + "/robots.txt", check_robots=False, max_retries=1, raise_for_status=False)
        except FetchError:
            return None  # unreachable robots.txt -> treat as no restrictions (RFC 9309 §2.3.1.3 for 4xx)
        if res.status >= 500:
            return RobotsRules(["User-agent: *", "Disallow: /"])  # RFC 9309: 5xx => assume complete disallow
        if res.status >= 400:
            return None
        return RobotsRules(res.text.splitlines())


class HttpClient:
    def __init__(self, settings: Settings | None = None, *, domain_rps: dict[str, float] | None = None) -> None:
        self.s = settings or get_settings()
        self._session: aiohttp.ClientSession | None = None
        self._sem = asyncio.Semaphore(self.s.http_max_connections)
        self.limiters = DomainLimiters(self.s.default_domain_rps, domain_rps)
        self.breakers = CircuitRegistry(self.s.circuit_breaker_threshold, self.s.circuit_breaker_cooldown_s)
        self.robots = RobotsCache(self)
        self.domain_headers: dict[str, dict[str, str]] = {}
        if self.s.hf_token:
            self.domain_headers["huggingface.co"] = {"Authorization": f"Bearer {self.s.hf_token.get_secret_value()}"}

    async def __aenter__(self) -> HttpClient:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def start(self) -> None:
        if self._session is None:
            connector = aiohttp.TCPConnector(limit=self.s.http_max_connections, limit_per_host=self.s.http_max_per_host, ttl_dns_cache=300)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=self.s.http_timeout_s),
                headers={"User-Agent": self.s.user_agent, "Accept-Encoding": "gzip, deflate"},
                trust_env=True,  # honour HTTPS_PROXY / corporate proxies
            )

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        check_robots: bool = True,
        max_retries: int | None = None,
        raise_for_status: bool = True,
        accept_status: frozenset[int] = frozenset(),
    ) -> FetchResult:
        """``accept_status``: statuses returned to the caller untouched (no retry/raise),
        e.g. GitHub's 403 rate-limit or 304 Not Modified."""
        await self.start()
        assert self._session is not None
        domain = domain_of(url)
        breaker = self.breakers.get(domain)
        if not breaker.allow():
            CIRCUIT_OPEN.labels(domain).set(1)
            raise CircuitOpenFetchError(f"circuit open for {domain}", url=url)
        if check_robots and self.s.respect_robots_txt and not await self.robots.allowed(url, self.s.user_agent):
            raise RobotsDisallowedError("disallowed by robots.txt", url=url)

        for host, extra in self.domain_headers.items():
            if domain == host or domain.endswith("." + host):
                headers = {**extra, **(headers or {})}
        retries = self.s.http_max_retries if max_retries is None else max_retries
        last_exc: Exception | None = None
        seen_429 = 0
        for attempt in range(retries + 1):
            await self.limiters.get(domain).acquire()
            t0 = time.monotonic()
            try:
                async with self._sem, self._session.request(method, url, params=params, headers=headers, json=json_body) as resp:
                    body = await resp.text(errors="replace")
                    hdrs = dict(resp.headers)
                    elapsed = time.monotonic() - t0
                    HTTP_REQUESTS.labels(domain, str(resp.status)).inc()
                    HTTP_LATENCY.labels(domain).observe(elapsed)
                    if resp.status in accept_status:
                        return FetchResult(url, str(resp.url), resp.status, body, hdrs, elapsed)
                    challenge = detect_challenge(resp.status, hdrs, body)
                    if challenge:
                        breaker.record_failure()
                        raise SourceBlockedError(f"anti-bot protection detected ({challenge})", url=url, status=resp.status)
                    if resp.status in (401, 403):
                        breaker.record_failure()
                        raise SourceBlockedError(f"access denied HTTP {resp.status}", url=url, status=resp.status)
                    asked = parse_retry_after(hdrs.get("Retry-After"), cap=86_400) if resp.status in (429, 503) else None
                    seen_429 += resp.status == 429
                    may_retry = resp.status != 429 or seen_429 <= self.s.http_max_429_retries
                    if resp.status in RETRYABLE_STATUS and attempt < retries and may_retry:
                        if asked is not None and asked > self.s.http_max_retry_after_s:
                            # the source asks for a long pause: stop now and let the scheduler cool the source down
                            breaker.record_failure()
                            raise FetchError(f"HTTP {resp.status} (rate limited; Retry-After {round(asked)}s)", url=url, status=resp.status, retry_after=asked)
                        delay = asked if asked is not None else backoff_delay(attempt)
                        if resp.status == 429:
                            self.limiters.get(domain).penalize(delay)
                        log.warning("http.retry", url=url, status=resp.status, attempt=attempt, delay=round(delay, 2))
                        await asyncio.sleep(delay)
                        continue
                    if raise_for_status and resp.status >= 400:
                        breaker.record_failure()
                        why = f" (rate limited after {attempt + 1} attempts)" if resp.status == 429 else ""
                        raise FetchError(f"HTTP {resp.status}{why}", url=url, status=resp.status, retry_after=asked)
                    breaker.record_success()
                    CIRCUIT_OPEN.labels(domain).set(0)
                    return FetchResult(url, str(resp.url), resp.status, body, hdrs, elapsed)
            except aiohttp.ClientHttpProxyError as e:
                # egress proxy / firewall policy denial: not transient, do not retry
                breaker.record_failure()
                raise SourceBlockedError(f"egress proxy denied access (HTTP {e.status})", url=url, status=e.status) from e
            except (TimeoutError, aiohttp.ClientError) as e:
                last_exc = e
                HTTP_REQUESTS.labels(domain, "error").inc()
                if attempt < retries:
                    delay = backoff_delay(attempt)
                    log.warning("http.network_retry", url=url, error=type(e).__name__, attempt=attempt, delay=round(delay, 2))
                    await asyncio.sleep(delay)
                    continue
                breaker.record_failure()
        detail = f"{type(last_exc).__name__}: {str(last_exc)[:160]}" if last_exc else "unknown"
        raise FetchError(f"network failure after {retries + 1} attempts ({detail})", url=url)

    async def get_text(self, url: str, **kw: Any) -> FetchResult:
        return await self.request("GET", url, **kw)

    async def get_json(self, url: str, **kw: Any) -> Any:
        return (await self.request("GET", url, **kw)).json()
