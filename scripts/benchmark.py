"""Controlled scale benchmark on SYNTHETIC fixtures (never mixed with real data).

* writes to a dedicated benchmark database (``BENCH_DATABASE_URL``), dropped first;
* serves synthetic job-board JSON from a local aiohttp server (real HTTP, localhost);
* simulates LLM providers with latency + injected 429/413/5xx faults.

Output: ``docs/benchmarks.json`` (every figure labelled synthetic).

    BENCH_DATABASE_URL=postgresql+asyncpg://... python scripts/benchmark.py --records 100000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import resource
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiohttp import web

from src.config.settings import Settings
from src.core.dedup import InMemoryDeduplicator
from src.core.http import HttpClient
from src.core.schemas import StartupRecord
from src.crawlers.jobs.boards import GreenhouseCrawler
from src.database.repository import Repository
from src.database.session import Database
from src.extraction.orchestrator import LLMOrchestrator
from src.extraction.providers.base import (
    BaseProvider,
    LLMRequest,
    LLMResponse,
    PayloadTooLargeError,
    ProviderUnavailableError,
    RateLimitError,
)
from src.extraction.tasks import PRODUCT_PRICING
from src.monitoring.logging import configure_logging
from src.pipelines.ingestion import IngestionPipeline

NOW = datetime.now(UTC)


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


async def bench_storage(repo: Repository, n: int) -> dict:
    recs = [
        StartupRecord.model_validate({
            "source": {"name": "SYNTHETIC", "url": f"https://synthetic.invalid/{i}"}, "collectedAt": NOW,
            "content": {"entityName": f"Synthetic Org {i}", "description": "synthetic benchmark fixture"},
            "provenance": {"extractionMethod": "STRUCTURED_API"}})
        for i in range(n)
    ]
    t0 = time.perf_counter()
    r1 = await repo.bulk_upsert(recs)
    t1 = time.perf_counter()
    r2 = await repo.bulk_upsert(recs)
    t2 = time.perf_counter()
    return {"records": n, "insert_seconds": round(t1 - t0, 2), "insert_rows_per_s": round(n / (t1 - t0)),
            "idempotent_rerun_seconds": round(t2 - t1, 2), "rerun_inserted": r2.inserted, "first_inserted": r1.inserted}


async def bench_crawl(repo: Repository, boards: int, jobs_per_board: int, latency_ms: int) -> dict:
    rnd = random.Random(7)

    async def handler(req: web.Request) -> web.Response:
        await asyncio.sleep(latency_ms / 1000)
        token = req.match_info["token"]
        jobs = [{"id": i, "title": rnd.choice(["ML Engineer", "Research Scientist", "Account Executive", "Data Scientist"]),
                 "absolute_url": f"http://127.0.0.1:{port}/{token}/jobs/{i}",
                 "first_published": (NOW - timedelta(hours=rnd.uniform(0, 48))).isoformat(), "location": {"name": rnd.choice(["Remote", "SF"])}}
                for i in range(jobs_per_board)]
        return web.json_response({"jobs": jobs})

    app = web.Application()
    app.router.add_get("/v1/boards/{token}/jobs", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

    class LocalGreenhouse(GreenhouseCrawler):
        async def raw_jobs(self, ctx):  # type: ignore[no-untyped-def]
            import src.crawlers.jobs.boards as b

            orig = "https://boards-api.greenhouse.io"
            async def patched_get_json(url, **kw):  # type: ignore[no-untyped-def]
                return await real(url.replace(orig, f"http://127.0.0.1:{port}"), **kw)
            real = ctx.http.get_json
            ctx.http.get_json = patched_get_json
            try:
                async for j in b.GreenhouseCrawler.raw_jobs(self, ctx):
                    yield j
            finally:
                ctx.http.get_json = real

    s = Settings(database_url="sqlite+aiosqlite://", respect_robots_txt=False, default_domain_rps=10_000, http_max_per_host=64)
    async with HttpClient(s) as http:
        pipe = IngestionPipeline(repo, settings=s, http=http, dedup=InMemoryDeduplicator(), flush_size=500)
        t0 = time.perf_counter()
        crawlers = [LocalGreenhouse(boards={f"b{w}_{i}": f"Synthetic Co {i}" for i in range(boards // 8)}) for w in range(8)]
        for c in crawlers:
            c.name = f"bench_{id(c)}"
        sums = await asyncio.gather(*(pipe.run_crawler(c) for c in crawlers))
        dt = time.perf_counter() - t0
    await runner.cleanup()
    fetched = sum(x.fetched for x in sums)
    stored = sum(x.inserted for x in sums)
    return {"boards": boards, "jobs_fetched": fetched, "fresh_stored": stored, "stale_rejected": sum(x.rejected.get("freshness:stale", 0) for x in sums),
            "seconds": round(dt, 2), "jobs_per_s": round(fetched / dt), "simulated_latency_ms": latency_ms, "concurrent_workers": 8}


class FaultyProvider(BaseProvider):
    def __init__(self, name: str, rng: random.Random, p429: float, p413: float, p5xx: float, latency: float) -> None:
        super().__init__(api_key="synthetic", model="sim", input_token_budget=8000, rpm=600_000)
        self.name, self.rng, self.p429, self.p413, self.p5xx, self.latency = name, rng, p429, p413, p5xx, latency

    def build_request(self, req):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def parse_response(self, data, latency):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    async def complete(self, req: LLMRequest) -> LLMResponse:
        await asyncio.sleep(self.latency * self.rng.uniform(0.5, 1.5))
        x = self.rng.random()
        if x < self.p429:
            raise RateLimitError("429", retry_after=None, provider=self.name)
        if x < self.p429 + self.p413 and len(req.user) > 1200:
            raise PayloadTooLargeError("413", provider=self.name)
        if x < self.p429 + self.p413 + self.p5xx:
            raise ProviderUnavailableError("503", provider=self.name)
        body = {"fields": {"pricing_model": {"value": "FREEMIUM", "evidence": "free to start"}}} if "free to start" in req.user else {"fields": {}}
        return LLMResponse(json.dumps(body), self.name, "sim", None, None, 0.0)


async def bench_llm(n: int) -> dict:
    rng = random.Random(11)
    s = Settings(database_url="sqlite+aiosqlite://", llm_max_concurrency=64, llm_backoff_base_s=0.01, llm_backoff_max_s=0.05, llm_max_retries_per_provider=3)
    provs = [FaultyProvider("gemini", rng, 0.25, 0.05, 0.05, 0.03), FaultyProvider("groq", rng, 0.10, 0.10, 0.02, 0.02), FaultyProvider("deepseek", rng, 0.02, 0.0, 0.01, 0.05)]

    async def fast_sleep(d: float) -> None:  # compress real back-off time 100x, keep ordering semantics
        await asyncio.sleep(min(d, 0.05))

    orch = LLMOrchestrator(provs, s, sleep=fast_sleep)
    for b in orch.breakers.values():  # benchmark measures retry/fallback, not breaker cooldown
        b.threshold = 10**9
    docs = [f"Product {i}\n\n" + ("Atlas is free to start; upgrade for $20/month. " if i % 2 else "") + "Filler text about the product. " * rng.randint(5, 120) for i in range(n)]
    lat: list[float] = []
    ok = fail = 0
    used: dict[str, int] = {}
    outcomes: dict[str, int] = {}

    async def one(d: str) -> None:
        nonlocal ok, fail
        t = time.perf_counter()
        try:
            r = await orch.extract(PRODUCT_PRICING, d)
            ok += 1
            for p in r.providers_used:
                used[p] = used.get(p, 0) + 1
            for a in r.attempts:
                outcomes[f"{a.provider}:{a.outcome}"] = outcomes.get(f"{a.provider}:{a.outcome}", 0) + 1
        except Exception:
            fail += 1
        lat.append(time.perf_counter() - t)

    t0 = time.perf_counter()
    await asyncio.gather(*(one(d) for d in docs))
    dt = time.perf_counter() - t0
    q = statistics.quantiles(lat, n=100)
    return {"extractions": n, "succeeded": ok, "failed": fail, "success_rate": round(ok / n, 4), "seconds": round(dt, 2),
            "extractions_per_s": round(n / dt, 1), "p50_s": round(q[49], 3), "p95_s": round(q[94], 3),
            "fault_injection": {"gemini": "25% 429, 5% 413, 5% 5xx", "groq": "10% 429, 10% 413, 2% 5xx", "deepseek": "2% 429, 1% 5xx"},
            "final_provider_counts": used, "attempt_outcomes": dict(sorted(outcomes.items()))}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", type=int, default=100_000)
    ap.add_argument("--boards", type=int, default=400)
    ap.add_argument("--jobs-per-board", type=int, default=100)
    ap.add_argument("--llm", type=int, default=2000)
    a = ap.parse_args()
    configure_logging("ERROR", json=True)
    url = os.getenv("BENCH_DATABASE_URL", "sqlite+aiosqlite:///./data/bench.db")
    db = Database(url)
    await db.drop_all()
    await db.create_all()
    repo = Repository(db, batch_size=1000)
    out = {"label": "SYNTHETIC BENCHMARK FIXTURES — not real collected data", "database": url.split("@")[-1].split("+")[0] if "@" in url else url.split(":")[0],
           "run_at": NOW.isoformat(), "python": sys.version.split()[0], "cpu_count": os.cpu_count()}
    out["storage_bulk_upsert"] = await bench_storage(repo, a.records)
    out["crawl_pipeline_local_http"] = await bench_crawl(repo, a.boards, a.jobs_per_board, latency_ms=50)
    out["llm_orchestrator_fault_injection"] = await bench_llm(a.llm)
    out["peak_rss_mb"] = round(rss_mb(), 1)
    await db.drop_all()
    await db.dispose()
    Path("docs").mkdir(exist_ok=True)
    Path("docs/benchmarks.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
