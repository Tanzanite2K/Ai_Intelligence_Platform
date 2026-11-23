"""Scan the STAGED files of a git clone before any commit. Never prints a secret value (file, line and rule only).

Checks: (1) forbidden paths (.env*, credentials/, service-account JSON, keys, SQLite files, backups, logs, venv/node_modules);
(2) secret-shaped strings (same rules as scripts/scan_secrets.py); (3) the literal secret values of this project's .env.
Usage: python scripts/git_stage_scan.py <clone dir> <report.json>.  Exit 0 = clean.
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import scan_secrets  # noqa: E402

FORBIDDEN = [".env", ".env.*", "*/.env", "*/.env.*", "credentials/*", "*/credentials/*", "service-account*.json", "*.json.key", "*.pem", "*.key",
             "*.p12", "*.pfx", "*.db", "*.db-*", "*.sqlite", "*.sqlite3", "backups/*", "*.log", ".venv/*", "*/node_modules/*", "node_modules/*"]
ALLOWED = {".env.example", "frontend/.env.example"}


def main() -> int:
    clone, out = Path(sys.argv[1]), Path(sys.argv[2])
    names = subprocess.run(["git", "-C", str(clone), "diff", "--cached", "--name-only", "-z"], capture_output=True, text=True, encoding="utf-8").stdout.split("\0")
    names = [n for n in names if n]
    deleted = subprocess.run(["git", "-C", str(clone), "diff", "--cached", "--name-only", "--diff-filter=D", "-z"], capture_output=True, text=True,
                             encoding="utf-8").stdout.split("\0")
    deleted = {n for n in deleted if n}
    old = Path.cwd()
    try:
        import os
        os.chdir(ROOT)
        literal = scan_secrets.env_values()
    finally:
        os.chdir(old)
    forbidden = [n for n in names if n not in ALLOWED and any(fnmatch.fnmatch(n, p) for p in FORBIDDEN)]
    findings = []
    for n in names:
        if n in deleted:
            continue
        f = clone / n
        if not f.is_file() or f.stat().st_size > 50_000_000:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for rule, rx in scan_secrets.RULES.items():
                if rx.search(line):
                    findings.append({"file": n, "line": i, "rule": rule})
            if any(v in line for v in literal):
                findings.append({"file": n, "line": i, "rule": "literal_.env_value"})
    rep = {"staged_files": len(names), "deleted_files": sorted(deleted), "forbidden_paths": forbidden, "secret_findings": findings,
           "env_secret_values_checked": len(literal), "clean": not forbidden and not findings}
    out.write_text(json.dumps(rep, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in rep.items()}, indent=2))
    return 0 if rep["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
