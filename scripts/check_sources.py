"""Read-only live health check for the six sources fixed on 2026-11-22. Never writes to the database.

Makes a handful of polite requests per source with the project's HTTP client (robots.txt, rate limits and
Retry-After are respected; nothing is retried against a rejected credential) and writes a JSON report to
``docs/source_check_<UTC stamp>.json``. Secret values are never printed or written.

    python scripts/check_sources.py                    # all six
    python scripts/check_sources.py --only producthunt
    python scripts/check_sources.py --force            # ignore the cooldown / rejected-token memory below

Repeat runs do not waste requests: after an HTTP 429 a source is put on the same escalating cooldown the
scheduler uses (15 min, 30 min, 1 h, ... 24 h, or the source's Retry-After), and a Product Hunt token that was
rejected is not sent again until it changes. That memory is kept in ``data/source_check_state.json`` (no secrets:
only a one-way token fingerprint and timestamps).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.robotparser
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config.settings import get_settings  # noqa: E402
from src.core.dedup import InMemoryDeduplicator  # noqa: E402
from src.core.http import FetchError, FetchResult, HttpClient, RobotsRules  # noqa: E402
from src.crawlers.base import CrawlContext  # noqa: E402
from src.crawlers.news.rss import NEWS_SOURCES, RSSNewsCrawler  # noqa: E402
from src.crawlers.products.producthunt import API as PH_API  # noqa: E402
from src.crawlers.products.producthunt import auth_error_code, auth_error_message, token_fingerprint  # noqa: E402
from src.crawlers.registry import DOMAIN_RPS  # noqa: E402
from src.crawlers.research.github import GitHubAuthError, GitHubClient, GitHubRateLimited  # noqa: E402
from src.crawlers.startups.yc import YC_OSS_ENDPOINTS, YCStartupCrawler  # noqa: E402
from src.monitoring.logging import redact  # noqa: E402

ARS_FAILING_ARTICLE = "https://arstechnica.com/ai/2026/11/meta-reconsiders-2030-net-zero-goal-amid-record-emissions-from-ai-data-centers/"
GITHUB_PROBE_REPO = "https://github.com/huggingface/transformers"
STATE_PATH = ROOT / "data" / "source_check_state.json"
SOURCES = ("venturebeat_ai", "producthunt", "the_decoder", "arstechnica_ai", "research_papers", "yc_startups")


def feed(key: str):  # type: ignore[no-untyped-def]
    return next(s for s in NEWS_SOURCES if s.key == key)


def result(verdict: str, /, **kw: Any) -> dict[str, Any]:
    """Report entry. ``verdict`` is positional-only so no detail key (e.g. a crawler ``status``) can collide with it."""
    return {"verdict": verdict, **{k: (redact(v) if isinstance(v, str) else v) for k, v in kw.items()}}


def _snippet(text: str, n: int = 200) -> str:
    return redact(" ".join(text[:2000].split()))[:n]


class RecordingHttp:
    """Wraps the project's HttpClient and records every exchange (status, headers, excerpt) for the report.

    The crawler is run against this wrapper, so the report shows the *actual* HTTP response the crawler got,
    without a second request. Errors are recorded with their status and re-raised unchanged.
    """

    def __init__(self, http: HttpClient) -> None:
        self._http = http
        self.log: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._http, name)

    async def request(self, method: str, url: str, **kw: Any) -> FetchResult:
        entry: dict[str, Any] = {"method": method, "url": url}
        self.log.append(entry)
        try:
            res = await self._http.request(method, url, **kw)
        except FetchError as e:
            entry.update(http_status=e.status, error=f"{type(e).__name__}: {e}", retry_after_s=e.retry_after)
            raise
        h = {k.lower(): v for k, v in res.headers.items()}
        entry.update(http_status=res.status, final_url=res.final_url if res.final_url != url else None, content_type=h.get("content-type"),
                     bytes=len(res.text), retry_after=h.get("retry-after"), elapsed_s=round(res.elapsed_s, 2), body_excerpt=_snippet(res.text))
        return res

    async def get_text(self, url: str, **kw: Any) -> FetchResult:
        return await self.request("GET", url, **kw)

    async def get_json(self, url: str, **kw: Any) -> Any:
        return (await self.request("GET", url, **kw)).json()

    def for_url(self, url: str) -> dict[str, Any] | None:
        return next((e for e in self.log if e["url"] == url), None)


async def robots_verdicts(http: RecordingHttp, url: str, ua: str) -> dict[str, Any]:
    """Compare the project parser with the stdlib one, so a behaviour change is visible before it matters."""
    from urllib.parse import urlsplit

    p = urlsplit(url)
    robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
    try:
        res = await http.request("GET", robots_url, check_robots=False, raise_for_status=False, max_retries=1)
    except FetchError as e:  # e.g. a 403 anti-bot answer: report it, do not abort the check
        return {"robots_http_status": e.status, "robots_error": f"{type(e).__name__}: {e}"}
    if res.status >= 400:
        return {"robots_http_status": res.status, "project_allows": res.status < 500, "stdlib_allows": res.status < 500}
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(res.text.splitlines())
    return {"robots_http_status": res.status, "project_allows": RobotsRules(res.text.splitlines()).can_fetch(ua, url),
            "stdlib_allows": rp.can_fetch(ua, url)}


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _cooling(entry: dict[str, Any], now: datetime) -> datetime | None:
    try:
        until = datetime.fromisoformat(entry["cooldown_until"]) if entry.get("cooldown_until") else None
    except ValueError:
        return None
    return until if until and until > now else None


async def check_feed(http: HttpClient, key: str, ctx: CrawlContext, state: dict[str, Any] | None = None, force: bool = False) -> dict[str, Any]:
    src = feed(key)
    if src.disabled_reason:  # never contacted: not the feed, not robots.txt
        return result("DISABLED (manual)", feed_url=src.feed_url, reason=src.disabled_reason, requests_made=[])
    st = (state if state is not None else {}).setdefault(key, {})
    until = _cooling(st, ctx.now)
    if until and not force:
        return result("RATE LIMITED (cooling down)", feed_url=src.feed_url, cooldown_until=until.isoformat(), strikes=st.get("strikes"),
                      last_http_status=st.get("last_http_status"), last_attempt_at=st.get("last_attempt_at"), requests_made=[],
                      note="not contacted: the source answered HTTP 429 recently; run with --force to override")
    ctx.checkpoint = {"rate_limit_strikes": st.get("strikes", 0)}  # escalate the cooldown across diagnostic runs too
    rec = RecordingHttp(http)
    robots = await robots_verdicts(rec, src.feed_url, ctx.settings.user_agent)
    ctx.http = rec  # type: ignore[assignment]
    c = RSSNewsCrawler(src, fetch_full_text=False)  # feed only: no article pages are requested here
    recs = [r async for r in c.crawl(ctx)]
    feed_resp = rec.for_url(src.feed_url)
    out = {"feed_url": src.feed_url, **robots, "feed_response": feed_resp, "crawler_status": c.stats.status.value,
           "feed_items": c.stats.fetched, "fresh_items_24h": len(recs), "errors": c.stats.errors[:3],
           "cooldown_until": c.next_checkpoint.get("cooldown_until"), "requests_made": [e["url"] for e in rec.log]}
    ok = bool(feed_resp) and feed_resp.get("http_status") == 200 and c.stats.fetched > 0
    status = (feed_resp or {}).get("http_status")
    st.update(last_attempt_at=ctx.now.isoformat(), last_http_status=status)
    if status == 429:
        st.update(strikes=c.next_checkpoint.get("rate_limit_strikes"), cooldown_until=c.next_checkpoint.get("cooldown_until"))
        return result("RATE LIMITED", **out)
    if ok:
        st.pop("strikes", None)
        st.pop("cooldown_until", None)
    return result("PASS" if ok else "FAIL", **out)


async def check_ars(http: HttpClient, ctx: CrawlContext, state: dict[str, Any] | None = None, force: bool = False,
                    probe_article: bool = False) -> dict[str, Any]:
    out = await check_feed(http, "arstechnica_ai", ctx, state, force)
    if not probe_article or out["verdict"] != "PASS":
        # 2026-11-22: that page answered HTTP 405 "Human Verification" (bot challenge). Not re-requested by default.
        return out
    rec = RecordingHttp(http)
    try:  # the one article page that failed on every run: request it once and record the exact status
        await rec.request("GET", ARS_FAILING_ARTICLE, raise_for_status=False, max_retries=1)
    except FetchError:
        pass  # recorded with its status
    art = rec.log[0]
    out["failing_article"] = art
    out["requests_made"] = [*out.get("requests_made", []), ARS_FAILING_ARTICLE]
    return out


async def check_producthunt(http: HttpClient, settings: Any, state: dict[str, Any] | None = None, force: bool = False) -> dict[str, Any]:
    if not settings.producthunt_token:
        return result("SKIPPED", detail="PRODUCTHUNT_TOKEN not set")
    tok = settings.producthunt_token.get_secret_value()
    fp = token_fingerprint(tok)
    st = (state if state is not None else {}).setdefault("producthunt", {})
    if st.get("rejected_fingerprint") == fp and not force:
        return result("CREDENTIAL REQUIRED (token unchanged, not re-sent)", rejected_at=st.get("rejected_at"), http_status=st.get("http_status"),
                      code=st.get("code"), requests_made=[], detail=f"PRODUCTHUNT_TOKEN was rejected and has not changed since; {auth_help()}")
    headers = {"Authorization": f"Bearer {tok}", "Accept": "application/json", "Content-Type": "application/json"}
    try:
        res = await http.request("POST", PH_API, headers=headers, json_body={"query": "{ posts(first: 1) { edges { node { id name } } } }"},
                                 check_robots=False, accept_status=frozenset({400, 401, 403}), max_retries=1)
    except FetchError as e:
        return result("FAIL", detail=f"{type(e).__name__}: {e}", token_length=len(tok))
    if res.status in (401, 403):
        st.update(rejected_fingerprint=fp, rejected_at=datetime.now(UTC).isoformat(), http_status=res.status, code=auth_error_code(res)[0])
        return result("CREDENTIAL REQUIRED", http_status=res.status, detail=auth_error_message(res), token_length=len(tok))
    st.clear()  # the token is accepted
    body = res.json()
    if body.get("errors"):
        return result("FAIL", http_status=res.status, detail=str(body["errors"])[:300])
    n = len(((body.get("data") or {}).get("posts") or {}).get("edges") or [])
    return result("PASS", http_status=res.status, posts_returned=n)


def auth_help() -> str:
    from src.crawlers.products.producthunt import TOKEN_HELP

    return f"action required: {TOKEN_HELP}; then run this check again"


async def check_github(http: HttpClient, settings: Any) -> dict[str, Any]:
    tok = settings.github_token.get_secret_value() if settings.github_token else None
    gh = GitHubClient(http, tok)
    try:
        st = await gh.rate_limit_status()
    except GitHubAuthError as e:
        return result("CREDENTIAL REQUIRED", detail=str(e))
    out: dict[str, Any] = {"token_configured": bool(tok), "authenticated": st.authenticated, "limit_per_hour": st.limit, "remaining": st.remaining,
                           "resets_at": datetime.fromtimestamp(st.reset_at, UTC).isoformat()}
    try:
        m = await gh.repo_metrics(GITHUB_PROBE_REPO)
        out["probe_repo"] = {"repo": m.full_name, "stars": m.stars} if m else {"repo": GITHUB_PROBE_REPO, "found": False}
    except GitHubRateLimited as e:
        return result("RATE LIMITED (temporary)", **out, detail=str(e))
    verdict = "PASS" if st.authenticated and st.remaining > 0 else "DEGRADED (unauthenticated)" if st.remaining > 0 else "RATE LIMITED (temporary)"
    return result(verdict, **out)


async def check_yc(http: HttpClient, ctx: CrawlContext) -> dict[str, Any]:
    eps = []
    for u in YC_OSS_ENDPOINTS:
        try:
            res = await http.request("HEAD", u, raise_for_status=False, max_retries=1)
            eps.append({"url": u, "status": res.status})
        except FetchError as e:
            eps.append({"url": u, "status": e.status, "error": type(e).__name__})
    c = YCStartupCrawler(endpoints=YC_OSS_ENDPOINTS[:1], use_algolia=False)  # parse one real listing in memory
    ctx.limit = 5
    recs = [r async for r in c.crawl(ctx)]
    ok = all(e["status"] == 200 for e in eps) and len(recs) > 0
    return result("PASS" if ok else "FAIL", endpoints=eps, parsed_sample=[r.content.entity_name for r in recs], parser_status=c.stats.status.value,
                  errors=c.stats.errors[:3])


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", choices=SOURCES, action="append")
    ap.add_argument("--out", default=None)
    ap.add_argument("--force", action="store_true", help="contact sources even during a cooldown / with a rejected token")
    ap.add_argument("--probe-ars-article", action="store_true", help="also request the Ars article page that answered 405")
    a = ap.parse_args()
    state = load_state()
    s = get_settings()
    wanted = a.only or list(SOURCES)
    report: dict[str, Any] = {"checked_at": datetime.now(UTC).isoformat(), "database_written": False, "sources": {}}
    async with HttpClient(s, domain_rps=DOMAIN_RPS) as http:
        def ctx() -> CrawlContext:
            return CrawlContext(http=http, settings=s, extras={"dedup": InMemoryDeduplicator()})

        jobs = {
            "venturebeat_ai": lambda: check_feed(http, "venturebeat_ai", ctx(), state, a.force),
            "the_decoder": lambda: check_feed(http, "the_decoder", ctx(), state, a.force),
            "arstechnica_ai": lambda: check_ars(http, ctx(), state, a.force, a.probe_ars_article),
            "producthunt": lambda: check_producthunt(http, s, state, a.force),
            "research_papers": lambda: check_github(http, s),
            "yc_startups": lambda: check_yc(http, ctx()),
        }
        for name in wanted:
            try:
                report["sources"][name] = await jobs[name]()
            except Exception as e:  # report, never crash the whole check
                report["sources"][name] = result("ERROR", detail=f"{type(e).__name__}: {e}")
            print(f"{name:16} {report['sources'][name]['verdict']}")
    out = Path(a.out) if a.out else ROOT / "docs" / f"source_check_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    save_state(state)
    print(f"\nreport: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
