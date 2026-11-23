"""Typed SQL expressions over the stored record payload (no schema change).

Every record keeps its canonical JSON in ``records.payload``; these helpers expose the
fields that actually exist per record type as portable SQLAlchemy JSON expressions
(SQLite ``json_extract`` / PostgreSQL ``->>``). Only fields present in stored data are
listed here — nothing is synthesised.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Integer, String, and_, case, cast, func, or_
from sqlalchemy.sql.elements import ColumnElement

from src.database.models import RecordRow

# URL slug -> stored record_type
TYPE_SLUGS: dict[str, str] = {
    "startups": "STARTUP",
    "products": "PRODUCT",
    "papers": "RESEARCH_PAPER",
    "jobs": "JOB",
    "news": "NEWS",
}
SLUG_OF: dict[str, str] = {v: k for k, v in TYPE_SLUGS.items()}


def c(*path: str) -> ColumnElement[Any]:
    """``payload.content.<path>`` as text."""
    return RecordRow.payload[("content", *path)].as_string()


def c_int(*path: str) -> ColumnElement[Any]:
    return RecordRow.payload[("content", *path)].as_integer()


def c_bool(*path: str) -> ColumnElement[Any]:
    return RecordRow.payload[("content", *path)].as_boolean()


RT = RecordRow.record_type

# Display name per type (paper/job/news: title; startup: canonical entityName; product: productName)
NAME = case(
    (RT == "STARTUP", c("entityName")),
    (RT == "PRODUCT", c("productName")),
    else_=c("title"),
)
DESCRIPTION = case(
    (RT == "RESEARCH_PAPER", c("abstract")),
    (RT == "NEWS", c("summary")),
    else_=c("description"),
)
# Organisation attached to the record in stored data (startup = itself, product = maker, job = company)
ORGANISATION = case(
    (RT == "STARTUP", c("entityName")),
    (RT == "PRODUCT", c("startupName")),
    (RT == "JOB", c("company")),
    else_=None,
)
STARS = c_int("github_stars")
EFFECTIVE_DATE = func.coalesce(RecordRow.published_at, RecordRow.collected_at)

# Text searched by keyword search (case-insensitive substring). JSON lists (tags, authors, organizations)
# are matched on their JSON text, which contains each element verbatim.
SEARCH_FIELDS: list[ColumnElement[Any]] = [
    RecordRow.entity_name,
    c("title"), c("entityName"), c("rawName"), c("productName"), c("startupName"), c("rawStartupName"), c("company"),
    c("description"), c("abstract"), c("summary"),
    c("tags"), c("authors"), c("organizations"), c("location"), c("role_family"), c("arxiv_id"),
]

# "Category" means different stored fields per type; only these exist in the data.
CATEGORY_FIELD: dict[str, ColumnElement[Any]] = {
    "STARTUP": c("tags"),          # YC/HF tags, e.g. "Computer Vision"
    "PRODUCT": c("description"),   # HF pipeline tag stored as description, e.g. "text-to-video"
    "JOB": c("role_family"),       # e.g. "Engineering"
}


def escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def contains(expr: ColumnElement[Any], term: str) -> ColumnElement[bool]:
    return func.lower(cast(expr, String)).like(f"%{escape_like(term.lower())}%", escape="\\")


def has_org_tag(name: str) -> ColumnElement[bool]:
    """True when the stored, evidence-validated ``content.organizations`` array contains ``name`` as a whole element
    (case-insensitive). Matches the quoted JSON element, so 'Google' never matches 'Google DeepMind' and article text
    is never searched. Both the ASCII-escaped and the raw UTF-8 JSON spellings are accepted."""
    n = name.strip()
    forms = {json.dumps(n.lower()), json.dumps(n.lower(), ensure_ascii=False)}
    return or_(*[contains(c("organizations"), f) for f in forms])


def has_any_org_tag() -> ColumnElement[bool]:
    return and_(c("organizations").is_not(None), c("organizations") != "[]")


def keyword_filter(q: str) -> ColumnElement[bool]:
    """Every whitespace-separated term must occur in at least one searchable field (AND of ORs)."""
    terms = [t for t in q.split() if t][:8]
    return and_(*[or_(*[contains(f, t) for f in SEARCH_FIELDS]) for t in terms])


def category_filter(record_type: str | None, value: str) -> ColumnElement[bool]:
    if record_type in CATEGORY_FIELD:
        expr = CATEGORY_FIELD[record_type]
        return contains(expr, value) if record_type == "STARTUP" else func.lower(expr) == value.lower()
    # cross-type: any of the category-bearing fields
    return or_(
        and_(RT == "STARTUP", contains(c("tags"), value)),
        and_(RT == "PRODUCT", func.lower(c("description")) == value.lower()),
        and_(RT == "JOB", func.lower(c("role_family")) == value.lower()),
    )


def stars_int() -> ColumnElement[Any]:
    return cast(STARS, Integer)
