"""Phase 7D/7E read-only organisation intelligence: timeline and evidence-backed claims.

Records are linked to an organisation exactly as on the existing organisation pages: through VERIFIED entity mappings
(``needs_review = false``, ``mapping.source_url = record.source_url``). Pending reviews are reported, never used.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import EntityMappingRow as M
from src.database.models import RecordRow
from src.history.evidence import build_claims

from . import fields as F
from .deps import ApiError, Session
from .entity_ids import decode_entity_id
from .history import BASELINE, _has_history, iso

router = APIRouter()


def _name_of(entity_id: str) -> str:
    name = decode_entity_id(entity_id)
    if name is None:
        raise ApiError(422, "invalid_id", "Malformed organisation id")
    return name


def _j(v: Any) -> Any:
    return json.loads(v) if isinstance(v, (str, bytes)) else (v or {})


async def _linked(s: AsyncSession, name: str) -> tuple[list[RecordRow], list[str]]:
    urls = select(M.source_url).where(M.canonical_name == name, M.needs_review.is_(False))
    rows = (await s.execute(select(RecordRow).where(RecordRow.source_url.in_(urls)).order_by(RecordRow.id))).scalars().all()
    review = (await s.execute(select(M.original_name).where(M.canonical_name == name, M.needs_review.is_(True)).distinct())).scalars().all()
    exists = (await s.execute(select(M.id).where(M.canonical_name == name).limit(1))).first()
    if not exists:
        raise ApiError(404, "not_found", f"No organisation named {name!r}")
    return list(rows), list(review)


def _title(r: RecordRow, c: dict[str, Any]) -> str:
    return str(c.get("entityName") if r.record_type == "STARTUP" else c.get("productName") if r.record_type == "PRODUCT" else c.get("title") or "")


@router.get("/entities/{entity_id}/timeline", tags=["organisations"], summary="Dated events for one organisation, each linked to its record")
async def org_timeline(entity_id: str, s: AsyncSession = Session) -> dict[str, Any]:
    name = _name_of(entity_id)
    rows, review = await _linked(s, name)
    hist = await _has_history(s)
    first_seen: dict[tuple[str, str], tuple[str, str]] = {}
    changes: list[tuple] = []
    if hist and rows:
        keys = [(r.record_type, r.dedup_hash) for r in rows]
        for rt in {k[0] for k in keys}:
            hs = [k[1] for k in keys if k[0] == rt]
            for i in range(0, len(hs), 400):
                chunk = hs[i:i + 400]
                ph = ",".join(f":h{j}" for j in range(len(chunk)))
                params = {"t": rt, **{f"h{j}": h for j, h in enumerate(chunk)}}
                for dh, at, origin in (await s.execute(text(
                        f"select o.dedup_hash, o.observed_at, o.observation_origin from record_observations o where o.record_type=:t and o.dedup_hash in ({ph}) "
                        f"and o.observed_at = (select min(x.observed_at) from record_observations x where x.record_type=o.record_type and x.dedup_hash=o.dedup_hash)"),
                        params)).all():
                    first_seen[(rt, dh)] = (str(at), origin)
                changes += [(rt, *x) for x in (await s.execute(text(
                    f"select dedup_hash, version_no, valid_from, changed_fields from record_versions where record_type=:t and version_no>1 and dedup_hash in ({ph})"),
                    params)).all()]
                changes += [(rt, x[0], None, x[2], x[1]) for x in (await s.execute(text(
                    f"select dedup_hash, event, observed_at from presence_events where record_type=:t and event in ('disappeared','reappeared') and dedup_hash in ({ph})"),
                    params)).all()]
    by_key = {(r.record_type, r.dedup_hash): r for r in rows}
    events: list[dict[str, Any]] = []
    for r in rows:
        c = _j(r.payload).get("content") or {}
        kind = {"STARTUP": "profile_listed", "PRODUCT": "product_listed", "JOB": "job_posted", "NEWS": "news_mention", "RESEARCH_PAPER": "paper_published"}[r.record_type]
        if r.published_at is not None:
            date, basis = iso(r.published_at), "published"
        elif (r.record_type, r.dedup_hash) in first_seen:
            at, origin = first_seen[(r.record_type, r.dedup_hash)]
            date, basis = iso(at), ("first_seen_on_or_before" if origin == BASELINE else "first_seen")
        else:
            date, basis = iso(r.collected_at), "collected"
        detail = c.get("role_family") if r.record_type == "JOB" else (r.payload and (_j(r.payload).get("provenance") or {}).get("evidence", {}).get("batch")) if r.record_type == "STARTUP" else None
        events.append({"date": date, "date_basis": basis, "kind": kind, "title": _title(r, c), "detail": detail,
                       "record": {"id": r.id, "type": F.SLUG_OF.get(r.record_type, r.record_type)}, "source": r.source_name, "url": r.source_url})
    for rt, dh, ver, at, extra in changes:
        r = by_key.get((rt, dh))
        if r is None:
            continue
        c = _j(r.payload).get("content") or {}
        if ver is not None:
            events.append({"date": iso(at), "date_basis": "observed_change", "kind": "record_changed", "title": _title(r, c),
                           "detail": ", ".join(json.loads(extra) if isinstance(extra, str) else (extra or [])), "record": {"id": r.id, "type": F.SLUG_OF.get(rt, rt)},
                           "source": r.source_name, "url": r.source_url})
        else:
            events.append({"date": iso(at), "date_basis": "observed", "kind": f"record_{extra}", "title": _title(r, c), "detail": None,
                           "record": {"id": r.id, "type": F.SLUG_OF.get(rt, rt)}, "source": r.source_name, "url": r.source_url})
    events.sort(key=lambda e: e["date"] or "", reverse=True)
    months = Counter((e["date"] or "")[:7] for e in events if e["date_basis"] == "published")
    note_parts = []
    if any(e["date_basis"] == "first_seen_on_or_before" for e in events):
        note_parts.append("Startup profiles and products carry no publication date; they are placed at their first-seen date, "
                          "which for baseline records is a bound (on or before 2026-11-21).")
    if review:
        note_parts.append(f"{len(review)} name(s) awaiting entity-resolution review are not included: {', '.join(review[:5])}.")
    return {"data": {"organisation": name, "events": events, "counts": dict(Counter(e["kind"] for e in events)),
                     "published_per_month": [{"month": m, "events": n} for m, n in sorted(months.items())]},
            "meta": {"records": len(rows), "history_available": hist}, "note": " ".join(note_parts) or None}


@router.get("/entities/{entity_id}/evidence", tags=["organisations"], summary="Evidence-backed claims with independent-source corroboration")
async def org_evidence(entity_id: str, s: AsyncSession = Session) -> dict[str, Any]:
    name = _name_of(entity_id)
    rows, review = await _linked(s, name)
    recs = []
    for r in rows:
        p = _j(r.payload)
        recs.append({"id": r.id, "type": r.record_type, "source": r.source_name, "url": r.source_url, "published": iso(r.published_at),
                     "content": p.get("content") or {}, "evidence": (p.get("provenance") or {}).get("evidence") or {}})
    out = build_claims(name, recs, needs_review=bool(review))
    out["review"] = {"pending_names": review, "note": "Claims are flagged while any name mapped to this organisation awaits human review."} if review else None
    return {"data": out, "meta": {"records": len(rows)}}
