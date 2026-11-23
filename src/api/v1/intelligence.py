"""Frontier Intelligence API (read-only): trend radar, market signals, innovation map, historical trends, brief.

All endpoints derive from an in-memory index built from ONE read-only scan of ``records`` (only the fields the topic
rules need, selected with JSON extraction) and ``entity_mappings``. The index is cached and rebuilt only when the
database fingerprint (row counts, max ids, last update) changes. No tables, indexes or migrations are added.
"""


import asyncio
import json
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import EntityMappingRow, RecordRow
from src.intelligence import analysis as A
from src.intelligence.brief import build_brief
from src.intelligence.topics import TOPIC_BY_ID, TOPICS

from . import fields as F
from .deps import ApiError, Paging, Session
from .entity_ids import encode_entity_id

router = APIRouter(prefix="/intelligence", tags=["intelligence"])

_FIELDS = ("title", "abstract", "description", "productName", "role_family", "summary", "entityName")
_ARRAYS = ("tags", "organizations")
_cache: dict[str, Any] = {"index": None, "lock": None, "loop": None}


def _lock() -> asyncio.Lock:
    """One build lock per event loop (an asyncio.Lock is bound to the loop that first awaits it)."""
    loop = asyncio.get_running_loop()
    if _cache["loop"] is not loop:
        _cache["lock"], _cache["loop"] = asyncio.Lock(), loop
    return _cache["lock"]


# ------------------------------------------------------------------ index cache
async def _fingerprint(s: AsyncSession) -> tuple[Any, ...]:
    r = (await s.execute(select(func.count(), func.max(RecordRow.id), func.max(RecordRow.updated_at)))).one()
    m = (await s.execute(select(func.count(), func.max(EntityMappingRow.id), func.sum(cast(EntityMappingRow.needs_review, Integer))))).one()
    return (*tuple(r), *tuple(m))


def _arr(v: Any) -> list[Any]:
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.startswith("["):
        try:
            out = json.loads(v)
            return out if isinstance(out, list) else []
        except ValueError:
            return []
    return []


async def get_index(s: AsyncSession = Session) -> A.Index:
    fp = await _fingerprint(s)
    idx: A.Index | None = _cache["index"]
    if idx is not None and idx.fingerprint == fp:
        return idx
    async with _lock():
        idx = _cache["index"]
        if idx is not None and idx.fingerprint == fp:
            return idx
        cols = [RecordRow.id, RecordRow.record_type, RecordRow.published_at, RecordRow.collected_at, RecordRow.source_name, RecordRow.source_url,
                RecordRow.entity_name, *(F.c(f) for f in _FIELDS), *(F.c(a) for a in _ARRAYS)]
        rows = []
        for r in (await s.execute(select(*cols))).all():
            content = {f: r[7 + i] for i, f in enumerate(_FIELDS)}
            content.update({a: _arr(r[7 + len(_FIELDS) + i]) for i, a in enumerate(_ARRAYS)})
            rows.append({"id": r[0], "record_type": r[1], "published_at": r[2], "collected_at": r[3], "source_name": r[4], "source_url": r[5],
                         "entity_name": r[6], "content": content})
        maps = (await s.execute(select(EntityMappingRow.source_url, EntityMappingRow.canonical_name, EntityMappingRow.needs_review))).all()
        idx = A.build_index(rows, [tuple(m) for m in maps], fingerprint=fp, now=datetime.now(UTC))
        _cache["index"] = idx
        return idx


Index = Depends(get_index)


# ------------------------------------------------------------------ shared filters
class Filters:
    def __init__(
        self,
        types: str | None = Query(None, max_length=60, description="Comma-separated categories: startups, products, papers, jobs, news. Default: all."),
        date_from: date | None = Query(None, description="Publication date from (inclusive). Records without a publication date are excluded when a date is set."),
        date_to: date | None = Query(None, description="Publication date to (inclusive)."),
    ) -> None:
        slugs = [t.strip() for t in (types or "").split(",") if t.strip()]
        bad = [t for t in slugs if t not in A.TYPE_OF_SLUG]
        if bad:
            raise ApiError(422, "invalid_type", f"Unknown category: {', '.join(bad)}", {"allowed": list(A.TYPE_OF_SLUG)})
        if date_from and date_to and date_from > date_to:
            raise ApiError(422, "invalid_range", "date_from is after date_to")
        self.types = [A.TYPE_OF_SLUG[t] for t in slugs] or list(A.TYPES)
        self.date_from, self.date_to = date_from, date_to

    def echo(self) -> dict[str, Any]:
        return {"types": [A.SLUG[t] for t in self.types], "date_from": self.date_from, "date_to": self.date_to}


def _org(name: str) -> dict[str, str]:
    return {"name": name, "id": encode_entity_id(name)}


