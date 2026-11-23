"""Intelligence Brief: deterministic, evidence-backed insights built only from measured counts.

Every insight states the observation, the supporting counts, organisations/topics involved, the time period and its
basis, links to supporting records, and a confidence level with the reason. No predictions, no market-size claims.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from .analysis import LABEL, SLUG, TYPES, Index, Rec, coverage, emerging, org_activity, topic_frequency

KEYWORD_NOTE = ("Topics are assigned by transparent keyword rules on titles, abstracts, descriptions and tags; a record can match "
                "several topics and the matched term is kept as evidence.")


def _recent_first(recs: list[Rec]) -> list[Rec]:
    return sorted(recs, key=lambda r: (r.published or date.min, r.id), reverse=True)


def _evidence(recs: list[Rec], href: str, n: int = 5) -> dict[str, Any]:
    return {"records": [{"type": r.slug, "id": r.id, "name": r.name, "published": r.published} for r in _recent_first(recs)[:n]],
            "total_supporting": len(recs), "explore_href": href}


def _period(recs: list[Rec], collected: list[str]) -> dict[str, Any]:
    dated = [r.published for r in recs if r.published]
    if dated:
        undated = len(recs) - len(dated)
        basis = "publication dates of the supporting records"
        if undated:
            basis = f"publication dates of {len(dated):,} dated records; {undated:,} undated records collected {', '.join(collected)}"
        return {"from": min(dated), "to": max(dated), "basis": basis}
    return {"from": collected[0] if collected else None, "to": collected[-1] if collected else None,
            "basis": "collection date (these records have no publication date)"}


def _org(name: str) -> dict[str, str]:
    from src.api.v1.entity_ids import encode_entity_id

    return {"name": name, "id": encode_entity_id(name)}


def build_brief(index: Index) -> list[dict[str, Any]]:
    cov = coverage(index)
    collected = cov["collection_days"]
    recs = index.recs
    out: list[dict[str, Any]] = []

    # 1. What the data can and cannot say
    papers = next(c for c in cov["categories"] if c["type"] == "papers")
    out.append({
        "id": "coverage", "title": "Coverage and limits of this evidence",
        "observation": (f"{cov['total_records']:,} records are stored. " + " ".join(cov["notes"]) +
                        (f" Research papers carry publication dates from {papers['publication_first']} to {papers['publication_last']} "
                         f"({papers['days_with_records']} days with papers, {papers['missing_days']} days without)." if papers["with_publication_date"] else "")),
        "metrics": [{"label": c["label"], "value": c["records"]} for c in cov["categories"]],
        "organisations": [], "topics": [], "period": {"from": collected[0] if collected else None, "to": collected[-1] if collected else None, "basis": "collection dates"},
        "evidence": {"records": [], "total_supporting": cov["total_records"], "explore_href": "/intelligence/history"},
        "confidence": {"level": "high", "explanation": "Direct measurement of stored records and their timestamps."},
    })

    freq = topic_frequency(recs)
    # 2. Most frequent topic overall
    top = freq[0]
    top_recs = [r for r in recs if top["id"] in r.topics]
    out.append({
        "id": "top-topic", "title": f"Most frequent topic: {top['label']}",
        "observation": (f"{top['label']} matches {top['records']:,} of {len(recs):,} records, more than any other topic, across "
                        f"{top['categories']} of 5 categories (" + ", ".join(f"{LABEL[t]} {top['by_type'][SLUG[t]]:,}" for t in TYPES if top["by_type"][SLUG[t]]) + ")."),
        "metrics": [{"label": LABEL[t], "value": top["by_type"][SLUG[t]]} for t in TYPES],
        "organisations": [_org(o["name"]) for o in org_activity(top_recs, 3)["verified"]],
        "topics": [{"id": top["id"], "label": top["label"]}], "period": _period(top_recs, collected),
        "evidence": _evidence(top_recs, f"/intelligence/trends/{top['id']}"),
        "confidence": {"level": "medium", "explanation": f"Counts are exact; topic membership is rule-based. {KEYWORD_NOTE}"},
    })

    # 3. Broadest topic (present in the most categories)
    broad = sorted(freq, key=lambda f: (-f["categories"], -f["records"]))[0]
    if broad["id"] != top["id"]:
        b_recs = [r for r in recs if broad["id"] in r.topics]
        out.append({
            "id": "broadest-topic", "title": f"Topic seen in the most categories: {broad['label']}",
            "observation": (f"{broad['label']} appears in {broad['categories']} of 5 categories (" +
                            ", ".join(f"{LABEL[t]} {broad['by_type'][SLUG[t]]:,}" for t in TYPES if broad["by_type"][SLUG[t]]) + ")."),
            "metrics": [{"label": LABEL[t], "value": broad["by_type"][SLUG[t]]} for t in TYPES],
            "organisations": [_org(o["name"]) for o in org_activity(b_recs, 3)["verified"]],
            "topics": [{"id": broad["id"], "label": broad["label"]}], "period": _period(b_recs, collected),
            "evidence": _evidence(b_recs, f"/intelligence/trends/{broad['id']}"),
            "confidence": {"level": "medium", "explanation": f"Category presence is counted exactly; topic membership is rule-based. {KEYWORD_NOTE}"},
        })

    # 4. Research topics whose share changed (only where coverage supports it)
    em = emerging(index, ["RESEARCH_PAPER"])["by_type"]["papers"]
    if not em["assessable"]:
        out.append({
            "id": "research-change", "title": "Emerging research topics cannot be assessed",
            "observation": f"Not enough publication-date coverage to compare periods: {em['reason']}.",
            "metrics": [], "organisations": [], "topics": [], "period": {"from": None, "to": None, "basis": "publication dates"},
            "evidence": {"records": [], "total_supporting": 0, "explore_href": "/intelligence/history?type=papers"},
            "confidence": {"level": "high", "explanation": "Coverage thresholds are checked directly."},
        })
    else:
        w = em["windows"]
        papers_recs = [r for r in index.by_type["RESEARCH_PAPER"] if r.published]
        for status, title in (("rising", "Research topic with a rising share"), ("declining", "Research topic with a falling share")):
            moved = [t for t in em["topics"] if t["status"] == status]
            moved = moved[:3] if status == "rising" else sorted(moved, key=lambda t: t["z"])[:2]
            for t in moved:
                rr = [r for r in papers_recs if t["id"] in r.topics and w["recent"]["from"] <= r.published <= w["recent"]["to"]]  # type: ignore[operator]
                strong = abs(t["z"]) >= 3 and min(t["recent_records"], t["baseline_records"]) >= 30
                out.append({
                    "id": f"research-{status}-{t['id']}", "title": f"{title}: {t['label']}",
                    "observation": (f"{t['label']} accounts for {t['recent_share']:.1%} of research papers published {w['recent']['from']} to "
                                    f"{w['recent']['to']} ({t['recent_records']} of {w['recent']['records']}), versus {t['baseline_share']:.1%} in "
                                    f"{w['baseline']['from']} to {w['baseline']['to']} ({t['baseline_records']} of {w['baseline']['records']})."),
                    "metrics": [{"label": "Recent share", "value": t["recent_share"]}, {"label": "Baseline share", "value": t["baseline_share"]},
                                {"label": "Share ratio", "value": t["share_ratio"]}, {"label": "z", "value": t["z"]}],
                    "organisations": [], "topics": [{"id": t["id"], "label": t["label"]}],
                    "period": {"from": w["baseline"]["from"], "to": w["recent"]["to"], "basis": "publication dates (research papers)"},
                    "evidence": _evidence(rr, f"/intelligence/trends/{t['id']}?types=papers"),
                    "confidence": {"level": "medium" if strong else "low", "explanation": (
                        "Share change is statistically clear (|z| >= 3, >= 30 papers per window)" if strong else "Share change passes the threshold but on modest counts") +
                        ". Papers come only from Hugging Face daily papers with a linked GitHub repository, so this describes that stream, "
                        "not all research. " + KEYWORD_NOTE},
                })
        if not any(t["status"] in ("rising", "declining") for t in em["topics"]):
            out.append({
                "id": "research-stable", "title": "No research topic changed share beyond the threshold",
                "observation": (f"Comparing {w['recent']['from']}–{w['recent']['to']} with {w['baseline']['from']}–{w['baseline']['to']}, "
                                "no topic's share of research papers rose or fell enough to pass the significance test."),
                "metrics": [], "organisations": [], "topics": [], "period": {"from": w["baseline"]["from"], "to": w["recent"]["to"], "basis": "publication dates"},
                "evidence": {"records": [], "total_supporting": w["recent"]["records"] + w["baseline"]["records"], "explore_href": "/intelligence/trends"},
                "confidence": {"level": "medium", "explanation": KEYWORD_NOTE},
            })

    # 5. Most active organisations (verified mappings only)
    acts = org_activity(recs, 5)["verified"]
    if acts:
        names = [a["name"] for a in acts]
        o_recs = [r for r in recs if set(r.orgs) & set(names)]
        per = {n: Counter(r.slug for r in recs if n in r.orgs) for n in names}
        out.append({
            "id": "active-orgs", "title": "Organisations with the most linked records",
            "observation": "Organisations with the most records linked through verified entity mappings: " + "; ".join(
                f"{n} ({sum(per[n].values())}: " + ", ".join(f"{k} {v}" for k, v in per[n].most_common()) + ")" for n in names) + ".",
            "metrics": [{"label": a["name"], "value": a["records"]} for a in acts],
            "organisations": [_org(n) for n in names], "topics": [], "period": _period(o_recs, collected),
            "evidence": _evidence(o_recs, "/intelligence/map"),
            "confidence": {"level": "medium", "explanation": ("Links come from verified mappings (pending reviews excluded). Counts reflect which sources "
                                                             "were crawled (e.g. Hugging Face Hub lists many models per organisation), not company size or market share.")},
        })

    # 6. Organisations leading the top topics
    leaders = []
    for f in freq[:3]:
        t_recs = [r for r in recs if f["id"] in r.topics and r.orgs]
        lead = org_activity(t_recs, 1)["verified"]
        if lead and lead[0]["records"] >= 3:  # a single linked record says nothing about who leads a topic
            leaders.append((f, lead[0], [r for r in t_recs if lead[0]["name"] in r.orgs]))
    if leaders:
        out.append({
            "id": "topic-leaders", "title": "Most linked organisation in each leading topic",
            "observation": "; ".join(f"{f['label']}: {lead['name']} ({lead['records']} linked records)" for f, lead, _ in leaders) + ".",
            "metrics": [{"label": f["label"], "value": lead["records"]} for f, lead, _ in leaders],
            "organisations": [_org(lead["name"]) for _, lead, _ in leaders], "topics": [{"id": f["id"], "label": f["label"]} for f, _, _ in leaders],
            "period": _period([r for _, _, rs in leaders for r in rs], collected),
            "evidence": _evidence([r for _, _, rs in leaders for r in rs], "/intelligence/map"),
            "confidence": {"level": "medium", "explanation": f"Verified mappings only. {KEYWORD_NOTE}"},
        })

    # 7. News co-mentions (never presented as relationships)
    news = [r for r in index.by_type["NEWS"] if r.mentioned]
    if news:
        mentions = Counter(o for r in news for o in r.mentioned)
        pairs = Counter(tuple(sorted((a, b))) for r in news for i, a in enumerate(r.mentioned) for b in r.mentioned[i + 1:])
        pair_txt = f" The most frequent pair is {pairs.most_common(1)[0][0][0]} and {pairs.most_common(1)[0][0][1]} ({pairs.most_common(1)[0][1]} articles)." if pairs else ""
        top_m = mentions.most_common(5)
        out.append({
            "id": "news-mentions", "title": "Organisations most named in news",
            "observation": (f"Across {len(index.by_type['NEWS'])} news articles, the organisations named most often are " +
                            ", ".join(f"{n} ({c})" for n, c in top_m) + "." + pair_txt +
                            " Being named in the same article is a co-mention, not evidence of any partnership, investment or ownership."),
            "metrics": [{"label": n, "value": c} for n, c in top_m],
            "organisations": [_org(n) for n, _ in top_m], "topics": [], "period": _period(news, collected),
            "evidence": _evidence(news, "/news?tagged=true"),
            "confidence": {"level": "low", "explanation": (f"Each tag is backed by a verbatim quote, but only {len(index.by_type['NEWS'])} articles from about one day "
                                                          "were collected, so this is a snapshot, not a trend.")},
        })

    # 8. Hiring snapshot
    jobs = index.by_type["JOB"]
    if jobs:
        hiring = org_activity(jobs, 5)["verified"]
        out.append({
            "id": "jobs-snapshot", "title": "AI-related hiring snapshot",
            "observation": (f"{len(jobs)} AI-related job postings were collected, published within a 24-hour window"
                            + ("; the most postings come from " + ", ".join(f"{h['name']} ({h['records']})" for h in hiring) + "." if hiring else ".")),
            "metrics": [{"label": h["name"], "value": h["records"]} for h in hiring],
            "organisations": [_org(h["name"]) for h in hiring], "topics": [], "period": _period(jobs, collected),
            "evidence": _evidence(jobs, "/jobs"),
            "confidence": {"level": "low", "explanation": "One day of postings from the crawled job boards only; not a measure of overall hiring."},
        })
    return out
