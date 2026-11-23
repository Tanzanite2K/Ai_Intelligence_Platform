"""Integrity audit of the REAL collected dataset (read-only).

Checks every stored record and writes ``docs/data_audit.json``:
* counts per vertical and per source;
* schema re-validation of every payload against the Pydantic models;
* source URL validity (absolute http(s)); duplicate natural keys / URLs;
* ISO-8601 UTC timestamps; collectedAt never used as a publication date;
* research: title/authors/arXiv URL/repo/stars coverage, star provenance;
* jobs/news: verified-fresh within the window *now* vs unverified vs stale;
* entity mappings: methods, review queue, suspicious merges for manual check.

    python scripts/audit_data.py [--window-hours 24]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError

from src.config.settings import get_settings
from src.core.schemas import RECORD_CLASSES, RecordType
from src.database.repository import Repository
from src.database.session import Database
from src.entity_resolution.normalize import compact_key

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
URL_RE = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.I)


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


async def audit(window_hours: int) -> dict:
    s = get_settings()
    db = Database(s.database_url)
    repo = Repository(db)
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=window_hours)
    out: dict = {"audited_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "database": s.database_url.split("///")[-1].split("@")[-1], "verticals": {}}
    for rt in RecordType:
        rows = await repo.fetch_records(rt.value)
        cls = RECORD_CLASSES[rt]
        v: dict = {"stored": len(rows), "by_source": dict(Counter(r["source"]["name"] for r in rows))}
        invalid, bad_url, bad_ts, keys, urls = 0, 0, 0, Counter(), Counter()
        for r in rows:
            try:
                rec = cls.model_validate(r)
                keys[rec.dedup_hash()] += 1
            except ValidationError:
                invalid += 1
            if not URL_RE.match(r["source"]["url"] or ""):
                bad_url += 1
            urls[r["source"]["url"]] += 1
            if not ISO_RE.match(r.get("collectedAt") or ""):
                bad_ts += 1
        v.update(schema_invalid=invalid, invalid_source_urls=bad_url, non_iso_collectedAt=bad_ts,
                 duplicate_natural_keys=sum(c - 1 for c in keys.values() if c > 1), duplicate_source_urls=sum(c - 1 for c in urls.values() if c > 1),
                 validation_status=dict(Counter(r["provenance"]["validationStatus"] for r in rows)))

        if rt is RecordType.STARTUP:
            v["with_employee_count"] = sum(1 for r in rows if r["content"]["data"].get("employeeCount") is not None)
            v["with_website"] = sum(1 for r in rows if r["content"].get("website"))
        if rt is RecordType.PRODUCT:
            v["pricing_model"] = dict(Counter(str(r["content"].get("pricingModel")) for r in rows))
            v["with_startup_name"] = sum(1 for r in rows if r["content"].get("startupName"))
            v["llm_enriched"] = sum(1 for r in rows if r["provenance"].get("llmProvider"))
        if rt is RecordType.RESEARCH_PAPER:
            c = [r["content"] for r in rows]
            v.update(
                with_title=sum(1 for x in c if len(x.get("title") or "") >= 3),
                with_authors=sum(1 for x in c if x.get("authors")),
                arxiv_paper_url=sum(1 for x in c if re.match(r"^https://arxiv\.org/abs/\d{4}\.\d{4,5}$", x.get("paper_url") or "")),
                with_github_url=sum(1 for x in c if x.get("github_url")),
                with_stars=sum(1 for x in c if x.get("github_stars") is not None),
                stars_source=dict(Counter(str(x.get("github_stars_source")) for x in c)),
                stars_checked_last_24h_live=sum(1 for x in c if x.get("github_stars_source") == "github_api"
                                                 and (_dt(x.get("github_stars_checked_at")) or now - timedelta(days=9)) >= cutoff),
                published_date_iso=sum(1 for x in c if ISO_RE.match(x.get("published_date") or "")),
                published_range=[min((x["published_date"] for x in c), default=None), max((x["published_date"] for x in c), default=None)],
            )
        if rt in (RecordType.JOB, RecordType.NEWS):
            field = "date" if rt is RecordType.JOB else "published_date"
            fresh = unverified = stale = 0
            same_as_collected = 0
            for r in rows:
                c = r["content"]
                pub = _dt(c.get(field))
                if pub and r.get("collectedAt") and abs((pub - _dt(r["collectedAt"])).total_seconds()) < 1:  # type: ignore[operator]
                    same_as_collected += 1
                if c.get("date_verified") and pub:
                    if pub >= cutoff:
                        fresh += 1
                    else:
                        stale += 1
                else:
                    unverified += 1
            v.update(verified_fresh_now=fresh, verified_but_older_than_window_now=stale, unverified_dates=unverified,
                     published_equals_collected=same_as_collected,
                     date_sources=dict(Counter((r["content"].get("date_source") or "none").split("=")[0].split(":")[0] for r in rows)))
            if rt is RecordType.NEWS:
                v["with_full_text"] = sum(1 for r in rows if r["content"].get("full_text"))
        out["verticals"][rt.value] = v

    maps = await repo.fetch_mappings()
    methods = Counter(m["matchMethod"] for m in maps)
    # merges where raw and canonical share no normalised token -> listed for manual verification
    suspicious = []
    for m in maps:
        if m["matchMethod"] in ("FUZZY", "SEMANTIC", "DOMAIN", "ALIAS"):
            a, b = compact_key(m["originalName"]), compact_key(m["canonicalName"])
            if a and b and a not in b and b not in a:
                suspicious.append({k: m[k] for k in ("originalName", "canonicalName", "matchMethod", "confidence", "sourceUrl")})
    out["entity_resolution"] = {
        "mappings": len(maps), "by_method": dict(methods), "auto_resolved_to_existing": sum(methods[k] for k in ("EXACT", "ALIAS", "DOMAIN", "FUZZY", "SEMANTIC")),
        "new_entities": methods.get("NEW", 0), "needs_review": sum(1 for m in maps if m["needsReview"]),
        "merges_for_manual_check": suspicious[:200], "merges_for_manual_check_count": len(suspicious),
    }
    out["source_runs_latest"] = {}
    for r in await repo.fetch_runs():
        out["source_runs_latest"][r["source"]] = {"status": r["status"], "fetched": r["fetched"], "stored": r["stored"], "error": (r["error"] or "")[:200],
                                                  "at": r["started_at"].isoformat() if hasattr(r["started_at"], "isoformat") else str(r["started_at"])}
    out["dead_letters"] = await repo.count_dead_letters()
    await db.dispose()
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-hours", type=int, default=24)
    res = asyncio.run(audit(ap.parse_args().window_hours))
    Path("docs").mkdir(exist_ok=True)
    Path("docs/data_audit.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "entity_resolution"} | {"entity_resolution": {k: v for k, v in res["entity_resolution"].items() if k != "merges_for_manual_check"}}, indent=2, default=str))
