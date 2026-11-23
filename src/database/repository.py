"""Persistence operations: idempotent bulk upserts, checkpoints, logs.

``bulk_upsert`` uses ``INSERT ... ON CONFLICT (record_type, dedup_hash)`` so a
retried or concurrently executed batch can never create duplicates. Rows are
only rewritten when the content hash changed (incremental updates, e.g. new
GitHub star counts).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.core.schemas import (
    EntityMapping,
    JobRecord,
    NewsRecord,
    ProductRecord,
    RecordBase,
    ResearchPaperRecord,
    StartupRecord,
)
from src.database.models import (
    CanonicalEntityRow,
    CrawlCheckpointRow,
    DeadLetterRow,
    EntityMappingRow,
    RecordRow,
    SeenItemRow,
    SourceRunRow,
)
from src.database.session import Database


@dataclass(slots=True)
class UpsertResult:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0

    def __iadd__(self, other: UpsertResult) -> UpsertResult:
        self.inserted += other.inserted
        self.updated += other.updated
        self.unchanged += other.unchanged
        return self


def record_to_row(rec: RecordBase) -> dict[str, Any]:
    published_at = None
    date_verified = None
    entity = None
    if isinstance(rec, StartupRecord):
        entity = rec.content.entity_name
    elif isinstance(rec, ProductRecord):
        entity = rec.content.startup_name or rec.content.product_name
    elif isinstance(rec, ResearchPaperRecord):
        published_at = rec.content.published_date
    elif isinstance(rec, JobRecord):
        published_at, date_verified, entity = rec.content.date, rec.content.date_verified, rec.content.company
    elif isinstance(rec, NewsRecord):
        published_at, date_verified = rec.content.published_date, rec.content.date_verified
    return {
        "record_type": rec.record_type.value,
        "schema_version": rec.schema_version,
        "dedup_hash": rec.dedup_hash(),
        "content_hash": rec.content_hash(),
        "entity_name": entity,
        "source_name": rec.source.name,
        "source_url": rec.source.url,
        "collected_at": rec.collected_at,
        "published_at": published_at,
        "date_verified": date_verified,
        "validation_status": rec.provenance.validation_status.value,
        "confidence": rec.provenance.confidence,
        "extraction_method": rec.provenance.extraction_method.value,
        "payload": rec.to_canonical(),
    }


class Repository:
    def __init__(self, db: Database, batch_size: int = 500) -> None:
        self.db = db
        self.batch_size = batch_size

    def _insert(self, table: Any) -> Any:
        return pg_insert(table) if self.db.dialect == "postgresql" else sqlite_insert(table)

    # ------------------------------------------------------------- records
    async def bulk_upsert(self, records: Sequence[RecordBase], *, priority: int = 0) -> UpsertResult:
        """Idempotent upsert. A row is only overwritten by an equal-or-higher priority source
        whose content differs (richer sources are never clobbered by poorer ones)."""
        total = UpsertResult()
        rows_all = [record_to_row(r) | {"source_priority": priority} for r in records]
        # collapse in-batch duplicates (last wins) so ON CONFLICT never sees the same key twice
        uniq: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows_all:
            uniq[(row["record_type"], row["dedup_hash"])] = row
        rows = list(uniq.values())
        total.unchanged += len(rows_all) - len(rows)
        by_type: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_type.setdefault(row["record_type"], []).append(row)
        for group in by_type.values():
            for i in range(0, len(group), self.batch_size):
                total += await self._upsert_batch(group[i : i + self.batch_size])
        return total

    async def _upsert_batch(self, rows: list[dict[str, Any]]) -> UpsertResult:
        res = UpsertResult()
        async with self.db.session() as s:
            hashes = [r["dedup_hash"] for r in rows]
            rtype = rows[0]["record_type"]
            existing = {
                h: (c, p)
                for h, c, p in (
                    await s.execute(
                        select(RecordRow.dedup_hash, RecordRow.content_hash, RecordRow.source_priority).where(
                            RecordRow.record_type == rtype, RecordRow.dedup_hash.in_(hashes)
                        )
                    )
                ).all()
            }
            for r in rows:
                if r["dedup_hash"] not in existing:
                    res.inserted += 1
                elif existing[r["dedup_hash"]][0] != r["content_hash"] and r["source_priority"] >= existing[r["dedup_hash"]][1]:
                    res.updated += 1
                else:
                    res.unchanged += 1
            stmt = self._insert(RecordRow).values(rows)
            excluded = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=["record_type", "dedup_hash"],
                set_={
                    "content_hash": excluded.content_hash,
                    "payload": excluded.payload,
                    "published_at": excluded.published_at,
                    "date_verified": excluded.date_verified,
                    "validation_status": excluded.validation_status,
                    "confidence": excluded.confidence,
                    "collected_at": excluded.collected_at,
                    "source_name": excluded.source_name,
                    "source_url": excluded.source_url,
                    "entity_name": excluded.entity_name,
                    "source_priority": excluded.source_priority,
                    "updated_at": func.now(),
                },
                where=(RecordRow.content_hash != excluded.content_hash) & (excluded.source_priority >= RecordRow.source_priority),
            )
            await s.execute(stmt)
        return res

    async def fetch_payloads_with_priority(self, record_type: str) -> list[tuple[dict[str, Any], int]]:
        async with self.db.session() as s:
            q = select(RecordRow.payload, RecordRow.source_priority).where(RecordRow.record_type == record_type).order_by(RecordRow.id)
            return [(p, int(pr)) for p, pr in (await s.execute(q)).all()]

    async def replace_records(self, record_type: str, records: Sequence[tuple[RecordBase, int]]) -> dict[str, int]:
        """Atomically replace every row of one type (used after re-keying / re-resolution).

        Rows whose new natural key collides keep the highest-priority (then first) record. One
        transaction: on any failure nothing changes.
        """
        from sqlalchemy import delete

        best: dict[str, tuple[dict[str, Any], int]] = {}
        for rec, prio in records:
            row = record_to_row(rec) | {"source_priority": prio}
            cur = best.get(row["dedup_hash"])
            if cur is None or prio > cur[1]:
                best[row["dedup_hash"]] = (row, prio)
        rows = [r for r, _ in best.values()]
        async with self.db.session() as s:
            before = int((await s.execute(select(func.count()).select_from(RecordRow).where(RecordRow.record_type == record_type))).scalar_one())
            await s.execute(delete(RecordRow).where(RecordRow.record_type == record_type))
            for i in range(0, len(rows), self.batch_size):
                await s.execute(self._insert(RecordRow).values(rows[i : i + self.batch_size]))
        return {"before": before, "after": len(rows), "collapsed_duplicates": len(records) - len(rows)}

    async def replace_mappings(self, mappings: Sequence[EntityMapping]) -> int:
        from sqlalchemy import delete

        async with self.db.session() as s:
            await s.execute(delete(EntityMappingRow))
        return await self.save_mappings(mappings)

    async def count_by_type(self) -> dict[str, int]:
        async with self.db.session() as s:
            rows = (await s.execute(select(RecordRow.record_type, func.count()).group_by(RecordRow.record_type))).all()
        return {k: v for k, v in rows}

    async def fetch_records(
        self,
        record_type: str,
        *,
        published_after: datetime | None = None,
        verified_dates_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        q = select(RecordRow.payload).where(RecordRow.record_type == record_type)
        if published_after is not None:
            q = q.where(RecordRow.published_at >= published_after)
        if verified_dates_only:
            q = q.where(RecordRow.date_verified.is_(True))
        q = q.order_by(RecordRow.id).offset(offset)
        if limit:
            q = q.limit(limit)
        async with self.db.session() as s:
            return [r[0] for r in (await s.execute(q)).all()]

    # ---------------------------------------------------------- entities
    async def upsert_canonical_entities(self, entities: Iterable[dict[str, Any]]) -> None:
        rows = list(entities)
        if not rows:
            return
        async with self.db.session() as s:
            stmt = self._insert(CanonicalEntityRow).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["canonical_name"],
                set_={"aliases": stmt.excluded.aliases, "domain": stmt.excluded.domain},
            )
            await s.execute(stmt)

    async def save_mappings(self, mappings: Sequence[EntityMapping]) -> int:
        if not mappings:
            return 0
        rows = {
            (m.original_name, m.canonical_name, m.source_url or ""): {
                "original_name": m.original_name[:300],
                "canonical_name": m.canonical_name[:300],
                "match_method": m.match_method,
                "confidence": m.confidence,
                "needs_review": m.needs_review,
                "entity_kind": m.entity_kind,
                "source_url": m.source_url or "",
                "resolved_at": m.resolved_at,
            }
            for m in mappings
        }
        vals = list(rows.values())
        async with self.db.session() as s:
            for i in range(0, len(vals), self.batch_size):
                stmt = self._insert(EntityMappingRow).values(vals[i : i + self.batch_size])
                await s.execute(stmt.on_conflict_do_nothing(index_elements=["original_name", "canonical_name", "source_url"]))
        return len(vals)

    async def fetch_mappings(self) -> list[dict[str, Any]]:
        async with self.db.session() as s:
            rows = (await s.execute(select(EntityMappingRow).order_by(EntityMappingRow.id))).scalars().all()
        return [
            {
                "originalName": r.original_name,
                "canonicalName": r.canonical_name,
                "matchMethod": r.match_method,
                "confidence": round(r.confidence, 4),
                "needsReview": r.needs_review,
                "entityKind": r.entity_kind,
                "sourceUrl": r.source_url,
                "resolvedAt": r.resolved_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if r.resolved_at.tzinfo else r.resolved_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            for r in rows
        ]

    # -------------------------------------------------------- checkpoints
    async def get_checkpoint(self, source: str) -> dict[str, Any]:
        async with self.db.session() as s:
            row = await s.get(CrawlCheckpointRow, source)
            return dict(row.cursor or {}) if row else {}

    async def save_checkpoint(self, source: str, cursor: dict[str, Any], *, success: bool, items: int) -> None:
        """Atomic upsert (safe when several workers finish the same source concurrently)."""
        now = datetime.now(UTC)
        row = {"source": source, "cursor": cursor, "last_run_at": now, "last_success_at": now if success else None, "items_seen": items}
        stmt = self._insert(CrawlCheckpointRow).values(row)
        set_: dict[str, Any] = {
            "cursor": stmt.excluded.cursor,
            "last_run_at": stmt.excluded.last_run_at,
            "items_seen": CrawlCheckpointRow.items_seen + stmt.excluded.items_seen,
        }
        if success:
            set_["last_success_at"] = stmt.excluded.last_success_at
        async with self.db.session() as s:
            await s.execute(stmt.on_conflict_do_update(index_elements=["source"], set_=set_))

    # --------------------------------------------------------- seen items
    async def mark_seen(self, items: Sequence[tuple[str, str, str | None]]) -> set[str]:
        """Register (url_hash, source, content_hash) tuples; return url_hashes that were NEW."""
        if not items:
            return set()
        now = datetime.now(UTC)
        hashes = [h for h, _, _ in items]
        async with self.db.session() as s:
            existing = set((await s.execute(select(SeenItemRow.url_hash).where(SeenItemRow.url_hash.in_(hashes)))).scalars())
            rows = [
                {"url_hash": h, "source": src, "content_hash": ch, "first_seen_at": now, "last_seen_at": now}
                for h, src, ch in {i[0]: i for i in items}.values()
            ]
            stmt = self._insert(SeenItemRow).values(rows)
            await s.execute(stmt.on_conflict_do_update(index_elements=["url_hash"], set_={"last_seen_at": now}))
        return set(hashes) - existing

    async def first_seen(self, url_hashes: Sequence[str]) -> dict[str, datetime]:
        async with self.db.session() as s:
            rows = (await s.execute(select(SeenItemRow.url_hash, SeenItemRow.first_seen_at).where(SeenItemRow.url_hash.in_(list(url_hashes))))).all()
        return {h: (t if t.tzinfo else t.replace(tzinfo=UTC)) for h, t in rows}

    # ------------------------------------------------------- run log/DLQ
    async def log_run(self, **kw: Any) -> None:
        async with self.db.session() as s:
            s.add(SourceRunRow(**kw))

    async def fetch_runs(self) -> list[dict[str, Any]]:
        async with self.db.session() as s:
            rows = (await s.execute(select(SourceRunRow).order_by(SourceRunRow.id))).scalars().all()
        return [
            {c: getattr(r, c) for c in ("source", "vertical", "status", "fetched", "stored", "duplicates", "rejected", "error", "started_at", "finished_at")}
            for r in rows
        ]

    async def dead_letter(self, stage: str, key: str | None, payload: dict[str, Any], error: str) -> None:
        async with self.db.session() as s:
            s.add(DeadLetterRow(stage=stage, key=key, payload=payload, error=error[:4000]))

    async def count_dead_letters(self) -> int:
        async with self.db.session() as s:
            return int((await s.execute(select(func.count()).select_from(DeadLetterRow))).scalar_one())
