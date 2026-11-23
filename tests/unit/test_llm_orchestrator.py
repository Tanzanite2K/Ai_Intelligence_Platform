"""LLM orchestration: fallback chain, 429 back-off, 413 splitting, malformed JSON, evidence validation.

Providers are mocked at two levels:
* ``ScriptedProvider`` – in-process fake returning scripted outcomes (logic tests);
* real ``GeminiProvider``/``GroqProvider``/``DeepSeekProvider`` against
  ``aioresponses`` HTTP mocks (vendor error-mapping tests).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

import pytest

from src.config.settings import Settings
from src.core.schemas import ValidationStatus
from src.extraction.chunking import estimate_tokens
from src.extraction.orchestrator import ExtractionFailed, LLMOrchestrator, NoProviderAvailable
from src.extraction.providers.base import (
    AuthError,
    BaseProvider,
    LLMRequest,
    LLMResponse,
    MalformedResponseError,
    PayloadTooLargeError,
    ProviderUnavailableError,
    RateLimitError,
    classify_http_error,
)
from src.extraction.providers.vendors import DeepSeekProvider, GeminiProvider, GroqProvider
from src.extraction.tasks import NEWS_ENTITIES, PRODUCT_PRICING

DOC = "Atlas by Acme Robotics\n\nAtlas is free to start; upgrade to the Pro plan for $20/month. Built by Acme Robotics in Berlin."
GOOD = {"fields": {"pricing_model": {"value": "FREEMIUM", "evidence": "free to start; upgrade to the Pro plan for $20/month"},
                   "startup_name": {"value": "Acme Robotics", "evidence": "Built by Acme Robotics"}}}


class ScriptedProvider(BaseProvider):
    def __init__(self, name: str, script: list[Any] | Callable[[LLMRequest], Any], budget: int = 8000) -> None:
        super().__init__(api_key="k", model=f"{name}-model", input_token_budget=budget, rpm=60_000)
        self.name = name
        self.script = script
        self.calls: list[LLMRequest] = []

    def build_request(self, req):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def parse_response(self, data, latency):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    async def complete(self, req: LLMRequest) -> LLMResponse:
        self.calls.append(req)
        step = self.script(req) if callable(self.script) else (self.script.pop(0) if self.script else GOOD)
        if isinstance(step, Exception):
            raise step
        text = step if isinstance(step, str) else json.dumps(step)
        return LLMResponse(text, self.name, self.model, estimate_tokens(req.user), 50, 0.01)


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, d: float) -> None:
        self.calls.append(d)


@pytest.fixture
def s() -> Settings:
    return Settings(_env_file=None, database_url="sqlite+aiosqlite://", llm_max_retries_per_provider=3, llm_backoff_base_s=1.0, llm_backoff_max_s=8.0)


def orch(providers: list[BaseProvider], s: Settings, sleeps: Sleeps | None = None, **kw: Any) -> LLMOrchestrator:
    return LLMOrchestrator(providers, s, sleep=sleeps or Sleeps(), **kw)


async def test_happy_path_primary_only(s: Settings) -> None:
    g = ScriptedProvider("gemini", [GOOD])
    r = await orch([g, ScriptedProvider("groq", [])], s).extract(PRODUCT_PRICING, DOC)
    assert r.data == {"pricing_model": "FREEMIUM", "startup_name": "Acme Robotics"}
    assert r.status is ValidationStatus.VERIFIED and r.confidence == 1.0 and r.providers_used == ["gemini"]
    assert "free to start" in r.evidence["pricing_model"]


async def test_fallback_gemini_5xx_to_groq(s: Settings) -> None:
    sleeps = Sleeps()
    g = ScriptedProvider("gemini", [ProviderUnavailableError("503", provider="gemini")] * 4)
    q = ScriptedProvider("groq", [GOOD])
    r = await orch([g, q, ScriptedProvider("deepseek", [])], s, sleeps).extract(PRODUCT_PRICING, DOC)
    assert r.providers_used == ["groq"] and len(g.calls) == 4 and len(q.calls) == 1
    assert len(sleeps.calls) == 3  # backoff between gemini retries


async def test_429_waits_retry_after_then_succeeds(s: Settings) -> None:
    sleeps = Sleeps()
    g = ScriptedProvider("gemini", [RateLimitError("429", retry_after=7.0, provider="gemini"), RateLimitError("429", provider="gemini"), GOOD])
    r = await orch([g], s, sleeps).extract(PRODUCT_PRICING, DOC)
    assert r.providers_used == ["gemini"]
    assert sleeps.calls[0] == 7.0  # Retry-After honoured exactly
    assert 0.25 <= sleeps.calls[1] <= 2.0  # then exponential backoff with full jitter (attempt 1 -> cap 2s)
    assert [a.outcome for a in r.attempts] == ["429", "429", "ok"]


async def test_429_long_wait_falls_back_immediately(s: Settings) -> None:
    sleeps = Sleeps()
    g = ScriptedProvider("gemini", [RateLimitError("429", retry_after=600, provider="gemini")])
    q = ScriptedProvider("groq", [GOOD])
    r = await orch([g, q], s, sleeps, max_wait_s=30).extract(PRODUCT_PRICING, DOC)
    assert r.providers_used == ["groq"] and sleeps.calls == []


async def test_429_exhausts_retries_then_deepseek(s: Settings) -> None:
    rl = lambda: RateLimitError("429", provider="x")  # noqa: E731
    g = ScriptedProvider("gemini", [rl() for _ in range(4)])
    q = ScriptedProvider("groq", [rl() for _ in range(4)])
    d = ScriptedProvider("deepseek", [GOOD])
    r = await orch([g, q, d], s).extract(PRODUCT_PRICING, DOC)
    assert r.providers_used == ["deepseek"] and len(g.calls) == 4 and len(q.calls) == 4


async def test_413_bisects_and_merges(s: Settings) -> None:
    big = "Atlas by Acme Robotics\n\n" + "\n\n".join(
        [f"Filler paragraph {i} about the market landscape and general commentary." for i in range(40)]
        + ["Atlas is free to start; upgrade to the Pro plan for $20/month."]
        + [f"More filler {i} on company history and the founding team." for i in range(40)]
    )

    def script(req: LLMRequest) -> Any:
        if estimate_tokens(req.user) > 900:
            return PayloadTooLargeError("413 Payload Too Large", provider="groq")
        if "free to start" in req.user:
            return GOOD["fields"] and {"fields": {"pricing_model": GOOD["fields"]["pricing_model"]}}
        return {"fields": {"pricing_model": {"value": None, "evidence": None}, "startup_name": {"value": "Acme Robotics", "evidence": "Atlas by Acme Robotics"}}}

    q = ScriptedProvider("groq", script, budget=50_000)
    r = await orch([q], s).extract(PRODUCT_PRICING, big)
    outcomes = [a.outcome for a in r.attempts]
    assert "413" in outcomes and outcomes.count("ok") >= 2
    assert r.data["pricing_model"] == "FREEMIUM" and r.data["startup_name"] == "Acme Robotics"
    # no payload that triggered a 413 was ever re-sent unchanged
    too_big = [c.user for c in q.calls if estimate_tokens(c.user) > 900]
    assert len(too_big) == len(set(too_big))


async def test_fallback_to_smaller_budget_provider_resplits(s: Settings) -> None:
    long_doc = DOC + "\n\n" + "\n\n".join(f"Background paragraph {i} with neutral content about robotics markets." for i in range(150))
    g = ScriptedProvider("gemini", [AuthError("401", provider="gemini")], budget=20_000)
    q = ScriptedProvider("groq", lambda req: GOOD if "free to start" in req.user else {"fields": {}}, budget=1500)
    r = await orch([g, q], s).extract(PRODUCT_PRICING, long_doc)
    assert len(q.calls) > 1  # one gemini-sized chunk became several groq-sized pieces
    assert all(estimate_tokens(c.user) + estimate_tokens(c.system) <= 1500 for c in q.calls)
    assert r.data["pricing_model"] == "FREEMIUM"


async def test_malformed_json_repair_retry(s: Settings) -> None:
    g = ScriptedProvider("gemini", ["I think the pricing is freemium.", GOOD])
    r = await orch([g], s).extract(PRODUCT_PRICING, DOC)
    assert r.data["pricing_model"] == "FREEMIUM"
    assert "PREVIOUS ANSWER WAS REJECTED" in g.calls[1].user


async def test_malformed_twice_falls_back(s: Settings) -> None:
    g = ScriptedProvider("gemini", ["nope", '{"fields": '])
    q = ScriptedProvider("groq", [GOOD])
    r = await orch([g, q], s).extract(PRODUCT_PRICING, DOC)
    assert r.providers_used == ["groq"]


async def test_hallucinated_value_rejected_not_invented(s: Settings) -> None:
    bad = {"fields": {"pricing_model": {"value": "ENTERPRISE", "evidence": "Contact sales for enterprise pricing"},
                      "startup_name": {"value": "Acme Robotics", "evidence": "Built by Acme Robotics"}}}
    r = await orch([ScriptedProvider("gemini", [bad])], s).extract(PRODUCT_PRICING, DOC)
    assert "pricing_model" not in r.data and "hallucination" in r.rejected_fields["pricing_model"]
    assert r.data == {"startup_name": "Acme Robotics"}
    assert r.status is ValidationStatus.PARTIAL and r.confidence == 0.5


async def test_wrong_type_rejected(s: Settings) -> None:
    bad = {"fields": {"pricing_model": {"value": "SUPER_CHEAP", "evidence": "free to start"}}}
    r = await orch([ScriptedProvider("gemini", [bad])], s).extract(PRODUCT_PRICING, DOC)
    assert "type error" in r.rejected_fields["pricing_model"]


async def test_all_providers_fail_raises(s: Settings) -> None:
    provs = [ScriptedProvider(n, [AuthError("401", provider=n)]) for n in ("gemini", "groq", "deepseek")]
    with pytest.raises(ExtractionFailed):
        await orch(provs, s).extract(PRODUCT_PRICING, DOC)


async def test_no_provider_configured(s: Settings) -> None:
    p = GeminiProvider(api_key=None, model="m", input_token_budget=8000, rpm=10)
    with pytest.raises(NoProviderAvailable):
        await orch([p], s).extract(PRODUCT_PRICING, DOC)


async def test_partial_chunk_failure_marks_partial(s: Settings) -> None:
    doc = "Lead line\n\n" + "\n\n".join(f"OpenAI paragraph {i} " + "filler words " * 60 for i in range(12))

    def script(req: LLMRequest) -> Any:
        if "paragraph 11 " in req.user:  # the last chunk fails on every provider
            return AuthError("401", provider="gemini")
        return {"fields": {"organizations": {"value": ["OpenAI"], "evidence": "OpenAI paragraph"}}}

    r = await orch([ScriptedProvider("gemini", script, budget=1500)], s).extract(NEWS_ENTITIES, doc)
    assert r.chunks_failed >= 1 and r.chunks_total > r.chunks_failed
    assert r.status is ValidationStatus.PARTIAL and r.confidence < 1.0
    assert r.data["organizations"] == ["OpenAI"]


async def test_conflicting_chunks_majority_and_reported(s: Settings) -> None:
    doc = "This is a neutral lead paragraph about the tool.\n\n" + "\n\n".join([f"Part {i}: the tool is free to use. " + "x " * 300 for i in range(3)] + ["Part 9: Pro costs $20/month. " + "y " * 300])

    def script(req: LLMRequest) -> Any:
        if "Part 9" in req.user:
            return {"fields": {"pricing_model": {"value": "PAID", "evidence": "Pro costs $20/month"}}}
        return {"fields": {"pricing_model": {"value": "FREE", "evidence": "the tool is free to use"}}}

    r = await orch([ScriptedProvider("gemini", script, budget=1200)], s).extract(PRODUCT_PRICING, doc)
    assert r.data["pricing_model"] == "FREE" and "pricing_model" in r.conflicts and r.confidence < 1


# ------------------------------------------------- vendor HTTP error mapping
@pytest.mark.parametrize(
    "status,body,headers,exc",
    [
        (429, '{"error":{"code":429,"status":"RESOURCE_EXHAUSTED","details":[{"retryDelay":"17s"}]}}', {}, RateLimitError),
        (429, "rate limit", {"retry-after": "3"}, RateLimitError),
        (413, '{"error":{"message":"Request too large for model llama-3.3-70b-versatile ... tokens per minute (TPM)"}}', {}, PayloadTooLargeError),
        (400, '{"error":{"message":"This model\'s maximum context length is 65536 tokens"}}', {}, PayloadTooLargeError),
        (400, '{"error":{"message":"The input token count exceeds the maximum number of tokens allowed"}}', {}, PayloadTooLargeError),
        (401, "bad key", {}, AuthError),
        (503, "overloaded", {}, ProviderUnavailableError),
    ],
)
def test_classify_http_error(status, body, headers, exc) -> None:  # type: ignore[no-untyped-def]
    e = classify_http_error("x", status, body, headers)
    assert isinstance(e, exc)
    if status == 429 and "17s" in body:
        assert e.retry_after == 17.0  # type: ignore[attr-defined]
    if headers.get("retry-after"):
        assert e.retry_after == 3.0  # type: ignore[attr-defined]


async def test_real_providers_over_http_fallback_chain(s: Settings, mocked) -> None:  # type: ignore[no-untyped-def]
    """Gemini 429 (x4) -> Groq 413 at every split depth -> DeepSeek OK, through real request building/parsing."""
    gem = GeminiProvider(api_key="AIza-fake", model="gemini-2.0-flash", input_token_budget=8000, rpm=6000)
    groq = GroqProvider(api_key="gsk_fakefakefakefake", model="llama", input_token_budget=8000, rpm=6000)
    ds = DeepSeekProvider(api_key="sk-fakefakefakefake", model="deepseek-chat", input_token_budget=8000, rpm=6000)
    mocked.post(re.compile(r"https://generativelanguage\.googleapis\.com/.*"), status=429, body='{"error":{"status":"RESOURCE_EXHAUSTED"}}', repeat=True)
    mocked.post("https://api.groq.com/openai/v1/chat/completions", status=413, body='{"error":{"message":"Request too large"}}', repeat=True)
    mocked.post("https://api.deepseek.com/chat/completions", status=200, payload={
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(GOOD)}}], "usage": {"prompt_tokens": 100, "completion_tokens": 40}})
    o = LLMOrchestrator([gem, groq, ds], s, sleep=Sleeps(), max_split_depth=1)
    r = await o.extract(PRODUCT_PRICING, DOC)
    await o.close()
    assert r.providers_used == ["deepseek"] and r.data["pricing_model"] == "FREEMIUM"
    outcomes = [(a.provider, a.outcome) for a in r.attempts]
    assert ("gemini", "429") in outcomes and ("groq", "413") in outcomes and ("deepseek", "ok") in outcomes
    # API keys never appear in logged attempt details
    assert not any("AIza" in a.detail or "gsk_" in a.detail for a in r.attempts)


async def test_gemini_parses_and_detects_truncation(mocked) -> None:  # type: ignore[no-untyped-def]
    gem = GeminiProvider(api_key="k", model="gemini-2.0-flash", input_token_budget=8000, rpm=6000)
    url = re.compile(r"https://generativelanguage\.googleapis\.com/.*")
    mocked.post(url, payload={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"fields":{}}'}]}}], "usageMetadata": {"promptTokenCount": 10}})
    mocked.post(url, payload={"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": '{"fields":'}]}}]})
    mocked.post(url, payload={"promptFeedback": {"blockReason": "SAFETY"}})
    req = LLMRequest("sys", "user")
    assert (await gem.complete(req)).text == '{"fields":{}}'
    from src.extraction.providers.base import IncompleteResponseError

    with pytest.raises(IncompleteResponseError):
        await gem.complete(req)
    with pytest.raises(MalformedResponseError):
        await gem.complete(req)
    await gem.close()


# --- 1b regression: list field returned as one comma-separated string ---------------------------------------------
ART = ("Baseten has released bison-1 and groundhog-1, built on Google’s \nGemma 4\n. Weights on Hugging Face.\n"
       "The post \nBaseten Releases Bison-1\n appeared first on \nMarkTechPost\n.")
EV = "Baseten has released bison-1 and groundhog-1 | built on Google’s Gemma 4 | Weights on Hugging Face"


async def test_comma_string_list_triggers_repair_then_array_accepted(s: Settings) -> None:
    g = ScriptedProvider("gemini", [{"fields": {"organizations": {"value": "Baseten, Google", "evidence": EV}}},
                                    {"fields": {"organizations": {"value": ["Baseten", "Google"], "evidence": EV}}}])
    r = await orch([g], s).extract(NEWS_ENTITIES, ART)
    assert r.data["organizations"] == ["Baseten", "Google"]
    assert [a.outcome for a in r.attempts] == ["malformed", "ok"]
    assert "JSON array" in g.calls[1].user and "JSON array" in g.calls[0].system  # repair hint + schema instruction


async def test_repair_returning_same_string_falls_back_to_per_name_validation(s: Settings) -> None:
    same = {"fields": {"organizations": {"value": "Baseten, Google, Hugging Face, MarkTechPost",
                                         "evidence": EV + " | The post appeared first on MarkTechPost"}}}
    g = ScriptedProvider("gemini", [same, same])
    r = await orch([g], s).extract(NEWS_ENTITIES, ART)
    assert r.data["organizations"] == ["Baseten", "Google", "Hugging Face"]  # MarkTechPost: footer, non-verbatim quote
    assert len(g.calls) == 2
