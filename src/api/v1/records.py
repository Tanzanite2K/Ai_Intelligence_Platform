"""Record explorers, record detail and global search."""


import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from src.database.models import EntityMappingRow, RecordRow

from . import fields as F
from .deps import ApiError, Paging, Session, _aware, summarize, unique_ci
from .entity_ids import encode_entity_id
from .schemas import FieldInfo, Item, NewsFacets, OrgRef, OrgTag, Page, RecordDetail, RecordSummary, SearchResult

router = APIRouter()

Sort = Literal["newest", "oldest", "name", "stars"]


def naive_utc(d: datetime) -> datetime:
    """Timestamps are stored as UTC; naive inputs are taken as UTC."""
    return d.astimezone(UTC).replace(tzinfo=None) if d.tzinfo else d


class Filters:
    """Query parameters shared by every explorer and by global search. Only stored fields are filterable."""

    def __init__(
        self,
        q: str | None = Query(None, max_length=200, description="Keyword(s); case-insensitive; every term must match"),
        source: str | None = Query(None, max_length=200, description="Exact source name, e.g. 'Y Combinator'"),
        date_from: datetime | None = Query(None, description="Published (or collected, when undated) on/after"),
        date_to: datetime | None = Query(None, description="Published (or collected, when undated) on/before"),
        category: str | None = Query(None, max_length=120, description="Startup tag / product task / job role family"),
        organisation: str | None = Query(None, max_length=300, description="Startup/maker/employer name (contains) or news organisation tag (exact, case-insensitive)"),
        tagged: bool | None = Query(None, description="News only: articles with (true) / without (false) validated organisation tags"),
        min_stars: int | None = Query(None, ge=0, description="Research papers: minimum live GitHub stars"),
        fresh_hours: int | None = Query(None, ge=1, le=24 * 365, description="Published within the last N hours"),
        location: str | None = Query(None, max_length=120),
        pricing_model: Literal["FREE", "FREEMIUM", "PAID", "ENTERPRISE"] | None = Query(None),
        remote: bool | None = Query(None, description="Jobs: remote only (true) / on-site only (false)"),
        verified_dates: bool | None = Query(None, description="Only records whose publication date was verified"),
        sort: Sort = Query("newest"),
    ) -> None:
        self.q, self.source, self.date_from, self.date_to = (q or "").strip() or None, source, date_from, date_to
        self.category, self.organisation, self.min_stars, self.fresh_hours = category, (organisation or "").strip() or None, min_stars, fresh_hours
        self.tagged = tagged
        self.location, self.pricing_model, self.remote, self.verified_dates, self.sort = location, pricing_model, remote, verified_dates, sort
        if date_from and date_to and date_from > date_to:
            raise ApiError(422, "invalid_filter", "date_from must not be after date_to")

    def conditions(self, record_type: str | None) -> list[ColumnElement[bool]]:
        cond: list[ColumnElement[bool]] = []
        if self.q:
            cond.append(F.keyword_filter(self.q))
        if self.source:
            cond.append(RecordRow.source_name == self.source)
        if self.date_from:
            cond.append(F.EFFECTIVE_DATE >= naive_utc(self.date_from))
        if self.date_to:
            cond.append(F.EFFECTIVE_DATE <= naive_utc(self.date_to))
        if self.category:
            cond.append(F.category_filter(record_type, self.category))
        if self.organisation:
            cond.append(or_(F.contains(F.ORGANISATION, self.organisation),
                            and_(F.RT == "NEWS", F.has_org_tag(self.organisation)),
                            F.contains(RecordRow.entity_name, self.organisation)))
        if self.tagged is not None:
            cond.append(and_(F.RT == "NEWS", F.has_any_org_tag() if self.tagged else ~F.has_any_org_tag()))
        if self.min_stars is not None:
            cond.append(and_(F.RT == "RESEARCH_PAPER", F.stars_int() >= self.min_stars))
        if self.fresh_hours:
            cond.append(RecordRow.published_at >= naive_utc(datetime.now(UTC) - timedelta(hours=self.fresh_hours)))
        if self.location:
            cond.append(F.contains(F.c("location"), self.location))
        if self.pricing_model:
            cond.append(F.c("pricingModel") == self.pricing_model)
        if self.remote is not None:
            cond.append(F.c_bool("is_remote").is_(self.remote) if self.remote else or_(F.c_bool("is_remote").is_(False), F.c("is_remote").is_(None)))
        if self.verified_dates:
            cond.append(RecordRow.date_verified.is_(True))
        return cond

    def order(self) -> list[Any]:
        if self.sort == "oldest":
            return [F.EFFECTIVE_DATE.asc(), RecordRow.id.asc()]
        if self.sort == "name":
            return [func.lower(F.NAME).asc(), RecordRow.id.asc()]
        if self.sort == "stars":
            return [F.stars_int().is_(None), F.stars_int().desc(), RecordRow.id.asc()]
        return [F.EFFECTIVE_DATE.desc(), RecordRow.id.desc()]


