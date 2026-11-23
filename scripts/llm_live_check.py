"""LIVE LLM orchestration checks against the real configured providers (costs a few requests).

1. direct extraction on a real stored product/news text with the configured chain;
2. forced fallback: a provider with an intentionally invalid key placed *first* (real 400/401/403
   from Google), followed by the real chain -> must fall back and still succeed;
3. forced chunking: the same chain with a tiny input budget on a long real article -> several
   chunks, merged deterministically, every accepted value evidence-checked;
4. a hallucination probe: asks for a field the text does not contain -> must come back null.

Writes ``docs/llm_live_check.json``. Never logs keys.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.settings import get_settings
from src.database.repository import Repository
from src.database.session import Database
from src.extraction.orchestrator import ExtractionFailed, ExtractionTask, FieldSpec, LLMOrchestrator
from src.extraction.providers.vendors import DeepSeekProvider, GeminiProvider, GroqProvider, build_providers
from src.extraction.tasks import NEWS_ENTITIES, PRODUCT_PRICING
from src.monitoring.logging import redact

PROBE = ExtractionTask(
    name="hallucination_probe",
    instructions="Extract the CEO's date of birth and the company's 2019 revenue if, and only if, they are written in the document.",
    fields={"ceo_birth_date": FieldSpec("string", "CEO date of birth"), "revenue_2019": FieldSpec("string", "2019 revenue")},
    max_output_tokens=200,
)


PROVES = {
    "1_direct_extraction_product_pricing": "live generation + JSON parsing + evidence validation on the configured primary",
    "1b_direct_extraction_real_news_entities": "live extraction on a real stored article",
    "4_hallucination_probe_fields_absent": "absent facts stay null (no invention) on a live model",
    "2_forced_fallback_invalid_primary_key": "real Google auth failure for an invalid key is classified and the chain falls back",
    "3_forced_chunking_1500_token_budget": "live multi-chunk extraction with deterministic merge",
    "5_real_groq_deepseek_auth_failures_then_fallback": "real Groq + DeepSeek HTTP auth failures handled, then fallback to a working provider",
}


def summarise(name: str, res: object, t0: float) -> dict:
    r = res  # ExtractionResult
    return {"check": name, "ok": True, "live": True, "proves": PROVES.get(name, "live fallback to this vendor"), "seconds": round(time.perf_counter() - t0, 2), "providers_used": r.providers_used,  # type: ignore[attr-defined]
            "chunks_total": r.chunks_total, "chunks_failed": r.chunks_failed, "extraction_status": r.status.value, "confidence": r.confidence,  # type: ignore[attr-defined]
            "accepted": r.data, "evidence": {k: v[:160] for k, v in r.evidence.items()}, "rejected": r.rejected_fields,  # type: ignore[attr-defined]
            "attempts": [f"{a.provider}:{a.outcome}" for a in r.attempts]}  # type: ignore[attr-defined]


def status_of(c: dict) -> str:
    if c.get("ok"):
        return "PASS"
    err = str(c.get("error", ""))
    if any(t in err for t in ("unavailable", "rate_limited", "timeout", "503", "429")):
        return "UNAVAILABLE_TRANSIENT"  # provider overload persisted through retries: not a logic failure, but not a pass
    return "FAIL"


async def main(only: set[str] | None = None) -> dict:
    # live checks are patient with transient overload (Gemini 503 "high demand" was observed repeatedly)
    s = get_settings().model_copy(update={"llm_max_retries_per_provider": 5, "llm_backoff_base_s": 2.0, "llm_backoff_max_s": 30.0})
    chain = [p for p in build_providers(s) if p.configured]
    out: dict = {"run_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "configured_chain": [p.name for p in chain], "checks": []}
    if not chain:
        out["error"] = "no LLM provider configured"
        return out
    db = Database(s.database_url)
    repo = Repository(db)
    news = [n for n in await repo.fetch_records("NEWS") if n["content"].get("full_text")]
    await db.dispose()
    article = max(news, key=lambda n: len(n["content"]["full_text"])) if news else None
    product_text = "Atlas\n\nAtlas is free to start; upgrade to the Pro plan for $20/month. Enterprise plans: contact sales."

    async def run(name: str, orch: LLMOrchestrator, task: ExtractionTask, text: str) -> None:
        if only and name not in only:
            return
        t0 = time.perf_counter()
        try:
            res = await orch.extract(task, text)
            c = summarise(name, res, t0)
            att = c["attempts"]
            expect = {  # a check passes only if its specific property is observed, not merely "no exception"
                "1_direct_extraction_product_pricing": c["accepted"].get("pricing_model") == "FREEMIUM",
                "4_hallucination_probe_fields_absent": not c["accepted"],
                "1b_direct_extraction_real_news_entities": bool(c["accepted"].get("organizations")),
                "2_forced_fallback_invalid_primary_key": any(a.startswith("gemini_invalid_key:client_error") for a in att)
                and any(a.endswith(":ok") and not a.startswith("gemini_invalid_key") for a in att),
                "3_forced_chunking_1500_token_budget": (c["chunks_total"] or 0) > 1,
                "5_real_groq_deepseek_auth_failures_then_fallback": any(a.startswith("groq_invalid_key:client_error") for a in att)
                and any(a.startswith("deepseek_invalid_key:client_error") for a in att) and any(a.endswith(":ok") for a in att),
            }
            if name.startswith("6_fallback_to_"):
                expect[name] = any(a.startswith(name.removeprefix("6_fallback_to_") + ":ok") for a in att)
            c["ok"] = bool(expect.get(name, True))
            if orch.capture_raw:  # diagnostics: raw model output (redacted) + per-field verdicts, for post-mortems
                c["diagnostic"] = [{"chunk": a.chunk, "provider": a.provider, "outcome": a.outcome, "detail": redact(a.detail)[:800],
                                    "raw_response": redact(a.raw) if a.raw else None} for a in res.attempts]
            out["checks"].append(c)
        except ExtractionFailed as e:
            out["checks"].append({"check": name, "ok": False, "error": str(e)[:400]})

    o1 = LLMOrchestrator(chain, s, capture_raw=True)
    await run("1_direct_extraction_product_pricing", o1, PRODUCT_PRICING, product_text)
    if article:
        await run("1b_direct_extraction_real_news_entities", o1, NEWS_ENTITIES, f"{article['content']['title']}\n\n{article['content']['full_text']}")
        out["news_source_url"] = article["source"]["url"]
    await run("4_hallucination_probe_fields_absent", o1, PROBE, product_text)
    await o1.close()

    broken = GeminiProvider(api_key="invalid-key-for-fallback-test", model=s.gemini_model, input_token_budget=s.gemini_input_token_budget, rpm=60)
    broken.name = "gemini_invalid_key"
    o2 = LLMOrchestrator([broken, *[p for p in build_providers(s) if p.configured]], s)
    await run("2_forced_fallback_invalid_primary_key", o2, PRODUCT_PRICING, product_text)
    await o2.close()

    # 5. real vendor failures: Groq and DeepSeek endpoints are called with deliberately invalid keys; their real
    #    auth errors must be classified and the chain must fall through to the working provider.
    g_bad = GroqProvider(api_key="invalid-groq-key-for-test", model=s.groq_model, input_token_budget=s.groq_input_token_budget, rpm=30)
    d_bad = DeepSeekProvider(api_key="invalid-deepseek-key-for-test", model=s.deepseek_model, input_token_budget=s.deepseek_input_token_budget, rpm=30)
    g_bad.name, d_bad.name = "groq_invalid_key", "deepseek_invalid_key"
    o5 = LLMOrchestrator([g_bad, d_bad, *[p for p in build_providers(s) if p.configured]], s)
    await run("5_real_groq_deepseek_auth_failures_then_fallback", o5, PRODUCT_PRICING, product_text)
    await o5.close()

    # 6. real fallback TO each configured non-primary vendor (only possible when its key is configured)
    out["cross_vendor_success_fallback"] = {}
    for name in ("groq", "deepseek"):
        if not any(p.name == name and p.configured for p in build_providers(s)):
            out["checks"].append({"check": f"6_fallback_to_{name}", "ok": None, "check_status": "NOT_CONFIGURED", "live": False,
                                  "proves": f"real successful fallback to {name}", "error": f"{name.upper()}_API_KEY not set (optional provider)"})
    for p in [p for p in build_providers(s) if p.configured and p.name != "gemini"]:
        bad = GeminiProvider(api_key="invalid-key-for-fallback-test", model=s.gemini_model, input_token_budget=s.gemini_input_token_budget, rpm=60)
        bad.name = "gemini_invalid_key"
        o6 = LLMOrchestrator([bad, p], s)
        await run(f"6_fallback_to_{p.name}", o6, PRODUCT_PRICING, product_text)
        await o6.close()
        out["cross_vendor_success_fallback"][p.name] = "tested (see checks)"
    for name in ("groq", "deepseek"):
        out["cross_vendor_success_fallback"].setdefault(name, "NOT VERIFIED live: no API key configured (verified only with mocked HTTP in tests)")

    if article:
        small = [type(p)(api_key=p._api_key, model=p.model, input_token_budget=1500, rpm=10, timeout_s=s.llm_timeout_s) for p in chain]
        o3 = LLMOrchestrator(small, s, max_chunks=4)
        await run("3_forced_chunking_1500_token_budget", o3, NEWS_ENTITIES, f"{article['content']['title']}\n\n{article['content']['full_text']}")
        await o3.close()
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="re-run only these checks; results are merged into the existing docs/llm_live_check.json")
    only = set(ap.parse_args().only or []) or None
    res = asyncio.run(main(only))
    for c in res.get("checks", []):
        c.setdefault("check_status", status_of(c))
        c.setdefault("run_at", res["run_at"])
    prev_path = Path("docs/llm_live_check.json")
    if only and prev_path.exists():
        prev = json.loads(prev_path.read_text(encoding="utf-8-sig"))
        fresh = {c["check"] for c in res["checks"]}
        kept = [c | {"check_status": c.get("check_status") or status_of(c), "run_at": c.get("run_at", prev.get("run_at"))}
                for c in prev.get("checks", []) if c["check"] not in fresh]
        res["checks"] = kept + res["checks"]
        res["merged_from_previous_run"] = prev.get("run_at")
    res["summary"] = {st: sum(1 for c in res.get("checks", []) if c.get("check_status") == st) for st in ("PASS", "FAIL", "UNAVAILABLE_TRANSIENT", "NOT_CONFIGURED")}
    Path("docs").mkdir(exist_ok=True)
    Path("docs/llm_live_check.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    for c in res.get("checks", []):
        print(f"[{c.get('check_status')}] {c['check']}: providers={c.get('providers_used')} chunks={c.get('chunks_total')} "
              f"accepted={c.get('accepted')} attempts={c.get('attempts')} {c.get('error', '')}")
    if "error" in res:
        print("ERROR:", res["error"])
