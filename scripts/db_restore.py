"""Restore a SQLite database file from a cold (byte-for-byte) backup and prove it with SHA-256.

* The source must hash to --expect-sha256 before anything happens.
* An existing target is renamed to <name>.failed-<stamp>.db, never deleted.
* The restored file must hash to --expect-sha256 and pass PRAGMA integrity_check.
* Refuses the project's live data/platform.db unless --allow-live is given (Run B only, after approval), and then also
  refuses while anything listens on ports 8000/3000.

Usage:  python scripts/db_restore.py --from <cold backup> --to <target> --expect-sha256 <hex>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import socket
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LIVE_DB = (ROOT / "data" / "platform.db").resolve()


class RestoreRefused(RuntimeError):
    pass


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def restore(src: Path, dst: Path, expect_sha256: str, *, allow_live: bool = False) -> dict[str, Any]:
    src, dst = src.resolve(), dst.resolve()
    if dst == LIVE_DB:
        if not allow_live:
            raise RestoreRefused(f"Refusing to restore over the live database {dst} without --allow-live (Run B approval).")
        busy = [p for p in (8000, 3000) if _port_in_use(p)]
        if busy:
            raise RestoreRefused(f"ports {busy} are in use: stop the API and web app before restoring the live database")
    if not src.exists():
        raise RestoreRefused(f"backup not found: {src}")
    src_sha = sha256_file(src)
    if src_sha != expect_sha256:
        raise RestoreRefused(f"backup hash {src_sha} does not match the expected {expect_sha256}")
    for suffix in ("-journal", "-wal", "-shm"):
        side = dst.with_name(dst.name + suffix)
        if side.exists():
            raise RestoreRefused(f"{side.name} exists next to the target: a connection may be open")
    kept = None
    if dst.exists():
        kept = dst.with_name(f"{dst.stem}.failed-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}{dst.suffix}")
        dst.rename(kept)
    shutil.copyfile(src, dst)
    got = sha256_file(dst)
    con = sqlite3.connect(dst.as_uri() + "?mode=ro", uri=True)
    try:
        integrity = con.execute("pragma integrity_check").fetchone()[0]
    finally:
        con.close()
    ok = got == expect_sha256 and integrity == "ok"
    return {"ok": ok, "from": str(src), "to": str(dst), "previous_file_kept_as": str(kept) if kept else None,
            "sha256": got, "expected_sha256": expect_sha256, "integrity": integrity}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--to", dest="dst", required=True)
    ap.add_argument("--expect-sha256", required=True)
    ap.add_argument("--allow-live", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    r = restore(Path(a.src), Path(a.dst), a.expect_sha256, allow_live=a.allow_live)
    print(json.dumps(r, indent=2))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
