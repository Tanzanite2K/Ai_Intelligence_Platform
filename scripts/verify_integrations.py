"""Integration verification report — never prints secret values.

For every integration: credential configured? live connectivity/auth verified?
result, sanitised error, corrective action. Writes ``docs/integration_report.json``
and ``docs/integration_report.md``.

    python scripts/verify_integrations.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aiohttp

from src.config.settings import Settings, get_settings
from src.monitoring.logging import redact


@dataclass
class Check:
    integration: str
    required_by_pipeline: str
    credential_configured: bool
    connectivity_verified: bool
    result: str
    detail: str = ""
    corrective_action: str = ""
    category: str = ""

    def __post_init__(self) -> None:
        if not self.category:
            self.category = {
                "PASS": "AUTHENTICATED",
                "WARN": "REACHABLE_UNAUTHENTICATED" if self.connectivity_verified else "SERVICE_UNAVAILABLE",
                "SKIP": "MISSING_CREDENTIALS",
                "N/A": "NOT_REQUIRED",
            }.get(self.result, "AUTH_FAILED" if self.credential_configured else "SERVICE_UNAVAILABLE")


def _secret(v: Any) -> str | None:
    return v.get_secret_value() if v else None


NETWORK = 0  # pseudo-status: request never got an HTTP response (DNS, TLS, proxy, timeout)


async def _req(session: aiohttp.ClientSession, method: str, url: str, **kw: Any) -> tuple[int, str]:
    """Return (status, FULL body). Bodies are truncated only when displayed (_clean), never before parsing."""
    try:
        async with session.request(method, url, timeout=aiohttp.ClientTimeout(total=25), **kw) as r:
            return r.status, await r.text()
    except Exception as e:
        return NETWORK, f"{type(e).__name__}: {str(e)[:200]}"


def _json(body: str) -> Any:
    try:
        return json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None


def _net(name: str, used: str, configured: bool, body: str) -> Check:
    return Check(name, used, configured, False, "FAIL", f"network failure: {body[:160]}", "Check internet/proxy/firewall; this is not a credential problem",
                 "SERVICE_UNAVAILABLE")


def _clean(text: str, *secrets: str | None) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return redact(text)[:300]


async def check_github(s: Settings, http: aiohttp.ClientSession) -> Check:
    tok = _secret(s.github_token)
    h = {"Accept": "application/vnd.github+json"} | ({"Authorization": f"Bearer {tok}"} if tok else {})
    st, body = await _req(http, "GET", "https://api.github.com/rate_limit", headers=h)
    if st == NETWORK:
        return _net("GitHub API", "research stars (refresh-stars)", bool(tok), body)
    if st == 401:
        return Check("GitHub API", "research stars (refresh-stars)", True, True, "FAIL", "HTTP 401: token rejected (invalid, expired or revoked)",
                     "Create a new token at github.com/settings/tokens and update GITHUB_TOKEN", "AUTH_FAILED")
    data = _json(body)
    if st != 200 or not isinstance(data, dict) or "resources" not in data:
        return Check("GitHub API", "research stars (refresh-stars)", bool(tok), True, "FAIL", _clean(f"HTTP {st}: {body[:300]}", tok),
                     "Unexpected response from GitHub", "SERVICE_UNAVAILABLE")
    core = data["resources"]["core"]
    authed = core["limit"] > 60
    return Check("GitHub API", "research stars (refresh-stars)", bool(tok), True, "PASS" if authed else "WARN",
                 f"core limit {core['limit']}/h, remaining {core['remaining']}", "" if authed else "Token missing or invalid: unauthenticated 60/h")


async def check_hf(s: Settings, http: aiohttp.ClientSession) -> Check:
    tok = _secret(s.hf_token)
    used = "products, orgs, papers"
    st2, b2 = await _req(http, "GET", "https://huggingface.co/api/models?limit=1")  # anonymous reachability, independent of the token
    if st2 == NETWORK:
        return _net("Hugging Face Hub", used, bool(tok), b2)
    if not tok:
        return Check("Hugging Face Hub", used, False, st2 == 200, "WARN" if st2 == 200 else "FAIL", f"no HF_TOKEN; anonymous API HTTP {st2}",
                     "Optional: set HF_TOKEN for higher rate limits")
    st, body = await _req(http, "GET", "https://huggingface.co/api/whoami-v2", headers={"Authorization": f"Bearer {tok}"})
    if st == 200:
        return Check("Hugging Face Hub", used, True, True, "PASS", f"token valid (whoami-v2 HTTP 200); anonymous API HTTP {st2}")
    if st == 401:
        return Check("Hugging Face Hub", used, True, True, "FAIL", f"HF_TOKEN rejected (whoami-v2 HTTP 401); service reachable (anonymous HTTP {st2})",
                     "Create a new read token at huggingface.co/settings/tokens", "AUTH_FAILED")
    return Check("Hugging Face Hub", used, True, st2 == 200, "WARN" if st2 == 200 else "FAIL", _clean(f"whoami HTTP {st}; anonymous API HTTP {st2}", tok),
                 "HTTP 429 = rate limited (retry later); 5xx = Hugging Face outage")


async def check_gemini(s: Settings, http: aiohttp.ClientSession) -> Check:
    key = _secret(s.gemini_api_key)
    used = "LLM tier 1"
    if not key:
        return Check("Gemini", used, False, False, "SKIP", "GEMINI_API_KEY empty", "Create a key in Google AI Studio")
    h = {"x-goog-api-key": key}
    st, body = await _req(http, "GET", f"https://generativelanguage.googleapis.com/v1beta/models/{s.gemini_model}", headers=h)
    if st == NETWORK:
        return _net("Gemini", used, True, body)
    if st in (400, 401, 403) and ("API key not valid" in body or "API_KEY_INVALID" in body or "PERMISSION_DENIED" in body):
        return Check("Gemini", used, True, True, "FAIL", f"HTTP {st}: key rejected", "Create a new key in Google AI Studio", "AUTH_FAILED")

    async def flash_hint() -> str:
        st2, lst = await _req(http, "GET", "https://generativelanguage.googleapis.com/v1beta/models", headers=h, params={"pageSize": "1000"})
        if st2 != 200:
            return ""
        flash = [str(m.get("name", "")).split("/")[-1] for m in (_json(lst) or {}).get("models", [])
                 if isinstance(m, dict) and "flash" in str(m.get("name", "")) and "generateContent" in (m.get("supportedGenerationMethods") or [])]
        return f" Flash models listed for this key with generateContent: {flash[:8]}. Run scripts/select_gemini_model.py --apply."

    if st != 200:
        return Check("Gemini", used, True, True, "FAIL", _clean(f"model '{s.gemini_model}' lookup HTTP {st}: {body[:200]}", key),
                     "Model not available to this key." + await flash_hint(), "CONFIG_ERROR")
    from src.extraction.health import gemini_health_check

    hc = await gemini_health_check(s, s.gemini_model, key)  # SAME function + config as select_gemini_model.py
    Path("docs").mkdir(exist_ok=True)
    Path("docs/gemini_health.json").write_text(json.dumps(hc, indent=2), encoding="utf-8")
    att = ", ".join(a["outcome"] for a in hc["minimal_generation"].get("attempts", []))
    if hc["healthy"]:
        return Check("Gemini", used, True, True, "PASS",
                     f"model {s.gemini_model}: live minimal generation OK [{att}] AND production extraction OK "
                     f"({hc['extraction'].get('accepted')}, {hc['extraction'].get('status')}); api_calls={hc['api_calls']}")
    err = hc["minimal_generation"].get("error") or hc["extraction"].get("error") or hc.get("unexpected_error") or "extraction returned unexpected value"
    hint = {"SERVICE_UNAVAILABLE": "Transient Google capacity/quota problem (503/429/timeout) persisted through bounded retries; re-run later or "
                                   "run scripts/select_gemini_model.py --apply to test other listed models",
            "AUTH_FAILED": "Key rejected: create a new key in Google AI Studio",
            "CONFIG_ERROR": "Model rejects the request or returns unusable output." }.get(hc["category"], "")
    if hc["category"] == "CONFIG_ERROR":
        hint += await flash_hint()
    return Check("Gemini", used, True, True, "FAIL", f"model {s.gemini_model}: {_clean(err, key)[:220]} [attempts: {att}]; api_calls={hc['api_calls']}",
                 hint, hc["category"])


async def check_openai_compat(name: str, key: str | None, base: str, model: str, http: aiohttp.ClientSession, env: str) -> Check:
    if not key:
        return Check(name, f"LLM fallback ({name})", False, False, "SKIP", f"{env} empty", f"Optional: set {env} to enable this fallback tier")
    st, body = await _req(http, "GET", f"{base}/models", headers={"Authorization": f"Bearer {key}"})
    if st == NETWORK:
        return _net(name, f"LLM fallback ({name})", True, body)
    if st in (401, 403):
        return Check(name, f"LLM fallback ({name})", True, True, "FAIL", f"HTTP {st}: key rejected", f"Verify {env}", "AUTH_FAILED")
    if st != 200:
        return Check(name, f"LLM fallback ({name})", True, True, "FAIL", _clean(f"HTTP {st}: {body[:200]}", key), f"Verify {env}", "SERVICE_UNAVAILABLE")
    ids = {m.get("id") for m in (_json(body) or {}).get("data", [])}
    ok = model in ids
    return Check(name, f"LLM fallback ({name})", True, True, "PASS" if ok else "FAIL", f"model '{model}' {'listed' if ok else 'NOT listed'} ({len(ids)} models)",
                 "" if ok else f"Set a supported model id, e.g. one of {sorted(i for i in ids if i)[:5]}")


async def check_producthunt(s: Settings, http: aiohttp.ClientSession) -> Check:
    tok = _secret(s.producthunt_token)
    if not tok:
        return Check("Product Hunt API", "products", False, False, "SKIP", "PRODUCTHUNT_TOKEN empty", "Copy the Developer Token from your PH application")
    st, body = await _req(http, "POST", "https://api.producthunt.com/v2/api/graphql", headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                          json={"query": "{ posts(first: 1) { edges { node { id name } } } }"})
    if st == NETWORK:
        return _net("Product Hunt API", "products", True, body)
    data = _json(body) or {}
    if st == 200 and not data.get("errors"):
        n = len(((data.get("data") or {}).get("posts") or {}).get("edges", []))
        return Check("Product Hunt API", "products", True, True, "PASS", f"GraphQL HTTP 200, {n} post returned")
    if st == 401 or "invalid_oauth_token" in body or "unauthorized" in body.lower():
        code = data.get("error") or ((data.get("errors") or [{}])[0].get("error") if isinstance(data.get("errors"), list) else None)
        return Check("Product Hunt API", "products", True, True, "FAIL",
                     f"HTTP {st}: Product Hunt rejected PRODUCTHUNT_TOKEN (error code: {code or 'n/a'}); the API itself is reachable. "
                     "A valid Developer Token is required; no Product Hunt data can be collected until then",
                     "At api.producthunt.com/v2/oauth/applications open your app and copy the 'Developer Token' (not API Key / API Secret); "
                     "if it was regenerated, update PRODUCTHUNT_TOKEN", "AUTH_FAILED")
    return Check("Product Hunt API", "products", True, True, "FAIL", _clean(f"HTTP {st}: {body[:200]}", tok), "Product Hunt API error", "SERVICE_UNAVAILABLE")


async def check_google(s: Settings, http: aiohttp.ClientSession) -> list[Check]:
    path = s.google_service_account_file
    if not path:
        return [Check("Google service account", "Sheets export", False, False, "SKIP", "GOOGLE_SERVICE_ACCOUNT_FILE empty", "See README: Publishing the Google Sheet")]
    if not Path(path).exists():
        return [Check("Google service account", "Sheets export", True, False, "FAIL", f"file not found: {Path(path).name}", "Fix the path (include .json)")]
    try:
        info = json.loads(Path(path).read_text(encoding="utf-8"))
        assert info.get("type") == "service_account" and info.get("private_key") and info.get("client_email")
    except Exception as e:
        return [Check("Google service account", "Sheets export", True, False, "FAIL", f"invalid service-account JSON ({type(e).__name__})", "Download a new JSON key")]
    from src.exports.google_sheets import load_token

    try:
        token = await asyncio.to_thread(load_token, path)
    except Exception as e:
        return [Check("Google service account", "Sheets export", True, False, "FAIL", _clean(f"token exchange failed: {type(e).__name__}: {e}"),
                      "Key may be revoked/disabled; create a new key")]
    out = [Check("Google service account", "Sheets export", True, True, "PASS", f"OAuth token issued for {info['client_email'].split('@')[1]}")]
    h = {"Authorization": f"Bearer {token}"}
    sid = s.google_sheet_id
    st, body = await _req(http, "GET", f"https://sheets.googleapis.com/v4/spreadsheets/{sid or 'verification-probe-nonexistent'}", headers=h, params={"fields": "properties.title"})
    if "SERVICE_DISABLED" in body or "has not been used" in body:
        out.append(Check("Google Sheets API", "Sheets export", True, False, "FAIL", "API disabled for the service account's project",
                         f"Enable it: console.cloud.google.com/apis/library/sheets.googleapis.com?project={info.get('project_id')}"))
    elif sid and st == 200:
        title = ((_json(body) or {}).get("properties") or {}).get("title", "?")
        st3, b3 = await _req(http, "GET", f"https://www.googleapis.com/drive/v3/files/{sid}", headers=h,
                             params={"fields": "capabilities(canEdit),permissions(type,role)", "supportsAllDrives": "true"})
        meta = _json(b3) or {}
        can_edit = (meta.get("capabilities") or {}).get("canEdit")
        public = any(p.get("type") == "anyone" for p in meta.get("permissions") or [])
        if st3 == 200 and can_edit and public:
            out.append(Check("Google Sheets API", "Sheets export", True, True, "WARN",
                             f"spreadsheet '{title}': service account has Editor (canEdit=true) BUT anyone-with-link access is ON",
                             "A private export will refuse to write. In Google Sheets: Share -> General access -> Restricted", "POLICY_BLOCK"))
        elif st3 == 200 and can_edit:
            out.append(Check("Google Sheets API", "Sheets export", True, True, "PASS",
                             f"spreadsheet '{title}' readable; service account has Editor (canEdit=true); anyone-with-link access: off"))
        elif st3 == 200:
            out.append(Check("Google Sheets API", "Sheets export", True, True, "FAIL", f"spreadsheet '{title}' is readable but the service account is NOT an editor",
                             f"Share the sheet with {info['client_email']} as Editor", "AUTH_FAILED"))
        else:
            out.append(Check("Google Sheets API", "Sheets export", True, True, "WARN", f"spreadsheet '{title}' readable; edit right not confirmed (Drive HTTP {st3})",
                             "Enable the Google Drive API to confirm Editor rights"))
    elif sid:
        out.append(Check("Google Sheets API", "Sheets export", True, False, "FAIL", f"HTTP {st} for GOOGLE_SHEET_ID",
                         f"Share the spreadsheet with {info['client_email']} as Editor"))
    else:
        out.append(Check("Google Sheets API", "Sheets export", True, st == 404, "WARN" if st == 404 else "FAIL", f"API enabled (probe HTTP {st}); GOOGLE_SHEET_ID empty",
                         "Recommended: create an empty Google Sheet in YOUR Drive, share it with the service-account e-mail as Editor, set GOOGLE_SHEET_ID "
                         "(service accounts created after 2024 have no Drive storage, so they often cannot create files themselves)"))
    st, body = await _req(http, "GET", "https://www.googleapis.com/drive/v3/about", headers=h, params={"fields": "user(emailAddress),storageQuota"})
    if st == 200:
        q = (_json(body) or {}).get("storageQuota", {})
        out.append(Check("Google Drive API", "public sharing", True, True, "PASS", f"enabled; SA storage limit={q.get('limit', 'n/a')}"))
    else:
        out.append(Check("Google Drive API", "public sharing", True, False, "FAIL", _clean(f"HTTP {st}: {body}"),
                         f"Enable it: console.cloud.google.com/apis/library/drive.googleapis.com?project={info.get('project_id')}"))
    return out


async def check_stores(s: Settings) -> list[Check]:
    out: list[Check] = []
    from src.database.session import Database

    db = Database(s.database_url)
    try:
        await db.create_all()
        from src.database.repository import Repository

        counts = await Repository(db).count_by_type()
        out.append(Check(f"Primary DB ({db.dialect})", "all persistence", True, True, "PASS", f"connected; records {counts}"))
    except Exception as e:
        out.append(Check(f"Primary DB ({db.dialect})", "all persistence", True, False, "FAIL", _clean(str(e)), "Check DATABASE_URL / start PostgreSQL"))
    finally:
        await db.dispose()
    if s.redis_url:
        try:
            import redis.asyncio as aioredis

            r = aioredis.from_url(s.redis_url)
            await r.ping()
            await r.aclose()
            out.append(Check("Redis", "distributed dedup, Celery", True, True, "PASS", "PING ok"))
        except Exception as e:
            out.append(Check("Redis", "distributed dedup, Celery", True, False, "FAIL", _clean(str(e)), "Start Redis or unset REDIS_URL for single-node mode"))
    else:
        out.append(Check("Redis", "distributed dedup, Celery (multi-node only)", False, False, "SKIP", "REDIS_URL unset -> in-process dedup (single node)",
                         "Only needed for multi-worker deployments (docker compose)"))
    from urllib.parse import urlsplit

    for name, url in (("Qdrant", s.qdrant_url), ("Neo4j", s.neo4j_url)):
        reach = "not configured"
        if url:
            u = urlsplit(url)
            try:
                _, w = await asyncio.wait_for(asyncio.open_connection(u.hostname, u.port or (6333 if name == "Qdrant" else 7687)), 4)
                w.close()
                reach = f"TCP reachable at {u.hostname}:{u.port}"
            except Exception as e:
                reach = f"not reachable at {u.hostname}:{u.port} ({type(e).__name__}); hostname is the docker-compose service name"
        out.append(Check(name, "optional (documented projection; not used by the ingestion pipeline)", bool(url), reach.startswith("TCP"), "N/A",
                         reach, "Not required to run or evaluate the pipeline; start with: docker compose --profile graph up -d", "NOT_REQUIRED"))
    return out


async def main() -> list[Check]:
    s = get_settings()
    checks: list[Check] = [
        Check(".env loading", "configuration", Path(".env").exists(), True,
              "PASS" if Path(".env").exists() else "WARN",
              f"loaded; blank values treated as unset: {sorted(k for k in ('groq_api_key', 'deepseek_api_key', 'producthunt_token', 'github_token', 'hf_token', 'gemini_api_key', 'google_sheet_id') if getattr(s, k) is None)}"),
    ]
    names = ["GitHub API", "Hugging Face Hub", "Gemini", "Groq", "DeepSeek", "Product Hunt API", "Google service account", "Primary DB / stores"]
    async with aiohttp.ClientSession(trust_env=True) as http:
        res = await asyncio.gather(
            check_github(s, http), check_hf(s, http), check_gemini(s, http),
            check_openai_compat("Groq", _secret(s.groq_api_key), "https://api.groq.com/openai/v1", s.groq_model, http, "GROQ_API_KEY"),
            check_openai_compat("DeepSeek", _secret(s.deepseek_api_key), "https://api.deepseek.com", s.deepseek_model, http, "DEEPSEEK_API_KEY"),
            check_producthunt(s, http), check_google(s, http), check_stores(s),
            return_exceptions=True,  # one failing check never hides the others
        )
    secrets = [_secret(x) for x in (s.github_token, s.hf_token, s.gemini_api_key, s.groq_api_key, s.deepseek_api_key, s.producthunt_token)]
    for name, r in zip(names, res, strict=True):
        if isinstance(r, BaseException):
            checks.append(Check(name, "-", True, False, "FAIL", _clean(f"verifier error {type(r).__name__}: {r}", *secrets),
                                "Bug in the verifier for this check; other checks are unaffected", "VERIFIER_ERROR"))
        else:
            checks.extend(r if isinstance(r, list) else [r])
    return checks


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parents[1])
    checks = asyncio.run(main())
    Path("docs").mkdir(exist_ok=True)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    Path("docs/integration_report.json").write_text(json.dumps({"checked_at": now, "checks": [asdict(c) for c in checks]}, indent=2), encoding="utf-8")
    lines = [f"# Integration verification ({now})", "", "All results below come from live requests made by scripts/verify_integrations.py.", "",
             "| Integration | Used for | Credential | Connectivity | Result | Category | Detail | Action |", "|---|---|---|---|---|---|---|---|"]
    for c in checks:
        lines.append(f"| {c.integration} | {c.required_by_pipeline} | {'yes' if c.credential_configured else 'no'} | {'yes' if c.connectivity_verified else 'no'} | "
                     f"**{c.result}** | {c.category} | {c.detail.replace('|', '/')} | {c.corrective_action.replace('|', '/')} |")
    Path("docs/integration_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for c in checks:
        print(f"[{c.result:5}] {c.category:26} {c.integration:28} {c.detail[:100]}" + (f"\n        -> {c.corrective_action}" if c.corrective_action and c.result != "PASS" else ""))
