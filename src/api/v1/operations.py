"""Operations (ingestion runs, source health, dead letters) and the dashboard aggregates."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Integer, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.crawlers.registry import ACTION_REQUIRED, REGISTRY
from src.database.models import CanonicalEntityRow, CrawlCheckpointRow, DeadLetterRow, EntityMappingRow, RecordRow, SourceRunRow
from src.monitoring.logging import redact

from . import fields as F
from .deps import Paging, Session, _aware, summarize
from .records import naive_utc
from .schemas import DeadLetterItem, Item, Overview, Page, RunItem, SourceHealth

router = APIRouter()
R = SourceRunRow
RATE_LIMITED = or_(R.error.like("%429%"), func.lower(R.error).like("%rate limit%"), func.lower(R.error).like("%too many requests%"))
ACCESS_DENIED = or_(R.status == "INACCESSIBLE", R.error.like("%HTTP 401%"), R.error.like("%HTTP 403%"))


DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"
_CREDENTIAL_RX = re.compile(r"HTTP 401|invalid_oauth_token|rejected [A-Z_]*TOKEN|[A-Z_]*TOKEN rejected|credential required", re.I)
_RATE_RX = re.compile(r"HTTP 429|rate.?limit|too many requests", re.I)


def latest_live_check(docs: Path | None = None) -> dict[str, dict[str, Any]]:
    """Newest read-only diagnostic report (scripts/check_sources.py), summarised per source. Never raises."""
    try:
        reports = sorted((docs or DOCS_DIR).glob("source_check_*.json"))
        if not reports:
            return {}
        data = json.loads(reports[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for name, r in (data.get("sources") or {}).items():
        if not isinstance(r, dict):
            continue
        fr = r.get("feed_response") or {}
        out[name] = {"verdict": r.get("verdict"), "checked_at": data.get("checked_at"), "report": reports[-1].name,
                     "http_status": fr.get("http_status", r.get("http_status")), "requests_made": len(r["requests_made"]) if "requests_made" in r else None}
    return out


RATE_EVIDENCE_TTL = timedelta(hours=24)  # = the longest cooldown; older 429s say nothing about the source today


def classify(*, disabled: str | None, last_status: str, last_error: str | None, cursor: dict[str, Any], now: datetime,
             last_run_at: datetime | None = None, live: dict[str, Any] | None = None) -> tuple[str, str | None, datetime | None]:
    """Operational category, in precedence order: disabled > credential > rate limit > partial > failed/skipped > healthy.

    Rate-limit evidence counts only while it is current: an active cooldown, a rate-limited run in the last 24 h, or a
    read-only live check (newer than the last run) that itself got HTTP 429.
    """
    if disabled:
        return "DISABLED", disabled, None
    rejected = cursor.get("credential_rejected") or {}
    err = last_error or ""
    if rejected or (last_status in ("INACCESSIBLE", "FAILED", "SKIPPED") and _CREDENTIAL_RX.search(err)):
        if rejected:
            return "CREDENTIAL_REQUIRED", (f"Credential rejected at {rejected.get('at')} (HTTP {rejected.get('http_status')}, "
                                           f"{rejected.get('code') or 'no error code'}); that token is not sent again until it changes"), None
        return "CREDENTIAL_REQUIRED", "The latest run was refused for its credential (HTTP 401/403)", None
    until = None
    if cursor.get("cooldown_until"):
        try:
            until = datetime.fromisoformat(str(cursor["cooldown_until"]))
            until = until if until.tzinfo else until.replace(tzinfo=UTC)
        except ValueError:
            until = None
    if until and until > now:
        return "RATE_LIMITED", f"Source answered HTTP 429; cooling down (strike {cursor.get('rate_limit_strikes', 1)}), no requests before the next attempt time", until
    recent = last_run_at is None or now - last_run_at <= RATE_EVIDENCE_TTL
    if last_status != "OK" and _RATE_RX.search(err) and recent:
        return "RATE_LIMITED", "The latest run was rate-limited by the source", until
    live_at = None
    if live and live.get("checked_at"):
        try:
            live_at = datetime.fromisoformat(str(live["checked_at"]))
        except ValueError:
            live_at = None
    if live and live.get("http_status") == 429 and live_at and (last_run_at is None or live_at > last_run_at) and now - live_at <= RATE_EVIDENCE_TTL:
        return "RATE_LIMITED", f"The latest live check got HTTP 429 at {live_at.isoformat(timespec='minutes')}", until
    if last_status == "PARTIAL":
        return "PARTIAL", "The latest run collected data but some requests failed", None
    if last_status in ("FAILED", "INACCESSIBLE"):
        return "FAILED", "The latest run collected nothing", None
    if last_status == "SKIPPED":
        return "SKIPPED", "The latest run was skipped", None
    return "HEALTHY", None, None


def _run(r: SourceRunRow) -> RunItem:
    st, fin = _aware(r.started_at), _aware(r.finished_at)
    return RunItem(id=r.id, source=r.source, vertical=r.vertical, status=r.status, started_at=st, finished_at=fin,
                   duration_s=round((fin - st).total_seconds(), 2) if st and fin else None, fetched=r.fetched, stored=r.stored,
                   duplicates=r.duplicates, rejected=r.rejected, error=redact(r.error)[:1000] if r.error else None)


@router.get("/operations/ingestion-runs", response_model=Page[RunItem], tags=["operations"])
async def ingestion_runs(
    source: str | None = Query(None, max_length=120),
    status: Literal["OK", "PARTIAL", "INACCESSIBLE", "FAILED", "SKIPPED"] | None = Query(None),
    paging: Paging = Depends(),
    s: AsyncSession = Session,
) -> Page[RunItem]:
    cond = [c for c in ((R.source == source) if source else None, (R.status == status) if status else None) if c is not None]
    total = (await s.execute(select(func.count()).select_from(R).where(*cond))).scalar() or 0
    rows = (await s.execute(select(R).where(*cond).order_by(R.started_at.desc(), R.id.desc()).offset(paging.offset).limit(paging.page_size))).scalars().all()
    return Page[RunItem](data=[_run(r) for r in rows], meta=paging.meta(total))


async def source_health_rows(s: AsyncSession) -> list[SourceHealth]:
    agg = (await s.execute(
        select(R.source, func.max(R.vertical), func.count(), func.max(R.started_at), func.max(case((R.status == "OK", R.finished_at))),
               func.sum(cast(RATE_LIMITED, Integer)), func.sum(cast(ACCESS_DENIED, Integer)), func.max(R.id))
        .group_by(R.source)
    )).all()
    by_status: dict[str, dict[str, int]] = {}
    for src, st, n in (await s.execute(select(R.source, R.status, func.count()).group_by(R.source, R.status))).all():
        by_status.setdefault(src, {})[st] = n
    last_ids = [a[7] for a in agg]
    last = {r.source: r for r in (await s.execute(select(R).where(R.id.in_(last_ids)))).scalars()}
    last_err = {}
    for src, err in (await s.execute(select(R.source, R.error).where(R.error.is_not(None)).order_by(R.started_at.desc()))).all():
        last_err.setdefault(src, err)
    cps = {c.source: c for c in (await s.execute(select(CrawlCheckpointRow))).scalars()}
    live = latest_live_check()
    now = datetime.now(UTC)
    out = []
    for src, vert, n, last_run, last_ok, rl, ad, _ in agg:
        cp = cps.get(src)
        ok_at = _aware(last_ok) or (_aware(cp.last_success_at) if cp else None)
        spec = REGISTRY.get(src)
        disabled = spec.disabled_reason if spec else None
        action = None if disabled else (ACTION_REQUIRED.get(src) if last[src].status != "OK" else None)  # disabled: reason is the detail
        health, detail, nxt = classify(disabled=disabled, last_status=last[src].status, last_error=last[src].error,
                                       cursor=dict(cp.cursor or {}) if cp else {}, now=now, last_run_at=_aware(last_run), live=live.get(src))
        out.append(SourceHealth(disabled_reason=disabled, action_required=action, health=health, health_detail=detail, next_attempt_at=nxt,
                                live_check=live.get(src), source=src, vertical=vert, status=last[src].status, last_run_at=_aware(last_run), last_success_at=ok_at, runs=n,
                                runs_by_status=by_status.get(src, {}), rate_limited_runs=int(rl or 0), access_denied_runs=int(ad or 0),
                                last_error=redact(last_err[src])[:500] if src in last_err else None, items_seen=cp.items_seen if cp else None))
    order = {"FAILED": 0, "CREDENTIAL_REQUIRED": 1, "RATE_LIMITED": 2, "PARTIAL": 3, "SKIPPED": 4, "DISABLED": 5, "HEALTHY": 6}
    return sorted(out, key=lambda h: (order.get(h.health, 7), h.source))


@router.get("/operations/source-health", response_model=Item[list[SourceHealth]], tags=["operations"])
async def source_health(s: AsyncSession = Session) -> Item[list[SourceHealth]]:
    return Item[list[SourceHealth]](data=await source_health_rows(s))


@router.get("/operations/dead-letters", response_model=Page[DeadLetterItem], tags=["operations"])
async def dead_letters(stage: str | None = Query(None, max_length=64), paging: Paging = Depends(), s: AsyncSession = Session) -> Page[DeadLetterItem]:
    D = DeadLetterRow
    cond = [D.stage == stage] if stage else []
    total = (await s.execute(select(func.count()).select_from(D).where(*cond))).scalar() or 0
    rows = (await s.execute(select(D).where(*cond).order_by(D.created_at.desc(), D.id.desc()).offset(paging.offset).limit(paging.page_size))).scalars().all()
    import json

    return Page[DeadLetterItem](data=[
        DeadLetterItem(id=d.id, stage=d.stage, key=d.key, error=redact(d.error)[:1000], attempts=d.attempts, created_at=_aware(d.created_at),
                       payload_preview=redact(json.dumps(d.payload, ensure_ascii=False))[:400])
        for d in rows], meta=paging.meta(total))


# ------------------------------------------------------------------ dashboard
@router.get("/dashboard/overview", response_model=Item[Overview], tags=["dashboard"])
async def overview(s: AsyncSession = Session) -> Item[Overview]:
    counts = dict((await s.execute(select(RecordRow.record_type, func.count()).group_by(RecordRow.record_type))).all())
    M = EntityMappingRow
    orgs = (await s.execute(select(func.count(func.distinct(M.canonical_name))))).scalar() or 0
    seed = (await s.execute(select(func.count()).select_from(CanonicalEntityRow))).scalar() or 0
    maps = (await s.execute(select(func.count()).select_from(M))).scalar() or 0
    review = (await s.execute(select(func.count()).where(M.needs_review.is_(True)))).scalar() or 0
    dlq = (await s.execute(select(func.count()).select_from(DeadLetterRow))).scalar() or 0
    failed = (await s.execute(select(func.count()).where(R.status.in_(["FAILED", "INACCESSIBLE"])))).scalar() or 0
    runs = (await s.execute(select(func.count()).select_from(R))).scalar() or 0
    last = (await s.execute(select(func.max(R.started_at)))).scalar()
    return Item[Overview](data=Overview(
        records={F.SLUG_OF.get(k, k): v for k, v in counts.items()}, total_records=sum(counts.values()), organisations=orgs, seed_organisations=seed,
        entity_mappings=maps, pending_reviews=review, dead_letters=dlq, failed_runs=failed, ingestion_runs=runs, last_run_at=_aware(last),
        generated_at=datetime.now(UTC)))


@router.get("/dashboard/activity", tags=["dashboard"])
async def activity(days: int = Query(90, ge=1, le=3650), s: AsyncSession = Session) -> dict[str, Any]:
    """Real daily aggregates. 'collected' = when the platform stored records; 'published' = source publication dates."""
    since = naive_utc(datetime.now(UTC) - timedelta(days=days))
    day = func.date(RecordRow.collected_at)
    collected = (await s.execute(select(day, RecordRow.record_type, func.count()).where(RecordRow.collected_at >= since).group_by(day, RecordRow.record_type).order_by(day))).all()
    pday = func.date(RecordRow.published_at)
    published = (await s.execute(select(pday, RecordRow.record_type, func.count()).where(RecordRow.published_at >= since).group_by(pday, RecordRow.record_type).order_by(pday))).all()

    def series(rows: list[Any]) -> list[dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for d, t, n in rows:
            out.setdefault(str(d), {"date": str(d)})[F.SLUG_OF.get(t, t)] = n
        return list(out.values())

    col = series(collected)
    runs_by_day = (await s.execute(select(func.date(R.started_at), func.count(), func.sum(R.stored)).where(R.started_at >= since).group_by(func.date(R.started_at)).order_by(func.date(R.started_at)))).all()
    note = None
    if len(col) <= 1:
        note = (f"Collection history covers {len(col)} day(s): all stored records were collected on {col[0]['date'] if col else 'no date'}. "
                "A time series becomes meaningful once scheduled ingestion has run on more days.")
    return {"data": {"collected_per_day": col, "published_per_day": series(published),
                     "runs_per_day": [{"date": str(d), "runs": n, "stored": int(st or 0)} for d, n, st in runs_by_day]},
            "meta": {"days": days, "collection_days": len(col)}, "note": note}


@router.get("/dashboard/source-contribution", tags=["dashboard"])
async def source_contribution(s: AsyncSession = Session) -> dict[str, Any]:
    rows = (await s.execute(select(RecordRow.source_name, RecordRow.record_type, func.count()).group_by(RecordRow.source_name, RecordRow.record_type).order_by(func.count().desc()))).all()
    return {"data": [{"source": src, "type": F.SLUG_OF.get(t, t), "records": n} for src, t, n in rows]}


@router.get("/dashboard/freshness", tags=["dashboard"])
async def freshness(s: AsyncSession = Session) -> dict[str, Any]:
    now = datetime.now(UTC)
    cuts = {"24h": 1, "7d": 7, "30d": 30, "365d": 365}
    p = RecordRow.published_at
    cols = [func.sum(cast(p.is_(None), Integer))] + [func.sum(cast(p >= naive_utc(now - timedelta(days=d)), Integer)) for d in cuts.values()]
    rows = (await s.execute(select(RecordRow.record_type, func.count(), *cols).group_by(RecordRow.record_type))).all()
    data = []
    for t, n, undated, *within in rows:
        w = [int(x or 0) for x in within]
        data.append({"type": F.SLUG_OF.get(t, t), "total": n, "undated": int(undated or 0),
                     **{f"within_{k}": v for k, v in zip(cuts, w, strict=True)}, "older_than_365d": n - int(undated or 0) - w[-1]})
    return {"data": data, "meta": {"as_of": now, "basis": "published_at (startups/products have no publication date and count as undated)"}}


@router.get("/dashboard/recent", tags=["dashboard"])
async def recent(limit: int = Query(5, ge=1, le=20), s: AsyncSession = Session) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for slug, rt in F.TYPE_SLUGS.items():
        order = [F.EFFECTIVE_DATE.desc(), RecordRow.id.desc()] if rt in ("RESEARCH_PAPER", "NEWS", "JOB") else [RecordRow.collected_at.desc(), RecordRow.id.desc()]
        rows = (await s.execute(select(RecordRow).where(RecordRow.record_type == rt).order_by(*order).limit(limit))).scalars().all()
        out[slug] = [summarize(r).model_dump(mode="json") for r in rows]
    runs = (await s.execute(select(R).order_by(R.started_at.desc(), R.id.desc()).limit(10))).scalars().all()
    out["runs"] = [_run(r).model_dump(mode="json") for r in runs]
    return {"data": out}


@router.get("/dashboard/source-health", response_model=Item[list[SourceHealth]], tags=["dashboard"])
async def dashboard_source_health(s: AsyncSession = Session) -> Item[list[SourceHealth]]:
    return Item[list[SourceHealth]](data=await source_health_rows(s))
