"""Rate-limit-aware GitHub REST client for repository metrics.

* ETag conditional requests (``If-None-Match``) – 304s are free for
  authenticated clients and avoid re-downloading unchanged repos.
* Reads ``X-RateLimit-Remaining``/``Reset``: sleeps until reset when the wait
  is short, otherwise raises ``GitHubRateLimited`` so callers can fall back
  (and report) instead of hammering the API.
* Bounded concurrency; follows renamed-repo redirects; 404 -> ``None``
  (repository does not exist — we never fabricate a star count).
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from src.core.http import FetchError, HttpClient
from src.monitoring.logging import get_logger

log = get_logger(__name__)

API = "https://api.github.com"
_GH_RE = re.compile(r"https?://(?:www\.)?github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100})", re.I)
_NON_REPO_OWNERS = {"topics", "features", "orgs", "sponsors", "marketplace", "about", "collections", "settings", "login", "apps", "site", "pricing", "readme"}


def normalize_github_url(url: str | None) -> str | None:
    """Return ``https://github.com/{owner}/{repo}`` or None for non-repo links."""
    if not url:
        return None
    m = _GH_RE.search(url)
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    repo = re.sub(r"\.git$", "", repo).rstrip(".,;:)")
    if owner.lower() in _NON_REPO_OWNERS or not repo:
        return None
    return f"https://github.com/{owner}/{repo}"


def find_github_urls(text: str | None) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    for m in _GH_RE.finditer(text):
        u = normalize_github_url(m.group(0))
        if u and u not in out:
            out.append(u)
    return out


class GitHubRateLimited(RuntimeError):
    def __init__(self, reset_at: float, *, kind: str = "primary", limit: int | None = None) -> None:
        quota = f", {limit}/h quota" + (" - unauthenticated; set a valid GITHUB_TOKEN for 5000/h" if limit == 60 else "") if limit else ""
        super().__init__(f"GitHub {kind} rate limit exhausted until {datetime.fromtimestamp(reset_at, UTC).isoformat()}{quota}")
        self.reset_at, self.kind, self.limit = reset_at, kind, limit


class GitHubAuthError(RuntimeError):
    """The configured GITHUB_TOKEN was rejected (revoked, expired or mistyped)."""


@dataclass(slots=True)
class RateLimitStatus:
    authenticated: bool
    limit: int
    remaining: int
    reset_at: float


@dataclass(slots=True)
class RepoMetrics:
    url: str
    full_name: str
    stars: int
    checked_at: datetime


class GitHubClient:
    def __init__(self, http: HttpClient, token: str | None = None, *, concurrency: int = 8, max_wait_s: float = 90.0, reserve: int = 2) -> None:
        self.http = http
        self.token = token
        self._sem = asyncio.Semaphore(concurrency)
        self._etags: dict[str, tuple[str, RepoMetrics | None]] = {}
        self.max_wait_s, self.reserve = max_wait_s, reserve
        self.remaining: int | None = None
        self.limit: int | None = None
        self.reset_at: float = 0.0
        self.calls = 0
        self._rl_lock = asyncio.Lock()

    def _headers(self, etag: str | None) -> dict[str, str]:
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if etag:
            h["If-None-Match"] = etag
        return h

    async def _respect_limit(self) -> None:
        async with self._rl_lock:
            if self.remaining is not None and self.remaining <= self.reserve:
                wait = self.reset_at - time.time()
                if wait > self.max_wait_s:
                    raise GitHubRateLimited(self.reset_at, limit=self.limit)
                if wait > 0:
                    log.info("github.rate_limit_sleep", seconds=round(wait, 1))
                    await asyncio.sleep(wait + 1)
                self.remaining = None

    def _update_limit(self, headers: dict[str, str]) -> None:
        h = {k.lower(): v for k, v in headers.items()}
        if "x-ratelimit-remaining" in h:
            self.remaining = int(h["x-ratelimit-remaining"])
        if "x-ratelimit-reset" in h:
            self.reset_at = float(h["x-ratelimit-reset"])
        if "x-ratelimit-limit" in h:
            self.limit = int(h["x-ratelimit-limit"])

    async def rate_limit_status(self) -> RateLimitStatus:
        """GET /rate_limit (does not count against the quota). Raises GitHubAuthError for a rejected token."""
        res = await self.http.request("GET", f"{API}/rate_limit", headers=self._headers(None), check_robots=False,
                                      accept_status=frozenset({401, 403}), max_retries=1)
        if res.status == 401:
            raise GitHubAuthError("GITHUB_TOKEN rejected by GitHub (HTTP 401 Bad credentials): create a new token and update .env")
        core = res.json().get("resources", {}).get("core", {}) if res.status == 200 else {}
        self._update_limit(res.headers)
        st = RateLimitStatus(authenticated=bool(self.token) and int(core.get("limit", 0)) > 60, limit=int(core.get("limit", self.limit or 0)),
                             remaining=int(core.get("remaining", self.remaining or 0)), reset_at=float(core.get("reset", self.reset_at or 0)))
        self.limit, self.remaining, self.reset_at = st.limit, st.remaining, st.reset_at
        return st

    async def repo_metrics(self, repo_url: str) -> RepoMetrics | None:
        norm = normalize_github_url(repo_url)
        if not norm:
            return None
        owner_repo = norm.split("github.com/", 1)[1]
        etag, cached = self._etags.get(owner_repo, (None, None))
        for attempt in range(2):
            async with self._sem:
                await self._respect_limit()
                self.calls += 1
                res = await self.http.request(
                    "GET", f"{API}/repos/{owner_repo}", headers=self._headers(etag), check_robots=False,
                    accept_status=frozenset({304, 403, 404, 410, 429, 451}), max_retries=3,
                )
            self._update_limit(res.headers)
            if res.status not in (403, 429):
                break
            body = res.text.lower()
            if "secondary rate limit" in body or (res.status == 429 and self.remaining != 0):
                # abuse-protection limit: GitHub says how long to pause (Retry-After), independent of the hourly quota
                wait = float(res.headers.get("Retry-After") or res.headers.get("retry-after") or 60)
                if attempt == 0 and wait <= self.max_wait_s:
                    log.info("github.secondary_limit_sleep", seconds=wait)
                    await asyncio.sleep(wait)
                    continue
                raise GitHubRateLimited(time.time() + wait, kind="secondary", limit=self.limit)
            if self.remaining == 0 or "rate limit" in body:
                raise GitHubRateLimited(self.reset_at or time.time() + 3600, limit=self.limit)
            raise FetchError("GitHub 403", url=repo_url, status=403)
        if res.status == 304 and cached:
            return RepoMetrics(cached.url, cached.full_name, cached.stars, datetime.now(UTC))
        if res.status in (404, 410, 451):
            self._etags[owner_repo] = ("", None)
            return None
        data = res.json()
        m = RepoMetrics(
            url=data.get("html_url") or norm,
            full_name=data.get("full_name") or owner_repo,
            stars=int(data.get("stargazers_count", 0)),
            checked_at=datetime.now(UTC),
        )
        if res.headers.get("ETag") or res.headers.get("etag"):
            self._etags[owner_repo] = (res.headers.get("ETag") or res.headers.get("etag"), m)  # type: ignore[assignment]
        return m
