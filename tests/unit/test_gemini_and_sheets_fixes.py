"""Regression tests for the RUN_VERIFY findings (all HTTP mocked; NOT live verification)."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

from src.config.settings import Settings
from src.exports.google_sheets import GoogleSheetsExporter, SheetsError, required_grid
from src.exports.tabs import Tab
from src.extraction.providers.base import IncompleteResponseError, LLMRequest
from src.extraction.providers.vendors import GeminiProvider

ROOT = Path(__file__).resolve().parents[2]
GEN = re.compile(r"https://generativelanguage\.googleapis\.com/v1beta/models/[^/?]+:generateContent")
LIST = re.compile(r"https://generativelanguage\.googleapis\.com/v1beta/models\?.*")
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
EXTRACT_OK = {"fields": {"pricing_model": {"value": "FREEMIUM", "evidence": "free to start; upgrade to the Pro plan for $20/month"}}}


def _load(name: str):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


sg = _load("select_gemini_model")


def gen_payload(text: str, finish: str = "STOP", thoughts: int = 0, thought_part: bool = False) -> dict:
    parts = ([{"text": "internal reasoning...", "thought": True}] if thought_part else []) + [{"text": text}]
    return {"candidates": [{"finishReason": finish, "content": {"parts": parts}}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "thoughtsTokenCount": thoughts}}


def settings(**kw) -> Settings:  # type: ignore[no-untyped-def]
    return Settings(_env_file=None, database_url="sqlite+aiosqlite://", llm_backoff_base_s=0.001, **kw)  # type: ignore[call-arg]


# ------------------------------------------------------------------ model list
def test_rank_models_skips_missing_fields_and_no_generate_content() -> None:
    rejected: list[dict[str, str]] = []
    models = [
        {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent", "countTokens"], "inputTokenLimit": 1048576},
        {"name": "models/gemini-3.7-flash"},                                                  # no methods field -> never assumed
        {"name": "models/gemini-3.6-flash", "supportedGenerationMethods": ["countTokens"]},   # no generateContent
        {"supportedGenerationMethods": ["generateContent"]},                                  # no name
        {"name": None, "supportedGenerationMethods": ["generateContent"]},
        "garbage-entry",
        {"name": "models/gemini-3.8-flash-tts", "supportedGenerationMethods": ["generateContent"]},
    ]
    ranked = sg.rank_models(models, rejected)  # type: ignore[arg-type]
    assert [m["id"] for m in ranked] == ["gemini-3.8-flash"]
    reasons = {r["model"]: r["reason"] for r in rejected}
    assert reasons["gemini-3.7-flash"] == "supportedGenerationMethods missing"
    assert reasons["gemini-3.6-flash"] == "generateContent not supported"
    assert reasons["gemini-3.8-flash-tts"] == "non-text variant"


# ------------------------------------------------------------ provider details
async def test_gemini_ignores_thought_parts_and_sends_thinking_budget(mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.post(GEN, payload=gen_payload('{"ok": true}', thought_part=True))
    p = GeminiProvider(api_key="k", model="gemini-3.8-flash", input_token_budget=8000, rpm=6000, thinking_budget=0)
    r = await p.complete(LLMRequest("s", "u", max_output_tokens=256))
    assert r.text == '{"ok": true}'
    sent = next(iter(mocked.requests.values()))[0].kwargs["json"]["generationConfig"]
    assert sent["thinkingConfig"] == {"thinkingBudget": 0} and sent["maxOutputTokens"] == 256
    await p.close()


async def test_gemini_drops_thinking_config_when_rejected(mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.post(GEN, status=400, body='{"error":{"message":"thinkingBudget is not supported for this model"}}')
    mocked.post(GEN, payload=gen_payload('{"ok": true}'))
    p = GeminiProvider(api_key="k", model="m", input_token_budget=8000, rpm=6000, thinking_budget=0)
    assert (await p.complete(LLMRequest("s", "u", max_output_tokens=256))).text == '{"ok": true}'
    second = [c for v in mocked.requests.values() for c in v][1].kwargs["json"]["generationConfig"]
    assert "thinkingConfig" not in second and second["maxOutputTokens"] >= GeminiProvider.MIN_OUTPUT_WITH_THINKING
    await p.close()


async def test_gemini_max_tokens_from_thinking_is_reported(mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.post(GEN, payload=gen_payload("", finish="MAX_TOKENS", thoughts=50, thought_part=True))
    p = GeminiProvider(api_key="k", model="m", input_token_budget=8000, rpm=6000, thinking_budget=0)
    with pytest.raises(IncompleteResponseError, match="thoughts=50"):
        await p.complete(LLMRequest("s", "u", max_output_tokens=50))
    assert p.thinking_budget is None  # budget ignored by the model -> next request reserves headroom
    await p.close()


# --------------------------------------------------------------- model testing
async def test_test_model_failed_generation_is_recorded_not_raised(mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.post(GEN, status=404, body='{"error":{"code":404,"message":"This model is no longer available"}}', repeat=True)
    key = "AQ.not-a-real-key-123"
    res = await sg.test_model(settings(), key, "gemini-old-flash", {})
    assert res["compatible"] is False and "404" in res["minimal_generation"]["error"]
    assert key not in json.dumps(res)


async def test_test_model_success_generation_and_extraction(mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.post(GEN, payload=gen_payload('{"ok": true}'))
    mocked.post(GEN, payload=gen_payload(json.dumps(EXTRACT_OK)))
    res = await sg.test_model(settings(), "k", "gemini-3.8-flash", {"inputTokenLimit": 1048576})
    assert res["minimal_generation"]["ok"] and res["extraction"]["ok"] and res["compatible"] is True
    assert res["extraction"]["accepted"] == {"pricing_model": "FREEMIUM"}


async def test_selector_updates_only_gemini_model_after_live_success(mocked, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    env = tmp_path / ".env"
    original = "GEMINI_API_KEY=AQ.keep-me\r\nGEMINI_MODEL=gemini-2.0-flash\r\nGITHUB_TOKEN=keep\r\n"
    env.write_bytes(original.encode())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sg, "get_settings", lambda: settings(gemini_api_key=SecretStr("AQ.keep-me"), gemini_model="gemini-2.0-flash"))
    mocked.get(LIST, payload={"models": [{"name": "models/gemini-2.0-flash", "supportedGenerationMethods": ["generateContent"]},
                                         {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]}]})
    mocked.post(re.compile(r".*/models/gemini-3\.8-flash:generateContent"), payload=gen_payload('{"ok": true}'))
    mocked.post(re.compile(r".*/models/gemini-3\.8-flash:generateContent"), payload=gen_payload(json.dumps(EXTRACT_OK)))
    out = await sg.main(apply=True)
    assert out["selected_model"] == "gemini-3.8-flash" and out["applied"] is True
    assert env.read_bytes().decode() == original.replace("GEMINI_MODEL=gemini-2.0-flash", "GEMINI_MODEL=gemini-3.8-flash")


async def test_selector_no_working_model_leaves_env_untouched(mocked, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    env = tmp_path / ".env"
    env.write_text("GEMINI_MODEL=gemini-2.0-flash\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sg, "get_settings", lambda: settings(gemini_api_key=SecretStr("k"), gemini_model="gemini-2.0-flash"))
    mocked.get(LIST, payload={"models": [{"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]}]})
    mocked.post(GEN, status=429, body='{"error":{"status":"RESOURCE_EXHAUSTED"}}', repeat=True)
    out = await sg.main(apply=True)
    assert out["selected_model"] is None and out["applied"] is False and "error" in out
    assert env.read_text() == "GEMINI_MODEL=gemini-2.0-flash\n"


async def test_selector_models_list_error_reported(mocked, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sg, "get_settings", lambda: settings(gemini_api_key=SecretStr("secret-key-xyz"), gemini_model="m"))
    mocked.get(LIST, status=403, body='{"error":{"message":"API key secret-key-xyz has no access"}}')
    out = await sg.main(apply=True)
    assert out["selected_model"] is None and "403" in out["error"] and "secret-key-xyz" not in out["error"]


# ------------------------------------------------------------------ Sheets
SIX = ["Startups", "Products", "Research Papers", "Jobs", "News", "Entity Mapping Log"]
EXPECTED = {"Startups": 2509, "Products": 1800, "Research Papers": 2290, "Jobs": 92, "News": 16, "Entity Mapping Log": 4892}


def six_tabs() -> list[Tab]:
    return [Tab(t, ["c1", "c2"], [[i, f"https://x.example/{i}"] for i in range(n)]) for t, n in EXPECTED.items()]


def test_required_grid() -> None:
    assert required_grid(Tab("S", ["a", "b", "c"], [[1, 2, 3]] * 2509)) == (2510, 3)
    assert required_grid(Tab("N", ["a"], [])) == (1, 1)


async def test_grid_expanded_before_writing_beyond_2000_rows(mocked) -> None:  # type: ignore[no-untyped-def]
    tabs = six_tabs()
    props = {"sheets": [{"properties": {"sheetId": i, "title": t, "gridProperties": {"rowCount": 2000, "columnCount": 26}}} for i, t in enumerate(SIX)]}
    mocked.get(re.compile(r"https://www\.googleapis\.com/drive/v3/files/SID/permissions.*"), payload={"permissions": [{"type": "user", "role": "owner"}]})
    mocked.get(re.compile(rf"{SHEETS}/SID\?.*"), payload=props, repeat=True)
    mocked.post(f"{SHEETS}/SID:batchUpdate", payload={}, repeat=True)
    mocked.post(re.compile(rf"{SHEETS}/SID/values/.*:clear"), payload={}, repeat=True)
    mocked.post(f"{SHEETS}/SID/values:batchUpdate", payload={}, repeat=True)
    ex = GoogleSheetsExporter("tok", batch_rows=2000)
    await ex.export(tabs, spreadsheet_id="SID", title="T")
    grow = [c.kwargs["json"] for k, v in mocked.requests.items() if str(k[1]).endswith("SID:batchUpdate") for c in v]
    upd = {r["updateSheetProperties"]["properties"]["sheetId"]: r["updateSheetProperties"]["properties"]["gridProperties"]
           for body in grow for r in body["requests"] if "updateSheetProperties" in r}
    assert upd == {0: {"rowCount": 2510, "columnCount": 26}, 2: {"rowCount": 2291, "columnCount": 26}, 5: {"rowCount": 4893, "columnCount": 26}}
    assert ex.grid_changes["Startups"] == {"rows_before": 2000, "rows_after": 2510, "cols_before": 26, "cols_after": 26}
    # the grid change is sent BEFORE any value write
    order = [str(k[1]) for k in mocked.requests]
    assert order.index(f"{SHEETS}/SID:batchUpdate") < order.index(f"{SHEETS}/SID/values:batchUpdate")


async def test_readback_six_tabs_counts_headers_private(mocked) -> None:  # type: ignore[no-untyped-def]
    tabs = six_tabs()
    mocked.get(re.compile(rf"{SHEETS}/SID\?.*fields=sheets.properties.title.*"), payload={"sheets": [{"properties": {"title": t}} for t in SIX]})
    cols = {"valueRanges": [{"values": [["c1"] + [str(i) for i in range(EXPECTED[t])]]} for t in SIX]}
    mocked.get(re.compile(rf"{SHEETS}/SID/values:batchGet\?.*majorDimension=COLUMNS.*"), payload=cols)
    mocked.get(re.compile(rf"{SHEETS}/SID/values:batchGet\?(?!.*majorDimension).*"), payload={"valueRanges": [{"values": [["c1", "c2"]]} for _ in SIX]})
    mocked.get(re.compile(r"https://www\.googleapis\.com/drive/v3/files/SID/permissions.*"), payload={"permissions": [{"type": "user", "role": "owner"}]})
    v = await GoogleSheetsExporter("tok").verify("SID", tabs)
    assert v["tabs"] == SIX and v["exactly_six_expected_tabs"] and v["rows_match"] and v["data_rows"] == EXPECTED
    assert all(v["headers_match"].values()) and v["public_link_reader"] is False


def test_export_failure_is_reported_as_failure(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A Sheets error (e.g. grid limits) must make `export --sheets` exit non-zero, never 'OK'."""
    import src.exports.google_sheets as gs
    import src.main as m
    from src.config import settings as cfg

    sa = tmp_path / "sa.json"
    sa.write_text("{}")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/x.db")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", str(sa))
    monkeypatch.setenv("GOOGLE_SHEET_ID", "SID")
    monkeypatch.setenv("EXPORT_DIR", str(tmp_path / "exp"))
    monkeypatch.chdir(tmp_path)
    cfg.get_settings.cache_clear()
    monkeypatch.setattr(gs, "load_token", lambda p: "tok")

    async def boom(self, *a, **k):  # type: ignore[no-untyped-def]
        raise SheetsError("Range (Startups!A2001) exceeds grid limits. Max rows: 2000")

    monkeypatch.setattr(gs.GoogleSheetsExporter, "export", boom)
    assert m.main(["export", "--sheets"]) == 3
    rep = json.loads((tmp_path / "docs" / "sheets_export.json").read_text())
    assert rep["sheets_ok"] is False and "grid limits" in rep["sheets_error"]
    cfg.get_settings.cache_clear()


