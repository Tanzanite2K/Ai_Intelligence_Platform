"""Shared freshness gate for the NEWS and JOB verticals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from src.core.dates import Freshness, FreshnessPolicy, ParsedDate, new_since_last_run
from src.core.dedup import Deduplicator, InMemoryDeduplicator
from src.core.urls import canonicalize_url, url_hash
from src.crawlers.base import BaseCrawler, CrawlContext
from src.monitoring.metrics import RECORDS_DUPLICATE


@dataclass(slots=True)
class FreshDecision:
    keep: bool
    verified: bool
    date: datetime | None
    date_source: str | None
    freshness: Freshness


class FreshSignalCrawler(BaseCrawler):
    """Adds: distributed claim, freshness classification, undated-item heuristic, listing checkpoint."""

    def setup_fresh(self, ctx: CrawlContext) -> None:
        self.policy = FreshnessPolicy(window=timedelta(hours=ctx.settings.freshness_window_hours))
        self.dedup: Deduplicator = ctx.extras.setdefault("dedup", InMemoryDeduplicator())
        last = ctx.checkpoint.get("last_success_at")
        self._last_success = datetime.fromisoformat(last) if last else None
        self._prev_listing: set[str] = set(ctx.checkpoint.get("listing", []))
        self._listing: list[str] = []
        self._heuristic_new: set[str] | None = None
        self.claimed: list[str] = []
        self.freshness_counts: dict[str, int] = {}

    def observe_listing(self, urls: list[str]) -> None:
        canon = [canonicalize_url(u) for u in urls]
        self._listing.extend(canon)
        self.next_checkpoint["listing"] = self._listing[:500]

    def heuristic_new(self, ctx: CrawlContext) -> set[str]:
        if self._heuristic_new is None:
            self._heuristic_new = new_since_last_run(self._listing, self._prev_listing, self._last_success, ctx.now, self.policy.window)
        return self._heuristic_new

    async def claim(self, url: str) -> str | None:
        """Atomically claim a URL across workers; None if another worker has/had it."""
        key = url_hash(url)
        if not await self.dedup.claim(key):
            RECORDS_DUPLICATE.labels(self.vertical.value).inc()
            self.stats.reject("duplicate_claimed_elsewhere")
            return None
        self.claimed.append(key)
        return key

    async def finish_rejected(self, key: str) -> None:
        """Item was processed but rejected (e.g. stale) -> mark done so no worker repeats it."""
        await self.dedup.mark_done(key)
        if key in self.claimed:
            self.claimed.remove(key)

    def decide(self, pd: ParsedDate | None, url: str, ctx: CrawlContext) -> FreshDecision:
        f = self.policy.classify(pd, ctx.now)
        self.freshness_counts[f.value] = self.freshness_counts.get(f.value, 0) + 1
        if f is Freshness.FRESH:
            return FreshDecision(True, True, pd.value if pd else None, pd.source if pd else None, f)
        if f in (Freshness.STALE, Freshness.FUTURE):
            self.stats.reject(f"freshness:{f.value.lower()}")
            return FreshDecision(False, False, None, None, f)
        # UNVERIFIED: keep with date_verified=False (excluded from the guaranteed-fresh export)
        if canonicalize_url(url) in self.heuristic_new(ctx):
            src = f"heuristic:new-since-last-run({self._last_success.isoformat() if self._last_success else ''})"
            return FreshDecision(True, False, pd.value if pd else None, src, f)
        return FreshDecision(True, False, pd.value if pd else None, (pd.source + ":imprecise") if pd else "unknown", f)
