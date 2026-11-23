"""AI news from five publisher feeds, with full-text extraction and date cross-checks.

Flow per item: canonicalise URL -> distributed claim -> feed date (published,
never ``updated``) -> freshness gate -> full text (``content:encoded`` if
complete, else fetch the article; JSON-LD/meta date used when the feed lacks
one) -> NEWS record. Paywalled/blocked articles keep the feed summary and
are flagged ``fullTextAvailable=false`` in evidence rather than faked.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import feedparser

from src.core.dates import extract_publication_date, parse_datetime
from src.core.http import FetchError, RobotsDisallowedError, SourceBlockedError
from src.core.ratelimit import CircuitOpenError
from src.core.schemas import ExtractionMethod, NewsRecord, RecordBase, RecordType
from src.core.text import extract_main_text, html_to_text
from src.core.urls import canonicalize_url
from src.crawlers.base import CrawlContext, SourceStatus, clean_text
from src.crawlers.fresh import FreshSignalCrawler


@dataclass(frozen=True, slots=True)
class FeedSource:
    key: str
    name: str
    feed_url: str
    disabled_reason: str | None = None  # set when the publisher does not permit automated access to the feed


NEWS_SOURCES: list[FeedSource] = [
    FeedSource("techcrunch_ai", "TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    # VentureBeat moved articles to section paths (/orchestration/, /infrastructure/, /technology/, ...);
    # /category/ai/feed/ is a frozen legacy feed (newest item 2026-08-27). /feed/ is the live site feed
    # (robots.txt allows it for User-agent: *). Verified 2026-11-21.
    FeedSource("venturebeat_ai", "VentureBeat", "https://venturebeat.com/feed/"),
    FeedSource("theverge_ai", "The Verge", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    FeedSource("mit_tr_ai", "MIT Technology Review", "https://www.technologyreview.com/topic/artificial-intelligence/feed"),
    FeedSource("arstechnica_ai", "Ars Technica", "https://arstechnica.com/ai/feed/"),
    # extra sources so >=5 stay healthy when one publisher rate-limits (VentureBeat returned 429)
    FeedSource("openai_news", "OpenAI News", "https://openai.com/news/rss.xml"),
    FeedSource("the_decoder", "The Decoder", "https://the-decoder.com/feed/",
               disabled_reason="robots.txt disallows */feed/ for all user agents (checked 2026-11-21); the feed answered HTTP 403. "
                               "Not crawled. Re-enable only with the publisher's written permission or a licensed feed."),
    FeedSource("marktechpost", "MarkTechPost", "https://www.marktechpost.com/feed/"),
]
FULLTEXT_MIN_CHARS = 1200


class RSSNewsCrawler(FreshSignalCrawler):
    vertical = RecordType.NEWS

    def __init__(self, source: FeedSource, fetch_full_text: bool = True) -> None:
        self.name = source.key
        self.source_name = source.name
        super().__init__()
        self.source = source
        self.fetch_full_text = fetch_full_text
        self._articles_paused = False  # set after the publisher rate-limits article pages in this run

    async def crawl(self, ctx: CrawlContext) -> AsyncIterator[RecordBase]:
        self.setup_fresh(ctx)
        if self.source.disabled_reason:
            self.stats.status = SourceStatus.SKIPPED
            self.stats.errors.append(f"source disabled: {self.source.disabled_reason}")
            return
        try:
            res = await ctx.http.get_text(self.source.feed_url)
        except (SourceBlockedError, RobotsDisallowedError) as e:
            self.stats.error(f"feed inaccessible: {e}", inaccessible=True)
            return
        except FetchError as e:
            if e.rate_limited:
                until = self.note_rate_limit(ctx, e.retry_after)
                self.stats.error(f"feed rate-limited by publisher ({e}); next attempt after {until.isoformat()}", fatal=True)
            else:
                self.stats.error(f"feed fetch failed: {e}", fatal=True)
            return
        except CircuitOpenError as e:
            self.stats.error(f"feed fetch failed: {e}", fatal=True)
            return
        feed = feedparser.parse(res.text)
        if feed.bozo and not feed.entries:
            self.stats.error(f"unparseable feed: {feed.bozo_exception!r}", fatal=True)
            return
        entries = [e for e in feed.entries if e.get("link")]
        self.observe_listing([e["link"] for e in entries])
        for e in entries:
            self.stats.fetched += 1
            rec = await self._process(e, ctx)
            if rec:
                yield rec
                if ctx.limit and self.stats.emitted >= ctx.limit:
                    return
        if self.stats.status is SourceStatus.OK and not entries:
            self.stats.status = SourceStatus.PARTIAL

    def _article_error(self, url: str, ex: Exception) -> None:
        """One article page failed. The record is still stored (feed summary, ``fullTextAvailable=false``).

        * 404/410: the publisher removed or moved that page -> a warning, not a source-health problem.
        * 429: stop fetching article pages for the rest of this run (summaries only) -> PARTIAL.
        * blocked/robots/network/5xx -> PARTIAL, with the HTTP status kept in the message.
        """
        msg = _article_error_text(url, ex)
        status = getattr(ex, "status", None)
        if status in (404, 410) and not isinstance(ex, SourceBlockedError):
            self.stats.warn(msg)
            return
        if status == 429:
            self._articles_paused = True
            msg += "; remaining article pages skipped for this run"
        self.stats.error(msg)

    async def _process(self, e: feedparser.FeedParserDict, ctx: CrawlContext) -> RecordBase | None:
        url = canonicalize_url(e["link"])
        key = await self.claim(url)
        if key is None:
            return None
        feed_date = parse_datetime(e.get("published") or e.get("dc_date") or e.get("created"), now=ctx.now, source="rss:pubDate")
        # cheap pre-filter: provably stale -> skip the article fetch entirely
        pre = self.policy.classify(feed_date, ctx.now) if feed_date else None
        if pre is not None and pre.value in ("STALE", "FUTURE"):
            self.decide(feed_date, url, ctx)
            await self.finish_rejected(key)
            return None

        content_html = ""
        if e.get("content"):
            content_html = max((c.get("value", "") for c in e["content"]), key=len)
        text = html_to_text(content_html)
        page_date, fulltext_src = None, "rss:content:encoded" if len(text) >= FULLTEXT_MIN_CHARS else None
        if self.fetch_full_text and not self._articles_paused and (len(text) < FULLTEXT_MIN_CHARS or feed_date is None):
            try:
                page = await ctx.http.get_text(e["link"])
                page_date = extract_publication_date(page.text, url, now=ctx.now)
                body = extract_main_text(page.text)
                if len(body) > len(text):
                    text, fulltext_src = body, "article:html"
            except (FetchError, CircuitOpenError) as ex:
                self._article_error(url, ex)
        pd = feed_date or page_date
        d = self.decide(pd, url, ctx)
        if not d.keep:
            await self.finish_rejected(key)
            return None
        summary = clean_text(html_to_text(e.get("summary", "")), 1500)
        author = clean_text(e.get("author"), 200)
        evidence = {"published_date": f"{pd.source}={pd.iso}" if pd else "none", "fullTextAvailable": str(bool(fulltext_src)).lower()}
        if feed_date and page_date and abs((feed_date.value - page_date.value).total_seconds()) > 3600:
            evidence["dateConflict"] = f"feed={feed_date.iso} page={page_date.iso}"
        return self.build(
            NewsRecord,
            source={"name": self.source_name, "url": url},
            collectedAt=ctx.now,
            content={
                "title": clean_text(e.get("title"), 1000),
                "article_url": url,
                "published_date": d.date,
                "date_verified": d.verified,
                "date_source": d.date_source,
                "author": author,
                "full_text": text[:50_000] if fulltext_src else None,
                "summary": summary,
                "word_count": len(text.split()) if fulltext_src else None,
            },
            provenance={
                "extractionMethod": ExtractionMethod.HTML_PARSER,
                "sourceRecordId": e.get("id") or url,
                "evidence": evidence,
                "validationStatus": "VERIFIED" if d.verified else "UNVERIFIED",
            },
        )


def _article_error_text(url: str, ex: Exception) -> str:
    status = getattr(ex, "status", None)
    detail = f"HTTP {status}" if status else str(ex)[:160] or type(ex).__name__
    return f"article {url}: {type(ex).__name__} ({detail}); stored with feed summary, fullTextAvailable=false"


def build_news_crawlers(sources: list[FeedSource] | None = None) -> list[RSSNewsCrawler]:
    return [RSSNewsCrawler(s) for s in (sources or NEWS_SOURCES)]
