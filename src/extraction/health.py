"""Single live health check for an LLM provider, shared by the model selector and the integration verifier.

Both tools call :func:`gemini_health_check` with the same settings (model, API version, timeout, thinking
budget), so they can no longer disagree because of different request configurations.

A model is healthy only if BOTH succeed live:
1. a minimal JSON-mode generation, and
2. a real extraction through the production path (``LLMOrchestrator`` + ``GeminiProvider`` + the
   ``PRODUCT_PRICING`` task + evidence validation) returning the expected, evidence-backed value.

Transient failures (HTTP 503/5xx, 429, timeouts) are retried a bounded number of times with exponential
backoff + jitter (honouring Retry-After). Permanent failures (400/401/403/404) are NOT retried, except the
provider's single retry without ``thinkingConfig`` when a model rejects that field.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from src.config.settings import Settings
from src.core.retry import backoff_delay
from src.extraction.orchestrator import ExtractionFailed, LLMOrchestrator
from src.extraction.providers.base import (
    LLMRequest,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
)
from src.extraction.providers.vendors import GeminiProvider
from src.extraction.tasks import PRODUCT_PRICING

TRANSIENT = (ProviderUnavailableError, RateLimitError, ProviderTimeoutError)
TEST_DOC = "Atlas\n\nAtlas is free to start; upgrade to the Pro plan for $20/month."
API_VERSION = "v1beta"


def _scrub(text: str, key: str | None) -> str:
    return (text.replace(key, "***") if key else text)[:400]


def gemini_request_config(s: Settings, model: str) -> dict[str, Any]:
    """The exact, non-secret configuration every live check uses (recorded in evidence)."""
    return {"model": model, "api_version": API_VERSION, "endpoint": f"{GeminiProvider.BASE}/{model}:generateContent",
            "timeout_s": s.llm_timeout_s, "thinking_budget": s.gemini_thinking_budget, "input_token_budget": s.gemini_input_token_budget,
            "max_attempts_transient": s.llm_max_retries_per_provider + 1}


async def gemini_health_check(
    s: Settings,
    model: str,
    key: str,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    provider_factory: Callable[..., GeminiProvider] = GeminiProvider,
) -> dict[str, Any]:
    """Run the live check; never raises. Returns a complete evidence record with ``healthy`` set."""
    cfg = gemini_request_config(s, model)
    rec: dict[str, Any] = {"config": cfg, "minimal_generation": {"ok": False, "attempts": []}, "extraction": {"ok": False, "error": "not run"},
                           "healthy": False, "api_calls": 0, "category": "SERVICE_UNAVAILABLE"}
    prov = provider_factory(api_key=key, model=model, input_token_budget=s.gemini_input_token_budget, rpm=60,
                            timeout_s=s.llm_timeout_s, thinking_budget=s.gemini_thinking_budget)
    calls = {"n": 0}
    orig_complete = prov.complete

    async def counted(req: LLMRequest) -> Any:
        calls["n"] += 1
        return await orig_complete(req)

    prov.complete = counted  # type: ignore[method-assign]
    try:
        # ---- 1. minimal generation with bounded retries for transient errors only
        for attempt in range(cfg["max_attempts_transient"]):
            t0 = time.perf_counter()
            try:
                r = await prov.complete(LLMRequest(system="Reply with JSON only.", user='Return exactly {"ok": true}', max_output_tokens=256))
                ok = isinstance(parsed := json.loads(r.text), dict) and parsed.get("ok") is True
                rec["minimal_generation"]["attempts"].append({"outcome": "ok" if ok else "unexpected_text", "seconds": round(time.perf_counter() - t0, 2)})
                rec["minimal_generation"].update(ok=ok, input_tokens=r.input_tokens, output_tokens=r.output_tokens, thinking_budget_sent=prov.thinking_budget)
                if not ok:
                    rec["category"] = "CONFIG_ERROR"
                break
            except TRANSIENT as e:
                retry_after = getattr(e, "retry_after", None)
                delay = min(retry_after if retry_after is not None else backoff_delay(attempt, base=2 * s.llm_backoff_base_s, cap=s.llm_backoff_max_s), 30.0)
                rec["minimal_generation"]["attempts"].append({"outcome": f"transient:{type(e).__name__}", "error": _scrub(str(e), key)[:160]})
                if attempt + 1 < cfg["max_attempts_transient"]:
                    await sleep(delay)
                    rec["minimal_generation"]["attempts"][-1]["retry_in_s"] = round(delay, 2)
                else:
                    rec["minimal_generation"]["error"] = f"still unavailable after {attempt + 1} attempts: {_scrub(str(e), key)[:200]}"
                    rec["category"] = "SERVICE_UNAVAILABLE"
            except (ProviderError, json.JSONDecodeError, ValueError) as e:  # permanent: no retry
                rec["minimal_generation"]["attempts"].append({"outcome": f"permanent:{type(e).__name__}", "error": _scrub(str(e), key)[:200]})
                rec["minimal_generation"]["error"] = _scrub(f"{type(e).__name__}: {e}", key)
                msg = str(e)
                rec["category"] = ("AUTH_FAILED" if ("API key not valid" in msg or " 401" in msg or " 403" in msg)
                                   else "CONFIG_ERROR")
                break
        if not rec["minimal_generation"]["ok"]:
            return rec
        # ---- 2. production extraction path (orchestrator has its own bounded transient retries)
        orch = LLMOrchestrator([prov], s, sleep=sleep)
        try:
            out = await orch.extract(PRODUCT_PRICING, TEST_DOC)
            ok = out.data.get("pricing_model") == "FREEMIUM" and "pricing_model" in out.evidence
            rec["extraction"] = {"ok": ok, "path": "LLMOrchestrator+GeminiProvider+PRODUCT_PRICING+validators", "accepted": out.data,
                                 "evidence": {k: v[:160] for k, v in out.evidence.items()}, "status": out.status.value, "confidence": out.confidence,
                                 "attempts": [f"{a.provider}:{a.outcome}" for a in out.attempts]}
            if not ok:
                rec["category"] = "CONFIG_ERROR"
        except ExtractionFailed as e:
            rec["extraction"] = {"ok": False, "path": "LLMOrchestrator+GeminiProvider+PRODUCT_PRICING+validators", "error": _scrub(str(e), key)}
            rec["category"] = "SERVICE_UNAVAILABLE" if any(t in str(e) for t in ("unavailable", "rate_limited", "timeout")) else "CONFIG_ERROR"
        rec["healthy"] = bool(rec["minimal_generation"]["ok"] and rec["extraction"]["ok"])
        if rec["healthy"]:
            rec["category"] = "AUTHENTICATED"
        return rec
    except Exception as e:
        rec["unexpected_error"] = _scrub(f"{type(e).__name__}: {e}", key)
        return rec
    finally:
        rec["api_calls"] = calls["n"]
        await prov.close()
