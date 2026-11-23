"""Five AI job boards via their public JSON job-board APIs.

| Board       | Endpoint                                              | Publication field          |
|-------------|-------------------------------------------------------|----------------------------|
| Greenhouse  | boards-api.greenhouse.io/v1/boards/{token}/jobs       | ``first_published``        |
| Ashby       | api.ashbyhq.com/posting-api/job-board/{org}           | ``publishedAt``            |
| Lever       | api.lever.co/v0/postings/{company}?mode=json          | ``createdAt`` (epoch ms)   |
| RemoteOK    | remoteok.com/api                                      | ``epoch`` / ``date``       |
| Himalayas   | himalayas.app/jobs/api                                | ``pubDate`` (epoch s)      |

Greenhouse's ``updated_at`` is deliberately *not* used as a publication date:
an edited old posting would otherwise look fresh.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from src.core.dates import ParsedDate, parse_datetime
from src.core.http import FetchError, RobotsDisallowedError, SourceBlockedError
from src.core.ratelimit import CircuitOpenError
from src.core.schemas import ExtractionMethod, JobRecord, RecordBase, RecordType
from src.core.text import html_to_text
from src.core.urls import canonicalize_url
from src.crawlers.base import CrawlContext, SourceStatus, clean_text
from src.crawlers.fresh import FreshSignalCrawler
from src.crawlers.jobs.taxonomy import is_ai_related, remote_from_text, role_family

_ACCESS_ERRORS = (SourceBlockedError, RobotsDisallowedError)


@dataclass(slots=True)
class RawJob:
    title: str
    company: str
    url: str
    published: ParsedDate | None
    location: str | None = None
    is_remote: bool | None = None
    department: str | None = None
    description: str | None = None
    source_id: str | None = None
    tags: list[str] = field(default_factory=list)


class JobBoardCrawler(FreshSignalCrawler):
    vertical = RecordType.JOB
    require_ai_keywords = False  # company-specific boards of AI companies: every role is an AI-company job

    async def raw_jobs(self, ctx: CrawlContext) -> AsyncIterator[RawJob]:  # pragma: no cover - abstract
        raise NotImplementedError
        yield

    async def crawl(self, ctx: CrawlContext) -> AsyncIterator[RecordBase]:
        self.setup_fresh(ctx)
        batch: list[RawJob] = []
        try:
            async for j in self.raw_jobs(ctx):
                batch.append(j)
        except _ACCESS_ERRORS as e:
            self.stats.error(f"inaccessible: {e}", inaccessible=not batch)
        except (FetchError, CircuitOpenError) as e:
            self.stats.error(f"fetch failed: {e}", fatal=not batch)
        self.observe_listing([j.url for j in batch])
        for j in batch:
            self.stats.fetched += 1
            if self.require_ai_keywords and not is_ai_related(j.title, " ".join(j.tags), j.department):
                self.stats.reject("not_ai_related")
                continue
            url = canonicalize_url(j.url)
            key = await self.claim(url)
            if key is None:
                continue
            d = self.decide(j.published, url, ctx)
            if not d.keep:
                await self.finish_rejected(key)
                continue
            rec = self.build(
                JobRecord,
                source={"name": self.source_name, "url": url},
                collectedAt=ctx.now,
                content={
                    "title": clean_text(j.title, 500),
                    "company": clean_text(j.company, 300),
                    "raw_company": clean_text(j.company, 300),
                    "date": d.date,
                    "date_verified": d.verified,
                    "date_source": d.date_source,
                    "is_remote": j.is_remote,
                    "role_family": role_family(j.title, j.department),
                    "location": clean_text(j.location, 300),
                    "job_url": url,
                    "description": clean_text(j.description, 8000),
                },
                provenance={
                    "extractionMethod": ExtractionMethod.STRUCTURED_API,
                    "sourceRecordId": j.source_id,
                    "evidence": {"date": f"{j.published.source}={j.published.iso}" if j.published else "none"},
                    "validationStatus": "VERIFIED" if d.verified else "UNVERIFIED",
                },
            )
            if rec:
                yield rec
                if ctx.limit and self.stats.emitted >= ctx.limit:
                    return


class GreenhouseCrawler(JobBoardCrawler):
    name, source_name = "greenhouse", "Greenhouse"
    DEFAULT_BOARDS = {"anthropic": "Anthropic", "scaleai": "Scale AI", "databricks": "Databricks", "xai": "xAI", "stabilityai": "Stability AI"}

    def __init__(self, boards: dict[str, str] | None = None) -> None:
        super().__init__()
        self.boards = boards or self.DEFAULT_BOARDS

    async def raw_jobs(self, ctx: CrawlContext) -> AsyncIterator[RawJob]:
        for token, company in self.boards.items():
            try:
                data = await ctx.http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", params={"content": "true"})
            except FetchError as e:
                self.stats.error(f"board {token}: {e}")
                continue
            for j in data.get("jobs", []):
                meta = {m.get("name"): m.get("value") for m in j.get("metadata") or [] if isinstance(m, dict)}
                loc = (j.get("location") or {}).get("name")
                loc_type = meta.get("Location Type") or meta.get("Workplace Type")
                yield RawJob(
                    title=j.get("title", ""), company=j.get("company_name") or company, url=j.get("absolute_url", ""),
                    published=parse_datetime(j.get("first_published"), now=ctx.now, source="greenhouse:first_published"),
                    location=loc, is_remote=remote_from_text(loc_type if isinstance(loc_type, str) else None, loc),
                    department=", ".join(d.get("name", "") for d in j.get("departments") or []) or None,
                    description=html_to_text(j.get("content"))[:8000] or None, source_id=f"gh:{j.get('id')}",
                )


class AshbyCrawler(JobBoardCrawler):
    name, source_name = "ashby", "Ashby"
    DEFAULT_ORGS = {"openai": "OpenAI", "cohere": "Cohere", "perplexity": "Perplexity", "elevenlabs": "ElevenLabs", "harvey": "Harvey", "notion": "Notion"}

    def __init__(self, orgs: dict[str, str] | None = None) -> None:
        super().__init__()
        self.orgs = orgs or self.DEFAULT_ORGS

    async def raw_jobs(self, ctx: CrawlContext) -> AsyncIterator[RawJob]:
        for org, company in self.orgs.items():
            try:
                data = await ctx.http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{org}")
            except FetchError as e:
                self.stats.error(f"org {org}: {e}")
                continue
            for j in data.get("jobs", []):
                if j.get("isListed") is False:
                    continue
                remote = j.get("isRemote") if isinstance(j.get("isRemote"), bool) else remote_from_text(j.get("workplaceType"), j.get("location"))
                yield RawJob(
                    title=j.get("title", ""), company=company, url=j.get("jobUrl", ""),
                    published=parse_datetime(j.get("publishedAt"), now=ctx.now, source="ashby:publishedAt"),
                    location=j.get("location"), is_remote=remote, department=j.get("department"),
                    description=(j.get("descriptionPlain") or "")[:8000] or None, source_id=f"ashby:{j.get('id')}",
                )


class LeverCrawler(JobBoardCrawler):
    name, source_name = "lever", "Lever"
    # verified live on 2026-11-21 (api.lever.co returned postings)
    DEFAULT_COMPANIES = {"shieldai": "Shield AI", "zoox": "Zoox", "palantir": "Palantir"}

    def __init__(self, companies: dict[str, str] | None = None) -> None:
        super().__init__()
        self.companies = companies or self.DEFAULT_COMPANIES

    async def raw_jobs(self, ctx: CrawlContext) -> AsyncIterator[RawJob]:
        for slug, company in self.companies.items():
            try:
                data = await ctx.http.get_json(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
            except FetchError as e:
                self.stats.error(f"company {slug}: {e}")
                continue
            for j in data if isinstance(data, list) else []:
                cats = j.get("categories") or {}
                wt = j.get("workplaceType")
                yield RawJob(
                    title=j.get("text", ""), company=company, url=j.get("hostedUrl", ""),
                    published=parse_datetime(j.get("createdAt"), now=ctx.now, source="lever:createdAt"),
                    location=cats.get("location"), is_remote=True if wt == "remote" else (False if wt in ("onsite", "hybrid") else remote_from_text(cats.get("location"))),
                    department=cats.get("department") or cats.get("team"), description=(j.get("descriptionPlain") or "")[:8000] or None,
                    source_id=f"lever:{j.get('id')}",
                )


class RemoteOKCrawler(JobBoardCrawler):
    """RemoteOK public API (terms: link back to the job URL, which ``source.url`` does)."""

    name, source_name = "remoteok", "Remote OK"
    require_ai_keywords = True

    async def raw_jobs(self, ctx: CrawlContext) -> AsyncIterator[RawJob]:
        data = await ctx.http.get_json("https://remoteok.com/api")
        for j in data if isinstance(data, list) else []:
            if not isinstance(j, dict) or "position" not in j:
                continue  # first element is the legal notice
            pd = parse_datetime(j.get("epoch"), now=ctx.now, source="remoteok:epoch") or parse_datetime(j.get("date"), now=ctx.now, source="remoteok:date")
            yield RawJob(
                title=j.get("position", ""), company=j.get("company", ""), url=j.get("url") or f"https://remoteok.com/remote-jobs/{j.get('id')}",
                published=pd, location=j.get("location") or "Remote", is_remote=True, tags=[t for t in j.get("tags") or [] if isinstance(t, str)],
                description=html_to_text(j.get("description"))[:8000] or None, source_id=f"remoteok:{j.get('id')}",
            )


class HimalayasCrawler(JobBoardCrawler):
    name, source_name = "himalayas", "Himalayas"
    require_ai_keywords = True

    def __init__(self, max_pages: int = 25, page_size: int = 20) -> None:
        super().__init__()
        self.max_pages, self.page_size = max_pages, page_size

    async def raw_jobs(self, ctx: CrawlContext) -> AsyncIterator[RawJob]:
        for page in range(self.max_pages):
            data = await ctx.http.get_json("https://himalayas.app/jobs/api", params={"limit": str(self.page_size), "offset": str(page * self.page_size)})
            jobs = data.get("jobs", []) if isinstance(data, dict) else []
            if not jobs:
                return
            oldest = None
            for j in jobs:
                pd = parse_datetime(j.get("pubDate"), now=ctx.now, source="himalayas:pubDate")
                oldest = pd if pd and (oldest is None or pd.value < oldest.value) else oldest
                yield RawJob(
                    title=j.get("title", ""), company=j.get("companyName", ""), url=j.get("applicationLink") or j.get("guid", ""),
                    published=pd, location=", ".join(j.get("locationRestrictions") or []) or "Remote", is_remote=True,
                    tags=[c for c in j.get("categories") or [] if isinstance(c, str)], description=html_to_text(j.get("description"))[:8000] or None,
                    source_id=f"himalayas:{j.get('guid')}",
                )
            # listing is newest-first: stop paging once a whole page is older than the window
            if oldest and (ctx.now - oldest.value).total_seconds() > 2 * 86400:
                return


def build_job_crawlers() -> list[JobBoardCrawler]:
    return [GreenhouseCrawler(), AshbyCrawler(), LeverCrawler(), RemoteOKCrawler(), HimalayasCrawler()]


__all__ = [
    "AshbyCrawler", "GreenhouseCrawler", "HimalayasCrawler", "JobBoardCrawler", "LeverCrawler", "RawJob", "RemoteOKCrawler",
    "SourceStatus", "build_job_crawlers",
]

