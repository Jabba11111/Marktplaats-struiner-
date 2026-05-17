"""Onlineveilingmeester (OVM, onlineveilingmeester.nl)."""
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

BASE = "https://www.onlineveilingmeester.nl"
PRICE_RE = re.compile(r"€\s*([\d.,]+)")
KAVEL_RE = re.compile(r"/kavels?/(\d+)")


class OVMSource:
    name = "ovm"
    country = "NL"
    site_code = "ovm"
    requires_browser = False

    def __init__(self, request_interval: float = 3.0):
        self._client = httpx.AsyncClient(
            timeout=20.0,
            headers={
                "Accept": "text/html",
                "Accept-Language": "nl-NL,nl;q=0.9",
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
            url = f"{BASE}/zoeken?q={quote_plus(query)}&pagina={page}"
            await self._throttle()
            try:
                resp = await self._client.get(url)
                resp.raise_for_status()
                html = resp.text
            except Exception as e:
                log.warning("ovm_fetch_failed", error=str(e), url=url)
                return
            for listing in self._parse(html):
                yield listing

    def _parse(self, html: str) -> list[Listing]:
        tree = HTMLParser(html)
        out: list[Listing] = []
        seen: set[str] = set()
        for a in tree.css("a[href*='/kavel'], a[href*='/kavels/']"):
            href = a.attributes.get("href", "")
            m = KAVEL_RE.search(href)
            if not m:
                continue
            lot_id = m.group(1)
            if lot_id in seen:
                continue
            seen.add(lot_id)

            url = href if href.startswith("http") else f"{BASE}{href}"
            title = a.text(strip=True)[:120] or "OVM kavel"

            # Walk up to nearest card-like parent for price/image.
            card = a
            for _ in range(4):
                if card.parent is None:
                    break
                card = card.parent
            price_node = card.css_first("[class*='price'], [class*='Price'], .bid")
            price = None
            if price_node:
                pm = PRICE_RE.search(price_node.text(strip=True))
                if pm:
                    try:
                        raw = pm.group(1).replace(".", "").replace(",", ".")
                        price = float(raw)
                    except ValueError:
                        pass

            img = card.css_first("img")
            thumb = (img.attributes.get("src") or img.attributes.get("data-src")
                     if img else None)

            out.append(Listing(
                item_id=f"{self.site_code}:{lot_id}",
                title=title, description="",
                price=price, price_type="bidding",
                url=url, seller_name="OVM",
                location=None, posted_at=datetime.now(timezone.utc),
                thumbnail_url=thumb, category_id=None, category_name=None,
                site=self.name, country=self.country,
                raw={"lot_id": lot_id, "auction_source": "ovm"},
            ))
            if len(out) >= 30:
                break
        return out