async def paged(s: AsyncSession, base: Select[Any], flt: Filters, paging: Paging) -> tuple[list[RecordSummary], int]:
    total = (await s.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    rows = (await s.execute(base.order_by(*flt.order()).offset(paging.offset).limit(paging.page_size))).scalars().all()
    return [summarize(r) for r in rows], total


def _check_sort(flt: Filters, record_type: str | None) -> None:
    if flt.sort == "stars" and record_type not in (None, "RESEARCH_PAPER"):
        raise ApiError(422, "invalid_sort", "sort=stars is only available for research papers (the only records with GitHub stars)")


def make_list_endpoint(slug: str, record_type: str) -> None:
    @router.get(f"/{slug}", response_model=Page[RecordSummary], name=f"list_{slug}", tags=[slug])
    async def list_records(flt: Filters = Depends(), paging: Paging = Depends(), s: AsyncSession = Session) -> Page[RecordSummary]:
        _check_sort(flt, record_type)
        base = select(RecordRow).where(RecordRow.record_type == record_type, *flt.conditions(record_type))
        items, total = await paged(s, base, flt, paging)
        return Page[RecordSummary](data=items, meta=paging.meta(total))

    @router.get(f"/{slug}/{{record_id}}", response_model=Item[RecordDetail], name=f"get_{slug}", tags=[slug])
    async def get_record(record_id: int = Path(ge=1), s: AsyncSession = Session) -> Item[RecordDetail]:
        row = await s.get(RecordRow, record_id)
        if row is None or row.record_type != record_type:
            raise ApiError(404, "not_found", f"No {slug[:-1] if slug != 'news' else 'news'} record with id {record_id}")
        return Item[RecordDetail](data=await detail(s, row))


@router.get("/news/facets", response_model=Item[NewsFacets], tags=["news"], summary="Tagged/untagged counts and organisation tag counts")
async def news_facets(top: int = Query(50, ge=1, le=500), s: AsyncSession = Session) -> Item[NewsFacets]:
    """Computed from the stored, evidence-validated organisation tags (content.organizations) only."""
    rows = (await s.execute(select(F.c("organizations")).where(RecordRow.record_type == "NEWS"))).scalars().all()
    counts: dict[str, list[Any]] = {}
    tagged = 0
    for raw in rows:
        tags = unique_ci([str(x) for x in (json.loads(raw) if isinstance(raw, str) else (raw or [])) if isinstance(x, str)])
        tagged += bool(tags)
        for t in tags:
            k = t.casefold()
            counts.setdefault(k, [t, 0])[1] += 1
    orgs = sorted(counts.values(), key=lambda x: (-x[1], x[0].casefold()))[:top]
    return Item[NewsFacets](data=NewsFacets(total=len(rows), tagged=tagged, untagged=len(rows) - tagged,
                                            organisations=[{"name": n, "id": encode_entity_id(n), "articles": k} for n, k in orgs]))


for _slug, _rt in F.TYPE_SLUGS.items():
    make_list_endpoint(_slug, _rt)


@router.get("/records/{record_id}", response_model=Item[RecordDetail], tags=["search"], summary="Any record by id (type-agnostic)")
async def get_any(record_id: int = Path(ge=1), s: AsyncSession = Session) -> Item[RecordDetail]:
    row = await s.get(RecordRow, record_id)
    if row is None:
        raise ApiError(404, "not_found", f"No record with id {record_id}")
    return Item[RecordDetail](data=await detail(s, row))


@router.get("/search", response_model=SearchResult, tags=["search"])
async def search(
    flt: Filters = Depends(),
    paging: Paging = Depends(),
    types: str | None = Query(None, description="Comma-separated: startups,products,papers,jobs,news (default: all)"),
    s: AsyncSession = Session,
) -> SearchResult:
    wanted = [t.strip() for t in (types or "").split(",") if t.strip()]
    bad = [t for t in wanted if t not in F.TYPE_SLUGS]
    if bad:
        raise ApiError(422, "invalid_filter", f"unknown record type(s): {bad}; allowed: {sorted(F.TYPE_SLUGS)}")
    rts = [F.TYPE_SLUGS[t] for t in wanted] or list(F.TYPE_SLUGS.values())
    single = rts[0] if len(rts) == 1 else None
    _check_sort(flt, single)
    cond = flt.conditions(single)
    counts_rows = (await s.execute(select(RecordRow.record_type, func.count()).where(*cond).group_by(RecordRow.record_type))).all()
    counts = {F.SLUG_OF[k]: v for k, v in counts_rows}
    base = select(RecordRow).where(RecordRow.record_type.in_(rts), *cond)
    items, total = await paged(s, base, flt, paging)
    orgs: list[dict[str, Any]] = []
    if flt.q and paging.page == 1:
        term = flt.q.lower()
        orows = (await s.execute(
            select(EntityMappingRow.canonical_name, func.count(), func.max(EntityMappingRow.needs_review))
            .where(or_(F.contains(EntityMappingRow.canonical_name, term), F.contains(EntityMappingRow.original_name, term)))
            .group_by(EntityMappingRow.canonical_name).order_by(func.count().desc()).limit(8)
        )).all()
        orgs = [{"id": encode_entity_id(n), "canonical_name": n, "mapping_count": c, "needs_review": bool(r)} for n, c, r in orows]
    return SearchResult(data=items, meta=paging.meta(total), counts_by_type=counts, organisations=orgs, query=flt.q, sort=flt.sort)


# ------------------------------------------------------------------ detail + provenance
LLM_FIELDS = {"pricingModel", "startupName", "organizations"}  # written only by evidence-validated LLM enrichment
ENRICHED = {"github_stars": "live GitHub API", "github_stars_checked_at": "live GitHub API", "github_stars_source": "live GitHub API"}


def field_infos(row: RecordRow) -> list[FieldInfo]:
    p = row.payload or {}
    ct: dict[str, Any] = p.get("content") or {}
    prov = p.get("provenance") or {}
    ev: dict[str, str] = prov.get("evidence") or {}
    llm = bool(prov.get("llmProvider"))
    out: list[FieldInfo] = []
    for k, v in ct.items():
        empty = v is None or v == "" or v == [] or v == {}
        if empty:
            out.append(FieldInfo(key=k, value=None, provenance="missing", note="not present in the stored source data"))
        elif k in LLM_FIELDS and llm and k in ev:
            out.append(FieldInfo(key=k, value=v, provenance="llm_validated", evidence=ev[k],
                                 note=f"extracted by {prov.get('llmProvider')}; accepted only because the quoted evidence was found in the source"))
        elif k in ENRICHED:
            out.append(FieldInfo(key=k, value=v, provenance="enriched", note=ENRICHED[k]))
        elif (k, row.record_type) in {("entityName", "STARTUP"), ("startupName", "PRODUCT")} and v != ct.get("rawName" if k == "entityName" else "rawStartupName"):
            out.append(FieldInfo(key=k, value=v, provenance="entity_resolution", note=f"canonical name; source wrote {ct.get('rawName') or ct.get('rawStartupName')!r}"))
        else:
            out.append(FieldInfo(key=k, value=v, provenance="source", evidence=ev.get(k)))
    return out


async def news_tags(s: AsyncSession, row: RecordRow, tags: list[str]) -> tuple[list[OrgTag], list[RecordSummary]]:
    """Every stored tag (with its mapping for this article, if any) and other articles sharing a tag via verified mappings."""
    maps: dict[str, EntityMappingRow] = {}
    for m in (await s.execute(select(EntityMappingRow).where(EntityMappingRow.source_url == row.source_url).order_by(EntityMappingRow.id))).scalars():
        maps.setdefault(m.canonical_name.casefold(), m)
    out = []
    for t in tags:
        m = maps.get(t.casefold())
        out.append(OrgTag(name=t, entity_id=encode_entity_id(m.canonical_name if m else t), mapped=m is not None,
                          match_method=m.match_method if m else None, confidence=round(m.confidence, 4) if m else None,
                          needs_review=bool(m and m.needs_review)))
    wanted = {t.casefold() for t in tags}
    verified = [m.canonical_name for k, m in maps.items() if k in wanted and not m.needs_review]
    related: list[RecordSummary] = []
    if verified:
        urls = select(EntityMappingRow.source_url).where(EntityMappingRow.canonical_name.in_(verified), EntityMappingRow.needs_review.is_(False))
        rel = (await s.execute(select(RecordRow).where(RecordRow.record_type == "NEWS", RecordRow.source_url.in_(urls), RecordRow.id != row.id)
                               .order_by(F.EFFECTIVE_DATE.desc(), RecordRow.id.desc()).limit(10))).scalars().all()
        related = [summarize(r) for r in rel]
    return out, related


async def detail(s: AsyncSession, row: RecordRow) -> RecordDetail:
    base = summarize(row)
    if row.record_type == "NEWS":  # several organisations per article: no single "resolved organisation"
        tags, related = await news_tags(s, row, base.organizations)
        ref = _aware(row.published_at) or _aware(row.collected_at)
        fresh = round((datetime.now(UTC) - ref).total_seconds() / 3600, 1) if ref else None
        return RecordDetail(**base.model_dump(), fields=field_infos(row), provenance=dict((row.payload or {}).get("provenance") or {}),
                            resolved_organisation=None, organisation_tags=tags, related=related, freshness_hours=fresh,
                            created_at=_aware(row.created_at), updated_at=_aware(row.updated_at))
    mrow = (await s.execute(select(EntityMappingRow).where(EntityMappingRow.source_url == row.source_url).order_by(EntityMappingRow.id).limit(1))).scalars().first()
    org = OrgRef(canonical_name=mrow.canonical_name, match_method=mrow.match_method, confidence=round(mrow.confidence, 4), needs_review=mrow.needs_review) if mrow else None
    related: list[RecordSummary] = []
    if mrow and not mrow.needs_review:  # a mapping pending review never links records together
        urls = select(EntityMappingRow.source_url).where(EntityMappingRow.canonical_name == mrow.canonical_name, EntityMappingRow.needs_review.is_(False))
        rel = (await s.execute(select(RecordRow).where(RecordRow.source_url.in_(urls), RecordRow.id != row.id)
                               .order_by(F.EFFECTIVE_DATE.desc()).limit(10))).scalars().all()
        related = [summarize(r) for r in rel]
    ref = _aware(row.published_at) or _aware(row.collected_at)
    fresh = round((datetime.now(UTC) - ref).total_seconds() / 3600, 1) if ref else None
    prov = dict((row.payload or {}).get("provenance") or {})
    return RecordDetail(**base.model_dump(), fields=field_infos(row), provenance=prov, resolved_organisation=org, related=related,
                        freshness_hours=fresh, created_at=_aware(row.created_at), updated_at=_aware(row.updated_at))
