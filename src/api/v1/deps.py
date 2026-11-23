"""Shared dependencies: DB session, pagination, admin auth, rate limiting, serialisation."""


import math
import secrets
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, Header, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.database.models import RecordRow

from .schemas import PageMeta, RecordSummary

DEFAULT_PAGE_SIZE = 24
MAX_PAGE_SIZE = 100


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str, details: Any | None = None) -> None:
        super().__init__(status_code=status, detail=message)
        self.code, self.message, self.details = code, message, details


async def get_session(request: Request):  # type: ignore[no-untyped-def]
    db = request.app.state.read_db
    async with db.sessionmaker() as s:  # read-only use: no commit
        yield s


Session = Depends(get_session)


class Paging:
    def __init__(self, page: int = Query(1, ge=1, le=100_000), page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)) -> None:
        self.page, self.page_size = page, page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    def meta(self, total: int) -> PageMeta:
        return PageMeta(page=self.page, page_size=self.page_size, total=total, total_pages=max(1, math.ceil(total / self.page_size)) if total else 0)


def require_admin(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")) -> None:
    """Admin operations need ADMIN_API_KEY; when it is not configured they are disabled, never open."""
    configured = get_settings().admin_api_key
    if configured is None:
        raise ApiError(503, "admin_disabled", "Administrative operations are disabled: ADMIN_API_KEY is not configured on the server.")
    if not x_admin_key or not secrets.compare_digest(x_admin_key.encode(), configured.get_secret_value().encode()):
        raise ApiError(401, "unauthorized", "A valid X-Admin-Key header is required for this operation.")


class RateLimiter:
    """Sliding one-minute window per client address. In-process: correct for a single API worker."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = {}

    def check(self, key: str) -> float | None:
        if self.per_minute <= 0:
            return None
        now = time.monotonic()
        q = self.hits.setdefault(key, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return max(1.0, 60 - (now - q[0]))
        q.append(now)
        if len(self.hits) > 10_000:  # bound memory
            for k in [k for k, v in self.hits.items() if not v][:5000]:
                self.hits.pop(k, None)
        return None


def _aware(d: datetime | None) -> datetime | None:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _list(v: Any) -> list[str]:
    return [str(x) for x in v] if isinstance(v, list) else []


def unique_ci(items: list[str]) -> list[str]:
    """De-duplicate case-insensitively, keeping the first spelling and the original order."""
    seen: set[str] = set()
    out = []
    for x in items:
        k = x.strip().casefold()
        if k and k not in seen:
            seen.add(k)
            out.append(x.strip())
    return out


def primary_url(rt: str, content: dict[str, Any]) -> str | None:
    return {"STARTUP": content.get("website"), "PRODUCT": content.get("website"), "RESEARCH_PAPER": content.get("paper_url"),
            "JOB": content.get("job_url"), "NEWS": content.get("article_url")}.get(rt)


def summarize(row: RecordRow) -> RecordSummary:
    p = row.payload or {}
    ct: dict[str, Any] = p.get("content") or {}
    rt = row.record_type
    name = {"STARTUP": ct.get("entityName"), "PRODUCT": ct.get("productName")}.get(rt, ct.get("title"))
    desc = {"RESEARCH_PAPER": ct.get("abstract"), "NEWS": ct.get("summary")}.get(rt, ct.get("description"))
    org = {"STARTUP": ct.get("entityName"), "PRODUCT": ct.get("startupName"), "JOB": ct.get("company")}.get(rt)
    stars = ct.get("github_stars")
    return RecordSummary(
        id=row.id, type=rt, name=name, description=(desc[:400] if isinstance(desc, str) else None), organisation=org,
        url=primary_url(rt, ct), source_name=row.source_name, source_url=row.source_url,
        published_at=_aware(row.published_at), collected_at=_aware(row.collected_at) or datetime.now(UTC),
        date_verified=row.date_verified, validation_status=row.validation_status, extraction_method=row.extraction_method,
        github_stars=stars if isinstance(stars, int) else None, tags=_list(ct.get("tags")), location=ct.get("location"),
        pricing_model=ct.get("pricingModel"), is_remote=ct.get("is_remote"), role_family=ct.get("role_family"),
        authors=_list(ct.get("authors")), organizations=unique_ci(_list(ct.get("organizations"))),
    )


async def scalar(s: AsyncSession, q: Any) -> Any:
    return (await s.execute(q)).scalar()
