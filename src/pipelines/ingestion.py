"""End-to-end ingestion: crawl -> resolve entities -> (LLM enrich) -> validate -> idempotent store.

Exactly-once *effect* per item:
  claim (Redis NX lease) -> process -> DB upsert (UNIQUE + ON CONFLICT) -> mark done.
If the DB write fails, claims are released so another worker can retry; if a
worker dies, its lease expires. Re-running a crawl is always safe.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.config.settings import Settings, get_settings
from src.core.dedup import Deduplicator, InMemoryDeduplicator
from src.core.http import HttpClient
from src.core.schemas import (
    JobRecord,
    NewsRecord,
    PricingModel,
    ProductRecord,
    RecordBase,
    StartupRecord,
)
from src.crawlers.base import BaseCrawler, CrawlContext, SourceStatus
from src.crawlers.fresh import FreshSignalCrawler
from src.crawlers.registry import DOMAIN_RPS, CrawlerSpec
from src.database.repository import Repository, UpsertResult
from src.entity_resolution.resolver import EntityResolver
from src.extraction.orchestrator import ExtractionFailed, LLMOrchestrator, NoProviderAvailable
from src.extraction.tasks import NEWS_ENTITIES, PRODUCT_PRICING
from src.monitoring.logging import get_logger
from src.monitoring.metrics import RECORDS_DUPLICATE, RECORDS_STORED, SOURCE_STATUS

log = get_logger(__name__)


@dataclass
class RunSummary:
    source: str
    vertical: str
    status: str
    fetched: int = 0
    emitted: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    llm: dict[str, int] = field(default_factory=dict)
    freshness: dict[str, int] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0


def _cooldown_until(checkpoint: dict[str, Any]) -> datetime | None:
    raw = checkpoint.get("cooldown_until")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class IngestionPipeline:
    def __init__(
        self,
        repo: Repository,
        *,
        settings: Settings | None = None,
        http: HttpClient | None = None,
        resolver: EntityResolver | None = None,
        orchestrator: LLMOrchestrator | None = None,
        dedup: Deduplicator | None = None,
        flush_size: int = 200,
        clock: Any = None,
        history: Any = None,
        history_batch: str | None = None,
    ) -> None:
        self.s = settings or get_settings()
        self.repo = repo
        self.http = http or HttpClient(self.s, domain_rps=DOMAIN_RPS)
        self.resolver = resolver or EntityResolver(fuzzy_accept=self.s.er_fuzzy_accept, fuzzy_review=self.s.er_fuzzy_review, semantic_accept=self.s.er_semantic_accept)
        self.orchestrator = orchestrator
        self.dedup = dedup or InMemoryDeduplicator()
        self.flush_size = flush_size
        self.clock = clock or (lambda: datetime.now(UTC))  # injectable for deterministic tests
        self.shared_extras: dict[str, Any] = {"dedup": self.dedup}
        # Phase 7C: optional HistoryRecorder (only when HISTORY_ENABLED=true) and the batch it records into.
        self.history = history
        self.history_batch = history_batch
        self._seeded = False

    async def seed_entities(self) -> None:
        if not self._seeded:
            await self.repo.upsert_canonical_entities(self.resolver.canonical_rows())
            self._seeded = True

    # ------------------------------------------------------------- run
    async def run_spec(self, spec: CrawlerSpec, *, limit: int | None = None) -> RunSummary:
        return await self.run_crawler(spec.factory(), priority=spec.priority, limit=limit)

    async def run_crawler(self, crawler: BaseCrawler, *, priority: int = 0, limit: int | None = None) -> RunSummary:
        await self.seed_entities()
        t0 = self.clock()
        checkpoint = await self.repo.get_checkpoint(crawler.name)
        cooling = _cooldown_until(checkpoint)
        if cooling is not None and cooling > t0:
            # The source rate-limited us recently: no request before the agreed time. No source_runs row either: the
            # scheduler ticks every 15 min, so a row per skip would bury the real attempts. Source Health reads the
            # cooldown from the checkpoint instead.
            msg = f"rate-limit cooldown until {cooling.isoformat()} (source answered HTTP 429; strike {checkpoint.get('rate_limit_strikes', 1)})"
            log.info("pipeline.cooldown_skip", source=crawler.name, until=cooling.isoformat())
            return RunSummary(crawler.name, crawler.vertical.value, SourceStatus.SKIPPED.value, errors=[msg])
        ctx = CrawlContext(http=self.http, settings=self.s, checkpoint=checkpoint, limit=limit, now=t0, extras=self.shared_extras)
        summary = RunSummary(crawler.name, crawler.vertical.value, "OK")
        buf: list[RecordBase] = []
        totals = UpsertResult()
        try:
            async for rec in crawler.crawl(ctx):
                buf.append(rec)
                if len(buf) >= self.flush_size:
                    await self._flush(crawler, buf, priority, totals, summary)
                    buf = []
            if buf:
                await self._flush(crawler, buf, priority, totals, summary)
        except Exception as e:
            log.exception("pipeline.crawler_crashed", source=crawler.name)
            crawler.stats.error(f"crawler crashed: {type(e).__name__}: {e}", fatal=True)
            if isinstance(crawler, FreshSignalCrawler):
                for k in crawler.claimed:
                    await self.dedup.release(k)
                crawler.claimed.clear()
        st = crawler.stats
        if st.fetched == 0 and st.emitted == 0 and st.errors and st.status in (SourceStatus.OK, SourceStatus.PARTIAL):
            # nothing collected and errors seen: never report that as success
            blocked = any(k in e for e in st.errors for k in ("denied", "anti-bot", "robots", "circuit open", "inaccessible"))
            st.status = SourceStatus.INACCESSIBLE if blocked else SourceStatus.FAILED
        summary.status = st.status.value
        summary.fetched, summary.emitted = st.fetched, st.emitted
        summary.inserted, summary.updated, summary.unchanged = totals.inserted, totals.updated, totals.unchanged
        summary.rejected = dict(st.rejected)
        summary.errors = st.errors[:20]
        if isinstance(crawler, FreshSignalCrawler):
            summary.freshness = dict(crawler.freshness_counts)
        for attr in ("github_live", "github_cached", "github_missing_repo", "github_deferred"):
            if hasattr(crawler, attr):
                summary.extra[attr] = getattr(crawler, attr)
        finished = datetime.now(UTC)
        summary.seconds = round((finished - t0).total_seconds(), 2)
        ok = st.status in (SourceStatus.OK, SourceStatus.PARTIAL)
        cursor = {**checkpoint, **crawler.next_checkpoint}
        if not crawler.rate_limited and st.status is not SourceStatus.FAILED:
            cursor.pop("cooldown_until", None)  # the source answered normally: reset the back-off
            cursor.pop("rate_limit_strikes", None)
        if ok:
            cursor["last_success_at"] = t0.isoformat()
        await self.repo.save_checkpoint(crawler.name, cursor, success=ok, items=st.emitted)
        await self.repo.log_run(
            source=crawler.name, vertical=crawler.vertical.value, started_at=t0, finished_at=finished, status=st.status.value,
            fetched=st.fetched, stored=totals.inserted + totals.updated, duplicates=totals.unchanged, rejected=sum(st.rejected.values()),
            error="; ".join(st.errors[:5]) or None,
        )
        if self.history is not None and self.history_batch:
            complete = (st.status is SourceStatus.OK and not crawler.rate_limited and not st.errors and limit is None and st.fetched > 0)
            reason = None if complete else f"status={st.status.value}, rate_limited={crawler.rate_limited}, errors={len(st.errors)}, limit={limit}"
            scan = await self.history.record_scan(self.history_batch, crawler.name, complete=complete, items_listed=st.emitted, reason=reason)
            gone = await self.history.detect_disappearances(self.history_batch, crawler.name) if scan.get("recorded") else {"disappeared": 0}
            summary.extra["history_scan"] = {**scan, **gone}
        SOURCE_STATUS.labels(crawler.name).set(1 if ok else 0)
        log.info("pipeline.run_complete", **{k: v for k, v in summary.__dict__.items() if k not in ("errors",)})
        return summary

    async def _flush(self, crawler: BaseCrawler, buf: list[RecordBase], priority: int, totals: UpsertResult, summary: RunSummary) -> None:
        # The crawler generator is paused at a yield, so ``crawler.claimed`` holds exactly the
        # claims of the buffered (emitted) records; rejected items were already marked done.
        processed = await asyncio.gather(*(self._postprocess(r, summary) for r in buf))
        records = [r for r in processed if r is not None]
        try:
            res = await self.repo.bulk_upsert(records, priority=priority)
        except Exception:
            if isinstance(crawler, FreshSignalCrawler):
                for k in crawler.claimed:
                    await self.dedup.release(k)
                crawler.claimed.clear()
            raise
        totals += res
        if self.history is not None and self.history_batch and records:
            hs = await self.history.record(self.history_batch, records, observed_at=self.clock())
            agg = summary.extra.setdefault("history", {})
            for k, v in hs.as_dict().items():
                if isinstance(v, int):
                    agg[k] = agg.get(k, 0) + v
        RECORDS_STORED.labels(crawler.vertical.value).inc(res.inserted)
        RECORDS_DUPLICATE.labels(crawler.vertical.value).inc(res.unchanged)
        await self.repo.save_mappings(self.resolver.log)
        self.resolver.log.clear()
        if crawler.next_checkpoint:
            # persist the resume cursor after every committed batch, so an interrupted run continues where it stopped
            cp = await self.repo.get_checkpoint(crawler.name)
            await self.repo.save_checkpoint(crawler.name, {**cp, **crawler.next_checkpoint}, success=False, items=0)
        if isinstance(crawler, FreshSignalCrawler):
            for k in crawler.claimed:
                await self.dedup.mark_done(k)
            crawler.claimed.clear()

    # ---------------------------------------------------- post-processing
    async def _postprocess(self, rec: RecordBase, summary: RunSummary) -> RecordBase | None:
        try:
            if isinstance(rec, StartupRecord):
                handle = rec.provenance.evidence.get("hubHandle")
                r = (self.resolver.resolve_with_handle(rec.content.entity_name, handle, source_url=rec.source.url) if handle
                     else self.resolver.resolve(rec.content.entity_name, website=rec.content.website, source_url=rec.source.url))
                return rec.model_copy(update={"content": rec.content.model_copy(update={"entity_name": r.canonical, "raw_name": rec.content.raw_name or rec.content.entity_name})})
            if isinstance(rec, ProductRecord):
                return await self._product(rec, summary)
            if isinstance(rec, JobRecord):
                r = self.resolver.resolve(rec.content.company, source_url=rec.source.url)
                return rec.model_copy(update={"content": rec.content.model_copy(update={"company": r.canonical, "raw_company": rec.content.raw_company or rec.content.company})})
            if isinstance(rec, NewsRecord):
                return await self._news(rec, summary)
        except ValueError as e:
            summary.rejected["postprocess"] = summary.rejected.get("postprocess", 0) + 1
            log.warning("pipeline.postprocess_rejected", url=rec.source.url, error=str(e))
            return None
        return rec

    def _count(self, summary: RunSummary, key: str) -> None:
        summary.llm[key] = summary.llm.get(key, 0) + 1

    async def _product(self, rec: ProductRecord, summary: RunSummary) -> ProductRecord:
        c = rec.content
        updates: dict[str, Any] = {}
        prov = rec.provenance
        evidence = dict(prov.evidence)
        llm_provider, confidence, status = prov.llm_provider, prov.confidence, prov.validation_status
        if c.pricing_model is None and c.description and self.orchestrator is not None:
            try:
                out = await self.orchestrator.extract(PRODUCT_PRICING, f"{c.product_name}\n\n{c.description}")
                self._count(summary, "success")
                if out.data.get("pricing_model"):
                    updates["pricing_model"] = PricingModel(out.data["pricing_model"])
                    evidence["pricingModel"] = out.evidence["pricing_model"]
                if out.data.get("startup_name") and not c.startup_name:
                    updates["startup_name"] = out.data["startup_name"]
                    evidence["startupName"] = out.evidence["startup_name"]
                llm_provider = ",".join(out.providers_used)
                confidence = min(confidence, out.confidence) if updates else confidence
            except NoProviderAvailable:
                self._count(summary, "skipped_no_provider")
            except ExtractionFailed as e:
                self._count(summary, "failed")
                await self.repo.dead_letter("llm.product_pricing", rec.source.url, {"description": c.description[:2000]}, str(e))
        name = updates.get("startup_name") or c.startup_name
        if name:
            r = self.resolver.resolve_with_handle(name, c.raw_startup_name, source_url=rec.source.url)
            updates["startup_name"] = r.canonical
            updates["raw_startup_name"] = c.raw_startup_name or name
        if not updates:
            return rec
        return rec.model_copy(
            update={
                "content": c.model_copy(update=updates),
                "provenance": prov.model_copy(update={"evidence": evidence, "llm_provider": llm_provider, "confidence": confidence, "validation_status": status}),
            }
        )

    async def _news(self, rec: NewsRecord, summary: RunSummary) -> NewsRecord:
        if self.orchestrator is None or not (rec.content.full_text or rec.content.summary):
            return rec
        try:
            out = await self.orchestrator.extract(NEWS_ENTITIES, f"{rec.content.title}\n\n{rec.content.full_text or rec.content.summary}")
            self._count(summary, "success")
        except NoProviderAvailable:
            self._count(summary, "skipped_no_provider")
            return rec
        except ExtractionFailed as e:
            self._count(summary, "failed")
            await self.repo.dead_letter("llm.news_entities", rec.source.url, {"title": rec.content.title}, str(e))
            return rec
        orgs = [self.resolver.resolve(o, source_url=rec.source.url).canonical for o in out.data.get("organizations", [])]
        ev = dict(rec.provenance.evidence)
        if orgs:
            ev["organizations"] = out.evidence.get("organizations", "")[:600]
        return rec.model_copy(
            update={
                "content": rec.content.model_copy(update={"organizations": list(dict.fromkeys(orgs))}),
                "provenance": rec.provenance.model_copy(update={"evidence": ev, "llm_provider": ",".join(out.providers_used)}),
            }
        )

    async def run_many(self, specs: list[CrawlerSpec], *, limit: int | None = None, concurrency: int = 4) -> list[RunSummary]:
        sem = asyncio.Semaphore(concurrency)

        async def one(spec: CrawlerSpec) -> RunSummary:
            async with sem:
                return await self.run_spec(spec, limit=limit)

        return list(await asyncio.gather(*(one(s) for s in specs)))
