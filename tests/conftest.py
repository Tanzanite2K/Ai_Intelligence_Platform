"""Shared fixtures.

All payloads below are SYNTHETIC TEST FIXTURES shaped like the real APIs
(field names verified against live responses on 2026-11-21). They are used
only by tests and are never written to the production database or exports.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from aioresponses import aioresponses

from src.config.settings import Settings
from src.core.http import HttpClient
from src.database.repository import Repository
from src.database.session import Database

NOW = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def settings(tmp_path) -> Settings:  # type: ignore[no-untyped-def]
    return Settings(
        _env_file=None,  # type: ignore[call-arg]  # never pick up the developer's .env in tests
        hf_org_cache_path=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db", respect_robots_txt=False, http_max_retries=2,
        default_domain_rps=1000, redis_url=None, export_dir=str(tmp_path / "exports"), llm_backoff_base_s=0.001,
    )


@pytest.fixture
async def http(settings: Settings) -> AsyncIterator[HttpClient]:
    async with HttpClient(settings) as c:
        yield c


@pytest.fixture
def mocked() -> aioresponses:  # type: ignore[misc]
    with aioresponses() as m:
        yield m


def _db_urls() -> list[str]:
    urls = ["sqlite"]
    if os.getenv("TEST_DATABASE_URL"):
        urls.append("postgres")
    return urls


@pytest.fixture(params=_db_urls())
async def repo(request, tmp_path) -> AsyncIterator[Repository]:  # type: ignore[no-untyped-def]
    url = f"sqlite+aiosqlite:///{tmp_path}/r.db" if request.param == "sqlite" else os.environ["TEST_DATABASE_URL"]
    db = Database(url)
    await db.drop_all()
    await db.create_all()
    yield Repository(db, batch_size=50)
    await db.drop_all()
    await db.dispose()


# ----------------------------------------------------------------- payloads
def arxiv_atom(entries: list[dict]) -> str:
    items = []
    for e in entries:
        authors = "".join(f"<author><name>{a}</name></author>" for a in e["authors"])
        items.append(
            f"""<entry><id>http://arxiv.org/abs/{e['id']}v1</id><updated>{e['published']}</updated><published>{e['published']}</published>
<title>{e['title']}</title><summary>{e.get('summary', 'An abstract.')}</summary>{authors}
<arxiv:comment xmlns:arxiv="http://arxiv.org/schemas/atom">{e.get('comment', '')}</arxiv:comment>
<link href="http://arxiv.org/abs/{e['id']}v1" rel="alternate" type="text/html"/></entry>"""
        )
    return f'<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">{"".join(items)}</feed>'


def rss(items: list[dict]) -> str:
    body = []
    for it in items:
        pub = f"<pubDate>{it['pub']}</pubDate>" if it.get("pub") else ""
        content = f"<content:encoded><![CDATA[{it['content']}]]></content:encoded>" if it.get("content") else ""
        body.append(f"<item><title>{it['title']}</title><link>{it['link']}</link>{pub}<description>{it.get('desc', 'Summary.')}</description>{content}</item>")
    return (
        '<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>T</title>'
        + "".join(body)
        + "</channel></rss>"
    )


def rfc2822(dt: datetime) -> str:
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


LONG_ARTICLE = "\n".join(
    f"<p>Paragraph {i}: OpenAI and Anthropic announced new models with improved reasoning benchmarks and pricing details for developers.</p>"
    for i in range(20)
)
HOURS = timedelta(hours=1)
