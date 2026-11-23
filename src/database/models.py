"""SQLAlchemy 2.0 ORM models.

One canonical ``records`` table holds every vertical: the full canonical JSON
lives in ``payload`` (JSONB on PostgreSQL) while hot query fields are promoted
to typed, indexed columns. ``UNIQUE(record_type, dedup_hash)`` is the final,
transactional guard against double-processing — whatever happens in Redis.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JsonType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class RecordRow(Base):
    __tablename__ = "records"
    __table_args__ = (
        UniqueConstraint("record_type", "dedup_hash", name="uq_records_type_dedup"),
        Index("ix_records_type_published", "record_type", "published_at"),
        Index("ix_records_type_entity", "record_type", "entity_name"),
        Index("ix_records_content_hash", "content_hash"),
        Index("ix_records_source", "source_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_type: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    dedup_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_name: Mapped[str | None] = mapped_column(String(300))
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    date_verified: Mapped[bool | None] = mapped_column(Boolean)
    validation_status: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source_priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extraction_method: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CanonicalEntityRow(Base):
    __tablename__ = "canonical_entities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_name: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    aliases: Mapped[list] = mapped_column(JsonType, default=list)
    kind: Mapped[str] = mapped_column(String(32), default="ORGANIZATION")
    origin: Mapped[str] = mapped_column(String(32), default="SEED")  # SEED | DISCOVERED


class EntityMappingRow(Base):
    __tablename__ = "entity_mappings"
    __table_args__ = (
        UniqueConstraint("original_name", "canonical_name", "source_url", name="uq_entity_mapping"),
        Index("ix_entity_mapping_review", "needs_review"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    original_name: Mapped[str] = mapped_column(String(300), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(300), nullable=False)
    match_method: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    entity_kind: Mapped[str] = mapped_column(String(32), default="ORGANIZATION")
    source_url: Mapped[str] = mapped_column(Text, default="")
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CrawlCheckpointRow(Base):
    __tablename__ = "crawl_checkpoints"
    source: Mapped[str] = mapped_column(String(120), primary_key=True)
    cursor: Mapped[dict] = mapped_column(JsonType, default=dict)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    items_seen: Mapped[int] = mapped_column(Integer, default=0)


class SeenItemRow(Base):
    """First-seen registry: powers the 'new since last run' heuristic for undated sources."""

    __tablename__ = "seen_items"
    url_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(120), index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceRunRow(Base):
    __tablename__ = "source_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(120), index=True)
    vertical: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24))  # OK | PARTIAL | INACCESSIBLE | FAILED
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    stored: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class DeadLetterRow(Base):
    __tablename__ = "dead_letters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stage: Mapped[str] = mapped_column(String(64), index=True)
    key: Mapped[str | None] = mapped_column(String(300))
    payload: Mapped[dict] = mapped_column(JsonType)
    error: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
