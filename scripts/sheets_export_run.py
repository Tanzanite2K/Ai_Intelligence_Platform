"""Authorized Google Sheets export (user authorization 2026-11-23 14:34 IST), with evidence.

1. Production SHA-256 before.  2. Expected tabs computed from the database with the exporter's own build_tabs (same 24 h
freshness rule for Jobs/News); duplicate check per tab on its record URL column.  3. `python -m src.main export --sheets`
(the exporter authenticates with the service account, clears + rewrites the 6 tabs, then reads the sheet back and compares
tabs, headers and row counts).  4. Production SHA-256 after (must be unchanged).  Never prints credentials.
Report: docs/sheets_export_run_<stamp>.json
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
LIVE = ROOT / "data" / "platform.db"
KEY_COL = {"Startups": "source.url", "Products": "source.url", "Research Papers": "content.paper_url", "Jobs": "content.job_url",
           "News": "content.article_url"}


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def log(m: str) -> None:
    print(f"[{datetime.now(UTC).strftime('%H:%M:%S')}Z] {m}", flush=True)


async def expected() -> dict[str, dict[str, object]]:
    from src.config.settings import get_settings
    from src.database.repository import Repository
    from src.database.session import Database
    from src.exports.tabs import build_tabs

    s = get_settings()
    db = Database(s.database_url.replace("sqlite+aiosqlite:///", "sqlite+aiosqlite:///file:", 1) + "?mode=ro&uri=true")
    try:
        tabs = await build_tabs(Repository(db), window_hours=s.freshness_window_hours)
    finally:
        await db.dispose()
    out: dict[str, dict[str, object]] = {}
    for t in tabs:
        info: dict[str, object] = {"rows": len(t.rows)}
        k = KEY_COL.get(t.title)
        if k and k in t.columns:
            i = t.columns.index(k)
            keys = [r[i] for r in t.rows]
            info.update({"key": k, "duplicate_keys": len(keys) - len(set(keys)), "empty_keys": sum(1 for x in keys if not x)})
        out[t.title] = info
    return out


def main() -> int:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rep: dict[str, object] = {"stamp": stamp, "authorized": "user, 2026-11-23 14:34 IST"}
    h0 = sha(LIVE)
    rep["production_sha256_before"] = h0
    log(f"production SHA-256 before: {h0}")
    from src.config.settings import get_settings

    s = get_settings()
    sa = Path(s.google_service_account_file or "")
    rep["service_account_file_present"] = bool(s.google_service_account_file) and (sa if sa.is_absolute() else ROOT / sa).exists()
    rep["sheet_id_configured"] = bool(s.google_sheet_id)
    log(f"service-account file present: {rep['service_account_file_present']}; sheet id configured: {rep['sheet_id_configured']}")
    exp = asyncio.run(expected())
    rep["expected_from_database"] = exp
    log("expected rows (database, export-time freshness rule): " + json.dumps({k: v["rows"] for k, v in exp.items()}))
    log("duplicates/empty keys: " + json.dumps({k: [v.get("duplicate_keys"), v.get("empty_keys")] for k, v in exp.items() if "key" in v}))
    import src.main as cli

    log("$ python -m src.main export --sheets")
    rc = cli.main(["export", "--sheets"])
    rep["export_exit"] = rc
    res = json.loads((ROOT / "docs" / "sheets_export.json").read_text(encoding="utf-8"))
    rep["export_result"] = res
    h1 = sha(LIVE)
    rep["production_sha256_after"] = h1
    v = res.get("sheets_verification") or {}
    rows_vs_db = {k: (v.get("data_rows", {}).get(k), exp[k]["rows"]) for k in exp}
    rep["sheet_rows_vs_database"] = rows_vs_db
    ok = (rc == 0 and res.get("sheets_ok") is True and v.get("rows_match") is True and v.get("exactly_six_expected_tabs") is True
          and all(v.get("headers_match", {}).values()) and not v.get("public_link_reader") and h0 == h1
          and all(a == b for a, b in rows_vs_db.values())
          and all(x.get("duplicate_keys", 0) == 0 and x.get("empty_keys", 0) == 0 for x in exp.values()))
    rep["result"] = "PASS" if ok else "CHECK"
    (ROOT / "docs" / f"sheets_export_run_{stamp}.json").write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    log(f"sheet rows vs database: {json.dumps(rows_vs_db)}")
    log(f"sheets_ok={res.get('sheets_ok')} rows_match={v.get('rows_match')} headers_ok={all(v.get('headers_match', {}).values())} "
        f"public_link={v.get('public_link_reader')} url={res.get('sheet_url')}")
    log(f"production SHA-256 after: {h1} (unchanged: {h0 == h1})")
    log(f"RESULT: {rep['result']}  report docs/sheets_export_run_{stamp}.json")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
