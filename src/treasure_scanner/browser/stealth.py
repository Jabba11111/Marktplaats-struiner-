"""Stealth Playwright wrapper to avoid bot detection on sites that block
plain HTTP clients (Catawiki, Troostwijk, etc.).

Detection-avoidance approach (in order of importance):
  1. patchright (a maintained Playwright fork with the common stealth
     patches baked in: navigator.webdriver removed, plugin shims,
     consistent user-agent across CDP, etc.). Falls back to vanilla
     Playwright with our own minimal patches when patchright isn't
     installed.
  2. Realistic fingerprints (Chrome 131 / Firefox 128 / Safari 17),
     proper Accept-Language matching the locale, sensible viewport.
  3. Persistent context per host so cookies + session storage stick,
     which makes us look like a returning visitor instead of a fresh
     headless instance every page load.
  4. Human-ish interaction: random small mouse moves before clicks,
     scroll jitter, dwell time on first page-load.

Inspired by CloakBrowser (https://github.com/CloakHQ/CloakBrowser);
similar idea, MIT-friendly implementation.
"""
from __future__ import annotations

import asyncio
import random
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import structlog

from .fingerprints import Fingerprint, random_fingerprint

log = structlog.get_logger(__name__)


class BrowserClosedError(RuntimeError):
    """Raised when calling a closed/uninitialised StealthBrowser."""


# Lazy import: don't crash the whole app if playwright isn't installed.
async def _import_playwright():
    try:
        from patchright.async_api import async_playwright  # type: ignore
        log.debug("stealth_using_patchright")
        return async_playwright, "patchright"
    except ImportError:
        try:
            from playwright.async_api import async_playwright  # type: ignore
            log.warning("stealth_patchright_missing_using_playwright")
            return async_playwright, "playwright"
        except ImportError as e:
            raise BrowserClosedError(
                "Neither patchright nor playwright is installed. "
                "Run: pip install patchright && patchright install chromium"
            ) from e


# Minimal init script for plain playwright fallback. patchright already
# does much more than this internally.
INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['nl-NL', 'nl', 'en'] });
Object.defineProperty(navigator, 'plugins', {
  get: () => [
    { name: 'PDF Viewer', filename: 'internal-pdf-viewer' },
    { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer' },
    { name: 'Native Client', filename: 'internal-nacl-plugin' },
  ],
});
window.chrome = window.chrome || { runtime: {} };
const origQuery = window.navigator.permissions && window.navigator.permissions.query;
if (origQuery) {
  window.navigator.permissions.query = (params) =>
    params.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : origQuery(params);
}
"""


class StealthBrowser:
    """Async context-managed stealth browser, one shared instance per process.

    Use as::

        browser = StealthBrowser(profile_dir=Path("data/browser"))
        await browser.start()
        async with browser.page("https://...") as page:
            html = await page.content()
        await browser.stop()
    """

    def __init__(
        self,
        profile_dir: Path,
        headless: bool = True,
        min_request_interval: float = 4.0,
    ):
        self.profile_dir = profile_dir
        self.headless = headless
        self.min_request_interval = min_request_interval
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        self._playwright = None
        self._context = None
        self._lock = asyncio.Lock()
        self._last_nav_at = 0.0
        self._fingerprint: Fingerprint | None = None
        self._flavor: str | None = None

    async def start(self) -> None:
        if self._context is not None:
            return
        async_playwright, flavor = await _import_playwright()
        self._flavor = flavor
        self._fingerprint = random_fingerprint()
        fp = self._fingerprint

        self._playwright = await async_playwright().start()
        # Persistent context so cookies + cache survive restarts; this is
        # the biggest single factor in not looking like a fresh headless bot.
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            user_agent=fp.user_agent,
            viewport=fp.viewport,
            locale=fp.locale,
            timezone_id=fp.timezone,
            color_scheme=fp.color_scheme,
            device_scale_factor=fp.device_scale_factor,
            extra_http_headers={"Accept-Language": fp.accept_language},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-default-browser-check",
                "--no-first-run",
                "--disable-dev-shm-usage",
            ],
            ignore_default_args=["--enable-automation"],
        )
        if flavor == "playwright":
            await self._context.add_init_script(INIT_SCRIPT)

        log.info(
            "stealth_browser_started",
            flavor=flavor, locale=fp.locale, ua_hint=fp.user_agent.split()[-2],
        )

    async def stop(self) -> None:
        try:
            if self._context is not None:
                await self._context.close()
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception as e:
            log.warning("stealth_browser_stop_error", error=str(e))
        finally:
            self._context = None
            self._playwright = None

    async def _throttle(self) -> None:
        loop = asyncio.get_event_loop()
        wait = self.min_request_interval - (loop.time() - self._last_nav_at)
        if wait > 0:
            await asyncio.sleep(wait + random.uniform(0.5, 1.5))
        self._last_nav_at = asyncio.get_event_loop().time()

    @asynccontextmanager
    async def page(self, url: str, wait_for: str | None = None,
                   timeout_ms: int = 25_000) -> AsyncIterator:
        if self._context is None:
            raise BrowserClosedError("StealthBrowser.start() not called")
        async with self._lock:
            await self._throttle()
            page = await self._context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded",
                                timeout=timeout_ms)
                # Light human-ish interaction so SPA's hydrate and
                # mouse-tracking analytics don't flag us.
                await page.mouse.move(
                    random.randint(100, 400), random.randint(100, 400),
                )
                await asyncio.sleep(random.uniform(0.4, 1.2))
                await page.mouse.wheel(0, random.randint(120, 380))
                if wait_for:
                    try:
                        await page.wait_for_selector(wait_for, timeout=timeout_ms)
                    except Exception:
                        pass  # let caller decide what to do with partial content
                yield page
            finally:
                await page.close()

    async def html(self, url: str, wait_for: str | None = None) -> str:
        async with self.page(url, wait_for=wait_for) as page:
            return await page.content()
