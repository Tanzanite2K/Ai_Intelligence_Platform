"""Phase 7 production-activation preflight. READ-ONLY on the production database.

It never opens data/platform.db for writing (every connection is `mode=ro`) and takes no write lock. It:
  1. confirms the exact production path and that no writer is active (ports 8000/3000 closed, no -journal/-wal/-shm,
     file unchanged across a 5 s window);
  2. records SHA-256, size, row counts and the Alembic revision;
  3. makes a byte-for-byte cold copy and an SQLite online backup OUTSIDE OneDrive (C:\\FrontierAtlas-backups\\activation-<stamp>);
  4. verifies both backups: SHA-256 of the cold copy, integrity_check and foreign_key_check on each, equal row counts;
  5. runs integrity_check + foreign_key_check on production (read-only);
  6. reports the selected sources' last run status, credential rejections and active cooldowns, and the history/API flags;
  7. writes docs/activation_preflight_<stamp>.json/.md. Production must hash the same at the end (checked).
Exit 0 = ready to ask for AUTHORIZE PRODUCTION WRITES, 1 = a check failed, 2 = a writer is active.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import sys
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LIVE = (ROOT / "data" / "platform.db").resolve()
BACKUP_ROOT = Path(r"C:\FrontierAtlas-backups") if os.name == "nt" else Path.home() / "FrontierAtlas-backups"
SELECTED = ["techcrunch_ai", "theverge_ai", "mit_tr_ai", "arstechnica_ai", "openai_news",
            "greenhouse", "ashby", "lever", "remoteok", "himalayas"]
EXCLUDED = {"marktechpost": "INACCESSIBLE in the 2026-11-23 rehearsal", "venturebeat_ai": "last run FAILED", "producthunt": "credential rejected", "the_decoder": "disabled"}


def sha(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def ro(p: Path) -> sqlite3.Connection:
    return sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True)


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def facts(p: Path) -> dict[str, Any]:
    with closing(ro(p)) as c:
        tables = [r[0] for r in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]
        counts = {t: c.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables}
        rev = c.execute("select version_num from alembic_version").fetchone()[0] if "alembic_version" in tables else None
        integ = c.execute("pragma integrity_check").fetchone()[0]
        fk = c.execute("pragma foreign_key_check").fetchall()
    return {"sha256": sha(p), "bytes": p.stat().st_size, "alembic_revision": rev, "integrity": integ,
            "foreign_key_violations": len(fk), "row_counts": counts}


def sources(p: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    now = datetime.now(UTC)
    with closing(ro(p)) as c:
        for s in SELECTED + list(EXCLUDED):
            run = c.execute("select status, finished_at, error from source_runs where source=? order by id desc limit 1", (s,)).fetchone()
            cp = c.execute("select cursor from crawl_checkpoints where source=?", (s,)).fetchone()
            cur: dict[str, Any] = {}
            if cp and cp[0]:
                try:
                    cur = json.loads(cp[0]) if isinstance(cp[0], str) else dict(cp[0])
                except (ValueError, TypeError):
                    cur = {}
            cool = cur.get("cooldown_until")
            active = False
            if cool:
                try:
                    t = datetime.fromisoformat(str(cool))
                    active = (t if t.tzinfo else t.replace(tzinfo=UTC)) > now
                except ValueError:
                    pass
            out[s] = {"selected": s in SELECTED, "excluded_reason": EXCLUDED.get(s), "last_status": run[0] if run else None,
                      "last_finished": run[1] if run else None, "credential_rejected": bool(cur.get("credential_rejected")),
                      "cooldown_until": cool, "cooldown_active": active}
    return out


def main() -> int:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    r: dict[str, Any] = {"stamp": stamp, "production_path": str(LIVE), "checks": {}, "production_writes": "none (read-only)"}
    ck = r["checks"]
    print(f"Activation preflight {stamp}\n  production: {LIVE}", flush=True)
    if not LIVE.exists():
        print("production database not found")
        return 1
    side = [x for x in ("-journal", "-wal", "-shm") if LIVE.with_name(LIVE.name + x).exists()]
    ports = {p: port_open(p) for p in (8000, 3000)}
    m0 = LIVE.stat().st_mtime_ns
    time.sleep(5)
    m1 = LIVE.stat().st_mtime_ns
    ck["no_writer"] = {"ok": not side and not any(ports.values()) and m0 == m1, "side_files": side, "ports_open": ports, "mtime_stable_5s": m0 == m1}
    print(f"  no writer: {ck['no_writer']}", flush=True)
    if not ck["no_writer"]["ok"]:
        print("STOP: stop the app (scripts\\stop_app.ps1) and retry.")
        write(r, stamp)
        return 2
    before = facts(LIVE)
    r["before"] = before
    print(f"  sha256 {before['sha256']}  bytes {before['bytes']}  rev {before['alembic_revision']}  integrity {before['integrity']}  fk {before['foreign_key_violations']}", flush=True)
    B = BACKUP_ROOT / f"activation-{stamp}"
    B.mkdir(parents=True, exist_ok=False)
    cold, online = B / "platform.pre-activation.cold.db", B / "platform.pre-activation.online.db"
    shutil.copy2(LIVE, cold)
    s = ro(LIVE)
    d = sqlite3.connect(online)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    fc, fo = facts(cold), facts(online)
    ck["backups"] = {"dir": str(B), "outside_onedrive": "onedrive" not in str(B).lower(),
                     "cold": {"path": str(cold), "sha256": fc["sha256"], "sha_matches_production": fc["sha256"] == before["sha256"],
                              "integrity": fc["integrity"], "fk": fc["foreign_key_violations"]},
                     "online": {"path": str(online), "sha256": fo["sha256"], "integrity": fo["integrity"], "fk": fo["foreign_key_violations"],
                                "row_counts_match": fo["row_counts"] == before["row_counts"], "revision_matches": fo["alembic_revision"] == before["alembic_revision"]}}
    b = ck["backups"]
    b["ok"] = (b["outside_onedrive"] and b["cold"]["sha_matches_production"] and fc["integrity"] == "ok" and fo["integrity"] == "ok"
               and b["online"]["row_counts_match"] and b["online"]["revision_matches"])
    (B / "SHA256SUMS.txt").write_text(f"{fc['sha256']}  {cold.name}\n{fo['sha256']}  {online.name}\n")
    print(f"  backups ok: {b['ok']}  -> {B}", flush=True)
    ck["production_integrity"] = {"ok": before["integrity"] == "ok" and before["foreign_key_violations"] == 0}
    try:
        from src.config.settings import get_settings
        st = get_settings()
        r["config"] = {"history_enabled": st.history_enabled, "api_read_only": st.api_read_only}
    except Exception as e:
        r["config"] = {"error": type(e).__name__}
    r["sources"] = sources(LIVE)
    bad = [k for k, v in r["sources"].items() if v["selected"] and (v["credential_rejected"] or v["cooldown_active"])]
    ck["selected_sources_usable"] = {"ok": not bad, "blocked": bad}
    after = sha(LIVE)
    ck["production_unchanged"] = {"ok": after == before["sha256"], "sha256_after": after}
    r["ready"] = all(v.get("ok") for v in ck.values())
    write(r, stamp)
    print(f"  selected sources usable: {ck['selected_sources_usable']}\n  production unchanged: {ck['production_unchanged']['ok']}\n  READY: {r['ready']}", flush=True)
    return 0 if r["ready"] else 1


def write(r: dict[str, Any], stamp: str) -> None:
    docs = ROOT / "docs"
    (docs / f"activation_preflight_{stamp}.json").write_text(json.dumps(r, indent=2, default=str), encoding="utf-8")
    lines = [f"# Activation preflight {stamp}", "", f"Production: `{r['production_path']}` — writes: {r['production_writes']}", ""]
    for k, v in r["checks"].items():
        lines.append(f"- **{k}**: {'PASS' if v.get('ok') else 'FAIL'} — `{json.dumps({x: y for x, y in v.items() if x != 'ok'}, default=str)[:300]}`")
    if "before" in r:
        bf = r["before"]
        lines += ["", f"SHA-256 `{bf['sha256']}`, {bf['bytes']:,} bytes, Alembic `{bf['alembic_revision']}`, integrity {bf['integrity']}, FK violations {bf['foreign_key_violations']}", "",
                  "| table | rows |", "|---|---|", *[f"| {t} | {n:,} |" for t, n in bf["row_counts"].items()]]
    lines += ["", f"Config: `{r.get('config')}`", "", f"READY: **{r.get('ready')}**"]
    (docs / f"activation_preflight_{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
