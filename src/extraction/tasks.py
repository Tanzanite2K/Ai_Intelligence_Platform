"""Extraction task definitions used by the pipelines.

Dates are never computed by the LLM: the job task returns the *raw* date
phrase with evidence and the deterministic date parser normalises it.
"""

from __future__ import annotations

from src.extraction.orchestrator import ExtractionTask, FieldSpec

PRODUCT_PRICING = ExtractionTask(
    name="product_pricing",
    instructions=(
        "From this AI product description, classify the pricing model ONLY if the text states it "
        "(FREE = entirely free/open-source; FREEMIUM = free tier plus paid upgrades; PAID = requires payment; "
        "ENTERPRISE = sales-led/custom pricing). Also return the company/maker name only if written."
    ),
    fields={
        "pricing_model": FieldSpec("enum", "pricing model stated in the text", ("FREE", "FREEMIUM", "PAID", "ENTERPRISE")),
        "startup_name": FieldSpec("string", "company or maker that builds the product, if named"),
    },
    max_output_tokens=300,
)

NEWS_ENTITIES = ExtractionTask(
    name="news_entities",
    instructions=(
        "List the AI companies/organisations that are explicitly named in this news article. Do not list the "
        "publisher or website that merely carries the article (bylines, 'appeared first on', copyright, newsletter "
        "or social footers) unless the article itself reports on that organisation."
    ),
    fields={"organizations": FieldSpec("string_list", "organisation names exactly as written")},
    max_output_tokens=600,
)

JOB_POSTING = ExtractionTask(
    name="job_posting",
    instructions=(
        "Extract job-posting facts from this page. For posted_date_text return the date phrase exactly as written "
        "(e.g. 'Posted 3 hours ago', '2026-11-20'); do not convert it."
    ),
    fields={
        "title": FieldSpec("string", "job title"),
        "company": FieldSpec("string", "hiring company"),
        "location": FieldSpec("string", "job location"),
        "is_remote": FieldSpec("boolean", "true if remote work is allowed, false if explicitly on-site/hybrid only"),
        "posted_date_text": FieldSpec("string", "raw publication date phrase"),
    },
    max_output_tokens=400,
)

STARTUP_PROFILE = ExtractionTask(
    name="startup_profile",
    instructions="Extract the company profile facts stated on this page.",
    fields={
        "entity_name": FieldSpec("string", "company name"),
        "employee_count": FieldSpec("integer", "number of employees if stated as a number"),
        "description": FieldSpec("string", "one-sentence description quoted from the page"),
    },
    max_output_tokens=400,
)
