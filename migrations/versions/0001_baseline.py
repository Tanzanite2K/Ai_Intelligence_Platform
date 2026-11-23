"""Baseline: the seven tables that existed before Phase 7 (as of 2026-11-22).

Frozen copy of the schema produced by ``Base.metadata.create_all`` for src/database/models.py. It is written out
explicitly (not generated from the models at run time) so later model edits cannot silently change what this
revision means.

Existing databases are NOT upgraded through this revision: they already have these tables and are marked with
``alembic stamp 0001_baseline`` after the schema check in scripts/phase7a_migration_test.py. ``upgrade`` refuses to
run on a database that already has the tables, and ``downgrade`` always refuses, because it would drop production
data.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
TS = sa.DateTime(timezone=True)
BASELINE_TABLES = ("records", "canonical_entities", "entity_mappings", "crawl_checkpoints", "seen_items", "source_runs", "dead_letters")


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names()) & set(BASELINE_TABLES)
    if existing:
        raise RuntimeError(
            f"Baseline tables already exist ({', '.join(sorted(existing))}). This is an existing FrontierAtlas "
            "database: verify its schema and run `alembic stamp 0001_baseline` instead of upgrading."
        )

    op.create_table(
        "records",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("record_type", sa.String(32), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("dedup_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("entity_name", sa.String(300), nullable=True),
        sa.Column("source_name", sa.String(200), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("collected_at", TS, nullable=False),
        sa.Column("published_at", TS, nullable=True),
        sa.Column("date_verified", sa.Boolean(), nullable=True),
        sa.Column("validation_status", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source_priority", sa.Integer(), nullable=False),
        sa.Column("extraction_method", sa.String(32), nullable=False),
        sa.Column("payload", JSON, nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("record_type", "dedup_hash", name="uq_records_type_dedup"),
    )
    op.create_index("ix_records_content_hash", "records", ["content_hash"])
    op.create_index("ix_records_source", "records", ["source_name"])
    op.create_index("ix_records_type_entity", "records", ["record_type", "entity_name"])
    op.create_index("ix_records_type_published", "records", ["record_type", "published_at"])

    op.create_table(
        "canonical_entities",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("canonical_name", sa.String(300), nullable=False),
        sa.Column("normalized_key", sa.String(300), nullable=False),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("aliases", JSON, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_name"),
        sa.UniqueConstraint("normalized_key"),
    )
    op.create_index("ix_canonical_entities_domain", "canonical_entities", ["domain"])

    op.create_table(
        "entity_mappings",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("original_name", sa.String(300), nullable=False),
        sa.Column("canonical_name", sa.String(300), nullable=False),
        sa.Column("match_method", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("entity_kind", sa.String(32), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("resolved_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("original_name", "canonical_name", "source_url", name="uq_entity_mapping"),
    )
    op.create_index("ix_entity_mapping_review", "entity_mappings", ["needs_review"])

    op.create_table(
        "crawl_checkpoints",
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("cursor", JSON, nullable=False),
        sa.Column("last_run_at", TS, nullable=True),
        sa.Column("last_success_at", TS, nullable=True),
        sa.Column("items_seen", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("source"),
    )

    op.create_table(
        "seen_items",
        sa.Column("url_hash", sa.String(64), nullable=False),
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("first_seen_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("url_hash"),
    )
    op.create_index("ix_seen_items_source", "seen_items", ["source"])

    op.create_table(
        "source_runs",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("vertical", sa.String(32), nullable=False),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("finished_at", TS, nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("fetched", sa.Integer(), nullable=False),
        sa.Column("stored", sa.Integer(), nullable=False),
        sa.Column("duplicates", sa.Integer(), nullable=False),
        sa.Column("rejected", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_source_runs_source", "source_runs", ["source"])

    op.create_table(
        "dead_letters",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("key", sa.String(300), nullable=True),
        sa.Column("payload", JSON, nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dead_letters_stage", "dead_letters", ["stage"])


def downgrade() -> None:
    raise RuntimeError(
        "0001_baseline cannot be downgraded: it would drop the production tables and all their data. "
        "To undo everything after the baseline, run `alembic downgrade 0001_baseline`; to roll back a database "
        "completely, restore its pre-migration backup."
    )
