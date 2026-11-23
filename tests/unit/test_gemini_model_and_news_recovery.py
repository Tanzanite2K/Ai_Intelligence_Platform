"""Gemini default model, provider wiring, error handling, and the targeted news-entity recovery.

All data here is SYNTHETIC (tmp databases, mocked HTTP); the production database and real APIs are never used.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from aioresponses import aioresponses
from pydantic import SecretStr

from src.config.settings import Settings
from src.core.schemas import NewsRecord, StartupRecord
from src.database.repository import Repository
from src.database.session import Database
from src.extraction.orchestrator import ExtractionFailed, LLMOrchestrator
from src.extraction.providers.vendors import GeminiProvider, build_providers
from src.extraction.tasks import NEWS_ENTITIES

ROOT = Path(__file__).resolve().parents[2]
CURRENT = "gemini-3.1-flash-lite"


def S(**kw) -> Settings:  # type: ignore[no-untyped-def]
    return Settings(_env_file=None, hf_org_cache_path=None, llm_backoff_base_s=0.001, llm_backoff_max_s=0.002, **kw)  # type: ignore[call-arg]


# ------------------------------------------------------------------ 1-3: configuration
def test_default_model_is_current_and_matches_env_example(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert S().gemini_model == CURRENT
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^GEMINI_MODEL=(.+)$", example, re.M).group(1).strip() == CURRENT  # type: ignore[union-attr]
    assert "gemini-2.0-flash" not in example


def test_env_variable_overrides_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    assert S().gemini_model == "gemini-3.5-flash-lite"


def test_provider_receives_configured_model_and_key_stays_out_of_url() -> None:
    s = S(gemini_api_key=SecretStr("AQ.test-key-never-in-url"), gemini_model=CURRENT)
    gem = next(p for p in build_providers(s) if p.name == "gemini")
    assert gem.model == CURRENT and gem.configured
    from src.extraction.providers.base import LLMRequest

    url, headers, _ = gem.build_request(LLMRequest(system="s", user="u", max_output_tokens=10))
    assert url.endswith(f"/models/{CURRENT}:generateContent")
    assert "test-key" not in url and headers["x-goog-api-key"] == "AQ.test-key-never-in-url"


# ------------------------------------------------------------------ 4-5: errors and structured response (mocked HTTP)
URL = re.compile(rf"https://generativelanguage\.googleapis\.com/v1beta/models/{re.escape(CURRENT)}:generateContent")
ARTICLE = "Baseten releases Bison-1\n\nBaseten has released bison-1, built on Google’s Gemma 4. Weights are on Hugging Face."
GOOD = {"fields": {"organizations": {"value": ["Baseten", "Google", "Hugging Face"],
                                     "evidence": "Baseten has released bison-1 | built on Google’s Gemma 4 | Weights are on Hugging Face"}}}


def envelope(obj: dict) -> dict:  # type: ignore[type-arg]
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(obj)}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20}}


def gemini(s: Settings) -> GeminiProvider:
    return GeminiProvider(api_key="k", model=s.gemini_model, input_token_budget=8000, rpm=6000)


async def test_unavailable_model_404_is_not_retried_and_fails_cleanly() -> None:
    s = S(gemini_model=CURRENT)
    body = {"error": {"code": 404, "message": f"models/{CURRENT} is not found", "status": "NOT_FOUND"}}
    with aioresponses() as m:
        m.post(URL, status=404, payload=body, repeat=True)
        orch = LLMOrchestrator([gemini(s)], s)
        with pytest.raises(ExtractionFailed, match="404"):
            await orch.extract(NEWS_ENTITIES, ARTICLE)
        await orch.close()
        assert sum(len(v) for v in m.requests.values()) == 1  # permanent error: exactly one request, no retry loop


async def test_transient_503_is_retried_then_structured_response_accepted() -> None:
    s = S(gemini_model=CURRENT)
    with aioresponses() as m:
        m.post(URL, status=503, payload={"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})
        m.post(URL, status=200, payload=envelope(GOOD))
        orch = LLMOrchestrator([gemini(s)], s)
        res = await orch.extract(NEWS_ENTITIES, ARTICLE)
        await orch.close()
    assert res.data["organizations"] == ["Baseten", "Google", "Hugging Face"]
    assert [a.outcome for a in res.attempts] == ["unavailable", "ok"]


async def test_comma_string_answer_is_repaired_not_accepted_as_one_item() -> None:
    s = S(gemini_model=CURRENT)
    bad = {"fields": {"organizations": {"value": "Baseten, Google", "evidence": GOOD["fields"]["organizations"]["evidence"]}}}
    with aioresponses() as m:
        m.post(URL, status=200, payload=envelope(bad))
        m.post(URL, status=200, payload=envelope(GOOD))
        orch = LLMOrchestrator([gemini(s)], s)
        res = await orch.extract(NEWS_ENTITIES, ARTICLE)
        await orch.close()
    assert res.data["organizations"] == ["Baseten", "Google", "Hugging Face"]
    assert [a.outcome for a in res.attempts] == ["malformed", "ok"]


# ------------------------------------------------------------------ 6: targeted recovery (fixture database)
def _load_recovery():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("recover_news_entities", ROOT / "scripts" / "recover_news_entities.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def news(url: str, title: str, text: str) -> NewsRecord:
    return NewsRecord.model_validate({
        "source": {"name": "TechCrunch", "url": url},
        "provenance": {"extractionMethod": "HTML_PARSER", "validationStatus": "VERIFIED", "evidence": {"published_date": "rss"}},
        "content": {"title": title, "article_url": url, "published_date": datetime(2026, 11, 20, 12, tzinfo=UTC),
                    "full_text": text, "organizations": []},
    })


def startup(name: str, url: str) -> StartupRecord:
    return StartupRecord.model_validate({
        "source": {"name": "Y Combinator", "url": url},
        "provenance": {"extractionMethod": "STRUCTURED_API", "validationStatus": "VERIFIED"},
        "content": {"entityName": name, "rawName": name, "website": f"https://{name.lower()}.example"},
    })


A_URL, B_URL, C_URL = "https://news.example/a", "https://news.example/b", "https://news.example/c"
TEXT_A = "Baseten has released bison-1, built on Google’s Gemma 4. Weights are on Hugging Face."


@pytest.fixture
async def fixture_db(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    path = tmp_path / "platform.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    db = Database(f"sqlite+aiosqlite:///{path}")
    await db.create_all()
    repo = Repository(db)
    await repo.bulk_upsert([startup("Baseten", "https://yc.example/baseten")])
    await repo.bulk_upsert([news(A_URL, "Baseten releases Bison-1", TEXT_A), news(B_URL, "Article B", "Some text about AI."),
                            news(C_URL, "Article C (never failed)", "Other text.")])
    for u in (A_URL, B_URL):
        await repo.dead_letter("llm.news_entities", u, {"title": "x"}, "gemini invalid request 404: models/gemini-2.0-flash is no longer available")
    await db.dispose()
    return path


async def test_recovery_tags_valid_article_keeps_failed_one_untouched(fixture_db, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from src.extraction.providers.base import InvalidRequestError
    from tests.unit.test_llm_orchestrator import ScriptedProvider

    rec = _load_recovery()
    s = S(database_url=f"sqlite+aiosqlite:///{fixture_db}")
    before = rec.snapshot(fixture_db)
    b_before = sqlite3.connect(fixture_db).execute("select payload, content_hash from records where source_url=?", (B_URL,)).fetchone()

    def script(req):  # type: ignore[no-untyped-def]
        if "Baseten releases" in req.user:
            return {"fields": {"organizations": {"value": ["Baseten", "Google", "OpenAI"],  # OpenAI: not in text -> dropped
                                                 "evidence": "Baseten has released bison-1 | built on Google’s Gemma 4 | OpenAI said"}}}
        return InvalidRequestError("gemini invalid request 404: model not found", provider="gemini", status=404)

    out = await rec.recover(s, apply=True, providers=[ScriptedProvider("gemini", script)], expected=2)
    by_url = {r["url"]: r for r in out["results"]}
    assert by_url[A_URL]["outcome"] == "tagged" and by_url[A_URL]["organizations"] == ["Baseten", "Google"]
    assert by_url[B_URL]["outcome"] == "failed" and "404" in by_url[B_URL]["reason"]
    assert out["summary"] == {"tagged": 1, "no_validated_organisations": 0, "failed": 1}
    assert out["resolver_replay"]["STARTUP"] == {"records": 1, "would_rename": 0}

    con = sqlite3.connect(fixture_db)
    a = json.loads(con.execute("select payload from records where source_url=?", (A_URL,)).fetchone()[0])
    assert a["content"]["organizations"] == ["Baseten", "Google"]
    assert a["provenance"]["evidence"]["organizations"] and a["provenance"]["llmProvider"] == "gemini"
    assert a["source"]["url"] == A_URL and a["content"]["title"] == "Baseten releases Bison-1"  # original kept
    assert con.execute("select payload, content_hash from records where source_url=?", (B_URL,)).fetchone() == b_before  # failed: untouched
    assert con.execute("select count(*) from dead_letters").fetchone()[0] == 2  # history preserved
    maps = con.execute("select original_name, canonical_name, source_url from entity_mappings order by id").fetchall()
    assert {(o, c) for o, c, u in maps if u == A_URL} == {("Baseten", "Baseten"), ("Google", "Google")}
    assert not [m for m in maps if m[2] in (B_URL, C_URL)]
    con.close()

    v = rec.verify(before, fixture_db)
    assert v["ok"], {k: x for k, x in v["checks"].items() if not x}
    assert v["after"]["news_tagged"] == 1 and v["after"]["news_untagged"] == 2


async def test_recovery_refuses_when_scope_differs_and_check_mode_never_writes(fixture_db) -> None:  # type: ignore[no-untyped-def]
    rec = _load_recovery()
    s = S(database_url=f"sqlite+aiosqlite:///{fixture_db}")
    h0 = rec.snapshot(fixture_db)
    with pytest.raises(rec.ScopeError, match="expected 16"):
        await rec.recover(s, apply=True, providers=[], expected=16)
    chk = await rec.recover(s, apply=False)
    assert chk["scope"]["targets"] == 2 and chk["scope"]["untagged_news_not_dead_lettered"] == [C_URL]
    h1 = rec.snapshot(fixture_db)
    assert {k: v for k, v in h0.items() if k != "taken_at"} == {k: v for k, v in h1.items() if k != "taken_at"}


async def test_verify_detects_unrelated_changes(fixture_db) -> None:  # type: ignore[no-untyped-def]
    rec = _load_recovery()
    before = rec.snapshot(fixture_db)
    con = sqlite3.connect(fixture_db)
    con.execute("update records set content_hash='tampered' where record_type='STARTUP'")
    con.execute("delete from dead_letters where id=1")
    con.commit()
    con.close()
    v = rec.verify(before, fixture_db)
    assert not v["ok"]
    assert v["checks"]["non_news_records_unchanged"] is False and v["checks"]["dead_letters_preserved"] is False
