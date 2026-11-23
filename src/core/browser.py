"""Playwright (async) renderer for JavaScript-heavy pages that *permit* automated access.

Resource optimisation: one shared Chromium, a bounded pool of contexts, and
route-level blocking of images/fonts/media/analytics so a page costs a
fraction of a normal load. Anti-bot policy: if a Cloudflare/DataDome
challenge is served the renderer raises ``SourceBlockedError`` — it does not
solve CAPTCHAs, spoof fingerprints or rotate residential proxies. Such
sources are routed to official APIs, feeds or licensed data providers.
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.core.http import SourceBlockedError, detect_challenge
from src.monitoring.logging import get_logger

log = get_logger(__name__)

BLOCKED_RESOURCE_TYPES = {"image", "media", "font", "stylesheet"}
BLOCKED_HOST_FRAGMENTS = ("google-analytics", "googletagmanager", "doubleclick", "facebook.net", "hotjar", "segment.io", "intercom")


class BrowserRenderer:
    def __init__(self, max_pages: int = 4, timeout_ms: int = 30_000, user_agent: str | None = None) -> None:
        self._sem = asyncio.Semaphore(max_pages)
        self.timeout_ms = timeout_ms
        self.user_agent = user_agent
        self._pw: Any = None
        self._browser: Any = None

    async def __aenter__(self) -> BrowserRenderer:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-sandbox"])
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    @staticmethod
    async def _route(route: Any) -> None:
        req = route.request
        if req.resource_type in BLOCKED_RESOURCE_TYPES or any(f in req.url for f in BLOCKED_HOST_FRAGMENTS):
            await route.abort()
        else:
            await route.continue_()

    async def render(self, url: str, wait_selector: str | None = None) -> str:
        async with self._sem:
            ctx = await self._browser.new_context(user_agent=self.user_agent, java_script_enabled=True)
            try:
                page = await ctx.new_page()
                await page.route("**/*", self._route)
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                if wait_selector:
                    await page.wait_for_selector(wait_selector, timeout=self.timeout_ms)
                html = await page.content()
                status = resp.status if resp else 0
                headers = await resp.all_headers() if resp else {}
                challenge = detect_challenge(status if status >= 400 else 403 if "challenge" in html[:3000].lower() else status, headers, html)
                if challenge:
                    raise SourceBlockedError(f"browser challenge served ({challenge})", url=url, status=status)
                return html
            finally:
                await ctx.close()
