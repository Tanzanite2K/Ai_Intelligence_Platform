"""Crawler registry: name -> factory, grouped by vertical, with source priority."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from src.crawlers.base import BaseCrawler
from src.crawlers.jobs.boards import AshbyCrawler, GreenhouseCrawler, HimalayasCrawler, LeverCrawler, RemoteOKCrawler
from src.crawlers.news.rss import NEWS_SOURCES, RSSNewsCrawler
from src.crawlers.products.huggingface import HuggingFaceProductCrawler
from src.crawlers.products.producthunt import ProductHuntCrawler
from src.crawlers.research.crawler import ResearchPaperCrawler
from src.crawlers.startups.huggingface_orgs import HuggingFaceOrgCrawler
from src.crawlers.startups.yc import YCStartupCrawler


@dataclass(frozen=True, slots=True)
class CrawlerSpec:
    name: str
    vertical: str
    factory: Callable[[], BaseCrawler]
    priority: int = 0  # higher = richer/more authoritative; wins on upsert conflicts
    domain_rps: dict[str, float] | None = None
    disabled_reason: str | None = None  # excluded from default runs; still reported by Source Health


def _news(i: int) -> Callable[[], BaseCrawler]:
    return lambda: RSSNewsCrawler(NEWS_SOURCES[i])


REGISTRY: dict[str, CrawlerSpec] = {
    s.name: s
    for s in [
        CrawlerSpec("yc_startups", "startups", YCStartupCrawler, priority=10),
        CrawlerSpec("hf_orgs", "startups", HuggingFaceOrgCrawler, priority=5),
        CrawlerSpec("producthunt", "products", ProductHuntCrawler, priority=10),
        CrawlerSpec("hf_products", "products", HuggingFaceProductCrawler, priority=5),
        CrawlerSpec("research_papers", "research", ResearchPaperCrawler, priority=10),
        *[CrawlerSpec(src.key, "news", _news(i), priority=10, disabled_reason=src.disabled_reason) for i, src in enumerate(NEWS_SOURCES)],
        CrawlerSpec("greenhouse", "jobs", GreenhouseCrawler, priority=10),
        CrawlerSpec("ashby", "jobs", AshbyCrawler, priority=10),
        CrawlerSpec("lever", "jobs", LeverCrawler, priority=10),
        CrawlerSpec("remoteok", "jobs", RemoteOKCrawler, priority=5),
        CrawlerSpec("himalayas", "jobs", HimalayasCrawler, priority=5),
    ]
}

VERTICALS = ("startups", "products", "research", "news", "jobs")

# What a person must do when a source cannot be fixed in code. Shown by Source Health while the source is unhealthy.
ACTION_REQUIRED: dict[str, str] = {
    "producthunt": (
        "Product Hunt rejects the configured PRODUCTHUNT_TOKEN (invalid_oauth_token). Create or open your app at "
        "https://api.producthunt.com/v2/oauth/applications, copy its Developer Token (not the API Key or API Secret) "
        "into PRODUCTHUNT_TOKEN in .env, then run scripts/check_sources.py --only producthunt. See docs/SOURCE_HEALTH.md."
    ),
}

# Polite per-domain request rates (requests/second). arXiv asks for 1 request / 3 s.
DOMAIN_RPS = {
    "export.arxiv.org": 0.34,
    "api.github.com": 10.0,
    "huggingface.co": 2.0,
    "yc-oss.github.io": 5.0,
    "remoteok.com": 0.5,
    "himalayas.app": 1.0,
    "api.producthunt.com": 1.0,
}


def specs_for(vertical: str | None = None, names: list[str] | None = None) -> list[CrawlerSpec]:
    if names:
        missing = [n for n in names if n not in REGISTRY]
        if missing:
            raise KeyError(f"unknown crawler(s): {missing}; known: {sorted(REGISTRY)}")
        return [REGISTRY[n] for n in names]
    return [s for s in REGISTRY.values() if vertical in (None, "all", s.vertical) and not s.disabled_reason]
