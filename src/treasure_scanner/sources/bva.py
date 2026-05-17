"""BVA-Auctions (bva-auctions.com) — NL business/clearance auctions.

HTML-based search. Lot pages carry JSON-LD with Product schema.
"""
from __future__ import annotations

import asyncio
import json
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

log = structlog.get_logger(__name__)

BASE = "https://www.bva-auctions.com"
PRICE_RE = re.compile(r"€\s*([\d.,]+)")


class BVASource:
    name = "bva"
    country = "NL"
    site_code = "bva"

    def __init__(
        self,
        browser: StealthBrowser | None = None,
        use_browser: bool = False,
        request_interval: float = 4.0,
    ):
        self.browser = browser
        self.use_browser = use_browser and browser is not None
        self.requires_browser = self.use_browser
        self._client = httpx.AsyncClient(
            timeout=25.0,
            headers={
                "Accept": "text/html",
                "Accept-Language": "nl-NL,nl;q=0.9",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/17.4 Safari/605.1.15"
                ),
            },
            follow_redirects=True,
        )
        self.request_interval = request_interval
        self._last_at = 0.0
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _throttle(self) -> None:
        async with self._lock:
            loop = asyncio.get_event_loop()
            wait = self.request_interval - (loop.time() - self._last_at)
            if wait > 0:
                await asyncio.sleep(wait + random.uniform(0, 1.0))
            self._last_at = asyncio.get_event_loop().time()

    async def _fetch_html(self, url: str) -> str | None:
        try:
            if self.use_browser:
                return await self.browser.html(url, wait_for="main")
            await self._throttle()
            resp = await self._client.get(url)
            if resp.status_code in (403, 429):
                return None
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            log.warning("bva_fetch_failed", error=str(e), url=url)
            return None

    async def search(
        self,
        query: str,
        max_pages: int = 1,
        category_id: int | None = None,
    ) -> AsyncIterator[Listing]:
        for page in range(1, max_pages + 1):
            url = f"{BASE}/nl/search?q={quote_plus(query)}&page={page}"
            html = await self._fetch_html(url)
            if not html:
                return
            for listing in self._parse(html):
                yield listing

    def _parse(self, html: str) -> list[Listing]:
        tree = HTMLParser(html)
        out: list[Listing] = []

        # 1) Try JSON-LD ItemList first (most reliable when present).
        for script in tree.css("script[type='application/ld+json']"):
            try:
                data = json.loads(script.text())
            except (ValueError, TypeError):
                continue
            items = self._extract_jsonld(data)
            out.extend(items)
            if out:
                return out[:50]

        # 2) Fallback: lot cards with anchors to /lot/<id>
        seen: set[str] = set()
        for a in tree.css("a[href*='/lot/'], a[href*='/auction/']"):
            href = a.attributes.get("href", "")
            m = re.search(r"/lot/(\d+)", href)
            if not m:
                continue
            lot_id = m.group(1)
            if lot_id in seen:
                continue
            seen.add(lot_id)
            url = href if href.startswith("http") else f"{BASE}{href}"
            title = a.text(strip=True)[:120] or "BVA lot"
            out.append(Listing(
                item_id=f"{self.site_code}:{lot_id}",
                title=title, description="",
                price=None, price_type="bidding",
                url=url, seller_name="BVA",
                location=None, posted_at=datetime.now(timezone.utc),
                thumbnail_url=None, category_id=None, category_name=None,
                site=self.name, country=self.country,
                raw={"lot_id": lot_id, "auction_source": "bva"},
            ))
        return out

    def _extract_jsonld(self, data) -> list[Listing]:
        out: list[Listing] = []
        if isinstance(data, list):
            for d in data:
                out.extend(self._extract_jsonld(d))
            return out
        if not isinstance(data, dict):
            return out

        if data.get("@type") in ("ItemList", "Collection"):
            for el in data.get("itemListElement", []):
                item = el.get("item") if isinstance(el, dict) else None
                if item:
                    parsed = self._jsonld_to_listing(item)
                    if parsed:
                        out.append(parsed)
        elif data.get("@type") == "Product":
            parsed = self._jsonld_to_listing(data)
            if parsed:
                out.append(parsed)
        return out

    def _jsonld_to_listing(self, item: dict) -> Listing | None:
        url = item.get("url") or item.get("@id")
        if not url:
            return None
        m = re.search(r"/lot/(\d+)", url)
        lot_id = m.group(1) if m else url.rsplit("/", 1)[-1]
        name = item.get("name", "")
        desc = item.get("description", "") or ""
        offers = item.get("offers") or {}
        price = None
        if isinstance(offers, dict):
            try:
                price = float(offers.get("price") or 0) or None
            except (TypeError, ValueError):
                pass
        image = item.get("image")
        thumb = image if isinstance(image, str) else (image[0] if isinstance(image, list) and image else None)
        return Listing(
            item_id=f"{self.site_code}:{lot_id}",
            title=name, description=desc,
            price=price, price_type="bidding",
            url=url if url.startswith("http") else f"{BASE}{url}",
            seller_name="BVA", location=None,
            posted_at=datetime.now(timezone.utc),
            thumbnail_url=thumb, category_id=None, category_name=None,
            site=self.name, country=self.country,
            raw={"lot_id": lot_id, "auction_source": "bva"},
        )
