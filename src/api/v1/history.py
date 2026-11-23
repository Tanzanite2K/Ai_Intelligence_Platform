"""Phase 7E read-only history API: record history, version comparison, metric series, change feed, batches, coverage,
source reliability. Everything is read from the history tables and ``source_runs``; nothing is estimated or filled in.

Baseline rows (``baseline_backfill``) describe what FrontierAtlas held on 2026-11-21. They are never reported as new or
as changes, and their first-seen date is a bound ("on or before"), not an exact date.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Path, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import RecordRow

from . import fields as F
from .deps import ApiError, Session

router = APIRouter()
BASELINE = "baseline_backfill"
EMERGING_MIN_DAYS = 30


def iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")
    s = str(v).replace(" ", "T")
    return s if s.endswith("Z") or "+" in s[10:] else s + "Z"


def _json(v: Any) -> Any:
    if isinstance(v, (str, bytes)):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _name(rt: str, payload: dict[str, Any]) -> str:
    c = payload.get("content") or {}
    return str(c.get("entityName") if rt == "STARTUP" else c.get("productName") if rt == "PRODUCT" else c.get("title") or "")


async def _has_history(s: AsyncSession) -> bool:
    try:
        await s.execute(text("select 1 from record_versions limit 1"))
        return True
    except Exception:
        return False


async def _record(s: AsyncSession, record_id: int) -> RecordRow:
    row = await s.get(RecordRow, record_id)
    if row is None:
        raise ApiError(404, "not_found", f"No record with id {record_id}")
    if not await _has_history(s):
        raise ApiError(503, "history_unavailable", "History tables are not present in this database (Phase 7B not applied).")
    return row


# ----------------------------------------------------------------------------------------------------------- record
@router.get("/records/{record_id}/history", tags=["history"], summary="Versions, observations and presence of one record")
async def record_history(record_id: int = Path(ge=1), s: AsyncSession = Session) -> dict[str, Any]:
    row = await _record(s, record_id)
    p = {"t": row.record_type, "h": row.dedup_hash}
    vers = (await s.execute(text(
        "select v.version_no, v.version_origin, v.content_hash, v.valid_from, v.valid_to, v.batch_id, b.trigger, v.changed_fields "
        "from record_versions v left join ingestion_batches b on b.batch_id=v.batch_id where v.record_type=:t and v.dedup_hash=:h order by v.version_no"), p)).all()
    obs = (await s.execute(text(
        "select o.batch_id, b.trigger, o.source_name, o.observed_at, o.observation_origin, o.content_hash, o.published_at "
        "from record_observations o left join ingestion_batches b on b.batch_id=o.batch_id where o.record_type=:t and o.dedup_hash=:h "
        "order by o.observed_at, o.id"), p)).all()
    pres = (await s.execute(text(
        "select event, source_name, observed_at, batch_id, scan_id from presence_events where record_type=:t and dedup_hash=:h order by id"), p)).all()
    versions = [{"version": v[0], "origin": v[1], "content_hash": v[2], "valid_from": iso(v[3]), "valid_to": iso(v[4]), "current": v[4] is None,
                 "batch_id": v[5], "trigger": v[6], "changed_fields": _json(v[7]) or []} for v in vers]
    observations = [{"batch_id": o[0], "trigger": o[1], "source": o[2], "observed_at": iso(o[3]), "origin": o[4], "content_hash": o[5],
                     "published_at": iso(o[6])} for o in obs]
    first = observations[0] if observations else None
    baseline_first = bool(first and first["origin"] == BASELINE)
    live = [o for o in observations if o["origin"] != BASELINE]
    note = None
    if not live:
        note = ("Only the baseline snapshot of 2026-11-21 exists for this record. FrontierAtlas records versions, changes and "
                "presence from the first live collection batch onward; nothing earlier is reconstructed.")
    return {"data": {
        "record": {"id": row.id, "type": F.SLUG_OF.get(row.record_type, row.record_type), "name": _name(row.record_type, _json(row.payload) or {}),
                   "source": row.source_name, "published_at": iso(row.published_at)},
        "first_seen": {"at": first["observed_at"] if first else None, "bound": "on_or_before" if baseline_first else "exact",
                       "label": (f"In baseline, first seen on or before {first['observed_at'][:10]}" if baseline_first
                                 else f"First seen {first['observed_at'][:10]}" if first else "Not observed")},
        "last_seen": observations[-1]["observed_at"] if observations else None,
        "in_baseline": baseline_first, "newly_discovered": bool(first and not baseline_first),
        "versions": versions, "observations": observations,
        "presence": [{"event": e[0], "source": e[1], "observed_at": iso(e[2]), "batch_id": e[3], "scan_id": e[4]} for e in pres],
        "counts": {"versions": len(versions), "observations": len(observations), "live_observations": len(live),
                   "sources": len({o["source"] for o in observations})},
    }, "note": note}


def _flatten(d: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else k))
    else:
        out[prefix] = d
    return out


@router.get("/records/{record_id}/history/compare", tags=["history"], summary="Field-level comparison of two versions")
async def compare_versions(record_id: int = Path(ge=1), a: int | None = Query(None, ge=1, description="older version (default: previous)"),
                           b: int | None = Query(None, ge=1, description="newer version (default: current)"),
                           s: AsyncSession = Session) -> dict[str, Any]:
    row = await _record(s, record_id)
    vers = {v[0]: v for v in (await s.execute(text(
        "select version_no, version_origin, valid_from, payload, content_hash from record_versions where record_type=:t and dedup_hash=:h"),
        {"t": row.record_type, "h": row.dedup_hash})).all()}
    if not vers:
        return {"data": None, "note": "This record has no stored versions."}
    newest = max(vers)
    b = b or newest
    a = a or max(1, b - 1)
    if a not in vers or b not in vers:
        raise ApiError(404, "no_such_version", f"Available versions: {sorted(vers)}")
    pa, pb = _flatten(_json(vers[a][3]) or {}), _flatten(_json(vers[b][3]) or {})
    changes = []
    for k in sorted(set(pa) | set(pb)):
        if k == "collectedAt":
            continue
        if pa.get(k) != pb.get(k):
            kind = "added" if k not in pa else "removed" if k not in pb else "changed"
            changes.append({"field": k, "change": kind, "before": pa.get(k), "after": pb.get(k)})
    meta = {n: {"version": n, "origin": vers[n][1], "valid_from": iso(vers[n][2]), "content_hash": vers[n][4]} for n in (a, b)}
    note = None
    if a == b:
        note = "Only one version is stored, so there is nothing to compare yet. A new version is created only when the content changes."
    return {"data": {"from": meta[a], "to": meta[b], "changes": changes, "available_versions": sorted(vers)}, "note": note}


@router.get("/records/{record_id}/history/metrics", tags=["history"], summary="Numeric series (e.g. GitHub stars) for one record")
async def record_metrics(record_id: int = Path(ge=1), s: AsyncSession = Session) -> dict[str, Any]:
    row = await _record(s, record_id)
    pts = (await s.execute(text(
        "select metric, value, observed_at, source_name, batch_id from metric_snapshots where subject_kind=:t and subject_key=:h order by observed_at"),
        {"t": row.record_type, "h": row.dedup_hash})).all()
    series: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m, v, at, src, b in pts:
        series[m].append({"at": iso(at), "value": v, "source": src, "batch_id": b, "kind": "snapshot"})
    # The baseline value stored on version 1 (with the time the source reported it), shown as the series' starting point.
    v1 = (await s.execute(text("select payload, valid_from from record_versions where record_type=:t and dedup_hash=:h and version_no=1"),
                          {"t": row.record_type, "h": row.dedup_hash})).first()
    if v1:
        c = (_json(v1[0]) or {}).get("content") or {}
        base: dict[str, tuple[Any, Any]] = {}
        if row.record_type == "RESEARCH_PAPER" and isinstance(c.get("github_stars"), int):
            base["github_stars"] = (c["github_stars"], c.get("github_stars_checked_at") or v1[1])
        if row.record_type == "STARTUP" and isinstance((c.get("data") or {}).get("employeeCount"), int):
            base["employeeCount"] = (c["data"]["employeeCount"], v1[1])
        for m, (val, at) in base.items():
            if not any(p["batch_id"] is None for p in series[m]):
                series[m].insert(0, {"at": iso(at), "value": float(val), "source": row.source_name, "batch_id": None, "kind": "baseline"})
    points = sum(len(v) for v in series.values())
    note = None
    if all(len(v) < 2 for v in series.values()):
        note = ("One data point so far (the baseline value). Movement is shown once later collection batches record new values; "
                "no values are interpolated.") if points else "This record has no numeric metrics."
    return {"data": {"series": [{"metric": m, "points": v} for m, v in series.items()]}, "note": note}


# ----------------------------------------------------------------------------------------------------- change feed
@router.get("/history/coverage", tags=["history"], summary="How much history exists, and whether trend labels are allowed")
async def coverage(s: AsyncSession = Session) -> dict[str, Any]:
    if not await _has_history(s):
        return {"data": {"available": False}, "note": "History tables are not present in this database."}
    b = (await s.execute(text("select trigger, scheduled_for, status, started_at from ingestion_batches order by started_at"))).all()
    live = [x for x in b if x[0] != BASELINE]
    # Only completed batches count as reliable collection days for the 30-day emerging-label rule.
    live_days = sorted({str(x[3])[:10] for x in live if x[2] == "completed"})
    base = next((x for x in b if x[0] == BASELINE), None)
    counts = dict((await s.execute(text("select observation_origin, count(*) from record_observations group by 1"))).all())
    eligible = len(live_days) >= EMERGING_MIN_DAYS
    note = (f"History holds the baseline snapshot and {len(live_days)} live collection day(s). Snapshot-based emerging-trend labels "
            f"need {EMERGING_MIN_DAYS} days of reliable collection; {max(0, EMERGING_MIN_DAYS - len(live_days))} more day(s) are required.")
    return {"data": {"available": True, "baseline_date": str(base[1])[:10] if base else None, "batches": len(b), "live_batches": len(live),
                     "live_collection_days": len(live_days), "first_live_day": live_days[0] if live_days else None,
                     "last_live_day": live_days[-1] if live_days else None, "observations": counts,
                     "emerging_labels_enabled": eligible, "emerging_min_days": EMERGING_MIN_DAYS}, "note": None if eligible else note}


@router.get("/history/batches", tags=["history"], summary="Collection batches with what each recorded")
async def batches(s: AsyncSession = Session) -> dict[str, Any]:
    if not await _has_history(s):
        return {"data": [], "note": "History tables are not present in this database."}
    rows = (await s.execute(text(
        "select b.batch_id, b.trigger, b.scheduled_for, b.started_at, b.finished_at, b.status, "
        "(select count(*) from record_observations o where o.batch_id=b.batch_id), "
        "(select count(*) from presence_events p where p.batch_id=b.batch_id and p.event='appeared'), "
        "(select count(*) from record_versions v where v.batch_id=b.batch_id and v.version_no>1), "
        "(select count(*) from presence_events p where p.batch_id=b.batch_id and p.event='disappeared'), "
        "(select count(*) from metric_snapshots m where m.batch_id=b.batch_id), b.notes "
        "from ingestion_batches b order by b.started_at desc"))).all()
    return {"data": [{"batch_id": r[0], "trigger": r[1], "scheduled_for": iso(r[2]), "started_at": iso(r[3]), "finished_at": iso(r[4]), "status": r[5],
                      "observations": r[6], "new": r[7], "modified": r[8], "disappeared": r[9], "metric_samples": r[10], "notes": r[11]} for r in rows]}


@router.get("/history/changes", tags=["history"], summary="Change feed: new, modified, disappeared and reappeared records")
async def changes(kind: str | None = Query(None, pattern="^(new|modified|disappeared|reappeared)$"),
                  type: str | None = Query(None, description="startups, products, papers, jobs or news"),
                  days: int = Query(90, ge=1, le=3650), page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
                  s: AsyncSession = Session) -> dict[str, Any]:
    if not await _has_history(s):
        return {"data": [], "meta": {"total": 0}, "note": "History tables are not present in this database."}
    rt = F.TYPE_SLUGS.get(type) if type else None
    if type and not rt:
        raise ApiError(422, "invalid_type", f"type must be one of {sorted(F.TYPE_SLUGS)}")
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    parts = []
    if kind in (None, "modified"):
        parts.append("select 'modified' as kind, v.record_type as rt, v.dedup_hash as dh, v.valid_from as at, v.batch_id as bid, "
                     "v.changed_fields as detail, v.version_no as ver from record_versions v where v.version_no > 1 and v.version_origin <> 'baseline_backfill'")
    for ev, k in (("appeared", "new"), ("disappeared", "disappeared"), ("reappeared", "reappeared")):
        if kind in (None, k):
            parts.append(f"select '{k}' as kind, p.record_type as rt, p.dedup_hash as dh, p.observed_at as at, p.batch_id as bid, "
                         f"p.source_name as detail, null as ver from presence_events p where p.event='{ev}'")
    union = " union all ".join(parts)
    where = "where c.at >= :since" + (" and c.rt = :rt" if rt else "")
    params: dict[str, Any] = {"since": since, "rt": rt}
    total = (await s.execute(text(f"select count(*) from ({union}) c {where}"), params)).scalar_one()
    rows = (await s.execute(text(
        f"select c.kind, c.rt, c.at, c.bid, c.detail, c.ver, r.id, r.payload, r.source_name, b.trigger from ({union}) c "
        f"left join records r on r.record_type=c.rt and r.dedup_hash=c.dh left join ingestion_batches b on b.batch_id=c.bid "
        f"{where} order by c.at desc limit :lim offset :off"), {**params, "lim": page_size, "off": (page - 1) * page_size})).all()
    data = []
    for k, t, at, bid, detail, ver, rid, payload, src, trig in rows:
        data.append({"kind": k, "type": F.SLUG_OF.get(t, t), "record_id": rid, "name": _name(t, _json(payload) or {}) if payload else None,
                     "at": iso(at), "batch_id": bid, "trigger": trig, "source": src,
                     "changed_fields": _json(detail) if k == "modified" else None, "version": ver})
    by_kind = dict((await s.execute(text(f"select c.kind, count(*) from ({union}) c {where} group by c.kind"), params)).all())
    cov = await coverage(s)
    note = None
    if total == 0:
        note = ("No changes recorded yet. The database holds the baseline snapshot of 2026-11-21 "
                f"({sum(cov['data'].get('observations', {}).values())} observations), which is never reported as new or changed. "
                "The feed fills from the first live collection batch (Phase 7C, enabled with HISTORY_ENABLED=true).")
    return {"data": data, "meta": {"total": total, "page": page, "page_size": page_size, "by_kind": by_kind, "days": days},
            "coverage": cov["data"], "note": note}


# ------------------------------------------------------------------------------------------------- source reliability
@router.get("/operations/source-reliability", tags=["operations"], summary="Per-source reliability by day, from recorded runs")
async def source_reliability(days: int = Query(30, ge=1, le=365), s: AsyncSession = Session) -> dict[str, Any]:
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = (await s.execute(text(
        "select source, date(started_at) d, count(*), sum(status='OK'), sum(status='PARTIAL'), sum(status='FAILED'), sum(status='INACCESSIBLE'), "
        "sum(fetched), sum(stored), avg((julianday(finished_at)-julianday(started_at))*86400.0) "
        "from source_runs where started_at >= :since group by source, d order by source, d"), {"since": since})).all()
    per: dict[str, dict[str, Any]] = {}
    for src, d, n, ok, part, failed, inacc, fetched, stored, secs in rows:
        e = per.setdefault(src, {"source": src, "days": [], "runs": 0, "ok": 0, "partial": 0, "failed": 0, "inaccessible": 0})
        e["days"].append({"date": d, "runs": n, "ok": ok, "partial": part, "failed": failed, "inaccessible": inacc,
                          "fetched": fetched or 0, "stored": stored or 0, "avg_seconds": round(secs, 1) if secs is not None else None})
        for k, v in (("runs", n), ("ok", ok), ("partial", part), ("failed", failed), ("inaccessible", inacc)):
            e[k] += v or 0
    out = []
    for e in per.values():
        e["success_rate"] = round((e["ok"] + e["partial"]) / e["runs"], 3) if e["runs"] else None
        out.append(e)
    out.sort(key=lambda e: (e["success_rate"] if e["success_rate"] is not None else -1, e["source"]))
    days_with_runs = sorted({d["date"] for e in out for d in e["days"]})
    note = None
    if len(days_with_runs) < 2:
        note = (f"Runs were recorded on {len(days_with_runs)} day(s), so this is a single snapshot rather than a trend. "
                "Each scheduled collection day adds one point per source.")
    return {"data": out, "meta": {"days": days, "days_with_runs": days_with_runs, "sources": len(out)},
            "definition": "success rate = (OK + PARTIAL runs) / all recorded runs; PARTIAL = some items collected with errors", "note": note}
