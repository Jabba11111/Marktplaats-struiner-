"""Hood.de — German general marketplace (auctions + fixed price)."""
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

from ..models import Listing

log = structlog.get_logger(__name__)

BASE = "https://www.hood.de"
PRICE_RE = re.compile(r"([\d.,]+)\s*€|€\s*([\d.,]+)")
ITEM_RE = re.compile(r"/item/(\d+)")


class HoodSource:
    name = "hood"
    country = "DE"
    site_code = "hd"
    requires_browser = False

    def __init__(self, request_interval: float = 3.0):
        self._client = httpx.AsyncClient(
            timeout=20.0,
            headers={
                "Accept": "text/html",
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
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
                await asyncio.sleep(wait + random.uniform(0, 0.8))
            self._last_at = asyncio.get_event_loop().time()

    async def search(
        self,
        query: str,
        max_pages: int = 1,
        category_id: int | None = None,
    ) -> AsyncIterator[Listing]:
        for page in range(1, max_pages + 1):
            params = {"search": query, "p": page, "sort": "newest"}
            await self._throttle()
            try:
                resp = await self._client.get(f"{BASE}/search/", params=params)
                resp.raise_for_status()
                html = resp.text
            except Exception as e:
                log.warning("hood_fetch_failed", error=str(e), q=query)
                return
            for listing in self._parse(html):
                yield listing

    def _parse(self, html: str) -> list[Listing]:
        tree = HTMLParser(html)
        out: list[Listing] = []
        seen: set[str] = set()
        for a in tree.css("a[href*='/item/']"):
            href = a.attributes.get("href", "")
            m = ITEM_RE.search(href)
            if not m:
                continue
            item_id = m.group(1)
            if item_id in seen:
                continue
            seen.add(item_id)

            url = href if href.startswith("http") else f"{BASE}{href}"
            title = a.text(strip=True)[:120] or "Hood item"
            if not title or title == "Hood item":
                # The naked link may not have the title; walk to parent.
                title_node = a.parent.css_first("h2, h3, .title") if a.parent else None
                if title_node:
                    title = title_node.text(strip=True)[:120]

            # Walk up for the price.
            card = a
            for _ in range(4):
                if card.parent is None:
                    break
                card = card.parent
            price_node = card.css_first(".price, [class*='Price'], .product-price")
            price, ptype = self._extract_price(
                price_node.text(strip=True) if price_node else ""
            )

            img = card.css_first("img")
            thumb = (img.attributes.get("src") or img.attributes.get("data-src")
                     if img else None)

            out.append(Listing(
                item_id=f"{self.site_code}:{item_id}",
                title=title, description="",
                price=price, price_type=ptype,
                url=url, seller_name=None,
                location=None, posted_at=datetime.now(timezone.utc),
                thumbnail_url=thumb, category_id=None, category_name=None,
                site=self.name, country=self.country,
                raw={"item_id": item_id},
            ))
            if len(out) >= 30:
                break
        return out

    @staticmethod
    def _extract_price(text: str) -> tuple[float | None, str]:
        if not text:
            return None, "unknown"
        low = text.lower()
        if "gebot" in low or "auktion" in low:
            ptype = "bidding"
        elif "kostenlos" in low:
            return 0.0, "free"
        else:
            ptype = "fixed"
        m = PRICE_RE.search(text)
        if not m:
            return None, ptype
        raw = (m.group(1) or m.group(2) or "")
        try:
            return float(raw.replace(".", "").replace(",", ".")), ptype
        except ValueError:
            return None, ptype
