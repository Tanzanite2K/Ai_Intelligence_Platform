"""Phase 7C incremental history capture.

Written next to the existing upsert, only when ``HISTORY_ENABLED=true`` and the database is at Alembic revision
``0002_history_tables``. Every write is idempotent, so a retried or resumed batch adds nothing twice:

* one ``ingestion_batches`` row per (trigger, scheduled slot); its id is a UUID5 of that slot, so a re-run of the same
  slot resumes the same batch, and a completed slot is refused (retry protection);
* one ``record_observations`` row per (batch, source, record) - ``ON CONFLICT DO NOTHING``;
* a new ``record_versions`` row only when the stored content hash differs from the open version (no duplicate
  versions for unchanged content); the previous version is closed at the observation time;
* ``metric_snapshots`` for numeric fields that change over time (GitHub stars, YC team size);
* ``presence_events``: ``appeared`` only for records never observed before (baseline records are never "new"),
  ``reappeared`` after a recorded disappearance, and ``disappeared`` only after a COMPLETE scan of a full-catalogue
  source whose item count is within 80% of its previous complete scan.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from src.core.schemas import RecordBase
from src.database.repository import record_to_row
from src.database.session import Database

REQUIRED_REVISION = "0002_history_tables"
BASELINE_ORIGIN = "baseline_backfill"
# Crawler key -> (source_name stored on records, record_type). Only sources that list their WHOLE catalogue on every
# run. Job boards are excluded: their freshness gate drops older postings, so absence from a run proves nothing.
FULL_CATALOGUE_SOURCES: dict[str, tuple[str, str]] = {"yc_startups": ("Y Combinator", "STARTUP")}
MIN_SCAN_RATIO = 0.8
METRICS: dict[str, tuple[str, ...]] = {"RESEARCH_PAPER": ("github_stars",), "STARTUP": ("employeeCount",)}


class HistoryUnavailable(RuntimeError):
    pass


class BatchAlreadyCompleted(RuntimeError):
    pass


def ts(dt: datetime) -> str:
    """Timestamp format used by every existing table (SQLAlchemy's SQLite DateTime storage)."""
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def batch_id_for(trigger: str, scheduled_for: datetime) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"frontieratlas/{trigger}/{ts(scheduled_for)}"))


def _load(v: Any) -> Any:
    return json.loads(v) if isinstance(v, (str, bytes)) else v


def changed_fields(old: dict[str, Any] | None, new: dict[str, Any] | None) -> list[str]:
    """Top-level content fields (and source/provenance blocks) whose values differ."""
    old, new = old or {}, new or {}
    out: list[str] = []
    oc, nc = old.get("content") or {}, new.get("content") or {}
    for k in sorted(set(oc) | set(nc)):
        if oc.get(k) != nc.get(k):
            out.append(f"content.{k}")
    for blk in ("source", "provenance"):
        if old.get(blk) != new.get(blk):
            out.append(blk)
    return out


def metric_values(record_type: str, payload: dict[str, Any]) -> dict[str, float]:
    c = payload.get("content") or {}
    out: dict[str, float] = {}
    for m in METRICS.get(record_type, ()):
        v = (c.get("data") or {}).get(m) if m == "employeeCount" else c.get(m)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[m] = float(v)
    return out


