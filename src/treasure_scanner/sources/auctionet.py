"""Auctionet — Swedish/DE/intl art & antiek auction platform.

Has a public JSON search endpoint (the front-end uses it directly):
  https://auctionet.com/en/search?q=...&format=json
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from ..models import Listing

log = structlog.get_logger(__name__)

BASE = "https://auctionet.com"
SEARCH_URL = f"{BASE}/en/search"


class AuctionetSource:
    name = "auctionet"
    country = "DE"  # multi-country; we mark DE so DE-watchers pick it up
    site_code = "an"
    requires_browser = False

    def __init__(self, request_interval: float = 2.0):
        self._client = httpx.AsyncClient(
            timeout=20.0,
            headers={
                "Accept": "application/json",
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
                await asyncio.sleep(wait + random.uniform(0, 0.6))
            self._last_at = asyncio.get_event_loop().time()

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=2, min=2, max=15),
           reraise=True)
    async def _get(self, params: dict) -> dict:
        await self._throttle()
        resp = await self._client.get(SEARCH_URL, params=params)
        resp.raise_for_status()
        return resp.json()

    async def search(
        self,
        query: str,
        max_pages: int = 1,
        category_id: int | None = None,
    ) -> AsyncIterator[Listing]:
        for page in range(1, max_pages + 1):
            params = {
                "q": query, "format": "json",
                "page": page, "order": "newest_first",
                "is_buy_now": "false",
            }
            try:
                data = await self._get(params)
            except Exception as e:
                log.warning("auctionet_fetch_failed", q=query, error=str(e))
                return
            items = (data.get("items") or data.get("lots")
                     or data.get("results") or [])
            for raw in items:
                listing = self._parse(raw)
                if listing is not None:
                    yield listing

    def _parse(self, raw: dict) -> Listing | None:
        lot_id = raw.get("id") or raw.get("lot_id")
        if not lot_id:
            return None
        title = raw.get("title") or raw.get("name") or ""
        slug = raw.get("slug") or str(lot_id)
        url = raw.get("url") or f"{BASE}/en/items/{lot_id}-{slug}"

        # current_bid is in minor units? Auctionet returns EUR/SEK in major.
        bid = raw.get("current_bid") or raw.get("starting_bid") or 0
        currency = raw.get("currency") or "EUR"
        try:
            price = float(bid) if bid else None
        except (TypeError, ValueError):
            price = None
        if price and currency == "SEK":
            price *= 0.087  # rough SEK→EUR
        elif price and currency == "USD":
            price *= 0.93
        elif price and currency == "GBP":
            price *= 1.17

        ends_iso = raw.get("ends_at") or raw.get("end_date")
        thumb = None
        images = raw.get("images") or raw.get("photos") or []
        if images:
            first = images[0]
            if isinstance(first, str):
                thumb = first
            elif isinstance(first, dict):
                thumb = (first.get("normal") or first.get("url")
                         or first.get("large"))

        return Listing(
            item_id=f"{self.site_code}:{lot_id}",
            title=title,
            description=raw.get("description") or "",
            price=price,
            price_type="bidding",
            url=url if url.startswith("http") else f"{BASE}{url}",
            seller_name=(raw.get("company") or {}).get("name")
                if isinstance(raw.get("company"), dict)
                else raw.get("company"),
            location=raw.get("city") or raw.get("country_code"),
            posted_at=datetime.now(timezone.utc),
            thumbnail_url=thumb,
            category_id=None,
            category_name=raw.get("category"),
            site=self.name,
            country=self.country,
            raw={**raw, "ends_at": ends_iso, "auction_source": "auctionet"},
        )
