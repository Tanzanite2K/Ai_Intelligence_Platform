"""Source stabilization (2026-11-22): unavailable sources must not be contacted again and again.

Simulated responses only (aioresponses); the pipeline runs against a temporary test database.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from src.api.v1.operations import classify, latest_live_check, source_health_rows
from src.config.settings import Settings
from src.core.dedup import InMemoryDeduplicator
from src.core.http import FetchError, HttpClient
from src.crawlers.news.rss import NEWS_SOURCES
from src.crawlers.products.producthunt import ProductHuntCrawler, token_fingerprint
from src.crawlers.registry import REGISTRY, specs_for
from src.database.models import CrawlCheckpointRow, SourceRunRow
from src.database.repository import Repository
from src.pipelines.ingestion import IngestionPipeline
from tests.conftest import HOURS, LONG_ARTICLE, NOW, rfc2822, rss

VB = next(s for s in NEWS_SOURCES if s.key == "venturebeat_ai")
PH_API = "https://api.producthunt.com/v2/api/graphql"
PH_401 = {"errors": [{"error": "invalid_oauth_token", "error_description": "Please supply a valid access token."}]}
MIN = timedelta(minutes=1)

_spec = importlib.util.spec_from_file_location("check_sources", Path(__file__).resolve().parents[2] / "scripts" / "check_sources.py")
cs = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(cs)  # type: ignore[union-attr]


def hits(mocked, needle: str) -> int:  # type: ignore[no-untyped-def]
    return sum(len(v) for k, v in mocked.requests.items() if needle in str(k[1]))


def pipe_at(repo: Repository, settings: Settings, http: HttpClient, t: datetime) -> IngestionPipeline:
    return IngestionPipeline(repo, settings=settings, http=http, dedup=InMemoryDeduplicator(), clock=lambda: t)


async def runs(repo: Repository, source: str) -> list[SourceRunRow]:
    async with repo.db.session() as s:
        return list((await s.execute(select(SourceRunRow).where(SourceRunRow.source == source).order_by(SourceRunRow.id))).scalars())


# ------------------------------------------------------------- HTTP: no in-run hammering after 429
async def test_repeated_429_without_retry_after_is_asked_only_twice(http, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get(VB.feed_url, status=429, repeat=True)
    try:
        await http.get_text(VB.feed_url)
    except FetchError as e:
        assert e.status == 429 and e.rate_limited
    assert hits(mocked, "venturebeat.com/feed/") == 2  # was 5 (1 + http_max_retries) before the cap


async def test_other_retryable_statuses_keep_their_retries(http, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get("https://x.example/a", status=503, repeat=True)
    try:
        await http.get_text("https://x.example/a")
    except FetchError:
        pass
    assert hits(mocked, "x.example/a") == 3  # settings fixture: http_max_retries=2 -> unchanged behaviour


# ------------------------------------------------------------- VentureBeat through the scheduler/pipeline
async def test_venturebeat_cooldown_escalates_and_blocks_scheduled_ticks(repo, settings, http, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get(VB.feed_url, status=429, repeat=True)
    spec = REGISTRY["venturebeat_ai"]
    s1 = await pipe_at(repo, settings, http, NOW).run_spec(spec)  # tick 0: real attempt
    assert s1.status == "FAILED" and hits(mocked, "venturebeat") == 2
    cp = await repo.get_checkpoint("venturebeat_ai")
    assert cp["rate_limit_strikes"] == 1 and datetime.fromisoformat(cp["cooldown_until"]) == NOW + 15 * MIN
    for minutes in (5, 10, 14):  # scheduler ticks inside the cooldown: no request, no run row
        s = await pipe_at(repo, settings, http, NOW + minutes * MIN).run_spec(spec)
        assert s.status == "SKIPPED" and "cooldown until" in s.errors[0]
    assert hits(mocked, "venturebeat") == 2 and len(await runs(repo, "venturebeat_ai")) == 1
    await pipe_at(repo, settings, http, NOW + 16 * MIN).run_spec(spec)  # cooldown over: one new attempt, 429 again
    cp = await repo.get_checkpoint("venturebeat_ai")
    assert cp["rate_limit_strikes"] == 2 and datetime.fromisoformat(cp["cooldown_until"]) == NOW + 16 * MIN + 30 * MIN
    assert hits(mocked, "venturebeat") == 4
    # history and source preserved: both real attempts are recorded, the source stays registered and scheduled
    assert [r.status for r in await runs(repo, "venturebeat_ai")] == ["FAILED", "FAILED"]
    assert "venturebeat_ai" in {s.name for s in specs_for("news")}


async def test_venturebeat_recovers_and_cooldown_resets(repo, settings, http, mocked) -> None:  # type: ignore[no-untyped-def]
    await repo.save_checkpoint("venturebeat_ai", {"cooldown_until": (NOW - MIN).isoformat(), "rate_limit_strikes": 3}, success=False, items=0)
    mocked.get(VB.feed_url, body=rss([{"title": "Back", "link": "https://venturebeat.com/technology/a/", "pub": rfc2822(NOW - HOURS), "content": LONG_ARTICLE}]))
    s = await pipe_at(repo, settings, http, NOW).run_spec(REGISTRY["venturebeat_ai"])
    assert s.status == "OK"
    cp = await repo.get_checkpoint("venturebeat_ai")
    assert "cooldown_until" not in cp and "rate_limit_strikes" not in cp


async def test_source_health_shows_rate_limited_with_next_attempt(repo, settings, http, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get(VB.feed_url, status=429, repeat=True)
    await pipe_at(repo, settings, http, datetime.now(UTC)).run_spec(REGISTRY["venturebeat_ai"])
    async with repo.db.session() as s:
        h = {x.source: x for x in await source_health_rows(s)}["venturebeat_ai"]
    assert h.health == "RATE_LIMITED" and h.next_attempt_at is not None and h.next_attempt_at > datetime.now(UTC)
    assert h.status == "FAILED"  # the raw run status is kept alongside the category


# ------------------------------------------------------------- Product Hunt: a rejected token is not re-sent
def ph_settings(token: str) -> Settings:
    return Settings(_env_file=None, database_url="sqlite+aiosqlite://", respect_robots_txt=False, default_domain_rps=1000,  # type: ignore[call-arg]
                    producthunt_token=token, http_max_retries=2)


async def test_producthunt_rejected_token_is_not_sent_again(repo, mocked) -> None:  # type: ignore[no-untyped-def]
    bad = "ph_known_invalid_token_value_000000000000000"
    mocked.post(PH_API, status=401, payload=PH_401, repeat=True)
    async with HttpClient(ph_settings(bad)) as h:
        s1 = await pipe_at(repo, ph_settings(bad), h, NOW).run_crawler(ProductHuntCrawler(max_pages=1))
        assert s1.status == "INACCESSIBLE" and hits(mocked, "producthunt") == 1
        for day in (1, 2, 3):  # daily scheduled runs with the same token
            s = await pipe_at(repo, ph_settings(bad), h, NOW + timedelta(days=day)).run_crawler(ProductHuntCrawler(max_pages=1))
            assert s.status == "SKIPPED" and "credential required" in s.errors[0] and "not contacted" in s.errors[0]
        assert hits(mocked, "producthunt") == 1  # never re-sent
    cp = await repo.get_checkpoint("producthunt")
    assert cp["credential_rejected"]["fingerprint"] == token_fingerprint(bad) and cp["credential_rejected"]["code"] == "invalid_oauth_token"
    async with repo.db.session() as s:  # the token never reaches the database, logs or run rows
        stored = json.dumps([c.cursor for c in (await s.execute(select(CrawlCheckpointRow))).scalars()]) + " ".join(r.error or "" for r in await runs(repo, "producthunt"))
    assert bad not in stored and bad[:12] not in stored
    async with repo.db.session() as s:
        hp = {x.source: x for x in await source_health_rows(s)}["producthunt"]
    assert hp.health == "CREDENTIAL_REQUIRED" and "not sent again" in (hp.health_detail or "") and "Developer Token" in (hp.action_required or "")


async def test_producthunt_new_token_is_tried_and_success_clears_flag(repo, mocked) -> None:  # type: ignore[no-untyped-def]
    await repo.save_checkpoint("producthunt", {"credential_rejected": {"fingerprint": token_fingerprint("old"), "at": NOW.isoformat()}}, success=False, items=0)
    mocked.post(PH_API, payload={"data": {"posts": {"edges": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}})
    async with HttpClient(ph_settings("new_valid_token")) as h:
        await pipe_at(repo, ph_settings("new_valid_token"), h, NOW).run_crawler(ProductHuntCrawler(max_pages=1))
    assert hits(mocked, "producthunt") == 1
    assert not (await repo.get_checkpoint("producthunt")).get("credential_rejected")


# ------------------------------------------------------------- The Decoder: zero requests on every path
async def test_the_decoder_is_never_contacted(repo, settings, http, mocked) -> None:  # type: ignore[no-untyped-def]
    pipe = pipe_at(repo, settings, http, NOW)
    assert "the_decoder" not in {s.name for s in specs_for()} | {s.name for s in specs_for("news")} | {s.name for s in specs_for("all")}
    s = await pipe.run_spec(specs_for(names=["the_decoder"])[0])  # explicit CLI/API run of the disabled source
    assert s.status == "SKIPPED" and "disabled" in s.errors[0]
    r = await cs.check_feed(http, "the_decoder", cs.CrawlContext(http=http, settings=settings, extras={"dedup": InMemoryDeduplicator()}), {}, True)
    assert r["requests_made"] == []  # not even with --force
    assert not mocked.requests


def test_scheduler_entries() -> None:
    from src.pipelines.tasks import app

    sched = app.conf.beat_schedule
    assert "fresh-venturebeat_ai" in sched and "bulk-producthunt" in sched  # kept: gating happens in the pipeline
    assert not any("the_decoder" in k for k in sched)
    assert all(not REGISTRY[k.split("-", 1)[1]].disabled_reason for k in sched if k.split("-", 1)[0] in ("fresh", "bulk"))


# ------------------------------------------------------------- health classification
def test_classify_precedence() -> None:
    now = datetime.now(UTC)
    later = (now + HOURS).isoformat()
    assert classify(disabled="robots", last_status="INACCESSIBLE", last_error="HTTP 403", cursor={}, now=now)[0] == "DISABLED"
    assert classify(disabled=None, last_status="INACCESSIBLE", last_error="Product Hunt rejected PRODUCTHUNT_TOKEN (HTTP 401, ...)", cursor={}, now=now)[0] == "CREDENTIAL_REQUIRED"
    assert classify(disabled=None, last_status="FAILED", last_error="feed fetch failed: HTTP 429", cursor={}, now=now)[0] == "RATE_LIMITED"
    h, _, nxt = classify(disabled=None, last_status="FAILED", last_error="x", cursor={"cooldown_until": later, "rate_limit_strikes": 2}, now=now)
    assert h == "RATE_LIMITED" and nxt is not None
    assert classify(disabled=None, last_status="PARTIAL", last_error="article x: HTTP 405", cursor={}, now=now)[0] == "PARTIAL"
    assert classify(disabled=None, last_status="FAILED", last_error="boom", cursor={}, now=now)[0] == "FAILED"
    assert classify(disabled=None, last_status="OK", last_error=None, cursor={}, now=now) == ("HEALTHY", None, None)


def test_stale_rate_limit_evidence_expires_but_a_fresh_live_429_counts() -> None:
    now = datetime.now(UTC)
    old = now - timedelta(hours=25)
    gh = "GitHub rate limit exhausted until 2026-11-21T10:26:59+00:00"
    # research_papers: 25 h old exhausted quota, live check passed since -> not "rate limited" any more
    assert classify(disabled=None, last_status="PARTIAL", last_error=gh, cursor={}, now=now, last_run_at=old,
                    live={"verdict": "PASS", "checked_at": (now - HOURS).isoformat(), "http_status": None})[0] == "PARTIAL"
    # venturebeat: 25 h old 429 run, and the live check 1 h ago got 429 again -> still rate limited
    h, detail, _ = classify(disabled=None, last_status="FAILED", last_error="feed fetch failed: HTTP 429", cursor={}, now=now, last_run_at=old,
                            live={"verdict": "RATE LIMITED", "checked_at": (now - HOURS).isoformat(), "http_status": 429})
    assert h == "RATE_LIMITED" and "live check" in (detail or "")
    # without any current evidence an old 429 failure is reported as a failure
    assert classify(disabled=None, last_status="FAILED", last_error="HTTP 429", cursor={}, now=now, last_run_at=old)[0] == "FAILED"


def test_live_check_summary_reads_newest_report(tmp_path) -> None:
    (tmp_path / "source_check_20261122T100000Z.json").write_text(json.dumps({"checked_at": "old", "sources": {"x": {"verdict": "FAIL"}}}))
    (tmp_path / "source_check_20261122T110000Z.json").write_text(json.dumps({"checked_at": "new", "sources": {
        "venturebeat_ai": {"verdict": "RATE LIMITED", "feed_response": {"http_status": 429}, "requests_made": ["a", "b"]},
        "the_decoder": {"verdict": "DISABLED (manual)", "requests_made": []}}}))
    live = latest_live_check(tmp_path)
    assert live["venturebeat_ai"] == {"verdict": "RATE LIMITED", "checked_at": "new", "report": "source_check_20261122T110000Z.json", "http_status": 429, "requests_made": 2}
    assert live["the_decoder"]["requests_made"] == 0 and "x" not in live
    assert latest_live_check(tmp_path / "missing") == {}


# ------------------------------------------------------------- diagnostic does not waste requests either
async def test_diagnostic_honours_its_cooldown(http, settings, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get("https://venturebeat.com/robots.txt", body="User-agent: *\n", repeat=True)
    mocked.get(VB.feed_url, status=429, repeat=True)
    state: dict = {}

    def ctx():  # type: ignore[no-untyped-def]
        return cs.CrawlContext(http=http, settings=settings, extras={"dedup": InMemoryDeduplicator()})

    r1 = await cs.check_feed(http, "venturebeat_ai", ctx(), state)
    assert r1["verdict"] == "RATE LIMITED" and state["venturebeat_ai"]["strikes"] == 1 and hits(mocked, "venturebeat.com/feed/") == 2
    r2 = await cs.check_feed(http, "venturebeat_ai", ctx(), state)
    assert r2["verdict"] == "RATE LIMITED (cooling down)" and r2["requests_made"] == [] and hits(mocked, "venturebeat.com/feed/") == 2
    r3 = await cs.check_feed(http, "venturebeat_ai", ctx(), state, force=True)
    assert r3["verdict"] == "RATE LIMITED" and state["venturebeat_ai"]["strikes"] == 2  # --force contacts it and escalates


async def test_diagnostic_does_not_resend_rejected_token(mocked, tmp_path) -> None:  # type: ignore[no-untyped-def]
    tok = "ph_known_invalid_token_value_111111111111111"
    mocked.post(PH_API, status=401, payload=PH_401, repeat=True)
    state: dict = {}
    async with HttpClient(ph_settings(tok)) as h:
        r1 = await cs.check_producthunt(h, ph_settings(tok), state)
        r2 = await cs.check_producthunt(h, ph_settings(tok), state)
    assert r1["verdict"] == "CREDENTIAL REQUIRED" and r1["http_status"] == 401
    assert r2["verdict"].startswith("CREDENTIAL REQUIRED") and r2["requests_made"] == [] and hits(mocked, "producthunt") == 1
    cs.save_state(state, tmp_path / "state.json")
    saved = (tmp_path / "state.json").read_text()
    assert tok not in saved and tok[:12] not in saved and token_fingerprint(tok) in saved
