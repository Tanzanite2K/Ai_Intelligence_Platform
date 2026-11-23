"""Frontier Intelligence analysis: pure functions over an in-memory index of stored records.

Principles (enforced here, not just in the UI):

* Counts are counts of *stored records*. All records were collected by this platform's crawlers, so record volume
  measures collection, not market growth. Time series use **publication dates** only; records without one are never
  placed on a timeline.
* "Emerging" compares a topic's *share* of a category in a recent window with its share in a baseline window, and is
  only computed when both windows have enough records and day coverage. Otherwise the result says why it cannot be
  assessed.
* Organisation links come from VERIFIED entity mappings (needs_review = false). Organisations named in news articles are
  *co-mentions* (evidence-validated tags) and are always reported separately; no relationship is inferred from them.
* Missing periods are reported as gaps; nothing is interpolated.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Any

from .topics import TOPIC_BY_ID, TOPICS, match_topics

TYPES = ("STARTUP", "PRODUCT", "RESEARCH_PAPER", "JOB", "NEWS")
SLUG = {"STARTUP": "startups", "PRODUCT": "products", "RESEARCH_PAPER": "papers", "JOB": "jobs", "NEWS": "news"}
TYPE_OF_SLUG = {v: k for k, v in SLUG.items()}
LABEL = {"STARTUP": "Startups", "PRODUCT": "Products", "RESEARCH_PAPER": "Research papers", "JOB": "Jobs", "NEWS": "News"}
# Which stored fields are searched for topics, per category (shown in the API/UI so the method is auditable).
TOPIC_FIELDS = {
    "STARTUP": ("description", "tags"),
    "PRODUCT": ("productName", "description (Hugging Face task)"),
    "RESEARCH_PAPER": ("title", "abstract"),
    "JOB": ("title", "role_family"),
    "NEWS": ("title", "summary"),
}

# Emerging-topic test (research-grade defaults; see emerging()).
RECENT_DAYS = 28
BASELINE_DAYS = 56
MIN_WINDOW_RECORDS = 100
MIN_DAY_COVERAGE = 0.5
MIN_TOPIC_RECORDS = 10
SHARE_RATIO = 1.5
Z_THRESHOLD = 2.0


@dataclass(slots=True)
class Rec:
    id: int
    type: str
    name: str
    source: str
    published: date | None
    collected: date | None
    orgs: tuple[str, ...]          # verified mapping (the record's own organisation)
    mentioned: tuple[str, ...]     # news: evidence-validated organisation tags with a verified mapping (co-mentions)
    topics: dict[str, str]         # topic id -> matched term

    @property
    def slug(self) -> str:
        return SLUG[self.type]


@dataclass(slots=True)
class Index:
    recs: list[Rec]
    fingerprint: tuple[Any, ...] = ()
    built_at: datetime | None = None
    by_type: dict[str, list[Rec]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.by_type = {t: [r for r in self.recs if r.type == t] for t in TYPES}


def _as_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.fromisoformat(str(v)).date()
    except ValueError:
        return None


def record_text(rtype: str, content: dict[str, Any]) -> str:
    if rtype == "STARTUP":
        return f"{content.get('description') or ''} ; {' ; '.join(content.get('tags') or [])}"
    if rtype == "PRODUCT":
        return f"{content.get('productName') or ''} ; {content.get('description') or ''}"
    if rtype == "RESEARCH_PAPER":
        return f"{content.get('title') or ''} ; {content.get('abstract') or ''}"
    if rtype == "JOB":
        return f"{content.get('title') or ''} ; {content.get('role_family') or ''}"
    if rtype == "NEWS":
        return f"{content.get('title') or ''} ; {content.get('summary') or ''}"
    return ""


def record_name(rtype: str, content: dict[str, Any], entity_name: str | None) -> str:
    for k in ("title", "productName", "entityName", "entity_name"):
        if content.get(k):
            return str(content[k])
    return entity_name or "Untitled"


def build_index(rows: Iterable[dict[str, Any]], mappings: Iterable[tuple[str | None, str, bool]],
                fingerprint: tuple[Any, ...] = (), now: datetime | None = None) -> Index:
    """rows: id, record_type, content (only the fields used here) or payload, published_at, collected_at, source_name,
    source_url, entity_name. mappings: (source_url, canonical_name, needs_review)."""
    verified: dict[str, list[str]] = defaultdict(list)
    for url, canonical, review in mappings:
        if url and not review and canonical not in verified[url]:
            verified[url].append(canonical)
    recs: list[Rec] = []
    for r in rows:
        rtype = r["record_type"]
        if rtype not in TYPES:
            continue
        content = r.get("content") if r.get("content") is not None else ((r.get("payload") or {}).get("content") or {})
        maps = verified.get(r.get("source_url") or "", [])
        if rtype == "NEWS":
            tags = {str(t).casefold() for t in content.get("organizations") or []}
            mentioned = tuple(c for c in maps if c.casefold() in tags)
            orgs: tuple[str, ...] = ()
        else:
            mentioned = ()
            orgs = tuple(maps)
        recs.append(Rec(
            id=int(r["id"]), type=rtype, name=record_name(rtype, content, r.get("entity_name")), source=r.get("source_name") or "",
            published=_as_date(r.get("published_at")), collected=_as_date(r.get("collected_at")),
            orgs=orgs, mentioned=mentioned, topics=match_topics(record_text(rtype, content)),
        ))
    return Index(recs=recs, fingerprint=fingerprint, built_at=now)


def select(index: Index, types: Sequence[str] | None = None, date_from: date | None = None, date_to: date | None = None) -> tuple[list[Rec], int]:
    """Records in the filter, and how many were excluded only because they have no publication date."""
    pool = [r for t in (types or TYPES) for r in index.by_type.get(t, [])]
    if date_from is None and date_to is None:
        return pool, 0
    keep = [r for r in pool if r.published and (date_from is None or r.published >= date_from) and (date_to is None or r.published <= date_to)]
    undated = sum(1 for r in pool if r.published is None)
    return keep, undated


# ------------------------------------------------------------------ coverage
def _day_runs(days: list[date]) -> list[tuple[date, date]]:
    """Runs of consecutive missing days between the first and last date."""
    gaps: list[tuple[date, date]] = []
    for a, b in pairwise(days):
        if (b - a).days > 1:
            gaps.append((a + timedelta(days=1), b - timedelta(days=1)))
    return gaps


def granularity_support(first: date | None, last: date | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    span = (last - first).days + 1 if first and last else 0
    for g, need, unit in (("day", 7, "7 days"), ("week", 28, "4 weeks"), ("month", 90, "3 months")):
        ok = span >= need
        out[g] = {"supported": ok, "reason": None if ok else (
            "no publication dates" if not span else f"publication dates span {span} day(s); at least {unit} needed")}
    return out


def coverage(index: Index) -> dict[str, Any]:
    cats = []
    collected_days: set[date] = set()
    for t in TYPES:
        recs = index.by_type[t]
        dated = sorted({r.published for r in recs if r.published})
        cdays = sorted({r.collected for r in recs if r.collected})
        collected_days.update(cdays)
        gaps = _day_runs(dated)
        first, last = (dated[0], dated[-1]) if dated else (None, None)
        span = (last - first).days + 1 if first and last else 0
        cats.append({
            "type": SLUG[t], "label": LABEL[t], "records": len(recs), "with_publication_date": sum(1 for r in recs if r.published),
            "publication_first": first, "publication_last": last, "span_days": span,
            "days_with_records": len(dated), "missing_days": sum((b - a).days + 1 for a, b in gaps),
            "largest_gaps": [{"from": a, "to": b, "days": (b - a).days + 1} for a, b in sorted(gaps, key=lambda g: (g[0] - g[1]).days)[:5]],
            "collection_days": [d.isoformat() for d in cdays], "sources": dict(Counter(r.source for r in recs).most_common()),
            "granularity": granularity_support(first, last), "topic_fields": list(TOPIC_FIELDS[t]),
            "with_topic": sum(1 for r in recs if r.topics),
        })
    notes = []
    if len(collected_days) <= 1:
        when = next(iter(collected_days)).isoformat() if collected_days else "unknown"
        notes.append(f"All records were collected on {when}. Record counts describe what was collected, not how activity changed over time.")
    undated = [c["label"] for c in cats if c["records"] and not c["with_publication_date"]]
    if undated:
        notes.append(f"{', '.join(undated)} have no publication or founding dates and cannot be placed on a timeline.")
    short = [c["label"] for c in cats if c["with_publication_date"] and c["span_days"] < 7]
    if short:
        notes.append(f"{', '.join(short)} cover fewer than 7 days of publication dates (crawled with a 24-hour freshness window).")
    return {"categories": cats, "collection_days": sorted(d.isoformat() for d in collected_days), "total_records": len(index.recs), "notes": notes}


# ------------------------------------------------------------------ topics
def topic_frequency(recs: list[Rec]) -> list[dict[str, Any]]:
    per_type_total = Counter(r.type for r in recs)
    by_topic: dict[str, Counter[str]] = {t.id: Counter() for t in TOPICS}
    terms: dict[str, Counter[str]] = {t.id: Counter() for t in TOPICS}
    for r in recs:
        for tid, term in r.topics.items():
            by_topic[tid][r.type] += 1
            terms[tid][term.lower()] += 1
    out = []
    for t in TOPICS:
        c = by_topic[t.id]
        total = sum(c.values())
        out.append({
            "id": t.id, "label": t.label, "records": total, "categories": sum(1 for v in c.values() if v),
            "by_type": {SLUG[k]: c.get(k, 0) for k in TYPES},
            "share_by_type": {SLUG[k]: round(c.get(k, 0) / per_type_total[k], 4) if per_type_total[k] else None for k in TYPES},
            "top_terms": [{"term": k, "records": v} for k, v in terms[t.id].most_common(5)],
        })
    return sorted(out, key=lambda x: (-x["records"], x["label"]))


def _z(r_k: int, r_n: int, b_k: int, b_n: int) -> float:
    p = (r_k + b_k) / (r_n + b_n)
    se = math.sqrt(p * (1 - p) * (1 / r_n + 1 / b_n)) if 0 < p < 1 else 0.0
    return (r_k / r_n - b_k / b_n) / se if se else 0.0


def emerging(index: Index, types: Sequence[str] | None = None, date_to: date | None = None, date_from: date | None = None) -> dict[str, Any]:
    """Share-based change test per category, only where publication-date coverage supports it."""
    results: dict[str, Any] = {}
    for t in types or TYPES:
        recs = [r for r in index.by_type[t] if r.published and (date_from is None or r.published >= date_from) and (date_to is None or r.published <= date_to)]
        if not recs:
            results[SLUG[t]] = {"assessable": False, "reason": "no records with a publication date in this range"}
            continue
        end = max(r.published for r in recs)  # type: ignore[type-var]
        r_start = end - timedelta(days=RECENT_DAYS - 1)
        b_end = r_start - timedelta(days=1)
        b_start = b_end - timedelta(days=BASELINE_DAYS - 1)
        recent = [r for r in recs if r_start <= r.published <= end]  # type: ignore[operator]
        base = [r for r in recs if b_start <= r.published <= b_end]  # type: ignore[operator]
        cov_r = len({r.published for r in recent}) / RECENT_DAYS
        cov_b = len({r.published for r in base}) / BASELINE_DAYS
        window = {"recent": {"from": r_start, "to": end, "records": len(recent), "day_coverage": round(cov_r, 2)},
                  "baseline": {"from": b_start, "to": b_end, "records": len(base), "day_coverage": round(cov_b, 2)}}
        problems = []
        if len(recent) < MIN_WINDOW_RECORDS or len(base) < MIN_WINDOW_RECORDS:
            problems.append(f"each window needs at least {MIN_WINDOW_RECORDS} records (recent {len(recent)}, baseline {len(base)})")
        if cov_r < MIN_DAY_COVERAGE or cov_b < MIN_DAY_COVERAGE:
            problems.append(f"each window needs records on at least {int(MIN_DAY_COVERAGE * 100)}% of its days (recent {cov_r:.0%}, baseline {cov_b:.0%})")
        if problems:
            results[SLUG[t]] = {"assessable": False, "reason": "; ".join(problems), "windows": window}
            continue
        topics = []
        rc = Counter(k for r in recent for k in r.topics)
        bc = Counter(k for r in base for k in r.topics)
        for tp in TOPICS:
            rk, bk = rc.get(tp.id, 0), bc.get(tp.id, 0)
            rs, bs = rk / len(recent), bk / len(base)
            z = _z(rk, len(recent), bk, len(base))
            ratio = rs / bs if bs else (math.inf if rs else None)
            status = "stable"
            if rk >= MIN_TOPIC_RECORDS and z >= Z_THRESHOLD and (ratio is math.inf or (ratio or 0) >= SHARE_RATIO):
                status = "rising"
            elif bk >= MIN_TOPIC_RECORDS and z <= -Z_THRESHOLD and ratio is not None and ratio <= 1 / SHARE_RATIO:
                status = "declining"
            elif rk + bk < MIN_TOPIC_RECORDS:
                status = "too_few_records"
            topics.append({"id": tp.id, "label": tp.label, "recent_records": rk, "baseline_records": bk, "recent_share": round(rs, 4),
                           "baseline_share": round(bs, 4), "share_ratio": None if ratio is None or ratio is math.inf else round(ratio, 2),
                           "z": round(z, 2), "status": status})
        results[SLUG[t]] = {"assessable": True, "windows": window, "topics": sorted(topics, key=lambda x: -x["z"])}
    return {"method": (f"Share of a category's records matching the topic in the last {RECENT_DAYS} days of publication dates versus the "
                       f"preceding {BASELINE_DAYS} days. 'Rising' needs share ratio >= {SHARE_RATIO}, >= {MIN_TOPIC_RECORDS} recent records "
                       f"and a two-proportion z >= {Z_THRESHOLD}. Shares (not counts) are compared, so changes in how many records "
                       "were collected per period do not by themselves create a trend."), "by_type": results}


# ------------------------------------------------------------------ timeline
def bucket_start(d: date, g: str) -> date:
    if g == "day":
        return d
    if g == "week":
        return d - timedelta(days=d.weekday())
    return d.replace(day=1)


def next_bucket(d: date, g: str) -> date:
    if g == "day":
        return d + timedelta(days=1)
    if g == "week":
        return d + timedelta(days=7)
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def timeline(recs: list[Rec], granularity: str, topic: str | None = None) -> dict[str, Any]:
    dated = [r for r in recs if r.published]
    undated = len(recs) - len(dated)
    if not dated:
        return {"granularity": granularity, "supported": False, "reason": "no records with a publication date in this selection",
                "buckets": [], "gaps": [], "undated_excluded": undated}
    first = min(r.published for r in dated)  # type: ignore[type-var]
    last = max(r.published for r in dated)  # type: ignore[type-var]
    support = granularity_support(first, last)[granularity]
    counts: Counter[date] = Counter(bucket_start(r.published, granularity) for r in dated)  # type: ignore[arg-type]
    tcounts: Counter[date] = Counter(bucket_start(r.published, granularity) for r in dated if topic and topic in r.topics)  # type: ignore[arg-type]
    buckets, gaps = [], []
    b = bucket_start(first, granularity)
    run: list[date] = []
    while b <= last:
        nb = next_bucket(b, granularity)
        n = counts.get(b, 0)
        item: dict[str, Any] = {"start": b, "end": nb - timedelta(days=1), "records": n, "has_records": n > 0,
                                "partial": b < first or nb - timedelta(days=1) > last}
        if topic:
            k = tcounts.get(b, 0)
            item.update(topic_records=k, topic_share=round(k / n, 4) if n else None)
        buckets.append(item)
        if n == 0:
            run.append(b)
        elif run:
            gaps.append({"from": run[0], "to": next_bucket(run[-1], granularity) - timedelta(days=1), "buckets": len(run)})
            run = []
        b = nb
    return {"granularity": granularity, "supported": support["supported"], "reason": support["reason"], "first": first, "last": last,
            "buckets": buckets, "gaps": gaps, "undated_excluded": undated, "records": len(dated)}


# ------------------------------------------------------------------ organisations
def org_activity(recs: list[Rec], limit: int = 10) -> dict[str, list[dict[str, Any]]]:
    verified: Counter[str] = Counter(o for r in recs for o in r.orgs)
    mentioned: Counter[str] = Counter(o for r in recs for o in r.mentioned)
    return {"verified": [{"name": n, "records": c} for n, c in verified.most_common(limit)],
            "mentioned": [{"name": n, "articles": c} for n, c in mentioned.most_common(limit)]}


def innovation_map(recs: list[Rec], topic: str | None = None) -> list[dict[str, Any]]:
    """Organisation rows: verified records by category and topic, plus news co-mentions (kept separate)."""
    rows: dict[str, dict[str, Any]] = {}

    def row(name: str) -> dict[str, Any]:
        return rows.setdefault(name, {"name": name, "verified": Counter(), "topics": Counter(), "mentions": 0, "co_mentions": Counter(),
                                      "sample": []})
    for r in recs:
        if topic and topic not in r.topics:
            continue
        for o in r.orgs:
            x = row(o)
            x["verified"][r.slug] += 1
            x["topics"].update(r.topics.keys())
            if len(x["sample"]) < 3:
                x["sample"].append({"type": r.slug, "id": r.id, "name": r.name})
        for o in r.mentioned:
            x = row(o)
            x["mentions"] += 1
            x["co_mentions"].update(m for m in r.mentioned if m != o)
    out = []
    for x in rows.values():
        out.append({"name": x["name"], "verified_records": sum(x["verified"].values()), "by_type": dict(x["verified"]),
                    "topics": [{"id": k, "label": TOPIC_BY_ID[k].label, "records": v} for k, v in x["topics"].most_common(6)],
                    "news_mentions": x["mentions"], "co_mentioned_with": [{"name": k, "articles": v} for k, v in x["co_mentions"].most_common(6)],
                    "sample_records": x["sample"]})
    return sorted(out, key=lambda o: (-o["verified_records"], -o["news_mentions"], o["name"].casefold()))


def topic_org_matrix(rows: list[dict[str, Any]], recs: list[Rec], orgs: int = 12, topics: int = 10) -> dict[str, Any]:
    top_orgs = [o["name"] for o in rows if o["verified_records"]][:orgs]
    tc: Counter[str] = Counter(k for r in recs for k in r.topics if r.orgs)
    top_topics = [k for k, _ in tc.most_common(topics)]
    cells: dict[tuple[str, str], int] = Counter()
    wanted = set(top_orgs)
    for r in recs:
        for o in r.orgs:
            if o in wanted:
                for k in r.topics:
                    cells[(o, k)] += 1
    return {"organisations": top_orgs, "topics": [{"id": k, "label": TOPIC_BY_ID[k].label} for k in top_topics],
            "cells": [[cells.get((o, k), 0) for k in top_topics] for o in top_orgs]}
