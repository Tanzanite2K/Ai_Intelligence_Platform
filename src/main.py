"""Command-line entry point.

    python -m src.main init-db
    python -m src.main crawl --vertical all [--limit N] [--no-llm]
    python -m src.main crawl --source yc_startups --source research_papers
    python -m src.main export [--sheets] [--share-public]
    python -m src.main report
    python -m src.main serve
    python -m src.main collect --trigger manual --vertical all   (Phase 7C; needs HISTORY_ENABLED=true)
    python -m src.main sync-evidence                               (Phase 7D; needs HISTORY_ENABLED=true)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.config.settings import get_settings
from src.crawlers.registry import REGISTRY, VERTICALS, specs_for
from src.database.repository import Repository
from src.database.session import Database
from src.exports.csv_export import write_csvs
from src.exports.tabs import build_tabs
from src.monitoring.logging import configure_logging, get_logger
from src.pipelines.runtime import pipeline_context

log = get_logger("main")


async def run_crawl(vertical: str | None, sources: list[str] | None, limit: int | None, use_llm: bool, concurrency: int) -> list[dict[str, Any]]:
    specs = specs_for(vertical, sources)
    async with pipeline_context(use_llm=use_llm) as pipe:
        summaries = await pipe.run_many(specs, limit=limit, concurrency=concurrency)
    out = [s.__dict__ for s in summaries]
    Path("data").mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    Path(f"data/run_{stamp}.json").write_text(json.dumps(out, indent=2, default=str))
    return out


TRIGGERS = ("manual", "daily_0730", "news_jobs_1330")


def slot_for(trigger: str, now: datetime) -> datetime:
    """Scheduled slot a run belongs to. A re-run of the same slot resumes the same batch (retry protection)."""
    if trigger == "daily_0730":
        return now.replace(hour=2, minute=0, second=0, microsecond=0)  # 07:30 IST = 02:00 UTC
    if trigger == "news_jobs_1330":
        return now.replace(hour=8, minute=0, second=0, microsecond=0)  # 13:30 IST = 08:00 UTC
    return now.replace(second=0, microsecond=0)


async def run_collect(trigger: str, vertical: str | None, sources: list[str] | None, use_llm: bool, concurrency: int) -> dict[str, Any]:
    """Crawl + Phase 7C history capture in one batch. Refuses unless HISTORY_ENABLED=true."""
    s = get_settings()
    if not s.history_enabled:
        return {"refused": "HISTORY_ENABLED is false. History collection is off until it is explicitly enabled."}
    from src.history.capture import HistoryRecorder

    db = Database(s.database_url)
    rec = HistoryRecorder(db)
    now = datetime.now(UTC)
    batch, resumed = await rec.open_batch(trigger, slot_for(trigger, now), code_version="7C")
    status = "failed"
    try:
        specs = specs_for(vertical, sources)
        async with pipeline_context(use_llm=use_llm, history_batch=batch) as pipe:
            summaries = await pipe.run_many(specs, limit=None, concurrency=concurrency)
        status = "completed" if all(x.status in ("OK", "PARTIAL", "SKIPPED") for x in summaries) else "partial"
        return {"batch_id": batch, "resumed": resumed, "status": status,
                "sources": [{"source": x.source, "status": x.status, **{k: x.extra.get(k) for k in ("history", "history_scan") if k in x.extra}} for x in summaries]}
    finally:
        await rec.close_batch(batch, status)
        await db.dispose()


async def run_export(*, sheets: bool, share_public: bool) -> dict[str, Any]:
    s = get_settings()
    db = Database(s.database_url)
    await db.create_all()
    repo = Repository(db)
    tabs = await build_tabs(repo, window_hours=s.freshness_window_hours)
    paths = write_csvs(tabs, s.export_dir)
    result: dict[str, Any] = {"csv": {k: str(v) for k, v in paths.items()}, "rows": {t.title: len(t.rows) for t in tabs}, "sheet_url": None}
    if sheets:
        if not s.google_service_account_file:
            result["sheets_ok"] = False
            result["sheets_error"] = "GOOGLE_SERVICE_ACCOUNT_FILE not set — CSV fallback written; no spreadsheet was created"
        else:
            from src.exports.google_sheets import GoogleSheetsExporter, SheetsError, load_token

            token = load_token(s.google_service_account_file)
            ex = GoogleSheetsExporter(token, batch_rows=s.sheets_batch_rows)
            try:
                result["sheet_url"] = await ex.export(tabs, spreadsheet_id=s.google_sheet_id, title=s.google_sheet_title, share_public=share_public)
                result["grid_expanded"] = ex.grid_changes
                v = await ex.verify(ex.last_spreadsheet_id, tabs)
                result["sheets_verification"] = v
                result["sheets_api_requests"] = ex.requests_made
                problems = []
                if not v["exactly_six_expected_tabs"]:
                    problems.append(f"tabs {v['tabs']}")
                if not v["rows_match"]:
                    problems.append(f"row counts {v['data_rows']} != expected {v['expected_rows']}")
                if not all(v["headers_match"].values()):
                    problems.append(f"headers {v['headers_match']}")
                if v["public_link_reader"] and not share_public:
                    problems.append("spreadsheet has anyone-with-link access but a PRIVATE export was requested")
                result["sheets_ok"] = not problems
                if problems:
                    result["sheets_error"] = "read-back verification failed: " + "; ".join(problems)
            except SheetsError as e:
                result["sheets_ok"] = False
                result["sheets_error"] = str(e)[:600] + " — CSV backups were written."
            Path("docs").mkdir(exist_ok=True)
            Path("docs/sheets_export.json").write_text(json.dumps({k: v for k, v in result.items() if k != "csv"}, indent=2), encoding="utf-8")
    await db.dispose()
    return result


async def run_refresh_stars(*, max_age_hours: float = 24.0, limit: int | None = None) -> dict[str, Any]:
    """Re-check GitHub stars for stored papers (live API) without re-crawling.

    Targets papers whose stars came from the Hugging Face cache or were checked
    more than ``max_age_hours`` ago. Stops cleanly when the GitHub rate limit is
    exhausted; already-updated rows are kept (idempotent upsert).
    """
    from datetime import timedelta

    from src.core.http import FetchError, HttpClient
    from src.core.schemas import ResearchPaperRecord
    from src.crawlers.registry import DOMAIN_RPS
    from src.crawlers.research.github import GitHubClient, GitHubRateLimited

    s = get_settings()
    db = Database(s.database_url)
    await db.create_all()
    repo = Repository(db, batch_size=s.bulk_insert_batch_size)
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=max_age_hours)
    papers = await repo.fetch_records("RESEARCH_PAPER")
    todo = []
    for p in papers:
        c = p["content"]
        checked = c.get("github_stars_checked_at")
        stale = checked is None or datetime.fromisoformat(checked.replace("Z", "+00:00")) < cutoff
        if c.get("github_url") and (c.get("github_stars_source") != "github_api" or stale):
            todo.append(p)
    todo = todo[:limit] if limit else todo
    out: dict[str, Any] = {"candidates": len(todo), "updated_live": 0, "repo_gone": 0, "errors": 0, "stopped_reason": None,
                           "token": bool(s.github_token)}
    token = s.github_token.get_secret_value() if s.github_token else None
    async with HttpClient(s, domain_rps=DOMAIN_RPS) as http:
        gh = GitHubClient(http, token)
        for i in range(0, len(todo), 50):
            batch, updated = todo[i : i + 50], []
            results = await asyncio.gather(*(gh.repo_metrics(p["content"]["github_url"]) for p in batch), return_exceptions=True)
            stop = None
            for p, r in zip(batch, results, strict=True):
                if isinstance(r, GitHubRateLimited):
                    stop = str(r)
                    continue
                if isinstance(r, FetchError) or isinstance(r, BaseException):
                    out["errors"] += 1
                    continue
                if r is None:
                    out["repo_gone"] += 1  # keep record; stars stay as last known, labelled by source
                    continue
                c = dict(p["content"], github_url=r.url, github_stars=r.stars, github_stars_source="github_api",
                         github_stars_checked_at=r.checked_at.strftime("%Y-%m-%dT%H:%M:%SZ"))
                prov = dict(p["provenance"], validationStatus="VERIFIED")
                updated.append(ResearchPaperRecord.model_validate({**p, "content": c, "provenance": prov}))
            if updated:
                await repo.bulk_upsert(updated, priority=10)
                out["updated_live"] += len(updated)
            if stop:
                out["stopped_reason"] = stop
                break
    await db.dispose()
    if not token:
        out["hint"] = "GITHUB_TOKEN not set: GitHub allows only 60 requests/hour unauthenticated"
    return out


async def run_report() -> dict[str, Any]:
    s = get_settings()
    db = Database(s.database_url)
    await db.create_all()
    repo = Repository(db)
    counts = await repo.count_by_type()
    tabs = await build_tabs(repo, window_hours=s.freshness_window_hours)
    mappings = await repo.fetch_mappings()
    methods: dict[str, int] = {}
    for m in mappings:
        methods[m["matchMethod"]] = methods.get(m["matchMethod"], 0) + 1
    runs = await repo.fetch_runs()
    papers = await repo.fetch_records("RESEARCH_PAPER")
    stars: dict[str, int] = {}
    for p in papers:
        k = p["content"].get("github_stars_source") or "none"
        stars[k] = stars.get(k, 0) + 1
    latest: dict[str, str] = {}
    for r in runs:
        latest[r["source"]] = r["status"]
    await db.dispose()
    return {
        "research_stars_source": stars,
        "latest_status_by_source": latest,
        "stored_by_type": counts,
        "export_rows": {t.title: len(t.rows) for t in tabs},
        "entity_resolution": {"mappings": len(mappings), "by_method": methods, "needs_review": sum(1 for m in mappings if m["needsReview"])},
        "dead_letters": await _dlq_count(),
        "last_runs": [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in runs[-30:]],
    }


async def _dlq_count() -> int:
    db = Database(get_settings().database_url)
    try:
        return await Repository(db).count_dead_letters()
    finally:
        await db.dispose()


def main(argv: list[str] | None = None) -> int:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    p = argparse.ArgumentParser(prog="aip", description="AI Intelligence Ingestion Platform")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    c = sub.add_parser("crawl")
    c.add_argument("--vertical", choices=[*VERTICALS, "all"], default=None)
    c.add_argument("--source", action="append", choices=sorted(REGISTRY), default=None)
    c.add_argument("--limit", type=int, default=None, help="max records per source")
    c.add_argument("--no-llm", action="store_true")
    c.add_argument("--concurrency", type=int, default=4, help="sources crawled concurrently")
    e = sub.add_parser("export")
    e.add_argument("--sheets", action="store_true")
    e.add_argument("--share-public", action="store_true", help="make the spreadsheet readable by anyone with the link")
    sub.add_parser("report")
    sub.add_parser("rebuild-entities", help="re-resolve stored records with current rules and regenerate the mapping log")
    en = sub.add_parser("enrich", help="LLM pricing extraction for stored products with an evidenced pricing cue")
    en.add_argument("--limit", type=int, default=None)
    en.add_argument("--news", action="store_true", help="extract organisation mentions for stored news lacking them")
    rs = sub.add_parser("refresh-stars", help="re-check GitHub stars of stored papers via the live GitHub API")
    rs.add_argument("--max-age-hours", type=float, default=24.0)
    rs.add_argument("--limit", type=int, default=None)
    sub.add_parser("serve")
    co = sub.add_parser("collect", help="Phase 7C: crawl and record history in one batch (needs HISTORY_ENABLED=true)")
    co.add_argument("--trigger", choices=TRIGGERS, default="manual")
    co.add_argument("--vertical", choices=[*VERTICALS, "all"], default=None)
    co.add_argument("--source", action="append", choices=sorted(REGISTRY), default=None)
    co.add_argument("--no-llm", action="store_true")
    co.add_argument("--concurrency", type=int, default=4)
    sub.add_parser("sync-evidence", help="Phase 7D: persist entity registry, resolution decisions, claims and evidence (needs HISTORY_ENABLED=true)")
    a = p.parse_args(argv)

    if a.cmd == "init-db":
        async def _init() -> None:
            db = Database(s.database_url)
            await db.create_all()
            await db.dispose()

        asyncio.run(_init())
        print("database ready:", s.database_url.split("@")[-1])
    elif a.cmd == "crawl":
        if not a.vertical and not a.source:
            p.error("give --vertical or --source")
        out = asyncio.run(run_crawl(a.vertical, a.source, a.limit, not a.no_llm, a.concurrency))
        print(json.dumps([{k: v for k, v in r.items() if k in ("source", "status", "fetched", "emitted", "inserted", "updated", "unchanged", "rejected", "freshness", "llm", "extra", "seconds", "errors")} for r in out], indent=2, default=str))
    elif a.cmd == "export":
        res = asyncio.run(run_export(sheets=a.sheets, share_public=a.share_public))
        print(json.dumps(res, indent=2))
        if a.sheets and not res.get("sheets_ok"):
            print("SHEETS EXPORT FAILED:", res.get("sheets_error"), file=sys.stderr)
            return 3  # Sheets requested but not uploaded+verified: never report success
    elif a.cmd == "rebuild-entities":
        from src.pipelines.maintenance import rebuild_entities

        print(json.dumps(asyncio.run(rebuild_entities()), indent=2))
    elif a.cmd == "enrich":
        from src.pipelines.enrich import run_enrich_news, run_enrich_products

        fn = run_enrich_news if a.news else run_enrich_products
        print(json.dumps(asyncio.run(fn(limit=a.limit)), indent=2))
    elif a.cmd == "refresh-stars":
        print(json.dumps(asyncio.run(run_refresh_stars(max_age_hours=a.max_age_hours, limit=a.limit)), indent=2))
    elif a.cmd == "report":
        print(json.dumps(asyncio.run(run_report()), indent=2, default=str))
    elif a.cmd == "collect":
        if not a.vertical and not a.source:
            p.error("give --vertical or --source")
        res = asyncio.run(run_collect(a.trigger, a.vertical, a.source, not a.no_llm, a.concurrency))
        print(json.dumps(res, indent=2, default=str))
        return 4 if "refused" in res else 0
    elif a.cmd == "sync-evidence":
        if not s.history_enabled:
            print("Refused: HISTORY_ENABLED is false. Evidence persistence writes to the history tables and stays off until enabled.")
            return 4
        from src.history.registry import sync_registry

        async def _sync() -> dict[str, Any]:
            db = Database(s.database_url)
            try:
                return await sync_registry(db)
            finally:
                await db.dispose()

        print(json.dumps(asyncio.run(_sync()), indent=2))
    elif a.cmd == "serve":
        import uvicorn

        uvicorn.run("src.api.app:app", host=s.api_host, port=s.api_port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
