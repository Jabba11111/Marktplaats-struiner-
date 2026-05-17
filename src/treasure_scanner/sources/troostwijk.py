"""Troostwijk Auctions (TBAuctions) source.

Auction site, so each lot has an `ends_at` deadline. Lots get a slight
score bonus when ending soon (<24h) because that's the highest-signal
window for low bids.

JSON API discovered from the public search page; if it changes, fall
back to stealth-browser HTML scraping. Search is on:
https://www.tbauctions.com/api/search-api/lots
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncIterator

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from ..models import Listing

log = structlog.get_logger(__name__)

API_URL = "https://www.tbauctions.com/api/search-api/lots"


class TroostwijkClient:
    def __init__(self, request_interval: float = 2.0):
        self._client = httpx.AsyncClient(
            timeout=20.0,
            headers={
                "Accept": "application/json",
                "Accept-Language": "nl-NL,nl",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            },
            follow_redirects=True,
        )
        self.request_interval = request_interval

    async def aclose(self) -> None:
        await self._client.aclose()

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=2, min=2, max=15),
           reraise=True)
    async def _get(self, params: dict) -> dict:
        resp = await self._client.get(API_URL, params=params)
        resp.raise_for_status()
        return resp.json()

    async def search(
        self,
        query: str,
        max_pages: int = 1,
        page_size: int = 30,
    ) -> AsyncIterator[Listing]:
        """Yield Troostwijk lots matching `query`, newest first."""
        for page in range(max_pages):
            params = {
                "q": query,
                "offset": page * page_size,
                "limit": page_size,
                "sort": "starting_at_desc",
                "site": "tba-nl",
            }
            try:
                data = await self._get(params)
            except Exception as e:
                log.warning("troostwijk_search_failed", q=query, error=str(e))
                return

            for raw in data.get("lots", []) or data.get("results", []):
                listing = self._parse(raw)
                if listing is not None:
                    yield listing

    @staticmethod
    def _parse(raw: dict) -> Listing | None:
        lot_id = raw.get("id") or raw.get("lotId")
        if not lot_id:
            return None
        title = raw.get("title") or raw.get("name") or ""
        description = raw.get("description") or ""

        # Auctions use "current bid". When there's no bid yet, fall back
        # to starting bid; mark price_type accordingly.
        bid = raw.get("currentBid") or raw.get("startingBid") or 0
        try:
            price = float(bid) / 100 if isinstance(bid, int) and bid > 1000 else float(bid)
        except (TypeError, ValueError):
            price = None
        ptype = "bidding"

        slug = raw.get("slug") or str(lot_id)
        url = f"https://www.tbauctions.com/nl/l/{slug}"

        ends_iso = raw.get("endsAt") or raw.get("end_time")
        try:
            ends_at = (datetime.fromisoformat(ends_iso.replace("Z", "+00:00"))
                       if ends_iso else None)
        except (ValueError, AttributeError):
            ends_at = None

        images = raw.get("images") or []
        thumb = None
        if images:
            first = images[0]
            thumb = first if isinstance(first, str) else first.get("url")

        listing = Listing(
            item_id=f"tba:{lot_id}",
            title=title,
            description=description,
            price=price,
            price_type=ptype,
            url=url,
            seller_name="Troostwijk",
            location=raw.get("location"),
            posted_at=datetime.now(timezone.utc),
            thumbnail_url=thumb,
            category_id=None,
            category_name=raw.get("categoryName"),
            raw={**raw, "ends_at": ends_at.isoformat() if ends_at else None,
                 "auction_source": "troostwijk"},
        )
        return listing
