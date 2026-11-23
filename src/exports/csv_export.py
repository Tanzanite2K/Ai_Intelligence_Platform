"""CSV fallback export: one UTF-8 CSV per tab (always produced; Sheets is optional)."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from src.exports.tabs import Tab


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")


def write_csvs(tabs: list[Tab], out_dir: str | Path) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for t in tabs:
        p = out / f"{slug(t.title)}.csv"
        with p.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(t.columns)
            for row in t.rows:
                w.writerow(["" if v is None else v for v in row])
        paths[t.title] = p
    return paths
