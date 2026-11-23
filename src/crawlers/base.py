"""Crawler contract.

A crawler is an async generator of validated canonical records. It never
writes to storage itself (pipelines do), it never invents values, and it
reports access problems through ``CrawlStats`` instead of raising past the
pipeline boundary.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from src.config.settings import Settings, get_settings
from src.core.http import HttpClient
from src.core.schemas import RecordBase, RecordType
from src.monitoring.logging import get_logger
from src.monitoring.metrics import RECORDS_COLLECTED, RECORDS_REJECTED

log = get_logger(__name__)


class SourceStatus(StrEnum):
    OK = "OK"
    PARTIAL = "PARTIAL"            # some pages failed
    INACCESSIBLE = "INACCESSIBLE"  # blocked / robots / auth missing
    SKIPPED = "SKIPPED"            # credentials not configured
    FAILED = "FAILED"


@dataclass
class CrawlStats:
    source: str
    vertical: str
    status: SourceStatus = SourceStatus.OK
    fetched: int = 0
    emitted: int = 0
    rejected: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def reject(self, reason: str) -> None:
        self.rejected[reason] += 1
        RECORDS_REJECTED.labels(self.vertical, reason).inc()

    def error(self, msg: str, *, fatal: bool = False, inaccessible: bool = False) -> None:
        self.errors.append(msg[:500])
        if inaccessible:
            self.status = SourceStatus.INACCESSIBLE
        elif fatal:
            self.status = SourceStatus.FAILED
        elif self.status is SourceStatus.OK:
            self.status = SourceStatus.PARTIAL

    def warn(self, msg: str) -> None:
        """Record a problem with one item that the source itself is not responsible for (status unchanged)."""
        self.errors.append(f"warning: {msg}"[:500])


# Cross-run back-off after a source rate-limits us: 15 min, 30 min, 1 h, ... capped at 24 h, or the
# source's own Retry-After when that is longer. Stored in the crawl checkpoint, honoured by the pipeline.
COOLDOWN_BASE_S = 15 * 60
COOLDOWN_CAP_S = 24 * 3600


def cooldown_seconds(strikes: int, retry_after: float | None) -> float:
    backoff = min(COOLDOWN_CAP_S, COOLDOWN_BASE_S * 2 ** max(0, strikes - 1))
    return max(backoff, min(COOLDOWN_CAP_S, retry_after or 0.0))


@dataclass
class CrawlContext:
    http: HttpClient
    settings: Settings = field(default_factory=get_settings)
    checkpoint: dict[str, Any] = field(default_factory=dict)
    limit: int | None = None
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    extras: dict[str, Any] = field(default_factory=dict)


class BaseCrawler(ABC):
    name: str = "base"
    source_name: str = "base"
    vertical: RecordType

    def __init__(self) -> None:
        self.stats = CrawlStats(self.name, self.vertical.value)
        self.next_checkpoint: dict[str, Any] = {}
        self.rate_limited = False

    def note_rate_limit(self, ctx: CrawlContext, retry_after: float | None) -> datetime:
        """Schedule this source's next attempt after an HTTP 429 (exponential across consecutive runs)."""
        strikes = int(ctx.checkpoint.get("rate_limit_strikes") or 0) + 1
        until = ctx.now + timedelta(seconds=cooldown_seconds(strikes, retry_after))
        self.rate_limited = True
        self.next_checkpoint.update({"rate_limit_strikes": strikes, "cooldown_until": until.isoformat()})
        return until

    @abstractmethod
    def crawl(self, ctx: CrawlContext) -> AsyncIterator[RecordBase]:
        """Yield validated records."""

    def build(self, cls: type[RecordBase], **kwargs: Any) -> RecordBase | None:
        """Validate a record; on failure count a rejection instead of emitting bad data."""
        try:
            rec = cls.model_validate(kwargs)
        except ValidationError as e:
            first = e.errors()[0]
            self.stats.reject(f"schema:{'.'.join(map(str, first['loc']))}")
            log.debug("record.rejected", source=self.name, error=str(first))
            return None
        self.stats.emitted += 1
        RECORDS_COLLECTED.labels(self.vertical.value, self.name).inc()
        return rec


def clean_text(s: Any, max_len: int | None = None) -> str | None:
    if s is None:
        return None
    s = " ".join(str(s).split())
    if not s:
        return None
    return s[:max_len] if max_len else s
