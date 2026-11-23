"""Back-off helpers shared by the HTTP client and the LLM orchestrator."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def backoff_delay(attempt: int, base: float = 1.0, cap: float = 30.0, rng: random.Random | None = None) -> float:
    """Exponential back-off with *full jitter* (AWS architecture blog variant).

    delay = U(0, min(cap, base * 2**attempt)); a floor of ``base/4`` avoids hot loops.
    """
    r = rng or random
    ceiling = min(cap, base * (2 ** max(0, attempt)))
    return max(base / 4, r.uniform(0, ceiling))


def parse_retry_after(value: str | None, *, now: datetime | None = None, cap: float = 120.0) -> float | None:
    """Parse ``Retry-After`` (delta-seconds or HTTP-date). Returns seconds or None."""
    if not value:
        return None
    value = value.strip()
    try:
        secs = float(value)
        return max(0.0, min(cap, secs))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta = (dt - (now or datetime.now(UTC))).total_seconds()
    return max(0.0, min(cap, delta))
