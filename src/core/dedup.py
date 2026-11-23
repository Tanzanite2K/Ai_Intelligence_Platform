"""Distributed de-duplication / exactly-once *processing* claims.

Three layers keep N crawler nodes from processing the same article or job twice:

1. **Claim (Redis ``SET NX PX``)** – before expensive work (fetch full text, LLM
   call) a worker atomically claims ``claim:{key}``. Losers skip. The claim
   carries a lease TTL so a crashed worker's item is reclaimed later.
2. **Done marker** – after a successful DB commit the key moves to
   ``done:{key}`` (long TTL). Future claims short-circuit.
3. **Database unique constraint** – ``UNIQUE(record_type, dedup_hash)`` with
   ``ON CONFLICT`` makes the write idempotent even if Redis is flushed.

``key`` = SHA-256 of the canonical URL (and, for syndicated copies, the text
fingerprint is checked as a secondary key).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Protocol


class Deduplicator(Protocol):
    async def claim(self, key: str) -> bool: ...
    async def mark_done(self, key: str) -> None: ...
    async def release(self, key: str) -> None: ...
    async def is_done(self, key: str) -> bool: ...


class InMemoryDeduplicator:
    """Single-process implementation (tests / local runs)."""

    def __init__(self, lease_s: float = 900) -> None:
        self._claims: dict[str, float] = {}
        self._done: set[str] = set()
        self._lock = asyncio.Lock()
        self.lease_s = lease_s

    async def claim(self, key: str) -> bool:
        async with self._lock:
            if key in self._done:
                return False
            exp = self._claims.get(key)
            if exp and exp > time.monotonic():
                return False
            self._claims[key] = time.monotonic() + self.lease_s
            return True

    async def mark_done(self, key: str) -> None:
        async with self._lock:
            self._done.add(key)
            self._claims.pop(key, None)

    async def release(self, key: str) -> None:
        async with self._lock:
            self._claims.pop(key, None)

    async def is_done(self, key: str) -> bool:
        return key in self._done


_RELEASE_LUA = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end"


class RedisDeduplicator:
    def __init__(self, redis: Any, *, namespace: str = "aip", lease_s: int = 900, done_ttl_s: int = 60 * 60 * 24 * 30) -> None:
        self.r = redis
        self.ns = namespace
        self.lease_ms = lease_s * 1000
        self.done_ttl_s = done_ttl_s
        self.owner = uuid.uuid4().hex
        self._release = redis.register_script(_RELEASE_LUA)

    def _k(self, kind: str, key: str) -> str:
        return f"{self.ns}:{kind}:{key}"

    async def claim(self, key: str) -> bool:
        if await self.r.exists(self._k("done", key)):
            return False
        return bool(await self.r.set(self._k("claim", key), self.owner, nx=True, px=self.lease_ms))

    async def mark_done(self, key: str) -> None:
        pipe = self.r.pipeline(transaction=True)
        pipe.set(self._k("done", key), "1", ex=self.done_ttl_s)
        pipe.delete(self._k("claim", key))
        await pipe.execute()

    async def release(self, key: str) -> None:
        await self._release(keys=[self._k("claim", key)], args=[self.owner])

    async def is_done(self, key: str) -> bool:
        return bool(await self.r.exists(self._k("done", key)))


def build_deduplicator(redis_url: str | None) -> Deduplicator:
    if not redis_url:
        return InMemoryDeduplicator()
    import redis.asyncio as aioredis

    return RedisDeduplicator(aioredis.from_url(redis_url, decode_responses=True))
