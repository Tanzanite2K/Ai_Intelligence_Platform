"""Publication-date extraction, normalisation and 24-hour freshness validation.

Never confuses *collected* time with *published* time: every parser returns a
``ParsedDate`` that records where the value came from and its precision. Only
second/minute-precision dates (or relative dates with an hour-level unit) can
make a record *guaranteed fresh*; day-precision or missing dates are
``UNVERIFIED`` and fall back to the "new since last run" heuristic.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from enum import StrEnum

from dateutil import parser as du_parser
from selectolax.parser import HTMLParser


class Precision(StrEnum):
    SECOND = "SECOND"
    MINUTE = "MINUTE"
    HOUR = "HOUR"
    DAY = "DAY"


@dataclass(frozen=True, slots=True)
class ParsedDate:
    value: datetime  # always tz-aware UTC
    precision: Precision
    source: str  # e.g. "jsonld:datePublished", "relative:'2 hours ago'"

    @property
    def iso(self) -> str:
        return self.value.strftime("%Y-%m-%dT%H:%M:%SZ")


class Freshness(StrEnum):
    FRESH = "FRESH"                  # provably published within the window
    STALE = "STALE"                  # provably older than the window
    UNVERIFIED = "UNVERIFIED"        # cannot be established -> excluded from guaranteed set
    FUTURE = "FUTURE"                # clock skew / bad metadata (> tolerance in the future)


_REL_UNITS = {
    "s": ("seconds", Precision.SECOND), "sec": ("seconds", Precision.SECOND), "second": ("seconds", Precision.SECOND),
    "m": ("minutes", Precision.MINUTE), "min": ("minutes", Precision.MINUTE), "minute": ("minutes", Precision.MINUTE),
    "h": ("hours", Precision.HOUR), "hr": ("hours", Precision.HOUR), "hour": ("hours", Precision.HOUR),
    "d": ("days", Precision.DAY), "day": ("days", Precision.DAY),
    "w": ("weeks", Precision.DAY), "wk": ("weeks", Precision.DAY), "week": ("weeks", Precision.DAY),
    "mo": ("months", Precision.DAY), "month": ("months", Precision.DAY),
    "y": ("years", Precision.DAY), "yr": ("years", Precision.DAY), "year": ("years", Precision.DAY),
}
_REL_RE = re.compile(
    r"(?P<num>\d+|an?|one)(?P<plus>\+)?\s*(?P<unit>seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|wks?|w|months?|mo|years?|yrs?|y)\b\.?\s*(ago)?",
    re.I,
)
_DATE_ONLY_RE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s*$")
_URL_DATE_RE = re.compile(r"/(20\d{2})/(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])(?:/|$)")


def _utc(dt: datetime, assume_tz=UTC) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=assume_tz)
    return dt.astimezone(UTC)


def parse_relative(text: str, now: datetime) -> ParsedDate | None:
    t = text.strip().lower()
    t = re.sub(r"^(posted|published|updated|active)\s*(on)?\s*", "", t)
    if t in {"just now", "now", "moments ago", "a moment ago", "just posted"}:
        return ParsedDate(now, Precision.MINUTE, f"relative:{text!r}")
    if t in {"today", "posted today"}:
        return ParsedDate(now.replace(hour=0, minute=0, second=0, microsecond=0), Precision.DAY, f"relative:{text!r}")
    if t == "yesterday":
        d = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return ParsedDate(d, Precision.DAY, f"relative:{text!r}")
    m = _REL_RE.search(t)
    if not m or ("ago" not in t and not re.fullmatch(r"\d+\s*[a-z]+", t)):
        return None
    n_raw = m.group("num")
    n = 1 if n_raw in ("a", "an", "one") else int(n_raw)
    unit_key = m.group("unit").rstrip(".")
    unit_key = unit_key if unit_key in _REL_UNITS else unit_key.rstrip("s")
    if unit_key not in _REL_UNITS:
        return None
    unit, prec = _REL_UNITS[unit_key]
    if unit == "months":
        delta = timedelta(days=30 * n)
    elif unit == "years":
        delta = timedelta(days=365 * n)
    else:
        delta = timedelta(**{unit: n})
    if m.group("plus"):  # "30+ days ago" -> at least that old
        prec = Precision.DAY
    return ParsedDate(now - delta, prec, f"relative:{text!r}")


def parse_datetime(value: str | int | float | None, *, now: datetime | None = None, source: str = "value") -> ParsedDate | None:
    """Parse ISO-8601, RFC-2822, epoch (s/ms), relative phrases, or loose date strings."""
    now = now or datetime.now(UTC)
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"\d{10}(\d{3})?", value.strip())):
        v = float(value)
        if v > 1e12:
            v /= 1000
        return ParsedDate(datetime.fromtimestamp(v, UTC), Precision.SECOND, f"{source}:epoch")
    s = str(value).strip()
    rel = parse_relative(s, now)
    if rel:
        return rel
    if _DATE_ONLY_RE.match(s):
        return ParsedDate(datetime.fromisoformat(s).replace(tzinfo=UTC), Precision.DAY, f"{source}:date-only")
    try:
        return ParsedDate(_utc(datetime.fromisoformat(s.replace("Z", "+00:00"))), Precision.SECOND, f"{source}:iso8601")
    except ValueError:
        pass
    try:
        return ParsedDate(_utc(parsedate_to_datetime(s)), Precision.SECOND, f"{source}:rfc2822")
    except (TypeError, ValueError, IndexError):
        pass
    try:
        dt = du_parser.parse(s, fuzzy=False)
    except (ValueError, OverflowError):
        return None
    has_time = bool(re.search(r"\d{1,2}:\d{2}", s))
    return ParsedDate(_utc(dt), Precision.MINUTE if has_time else Precision.DAY, f"{source}:loose")


_META_KEYS = [
    ("property", "article:published_time"),
    ("property", "og:published_time"),
    ("name", "article:published_time"),
    ("itemprop", "datePublished"),
    ("name", "pubdate"),
    ("name", "publishdate"),
    ("name", "publish-date"),
    ("name", "parsely-pub-date"),
    ("name", "sailthru.date"),
    ("name", "dc.date.issued"),
    ("name", "DC.date.issued"),
    ("name", "date"),
]


def _walk_jsonld(obj: object) -> list[str]:
    out: list[str] = []
    if isinstance(obj, dict):
        for k in ("datePublished", "datePosted", "dateCreated", "uploadDate"):
            if isinstance(obj.get(k), str):
                out.append(obj[k])
        for v in obj.values():
            out.extend(_walk_jsonld(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_walk_jsonld(v))
    return out


def extract_publication_date(html: str, url: str | None = None, *, now: datetime | None = None) -> ParsedDate | None:
    """Best-evidence publication date from an HTML document.

    Priority: JSON-LD > publisher meta tags > ``<time datetime>`` > URL path date
    (day precision). Explicitly ignores ``dateModified``/``og:updated_time``.
    """
    now = now or datetime.now(UTC)
    tree = HTMLParser(html)
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.text() or "null")
        except json.JSONDecodeError:
            continue
        for v in _walk_jsonld(data):
            pd = parse_datetime(v, now=now, source="jsonld:datePublished")
            if pd:
                return pd
    for attr, key in _META_KEYS:
        node = tree.css_first(f'meta[{attr}="{key}"]')
        if node and node.attributes.get("content"):
            pd = parse_datetime(node.attributes["content"], now=now, source=f"meta:{key}")
            if pd:
                return pd
    for node in tree.css("time[datetime]"):
        pd = parse_datetime(node.attributes.get("datetime"), now=now, source="html:time[datetime]")
        if pd:
            return pd
    for node in tree.css("time"):
        pd = parse_relative(node.text(strip=True), now)
        if pd:
            return pd
    if url:
        m = _URL_DATE_RE.search(url)
        if m:
            d = datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=UTC)
            return ParsedDate(d, Precision.DAY, "url:path-date")
    return None


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    window: timedelta = timedelta(hours=24)
    future_tolerance: timedelta = timedelta(minutes=15)

    def classify(self, pd: ParsedDate | None, now: datetime | None = None) -> Freshness:
        now = now or datetime.now(UTC)
        if pd is None:
            return Freshness.UNVERIFIED
        if pd.value - now > self.future_tolerance:
            return Freshness.FUTURE
        cutoff = now - self.window
        if pd.precision is Precision.DAY:
            # A calendar date D (unknown tz, UTC-12..UTC+14) spans [D-14h, D+1d+12h].
            earliest = pd.value - timedelta(hours=14)
            latest = pd.value + timedelta(days=1, hours=12)
            if latest < cutoff:
                return Freshness.STALE
            if earliest >= cutoff:
                return Freshness.FRESH
            return Freshness.UNVERIFIED
        # relative "N hours ago" carries +-1 unit of rounding error
        slack = timedelta(hours=1) if pd.precision is Precision.HOUR else timedelta(0)
        if pd.value - slack >= cutoff:
            return Freshness.FRESH
        if pd.value + slack < cutoff:
            return Freshness.STALE
        return Freshness.UNVERIFIED


def new_since_last_run(
    listing_urls: list[str], previously_seen: set[str], last_success_at: datetime | None, now: datetime, window: timedelta
) -> set[str]:
    """Heuristic for sources without dates.

    For a newest-first listing, items *above* the first previously-seen item
    that were not seen on a successful run less than ``window`` ago must have
    appeared since that run -> published within the window. Returns the URLs
    the heuristic can vouch for (empty on a first run or a stale last run).
    """
    if last_success_at is None or now - last_success_at > window or not previously_seen:
        return set()
    out: set[str] = set()
    for u in listing_urls:
        if u in previously_seen:
            break
        out.add(u)
    return out
