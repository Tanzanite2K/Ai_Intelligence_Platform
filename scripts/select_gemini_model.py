"""Choose a supported Gemini Flash model for this key and verify it with real requests.

1. lists models with the authenticated API (paginated);
2. keeps Flash text models that support ``generateContent`` (no image/tts/audio/live/embedding variants),
   ranks stable > preview, higher version first, full Flash before Flash-Lite;
3. for each candidate (max 4): a minimal JSON-mode generation, then a full evidence-checked extraction
   through the project's own ``GeminiProvider`` + ``LLMOrchestrator``;
4. with ``--apply`` rewrites only the ``GEMINI_MODEL=`` line of ``.env`` (API key and all other lines untouched).

Writes ``docs/gemini_model_selection.json``. Never prints the key.

    python scripts/select_gemini_model.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aiohttp

from src.config.settings import Settings, get_settings
from src.extraction.health import gemini_health_check
from src.extraction.providers.vendors import GeminiProvider

API = "https://generativelanguage.googleapis.com/v1beta/models"
EXCLUDE = re.compile(r"(image|tts|audio|live|embedding|vision|thinking-exp|robotics|computer-use|native)", re.I)
TEST_DOC = "Atlas\n\nAtlas is free to start; upgrade to the Pro plan for $20/month."


def rank_models(models: list[dict[str, Any]], rejected: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    """Flash text models that *declare* generateContent support, best first.

    Entries with a missing/invalid name or without a ``supportedGenerationMethods`` list are never assumed
    to work; they are skipped and (optionally) recorded in ``rejected`` with the reason.
    """
    out = []
    for m in models:
        if not isinstance(m, dict):
            continue
        name = m.get("name")
        mid = name.split("/")[-1] if isinstance(name, str) else ""
        methods = m.get("supportedGenerationMethods")
        reason = None
        if not mid:
            reason = "missing name"
        elif "flash" not in mid:
            reason = "not a Flash model"
        elif EXCLUDE.search(mid):
            reason = "non-text variant"
        elif not isinstance(methods, list):
            reason = "supportedGenerationMethods missing"
        elif "generateContent" not in methods:
            reason = "generateContent not supported"
        if reason:
            if rejected is not None and mid and "flash" in mid:
                rejected.append({"model": mid, "reason": reason})
            continue
        ver = re.search(r"gemini-(\d+(?:\.\d+)?)", mid)
        unstable = bool(re.search(r"(preview|exp|latest)", mid))
        out.append((not unstable, float(ver.group(1)) if ver else 0.0, "lite" not in mid, -len(mid), m | {"id": mid}))
    out.sort(key=lambda t: t[:4], reverse=True)
    return [t[4] for t in out]


def update_env_line(text: str, key: str, value: str) -> str:
    """Replace (or append) ``key=value`` preserving every other line and the file's newline style."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    done = False
    for i, line in enumerate(lines):
        if re.match(rf"^\s*{re.escape(key)}\s*=", line):
            lines[i] = f"{key}={value}"
            done = True
    if not done:
        lines.append(f"{key}={value}")
    return nl.join(lines) + nl


async def list_models(key: str) -> list[dict[str, Any]]:
    models, token = [], None
    async with aiohttp.ClientSession(trust_env=True) as http:
        for _ in range(10):
            params = {"pageSize": "1000"} | ({"pageToken": token} if token else {})
            async with http.get(API, headers={"x-goog-api-key": key}, params=params, timeout=aiohttp.ClientTimeout(total=30)) as r:
                body = await r.text()
                if r.status != 200:
                    raise RuntimeError(f"models.list HTTP {r.status}: {body[:200].replace(key, '***')}")
                data = json.loads(body)
            models += data.get("models", [])
            token = data.get("nextPageToken")
            if not token:
                break
    return models


def _scrub(text: str, key: str) -> str:
    return text.replace(key, "***")[:400]


async def test_model(s: Settings, key: str, mid: str, meta: dict[str, Any], provider_cls: type = GeminiProvider) -> dict[str, Any]:
    """Live-test one model with the SAME check the integration verifier uses. Never raises."""
    hc = await gemini_health_check(s, mid, key, provider_factory=provider_cls)
    try:
        limit = int(meta["inputTokenLimit"]) if meta.get("inputTokenLimit") else None
    except (TypeError, ValueError):
        limit = None
    budget_ok = limit is None or s.gemini_input_token_budget <= limit
    return {"model": mid, "inputTokenLimit": meta.get("inputTokenLimit"), "outputTokenLimit": meta.get("outputTokenLimit"),
            "minimal_generation": hc["minimal_generation"], "extraction": hc["extraction"], "config": hc["config"], "api_calls": hc["api_calls"],
            "category": hc["category"], "budget_within_model_limit": budget_ok, "compatible": bool(hc["healthy"] and budget_ok),
            **({"unexpected_error": hc["unexpected_error"]} if "unexpected_error" in hc else {})}


async def main(apply: bool) -> dict[str, Any]:
    s = get_settings()
    key = s.gemini_api_key.get_secret_value() if s.gemini_api_key else None
    rep: dict[str, Any] = {"run_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "configured_model": s.gemini_model, "applied": False}
    if not key:
        rep["error"] = "GEMINI_API_KEY not configured"
        return rep
    try:
        models = await list_models(key)
    except Exception as e:
        rep["error"] = _scrub(f"models.list failed: {e}", key)
        rep["selected_model"] = None
        return rep
    rejected: list[dict[str, str]] = []
    ranked = rank_models(models, rejected)
    rep["models_listed"] = len(models)
    rep["flash_candidates"] = [m["id"] for m in ranked]
    rep["flash_rejected"] = rejected
    ids = {str(m.get("name", "")).split("/")[-1] for m in models if isinstance(m, dict)}
    rep["configured_model_listed"] = s.gemini_model in ids
    # Minimise API calls: if the configured model passes the live check, keep it and test nothing else.
    order = ([m for m in ranked if m["id"] == s.gemini_model] + [m for m in ranked if m["id"] != s.gemini_model])[:6]
    rep["tests"] = []
    chosen = None
    for m in order:
        t = await test_model(s, key, m["id"], m)
        rep["tests"].append(t)
        if t.get("compatible"):
            chosen = m["id"]
            break
    rep["selected_model"] = chosen
    rep["api_calls_total"] = sum(t.get("api_calls", 0) for t in rep["tests"])
    if not chosen:
        rep["error"] = "no listed Flash model passed both live tests; GEMINI_MODEL left unchanged (see tests[].*.error)"
    if chosen and apply and chosen != s.gemini_model:
        env = Path(".env")
        raw = env.read_bytes().decode("utf-8")
        new = update_env_line(raw, "GEMINI_MODEL", chosen)
        env.write_bytes(new.encode("utf-8"))
        rep["applied"] = True
        rep["env_change"] = f"GEMINI_MODEL: {s.gemini_model} -> {chosen} (only this line changed)"
    elif chosen == s.gemini_model:
        rep["env_change"] = "none (configured model verified)"
    return rep


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the verified model to GEMINI_MODEL in .env")
    out = asyncio.run(main(ap.parse_args().apply))
    Path("docs").mkdir(exist_ok=True)
    Path("docs/gemini_model_selection.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "tests"} | {"tests": [
        {"model": t.get("model"), "compatible": t.get("compatible"), "minimal_error": t.get("minimal_generation", {}).get("error"),
         "extraction_error": t.get("extraction", {}).get("error"), "unexpected": t.get("unexpected_error")} for t in out.get("tests", [])]}, indent=2))
    sys.exit(0 if out.get("selected_model") else 1)
