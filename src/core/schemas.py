"""Canonical record schemas (assessment "Expected Schemas"), Pydantic v2.

The serialised (camelCase / by-alias) form reproduces the field paths from the
assessment exactly, e.g. ``source.name``, ``content.entityName``,
``content.data.employeeCount``, ``content.github_stars``. Additional fields are
*additive* (provenance, description, website ...) and never change the meaning
of a specified field.

Design rules:
* every record carries ``source.url`` (an absolute http(s) URL) -> provenance;
* ``collectedAt`` (when *we* fetched) is always distinct from any
  publication date (when *they* published);
* optional values are ``None`` when the source did not state them — never
  guessed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = "1.0"


def utcnow() -> datetime:
    return datetime.now(UTC)


def _ensure_utc(v: datetime | None) -> datetime | None:
    if v is None:
        return None
    if v.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return v.astimezone(UTC)


def iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class RecordType(StrEnum):
    STARTUP = "STARTUP"
    PRODUCT = "PRODUCT"
    RESEARCH_PAPER = "RESEARCH_PAPER"
    JOB = "JOB"
    NEWS = "NEWS"


class PricingModel(StrEnum):
    FREE = "FREE"
    FREEMIUM = "FREEMIUM"
    PAID = "PAID"
    ENTERPRISE = "ENTERPRISE"


class ValidationStatus(StrEnum):
    VERIFIED = "VERIFIED"          # every field traced to source evidence
    PARTIAL = "PARTIAL"            # some optional fields dropped for lack of evidence
    UNVERIFIED = "UNVERIFIED"      # e.g. publication date could not be established
    REJECTED = "REJECTED"


class ExtractionMethod(StrEnum):
    STRUCTURED_API = "STRUCTURED_API"   # parsed deterministically from an API/feed
    HTML_PARSER = "HTML_PARSER"         # deterministic DOM/meta parsing
    LLM = "LLM"                         # LLM extraction, evidence-checked


HttpUrlStr = Annotated[str, Field(min_length=8, max_length=2048)]


class _Base(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", str_strip_whitespace=True)


def _validate_url(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    AnyHttpUrl(v)  # raises on invalid
    return v


class Source(_Base):
    name: str = Field(min_length=1, max_length=200)
    url: HttpUrlStr

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _validate_url(v)  # type: ignore[return-value]


class Provenance(_Base):
    extraction_method: ExtractionMethod = Field(alias="extractionMethod")
    llm_provider: str | None = Field(default=None, alias="llmProvider")
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    validation_status: ValidationStatus = Field(alias="validationStatus", default=ValidationStatus.VERIFIED)
    evidence: dict[str, str] = Field(default_factory=dict, description="field -> verbatim source snippet")
    source_record_id: str | None = Field(default=None, alias="sourceRecordId")


class RecordBase(_Base):
    schema_version: str = Field(default=SCHEMA_VERSION, alias="schemaVersion")
    record_type: RecordType = Field(alias="recordType")
    source: Source
    collected_at: datetime = Field(default_factory=utcnow, alias="collectedAt")
    provenance: Provenance

    @field_validator("collected_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _ensure_utc(v)  # type: ignore[return-value]

    @field_serializer("collected_at")
    def _ser_dt(self, v: datetime) -> str | None:
        return iso(v)

    # ---- identity ---------------------------------------------------------
    def natural_key(self) -> str:
        """Stable de-duplication key. Overridden per type."""
        raise NotImplementedError

    def dedup_hash(self) -> str:
        return hashlib.sha256(f"{self.record_type}|{self.natural_key()}".encode()).hexdigest()

    def content_hash(self) -> str:
        payload = json.dumps(self.model_dump(by_alias=True, mode="json")["content"], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()

    def to_canonical(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


# ------------------------------------------------------------------ STARTUP
class StartupData(_Base):
    employee_count: int | None = Field(default=None, ge=0, alias="employeeCount")


class StartupContent(_Base):
    entity_name: str = Field(alias="entityName", min_length=1, max_length=300)
    raw_name: str | None = Field(default=None, alias="rawName")
    data: StartupData = Field(default_factory=StartupData)
    description: str | None = None
    website: str | None = None
    location: str | None = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("website")
    @classmethod
    def _w(cls, v: str | None) -> str | None:
        return _validate_url(v)


class StartupRecord(RecordBase):
    record_type: Literal[RecordType.STARTUP] = Field(default=RecordType.STARTUP, alias="recordType")
    content: StartupContent

    def natural_key(self) -> str:
        # canonical (post entity-resolution) name, so YC + Hugging Face copies of one org collapse
        # canonical (post entity-resolution) name + organisation domain: two different companies that
        # share a name (YC has two 'Contour's) never collide, while copies of one org (same site) still do
        from src.entity_resolution.normalize import compact_key
        from src.entity_resolution.resolver import org_domain

        dom = org_domain(self.content.website)
        return "org:" + compact_key(self.content.entity_name) + (f"|{dom}" if dom else "")


# ------------------------------------------------------------------ PRODUCT
class ProductContent(_Base):
    product_name: str = Field(alias="productName", min_length=1, max_length=300)
    startup_name: str | None = Field(default=None, alias="startupName")
    raw_startup_name: str | None = Field(default=None, alias="rawStartupName")
    pricing_model: PricingModel | None = Field(
        default=None, alias="pricingModel", description="None when the source does not state pricing"
    )
    description: str | None = None
    website: str | None = None

    @field_validator("website")
    @classmethod
    def _w(cls, v: str | None) -> str | None:
        return _validate_url(v)


class ProductRecord(RecordBase):
    record_type: Literal[RecordType.PRODUCT] = Field(default=RecordType.PRODUCT, alias="recordType")
    content: ProductContent

    def natural_key(self) -> str:
        from src.entity_resolution.normalize import compact_key

        return f"{compact_key(self.content.startup_name or '')}|{compact_key(self.content.product_name) or self.content.product_name.casefold()}"


# ----------------------------------------------------------- RESEARCH_PAPER
class ResearchContent(_Base):
    title: str = Field(min_length=3, max_length=1000)
    authors: list[str] = Field(min_length=1)
    paper_url: HttpUrlStr
    github_url: str | None = None
    github_stars: int | None = Field(default=None, ge=0)
    github_stars_checked_at: datetime | None = None
    github_stars_source: str | None = Field(default=None, description="'github_api' (live) or 'huggingface_papers' (cached)")
    published_date: datetime
    arxiv_id: str | None = None
    abstract: str | None = None

    @field_validator("paper_url", "github_url")
    @classmethod
    def _u(cls, v: str | None) -> str | None:
        return _validate_url(v)

    @field_validator("published_date", "github_stars_checked_at")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return _ensure_utc(v)

    @field_serializer("published_date", "github_stars_checked_at")
    def _ser(self, v: datetime | None) -> str | None:
        return iso(v)

    @model_validator(mode="after")
    def _stars_need_repo(self) -> ResearchContent:
        if self.github_stars is not None and not self.github_url:
            raise ValueError("github_stars without github_url is not traceable")
        return self


class ResearchPaperRecord(RecordBase):
    record_type: Literal[RecordType.RESEARCH_PAPER] = Field(default=RecordType.RESEARCH_PAPER, alias="recordType")
    content: ResearchContent

    def natural_key(self) -> str:
        if self.content.arxiv_id:
            return "arxiv:" + self.content.arxiv_id.split("v")[0]
        from src.core.urls import canonicalize_url

        return "url:" + canonicalize_url(self.content.paper_url)


# -------------------------------------------------------- JOB / NEWS (fresh)
class _FreshContentMixin(_Base):
    date_verified: bool = Field(
        default=True, description="False when publication time could not be established from the source"
    )
    date_source: str | None = Field(default=None, description="where the date came from, e.g. 'api:published_at'")


class JobContent(_FreshContentMixin):
    title: str = Field(min_length=2, max_length=500)
    company: str = Field(min_length=1, max_length=300, description="Canonical company name")
    raw_company: str | None = None
    date: datetime | None = Field(description="ISO-8601 publication date")
    is_remote: bool | None = None
    role_family: str
    location: str | None = None
    job_url: HttpUrlStr
    description: str | None = None

    @field_validator("job_url")
    @classmethod
    def _u(cls, v: str) -> str:
        return _validate_url(v)  # type: ignore[return-value]

    @field_validator("date")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return _ensure_utc(v)

    @field_serializer("date")
    def _ser(self, v: datetime | None) -> str | None:
        return iso(v)


class JobRecord(RecordBase):
    record_type: Literal[RecordType.JOB] = Field(default=RecordType.JOB, alias="recordType")
    content: JobContent

    def natural_key(self) -> str:
        from src.core.urls import canonicalize_url

        return canonicalize_url(self.content.job_url)


class NewsContent(_FreshContentMixin):
    title: str = Field(min_length=3, max_length=1000)
    article_url: HttpUrlStr
    published_date: datetime | None
    author: str | None = None
    full_text: str | None = None
    summary: str | None = None
    word_count: int | None = Field(default=None, ge=0)
    organizations: list[str] = Field(default_factory=list)

    @field_validator("article_url")
    @classmethod
    def _u(cls, v: str) -> str:
        return _validate_url(v)  # type: ignore[return-value]

    @field_validator("published_date")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return _ensure_utc(v)

    @field_serializer("published_date")
    def _ser(self, v: datetime | None) -> str | None:
        return iso(v)


class NewsRecord(RecordBase):
    record_type: Literal[RecordType.NEWS] = Field(default=RecordType.NEWS, alias="recordType")
    content: NewsContent

    def natural_key(self) -> str:
        from src.core.urls import canonicalize_url

        return canonicalize_url(self.content.article_url)


AnyRecord = StartupRecord | ProductRecord | ResearchPaperRecord | JobRecord | NewsRecord

RECORD_CLASSES: dict[RecordType, type[RecordBase]] = {
    RecordType.STARTUP: StartupRecord,
    RecordType.PRODUCT: ProductRecord,
    RecordType.RESEARCH_PAPER: ResearchPaperRecord,
    RecordType.JOB: JobRecord,
    RecordType.NEWS: NewsRecord,
}


class EntityMapping(_Base):
    original_name: str = Field(alias="originalName")
    canonical_name: str = Field(alias="canonicalName")
    match_method: str = Field(alias="matchMethod")
    confidence: float = Field(ge=0, le=1)
    resolved_at: datetime = Field(default_factory=utcnow, alias="resolvedAt")
    source_url: str | None = Field(default=None, alias="sourceUrl")
    entity_kind: str = Field(default="ORGANIZATION", alias="entityKind")
    needs_review: bool = Field(default=False, alias="needsReview")

    @field_serializer("resolved_at")
    def _ser(self, v: datetime) -> str | None:
        return iso(v)
