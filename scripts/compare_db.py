"""Explain every change between two database snapshots (read-only).

    python scripts/compare_db.py <before.db> <after.db>   -> docs/db_comparison.json

Per vertical: before/after counts, records added/removed (by source URL) with per-source
breakdown, and canonical-name changes (startup entityName, product startupName, job company).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

NAME_FIELD = {"STARTUP": "$.content.entityName", "PRODUCT": "$.content.startupName", "JOB": "$.content.company"}


def load(path: str) -> dict[str, dict[str, tuple[str, str | None]]]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out: dict[str, dict[str, tuple[str, str | None]]] = {}
    for rt, url, src, payload in con.execute("select record_type, source_url, source_name, payload from records"):
        name = None
        if rt in NAME_FIELD:
            name = con.execute("select json_extract(?, ?)", (payload, NAME_FIELD[rt])).fetchone()[0]
        out.setdefault(rt, {})[url] = (src, name)
    integrity = con.execute("pragma integrity_check").fetchone()[0]
    con.close()
    out["__integrity__"] = {"": (integrity, None)}
    return out


def main(before: str, after: str) -> dict:
    a, b = load(before), load(after)
    rep: dict = {"before": before, "after": after, "integrity_before": a.pop("__integrity__")[""][0],
                 "integrity_after": b.pop("__integrity__")[""][0], "verticals": {}}
    for rt in sorted(set(a) | set(b)):
        A, B = a.get(rt, {}), b.get(rt, {})
        added, removed = set(B) - set(A), set(A) - set(B)
        renamed = Counter((A[u][1], B[u][1]) for u in set(A) & set(B) if A[u][1] != B[u][1])
        rep["verticals"][rt] = {
            "before": len(A), "after": len(B), "delta": len(B) - len(A),
            "added": len(added), "added_by_source": dict(Counter(B[u][0] for u in added)),
            "removed": len(removed), "removed_by_source": dict(Counter(A[u][0] for u in removed)), "removed_sample": sorted(removed)[:15],
            "canonical_name_changes": sum(renamed.values()), "name_change_pairs": [[x, y, n] for (x, y), n in renamed.most_common(40)],
        }
    return rep


if __name__ == "__main__":
    res = main(sys.argv[1], sys.argv[2])
    Path("docs").mkdir(exist_ok=True)
    Path("docs/db_comparison.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "verticals"} | {"verticals": {k: {x: v[x] for x in ("before", "after", "delta", "added", "removed", "canonical_name_changes")} for k, v in res["verticals"].items()}}, indent=2))
