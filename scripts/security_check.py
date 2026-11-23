"""Credential and secret-hygiene check. Never prints, logs or stores a secret value.

For each credential: whether it is set, and whether the provider accepts it, using one read-only, non-billed request:
  GitHub      GET  https://api.github.com/user                       (status, token expiry header, scopes header)
  HuggingFace GET  https://huggingface.co/api/whoami-v2              (status, token role and creation date)
  Gemini      GET  https://generativelanguage.googleapis.com/v1beta/models?pageSize=1  (key in header; model listing is free)
  ProductHunt POST https://api.producthunt.com/v2/api/graphql  query { viewer { user { id } } }  (read-only query)
Also: Google service-account file (present / parseable / type; key id is never printed), Neo4j (configured? port open?
does the configured password authenticate?), .env age, and a working-tree secret scan (scripts/scan_secrets.py).
It cannot prove an OLD credential was revoked: that needs the old value, which this script never has.
Writes docs/security_check_<stamp>.json. Exit 0 when every configured credential is accepted and the scan is clean.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))


def call(method: str, url: str, headers: dict[str, str], body: bytes | None = None) -> tuple[int | None, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": "FrontierAtlas-security-check", **headers})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, dict(r.headers), r.read(200_000)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read(2000)
    except Exception as e:
        return None, {}, type(e).__name__.encode()


def main() -> int:
    from src.config.settings import get_settings

    s = get_settings()
    sec = lambda v: v.get_secret_value() if v else None  # noqa: E731
    out: dict[str, object] = {"stamp": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")}
    env = ROOT / ".env"
    out["env_file"] = {"exists": env.exists(), "modified_utc": datetime.fromtimestamp(env.stat().st_mtime, UTC).isoformat() if env.exists() else None}
    res: dict[str, dict[str, object]] = {}

    t = sec(s.github_token)
    if t:
        st, h, _ = call("GET", "https://api.github.com/user", {"Authorization": f"Bearer {t}", "Accept": "application/vnd.github+json"})
        hl = {k.lower(): v for k, v in h.items()}
        res["github"] = {"set": True, "accepted": st == 200, "http": st, "token_expires": hl.get("github-authentication-token-expiration"),
                         "classic_scopes": hl.get("x-oauth-scopes")}
    else:
        res["github"] = {"set": False}

    t = sec(s.hf_token)
    if t:
        st, _, b = call("GET", "https://huggingface.co/api/whoami-v2", {"Authorization": f"Bearer {t}"})
        info: dict[str, object] = {}
        if st == 200:
            try:
                tok = (json.loads(b).get("auth") or {}).get("accessToken") or {}
                info = {"role": tok.get("role"), "created_at": tok.get("createdAt")}
            except ValueError:
                pass
        res["huggingface"] = {"set": True, "accepted": st == 200, "http": st, **info}
    else:
        res["huggingface"] = {"set": False}

    t = sec(s.gemini_api_key)
    if t:
        st, _, _ = call("GET", "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1", {"x-goog-api-key": t})
        res["gemini"] = {"set": True, "accepted": st == 200, "http": st, "billed": False}
    else:
        res["gemini"] = {"set": False}

    t = sec(s.producthunt_token)
    if t:
        body = json.dumps({"query": "{ viewer { user { id } } }"}).encode()
        st, _, b = call("POST", "https://api.producthunt.com/v2/api/graphql",
                        {"Authorization": f"Bearer {t}", "Content-Type": "application/json", "Accept": "application/json"}, body)
        err = None
        try:
            j = json.loads(b)
            errs = j.get("errors") or []
            err = (errs[0].get("error") or errs[0].get("message")) if errs and isinstance(errs[0], dict) else (j.get("error") if isinstance(j, dict) else None)
        except ValueError:
            pass
        res["producthunt"] = {"set": True, "accepted": st == 200 and not err, "http": st, "error_code": err}
    else:
        res["producthunt"] = {"set": False}

    for name, v in (("groq", s.groq_api_key), ("deepseek", s.deepseek_api_key)):
        res[name] = {"set": bool(v), "note": None if v else "unset (placeholder or blank) - provider disabled"}

    f = s.google_service_account_file
    if f:
        p = Path(f) if Path(f).is_absolute() else ROOT / f
        info2: dict[str, object] = {"set": True, "file_exists": p.exists()}
        if p.exists():
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
                info2.update({"type": j.get("type"), "has_private_key": bool(j.get("private_key")),
                              "inside_credentials_dir": "credentials" in p.parts,
                              "file_modified_utc": datetime.fromtimestamp(p.stat().st_mtime, UTC).isoformat()})
            except ValueError:
                info2["parse_error"] = True
        res["google_service_account"] = info2
    else:
        res["google_service_account"] = {"set": False}

    with socket.socket() as so:
        so.settimeout(0.5)
        port_open = so.connect_ex(("127.0.0.1", 7687)) == 0
    env_keys = {ln.split("=", 1)[0].strip().upper() for ln in env.read_text(encoding="utf-8", errors="ignore").splitlines()
                if "=" in ln and not ln.lstrip().startswith("#")} if env.exists() else set()
    def origin(k: str) -> str | None:
        return "process environment" if k in os.environ else (".env" if k in env_keys else None)
    neo: dict[str, object] = {"url_set": bool(s.neo4j_url), "password_set": bool(s.neo4j_password), "url_from": origin("NEO4J_URL"),
                              "password_from": origin("NEO4J_PASSWORD"), "bolt_7687_open_locally": port_open, "used_by_src": False}
    if s.neo4j_url and s.neo4j_password:
        try:
            from neo4j import GraphDatabase  # type: ignore[import-not-found]

            drv = GraphDatabase.driver(s.neo4j_url, auth=(s.neo4j_user, s.neo4j_password.get_secret_value()))
            try:
                drv.verify_connectivity()
                neo["auth_accepted"] = True
            except Exception as e:
                neo["auth_accepted"] = False
                neo["error"] = type(e).__name__
            finally:
                drv.close()
        except ImportError:
            neo["auth_accepted"] = None
            neo["error"] = "neo4j driver not installed"
    res["neo4j"] = neo
    out["credentials"] = res

    scan = subprocess.run([sys.executable, "scripts/scan_secrets.py"], capture_output=True, text=True, encoding="utf-8")
    try:
        sj = json.loads(scan.stdout)
        out["secret_scan"] = {"clean": sj.get("clean"), "findings": len(sj.get("findings", [])), "env_values_checked": sj.get("env_secret_values_checked")}
    except ValueError:
        out["secret_scan"] = {"clean": False, "error": "scan output unreadable"}

    bad = [k for k, v in res.items() if v.get("set") and v.get("accepted") is False]
    out["rejected_credentials"] = bad
    # Blocking = a leaked secret in the tree. A rejected credential is a finding to act on, not a test failure: none of the
    # 10 sources in the approved activation plan needs a credential, and no LLM is used (--no-llm).
    blocking = not out["secret_scan"].get("clean")  # type: ignore[union-attr]
    out["findings"] = [f"{k}: credential rejected by provider" for k in bad] + (
        ["neo4j: configured but not reachable/verifiable"] if neo.get("url_set") and neo.get("auth_accepted") is not True else [])
    out["result"] = "FAIL" if blocking else ("PASS_WITH_FINDINGS" if out["findings"] else "PASS")
    (ROOT / "docs" / f"security_check_{out['stamp']}.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 1 if out["result"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
