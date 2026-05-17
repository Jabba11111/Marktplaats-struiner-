"""Quoka.de — German classifieds."""
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

BASE = "https://www.quoka.de"
PRICE_RE = re.compile(r"([\d.,]+)\s*€|€\s*([\d.,]+)")
ANZEIGE_RE = re.compile(r"/anzeige/([^/]+)/(\d+)")


class QuokaSource:
    name = "quoka"
    country = "DE"
    site_code = "qk"
    requires_browser = False

    def __init__(self, request_interval: float = 3.0):
        self._client = httpx.AsyncClient(
            timeout=20.0,
            headers={
                "Accept": "text/html",
                "Accept-Language": "de-DE,de;q=0.9",
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
                await asyncio.sleep(wait + random.uniform(0, 0.8))
            self._last_at = asyncio.get_event_loop().time()

    async def search(
        self,
        query: str,
        max_pages: int = 1,
        category_id: int | None = None,
    ) -> AsyncIterator[Listing]:
        for page in range(1, max_pages + 1):
            url = f"{BASE}/qs/{quote_plus(query)}/?sortBy=date&page={page}"
            await self._throttle()
            try:
                resp = await self._client.get(url)
                resp.raise_for_status()
                html = resp.text
            except Exception as e:
                log.warning("quoka_fetch_failed", error=str(e))
                return
            for listing in self._parse(html):
                yield listing

    def _parse(self, html: str) -> list[Listing]:
        tree = HTMLParser(html)
        out: list[Listing] = []
        seen: set[str] = set()
        for a in tree.css("a[href*='/anzeige/']"):
            href = a.attributes.get("href", "")
            m = ANZEIGE_RE.search(href)
            if not m:
                continue
            ad_id = m.group(2)
            if ad_id in seen:
                continue
            seen.add(ad_id)
            url = href if href.startswith("http") else f"{BASE}{href}"
            title = a.text(strip=True)[:120] or "Quoka Anzeige"

            card = a
            for _ in range(4):
                if card.parent is None:
                    break
                card = card.parent

            price_node = card.css_first("[class*='price'], [class*='Price']")
            price, ptype = None, "fixed"
            if price_node:
                ptext = price_node.text(strip=True)
                if "vb" in ptext.lower() or "verhandelbar" in ptext.lower():
                    ptype = "bidding"
                pm = PRICE_RE.search(ptext)
                if pm:
                    raw = pm.group(1) or pm.group(2) or ""
                    try:
                        price = float(raw.replace(".", "").replace(",", "."))
                    except ValueError:
                        pass

            loc_node = card.css_first("[class*='location'], [class*='Location']")
            location = loc_node.text(strip=True) if loc_node else None

            img = card.css_first("img")
            thumb = (img.attributes.get("src") or img.attributes.get("data-src")
                     if img else None)

            out.append(Listing(
                item_id=f"{self.site_code}:{ad_id}",
                title=title, description="",
                price=price, price_type=ptype,
                url=url, seller_name=None,
                location=location, posted_at=datetime.now(timezone.utc),
                thumbnail_url=thumb, category_id=None, category_name=None,
                site=self.name, country=self.country,
                raw={"ad_id": ad_id},
            ))
            if len(out) >= 30:
                break
        return out
