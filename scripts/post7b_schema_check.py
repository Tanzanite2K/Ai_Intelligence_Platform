"""Post-7B migration compatibility check on a COPY of the current production database (read-only on production).

Checks: production is at Alembic head; on the copy, `upgrade head` is a no-op (same revision, same DDL, same row
counts); `alembic check` finds no difference between the ORM models and the migrated schema; integrity and foreign
keys pass; production's SHA-256 is identical before and after.
Usage: python scripts/post7b_schema_check.py [--workdir C:\\FrontierAtlas-test]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import phase7a_migration_test as p7a  # noqa: E402

LIVE = (ROOT / "data" / "platform.db").resolve()


def schema(p: Path) -> dict[str, str]:
    with p7a.ro(p) as c:
        return {n: " ".join((s or "").split()) for n, s in c.execute("select name, sql from sqlite_master where name not like 'sqlite_%' order by name")}


def counts(p: Path) -> dict[str, int]:
    with p7a.ro(p) as c:
        ts = [r[0] for r in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
        return {t: c.execute(f'select count(*) from "{t}"').fetchone()[0] for t in ts}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=r"C:\FrontierAtlas-test" if os.name == "nt" else str(Path.home() / "FrontierAtlas-test"))
    a = ap.parse_args()
    from alembic import command
    from alembic.script import ScriptDirectory

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    W = Path(a.workdir) / f"post7b-{stamp}"
    W.mkdir(parents=True, exist_ok=False)
    assert "onedrive" not in str(W).lower(), "work folder must be outside OneDrive"
    live_before = p7a.sha256_file(LIVE)
    copy = W / "platform.copy.db"
    p7a.online_backup(LIVE, copy)
    head = ScriptDirectory.from_config(p7a.alembic_cfg(copy)).get_current_head()
    res: dict[str, object] = {"stamp": stamp, "copy": str(copy), "head": head}
    res["production_revision"] = p7a.revision(LIVE)
    s0, c0 = schema(copy), counts(copy)
    command.upgrade(p7a.alembic_cfg(copy), "head")
    res["upgrade_head_noop"] = p7a.revision(copy) == head and schema(copy) == s0 and counts(copy) == c0
    try:
        command.check(p7a.alembic_cfg(copy))
        res["orm_matches_schema"] = True
    except Exception as e:
        res["orm_matches_schema"] = False
        res["orm_diff"] = str(e)[:2000]
    with p7a.ro(copy) as c:
        res["integrity"] = c.execute("pragma integrity_check").fetchone()[0]
        res["foreign_key_violations"] = len(c.execute("pragma foreign_key_check").fetchall())
    res["production_sha256_before"] = live_before
    res["production_sha256_after"] = p7a.sha256_file(LIVE)
    ok = (res["production_revision"] == head and res["upgrade_head_noop"] and res["orm_matches_schema"] and res["integrity"] == "ok"
          and res["foreign_key_violations"] == 0 and live_before == res["production_sha256_after"])
    res["result"] = "PASS" if ok else "FAIL"
    (ROOT / "docs" / f"post7b_schema_check_{stamp}.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    for k, v in res.items():
        if k != "orm_diff":
            print(f"{k}: {v}")
    if "orm_diff" in res:
        print("orm_diff:", res["orm_diff"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
