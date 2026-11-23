from datetime import UTC, datetime, timedelta

import pytest

from src.core.dates import (
    Freshness,
    FreshnessPolicy,
    Precision,
    extract_publication_date,
    new_since_last_run,
    parse_datetime,
    parse_relative,
)
from tests.conftest import NOW

H = timedelta(hours=1)


@pytest.mark.parametrize(
    "text,delta,prec",
    [
        ("2 hours ago", 2 * H, Precision.HOUR),
        ("an hour ago", H, Precision.HOUR),
        ("Posted 5 minutes ago", timedelta(minutes=5), Precision.MINUTE),
        ("30 mins ago", timedelta(minutes=30), Precision.MINUTE),
        ("3d ago", timedelta(days=3), Precision.DAY),
        ("1 week ago", timedelta(weeks=1), Precision.DAY),
        ("just now", timedelta(0), Precision.MINUTE),
        ("30+ days ago", timedelta(days=30), Precision.DAY),
        ("5h", 5 * H, Precision.HOUR),
    ],
)
def test_relative_dates(text: str, delta: timedelta, prec: Precision) -> None:
    pd = parse_relative(text, NOW)
    assert pd is not None and pd.value == NOW - delta and pd.precision == prec


def test_yesterday_and_non_relative() -> None:
    assert parse_relative("yesterday", NOW).value == datetime(2026, 11, 20, tzinfo=UTC)  # type: ignore[union-attr]
    assert parse_relative("Senior Engineer", NOW) is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-11-21T10:21:43.702+00:00", datetime(2026, 11, 21, 10, 21, 43, 702000, tzinfo=UTC)),
        ("2026-10-19T11:17:24-04:00", datetime(2026, 10, 19, 15, 17, 24, tzinfo=UTC)),
        ("Fri, 21 Nov 2026 09:30:00 GMT", datetime(2026, 11, 21, 9, 30, tzinfo=UTC)),
        (1795262400, datetime.fromtimestamp(1795262400, UTC)),
        ("1795262400000", datetime.fromtimestamp(1795262400, UTC)),
        ("2026-11-21T08:00:00Z", datetime(2026, 11, 21, 8, tzinfo=UTC)),
    ],
)
def test_absolute_formats_to_utc(value, expected) -> None:  # type: ignore[no-untyped-def]
    pd = parse_datetime(value, now=NOW)
    assert pd is not None and pd.value == expected and pd.value.tzinfo == UTC


def test_date_only_is_day_precision() -> None:
    pd = parse_datetime("2026-11-21", now=NOW)
    assert pd is not None and pd.precision is Precision.DAY


def test_garbage_returns_none() -> None:
    assert parse_datetime("not a date", now=NOW) is None
    assert parse_datetime(None) is None


def test_html_priority_jsonld_over_meta_and_ignores_modified() -> None:
    html = """<html><head>
    <meta property="article:modified_time" content="2026-11-21T11:00:00Z">
    <meta property="article:published_time" content="2026-11-20T09:00:00Z">
    <script type="application/ld+json">{"@type":"NewsArticle","dateModified":"2026-11-21T11:30:00Z","datePublished":"2026-11-21T07:00:00Z"}</script>
    </head><body></body></html>"""
    pd = extract_publication_date(html, now=NOW)
    assert pd is not None and pd.iso == "2026-11-21T07:00:00Z" and pd.source.startswith("jsonld")


def test_html_meta_time_and_url_fallbacks() -> None:
    assert extract_publication_date('<meta property="article:published_time" content="2026-11-21T06:00:00+00:00">', now=NOW).iso == "2026-11-21T06:00:00Z"  # type: ignore[union-attr]
    assert extract_publication_date('<time datetime="2026-11-21T05:00:00Z">x</time>', now=NOW).source.startswith("html:time[datetime]")  # type: ignore[union-attr]
    assert extract_publication_date("<p><time>3 hours ago</time></p>", now=NOW).value == NOW - 3 * H  # type: ignore[union-attr]
    pd = extract_publication_date("<p>no date</p>", "https://site.com/2026/11/20/story", now=NOW)
    assert pd is not None and pd.precision is Precision.DAY and pd.source == "url:path-date"
    assert extract_publication_date("<p>nothing</p>", "https://site.com/story", now=NOW) is None


@pytest.mark.parametrize(
    "dt,prec,expected",
    [
        (NOW - 2 * H, Precision.SECOND, Freshness.FRESH),
        (NOW - 23.9 * H, Precision.SECOND, Freshness.FRESH),
        (NOW - 24.1 * H, Precision.SECOND, Freshness.STALE),
        (NOW + 2 * H, Precision.SECOND, Freshness.FUTURE),
        (NOW - 23.5 * H, Precision.HOUR, Freshness.UNVERIFIED),  # "23 hours ago" +- 1h could be 24.5h
        (NOW - 10 * H, Precision.HOUR, Freshness.FRESH),
        (datetime(2026, 11, 21, tzinfo=UTC), Precision.DAY, Freshness.UNVERIFIED),  # today in unknown tz
        (datetime(2026, 11, 10, tzinfo=UTC), Precision.DAY, Freshness.STALE),
    ],
)
def test_freshness_policy(dt, prec, expected) -> None:  # type: ignore[no-untyped-def]
    from src.core.dates import ParsedDate

    assert FreshnessPolicy().classify(ParsedDate(dt, prec, "t"), NOW) is expected


def test_missing_date_is_unverified() -> None:
    assert FreshnessPolicy().classify(None, NOW) is Freshness.UNVERIFIED


def test_new_since_last_run_heuristic() -> None:
    listing = ["u5", "u4", "u3", "u2", "u1"]
    prev = {"u3", "u2", "u1"}
    assert new_since_last_run(listing, prev, NOW - 2 * H, NOW, timedelta(hours=24)) == {"u5", "u4"}
    assert new_since_last_run(listing, prev, NOW - 30 * H, NOW, timedelta(hours=24)) == set()  # last run too old
    assert new_since_last_run(listing, set(), None, NOW, timedelta(hours=24)) == set()  # first run: cannot vouch
