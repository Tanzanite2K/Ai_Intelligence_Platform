"""Paper metadata sources: arXiv API (authoritative) and Hugging Face Papers.

Hugging Face Papers is the successor of Papers with Code (PwC's site and API
were retired in 2025 and now redirect to huggingface.co/papers); it links an
arXiv id to its code repository (``githubRepo``) — the PwC paper→code
correlation the assessment asks for.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import feedparser

from src.core.dates import parse_datetime
from src.core.http import FetchError, HttpClient
from src.crawlers.research.github import find_github_urls, normalize_github_url

ARXIV_API = "https://export.arxiv.org/api/query"
HF_API = "https://huggingface.co/api"
_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?$")


@dataclass
class PaperCandidate:
    arxiv_id: str
    title: str
    authors: list[str]
    published: datetime | None
    abstract: str | None
    paper_url: str
    source_name: str
    source_url: str
    github_url: str | None = None
    github_url_evidence: str | None = None
    hf_stars: int | None = None
    extra: dict[str, str] = field(default_factory=dict)


def parse_arxiv_feed(xml: str) -> list[PaperCandidate]:
    feed = feedparser.parse(xml)
    out: list[PaperCandidate] = []
    for e in feed.entries:
        m = _ARXIV_ID_RE.search(e.get("id", ""))
        if not m:
            continue
        aid = m.group(1)
        title = " ".join(e.get("title", "").split())
        authors = [a.get("name", "").strip() for a in e.get("authors", []) if a.get("name")]
        pub = parse_datetime(e.get("published"), source="arxiv:published")
        abstract = " ".join(e.get("summary", "").split()) or None
        comment = e.get("arxiv_comment", "") or ""
        links = " ".join(lk.get("href", "") for lk in e.get("links", []))
        gh, ev = None, None
        for field_name, text in (("arxiv:comment", comment), ("arxiv:abstract", abstract or ""), ("arxiv:links", links)):
            found = find_github_urls(text)
            if found:
                gh, ev = found[0], f"{field_name}: {found[0]}"
                break
        out.append(
            PaperCandidate(
                arxiv_id=aid, title=title, authors=authors, published=pub.value if pub else None, abstract=abstract,
                paper_url=f"https://arxiv.org/abs/{aid}", source_name="arXiv", source_url=f"https://arxiv.org/abs/{aid}",
                github_url=gh, github_url_evidence=ev,
            )
        )
    return out


async def iter_arxiv(
    http: HttpClient, categories: list[str], *, page_size: int = 200, max_results: int = 5000, start: int = 0
) -> AsyncIterator[tuple[int, list[PaperCandidate]]]:
    """Page through arXiv newest-first. arXiv asks for <=1 request / 3 s (set in domain_rps)."""
    q = " OR ".join(f"cat:{c}" for c in categories)
    offset = start
    while offset < start + max_results:
        params = {"search_query": q, "sortBy": "submittedDate", "sortOrder": "descending", "start": str(offset), "max_results": str(page_size)}
        res = await http.get_text(ARXIV_API, params=params)
        batch = parse_arxiv_feed(res.text)
        if not batch:
            return
        offset += page_size
        yield offset, batch


def parse_hf_daily(items: list[dict]) -> list[PaperCandidate]:
    out: list[PaperCandidate] = []
    for it in items:
        p = it.get("paper") or {}
        aid = p.get("id")
        if not aid or not _ARXIV_ID_RE.search(aid):
            continue
        authors = [a.get("name", "").strip() for a in p.get("authors", []) if a.get("name")]
        pub = parse_datetime(p.get("publishedAt"), source="hf:publishedAt")
        gh = normalize_github_url(p.get("githubRepo"))
        stars = p.get("githubStars")
        out.append(
            PaperCandidate(
                arxiv_id=aid, title=" ".join((p.get("title") or it.get("title") or "").split()), authors=authors,
                published=pub.value if pub else None, abstract=" ".join((p.get("summary") or "").split()) or None,
                paper_url=f"https://arxiv.org/abs/{aid}", source_name="Hugging Face Papers",
                source_url=f"https://huggingface.co/papers/{aid}",
                github_url=gh, github_url_evidence=f"hf:githubRepo: {p.get('githubRepo')}" if gh else None,
                hf_stars=int(stars) if isinstance(stars, int) and stars >= 0 else None,
            )
        )
    return out


async def iter_hf_daily(http: HttpClient, start: date, days: int) -> AsyncIterator[tuple[date, list[PaperCandidate]]]:
    for i in range(days):
        d = start - timedelta(days=i)
        try:
            items = await http.get_json(f"{HF_API}/daily_papers", params={"date": d.isoformat(), "limit": "100"})
        except FetchError:
            continue
        yield d, parse_hf_daily(items if isinstance(items, list) else [])


async def hf_paper_repo(http: HttpClient, arxiv_id: str) -> tuple[str | None, int | None]:
    res = await http.request("GET", f"{HF_API}/papers/{arxiv_id}", raise_for_status=False, max_retries=1)
    if res.status != 200:
        return None, None
    data = res.json()
    stars = data.get("githubStars")
    return normalize_github_url(data.get("githubRepo")), stars if isinstance(stars, int) else None
