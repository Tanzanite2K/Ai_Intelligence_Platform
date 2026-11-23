"""Shared Gemini health check: transient retries, permanent no-retry, extraction required, selector/verifier parity.
All HTTP is mocked — these tests do NOT constitute live verification."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import aiohttp
from pydantic import SecretStr

from src.config.settings import Settings
from src.extraction.health import gemini_health_check, gemini_request_config

ROOT = Path(__file__).resolve().parents[2]
GEN = re.compile(r"https://generativelanguage\.googleapis\.com/v1beta/models/[^/?]+:generateContent")
OK_MIN = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"ok": true}'}]}}]}
OK_EXT = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(
    {"fields": {"pricing_model": {"value": "FREEMIUM", "evidence": "free to start; upgrade to the Pro plan for $20/month"}}})}]}}]}
E503 = '{"error":{"code":503,"message":"This model is currently experiencing high demand.","status":"UNAVAILABLE"}}'


def S(**kw) -> Settings:  # type: ignore[no-untyped-def]
    base = {"llm_backoff_base_s": 0.001, "llm_backoff_max_s": 0.01, "llm_max_retries_per_provider": 3}
    return Settings(_env_file=None, database_url="sqlite+aiosqlite://", **(base | kw))  # type: ignore[call-arg]


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, d: float) -> None:
        self.calls.append(d)


async def test_transient_503_then_success_is_healthy(mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.post(GEN, status=503, body=E503)
    mocked.post(GEN, payload=OK_MIN)
    mocked.post(GEN, payload=OK_EXT)
    sl = Sleeps()
    rec = await gemini_health_check(S(), "gemini-3.1-flash-lite", "k", sleep=sl)
    assert rec["healthy"] and rec["category"] == "AUTHENTICATED"
    assert [a["outcome"] for a in rec["minimal_generation"]["attempts"]] == ["transient:ProviderUnavailableError", "ok"]
    assert rec["api_calls"] == 3 and len(sl.calls) == 1
    assert rec["extraction"]["path"].startswith("LLMOrchestrator+GeminiProvider")


async def test_persistent_503_is_bounded_and_unhealthy(mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.post(GEN, status=503, body=E503, repeat=True)
    rec = await gemini_health_check(S(), "m", "k", sleep=Sleeps())
    assert not rec["healthy"] and rec["category"] == "SERVICE_UNAVAILABLE"
    assert rec["api_calls"] == 4  # 1 + llm_max_retries_per_provider, then stop
    assert rec["extraction"]["error"] == "not run"


async def test_429_with_retry_after_is_honoured(mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.post(GEN, status=429, body='{"error":{"status":"RESOURCE_EXHAUSTED"}}', headers={"Retry-After": "2"})
    mocked.post(GEN, payload=OK_MIN)
    mocked.post(GEN, payload=OK_EXT)
    sl = Sleeps()
    rec = await gemini_health_check(S(), "m", "k", sleep=sl)
    assert rec["healthy"] and sl.calls[0] == 2.0


async def test_401_is_not_retried(mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.post(GEN, status=401, body='{"error":{"message":"API key not valid"}}', repeat=True)
    rec = await gemini_health_check(S(), "m", "AQ.secret-key", sleep=Sleeps())
    assert not rec["healthy"] and rec["api_calls"] == 1 and rec["category"] == "AUTH_FAILED"
    assert "AQ.secret-key" not in json.dumps(rec)


async def test_plain_400_not_retried_when_no_thinking_config(mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.post(GEN, status=400, body='{"error":{"code":400,"message":"Request contains an invalid argument.","status":"INVALID_ARGUMENT"}}', repeat=True)
    rec = await gemini_health_check(S(gemini_thinking_budget=None), "m", "k", sleep=Sleeps())
    assert not rec["healthy"] and rec["api_calls"] == 1 and rec["category"] == "CONFIG_ERROR"


async def test_generic_400_with_thinking_config_retried_once_without_it(mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.post(GEN, status=400, body='{"error":{"code":400,"message":"Request contains an invalid argument.","status":"INVALID_ARGUMENT"}}')
    mocked.post(GEN, payload=OK_MIN)
    mocked.post(GEN, payload=OK_EXT)
    rec = await gemini_health_check(S(gemini_thinking_budget=0), "gemini-3.5-flash-lite", "k", sleep=Sleeps())
    assert rec["healthy"] and rec["minimal_generation"]["thinking_budget_sent"] is None
    bodies = [c.kwargs["json"]["generationConfig"] for v in mocked.requests.values() for c in v]
    assert "thinkingConfig" in bodies[0] and "thinkingConfig" not in bodies[1]


async def test_minimal_ok_but_extraction_unsupported_is_not_healthy(mocked) -> None:  # type: ignore[no-untyped-def]
    bad = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(
        {"fields": {"pricing_model": {"value": "ENTERPRISE", "evidence": "contact sales for enterprise"}}})}]}}]}
    mocked.post(GEN, payload=OK_MIN)
    mocked.post(GEN, payload=bad, repeat=True)
    rec = await gemini_health_check(S(), "m", "k", sleep=Sleeps())
    assert rec["minimal_generation"]["ok"] and not rec["extraction"]["ok"] and not rec["healthy"]


async def test_selector_and_verifier_use_identical_check_and_config(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Regression for the inconsistency: both tools must call the same function with the same settings."""
    seen: list[dict] = []

    async def spy(s, model, key, **kw):  # type: ignore[no-untyped-def]
        seen.append(gemini_request_config(s, model))
        return {"healthy": False, "category": "SERVICE_UNAVAILABLE", "api_calls": 1, "config": gemini_request_config(s, model),
                "minimal_generation": {"ok": False, "attempts": [{"outcome": "transient:ProviderUnavailableError"}], "error": "503"},
                "extraction": {"ok": False, "error": "not run"}}

    import src.extraction.health as health

    monkeypatch.setattr(health, "gemini_health_check", spy)

    def load(name: str):  # type: ignore[no-untyped-def]
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod

    sg, vi = load("select_gemini_model"), load("verify_integrations")
    monkeypatch.setattr(sg, "gemini_health_check", spy)
    s = S(gemini_api_key=SecretStr("k"), gemini_model="gemini-3.1-flash-lite")
    await sg.test_model(s, "k", "gemini-3.1-flash-lite", {})

    async def model_ok(*a, **k):  # type: ignore[no-untyped-def]
        return 200, "{}"

    monkeypatch.setattr(vi, "_req", model_ok)
    monkeypatch.chdir(tmp_path)  # gemini_health.json lands in a throwaway docs dir
    async with aiohttp.ClientSession() as http:
        c = await vi.check_gemini(s, http)
    assert len(seen) == 2 and seen[0] == seen[1]
    assert c.result == "FAIL" and c.category == "SERVICE_UNAVAILABLE"  # model-list availability alone never yields PASS
