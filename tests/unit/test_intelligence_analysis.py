"""Frontier Intelligence core: topic rules, emerging test, timeline gaps (pure functions, no database)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.intelligence import analysis as A
from src.intelligence.topics import TOPICS, anchor, match_topics


@pytest.mark.parametrize(("text", "topic", "term"), [
    ("We present an agentic framework for web tasks", "agents", "agentic"),
    ("Retrieval-augmented generation with rerankers", "retrieval", "Retrieval-augmented generation"),
    ("image-text-to-text", "multimodal", "image-text-to-text"),
    ("A survey of large language models", "llm", "large language models"),
    ("Dexterous manipulation with humanoid robots", "robotics", "Dexterous manipulation"),
    ("RLHF and preference alignment for chat models", "safety", "preference alignment"),
    ("We introduce a new benchmark for coding agents", "evaluation", "introduce a new benchmark"),
    ("Healthcare IT ; Fintech", "finance", "Fintech"),
])
def test_topic_rules_match_with_evidence(text: str, topic: str, term: str) -> None:
    assert match_topics(text)[topic] == term


@pytest.mark.parametrize(("text", "absent"), [
    ("A travel agent booking flights", "agents"),             # bare "agent" is not a topic
    ("cross-modal feature alignment for retrieval", "safety"),  # alignment in the representation sense
    ("evaluated on standard benchmarks", "evaluation"),       # using a benchmark is not contributing one
    ("Image manipulation detection", "robotics"),
    ("", "llm"),
])
def test_topic_rules_are_conservative(text: str, absent: str) -> None:
    assert absent not in match_topics(text)


def test_prefilter_never_changes_results() -> None:
    samples = ["Multi-agent RAG over vector databases with GPU inference engines", "Open-weight MoE models; KV-cache quantization",
               "text-to-speech ; automatic-speech-recognition", "Self-driving cars and drones", "We curate a dataset of math problems",
               "health care chatbots", "LLMs", "GPT-5.1 and chain-of-thought reasoning", "Cyber security for fintech payments"]
    for s in samples:
        ref = {t.id: m.group(0) for t in TOPICS if (m := t.regex().search(s))}
        assert match_topics(s) == ref


def test_anchor_extraction() -> None:
    assert anchor(r"LLMs?") == "llm" and anchor(r"robots?") == "robot" and anchor(r"health ?care") == "health"
    assert anchor(r"(?:AI|safety) alignment") == "alignment" and anchor(r"quanti[sz]ation") == "quanti"
    assert anchor(r"(?:benchmark|dataset)s?") is None


# ------------------------------------------------------------------ emerging / timeline
END = date(2026, 11, 19)


def paper(i: int, d: date, topics: dict[str, str]) -> A.Rec:
    return A.Rec(id=i, type="RESEARCH_PAPER", name=f"p{i}", source="HF", published=d, collected=date(2026, 11, 21), orgs=(), mentioned=(), topics=topics)


def make_papers(recent_agents: int, base_agents: int, per_day: int = 5) -> list[A.Rec]:
    recs, i = [], 0
    for back in range(84):
        d = END - timedelta(days=back)
        n_agents = recent_agents if back < 28 else base_agents
        for k in range(per_day):
            i += 1
            recs.append(paper(i, d, {"agents": "agentic"} if k < n_agents else {"reasoning": "reasoning"}))
    return recs


def test_emerging_detects_rising_share() -> None:
    idx = A.Index(recs=make_papers(recent_agents=2, base_agents=1))  # 40% vs 20% share
    res = A.emerging(idx, ["RESEARCH_PAPER"])["by_type"]["papers"]
    assert res["assessable"] and res["windows"]["recent"]["records"] == 140 and res["windows"]["baseline"]["records"] == 280
    agents = next(t for t in res["topics"] if t["id"] == "agents")
    assert agents["status"] == "rising" and agents["share_ratio"] == 2.0 and agents["z"] > 2
    assert next(t for t in res["topics"] if t["id"] == "reasoning")["status"] == "stable"  # 80% -> 60%: ratio 0.75, above the 1/1.5 bar


def test_emerging_is_share_based_not_volume_based() -> None:
    """Doubling collection volume in the recent window with the same share must not create a trend."""
    recs = make_papers(recent_agents=1, base_agents=1)
    extra = [paper(10_000 + i, END - timedelta(days=i % 28), {"agents": "agentic"} if i % 5 == 0 else {"reasoning": "reasoning"}) for i in range(140)]
    res = A.emerging(A.Index(recs=recs + extra), ["RESEARCH_PAPER"])["by_type"]["papers"]
    assert next(t for t in res["topics"] if t["id"] == "agents")["status"] == "stable"


def test_emerging_refuses_thin_coverage() -> None:
    one_day = [paper(i, END, {"agents": "agentic"}) for i in range(500)]
    res = A.emerging(A.Index(recs=one_day), ["RESEARCH_PAPER", "STARTUP"])["by_type"]
    assert not res["papers"]["assessable"] and "50%" in res["papers"]["reason"] and "baseline 0" in res["papers"]["reason"]
    assert not res["startups"]["assessable"] and "no records with a publication date" in res["startups"]["reason"]


def test_timeline_reports_gaps_and_never_interpolates() -> None:
    recs = [paper(1, date(2026, 9, 1), {}), paper(2, date(2026, 9, 2), {}), paper(3, date(2026, 9, 29), {"agents": "agentic"})]
    tl = A.timeline(recs, "week")
    assert sum(b["records"] for b in tl["buckets"]) == 3 and tl["supported"]
    empty = [b for b in tl["buckets"] if not b["has_records"]]
    assert len(empty) == 3 and all(b["records"] == 0 for b in empty)
    assert tl["gaps"] == [{"from": date(2026, 9, 7), "to": date(2026, 9, 27), "buckets": 3}]
    m = A.timeline(recs, "month", "agents")
    assert [(b["start"], b["records"], b["topic_records"]) for b in m["buckets"]] == [(date(2026, 9, 1), 3, 1)]
    assert m["supported"] is False and "at least 3 months" in m["reason"]


def test_timeline_partial_and_month_rollover() -> None:
    recs = [paper(1, date(2026, 1, 31), {}), paper(2, date(2026, 4, 2), {})]
    tl = A.timeline(recs, "month")
    assert [b["start"] for b in tl["buckets"]] == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)]
    assert tl["buckets"][0]["partial"] and tl["buckets"][-1]["partial"] and not tl["buckets"][1]["partial"]


def test_select_reports_undated_exclusions() -> None:
    s = A.Rec(id=9, type="STARTUP", name="s", source="YC", published=None, collected=END, orgs=("Acme",), mentioned=(), topics={})
    idx = A.Index(recs=[*make_papers(1, 1)[:3], s])
    kept, undated = A.select(idx, None, END - timedelta(days=3), END)
    assert undated == 1 and all(r.published for r in kept)
    assert A.select(idx)[1] == 0 and len(A.select(idx)[0]) == 4