@dataclass
class CaptureStats:
    observed: int = 0
    already_recorded: int = 0
    new: int = 0
    modified: int = 0
    unchanged: int = 0
    metrics: int = 0
    reappeared: int = 0
    changed_fields: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class HistoryRecorder:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def check_ready(self) -> None:
        async with self.db.session() as s:
            try:
                rev = (await s.execute(text("select version_num from alembic_version"))).scalars().all()
            except Exception as e:  # table missing
                raise HistoryUnavailable("database is not registered with Alembic; history tables are missing") from e
        if rev != [REQUIRED_REVISION]:
            raise HistoryUnavailable(f"database is at revision {rev}, expected {REQUIRED_REVISION}")

    # ------------------------------------------------------------------ batches
    async def open_batch(self, trigger: str, scheduled_for: datetime, *, code_version: str | None = None,
                         now: datetime | None = None) -> tuple[str, bool]:
        """Return (batch_id, resumed). A completed slot is refused; a running/failed/partial slot is resumed."""
        await self.check_ready()
        bid = batch_id_for(trigger, scheduled_for)
        now = now or datetime.now(UTC)
        async with self.db.session() as s:
            row = (await s.execute(text("select status from ingestion_batches where batch_id=:b"), {"b": bid})).first()
            if row and row[0] == "completed":
                raise BatchAlreadyCompleted(f"batch {trigger} @ {ts(scheduled_for)} already completed ({bid})")
            if row:
                await s.execute(text("update ingestion_batches set status='running', finished_at=NULL where batch_id=:b"), {"b": bid})
                return bid, True
            await s.execute(text(
                "insert into ingestion_batches(batch_id,trigger,scheduled_for,started_at,status,code_version) "
                "values (:b,:t,:sf,:st,'running',:cv) on conflict(batch_id) do nothing"),
                {"b": bid, "t": trigger, "sf": ts(scheduled_for), "st": ts(now), "cv": code_version})
        return bid, False

    async def close_batch(self, batch_id: str, status: str, *, notes: str | None = None, now: datetime | None = None) -> None:
        async with self.db.session() as s:
            await s.execute(text("update ingestion_batches set status=:s, finished_at=:f, notes=coalesce(:n, notes) where batch_id=:b"),
                            {"s": status, "f": ts(now or datetime.now(UTC)), "n": notes, "b": batch_id})

    # ------------------------------------------------------------------ records
    async def record(self, batch_id: str, records: Sequence[RecordBase], *, observed_at: datetime | None = None) -> CaptureStats:
        rows = [record_to_row(r) for r in records]
        return await self.record_rows(batch_id, rows, observed_at=observed_at)

    async def record_rows(self, batch_id: str, rows: Iterable[dict[str, Any]], *, observed_at: datetime | None = None) -> CaptureStats:
        """``rows`` are ``record_to_row`` dicts (what the source showed). Versions are taken from ``records`` (the stored
        canonical row after the priority-aware upsert), so history always matches what the application serves."""
        at = ts(observed_at or datetime.now(UTC))
        st = CaptureStats()
        uniq: dict[tuple[str, str, str], dict[str, Any]] = {}
        for r in rows:
            uniq[(r["source_name"], r["record_type"], r["dedup_hash"])] = r
        if not uniq:
            return st
        async with self.db.session() as s:
            for (src, rtype, dh), r in uniq.items():
                res = await s.execute(text(
                    "insert into record_observations(batch_id,source_name,record_type,dedup_hash,observed_at,content_hash,published_at,observation_origin) "
                    "values (:b,:s,:t,:h,:at,:c,:p,'live') on conflict(batch_id,source_name,record_type,dedup_hash) do nothing"),
                    {"b": batch_id, "s": src, "t": rtype, "h": dh, "at": at, "c": r["content_hash"],
                     "p": ts(r["published_at"]) if isinstance(r.get("published_at"), datetime) else r.get("published_at")})
                if res.rowcount == 0:
                    st.already_recorded += 1
                else:
                    st.observed += 1
                await self._version_and_presence(s, batch_id, src, rtype, dh, at, st)
        return st

    async def _version_and_presence(self, s: Any, batch_id: str, src: str, rtype: str, dh: str, at: str, st: CaptureStats) -> None:
        stored = (await s.execute(text("select content_hash, payload from records where record_type=:t and dedup_hash=:h"),
                                  {"t": rtype, "h": dh})).first()
        if stored is None:  # rejected by the upsert (e.g. lower priority); nothing canonical to version
            return
        cur = (await s.execute(text(
            "select version_no, content_hash, payload, batch_id from record_versions where record_type=:t and dedup_hash=:h and valid_to is null"),
            {"t": rtype, "h": dh})).first()
        payload = _load(stored[1])
        if cur is None:
            prior = (await s.execute(text(
                "select count(*) from record_observations where record_type=:t and dedup_hash=:h and batch_id<>:b"),
                {"t": rtype, "h": dh, "b": batch_id})).scalar_one()
            await s.execute(text(
                "insert into record_versions(record_type,dedup_hash,version_no,content_hash,payload,valid_from,valid_to,batch_id,version_origin,changed_fields) "
                "select record_type,dedup_hash,1,content_hash,payload,:at,NULL,:b,'observed_change',NULL from records "
                "where record_type=:t and dedup_hash=:h on conflict do nothing"), {"at": at, "b": batch_id, "t": rtype, "h": dh})
            if prior == 0:
                res = await s.execute(text(
                    "insert into presence_events(record_type,dedup_hash,source_name,event,batch_id,observed_at) values (:t,:h,:s,'appeared',:b,:at) "
                    "on conflict do nothing"), {"t": rtype, "h": dh, "s": src, "b": batch_id, "at": at})
                st.new += res.rowcount
        elif cur[1] == stored[0]:
            st.unchanged += 1
        elif cur[3] == batch_id:
            st.unchanged += 1  # already versioned by this batch (retry); never two versions in one batch
        else:
            fields = changed_fields(_load(cur[2]), payload)
            await s.execute(text("update record_versions set valid_to=:at where record_type=:t and dedup_hash=:h and version_no=:v and valid_to is null"),
                            {"at": at, "t": rtype, "h": dh, "v": cur[0]})
            await s.execute(text(
                "insert into record_versions(record_type,dedup_hash,version_no,content_hash,payload,valid_from,valid_to,batch_id,version_origin,changed_fields) "
                "select record_type,dedup_hash,:v,content_hash,payload,:at,NULL,:b,'observed_change',:cf from records "
                "where record_type=:t and dedup_hash=:h on conflict do nothing"),
                {"v": cur[0] + 1, "at": at, "b": batch_id, "cf": json.dumps(fields), "t": rtype, "h": dh})
            st.modified += 1
            for f in fields:
                st.changed_fields[f] = st.changed_fields.get(f, 0) + 1
        for m, v in metric_values(rtype, payload).items():
            res = await s.execute(text(
                "insert into metric_snapshots(subject_kind,subject_key,metric,value,observed_at,source_name,batch_id) "
                "values (:k,:h,:m,:v,:at,:s,:b) on conflict do nothing"), {"k": rtype, "h": dh, "m": m, "v": v, "at": at, "s": src, "b": batch_id})
            st.metrics += res.rowcount
        last = (await s.execute(text(
            "select event from presence_events where record_type=:t and dedup_hash=:h and source_name=:s order by id desc limit 1"),
            {"t": rtype, "h": dh, "s": src})).first()
        if last and last[0] == "disappeared":
            res = await s.execute(text(
                "insert into presence_events(record_type,dedup_hash,source_name,event,batch_id,observed_at) values (:t,:h,:s,'reappeared',:b,:at) "
                "on conflict do nothing"), {"t": rtype, "h": dh, "s": src, "b": batch_id, "at": at})
            st.reappeared += res.rowcount

    # ------------------------------------------------------------- catalogue scans
    async def record_scan(self, batch_id: str, crawler: str, *, complete: bool, items_listed: int, reason: str | None = None,
                          pages_expected: int | None = None, pages_fetched: int = 0, now: datetime | None = None) -> dict[str, Any]:
        """Store how a full-catalogue source was read. ``complete`` is downgraded when the count is implausibly low."""
        if crawler not in FULL_CATALOGUE_SOURCES:
            return {"recorded": False, "reason": "not a full-catalogue source: absence never means deletion"}
        src, rtype = FULL_CATALOGUE_SOURCES[crawler]
        async with self.db.session() as s:
            prev = (await s.execute(text(
                "select items_listed from catalogue_scans where source_name=:s and complete and batch_id<>:b order by finished_at desc limit 1"),
                {"s": src, "b": batch_id})).first()
            if complete and prev and items_listed < MIN_SCAN_RATIO * prev[0]:
                complete, reason = False, f"listed {items_listed} items, under {int(MIN_SCAN_RATIO * 100)}% of the previous complete scan ({prev[0]})"
            if complete and items_listed == 0:
                complete, reason = False, "listed 0 items"
            sid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"frontieratlas/scan/{batch_id}/{src}"))
            await s.execute(text(
                "insert into catalogue_scans(scan_id,batch_id,source_name,pages_expected,pages_fetched,items_listed,complete,incomplete_reason,finished_at) "
                "values (:id,:b,:s,:pe,:pf,:n,:c,:r,:f) on conflict(batch_id,source_name) do update set pages_fetched=excluded.pages_fetched, "
                "items_listed=excluded.items_listed, complete=excluded.complete, incomplete_reason=excluded.incomplete_reason, finished_at=excluded.finished_at"),
                {"id": sid, "b": batch_id, "s": src, "pe": pages_expected, "pf": pages_fetched, "n": items_listed, "c": complete,
                 "r": None if complete else (reason or "incomplete"), "f": ts(now or datetime.now(UTC))})
        return {"recorded": True, "scan_id": sid, "complete": complete, "reason": None if complete else reason, "record_type": rtype}

    async def detect_disappearances(self, batch_id: str, crawler: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Records listed by the previous COMPLETE scan but not by this COMPLETE scan. Anything else: nothing is written."""
        if crawler not in FULL_CATALOGUE_SOURCES:
            return {"disappeared": 0, "reason": "not a full-catalogue source"}
        src, rtype = FULL_CATALOGUE_SOURCES[crawler]
        at = ts(now or datetime.now(UTC))
        async with self.db.session() as s:
            scan = (await s.execute(text("select scan_id, complete from catalogue_scans where batch_id=:b and source_name=:s"),
                                    {"b": batch_id, "s": src})).first()
            if not scan or not scan[1]:
                return {"disappeared": 0, "reason": "this scan is missing or incomplete"}
            prev = (await s.execute(text(
                "select batch_id from catalogue_scans where source_name=:s and complete and batch_id<>:b order by finished_at desc limit 1"),
                {"s": src, "b": batch_id})).first()
            if not prev:
                return {"disappeared": 0, "reason": "no earlier complete scan to compare with"}
            res = await s.execute(text(
                "insert into presence_events(record_type,dedup_hash,source_name,event,batch_id,scan_id,observed_at) "
                "select o.record_type, o.dedup_hash, o.source_name, 'disappeared', :b, :scan, :at from record_observations o "
                "where o.batch_id=:pb and o.source_name=:s and o.record_type=:t and not exists (select 1 from record_observations n "
                "where n.batch_id=:b and n.source_name=o.source_name and n.record_type=o.record_type and n.dedup_hash=o.dedup_hash) "
                "on conflict do nothing"), {"b": batch_id, "scan": scan[0], "at": at, "pb": prev[0], "s": src, "t": rtype})
        return {"disappeared": res.rowcount, "compared_with_batch": prev[0]}
