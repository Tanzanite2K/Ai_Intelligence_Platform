"""Y Combinator AI startups.

Primary path: the public ``yc-oss`` JSON API (a daily mirror of YC's own
public Algolia index, served from GitHub Pages). Every record's
``source.url`` is the company's official page on ycombinator.com.

Secondary path (``use_algolia=True``): query YC's public Algolia index
directly, reading the search-only key embedded in the public directory page —
the same request the browser makes. Used when the mirror is unavailable.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

from src.core.http import FetchError, SourceBlockedError
from src.core.schemas import (
    ExtractionMethod,
    RecordBase,
    RecordType,
    StartupRecord,
)
from src.crawlers.base import BaseCrawler, CrawlContext, SourceStatus, clean_text

YC_OSS_ENDPOINTS = [
    "https://yc-oss.github.io/api/tags/artificial-intelligence.json",
    "https://yc-oss.github.io/api/tags/generative-ai.json",
    "https://yc-oss.github.io/api/tags/machine-learning.json",
    "https://yc-oss.github.io/api/tags/ai.json",
    # industries/artificial-intelligence.json was removed: YC has no "Artificial Intelligence" industry and the file
    # never existed in the mirror (no commit touches that path on gh-pages; it returned HTTP 404 on every run).
    # The four tag endpoints above are the mirror's AI listings (verified 2026-11-21 against the gh-pages tree).
]
_ALGOLIA_OPTS_RE = re.compile(r'"app"\s*:\s*"(?P<app>[A-Z0-9]+)"\s*,\s*"key"\s*:\s*"(?P<key>[^"]+)"')


def yc_company_to_record_kwargs(c: dict[str, Any], *, api_url: str, collected_at: Any) -> dict[str, Any] | None:
    name = clean_text(c.get("name"), 300)
    slug = c.get("slug")
    if not name or not slug:
        return None
    team = c.get("team_size")
    page = c.get("url") or f"https://www.ycombinator.com/companies/{slug}"
    website = c.get("website") or None
    if website and not website.startswith(("http://", "https://")):
        website = "https://" + website
    desc = clean_text(c.get("one_liner")) or clean_text(c.get("long_description"), 2000)
    return {
        "source": {"name": "Y Combinator", "url": page},
        "collectedAt": collected_at,
        "content": {
            "entityName": name,
            "rawName": name,
            "data": {"employeeCount": int(team) if isinstance(team, (int, float)) and team >= 0 else None},
            "description": desc,
            "website": website,
            "location": clean_text(c.get("all_locations"), 300),
            "tags": [t for t in (c.get("tags") or []) if isinstance(t, str)][:20],
        },
        "provenance": {
            "extractionMethod": ExtractionMethod.STRUCTURED_API,
            "sourceRecordId": f"yc:{c.get('id', slug)}",
            "evidence": {"apiEndpoint": api_url, "batch": str(c.get("batch") or "")},
        },
    }


class YCStartupCrawler(BaseCrawler):
    name = "yc_startups"
    source_name = "Y Combinator"
    vertical = RecordType.STARTUP

    def __init__(self, endpoints: list[str] | None = None, use_algolia: bool = True) -> None:
        super().__init__()
        self.endpoints = endpoints or YC_OSS_ENDPOINTS
        self.use_algolia = use_algolia

    async def crawl(self, ctx: CrawlContext) -> AsyncIterator[RecordBase]:
        seen: set[str] = set()
        mirror_ok = False
        for ep in self.endpoints:
            try:
                data = await ctx.http.get_json(ep)
            except (FetchError, json.JSONDecodeError) as e:
                self.stats.error(f"{ep}: {e}")
                continue
            mirror_ok = True
            for c in data if isinstance(data, list) else []:
                self.stats.fetched += 1
                slug = c.get("slug")
                if not slug or slug in seen:
                    continue
                seen.add(slug)
                kw = yc_company_to_record_kwargs(c, api_url=ep, collected_at=ctx.now)
                if kw is None:
                    self.stats.reject("missing_name_or_slug")
                    continue
                rec = self.build(StartupRecord, **kw)
                if rec:
                    yield rec
                    if ctx.limit and self.stats.emitted >= ctx.limit:
                        return
        if not mirror_ok and self.use_algolia:
            async for rec in self._crawl_algolia(ctx, seen):
                yield rec
        if not mirror_ok and self.stats.emitted == 0:
            self.stats.status = SourceStatus.INACCESSIBLE

    async def _crawl_algolia(self, ctx: CrawlContext, seen: set[str]) -> AsyncIterator[RecordBase]:
        try:
            page = await ctx.http.get_text("https://www.ycombinator.com/companies")
        except (FetchError, SourceBlockedError) as e:
            self.stats.error(f"yc directory page: {e}", inaccessible=True)
            return
        m = _ALGOLIA_OPTS_RE.search(page.text)
        if not m:
            self.stats.error("algolia search key not present in YC page", inaccessible=True)
            return
        app, key = m["app"], m["key"]
        url = f"https://{app.lower()}-dsn.algolia.net/1/indexes/*/queries"
        headers = {"x-algolia-application-id": app, "x-algolia-api-key": key}
        # Algolia caps a single query at 1000 hits -> facet by batch to go wider.
        for batch_filter in [None] + [f'batch:"{b}"' for b in ctx.extras.get("yc_batches", [])]:
            for pg in range(0, 50):
                facet = '[["industries:Artificial Intelligence","tags:Artificial Intelligence","tags:Generative AI"]]'
                params = f"hitsPerPage=1000&page={pg}&facetFilters={facet}" + (f"&filters={batch_filter}" if batch_filter else "")
                body = {"requests": [{"indexName": "YCCompany_production", "params": params}]}
                try:
                    res = (await ctx.http.request("POST", url, headers=headers, json_body=body, check_robots=False)).json()
                except (FetchError, json.JSONDecodeError) as e:
                    self.stats.error(f"algolia: {e}")
                    break
                hits = res.get("results", [{}])[0].get("hits", [])
                if not hits:
                    break
                for c in hits:
                    self.stats.fetched += 1
                    if c.get("slug") in seen:
                        continue
                    seen.add(c.get("slug"))
                    kw = yc_company_to_record_kwargs(c, api_url="algolia:YCCompany_production", collected_at=ctx.now)
                    if kw and (rec := self.build(StartupRecord, **kw)):
                        yield rec
                        if ctx.limit and self.stats.emitted >= ctx.limit:
                            return
