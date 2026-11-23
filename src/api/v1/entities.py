"""Organisations (existing entity-resolution output) and the read-only review queue."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Integer, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import CanonicalEntityRow, EntityMappingRow, RecordRow

from . import fields as F
from .deps import ApiError, Paging, Session, _aware, summarize
from .entity_ids import decode_entity_id, encode_entity_id
from .records import Filters, paged
from .relationships import review_doc_notes
from .schemas import EntityDetail, EntitySummary, Item, Page, RecordSummary, ReviewItem

router = APIRouter()
M = EntityMappingRow

UNAVAILABLE = {
    "papers": "Stored research papers carry no organisation field, so none are linked to organisations.",
    "news": "News articles are linked only through evidence-validated organisation tags (each name backed by a verbatim quote).",
    "description": "No organisation description is stored by the ingestion pipeline.",
}


def _entity_name(entity_id: str) -> str:
    name = decode_entity_id(entity_id)
    if name is None:
        raise ApiError(422, "invalid_id", "Malformed organisation id")
    return name


def _summary(name: str, mappings: int, records: int, review: Any, seed: CanonicalEntityRow | None, methods: dict[str, int],
             review_records: int = 0) -> EntitySummary:
    return EntitySummary(
        id=encode_entity_id(name), canonical_name=name, seed_id=seed.id if seed else None, origin="SEED" if seed else "DISCOVERED",
        domain=seed.domain if seed else None, aliases=list(seed.aliases or []) if seed else [], mapping_count=mappings,
        record_count=records, review_record_count=review_records, needs_review=bool(review), match_methods=methods,
    )


@router.get("/entities", response_model=Page[EntitySummary], tags=["organisations"])
async def list_entities(
    q: str | None = Query(None, max_length=200, description="Matches canonical or original (raw) names"),
    origin: Literal["SEED", "DISCOVERED"] | None = Query(None, description="SEED = curated organisation list"),
    needs_review: bool | None = Query(None),
    sort: Literal["records", "name"] = Query("records"),
    paging: Paging = Depends(),
    s: AsyncSession = Session,
) -> Page[EntitySummary]:
    # records are counted only through VERIFIED mappings (needs_review = false); review-mapped records are counted apart
    verified_rec = func.count(func.distinct(case((M.needs_review.is_(False), RecordRow.id))))
    review_rec = func.count(func.distinct(case((M.needs_review.is_(True), RecordRow.id))))
    grp = (
        select(M.canonical_name.label("name"), func.count(func.distinct(M.id)).label("mappings"), verified_rec.label("records"),
               review_rec.label("review_records"), func.max(cast(M.needs_review, Integer)).label("review"))
        .select_from(M).outerjoin(RecordRow, RecordRow.source_url == M.source_url)
        .group_by(M.canonical_name)
    )
    if q:
        grp = grp.where(or_(F.contains(M.canonical_name, q), F.contains(M.original_name, q)))
    seed_names = select(CanonicalEntityRow.canonical_name)
    if origin == "SEED":
        grp = grp.where(M.canonical_name.in_(seed_names))
    elif origin == "DISCOVERED":
        grp = grp.where(M.canonical_name.not_in(seed_names))
    if needs_review is not None:
        grp = grp.having(func.max(cast(M.needs_review, Integer)) == (1 if needs_review else 0))
    sub = grp.subquery()
    total = (await s.execute(select(func.count()).select_from(sub))).scalar() or 0
    order = [sub.c.records.desc(), func.lower(sub.c.name)] if sort == "records" else [func.lower(sub.c.name)]
    rows = (await s.execute(select(sub).order_by(*order).offset(paging.offset).limit(paging.page_size))).all()
    names = [r.name for r in rows]
    seeds = {e.canonical_name: e for e in (await s.execute(select(CanonicalEntityRow).where(CanonicalEntityRow.canonical_name.in_(names)))).scalars()}
    methods: dict[str, dict[str, int]] = {}
    for n, m, cnt in (await s.execute(select(M.canonical_name, M.match_method, func.count()).where(M.canonical_name.in_(names)).group_by(M.canonical_name, M.match_method))).all():
        methods.setdefault(n, {})[m] = cnt
    return Page[EntitySummary](data=[_summary(r.name, r.mappings, r.records, r.review, seeds.get(r.name), methods.get(r.name, {}), r.review_records) for r in rows],
                               meta=paging.meta(total))


@router.get("/entities/{entity_id}", response_model=Item[EntityDetail], tags=["organisations"])
async def get_entity(entity_id: str, s: AsyncSession = Session) -> Item[EntityDetail]:
    name = _entity_name(entity_id)
    maps = (await s.execute(select(M).where(M.canonical_name == name).order_by(M.id).limit(500))).scalars().all()
    if not maps:
        raise ApiError(404, "not_found", "No organisation with this id")
    mcount = (await s.execute(select(func.count()).where(M.canonical_name == name))).scalar() or 0
    urls = select(M.source_url).where(M.canonical_name == name, M.needs_review.is_(False))
    rurls = select(M.source_url).where(M.canonical_name == name, M.needs_review.is_(True))
    by_type = dict((await s.execute(select(RecordRow.record_type, func.count(func.distinct(RecordRow.id))).where(RecordRow.source_url.in_(urls)).group_by(RecordRow.record_type))).all())
    review_by_type = dict((await s.execute(select(RecordRow.record_type, func.count(func.distinct(RecordRow.id))).where(RecordRow.source_url.in_(rurls)).group_by(RecordRow.record_type))).all())
    seed = (await s.execute(select(CanonicalEntityRow).where(CanonicalEntityRow.canonical_name == name))).scalars().first()
    methods: dict[str, int] = {}
    for m in maps:
        methods[m.match_method] = methods.get(m.match_method, 0) + 1
    base = _summary(name, mcount, sum(by_type.values()), any(m.needs_review for m in maps), seed, methods, sum(review_by_type.values()))
    return Item[EntityDetail](data=EntityDetail(
        **base.model_dump(),
        records_by_type={F.SLUG_OF.get(k, k): v for k, v in by_type.items()},
        review_records_by_type={F.SLUG_OF.get(k, k): v for k, v in review_by_type.items()},
        original_names=sorted({m.original_name for m in maps}),
        mappings=[{"id": m.id, "original_name": m.original_name, "match_method": m.match_method, "confidence": round(m.confidence, 4),
                   "needs_review": m.needs_review, "source_url": m.source_url, "resolved_at": _aware(m.resolved_at)} for m in maps[:200]],
        unavailable=UNAVAILABLE,
    ))


@router.get("/entities/{entity_id}/records", response_model=Page[RecordSummary], tags=["organisations"])
async def entity_records(
    entity_id: str,
    type: Literal["startups", "products", "papers", "jobs", "news"] | None = Query(None),
    mapping: Literal["verified", "review"] = Query("verified", description="verified = confirmed mappings only; review = mappings pending human review"),
    flt: Filters = Depends(),
    paging: Paging = Depends(),
    s: AsyncSession = Session,
) -> Page[RecordSummary]:
    """Records linked to the organisation through existing entity mappings (mapping.source_url = record.source_url).
    By default only VERIFIED mappings are used; `mapping=review` returns records whose mapping awaits human review."""
    name = _entity_name(entity_id)
    urls = select(M.source_url).where(M.canonical_name == name, M.needs_review.is_(mapping == "review"))
    rt = F.TYPE_SLUGS[type] if type else None
    base = select(RecordRow).where(RecordRow.source_url.in_(urls), *flt.conditions(rt))
    if rt:
        base = base.where(RecordRow.record_type == rt)
    items, total = await paged(s, base, flt, paging)
    return Page[RecordSummary](data=items, meta=paging.meta(total))


@router.get("/entities/{entity_id}/related", tags=["organisations"])
async def related_entities(entity_id: str, s: AsyncSession = Session) -> dict[str, Any]:
    """Organisations co-mentioned with this one in evidence-validated news tags — the only relationship the data records."""
    name = _entity_name(entity_id)
    news = (await s.execute(select(RecordRow).where(RecordRow.record_type == "NEWS", F.has_org_tag(name)).limit(500))).scalars().all()
    counts: dict[str, int] = {}
    for r in news:
        for o in (r.payload.get("content") or {}).get("organizations") or []:
            if isinstance(o, str) and o.casefold() != name.casefold():
                counts[o] = counts.get(o, 0) + 1
    data = [{"name": n, "id": encode_entity_id(n), "co_mentions": c, "basis": "news organisation tags"} for n, c in sorted(counts.items(), key=lambda x: -x[1])[:50]]
    note = None if data else "No relationships recorded: relationships are derived only from validated news organisation tags, and none link this organisation yet."
    return {"data": data, "meta": {"news_articles_considered": len(news)}, "note": note}


@router.get("/reviews", response_model=Page[ReviewItem], tags=["entity review"])
async def list_reviews(paging: Paging = Depends(), s: AsyncSession = Session) -> Page[ReviewItem]:
    """Read-only human-review queue (entity_mappings.needs_review). Decisions are not recorded through the API."""
    total = (await s.execute(select(func.count()).where(M.needs_review.is_(True)))).scalar() or 0
    rows = (await s.execute(select(M).where(M.needs_review.is_(True)).order_by(M.id).offset(paging.offset).limit(paging.page_size))).scalars().all()
    recs = {r.source_url: r for r in (await s.execute(select(RecordRow).where(RecordRow.source_url.in_([m.source_url for m in rows])))).scalars()}
    note = ("Kept as a separate entity (no merge) until a human decides. The database stores only the REVIEW flag and 0.5 confidence; "
            "the candidate and reason come from the manual audit notes in docs/entity_review.md. Decisions are applied via "
            "src/entity_resolution/seed.py + rebuild-entities, not through this API.")
    notes = review_doc_notes()
    cand_names = [n["candidate"] for n in notes.values()]
    cand_stats = {c: (n, r) for c, n, r in (await s.execute(
        select(M.canonical_name, func.count(), func.count(func.distinct(RecordRow.id))).outerjoin(RecordRow, RecordRow.source_url == M.source_url)
        .where(M.canonical_name.in_(cand_names), M.needs_review.is_(False)).group_by(M.canonical_name))).all()}
    shared = dict((await s.execute(
        select(M.canonical_name, func.count()).where(M.canonical_name.in_([m.canonical_name for m in rows]), M.needs_review.is_(False)).group_by(M.canonical_name))).all())
    items = []
    for m in rows:
        a = notes.get(m.original_name.lower())
        cand = a["candidate"] if a else None
        ce = {"id": encode_entity_id(cand), "canonical_name": cand, "verified_mappings": cand_stats[cand][0], "records": cand_stats[cand][1]} if cand in cand_stats else None
        items.append(ReviewItem(
            id=m.id, original_name=m.original_name, canonical_name=m.canonical_name, match_method=m.match_method, confidence=round(m.confidence, 4),
            source_url=m.source_url, resolved_at=_aware(m.resolved_at), status="PENDING", proposed_candidate=a["candidate_label"] if a else None,
            reason=a["reason"] if a else None, audit=a, candidate_entity=ce, shares_canonical_with=int(shared.get(m.canonical_name, 0)),
            source_record=summarize(recs[m.source_url]) if m.source_url in recs else None, note=note))
    return Page[ReviewItem](data=items, meta=paging.meta(total))

