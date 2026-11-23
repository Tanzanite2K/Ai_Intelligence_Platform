"""AI products published by organisations on the Hugging Face Hub.

Two product types: models (open-weight releases) and Spaces (hosted AI apps).
Only organisation-owned artefacts are kept so ``startupName`` is a company,
not an individual. Pricing is set *only* from explicit evidence:

* model with an open licence tag (``license:apache-2.0`` ...) -> ``FREE``
  (weights are downloadable at no cost; the licence tag is stored as evidence);
* everything else -> ``None`` (the pipeline may later ask the LLM to classify
  pricing from text, and only accepts it with a verbatim evidence quote).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from src.core.http import FetchError
from src.core.schemas import ExtractionMethod, PricingModel, ProductRecord, RecordBase, RecordType
from src.crawlers.base import BaseCrawler, CrawlContext, clean_text
from src.crawlers.startups.huggingface_orgs import HF, HFOrgResolver, _next_link

OPEN_LICENSES = {
    "apache-2.0", "mit", "bsd-3-clause", "bsd-2-clause", "bsd", "cc-by-4.0", "cc-by-sa-4.0", "cc0-1.0",
    "openrail", "openrail++", "creativeml-openrail-m", "bigscience-openrail-m", "llama2", "llama3", "llama3.1",
    "llama3.2", "llama3.3", "llama4", "gemma", "gpl-3.0", "lgpl-3.0", "agpl-3.0", "mpl-2.0", "artistic-2.0", "unlicense",
}


def license_of(tags: list[str]) -> str | None:
    for t in tags or []:
        if isinstance(t, str) and t.startswith("license:"):
            return t.split(":", 1)[1]
    return None


class HuggingFaceProductCrawler(BaseCrawler):
    name = "hf_products"
    source_name = "Hugging Face Hub"
    vertical = RecordType.PRODUCT

    def __init__(self, kinds: tuple[str, ...] = ("models", "spaces"), pages: int = 10, page_size: int = 1000) -> None:
        super().__init__()
        self.kinds, self.pages, self.page_size = kinds, pages, page_size

    async def crawl(self, ctx: CrawlContext) -> AsyncIterator[RecordBase]:
        resolver: HFOrgResolver = ctx.extras.setdefault("hf_org_resolver", HFOrgResolver(ctx.http, ctx.settings.hf_org_cache_path))
        try:
            async for rec in self._crawl_kinds(ctx, resolver):
                yield rec
                if ctx.limit and self.stats.emitted >= ctx.limit:
                    return
        finally:
            resolver.save()

    async def _crawl_kinds(self, ctx: CrawlContext, resolver: HFOrgResolver) -> AsyncIterator[RecordBase]:
        for kind in self.kinds:
            url: str | None = ctx.checkpoint.get(f"next_url:{kind}") or f"{HF}/api/{kind}?sort=likes&direction=-1&limit={self.page_size}&full=true"
            for _ in range(self.pages):
                if not url:
                    break
                try:
                    res = await ctx.http.request("GET", url)
                except FetchError as e:
                    self.stats.error(f"{kind}: {e}")
                    break
                url = _next_link(res.headers.get("Link") or res.headers.get("link"))
                self.next_checkpoint[f"next_url:{kind}"] = url
                for item in res.json():
                    self.stats.fetched += 1
                    rec = await self._to_record(kind, item, resolver, ctx)
                    if rec:
                        yield rec
            # reset only when the listing is exhausted; after an error (or the page cap) keep the cursor to resume
            if url is None:
                self.next_checkpoint[f"next_url:{kind}"] = None

    async def _to_record(self, kind: str, item: dict[str, Any], resolver: HFOrgResolver, ctx: CrawlContext) -> RecordBase | None:
        repo_id = item.get("id") or item.get("modelId")
        if not repo_id or "/" not in repo_id:
            self.stats.reject("no_repo_id")
            return None
        author, short = repo_id.split("/", 1)
        org = await resolver.org(author)
        if not org:
            self.stats.reject("author_not_organization")
            return None
        company = clean_text(org.get("fullname")) or author
        tags = item.get("tags") or []
        lic = license_of(tags)
        pricing, evidence = None, {"hubRepoId": repo_id, "productType": kind[:-1]}
        if kind == "models" and lic and lic.lower() in OPEN_LICENSES:
            pricing = PricingModel.FREE
            evidence["pricingModel"] = f"license:{lic} (open-weight download)"
        card = item.get("cardData") or {}
        title = clean_text(card.get("title")) if kind == "spaces" else None
        desc = clean_text(card.get("short_description") or item.get("pipeline_tag"), 1000)
        page = f"{HF}/{'spaces/' if kind == 'spaces' else ''}{repo_id}"
        return self.build(
            ProductRecord,
            source={"name": self.source_name, "url": page},
            collectedAt=ctx.now,
            content={
                "productName": title or short,
                "startupName": company,
                "rawStartupName": author,
                "pricingModel": pricing,
                "description": desc,
                "website": page,
            },
            provenance={
                "extractionMethod": ExtractionMethod.STRUCTURED_API,
                "sourceRecordId": f"hf-{kind[:-1]}:{repo_id}",
                "evidence": evidence,
                "validationStatus": "VERIFIED" if pricing else "PARTIAL",
            },
        )
