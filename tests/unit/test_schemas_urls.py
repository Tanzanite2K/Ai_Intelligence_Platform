from datetime import datetime

import pytest
from pydantic import ValidationError

from src.core.schemas import (
    JobRecord,
    ProductRecord,
    ResearchPaperRecord,
    StartupRecord,
)
from src.core.urls import canonicalize_url, registrable_domain, text_fingerprint, url_hash
from tests.conftest import NOW

PROV = {"extractionMethod": "STRUCTURED_API"}


def startup(**kw):  # type: ignore[no-untyped-def]
    base = {"source": {"name": "Y Combinator", "url": "https://www.ycombinator.com/companies/acme"}, "collectedAt": NOW,
            "content": {"entityName": "Acme", "data": {"employeeCount": 12}}, "provenance": PROV}
    base.update(kw)
    return StartupRecord.model_validate(base)


def test_startup_serialises_exact_assessment_paths() -> None:
    d = startup().to_canonical()
    assert d["schemaVersion"] == "1.0" and d["recordType"] == "STARTUP"
    assert d["source"] == {"name": "Y Combinator", "url": "https://www.ycombinator.com/companies/acme"}
    assert d["content"]["entityName"] == "Acme"
    assert d["content"]["data"]["employeeCount"] == 12
    assert d["collectedAt"] == "2026-11-21T12:00:00Z"


def test_product_enum_and_paths() -> None:
    r = ProductRecord.model_validate({"source": {"name": "PH", "url": "https://www.producthunt.com/posts/x"}, "collectedAt": NOW,
                                      "content": {"productName": "X", "startupName": "Acme", "pricingModel": "FREEMIUM"}, "provenance": PROV})
    d = r.to_canonical()
    assert d["recordType"] == "PRODUCT" and d["content"]["startupName"] == "Acme" and d["content"]["pricingModel"] == "FREEMIUM"
    with pytest.raises(ValidationError):
        ProductRecord.model_validate({**r.model_dump(by_alias=True), "content": {"productName": "X", "pricingModel": "CHEAP"}})


def test_research_requires_tz_and_traceable_stars() -> None:
    base = {"source": {"name": "arXiv", "url": "https://arxiv.org/abs/2601.00001"}, "provenance": PROV,
            "content": {"title": "A paper", "authors": ["A. B"], "paper_url": "https://arxiv.org/abs/2601.00001", "published_date": NOW}}
    r = ResearchPaperRecord.model_validate(base)
    assert r.to_canonical()["content"]["published_date"] == "2026-11-21T12:00:00Z"
    with pytest.raises(ValidationError):  # naive datetime
        ResearchPaperRecord.model_validate({**base, "content": {**base["content"], "published_date": datetime(2026, 1, 1)}})
    with pytest.raises(ValidationError):  # stars without a repo URL are untraceable
        ResearchPaperRecord.model_validate({**base, "content": {**base["content"], "github_stars": 5}})
    with pytest.raises(ValidationError):
        ResearchPaperRecord.model_validate({**base, "content": {**base["content"], "authors": []}})


def test_job_schema_fields() -> None:
    j = JobRecord.model_validate({"source": {"name": "Ashby", "url": "https://jobs.ashbyhq.com/openai/1"}, "provenance": PROV,
                                  "content": {"title": "ML Engineer", "company": "OpenAI", "date": NOW, "is_remote": True,
                                              "role_family": "Engineering", "job_url": "https://jobs.ashbyhq.com/openai/1"}})
    c = j.to_canonical()["content"]
    assert (c["company"], c["date"], c["is_remote"], c["role_family"]) == ("OpenAI", "2026-11-21T12:00:00Z", True, "Engineering")


def test_invalid_source_url_rejected() -> None:
    with pytest.raises(ValidationError):
        startup(source={"name": "x", "url": "not a url at all"})


def test_dedup_hash_stable_and_name_based() -> None:
    a = startup()
    b = startup(collectedAt=datetime(2026, 1, 1, tzinfo=NOW.tzinfo))
    assert a.dedup_hash() == b.dedup_hash()
    assert a.content_hash() == b.content_hash()  # collectedAt is not content


@pytest.mark.parametrize(
    "a,b",
    [
        ("https://www.TechCrunch.com/2026/11/21/story/?utm_source=x&utm_medium=y", "https://techcrunch.com/2026/11/21/story"),
        ("http://example.com:80/a//b/#frag", "https://example.com/a/b"),
        ("https://x.com/p?b=2&a=1&fbclid=zzz", "https://x.com/p?a=1&b=2"),
    ],
)
def test_canonicalize(a: str, b: str) -> None:
    assert canonicalize_url(a) == canonicalize_url(b)
    assert url_hash(a) == url_hash(b)


def test_registrable_domain_and_fingerprint() -> None:
    assert registrable_domain("https://blog.openai.com/x") == "openai.com"
    assert registrable_domain("https://www.bbc.co.uk/news") == "bbc.co.uk"
    assert text_fingerprint("Hello,   World!") == text_fingerprint("hello world")


def test_blank_env_values_mean_unset(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from src.config.settings import Settings

    for k in ("PRODUCTHUNT_TOKEN", "GITHUB_TOKEN", "GEMINI_API_KEY", "REDIS_URL", "GOOGLE_SERVICE_ACCOUNT_FILE", "HF_TOKEN"):
        monkeypatch.setenv(k, "")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.producthunt_token is None and s.github_token is None and s.gemini_api_key is None
    assert s.redis_url is None and s.google_service_account_file is None and s.hf_token is None


def test_sheet_id_accepts_full_url() -> None:
    from src.config.settings import Settings

    url = "https://docs.google.com/spreadsheets/d/1AbC_dEf-123/edit?gid=0#gid=0"
    assert Settings(_env_file=None, google_sheet_id=url).google_sheet_id == "1AbC_dEf-123"  # type: ignore[call-arg]
    assert Settings(_env_file=None, google_sheet_id=" 1AbC ").google_sheet_id == "1AbC"  # type: ignore[call-arg]
