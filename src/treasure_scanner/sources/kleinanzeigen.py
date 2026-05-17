"""Kleinanzeigen.de — HTML scraper for German classifieds.

No public API. Plain httpx works for now; if it gets blocked, set
`use_browser=True` and pass a StealthBrowser.
"""
from __future__ import annotations

import asyncio
import random
import re
from datetime import datetime, timezone
from typing import AsyncIterator
from urllib.parse import quote_plus

import httpx
import structlog
from selectolax.parser import HTMLParser

from ..browser import StealthBrowser
from ..models import Listing
from ..utils.throttle import Throttle

log = structlog.get_logger(__name__)

BASE = "https://www.kleinanzeigen.de"
SEARCH_URL = f"{BASE}/s-suchanfrage.html"

PRICE_RE = re.compile(r"([\d.,]+)\s*€")


class KleinanzeigenSource:
    name = "kleinanzeigen"
    country = "DE"
    site_code = "kln"

    def __init__(
        self,
        browser: StealthBrowser | None = None,
        use_browser: bool = False,
        request_interval: float = 3.0,
    ):
        self.browser = browser
        self.use_browser = use_browser and browser is not None
        self.requires_browser = self.use_browser
        self._client = httpx.AsyncClient(
            timeout=20.0,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            },
            follow_redirects=True,
        )
        self.throttle = Throttle(request_interval)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _fetch_html(self, url: str) -> str | None:
        if self.use_browser:
            try:
                return await self.browser.html(url, wait_for="article")
            except Exception as e:
                log.warning("kleinanzeigen_browser_failed", error=str(e), url=url)
                return None
        await self.throttle.wait()
        try:
            resp = await self._client.get(url)
            self.throttle.record_response(resp.status_code)
            if resp.status_code in (403, 429):
                log.warning("kleinanzeigen_blocked", status=resp.status_code,
                            interval=self.throttle.current)
                return None
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            self.throttle.record_failure()
            log.warning("kleinanzeigen_fetch_failed", error=str(e), url=url)
            return None

    async def search(
        self,
        query: str,
        max_pages: int = 1,
        category_id: int | None = None,
    ) -> AsyncIterator[Listing]:
        for page in range(1, max_pages + 1):
            # Kleinanzeigen URL scheme: /s-{query}/k0  → page param via /seite:N
            page_suffix = f"seite:{page}/" if page > 1 else ""
            url = f"{BASE}/s-{page_suffix}{quote_plus(query)}/k0"
            html = await self._fetch_html(url)
            if not html:
                return
            for listing in self._parse_results(html):
                yield listing

    def _parse_results(self, html: str) -> list[Listing]:
        tree = HTMLParser(html)
        out: list[Listing] = []
        seen_ids: set[str] = set()
        # Each result is an <article> with data-adid.
        for art in tree.css("article.aditem, article[data-adid]"):
            ad_id = art.attributes.get("data-adid")
            href = art.attributes.get("data-href") or ""
            if not ad_id and href:
                m = re.search(r"/s-anzeige/[^/]+/(\d+)", href)
                ad_id = m.group(1) if m else None
            if not ad_id or ad_id in seen_ids:
                continue
            seen_ids.add(ad_id)

            title_node = art.css_first("h2 a, .ellipsis a")
            title = title_node.text(strip=True) if title_node else ""
            if not href and title_node:
                href = title_node.attributes.get("href", "")
            url = href if href.startswith("http") else f"{BASE}{href}"

            price_node = art.css_first(".aditem-main--middle--price-shipping--price, p.price")
            price, ptype = self._parse_price(
                price_node.text(strip=True) if price_node else ""
            )

            location_node = art.css_first(".aditem-main--top--left, .aditem-main--top")
            location = (location_node.text(strip=True)
                        if location_node else None)

            desc_node = art.css_first(".aditem-main--middle--description, p.text")
            description = desc_node.text(strip=True) if desc_node else ""

            img_node = art.css_first("img")
            thumb = None
            if img_node:
                thumb = (img_node.attributes.get("src")
                         or img_node.attributes.get("data-imgsrc"))

            out.append(Listing(
                item_id=f"{self.site_code}:{ad_id}",
                title=title,
                description=description,
                price=price,
                price_type=ptype,
                url=url,
                seller_name=None,
                location=location,
                posted_at=datetime.now(timezone.utc),
                thumbnail_url=thumb,
                category_id=None,
                category_name=None,
                site=self.name,
                country=self.country,
                raw={"adid": ad_id},
            ))
        return out

    @staticmethod
    def _parse_price(text: str) -> tuple[float | None, str]:
        if not text:
            return None, "unknown"
        low = text.lower()
        if "zu verschenken" in low or "kostenlos" in low:
            return 0.0, "free"
        if "vb" in low or "verhandelbar" in low:
            ptype = "bidding"
        else:
            ptype = "fixed"
        m = PRICE_RE.search(text)
        if not m:
            return None, ptype
        try:
            # German format: "1.234,56" → 1234.56
            raw = m.group(1).replace(".", "").replace(",", ".")
            return float(raw), ptype
        except ValueError:
            return None, ptype
