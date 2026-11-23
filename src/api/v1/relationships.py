"""Entity relationship overview and data-quality aggregates (read-only, from existing resolution data)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import Integer, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import CanonicalEntityRow, EntityMappingRow, RecordRow

from . import fields as F
from .deps import Session
from .entity_ids import encode_entity_id

router = APIRouter()
M = EntityMappingRow


@router.get("/relationships/overview", tags=["organisations"])
async def relationships_overview(top: int = Query(15, ge=1, le=50), s: AsyncSession = Session) -> dict[str, Any]:
    """What the entity-resolution data actually links. Record links use VERIFIED mappings only (needs_review = false)."""
    total_orgs = (await s.execute(select(func.count(func.distinct(M.canonical_name))))).scalar() or 0
    seed_total = (await s.execute(select(func.count()).select_from(CanonicalEntityRow))).scalar() or 0
    seed_with_maps = (await s.execute(
        select(func.count(func.distinct(M.canonical_name))).where(M.canonical_name.in_(select(CanonicalEntityRow.canonical_name)))
    )).scalar() or 0
    methods = [
        {"method": m, "mappings": n, "min_confidence": round(lo, 4), "max_confidence": round(hi, 4)}
        for m, n, lo, hi in (await s.execute(
            select(M.match_method, func.count(), func.min(M.confidence), func.max(M.confidence)).group_by(M.match_method).order_by(func.count().desc())
        )).all()
    ]
    # Outer join + filtered aggregate (instead of WHERE needs_review) lets SQLite use an automatic index on
    # records.source_url: ~20 ms instead of ~20 s on the live database.
    rows = (await s.execute(
        select(M.canonical_name, RecordRow.record_type, func.count(func.distinct(case((M.needs_review.is_(False), RecordRow.id)))))
        .outerjoin(RecordRow, RecordRow.source_url == M.source_url)
        .group_by(M.canonical_name, RecordRow.record_type)
    )).all()
    per_org: dict[str, dict[str, int]] = {}
    for name, rt, n in rows:
        if rt is not None and n:
            per_org.setdefault(name, {})[F.SLUG_OF.get(rt, rt)] = n
    by_types: dict[int, int] = {}
    for counts in per_org.values():
        by_types[len(counts)] = by_types.get(len(counts), 0) + 1
    multi = sorted(((n, c) for n, c in per_org.items() if len(c) >= 2), key=lambda x: (-len(x[1]), -sum(x[1].values()), x[0].lower()))
    top_orgs = sorted(per_org.items(), key=lambda x: (-sum(x[1].values()), x[0].lower()))[:top]

    alias_q = select(M.canonical_name).where(M.needs_review.is_(False)).group_by(M.canonical_name).having(func.count(func.distinct(M.original_name)) > 1)
    alias_total = (await s.execute(select(func.count()).select_from(alias_q.subquery()))).scalar() or 0
    alias_names = [r[0] for r in (await s.execute(
        alias_q.order_by(func.count(func.distinct(M.original_name)).desc(), M.canonical_name).limit(top)
    )).all()]
    originals: dict[str, list[str]] = {}
    for c, o in (await s.execute(select(M.canonical_name, M.original_name).where(M.canonical_name.in_(alias_names), M.needs_review.is_(False)).distinct())).all():
        originals.setdefault(c, []).append(o)

    unmatched = (await s.execute(
        select(func.count()).select_from(M).outerjoin(RecordRow, RecordRow.source_url == M.source_url).where(RecordRow.id.is_(None))
    )).scalar() or 0
    news_tagged = (await s.execute(
        select(func.count()).where(RecordRow.record_type == "NEWS", F.c("organizations").is_not(None), F.c("organizations") != "[]")
    )).scalar() or 0
    review_total = (await s.execute(select(func.count()).where(M.needs_review.is_(True)))).scalar() or 0

    def org(name: str, counts: dict[str, int]) -> dict[str, Any]:
        return {"id": encode_entity_id(name), "name": name, "records_by_type": counts, "records": sum(counts.values())}

    return {
        "data": {
            "organisations": total_orgs,
            "organisations_with_records": len(per_org),
            "seed_organisations": seed_total,
            "seed_organisations_with_mappings": seed_with_maps,
            "match_methods": methods,
            "organisations_by_linked_types": {str(k): v for k, v in sorted(by_types.items())},
            "multi_type_organisations": {"total": len(multi), "top": [org(n, c) for n, c in multi[:top]]},
            "top_organisations": [org(n, c) for n, c in top_orgs],
            "alias_merges": {"total": alias_total, "top": [{"id": encode_entity_id(c), "name": c, "original_names": sorted(originals.get(c, []))} for c in alias_names]},
            "unmatched_mappings": unmatched,
            "pending_reviews": review_total,
            "news_articles_with_validated_tags": news_tagged,
        },
        "unavailable": {
            "co_mentions": "Organisation-to-organisation links come only from evidence-validated news tags; "
            + ("none are stored yet." if news_tagged == 0 else f"{news_tagged} tagged article(s) exist."),
            "papers": "Research papers carry no organisation field, so no paper is linked to an organisation.",
            "ownership": "Parent/subsidiary, investment and partnership relationships are not collected by any source.",
        },
    }


@router.get("/dashboard/quality", tags=["dashboard"])
async def quality(s: AsyncSession = Session) -> dict[str, Any]:
    """Validation status and date verification per record type, as stored at ingestion."""
    rows = (await s.execute(
        select(RecordRow.record_type, RecordRow.validation_status, func.count()).group_by(RecordRow.record_type, RecordRow.validation_status)
    )).all()
    status: dict[str, dict[str, int]] = {}
    for rt, st, n in rows:
        status.setdefault(F.SLUG_OF.get(rt, rt), {})[st] = n
    dates = (await s.execute(
        select(RecordRow.record_type, func.count(), func.sum(cast(RecordRow.published_at.is_not(None), Integer)), func.sum(cast(RecordRow.date_verified.is_(True), Integer)))
        .group_by(RecordRow.record_type)
    )).all()
    return {
        "data": {
            "validation_status": status,
            "dates": [{"type": F.SLUG_OF.get(t, t), "total": n, "dated": int(d or 0), "date_verified": int(v or 0)} for t, n, d, v in dates],
        },
        "meta": {"statuses": {"VERIFIED": "every stored field traced to source evidence", "PARTIAL": "optional fields dropped for lack of evidence",
                              "UNVERIFIED": "publication date could not be established", "REJECTED": "rejected at validation"}},
    }


# ------------------------------------------------------------------ review audit notes (documentation, not database)
REVIEW_DOC = Path("docs/entity_review.md")


def _clean(cell: str) -> str:
    return re.sub(r"\*\*|`", "", cell).strip()


def review_doc_notes(path: Path | None = None) -> dict[str, dict[str, str]]:
    """Parse the 'REVIEW queue' table of the manual audit document, keyed by lower-cased raw name.

    The resolver does not persist its candidate/reason; these notes were written during the audit on 2026-11-21."""
    try:
        text = (path or REVIEW_DOC).read_text(encoding="utf-8")
    except OSError:
        return {}
    m = re.search(r"## REVIEW queue.*?\n(.*?)(?:\n## |\Z)", text, re.S)
    if not m:
        return {}
    out: dict[str, dict[str, str]] = {}
    for line in m.group(1).splitlines():
        if not line.startswith("|") or line.startswith("|---") or line.startswith("| Raw name"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        raw = re.sub(r"\s*\(.*\)\s*$", "", _clean(cells[0])).strip()
        candidate = re.sub(r"\s*\(.*\)\s*$", "", _clean(cells[1])).strip()
        out[raw.lower()] = {"raw_label": _clean(cells[0]), "candidate": candidate, "candidate_label": _clean(cells[1]),
                            "reason": _clean(cells[2]), "evidence": _clean(cells[3]), "assessment": _clean(cells[4])}
    return out
