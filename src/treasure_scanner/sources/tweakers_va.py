"""Tweakers Vraag & Aanbod — NL hardware classifieds.

URL: https://tweakers.net/aanbod/zoeken/?keyword=...
"""
from __future__ import annotations

import asyncio
import random
import re
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx
import structlog
from selectolax.parser import HTMLParser

from ..models import Listing

log = structlog.get_logger(__name__)

BASE = "https://tweakers.net"
SEARCH_URL = f"{BASE}/aanbod/zoeken/"

PRICE_RE = re.compile(r"€\s*([\d.,]+)")


class TweakersVASource:
    name = "tweakers_va"
    country = "NL"
    site_code = "twv"
    requires_browser = False

    def __init__(self, request_interval: float = 3.0):
        self._client = httpx.AsyncClient(
            timeout=20.0,
            headers={
                "Accept": "text/html",
                "Accept-Language": "nl-NL,nl;q=0.9",
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
            },
            follow_redirects=True,
        )
        self._request_interval = request_interval
        self._last_at = 0.0
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _throttle(self) -> None:
        async with self._lock:
            loop = asyncio.get_event_loop()
            wait = self._request_interval - (loop.time() - self._last_at)
            if wait > 0:
                await asyncio.sleep(wait + random.uniform(0, 0.6))
            self._last_at = asyncio.get_event_loop().time()

    async def search(
        self,
        query: str,
        max_pages: int = 1,
        category_id: int | None = None,
    ) -> AsyncIterator[Listing]:
        for page in range(1, max_pages + 1):
            params = {"keyword": query, "sort": "date"}
            if page > 1:
                params["page"] = page
            await self._throttle()
            try:
                resp = await self._client.get(SEARCH_URL, params=params)
                resp.raise_for_status()
            except Exception as e:
                log.warning("tweakers_va_fetch_failed", error=str(e))
                return
            for listing in self._parse(resp.text):
                yield listing

    def _parse(self, html: str) -> list[Listing]:
        tree = HTMLParser(html)
        out: list[Listing] = []
        # Result rows on V&A: each is a <tr class="aanbod"> or article-like row.
        for row in tree.css("tr.aanbod, article.listingItem, .aanbodList__item"):
            link = row.css_first("a.title, a.listingItem__link, h3 a")
            if link is None:
                link = row.css_first("a")
            if link is None:
                continue
            href = link.attributes.get("href", "")
            title = link.text(strip=True)
            url = href if href.startswith("http") else f"{BASE}{href}"

            m = re.search(r"/aanbod/(\d+)/", url)
            ad_id = m.group(1) if m else url

            price_node = (row.css_first(".price, td.price, .listingItem__price")
                          or row)
            price_text = price_node.text(strip=True) if price_node else ""
            price, ptype = self._parse_price(price_text)

            loc_node = row.css_first(".location, .listingItem__location")
            location = loc_node.text(strip=True) if loc_node else None

            img_node = row.css_first("img")
            thumb = img_node.attributes.get("src") if img_node else None

            out.append(Listing(
                item_id=f"{self.site_code}:{ad_id}",
                title=title,
                description="",
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
                raw={"ad_id": ad_id},
            ))
        return out

    @staticmethod
    def _parse_price(text: str) -> tuple[float | None, str]:
        if not text:
            return None, "unknown"
        low = text.lower()
        if "bieden" in low or "n.o.t.k" in low:
            ptype = "bidding"
        elif "gratis" in low:
            return 0.0, "free"
        else:
            ptype = "fixed"
        m = PRICE_RE.search(text)
        if not m:
            return None, ptype
        try:
            raw = m.group(1).replace(".", "").replace(",", ".")
            return float(raw), ptype
        except ValueError:
            return None, ptype
