"""Scan repo files and generated logs for secret-shaped strings and for the literal values in .env.

Never prints a secret: reports only file, line number and which rule matched.
Excludes .env, credentials/, .venv/, databases. Exit 1 if anything is found.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

RULES = {
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "google_auth_key": re.compile(r"\bAQ\.[A-Za-z0-9_-]{40,}"),
    "github_token": re.compile(r"(ghp_[0-9A-Za-z]{36}|github_pat_[0-9A-Za-z_]{60,})"),
    "hf_token": re.compile(r"\bhf_[0-9A-Za-z]{30,}"),
    "groq_key": re.compile(r"\bgsk_[0-9A-Za-z]{40,}"),
    "openai_style_key": re.compile(r"\bsk-[0-9A-Za-z]{32,}"),
    "private_key": re.compile(r"-----BEGIN (RSA |EC )?PRIVATE KEY-----"),
}
SKIP_DIRS = {".venv", "credentials", ".git", "__pycache__", ".pytest_cache", "node_modules"}
SKIP_SUFFIX = {".db", ".pyc", ".png", ".pdf", ".gz", ".zip"}


def env_values() -> list[str]:
    p = Path(".env")
    if not p.exists():
        return []
    vals = []
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if len(v) >= 16 and any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                vals.append(v)
    return vals


def main() -> int:
    literal = env_values()
    findings = []
    files: list[Path] = []
    for root, dirs, names in os.walk("."):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]  # prune excluded trees instead of walking them
        files.extend(Path(root) / n for n in names)
    for f in files:
        if f.name.startswith(".env") or f.suffix in SKIP_SUFFIX or f.stat().st_size > 50_000_000:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for name, rx in RULES.items():
                if rx.search(line):
                    findings.append({"file": str(f), "line": i, "rule": name})
            if any(v in line for v in literal):
                findings.append({"file": str(f), "line": i, "rule": "literal_.env_value"})
    rep = {"files_scanned_excludes": [*sorted(SKIP_DIRS), ".env*", "*.db"], "env_secret_values_checked": len(literal), "findings": findings, "clean": not findings}
    Path("docs").mkdir(exist_ok=True)
    Path("docs/secret_scan.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    print(json.dumps(rep, indent=2))
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
