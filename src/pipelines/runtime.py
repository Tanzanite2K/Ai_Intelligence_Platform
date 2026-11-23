"""Wiring: build the pipeline and its dependencies from settings (used by CLI, API and Celery)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from src.config.settings import Settings, get_settings
from src.core.dedup import build_deduplicator
from src.core.http import HttpClient
from src.crawlers.registry import DOMAIN_RPS
from src.database.repository import Repository
from src.database.session import Database
from src.extraction.orchestrator import LLMOrchestrator
from src.extraction.providers.vendors import build_providers
from src.pipelines.ingestion import IngestionPipeline


@asynccontextmanager
async def pipeline_context(settings: Settings | None = None, *, use_llm: bool = True, history_batch: str | None = None) -> AsyncIterator[IngestionPipeline]:
    s = settings or get_settings()
    db = Database(s.database_url)
    await db.create_all()
    repo = Repository(db, batch_size=s.bulk_insert_batch_size)
    orch = None
    if use_llm:
        providers = build_providers(s)
        if any(p.configured for p in providers):
            orch = LLMOrchestrator(providers, s)
    async with HttpClient(s, domain_rps=DOMAIN_RPS) as http:
        history = None
        if history_batch:
            from src.history.capture import HistoryRecorder

            history = HistoryRecorder(db)
        pipe = IngestionPipeline(repo, settings=s, http=http, orchestrator=orch, dedup=build_deduplicator(s.redis_url),
                                 history=history, history_batch=history_batch)
        try:
            yield pipe
        finally:
            if orch:
                await orch.close()
            await db.dispose()
