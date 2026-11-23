"""Research-paper vertical: HF Papers + arXiv, correlated with GitHub repos and live stars."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC

from src.core.http import FetchError
from src.core.schemas import ExtractionMethod, RecordBase, RecordType, ResearchPaperRecord
from src.crawlers.base import BaseCrawler, CrawlContext
from src.crawlers.research.github import GitHubAuthError, GitHubClient, GitHubRateLimited
from src.crawlers.research.sources import PaperCandidate, hf_paper_repo, iter_arxiv, iter_hf_daily
from src.monitoring.logging import get_logger

log = get_logger(__name__)

DEFAULT_CATEGORIES = ["cs.AI", "cs.LG", "cs.CL", "cs.CV", "cs.IR", "cs.RO", "stat.ML"]


class ResearchPaperCrawler(BaseCrawler):
    name = "research_papers"
    source_name = "arXiv / Hugging Face Papers"
    vertical = RecordType.RESEARCH_PAPER

    def __init__(
        self,
        *,
        require_github: bool = True,
        hf_days: int = 120,
        arxiv_max: int = 4000,
        categories: list[str] | None = None,
        lookup_hf_for_arxiv: bool = True,
        enrich_batch: int = 40,
    ) -> None:
        super().__init__()
        self.require_github = require_github
        self.hf_days, self.arxiv_max = hf_days, arxiv_max
        self.categories = categories or DEFAULT_CATEGORIES
        self.lookup_hf_for_arxiv = lookup_hf_for_arxiv
        self.enrich_batch = enrich_batch
        self.github_live = 0
        self.github_cached = 0
        self.github_missing_repo = 0
        self._gh_exhausted = False
        self.github_deferred = 0  # papers left for the next run because the GitHub quota was used up

    async def _github_preflight(self, gh: GitHubClient, has_token: bool) -> None:
        """Check auth and quota before the first repo call (GET /rate_limit is free)."""
        try:
            st = await gh.rate_limit_status()
        except GitHubAuthError as e:
            self.stats.error(str(e))
            return
        except FetchError as e:
            self.stats.warn(f"github preflight failed ({e}); continuing")
            return
        if has_token and not st.authenticated:
            self.stats.warn("GITHUB_TOKEN is set but GitHub treats requests as unauthenticated (60/h)")
        elif not has_token:
            self.stats.warn("GITHUB_TOKEN not set: GitHub allows only 60 requests/h; most papers will be deferred")
        if st.remaining <= 0:
            self._gh_exhausted = True
            self.stats.error(str(GitHubRateLimited(st.reset_at, limit=st.limit)))

    async def crawl(self, ctx: CrawlContext) -> AsyncIterator[RecordBase]:
        token = ctx.settings.github_token.get_secret_value() if ctx.settings.github_token else None
        gh = GitHubClient(ctx.http, token)
        await self._github_preflight(gh, bool(token))
        seen: set[str] = set(ctx.checkpoint.get("seen_ids", [])) if ctx.checkpoint.get("resume") else set()

        async def source_batches() -> AsyncIterator[list[PaperCandidate]]:
            try:
                async for _, batch in iter_hf_daily(ctx.http, ctx.now.astimezone(UTC).date(), self.hf_days):
                    yield batch
            except FetchError as e:
                self.stats.error(f"hf papers: {e}")
            try:
                async for offset, batch in iter_arxiv(ctx.http, self.categories, max_results=self.arxiv_max):
                    self.next_checkpoint["arxiv_offset"] = offset
                    yield batch
            except FetchError as e:
                self.stats.error(f"arxiv: {e}", inaccessible=self.stats.emitted == 0)

        async for batch in source_batches():
            fresh = [p for p in batch if p.arxiv_id not in seen]
            for p in fresh:
                seen.add(p.arxiv_id)
            self.stats.fetched += len(batch)
            for i in range(0, len(fresh), self.enrich_batch):
                chunk = fresh[i : i + self.enrich_batch]
                recs = await asyncio.gather(*(self._enrich(p, gh, ctx) for p in chunk))
                for rec in recs:
                    if rec:
                        yield rec
                        if ctx.limit and self.stats.emitted >= ctx.limit:
                            self.next_checkpoint["github_calls"] = gh.calls
                            return
        self.next_checkpoint["github_calls"] = gh.calls
        if self.github_deferred:
            self.stats.errors.append(f"{self.github_deferred} papers deferred to the next run (GitHub quota exhausted)")

    async def _enrich(self, p: PaperCandidate, gh: GitHubClient, ctx: CrawlContext) -> RecordBase | None:
        if not p.github_url and self.lookup_hf_for_arxiv and p.source_name == "arXiv":
            try:
                url, stars = await hf_paper_repo(ctx.http, p.arxiv_id)
            except FetchError:
                url, stars = None, None
            if url:
                p.github_url, p.github_url_evidence, p.hf_stars = url, f"hf:githubRepo: {url}", stars
        if not p.github_url:
            self.github_missing_repo += 1
            if self.require_github:
                self.stats.reject("no_github_repo")
                return None
        if p.published is None:
            self.stats.reject("no_publication_date")
            return None

        stars, checked, stars_src, gh_url = None, None, None, p.github_url
        if p.github_url and not self._gh_exhausted:
            try:
                m = await gh.repo_metrics(p.github_url)
                if m is None:
                    # repository link is dead -> drop the link rather than keep an untraceable URL
                    self.stats.reject("github_repo_not_found")
                    if self.require_github:
                        return None
                    gh_url = None
                else:
                    stars, checked, stars_src, gh_url = m.stars, m.checked_at, "github_api", m.url
                    self.github_live += 1
            except GitHubRateLimited as e:
                if not self._gh_exhausted:  # concurrent workers hit the same limit: report it once
                    self._gh_exhausted = True
                    self.stats.error(str(e))
            except FetchError as e:
                self.stats.error(f"github: {e}")
        if stars is None and gh_url and p.hf_stars is not None:
            stars, checked, stars_src = p.hf_stars, ctx.now, "huggingface_papers"
            self.github_cached += 1
        if self.require_github and stars is None:
            if self._gh_exhausted and gh_url:
                self.github_deferred += 1  # not stored now; the paper is picked up again by the next run
                self.stats.reject("github_rate_limited_deferred")
            else:
                self.stats.reject("no_github_metrics")
            return None

        evidence = {"arxivId": p.arxiv_id}
        if p.github_url_evidence:
            evidence["github_url"] = p.github_url_evidence[:300]
        return self.build(
            ResearchPaperRecord,
            source={"name": p.source_name, "url": p.source_url},
            collectedAt=ctx.now,
            content={
                "title": p.title,
                "authors": p.authors,
                "paper_url": p.paper_url,
                "github_url": gh_url,
                "github_stars": stars,
                "github_stars_checked_at": checked,
                "github_stars_source": stars_src,
                "published_date": p.published,
                "arxiv_id": p.arxiv_id,
                "abstract": (p.abstract or "")[:4000] or None,
            },
            provenance={
                "extractionMethod": ExtractionMethod.STRUCTURED_API,
                "sourceRecordId": f"arxiv:{p.arxiv_id}",
                "evidence": evidence,
                "validationStatus": "VERIFIED" if stars_src == "github_api" or not gh_url else "PARTIAL",
            },
        )
