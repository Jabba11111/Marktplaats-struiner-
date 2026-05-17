from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from ..models import Listing

log = structlog.get_logger(__name__)

# Public LRP search endpoint used by the Marktplaats website itself.
SEARCH_URL = "https://www.marktplaats.nl/lrp/api/search"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


def _ua() -> str:
    return random.choice(USER_AGENTS)


def _parse_price(price_info: dict | None) -> tuple[float | None, str]:
    if not price_info:
        return None, "unknown"
    cents = price_info.get("priceCents")
    ptype = (price_info.get("priceType") or "").lower()
    mapping = {
        "fixed": "fixed",
        "bidding": "bidding",
        "free": "free",
        "see_description": "see_description",
        "reserved": "reserved",
        "exchange": "exchange",
        "min_bid": "bidding",
        "notk": "see_description",
        "on_demand": "see_description",
    }
    kind = mapping.get(ptype, ptype or "unknown")
    if kind == "free":
        return 0.0, "free"
    if cents and cents > 0:
        return cents / 100.0, kind
    return None, kind


def _parse_listing(item: dict) -> Listing | None:
    item_id = item.get("itemId") or item.get("id")
    if not item_id:
        return None
    price, ptype = _parse_price(item.get("priceInfo"))

    url_path = item.get("vipUrl") or ""
    url = f"https://www.marktplaats.nl{url_path}" if url_path.startswith("/") else url_path

    posted_raw = item.get("date")
    try:
        posted_at = datetime.fromisoformat(posted_raw.replace("Z", "+00:00")) \
            if posted_raw else datetime.now(timezone.utc)
    except (ValueError, AttributeError):
        posted_at = datetime.now(timezone.utc)

    seller = (item.get("sellerInformation") or {}).get("sellerName")
    location = (item.get("location") or {}).get("cityName")

    images = item.get("imageUrls") or []
    thumb = images[0] if images else None
    if thumb and thumb.startswith("//"):
        thumb = "https:" + thumb

    return Listing(
        item_id=str(item_id),
        title=item.get("title", ""),
        description=item.get("description", "") or "",
        price=price,
        price_type=ptype,
        url=url,
        seller_name=seller,
        location=location,
        posted_at=posted_at,
        thumbnail_url=thumb,
        category_id=(item.get("categoryId")
                     or (item.get("categorySpecificObject") or {}).get("id")),
        category_name=item.get("categoryName"),
        raw=item,
    )


class MarktplaatsClient:
    def __init__(self, request_interval: float = 1.0):
        self._client = httpx.AsyncClient(
            timeout=20.0,
            headers={"Accept": "application/json", "Accept-Language": "nl-NL,nl"},
            follow_redirects=True,
        )
        self._last_request_at = 0.0
        self._request_interval = request_interval
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _throttle(self) -> None:
        async with self._lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            wait = self._request_interval - (now - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait + random.uniform(0, 0.3))
            self._last_request_at = asyncio.get_event_loop().time()

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    async def _get(self, params: dict) -> dict:
        await self._throttle()
        headers = {"User-Agent": _ua()}
        resp = await self._client.get(SEARCH_URL, params=params, headers=headers)
        if resp.status_code in (429, 403):
            log.warning("marktplaats_throttled", status=resp.status_code)
            resp.raise_for_status()
        resp.raise_for_status()
        return resp.json()

    async def search_newest(
        self,
        query: str,
        category_id: int | None = None,
        max_pages: int = 2,
        page_size: int = 30,
    ) -> AsyncIterator[Listing]:
        """Yield listings newest-first."""
        for page in range(max_pages):
            params = {
                "query": query,
                "offset": page * page_size,
                "limit": page_size,
                "sortBy": "SORT_INDEX",
                "sortOrder": "DECREASING",
                "viewOptions": "list-view",
            }
            if category_id:
                params["l1CategoryId"] = category_id

            try:
                data = await self._get(params)
            except Exception as e:
                log.error("marktplaats_search_failed", query=query, error=str(e))
                return

            items = data.get("listings") or data.get("items") or []
            if not items:
                return

            for raw in items:
                # Skip sponsored / dealer "topadvertentie" if priorityProduct flag set;
                # those are duplicates and not "newest".
                if raw.get("priorityProduct") in ("DAGTOPPER", "ADMARKT"):
                    continue
                listing = _parse_listing(raw)
                if listing is not None:
                    yield listing