def _rec(r: A.Rec, topic: str | None = None) -> dict[str, Any]:
    return {"type": r.slug, "id": r.id, "name": r.name, "published": r.published, "source": r.source,
            "organisations": [_org(o) for o in r.orgs], "mentioned": [_org(o) for o in r.mentioned],
            "matched_term": r.topics.get(topic) if topic else None, "topics": list(r.topics)}


def _meta(idx: A.Index, **extra: Any) -> dict[str, Any]:
    return {"index_built_at": idx.built_at, "records_indexed": len(idx.recs), **extra}


# ------------------------------------------------------------------ endpoints
@router.get("/coverage")
async def coverage(idx: A.Index = Index) -> dict[str, Any]:
    """Measured date coverage per category: publication-date span, days with records, gaps, collection days and which
    time granularities the data can support."""
    return {"data": A.coverage(idx), "meta": _meta(idx)}


@router.get("/topics")
async def topics(f: Filters = Depends(), idx: A.Index = Index) -> dict[str, Any]:
    """Trend Radar: topic frequency and distribution across categories, plus the emerging-topic test where coverage allows."""
    recs, undated = A.select(idx, f.types, f.date_from, f.date_to)
    return {"data": {"topics": A.topic_frequency(recs), "records": len(recs), "by_type": {A.SLUG[t]: sum(1 for r in recs if r.type == t) for t in f.types},
                     "undated_excluded": undated, "emerging": A.emerging(idx, f.types, f.date_to, f.date_from),
                     "topic_fields": {A.SLUG[t]: list(A.TOPIC_FIELDS[t]) for t in f.types}},
            "meta": _meta(idx, filters=f.echo())}


@router.get("/topics/{topic_id}")
async def topic_detail(topic_id: str, f: Filters = Depends(), paging: Paging = Depends(), idx: A.Index = Index) -> dict[str, Any]:
    """One topic: counts by category, matched terms, organisations (verified links and news co-mentions kept apart),
    publication-date timeline, and the supporting records (paginated, newest first)."""
    t = TOPIC_BY_ID.get(topic_id)
    if t is None:
        raise ApiError(404, "not_found", f"Unknown topic '{topic_id}'", {"topics": [x.id for x in TOPICS]})
    recs, undated = A.select(idx, f.types, f.date_from, f.date_to)
    hits = [r for r in recs if topic_id in r.topics]
    freq = next(x for x in A.topic_frequency(recs) if x["id"] == topic_id)
    hits_sorted = sorted(hits, key=lambda r: (r.published or date.min, r.id), reverse=True)
    page = hits_sorted[paging.offset: paging.offset + paging.page_size]
    weekly = A.timeline([r for r in recs if r.published], "week", topic_id)
    return {"data": {"topic": {"id": t.id, "label": t.label, "patterns": list(t.patterns)}, "frequency": freq,
                     "organisations": {"verified": [{**_org(o["name"]), "records": o["records"]} for o in A.org_activity(hits, 10)["verified"]],
                                       "mentioned": [{**_org(o["name"]), "articles": o["articles"]} for o in A.org_activity(hits, 10)["mentioned"]]},
                     "timeline": weekly, "records": [_rec(r, topic_id) for r in page], "undated_excluded": undated},
            "meta": {**_meta(idx, filters=f.echo()), **paging.meta(len(hits)).model_dump()}}


@router.get("/signals")
async def signals(f: Filters = Depends(), idx: A.Index = Index) -> dict[str, Any]:
    """Market Signals per category: record counts, sources, dated share, collection days, leading topics and organisations.
    These are counts of collected records (collection volume), not measures of market growth."""
    recs, undated = A.select(idx, f.types, f.date_from, f.date_to)
    cov = {c["type"]: c for c in A.coverage(idx)["categories"]}
    cats = []
    for t in f.types:
        rs = [r for r in recs if r.type == t]
        freq = [x for x in A.topic_frequency(rs) if x["records"]][:6]
        acts = A.org_activity(rs, 8)
        cats.append({
            "type": A.SLUG[t], "label": A.LABEL[t], "records": len(rs), "with_publication_date": sum(1 for r in rs if r.published),
            "with_topic": sum(1 for r in rs if r.topics), "with_verified_organisation": sum(1 for r in rs if r.orgs),
            "sources": cov[A.SLUG[t]]["sources"], "collection_days": cov[A.SLUG[t]]["collection_days"],
            "publication_first": cov[A.SLUG[t]]["publication_first"], "publication_last": cov[A.SLUG[t]]["publication_last"],
            "top_topics": [{"id": x["id"], "label": x["label"], "records": x["by_type"][A.SLUG[t]], "share": x["share_by_type"][A.SLUG[t]]} for x in freq],
            "top_organisations": [{**_org(o["name"]), "records": o["records"]} for o in acts["verified"]],
            "most_mentioned": [{**_org(o["name"]), "articles": o["articles"]} for o in acts["mentioned"]],
        })
    notes = A.coverage(idx)["notes"] + ["Signals count records collected by this platform's crawlers from specific sources. "
                                        "A larger count means more was collected, not that a market grew."]
    return {"data": {"categories": cats, "records": len(recs), "undated_excluded": undated, "notes": notes}, "meta": _meta(idx, filters=f.echo())}


