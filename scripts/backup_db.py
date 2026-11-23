"""Consistent SQLite backup with verification (online backup API, not a file copy).

Creates data/platform.backup-<UTCSTAMP>.db, runs PRAGMA integrity_check on source and backup,
compares per-table row counts, and records the SHA-256 of the backup in docs/backup_<stamp>.json.
Exit code 0 only if every check passes.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "data/platform.db")


def counts(con: sqlite3.Connection) -> dict[str, int]:
    tables = [r[0] for r in con.execute("select name from sqlite_master where type='table' order by 1")]
    return {t: con.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables}


def main() -> int:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dst = SRC.with_name(f"platform.backup-{stamp}.db")
    src = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True)
    src_integrity = src.execute("pragma integrity_check").fetchone()[0]
    out = sqlite3.connect(dst)
    src.backup(out)
    out.close()
    bk = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    bk_integrity = bk.execute("pragma integrity_check").fetchone()[0]
    c_src, c_bk = counts(src), counts(bk)
    src.close()
    bk.close()
    sha = hashlib.sha256(dst.read_bytes()).hexdigest()
    ok = src_integrity == "ok" and bk_integrity == "ok" and c_src == c_bk
    rep = {"backup": str(dst), "sha256": sha, "bytes": dst.stat().st_size, "source_integrity": src_integrity,
           "backup_integrity": bk_integrity, "row_counts": c_bk, "counts_match": c_src == c_bk, "ok": ok, "created_at": stamp}
    Path("docs").mkdir(exist_ok=True)
    Path(f"docs/backup_{stamp}.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    Path("docs/backup_latest.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    print(json.dumps(rep, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
