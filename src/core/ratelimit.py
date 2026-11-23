"""Rate limiting and circuit breaking.

* ``TokenBucket`` – in-process async token bucket (per domain / per LLM provider).
* ``RedisRateLimiter`` – cluster-wide sliding-window limiter so N worker nodes
  share one budget per domain/provider (atomic via a Lua script).
* ``CircuitBreaker`` – stops hammering a failing source; half-opens after a cooldown.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TokenBucket:
    def __init__(self, rate_per_s: float, burst: float | None = None) -> None:
        self.rate = max(rate_per_s, 1e-6)
        self.capacity = burst if burst is not None else max(1.0, rate_per_s)
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> float:
        """Wait until ``tokens`` are available. Returns seconds waited."""
        waited = 0.0
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
                self._updated = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return waited
                need = (tokens - self._tokens) / self.rate
                waited += need
                await asyncio.sleep(need)

    def penalize(self, seconds: float) -> None:
        """Drain the bucket so callers back off (used on 429 Retry-After)."""
        self._tokens = -seconds * self.rate
        self._updated = time.monotonic()


class DomainLimiters:
    def __init__(self, default_rps: float, overrides: dict[str, float] | None = None) -> None:
        self.default_rps = default_rps
        self.overrides = overrides or {}
        self._buckets: dict[str, TokenBucket] = {}

    def get(self, domain: str) -> TokenBucket:
        if domain not in self._buckets:
            rps = next((v for k, v in self.overrides.items() if domain.endswith(k)), self.default_rps)
            self._buckets[domain] = TokenBucket(rps)
        return self._buckets[domain]


_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count < limit then
  redis.call('ZADD', key, now, ARGV[4])
  redis.call('PEXPIRE', key, window)
  return 0
end
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
return tonumber(oldest[2]) + window - now
"""


class RedisRateLimiter:
    """Distributed sliding-window limiter: ``limit`` calls per ``window_ms`` across all nodes."""

    def __init__(self, redis: Any, key: str, limit: int, window_ms: int = 60_000) -> None:
        self.redis, self.key, self.limit, self.window_ms = redis, f"rl:{key}", limit, window_ms
        self._script = redis.register_script(_SLIDING_WINDOW_LUA)
        self._seq = 0

    async def acquire(self) -> float:
        waited = 0.0
        while True:
            self._seq += 1
            now_ms = int(time.time() * 1000)
            wait_ms = int(await self._script(keys=[self.key], args=[now_ms, self.window_ms, self.limit, f"{now_ms}-{id(self)}-{self._seq}"]))
            if wait_ms <= 0:
                return waited
            waited += wait_ms / 1000
            await asyncio.sleep(wait_ms / 1000)


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(RuntimeError):
    pass


@dataclass
class CircuitBreaker:
    threshold: int = 5
    cooldown_s: float = 60.0
    failures: int = 0
    state: CircuitState = CircuitState.CLOSED
    opened_at: float = 0.0
    clock: Any = field(default=time.monotonic, repr=False)

    def allow(self) -> bool:
        if self.state is CircuitState.OPEN:
            if self.clock() - self.opened_at >= self.cooldown_s:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self.failures += 1
        if self.state is CircuitState.HALF_OPEN or self.failures >= self.threshold:
            self.state = CircuitState.OPEN
            self.opened_at = self.clock()


class CircuitRegistry:
    def __init__(self, threshold: int, cooldown_s: float) -> None:
        self._breakers: dict[str, CircuitBreaker] = defaultdict(lambda: CircuitBreaker(threshold, cooldown_s))

    def get(self, key: str) -> CircuitBreaker:
        return self._breakers[key]