def test_public_sheet_fails_private_export(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import src.exports.google_sheets as gs
    import src.main as m
    from src.config import settings as cfg

    sa = tmp_path / "sa.json"
    sa.write_text("{}")
    for k, v in {"DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path}/x.db", "GOOGLE_SERVICE_ACCOUNT_FILE": str(sa), "GOOGLE_SHEET_ID": "SID",
                 "EXPORT_DIR": str(tmp_path / "exp")}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)
    cfg.get_settings.cache_clear()
    monkeypatch.setattr(gs, "load_token", lambda p: "tok")

    async def fake_export(self, tabs, **k):  # type: ignore[no-untyped-def]
        self.last_spreadsheet_id = "SID"
        return "https://docs.google.com/spreadsheets/d/SID"

    async def fake_verify(self, sid, tabs):  # type: ignore[no-untyped-def]
        return {"tabs": [t.title for t in tabs], "exactly_six_expected_tabs": True, "data_rows": {}, "expected_rows": {}, "rows_match": True,
                "headers_match": {t.title: True for t in tabs}, "public_link_reader": True}

    monkeypatch.setattr(gs.GoogleSheetsExporter, "export", fake_export)
    monkeypatch.setattr(gs.GoogleSheetsExporter, "verify", fake_verify)
    assert m.main(["export", "--sheets"]) == 3
    assert "anyone-with-link" in json.loads((tmp_path / "docs" / "sheets_export.json").read_text())["sheets_error"]
    cfg.get_settings.cache_clear()


async def test_private_export_refuses_to_write_into_public_sheet(mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.get(re.compile(r"https://www\.googleapis\.com/drive/v3/files/SID/permissions.*"), payload={"permissions": [{"type": "anyone", "role": "reader"}]})
    with pytest.raises(SheetsError, match="Restricted"):
        await GoogleSheetsExporter("tok").export(six_tabs(), spreadsheet_id="SID", title="T", share_public=False)
    assert not any("values" in str(k[1]) for k in mocked.requests)  # nothing written
