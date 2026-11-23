"""Phase 7D: claim-specific evidence, independent-source corroboration and confidence (pure functions, no I/O).

A claim is one specific statement about one organisation, e.g. "builds product X" or "is hiring for Engineering".
Rules (from the approved design):
* evidence attaches to a claim only when the record itself states that claim (its own organisation field);
* sources in the same group count once (Hugging Face orgs and products are one group; every company ATS is the
  employer's own channel);
* reposts are not confirmations: job-aggregator copies are ``syndicated``, and news articles whose titles are
  near-duplicates collapse into one story;
* co-mentions in the same article never create a relationship between organisations;
* claims touching an organisation with unresolved entity-resolution reviews are flagged for human review.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

SOURCE_GROUPS = {
    "Y Combinator": "ycombinator", "Hugging Face Hub": "huggingface", "Hugging Face Papers": "huggingface", "Product Hunt": "producthunt",
    "Ashby": "employer_ats", "Greenhouse": "employer_ats", "Lever": "employer_ats", "Himalayas": "job_aggregator", "Remote OK": "job_aggregator",
}
AGGREGATORS = {"job_aggregator"}
BASE = {"primary": 0.6, "reported": 0.45, "syndicated": 0.3}
STEP, CAP, CONFLICT_CAP, REVIEW_FACTOR = 0.15, 0.95, 0.35, 0.7
FORMULA = (f"confidence = base(strongest evidence kind: primary {BASE['primary']}, reported {BASE['reported']}, syndicated {BASE['syndicated']}) "
           f"+ {STEP} x (independent source groups - 1), capped at {CAP}; capped at {CONFLICT_CAP} when independent evidence conflicts; "
           f"x {REVIEW_FACTOR} when the organisation has unresolved entity-resolution reviews")


def source_group(source: str) -> str:
    return SOURCE_GROUPS.get(source, "publisher:" + source.lower())


def _tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 2}


def same_story(a: str, b: str, threshold: float = 0.6) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= threshold


# Hosting/profile platforms are not an organisation's own website and must never create website claims or conflicts.
PLATFORM_HOSTS = {"huggingface.co", "github.com", "ycombinator.com", "producthunt.com", "linkedin.com", "twitter.com", "x.com", "medium.com"}


def domain(url: str | None) -> str | None:
    if not url:
        return None
    m = re.match(r"^(?:https?://)?(?:www\.)?([^/:?#]+)", url.strip().lower())
    host = m.group(1) if m else None
    if host and any(host == p or host.endswith("." + p) for p in PLATFORM_HOSTS):
        return None
    return host


@dataclass
class Evidence:
    record_id: int
    record_type: str
    source: str
    group: str
    kind: str  # primary | reported | syndicated
    snippet: str
    url: str | None = None
    date: str | None = None


@dataclass
class Claim:
    kind: str
    predicate: str
    object: str
    value: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    conflicting_values: list[str] = field(default_factory=list)

    def assess(self, needs_review: bool) -> dict[str, Any]:
        independent = sorted({e.group for e in self.evidence if e.kind != "syndicated"})
        groups = len(independent)
        strongest = "primary" if any(e.kind == "primary" for e in self.evidence) else "reported" if any(e.kind == "reported" for e in self.evidence) else "syndicated"
        conf = min(CAP, BASE[strongest] + STEP * max(0, groups - 1))
        tier = "corroborated" if groups >= 2 else "single_source"
        if self.conflicting_values:
            tier, conf = "conflicting", min(conf, CONFLICT_CAP)
        if needs_review:
            conf *= REVIEW_FACTOR
        return {"kind": self.kind, "predicate": self.predicate, "object": self.object, "value": self.value, "support_tier": tier,
                "independent_source_groups": independent, "evidence_count": len(self.evidence),
                "syndicated_copies": sum(e.kind == "syndicated" for e in self.evidence), "strongest_evidence": strongest,
                "confidence": round(conf, 3), "needs_review": needs_review, "conflicting_values": self.conflicting_values,
                "evidence": [e.__dict__ for e in self.evidence[:25]]}


def build_claims(org: str, recs: list[dict[str, Any]], *, needs_review: bool) -> dict[str, Any]:
    """``recs``: records whose OWN organisation field names ``org`` (startups, products, jobs) plus news articles whose
    evidence-validated tags name it. Each: id, type, source, url, published, content, evidence."""
    claims: dict[tuple[str, str, str], Claim] = {}

    def claim(kind: str, pred: str, obj: str, value: str = "") -> Claim:
        return claims.setdefault((kind, obj, value), Claim(kind, pred, obj, value))

    websites: dict[str, list[Evidence]] = defaultdict(list)
    stories: list[tuple[str, list[Evidence]]] = []
    co_mentions: dict[str, int] = defaultdict(int)
    for r in recs:
        c, src, t = r["content"], r["source"], r["type"]
        g = source_group(src)
        if t == "PRODUCT":
            name = str(c.get("productName") or "")
            claim("builds_product", "builds", name).evidence.append(
                Evidence(r["id"], t, src, g, "primary", f"{src} lists '{name}' under {org}", r.get("url"), r.get("published")))
        elif t == "JOB":
            role = str(c.get("role_family") or "Other")
            claim("hiring", "hires_for", role).evidence.append(
                Evidence(r["id"], t, src, g, "syndicated" if g in AGGREGATORS else "primary", str(c.get("title") or ""), r.get("url"), r.get("published")))
        elif t == "STARTUP":
            ev = Evidence(r["id"], t, src, g, "primary", f"{src} profile of {c.get('entityName')}", r.get("url"), r.get("published"))
            claim("directory_listing", "listed_by", src).evidence.append(ev)
            batch = (r.get("evidence") or {}).get("batch")
            if batch:
                claim("yc_batch", "in_batch", "Y Combinator", str(batch)).evidence.append(
                    Evidence(r["id"], t, src, g, "primary", f"batch: {batch}", r.get("url"), r.get("published")))
            d = domain(c.get("website"))
            if d:
                websites[d].append(Evidence(r["id"], t, src, g, "primary", f"website: {c.get('website')}", r.get("url"), r.get("published")))
        elif t == "NEWS":
            title = str(c.get("title") or "")
            ev = Evidence(r["id"], t, src, g, "reported", title, r.get("url"), r.get("published"))
            for st in stories:
                if same_story(st[0], title):
                    st[1].append(ev)
                    break
            else:
                stories.append((title, [ev]))
            for o in c.get("organizations") or []:
                if isinstance(o, str) and o.casefold() != org.casefold():
                    co_mentions[o] += 1
    # Website: one claim per domain; independent groups that disagree make every website claim conflicting.
    domain_groups = {d: {e.group for e in evs} for d, evs in websites.items()}
    for d, evs in websites.items():
        cl = claim("website", "has_website", d)
        cl.evidence.extend(evs)
        others = [x for x, gs in domain_groups.items() if x != d and gs - domain_groups[d]]
        cl.conflicting_values = sorted(others)
    for title, evs in stories:
        cl = claim("news_coverage", "reported_in_story", title)
        cl.evidence.extend(evs)
    assessed = [c.assess(needs_review) for c in claims.values()]
    order = {"conflicting": 0, "corroborated": 1, "single_source": 2}
    assessed.sort(key=lambda a: (order[a["support_tier"]], -a["evidence_count"], a["kind"], a["object"]))
    return {"claims": assessed,
            "summary": {k: sum(a["support_tier"] == k for a in assessed) for k in order},
            "co_mentions": [{"organisation": o, "articles": n} for o, n in sorted(co_mentions.items(), key=lambda x: (-x[1], x[0]))[:20]],
            "co_mentions_note": "Organisations named in the same articles. Co-mention is not evidence of a partnership, ownership or any other relationship.",
            "confidence_formula": FORMULA}