@router.get("/timeline")
async def timeline_ep(
    type: Literal["all", "startups", "products", "papers", "jobs", "news"] = Query("papers", description="Category, or all dated categories"),
    granularity: Literal["day", "week", "month"] = Query("week"),
    topic: str | None = Query(None, max_length=40, description="Optional topic id: adds per-bucket topic counts and share"),
    date_from: date | None = Query(None), date_to: date | None = Query(None),
    idx: A.Index = Index,
) -> dict[str, Any]:
    """Historical Trends: publication-date buckets. Empty buckets are reported (has_records=false) and listed as gaps;
    nothing is interpolated. ``supported=false`` when the date span is too short for the granularity."""
    if topic and topic not in TOPIC_BY_ID:
        raise ApiError(404, "not_found", f"Unknown topic '{topic}'")
    if date_from and date_to and date_from > date_to:
        raise ApiError(422, "invalid_range", "date_from is after date_to")
    types = list(A.TYPES) if type == "all" else [A.TYPE_OF_SLUG[type]]
    recs, undated = A.select(idx, types, date_from, date_to)
    tl = A.timeline(recs, granularity, topic)
    tl["undated_excluded"] += undated
    return {"data": tl, "meta": _meta(idx, filters={"type": type, "granularity": granularity, "topic": topic, "date_from": date_from, "date_to": date_to})}


@router.get("/innovation-map")
async def innovation_map(
    f: Filters = Depends(),
    topic: str | None = Query(None, max_length=40, description="Only records matching this topic"),
    q: str | None = Query(None, max_length=120, description="Organisation name contains (case-insensitive)"),
    relation: Literal["all", "verified", "mentioned"] = Query("all", description="verified = linked records; mentioned = named in news"),
    paging: Paging = Depends(),
    idx: A.Index = Index,
) -> dict[str, Any]:
    """Organisations connected to startups, products, jobs (verified entity mappings) and news (co-mentions, reported separately).
    Research papers carry no organisation affiliation in the stored data and are linked to topics only."""
    if topic and topic not in TOPIC_BY_ID:
        raise ApiError(404, "not_found", f"Unknown topic '{topic}'")
    recs, undated = A.select(idx, f.types, f.date_from, f.date_to)
    rows = A.innovation_map(recs, topic)
    if relation == "verified":
        rows = [r for r in rows if r["verified_records"]]
    elif relation == "mentioned":
        rows = [r for r in rows if r["news_mentions"]]
    if q:
        rows = [r for r in rows if q.casefold() in r["name"].casefold()]
    page = rows[paging.offset: paging.offset + paging.page_size]
    for r in page:
        r["id"] = encode_entity_id(r["name"])
        r["co_mentioned_with"] = [{**c, "id": encode_entity_id(c["name"])} for c in r["co_mentioned_with"]]
    scoped = [r for r in recs if (not topic or topic in r.topics)]
    return {"data": {"organisations": page, "matrix": A.topic_org_matrix(rows, scoped), "undated_excluded": undated,
                     "semantics": {"verified": "Record linked to the organisation by a verified entity mapping (pending reviews excluded).",
                                   "mentioned": "Organisation named in a news article (evidence-validated tag). A co-mention is not a partnership, investment or ownership.",
                                   "papers": "Research papers have no organisation affiliation in the stored data."}},
            "meta": {**_meta(idx, filters={**f.echo(), "topic": topic, "q": q, "relation": relation}), **paging.meta(len(rows)).model_dump()}}


@router.get("/brief")
async def brief(idx: A.Index = Index) -> dict[str, Any]:
    """Intelligence Brief: evidence-based insights, each with observation, counts, organisations/topics, period,
    supporting record links and a confidence explanation. No predictions."""
    return {"data": build_brief(idx), "meta": _meta(idx)}


@router.get("/topics-catalog")
async def topics_catalog() -> dict[str, Any]:
    """The topic taxonomy: ids, labels and the exact keyword patterns used."""
    return {"data": [{"id": t.id, "label": t.label, "patterns": list(t.patterns)} for t in TOPICS],
            "meta": {"fields": {A.SLUG[k]: list(v) for k, v in A.TOPIC_FIELDS.items()}}}

