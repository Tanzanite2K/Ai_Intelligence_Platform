"""AI organisations active on the Hugging Face Hub (second startup source).

Discovers organisation authors of the most-liked models, keeps only
organisations (verified via ``/api/organizations/{name}/overview``), and
emits them as STARTUP records with the Hub org page as provenance. Employee
count is not published by the Hub and is therefore left ``None``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from src.core.http import FetchError, HttpClient
from src.core.schemas import ExtractionMethod, RecordBase, RecordType, StartupRecord
from src.crawlers.base import BaseCrawler, CrawlContext, clean_text

HF = "https://huggingface.co"


class HFOrgResolver:
    """Org/user check shared by HF-based crawlers, cached on disk across runs.

    One lookup per author is the main source of Hugging Face 429s, so results
    (org profile or "not an org") are persisted to ``cache_path``. Transient
    failures are *not* cached, so they are retried on the next run.
    """

    def __init__(self, http: HttpClient, cache_path: str | Path | None = "data/hf_org_cache.json") -> None:
        self.http = http
        self.path = Path(cache_path) if cache_path else None
        self._cache: dict[str, dict[str, Any] | None] = {}
        self._dirty = 0
        if self.path and self.path.exists():
            try:
                self._cache = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._cache = {}

    async def org(self, name: str) -> dict[str, Any] | None:
        if name in self._cache:
            return self._cache[name]
        try:
            res = await self.http.request("GET", f"{HF}/api/organizations/{name}/overview", raise_for_status=False, max_retries=2)
        except FetchError:
            return None  # transient: do not cache
        if res.status == 200:
            try:
                self._cache[name] = res.json()
            except ValueError:
                return None
        elif res.status == 404:
            self._cache[name] = None  # a user, not an organisation
        else:
            return None  # 429/5xx after retries: do not cache
        self._dirty += 1
        if self._dirty >= 25:
            self.save()
        return self._cache[name]

    def save(self) -> None:
        if self.path and self._dirty:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._cache), encoding="utf-8")
            tmp.replace(self.path)
            self._dirty = 0


class HuggingFaceOrgCrawler(BaseCrawler):
    name = "hf_orgs"
    source_name = "Hugging Face Hub"
    vertical = RecordType.STARTUP

    def __init__(self, pages: int = 10, page_size: int = 1000, exclude: set[str] | None = None) -> None:
        super().__init__()
        self.pages, self.page_size = pages, page_size
        # big-tech / academic orgs are not startups; resolved but excluded here
        self.exclude = exclude or set()

    async def crawl(self, ctx: CrawlContext) -> AsyncIterator[RecordBase]:
        resolver: HFOrgResolver = ctx.extras.setdefault("hf_org_resolver", HFOrgResolver(ctx.http, ctx.settings.hf_org_cache_path))
        seen: set[str] = set()
        url: str | None = ctx.checkpoint.get("next_url") or f"{HF}/api/models?sort=likes&direction=-1&limit={self.page_size}"
        try:
            async for rec in self._pages(ctx, resolver, seen, url):
                yield rec
                if ctx.limit and self.stats.emitted >= ctx.limit:
                    return
        finally:
            resolver.save()

    async def _pages(self, ctx: CrawlContext, resolver: HFOrgResolver, seen: set[str], url: str | None) -> AsyncIterator[RecordBase]:
        for _ in range(self.pages):
            if not url:
                break
            try:
                res = await ctx.http.request("GET", url)
            except FetchError as e:
                self.stats.error(str(e))
                break
            models = res.json()
            url = _next_link(res.headers.get("Link") or res.headers.get("link"))
            self.next_checkpoint["next_url"] = url  # resume point if the run is interrupted
            for m in models:
                self.stats.fetched += 1
                author = m.get("author") or (m.get("id", "").split("/")[0] if "/" in m.get("id", "") else None)
                if not author or author in seen or author.lower() in self.exclude:
                    continue
                seen.add(author)
                org = await resolver.org(author)
                if not org:
                    self.stats.reject("not_an_organization")
                    continue
                full = clean_text(org.get("fullname")) or author
                rec = self.build(
                    StartupRecord,
                    source={"name": self.source_name, "url": f"{HF}/{author}"},
                    collectedAt=ctx.now,
                    content={
                        "entityName": full,
                        "rawName": full,
                        "data": {"employeeCount": None},
                        "description": clean_text(org.get("details") or org.get("description"), 2000),
                        "website": None,
                        "tags": ["huggingface-org"] + (["enterprise"] if org.get("isEnterprise") else []),
                    },
                    provenance={
                        "extractionMethod": ExtractionMethod.STRUCTURED_API,
                        "sourceRecordId": f"hf-org:{author}",
                        "evidence": {"hubHandle": author, "discoveredVia": m.get("id", "")},
                    },
                )
                if rec:
                    yield rec
        if url is None:
            self.next_checkpoint["next_url"] = None  # listing exhausted: next run starts from the top


def _next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="next"' in part:
            return part[part.find("<") + 1 : part.find(">")]
    return None
