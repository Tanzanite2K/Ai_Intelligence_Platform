"""Storage + distributed dedup. Runs on SQLite always and on PostgreSQL when TEST_DATABASE_URL is set;
Redis tests run when TEST_REDIS_URL is set."""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta

import pytest

from src.core.dedup import InMemoryDeduplicator, RedisDeduplicator
from src.core.schemas import EntityMapping, StartupRecord
from src.database.repository import Repository
from tests.conftest import NOW


def startup(name: str, desc: str = "d", src: str = "Y Combinator") -> StartupRecord:
    return StartupRecord.model_validate({
        "source": {"name": src, "url": f"https://example.com/{name.replace(' ', '-')}"}, "collectedAt": NOW,
        "content": {"entityName": name, "description": desc}, "provenance": {"extractionMethod": "STRUCTURED_API"}})


async def test_upsert_is_idempotent(repo: Repository) -> None:
    recs = [startup(f"Co {i}") for i in range(120)]
    r1 = await repo.bulk_upsert(recs)
    r2 = await repo.bulk_upsert(recs)
    assert (r1.inserted, r2.inserted, r2.unchanged) == (120, 0, 120)
    assert (await repo.count_by_type())["STARTUP"] == 120


async def test_in_batch_duplicates_collapse(repo: Repository) -> None:
    r = await repo.bulk_upsert([startup("Same"), startup("SAME"), startup("same")])  # one canonical org
    assert r.inserted == 1 and (await repo.count_by_type())["STARTUP"] == 1


async def test_update_on_content_change_respects_priority(repo: Repository) -> None:
    await repo.bulk_upsert([startup("Acme", "rich YC description")], priority=10)
    low = await repo.bulk_upsert([startup("Acme", "poor", src="Hugging Face Hub")], priority=5)
    assert low.updated == 0
    rows = await repo.fetch_records("STARTUP")
    assert rows[0]["content"]["description"] == "rich YC description"
    high = await repo.bulk_upsert([startup("Acme", "newer YC description")], priority=10)
    assert high.updated == 1 and (await repo.fetch_records("STARTUP"))[0]["content"]["description"] == "newer YC description"


async def test_concurrent_writers_never_duplicate(repo: Repository) -> None:
    recs = [startup(f"Org {i}") for i in range(200)]
    await asyncio.gather(*(repo.bulk_upsert(recs[i::3] + recs[:20]) for i in range(3)))
    assert (await repo.count_by_type())["STARTUP"] == 200


async def test_mappings_checkpoints_seen_dlq(repo: Repository) -> None:
    m = EntityMapping(originalName="Open AI", canonicalName="OpenAI", matchMethod="EXACT", confidence=1.0, sourceUrl="https://x.com/a")
    assert await repo.save_mappings([m, m]) == 1
    await repo.save_mappings([m])
    assert len(await repo.fetch_mappings()) == 1
    await repo.save_checkpoint("src", {"cursor": "abc"}, success=True, items=5)
    assert (await repo.get_checkpoint("src"))["cursor"] == "abc"
    new = await repo.mark_seen([("h1", "s", None), ("h2", "s", None)])
    again = await repo.mark_seen([("h1", "s", None), ("h3", "s", None)])
    assert new == {"h1", "h2"} and again == {"h3"}
    await repo.dead_letter("llm", "k", {"a": 1}, "boom")
    assert await repo.count_dead_letters() == 1


async def test_fetch_freshness_filter(repo: Repository) -> None:
    from src.core.schemas import NewsRecord

    def news(i: int, hours: float, verified: bool = True) -> NewsRecord:
        return NewsRecord.model_validate({"source": {"name": "T", "url": f"https://n.com/{i}"}, "provenance": {"extractionMethod": "HTML_PARSER"},
                                          "content": {"title": f"Story {i}", "article_url": f"https://n.com/{i}", "published_date": NOW - timedelta(hours=hours), "date_verified": verified}})

    await repo.bulk_upsert([news(1, 2), news(2, 30), news(3, 1, verified=False)])
    rows = await repo.fetch_records("NEWS", published_after=NOW - timedelta(hours=24), verified_dates_only=True)
    assert [r["content"]["title"] for r in rows] == ["Story 1"]


# ----------------------------------------------------------- dedup claims
async def test_inmemory_claims_exactly_once() -> None:
    d = InMemoryDeduplicator()
    results = await asyncio.gather(*(d.claim("k") for _ in range(50)))
    assert sum(results) == 1
    await d.mark_done("k")
    assert not await d.claim("k")


@pytest.mark.skipif(not os.getenv("TEST_REDIS_URL"), reason="TEST_REDIS_URL not set")
async def test_redis_claims_across_simulated_nodes() -> None:
    import redis.asyncio as aioredis

    r = aioredis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
    await r.flushdb()
    nodes = [RedisDeduplicator(r, namespace="t", lease_s=30) for _ in range(8)]  # 8 "workers"
    keys = [f"url{i}" for i in range(100)]
    wins = await asyncio.gather(*(n.claim(k) for k in keys for n in nodes))
    assert sum(wins) == 100  # each URL claimed by exactly one node
    holder = await r.get("t:claim:url0")
    owner = next(n for n in nodes if n.owner == holder)
    other = next(n for n in nodes if n is not owner)
    await other.release("url0")  # a non-owner cannot release someone else's lease
    assert await r.exists("t:claim:url0")
    await owner.mark_done("url0")
    assert not any(await asyncio.gather(*(n.claim("url0") for n in nodes)))
    await r.flushdb()
    await r.aclose()


@pytest.mark.skipif(not os.getenv("TEST_REDIS_URL"), reason="TEST_REDIS_URL not set")
async def test_redis_lease_expiry_allows_reclaim() -> None:
    import redis.asyncio as aioredis

    r = aioredis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
    a = RedisDeduplicator(r, namespace="t2", lease_s=1)
    a.lease_ms = 200  # crashed worker's lease
    b = RedisDeduplicator(r, namespace="t2")
    assert await a.claim("x") and not await b.claim("x")
    await asyncio.sleep(0.3)
    assert await b.claim("x")
    await r.flushdb()
    await r.aclose()


@pytest.mark.skipif(not os.getenv("TEST_REDIS_URL"), reason="TEST_REDIS_URL not set")
async def test_redis_distributed_rate_limiter() -> None:
    import time

    import redis.asyncio as aioredis

    from src.core.ratelimit import RedisRateLimiter

    r = aioredis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
    await r.delete("rl:prov")
    lims = [RedisRateLimiter(r, "prov", limit=5, window_ms=500) for _ in range(3)]
    t0 = time.monotonic()
    await asyncio.gather(*(lims[i % 3].acquire() for i in range(10)))
    assert time.monotonic() - t0 >= 0.45  # 10 calls at 5/0.5s shared across 3 nodes needs >= 1 window
    await r.aclose()
