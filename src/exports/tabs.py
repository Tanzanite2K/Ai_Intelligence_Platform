"""Build the six export tabs (column names = assessment field paths) from stored records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from src.database.repository import Repository


@dataclass(slots=True)
class Tab:
    title: str
    columns: list[str]
    rows: list[list[Any]]


def _get(d: dict[str, Any], path: str) -> Any:
    cur: Any = d
    for p in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    if isinstance(cur, list):
        return "; ".join(str(x) for x in cur)
    if isinstance(cur, dict):
        return "; ".join(f"{k}={v}" for k, v in cur.items())
    return cur


STARTUP_COLS = [
    "schemaVersion", "recordType", "source.name", "source.url", "content.entityName", "content.data.employeeCount", "collectedAt",
    "content.rawName", "content.website", "content.description", "content.location",
    "provenance.extractionMethod", "provenance.validationStatus", "provenance.confidence",
]
PRODUCT_COLS = [
    "schemaVersion", "recordType", "source.name", "source.url", "content.startupName", "content.pricingModel", "collectedAt",
    "content.productName", "content.rawStartupName", "content.website", "content.description",
    "provenance.extractionMethod", "provenance.llmProvider", "provenance.evidence.pricingModel", "provenance.validationStatus", "provenance.confidence",
]
RESEARCH_COLS = [
    "schemaVersion", "recordType", "content.title", "content.authors", "content.paper_url", "content.github_url",
    "content.github_stars", "content.published_date", "source.name", "source.url", "collectedAt",
    "content.arxiv_id", "content.github_stars_checked_at", "content.github_stars_source", "provenance.evidence.github_url",
]
JOB_COLS = [
    "schemaVersion", "recordType", "content.company", "content.date", "content.is_remote", "content.role_family",
    "content.title", "content.location", "content.job_url", "content.raw_company", "content.date_source", "freshness_basis",
    "source.name", "source.url", "collectedAt",
]
NEWS_COLS = [
    "schemaVersion", "recordType", "content.title", "content.published_date", "content.article_url", "content.author",
    "content.organizations", "content.word_count", "content.summary", "content.date_source", "freshness_basis",
    "source.name", "source.url", "collectedAt",
]
MAPPING_COLS = ["originalName", "canonicalName", "matchMethod", "confidence", "needsReview", "entityKind", "sourceUrl", "resolvedAt"]


def _rows(payloads: list[dict[str, Any]], cols: list[str], extra: dict[str, Any] | None = None) -> list[list[Any]]:
    out = []
    for p in payloads:
        row = []
        for c in cols:
            row.append(extra.get(c, None) if extra and c in extra else _get(p, c))
        out.append(row)
    return out


def _is_fresh(p: dict[str, Any], date_field: str, cutoff: datetime) -> tuple[bool, str]:
    c = p.get("content", {})
    src = c.get("date_source") or ""
    if c.get("date_verified") and c.get(date_field):
        dt = datetime.fromisoformat(c[date_field].replace("Z", "+00:00"))
        return dt >= cutoff, "published_timestamp"
    if src.startswith("heuristic:new-since-last-run"):
        # bound: first seen after a successful run that itself was < window ago
        ts = src[src.find("(") + 1 : src.find(")")]
        try:
            return datetime.fromisoformat(ts) >= cutoff, "new_since_last_run"
        except ValueError:
            return False, ""
    return False, ""


async def build_tabs(repo: Repository, *, now: datetime | None = None, window_hours: int = 24, research_require_stars: bool = True) -> list[Tab]:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=window_hours)
    startups = await repo.fetch_records("STARTUP")
    products = await repo.fetch_records("PRODUCT")
    papers = await repo.fetch_records("RESEARCH_PAPER")
    if research_require_stars:
        papers = [p for p in papers if p["content"].get("github_stars") is not None]
    jobs_rows, news_rows = [], []
    for p in await repo.fetch_records("JOB"):
        ok, basis = _is_fresh(p, "date", cutoff)  # re-checked at export time, not crawl time
        if ok:
            jobs_rows += _rows([p], JOB_COLS, {"freshness_basis": basis})
    for p in await repo.fetch_records("NEWS"):
        ok, basis = _is_fresh(p, "published_date", cutoff)
        if ok:
            news_rows += _rows([p], NEWS_COLS, {"freshness_basis": basis})
    mappings = await repo.fetch_mappings()
    return [
        Tab("Startups", STARTUP_COLS, _rows(startups, STARTUP_COLS)),
        Tab("Products", PRODUCT_COLS, _rows(products, PRODUCT_COLS)),
        Tab("Research Papers", RESEARCH_COLS, _rows(papers, RESEARCH_COLS)),
        Tab("Jobs", JOB_COLS, jobs_rows),
        Tab("News", NEWS_COLS, news_rows),
        Tab("Entity Mapping Log", MAPPING_COLS, [[m.get(c) for c in MAPPING_COLS] for m in mappings]),
    ]
