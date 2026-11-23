"""Database safety check. READ-ONLY on production; all writes go to a disposable copy in C:\\FrontierAtlas-test.

1. Production status: SHA-256, size, Alembic revision, integrity_check, foreign_key_check, row counts, and whether it still
   equals the expected post-7B hash (i.e. nothing changed it unexpectedly).
2. Latest activation backup (C:\\FrontierAtlas-backups\\activation-*): outside OneDrive; SHA256SUMS.txt matches both files;
   cold copy hash == production hash; integrity + FK checks on both copies; online copy row counts == production.
3. Rollback rehearsal: copy the online backup to a disposable target, change it with the real `sync-evidence` command
   (offline; HISTORY_ENABLED/DATABASE_URL set for that subprocess only), then restore it with scripts/db_restore.py from the
   cold backup and prove the restored file hashes to the backup and passes integrity. Also proves db_restore refuses the live
   database without --allow-live (refused before any file operation).
4. Production hash re-checked at the end.
Report: docs/database_safety_<stamp>.json. Exit 0 = all PASS.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import db_restore  # noqa: E402

LIVE = (ROOT / "data" / "platform.db").resolve()
EXPECTED_POST_7B = "c916f9cb81a036537318db3b9223c563ef7e3bcd3618269362017180dc1badff"
BACKUP_ROOT = Path(r"C:\FrontierAtlas-backups") if os.name == "nt" else Path.home() / "FrontierAtlas-backups"
WORK_ROOT = Path(r"C:\FrontierAtlas-test") if os.name == "nt" else Path.home() / "FrontierAtlas-test"


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def facts(p: Path) -> dict[str, object]:
    with closing(sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True)) as c:  # closing(): sqlite3's own `with` does not close
        tables = [r[0] for r in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]
        return {"sha256": sha(p), "bytes": p.stat().st_size,
                "revision": c.execute("select version_num from alembic_version").fetchone()[0] if "alembic_version" in tables else None,
                "integrity": c.execute("pragma integrity_check").fetchone()[0], "fk_violations": len(c.execute("pragma foreign_key_check").fetchall()),
                "row_counts": {t: c.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables}}


def main() -> int:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rep: dict[str, object] = {"stamp": stamp, "production_writes": "none"}
    prod = facts(LIVE)
    rep["production"] = {**prod, "path": str(LIVE), "equals_expected_post_7b": prod["sha256"] == EXPECTED_POST_7B,
                         "side_files": [x for x in ("-journal", "-wal", "-shm") if LIVE.with_name(LIVE.name + x).exists()]}
    dirs = sorted(BACKUP_ROOT.glob("activation-*"))
    if not dirs:
        rep["backup"] = {"ok": False, "error": f"no activation backup in {BACKUP_ROOT}"}
        return finish(rep, stamp)
    B = dirs[-1]
    cold, online = B / "platform.pre-activation.cold.db", B / "platform.pre-activation.online.db"
    sums = {}
    if (B / "SHA256SUMS.txt").exists():
        for line in (B / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
            if line.strip():
                h, n = line.split(None, 1)
                sums[n.strip()] = h
    fc, fo = facts(cold), facts(online)
    bk = {"dir": str(B), "outside_onedrive": "onedrive" not in str(B).lower(),
          "cold": {"sha256": fc["sha256"], "matches_SHA256SUMS": sums.get(cold.name) == fc["sha256"], "equals_production": fc["sha256"] == prod["sha256"],
                   "integrity": fc["integrity"], "fk_violations": fc["fk_violations"]},
          "online": {"sha256": fo["sha256"], "matches_SHA256SUMS": sums.get(online.name) == fo["sha256"], "integrity": fo["integrity"],
                     "fk_violations": fo["fk_violations"], "row_counts_equal_production": fo["row_counts"] == prod["row_counts"],
                     "revision": fo["revision"]}}
    bk["ok"] = (bk["outside_onedrive"] and bk["cold"]["matches_SHA256SUMS"] and bk["cold"]["equals_production"] and fc["integrity"] == "ok"
                and fc["fk_violations"] == 0 and bk["online"]["matches_SHA256SUMS"] and fo["integrity"] == "ok" and fo["fk_violations"] == 0
                and bk["online"]["row_counts_equal_production"])
    rep["backup"] = bk

    W = WORK_ROOT / f"rollback-{stamp}"
    W.mkdir(parents=True, exist_ok=False)
    target = W / "platform.db"
    shutil.copyfile(online, target)
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{target.as_posix()}", "HISTORY_ENABLED": "true", "REDIS_URL": "",
           "PYTHONIOENCODING": "utf-8"}
    sync = subprocess.run([sys.executable, "-m", "src.main", "sync-evidence"], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    changed = facts(target)
    rb: dict[str, object] = {"target": str(target), "simulated_write": "sync-evidence", "simulated_write_exit": sync.returncode,
                             "target_changed": changed["sha256"] != fo["sha256"],
                             "rows_added_by_simulation": {t: changed["row_counts"][t] - fo["row_counts"].get(t, 0)  # type: ignore[index]
                                                          for t in changed["row_counts"] if changed["row_counts"][t] != fo["row_counts"].get(t, 0)}}  # type: ignore[union-attr]
    r = db_restore.restore(cold, target, fc["sha256"])  # type: ignore[arg-type]
    rb["restore"] = r
    rb["restored_equals_backup"] = r["sha256"] == fc["sha256"]
    try:
        db_restore.restore(cold, LIVE, fc["sha256"])  # type: ignore[arg-type]
        rb["live_refused_without_allow_live"] = False
    except db_restore.RestoreRefused as e:
        rb["live_refused_without_allow_live"] = True
        rb["live_refusal_message"] = str(e)[:200]
    rb["ok"] = (sync.returncode == 0 and rb["target_changed"] and r["ok"] and rb["restored_equals_backup"] and rb["live_refused_without_allow_live"])
    rep["rollback_rehearsal"] = rb
    return finish(rep, stamp)


def finish(rep: dict[str, object], stamp: str) -> int:
    end = sha(LIVE)
    p = rep["production"]  # type: ignore[assignment]
    rep["production_unchanged_during_check"] = end == p["sha256"]  # type: ignore[index]
    ok = (p["integrity"] == "ok" and p["fk_violations"] == 0 and rep["production_unchanged_during_check"]  # type: ignore[index]
          and rep.get("backup", {}).get("ok") and rep.get("rollback_rehearsal", {}).get("ok"))  # type: ignore[union-attr]
    rep["result"] = "PASS" if ok else "FAIL"
    (ROOT / "docs" / f"database_safety_{stamp}.json").write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    print(json.dumps(rep, indent=2, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
