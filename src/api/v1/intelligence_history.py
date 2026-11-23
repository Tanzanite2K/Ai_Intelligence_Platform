"""Phase 7F historical intelligence: evidence-backed comparisons over time (read-only).

Every statement is computed from stored records or history rows and links to supporting records. Comparisons of
publication dates are descriptive (share of records, never market size). Snapshot-based "emerging" labels require at
least 30 days of reliable live collection; until then each section says so and still reports what can be measured.
"""

from __future__ import annotations

import json
import re
import statistics
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.intelligence import analysis as A
from src.intelligence.topics import TOPIC_BY_ID

from . import fields as F
from .deps import Session
from .history import _has_history, coverage
from .intelligence import get_index

router = APIRouter()
WINDOW = 28
MIN_PER_WINDOW = 50
MIN_TOPIC = 10
SEASONS = {"Winter": 0, "Spring": 1, "Summer": 2, "Fall": 3}


def _fmt(d: date) -> str:
    return d.strftime("%d %b %Y")


def _ev(recs: list[A.Rec], n: int = 4) -> list[dict[str, Any]]:
    return [{"id": r.id, "type": F.SLUG_OF.get(r.type, r.type), "name": r.name, "published": r.published.isoformat() if r.published else None}
            for r in sorted(recs, key=lambda r: (r.published or date.min, r.id), reverse=True)[:n]]


def research_section(idx: A.Index) -> dict[str, Any]:
    papers = [r for r in idx.by_type.get("RESEARCH_PAPER", []) if r.published]
    if not papers:
        return {"id": "research_topics", "title": "Research topic changes", "status": "no_data", "statements": []}
    end = max(r.published for r in papers)  # type: ignore[type-var]
    r_start, p_end = end - timedelta(days=WINDOW - 1), end - timedelta(days=WINDOW)
    p_start = p_end - timedelta(days=WINDOW - 1)
    recent = [r for r in papers if r_start <= r.published <= end]  # type: ignore[operator]
    prior = [r for r in papers if p_start <= r.published <= p_end]  # type: ignore[operator]
    days_r, days_p = len({r.published for r in recent}), len({r.published for r in prior})
    period = {"recent": [r_start.isoformat(), end.isoformat()], "prior": [p_start.isoformat(), p_end.isoformat()],
              "papers": {"recent": len(recent), "prior": len(prior)}, "days_with_papers": {"recent": days_r, "prior": days_p}}
    if len(recent) < MIN_PER_WINDOW or len(prior) < MIN_PER_WINDOW:
        return {"id": "research_topics", "title": "Research topic changes", "status": "insufficient_data", "period": period, "statements": [],
                "explanation": f"Each {WINDOW}-day window needs at least {MIN_PER_WINDOW} dated papers."}
    stmts = []
    for tid, topic in TOPIC_BY_ID.items():
        rn = [r for r in recent if tid in r.topics]
        pn = [r for r in prior if tid in r.topics]
        if max(len(rn), len(pn)) < MIN_TOPIC:
            continue
        rs, ps = len(rn) / len(recent), len(pn) / len(prior)
        stmts.append({"topic": tid, "label": topic.label, "recent": len(rn), "prior": len(pn), "recent_share": round(rs, 4), "prior_share": round(ps, 4),
                      "change_pp": round((rs - ps) * 100, 1),
                      "statement": (f"{topic.label}: {len(rn)} of {len(recent)} papers ({rs:.1%}) published {_fmt(r_start)}–{_fmt(end)}, "
                                    f"vs {len(pn)} of {len(prior)} ({ps:.1%}) in {_fmt(p_start)}–{_fmt(p_end)}."),
                      "evidence": _ev(rn)})
    stmts.sort(key=lambda x: -abs(x["change_pp"]))
    return {"id": "research_topics", "title": "Research topic changes", "status": "measured", "period": period, "statements": stmts[:8],
            "basis": "Share of papers matching each topic's keyword rules, by publication date. Shares, not counts, because paper volume "
                     "reflects what the source lists rather than the size of the field.", "label": "descriptive"}


def _batch_key(b: str) -> tuple[int, int] | None:
    m = re.match(r"^(Winter|Spring|Summer|Fall) (\d{4})$", b or "")
    return (int(m.group(2)), SEASONS[m.group(1)]) if m else None


async def startup_section(s: AsyncSession) -> dict[str, Any]:
    rows = (await s.execute(text("select id, payload from records where record_type='STARTUP' and source_name='Y Combinator'"))).all()
    per: dict[str, list[int]] = {}
    for rid, p in rows:
        b = ((json.loads(p) if isinstance(p, str) else p).get("provenance") or {}).get("evidence", {}).get("batch") or ""
        if _batch_key(b):
            per.setdefault(b, []).append(rid)
    ordered = sorted(per, key=lambda b: _batch_key(b))  # type: ignore[arg-type,return-value]
    series = [{"batch": b, "startups": len(per[b])} for b in ordered]
    stmts = []
    for season in SEASONS:
        yrs = sorted((k[0], b) for b in per if (k := _batch_key(b)) and b.startswith(season))
        if len(yrs) >= 2:
            (_, b1), (_, b0) = yrs[-1], yrs[-2]
            stmts.append({"statement": f"YC {b1}: {len(per[b1])} AI startups listed, vs {len(per[b0])} in {b0}.",
                          "evidence": [{"id": i, "type": "startups"} for i in per[b1][:4]], "batches": [b0, b1]})
    return {"id": "startup_discovery", "title": "Startup discovery by YC batch", "status": "measured", "series": series, "statements": stmts,
            "basis": "AI-tagged companies currently listed in Y Combinator's directory, grouped by the batch YC reports. This is YC's own "
                     "chronology, not the date FrontierAtlas discovered them; recent batches may still be filling.", "label": "descriptive"}


