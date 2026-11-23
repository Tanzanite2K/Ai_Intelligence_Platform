"""Response models for /api/v1. Consistent envelopes: ``{"data": ..., "meta": ...}``; errors ``{"error": {...}}``."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

RecordTypeName = Literal["STARTUP", "PRODUCT", "RESEARCH_PAPER", "JOB", "NEWS"]
Provenance = Literal["source", "enriched", "entity_resolution", "llm_validated", "missing"]


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class Page(BaseModel, Generic[T]):
    data: list[T]
    meta: PageMeta


class Item(BaseModel, Generic[T]):
    data: T


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class OrgRef(BaseModel):
    canonical_name: str
    match_method: str | None = None
    confidence: float | None = None
    needs_review: bool = False


class RecordSummary(BaseModel):
    id: int
    type: RecordTypeName
    name: str | None
    description: str | None = Field(None, description="Truncated to 400 characters")
    organisation: str | None = Field(None, description="Organisation named in the stored record (startup / maker / employer)")
    url: str | None = Field(None, description="Primary link stored for the record (website, paper, job or article URL)")
    source_name: str
    source_url: str
    published_at: datetime | None
    collected_at: datetime
    date_verified: bool | None
    validation_status: str
    extraction_method: str
    github_stars: int | None = None
    tags: list[str] = []
    location: str | None = None
    pricing_model: str | None = None
    is_remote: bool | None = None
    role_family: str | None = None
    authors: list[str] = []
    organizations: list[str] = []


class FieldInfo(BaseModel):
    key: str
    value: Any
    provenance: Provenance
    evidence: str | None = None
    note: str | None = None


class OrgTag(BaseModel):
    """A stored, evidence-validated organisation tag of a news article, with its entity mapping when one exists."""
    name: str
    entity_id: str
    mapped: bool = Field(description="An entity mapping links this article URL to the organisation")
    match_method: str | None = None
    confidence: float | None = None
    needs_review: bool = False


class NewsFacets(BaseModel):
    total: int
    tagged: int
    untagged: int
    organisations: list[dict[str, Any]] = Field(description="Tag name, entity id and number of articles carrying it")


class RecordDetail(RecordSummary):
    fields: list[FieldInfo]
    provenance: dict[str, Any]
    resolved_organisation: OrgRef | None = Field(None, description="Single organisation of startups/products/jobs; null for news (see organisation_tags)")
    organisation_tags: list[OrgTag] = Field([], description="News: all stored validated organisation tags")
    related: list[RecordSummary] = []
    freshness_hours: float | None = Field(None, description="Hours since publication (or collection when undated)")
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Overview(BaseModel):
    records: dict[str, int]
    total_records: int
    organisations: int
    seed_organisations: int
    entity_mappings: int
    pending_reviews: int
    dead_letters: int
    failed_runs: int
    ingestion_runs: int
    last_run_at: datetime | None
    generated_at: datetime


class SearchResult(BaseModel):
    data: list[RecordSummary]
    meta: PageMeta
    counts_by_type: dict[str, int]
    organisations: list[dict[str, Any]]
    query: str | None
    sort: str


class EntitySummary(BaseModel):
    id: str = Field(description="Canonical name (URL-encode in paths)")
    canonical_name: str
    seed_id: int | None = Field(None, description="canonical_entities.id for curated seed organisations")
    origin: Literal["SEED", "DISCOVERED"]
    domain: str | None = None
    aliases: list[str] = []
    mapping_count: int
    record_count: int = Field(description="Records linked through VERIFIED mappings only")
    review_record_count: int = Field(0, description="Records linked only through mappings pending human review")
    needs_review: bool
    match_methods: dict[str, int]


class EntityDetail(EntitySummary):
    records_by_type: dict[str, int]
    review_records_by_type: dict[str, int] = {}
    original_names: list[str]
    mappings: list[dict[str, Any]]
    unavailable: dict[str, str]


class ReviewItem(BaseModel):
    id: int
    original_name: str
    canonical_name: str
    match_method: str
    confidence: float
    source_url: str
    resolved_at: datetime
    status: Literal["PENDING"]
    proposed_candidate: str | None = Field(None, description="Closest existing entity, from the manual audit notes (not stored by the resolver)")
    reason: str | None = Field(None, description="Why the resolver flagged it, from the manual audit notes")
    audit: dict[str, str] | None = Field(None, description="Row of docs/entity_review.md for this case (documentation, not database)")
    candidate_entity: dict[str, Any] | None = Field(None, description="The candidate organisation as stored, if it exists in the mappings")
    shares_canonical_with: int = Field(0, description="VERIFIED mappings stored under the same canonical name as this pending case")
    source_record: RecordSummary | None = None
    note: str


class RunItem(BaseModel):
    id: int
    source: str
    vertical: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_s: float | None
    fetched: int
    stored: int
    duplicates: int
    rejected: int
    error: str | None


class SourceHealth(BaseModel):
    source: str
    vertical: str
    status: str = Field(description="Status of the most recent run")
    last_run_at: datetime | None
    last_success_at: datetime | None
    runs: int
    runs_by_status: dict[str, int]
    rate_limited_runs: int
    access_denied_runs: int
    last_error: str | None
    items_seen: int | None
    disabled_reason: str | None = Field(None, description="Set when the source is excluded from runs (e.g. robots.txt forbids it)")
    action_required: str | None = Field(None, description="What a person must do before this source can recover")
    health: Literal["HEALTHY", "RATE_LIMITED", "CREDENTIAL_REQUIRED", "DISABLED", "PARTIAL", "FAILED", "SKIPPED"] = Field(
        "HEALTHY", description="Operational category derived from configuration, the crawl checkpoint and the latest run")
    health_detail: str | None = Field(None, description="One-line explanation of the category")
    next_attempt_at: datetime | None = Field(None, description="Rate-limit cooldown: the scheduler will not contact the source before this time")
    live_check: dict[str, Any] | None = Field(None, description="Latest read-only diagnostic (scripts/check_sources.py), if one exists")


class DeadLetterItem(BaseModel):
    id: int
    stage: str
    key: str | None
    error: str
    attempts: int
    created_at: datetime
    payload_preview: str
    retry_supported: bool = False
