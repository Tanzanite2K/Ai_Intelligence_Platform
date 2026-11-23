"""Product Hunt AI launches via the official GraphQL API v2 (token required).

Product Hunt's website is behind Cloudflare; the sanctioned access path is the
developer API (``PRODUCTHUNT_TOKEN``). Without a token the crawler reports
``SKIPPED`` — it never falls back to scraping the protected site.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator

from src.core.http import FetchError, FetchResult, SourceBlockedError
from src.core.schemas import ExtractionMethod, ProductRecord, RecordBase, RecordType
from src.crawlers.base import BaseCrawler, CrawlContext, SourceStatus, clean_text

API = "https://api.producthunt.com/v2/api/graphql"
QUERY = """
query($after: String, $topic: String!) {
  posts(first: 20, after: $after, topic: $topic, order: NEWEST) {
    pageInfo { hasNextPage endCursor }
    edges { node {
      id name tagline description url website createdAt
      topics(first: 5) { edges { node { name } } }
    } }
  }
}
"""


TOKEN_HELP = ("create/open your app at https://api.producthunt.com/v2/oauth/applications and put its Developer Token "
              "(not the API Key or API Secret) in PRODUCTHUNT_TOKEN; see docs/SOURCE_HEALTH.md")


def token_fingerprint(token: str) -> str:
    """One-way, truncated fingerprint used only to recognise a token that was already rejected. Not reversible."""
    return hashlib.sha256(b"frontieratlas:producthunt-token:" + token.encode()).hexdigest()[:16]


def auth_error_code(res: FetchResult) -> tuple[str | None, str | None]:
    try:
        body = json.loads(res.text)
        first = (body.get("errors") or [{}])[0] if isinstance(body, dict) else {}
        return first.get("error") or body.get("error"), first.get("error_description") or body.get("error_description")
    except (ValueError, AttributeError, IndexError, TypeError):
        return None, None


def auth_error_message(res: FetchResult) -> str:
    """Describe a 401/403 from the API using its own error code. Never includes the token."""
    code, desc = auth_error_code(res)
    what = f"error code: {code}" + (f" - {str(desc)[:160]}" if desc else "") if code else "no error code in response"
    return f"Product Hunt rejected PRODUCTHUNT_TOKEN (HTTP {res.status}, {what}); action required: {TOKEN_HELP}"


class ProductHuntCrawler(BaseCrawler):
    name = "producthunt"
    source_name = "Product Hunt"
    vertical = RecordType.PRODUCT

    def __init__(self, topics: tuple[str, ...] = ("artificial-intelligence",), max_pages: int = 60) -> None:
        super().__init__()
        self.topics, self.max_pages = topics, max_pages

    async def crawl(self, ctx: CrawlContext) -> AsyncIterator[RecordBase]:
        token = ctx.settings.producthunt_token
        if token is None:
            self.stats.status = SourceStatus.SKIPPED
            self.stats.errors.append("PRODUCTHUNT_TOKEN not configured")
            return
        fp = token_fingerprint(token.get_secret_value())
        rejected = ctx.checkpoint.get("credential_rejected") or {}
        if rejected.get("fingerprint") == fp:
            # this exact token was already rejected: do not send it again until PRODUCTHUNT_TOKEN changes
            self.stats.status = SourceStatus.SKIPPED
            self.stats.errors.append(
                f"credential required: PRODUCTHUNT_TOKEN was rejected at {rejected.get('at')} (HTTP {rejected.get('http_status')}, "
                f"{rejected.get('code') or 'no error code'}); not contacted until the token changes. Action: {TOKEN_HELP}")
            return
        headers = {"Authorization": f"Bearer {token.get_secret_value()}", "Content-Type": "application/json"}
        for topic in self.topics:
            after = ctx.checkpoint.get(f"cursor:{topic}") if ctx.checkpoint.get("resume") else None
            for _ in range(self.max_pages):
                try:
                    res = await ctx.http.request("POST", API, headers=headers, json_body={"query": QUERY, "variables": {"after": after, "topic": topic}},
                                                 check_robots=False, accept_status=frozenset({401, 403}))
                except SourceBlockedError as e:
                    self.stats.error(str(e), inaccessible=True)
                    return
                except FetchError as e:
                    if e.rate_limited:
                        until = self.note_rate_limit(ctx, e.retry_after)
                        self.stats.error(f"Product Hunt API rate limit ({e}); next attempt after {until.isoformat()}")
                    else:
                        self.stats.error(str(e))
                    break
                if res.status in (401, 403):  # read the API's error code instead of a bare "HTTP 401"
                    self.stats.error(auth_error_message(res), inaccessible=True)
                    self.next_checkpoint["credential_rejected"] = {"fingerprint": fp, "at": ctx.now.isoformat(), "http_status": res.status,
                                                                   "code": auth_error_code(res)[0]}
                    return
                self.next_checkpoint["credential_rejected"] = None  # the token works
                body = res.json()
                if body.get("errors"):
                    # GraphQL reports auth/scope/argument problems with HTTP 200 + "errors": never treat that as success
                    self.stats.error(f"graphql errors: {str(body['errors'])[:300]}", inaccessible="auth" in str(body["errors"]).lower())
                    break
                data = (body.get("data") or {}).get("posts") or {}
                for edge in data.get("edges", []):
                    n = edge["node"]
                    self.stats.fetched += 1
                    # Product Hunt does not expose the company separately; the
                    # product name is the brand. Pricing left None (LLM step may
                    # classify from tagline/description with evidence).
                    rec = self.build(
                        ProductRecord,
                        source={"name": self.source_name, "url": n.get("url")},
                        collectedAt=ctx.now,
                        content={
                            "productName": clean_text(n.get("name"), 300),
                            "startupName": None,
                            "rawStartupName": None,
                            "pricingModel": None,
                            "description": clean_text(" — ".join(x for x in (n.get("tagline"), n.get("description")) if x), 2000),
                            "website": n.get("website"),
                        },
                        provenance={"extractionMethod": ExtractionMethod.STRUCTURED_API, "sourceRecordId": f"ph:{n.get('id')}", "validationStatus": "PARTIAL"},
                    )
                    if rec:
                        yield rec
                        if ctx.limit and self.stats.emitted >= ctx.limit:
                            return
                info = data.get("pageInfo") or {}
                after = info.get("endCursor")
                self.next_checkpoint[f"cursor:{topic}"] = after
                if not info.get("hasNextPage"):
                    break
