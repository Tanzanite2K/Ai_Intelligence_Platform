"""History tables for Phase 7 (approved design, revision 2 of the architecture proposal, 2026-11-22).

Adds twelve new tables and touches no existing table. The tables stay empty until Phase 7B; nothing in the
application reads or writes them while HISTORY_ENABLED is false.

Integrity rules enforced by the database (not only by code):
* retries are idempotent: every per-run table has a unique key that includes the batch;
* at most one open record version and one open resolution decision per subject (partial unique indexes);
* a 'disappeared' presence event must reference a catalogue scan (CHECK); that the scan was complete is
  enforced in code in Phase 7C;
* closed vocabularies (origins, events, tiers, evidence kinds) are CHECK constraints.

Revision ID: 0002_history_tables
Revises: 0001_baseline
Create Date: 2026-11-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_history_tables"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
TS = sa.DateTime(timezone=True)
ZERO = sa.text("0")

# Creation order respects foreign keys; downgrade drops in reverse.
HISTORY_TABLES = (
    "ingestion_batches", "source_groups", "entities", "record_observations", "record_versions", "metric_snapshots",
    "catalogue_scans", "presence_events", "entity_resolution_events", "claims", "evidence_links",
    "source_reliability_daily",
)


def _in(col: str, *values: str) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


def _partial(where: str) -> dict:
    return {"sqlite_where": sa.text(where), "postgresql_where": sa.text(where)}


def upgrade() -> None:
    op.create_table(
        "ingestion_batches",
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("scheduled_for", TS, nullable=False),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("finished_at", TS, nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("code_version", sa.String(64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("batch_id", name="pk_ingestion_batches"),
        sa.UniqueConstraint("trigger", "scheduled_for", name="uq_ingestion_batches_trigger_slot"),
        sa.CheckConstraint(_in("status", "running", "completed", "partial", "failed"), name="ck_ingestion_batches_status"),
    )

    op.create_table(
        "source_groups",
        sa.Column("source_name", sa.String(120), nullable=False),
        sa.Column("source_group", sa.String(64), nullable=False),
        sa.Column("owner", sa.String(200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("source_name", name="pk_source_groups"),
    )

    op.create_table(
        "entities",
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("canonical_name", sa.String(300), nullable=False),
        sa.Column("normalized_key", sa.String(300), nullable=False),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("first_observed_batch", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'active'")),
        sa.Column("merged_into", sa.String(36), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("entity_id", name="pk_entities"),
        sa.ForeignKeyConstraint(["first_observed_batch"], ["ingestion_batches.batch_id"], name="fk_entities_batch"),
        sa.ForeignKeyConstraint(["merged_into"], ["entities.entity_id"], name="fk_entities_merged_into"),
        sa.CheckConstraint(_in("origin", "seed", "discovered", "baseline_backfill"), name="ck_entities_origin"),
        sa.CheckConstraint(_in("status", "active", "merged"), name="ck_entities_status"),
        sa.CheckConstraint("(status = 'merged') = (merged_into IS NOT NULL)", name="ck_entities_merge_target"),
    )
    op.create_index("ux_entities_active_key", "entities", ["normalized_key"], unique=True, **_partial("status = 'active'"))
    op.create_index("ix_entities_domain", "entities", ["domain"])

    op.create_table(
        "record_observations",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("source_name", sa.String(200), nullable=False),
        sa.Column("record_type", sa.String(32), nullable=False),
        sa.Column("dedup_hash", sa.String(64), nullable=False),
        sa.Column("observed_at", TS, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("published_at", TS, nullable=True),
        sa.Column("observation_origin", sa.String(24), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_record_observations"),
        sa.ForeignKeyConstraint(["batch_id"], ["ingestion_batches.batch_id"], name="fk_record_observations_batch"),
        sa.UniqueConstraint("batch_id", "source_name", "record_type", "dedup_hash", name="uq_record_observations_batch_item"),
        sa.CheckConstraint(_in("observation_origin", "live", "baseline_backfill"), name="ck_record_observations_origin"),
    )
    op.create_index("ix_record_observations_record", "record_observations", ["record_type", "dedup_hash", "observed_at"])
    op.create_index("ix_record_observations_source", "record_observations", ["source_name", "observed_at"])

    op.create_table(
        "record_versions",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("record_type", sa.String(32), nullable=False),
        sa.Column("dedup_hash", sa.String(64), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("payload", JSON, nullable=False),
        sa.Column("valid_from", TS, nullable=False),
        sa.Column("valid_to", TS, nullable=True),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("version_origin", sa.String(24), nullable=False),
        sa.Column("changed_fields", JSON, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_record_versions"),
        sa.ForeignKeyConstraint(["batch_id"], ["ingestion_batches.batch_id"], name="fk_record_versions_batch"),
        sa.UniqueConstraint("record_type", "dedup_hash", "version_no", name="uq_record_versions_no"),
        sa.UniqueConstraint("record_type", "dedup_hash", "content_hash", "batch_id", name="uq_record_versions_content_batch"),
        sa.CheckConstraint(_in("version_origin", "baseline_backfill", "observed_change"), name="ck_record_versions_origin"),
        sa.CheckConstraint("version_no >= 1", name="ck_record_versions_no_positive"),
        sa.CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="ck_record_versions_interval"),
    )
    op.create_index("ux_record_versions_open", "record_versions", ["record_type", "dedup_hash"], unique=True, **_partial("valid_to IS NULL"))

    op.create_table(
        "metric_snapshots",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("subject_kind", sa.String(32), nullable=False),
        sa.Column("subject_key", sa.String(300), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("observed_at", TS, nullable=False),
        sa.Column("source_name", sa.String(200), nullable=False),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_metric_snapshots"),
        sa.ForeignKeyConstraint(["batch_id"], ["ingestion_batches.batch_id"], name="fk_metric_snapshots_batch"),
        sa.UniqueConstraint("subject_kind", "subject_key", "metric", "source_name", "batch_id", name="uq_metric_snapshots_batch"),
    )
    op.create_index("ix_metric_snapshots_series", "metric_snapshots", ["subject_kind", "subject_key", "metric", "observed_at"])

    op.create_table(
        "catalogue_scans",
        sa.Column("scan_id", sa.String(36), nullable=False),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("source_name", sa.String(200), nullable=False),
        sa.Column("pages_expected", sa.Integer(), nullable=True),
        sa.Column("pages_fetched", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("items_listed", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("incomplete_reason", sa.Text(), nullable=True),
        sa.Column("finished_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("scan_id", name="pk_catalogue_scans"),
        sa.ForeignKeyConstraint(["batch_id"], ["ingestion_batches.batch_id"], name="fk_catalogue_scans_batch"),
        sa.UniqueConstraint("batch_id", "source_name", name="uq_catalogue_scans_batch_source"),
        sa.CheckConstraint("NOT complete OR incomplete_reason IS NULL", name="ck_catalogue_scans_reason"),
    )

    op.create_table(
        "presence_events",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("record_type", sa.String(32), nullable=False),
        sa.Column("dedup_hash", sa.String(64), nullable=False),
        sa.Column("source_name", sa.String(200), nullable=False),
        sa.Column("event", sa.String(16), nullable=False),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("scan_id", sa.String(36), nullable=True),
        sa.Column("observed_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_presence_events"),
        sa.ForeignKeyConstraint(["batch_id"], ["ingestion_batches.batch_id"], name="fk_presence_events_batch"),
        sa.ForeignKeyConstraint(["scan_id"], ["catalogue_scans.scan_id"], name="fk_presence_events_scan"),
        sa.UniqueConstraint("source_name", "record_type", "dedup_hash", "batch_id", "event", name="uq_presence_events_batch_item"),
        sa.CheckConstraint(_in("event", "appeared", "disappeared", "reappeared"), name="ck_presence_events_event"),
        sa.CheckConstraint("event <> 'disappeared' OR scan_id IS NOT NULL", name="ck_presence_events_disappear_needs_scan"),
    )
    op.create_index("ix_presence_events_record", "presence_events", ["record_type", "dedup_hash", "observed_at"])

    op.create_table(
        "entity_resolution_events",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("raw_name", sa.String(300), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("method", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("decided_by", sa.String(16), nullable=False),
        sa.Column("resolver_version", sa.String(32), nullable=False),
        sa.Column("decided_at", TS, nullable=False),
        sa.Column("superseded_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_entity_resolution_events"),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.entity_id"], name="fk_entity_resolution_events_entity"),
        sa.CheckConstraint(_in("decided_by", "rule", "human"), name="ck_entity_resolution_events_decided_by"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_entity_resolution_events_confidence"),
    )
    op.create_index("ux_entity_resolution_open", "entity_resolution_events", ["raw_name", "source_url"], unique=True, **_partial("superseded_at IS NULL"))
    op.create_index("ix_entity_resolution_entity", "entity_resolution_events", ["entity_id"])

    op.create_table(
        "claims",
        sa.Column("claim_id", sa.String(36), nullable=False),
        sa.Column("claim_kind", sa.String(48), nullable=False),
        sa.Column("subject_entity_id", sa.String(36), nullable=False),
        sa.Column("predicate", sa.String(48), nullable=False),
        sa.Column("object_kind", sa.String(32), nullable=False),
        sa.Column("object_key", sa.String(300), nullable=False),
        sa.Column("value", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("first_observed_at", TS, nullable=False),
        sa.Column("last_observed_at", TS, nullable=False),
        sa.Column("support_tier", sa.String(16), nullable=False),
        sa.Column("independent_groups", sa.Integer(), nullable=False, server_default=ZERO),
        sa.PrimaryKeyConstraint("claim_id", name="pk_claims"),
        sa.ForeignKeyConstraint(["subject_entity_id"], ["entities.entity_id"], name="fk_claims_subject"),
        sa.UniqueConstraint("claim_kind", "subject_entity_id", "predicate", "object_key", "value", name="uq_claims_statement"),
        sa.CheckConstraint(_in("support_tier", "single_source", "corroborated", "conflicting"), name="ck_claims_tier"),
        sa.CheckConstraint("support_tier <> 'corroborated' OR independent_groups >= 2", name="ck_claims_corroborated_needs_two_groups"),
        sa.CheckConstraint("last_observed_at >= first_observed_at", name="ck_claims_interval"),
    )
    op.create_index("ix_claims_subject", "claims", ["subject_entity_id", "predicate"])

    op.create_table(
        "evidence_links",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("claim_id", sa.String(36), nullable=False),
        sa.Column("record_type", sa.String(32), nullable=False),
        sa.Column("dedup_hash", sa.String(64), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("source_name", sa.String(200), nullable=False),
        sa.Column("source_group", sa.String(64), nullable=False),
        sa.Column("primary_url", sa.Text(), nullable=True),
        sa.Column("content_fingerprint", sa.String(64), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("evidence_kind", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_evidence_links"),
        sa.ForeignKeyConstraint(["claim_id"], ["claims.claim_id"], name="fk_evidence_links_claim"),
        sa.ForeignKeyConstraint(["record_type", "dedup_hash", "version_no"], ["record_versions.record_type", "record_versions.dedup_hash", "record_versions.version_no"], name="fk_evidence_links_version"),
        sa.UniqueConstraint("claim_id", "record_type", "dedup_hash", "version_no", name="uq_evidence_links_claim_version"),
        sa.CheckConstraint(_in("evidence_kind", "primary", "reported", "syndicated"), name="ck_evidence_links_kind"),
        sa.CheckConstraint("length(snippet) > 0", name="ck_evidence_links_snippet"),
    )
    op.create_index("ix_evidence_links_claim_group", "evidence_links", ["claim_id", "source_group"])

    op.create_table(
        "source_reliability_daily",
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("runs", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("ok_runs", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("complete_scans", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("items_fetched", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("items_new", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("median_latency_ms", sa.Integer(), nullable=True),
        sa.Column("http_errors", sa.Integer(), nullable=False, server_default=ZERO),
        sa.PrimaryKeyConstraint("source", "day", name="pk_source_reliability_daily"),
        sa.CheckConstraint("ok_runs <= runs", name="ck_source_reliability_ok_runs"),
    )


def downgrade() -> None:
    for name in reversed(HISTORY_TABLES):
        op.drop_table(name)
