"""Regression tests for the operational scripts (all HTTP mocked — these do NOT count as live verification)."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import aiohttp
import pytest
from pydantic import SecretStr

from src.config.settings import Settings

ROOT = Path(__file__).resolve().parents[2]


def load(name: str):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


vi = load("verify_integrations")
sg = load("select_gemini_model")


def s(**kw) -> Settings:  # type: ignore[no-untyped-def]
    return Settings(_env_file=None, database_url="sqlite+aiosqlite://", **kw)  # type: ignore[call-arg]


async def test_github_long_body_parses(mocked) -> None:  # type: ignore[no-untyped-def]
    """Regression: the old helper truncated bodies to 600 chars before json.loads -> 'Unterminated string'."""
    body = {"resources": {"core": {"limit": 5000, "remaining": 4999, "reset": 1}, **{f"r{i}": {"limit": 1, "pad": "x" * 50} for i in range(40)}}}
    assert len(json.dumps(body)) > 600
    mocked.get("https://api.github.com/rate_limit", payload=body)
    async with aiohttp.ClientSession() as http:
        c = await vi.check_github(s(github_token=SecretStr("ghp_x")), http)
    assert c.result == "PASS" and c.category == "AUTHENTICATED" and "5000" in c.detail


async def test_github_401_vs_network(mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get("https://api.github.com/rate_limit", status=401, body='{"message":"Bad credentials"}')
    async with aiohttp.ClientSession() as http:
        c = await vi.check_github(s(github_token=SecretStr("ghp_bad")), http)
    assert c.category == "AUTH_FAILED" and c.connectivity_verified
    mocked.get("https://api.github.com/rate_limit", exception=aiohttp.ClientConnectionError("dns"))
    async with aiohttp.ClientSession() as http:
        c = await vi.check_github(s(github_token=SecretStr("ghp_bad")), http)
    assert c.category == "SERVICE_UNAVAILABLE" and not c.connectivity_verified


async def test_producthunt_invalid_token_is_auth_failure_not_network(mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.post("https://api.producthunt.com/v2/api/graphql", status=401, body='{"error":"invalid_oauth_token"}')
    async with aiohttp.ClientSession() as http:
        c = await vi.check_producthunt(s(producthunt_token=SecretStr("tok-123456")), http)
    assert c.category == "AUTH_FAILED" and "Developer Token" in c.corrective_action and "tok-123456" not in c.detail


async def test_hf_token_rejected_but_reachable(mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get("https://huggingface.co/api/models?limit=1", payload=[])
    mocked.get("https://huggingface.co/api/whoami-v2", status=401, body="{}")
    async with aiohttp.ClientSession() as http:
        c = await vi.check_hf(s(hf_token=SecretStr("hf_bad")), http)
    assert c.category == "AUTH_FAILED" and c.connectivity_verified


async def test_gemini_retired_model_lists_alternatives_without_leaking_key(mocked) -> None:  # type: ignore[no-untyped-def]
    key = "AQ.secretsecretsecret"
    mocked.get(re.compile(r".*/v1beta/models/gemini-2\.0-flash$"), status=404, body='{"error":{"message":"no longer available"}}')
    mocked.get(re.compile(r".*/v1beta/models\?.*"), payload={"models": [{"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]}]})
    async with aiohttp.ClientSession() as http:
        c = await vi.check_gemini(s(gemini_api_key=SecretStr(key), gemini_model="gemini-2.0-flash"), http)
    assert c.result == "FAIL" and "gemini-3.8-flash" in c.corrective_action and key not in c.detail + c.corrective_action


async def test_one_crashing_check_does_not_hide_others(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise ValueError("synthetic crash")

    async def ok(*a, **k):  # type: ignore[no-untyped-def]
        return vi.Check("X", "-", True, True, "PASS", "fine")

    for f in ("check_hf", "check_gemini", "check_producthunt", "check_openai_compat"):
        monkeypatch.setattr(vi, f, ok)
    monkeypatch.setattr(vi, "check_github", boom)
    monkeypatch.setattr(vi, "check_google", ok)
    monkeypatch.setattr(vi, "check_stores", ok)
    monkeypatch.setattr(vi, "get_settings", lambda: s())
    checks = await vi.main()
    gh = [c for c in checks if c.integration == "GitHub API"][0]
    assert gh.category == "VERIFIER_ERROR" and sum(c.result == "PASS" and c.integration == "X" for c in checks) == 7


def test_rank_models_prefers_stable_newest_full_flash() -> None:
    models = [
        {"name": "models/gemini-3.8-flash-preview-09", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.8-flash-lite", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.5-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.8-flash-image", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.8-pro", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-9", "supportedGenerationMethods": ["embedContent"]},
    ]
    assert [m["id"] for m in sg.rank_models(models)] == ["gemini-3.8-flash", "gemini-3.8-flash-lite", "gemini-3.5-flash", "gemini-3.8-flash-preview-09"]


def test_update_env_line_only_touches_one_key() -> None:
    env = "GEMINI_API_KEY=AQ.secret\r\nGEMINI_MODEL=gemini-2.0-flash\r\nGROQ_API_KEY=\r\n# c\r\n"
    out = sg.update_env_line(env, "GEMINI_MODEL", "gemini-3.8-flash")
    assert out == "GEMINI_API_KEY=AQ.secret\r\nGEMINI_MODEL=gemini-3.8-flash\r\nGROQ_API_KEY=\r\n# c\r\n"
    assert sg.update_env_line("A=1\n", "GEMINI_MODEL", "m") == "A=1\nGEMINI_MODEL=m\n"


async def test_enrich_news_fills_organizations(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from src.config import settings as cfg
    from src.core.schemas import NewsRecord
    from src.database.repository import Repository
    from src.database.session import Database
    from src.pipelines import enrich
    from tests.conftest import NOW
    from tests.unit.test_llm_orchestrator import ScriptedProvider

    url = f"sqlite+aiosqlite:///{tmp_path}/n.db"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg.get_settings.cache_clear()
    db = Database(url)
    await db.create_all()
    rec = NewsRecord.model_validate({"source": {"name": "T", "url": "https://n.example/a"}, "provenance": {"extractionMethod": "HTML_PARSER"},
                                     "content": {"title": "OpenAI and Anthropic", "article_url": "https://n.example/a", "published_date": NOW,
                                                 "full_text": "OpenAI and Anthropic announced models."}})
    await Repository(db).bulk_upsert([rec])
    monkeypatch.setattr(enrich, "build_providers", lambda st: [ScriptedProvider("gemini", [
        {"fields": {"organizations": {"value": ["OpenAI", "Anthropic"], "evidence": "OpenAI and Anthropic announced"}}}])])
    monkeypatch.chdir(tmp_path)
    out = await enrich.run_enrich_news()
    assert out["records_updated"] == 1
    assert (await Repository(db).fetch_records("NEWS"))[0]["content"]["organizations"] == ["OpenAI", "Anthropic"]
    await db.dispose()
    cfg.get_settings.cache_clear()


@pytest.mark.parametrize("v", ["https://docs.google.com/spreadsheets/d/1AbC_dEf-123/edit#gid=0", "1AbC_dEf-123"])
def test_sheet_id_forms(v: str) -> None:
    assert s(google_sheet_id=v).google_sheet_id == "1AbC_dEf-123"