def hiring_section(idx: A.Index) -> dict[str, Any]:
    jobs = [r for r in idx.by_type.get("JOB", []) if r.published]
    days = sorted({r.published for r in jobs})  # type: ignore[type-var]
    out: dict[str, Any] = {"id": "hiring", "title": "Hiring activity changes", "jobs": len(jobs),
                           "days": [d.isoformat() for d in days], "per_day": [{"date": d.isoformat(), "jobs": sum(r.published == d for r in jobs)} for d in days]}
    if len(days) < 14:
        out.update(status="insufficient_history", statements=[],
                   explanation=(f"Job postings cover {len(days)} publication day(s) ({days[0].isoformat()} to {days[-1].isoformat()}). " if days else "No dated job postings. ")
                   + "The job sources keep only postings from the last 24 hours, so a week-over-week comparison needs at least 14 days of daily collection.")
    else:
        last = days[-1]
        a = [r for r in jobs if r.published > last - timedelta(days=7)]
        b = [r for r in jobs if last - timedelta(days=14) < r.published <= last - timedelta(days=7)]  # type: ignore[operator]
        out.update(status="measured", statements=[{"statement": f"{len(a)} postings in the last 7 publication days vs {len(b)} in the 7 before.", "evidence": _ev(a)}])
    return out


async def roles_breakdown(s: AsyncSession) -> list[dict[str, Any]]:
    rows = (await s.execute(text("select json_extract(payload,'$.content.role_family'), count(*) from records where record_type='JOB' group by 1 order by 2 desc"))).all()
    return [{"role_family": r or "Other", "jobs": n} for r, n in rows]


async def metric_section(s: AsyncSession, hist: bool) -> dict[str, Any]:
    live = 0
    if hist:
        live = (await s.execute(text("select count(*) from metric_snapshots"))).scalar_one()
    stars = [(i, n, v) for i, n, v in (await s.execute(text(
        "select id, json_extract(payload,'$.content.title'), json_extract(payload,'$.content.github_stars') from records "
        "where record_type='RESEARCH_PAPER' and json_extract(payload,'$.content.github_stars') is not null"))).all()]
    vals = [v for _, _, v in stars]
    top = sorted(stars, key=lambda x: -x[2])[:5]
    snapshot = {"papers_with_stars": len(vals), "median": statistics.median(vals) if vals else None,
                "top": [{"id": i, "type": "papers", "name": n, "stars": v} for i, n, v in top]}
    if live < 2:
        return {"id": "metric_movement", "title": "Metric movement (GitHub stars)", "status": "insufficient_history", "snapshot": snapshot, "statements": [],
                "explanation": "Stars are stored once per paper (the baseline). Movement needs at least two recorded samples per paper; "
                               "live collection batches add one sample each run."}
    rows = (await s.execute(text(
        "select subject_key, min(value), max(value), count(*) from metric_snapshots where metric='github_stars' group by subject_key having count(*)>=2 "
        "order by max(value)-min(value) desc limit 10"))).all()
    return {"id": "metric_movement", "title": "Metric movement (GitHub stars)", "status": "measured", "snapshot": snapshot,
            "statements": [{"statement": f"{k[:12]}…: {lo:.0f} → {hi:.0f} stars over {n} samples", "subject": k} for k, lo, hi, n in rows]}


async def discovery_history_section(s: AsyncSession, hist: bool) -> dict[str, Any]:
    if not hist:
        return {"id": "new_records", "title": "Newly discovered records", "status": "no_history", "statements": []}
    rows = (await s.execute(text(
        "select date(p.observed_at), p.record_type, count(*) from presence_events p where p.event='appeared' group by 1, 2 order by 1"))).all()
    if not rows:
        return {"id": "new_records", "title": "Newly discovered records", "status": "insufficient_history", "statements": [],
                "explanation": "No live collection batch has run yet. The 6,708 baseline records are never counted as new; discoveries appear "
                               "here from the first live batch."}
    return {"id": "new_records", "title": "Newly discovered records", "status": "measured",
            "series": [{"date": d, "type": F.SLUG_OF.get(t, t), "new": n} for d, t, n in rows], "statements": []}


@router.get("/intelligence/changes", tags=["intelligence"], summary="Historical comparisons with evidence; trend labels gated by coverage")
async def intelligence_changes(idx: A.Index = Depends(get_index), s: AsyncSession = Session) -> dict[str, Any]:
    hist = await _has_history(s)
    cov = (await coverage(s))["data"] if hist else {"available": False, "emerging_labels_enabled": False, "live_collection_days": 0, "emerging_min_days": 30}
    hiring = hiring_section(idx)
    hiring["roles"] = await roles_breakdown(s)
    sections = [research_section(idx), await startup_section(s), hiring, await metric_section(s, hist), await discovery_history_section(s, hist)]
    return {"data": {"sections": sections, "coverage": cov},
            "meta": {"emerging_labels_enabled": bool(cov.get("emerging_labels_enabled")),
                     "rule": f"Snapshot-based emerging labels need {cov.get('emerging_min_days', 30)} days of reliable live collection; "
                             f"{cov.get('live_collection_days', 0)} recorded so far."}}
