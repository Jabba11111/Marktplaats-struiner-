"""Adevinta-family marketplaces: Marktplaats.nl, 2dehands.be, 2ememain.be.

All three share the same backend ("LRP" search API), only the public
host and locale differ. One class, three configured instances.
"""
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

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


def _ua() -> str:
    return random.choice(USER_AGENTS)


def _parse_price(price_info: dict | None) -> tuple[float | None, str]:
    if not price_info:
        return None, "unknown"
    cents = price_info.get("priceCents")
    ptype = (price_info.get("priceType") or "").lower()
    mapping = {
        "fixed": "fixed", "bidding": "bidding", "free": "free",
        "see_description": "see_description", "reserved": "reserved",
        "exchange": "exchange", "min_bid": "bidding",
        "notk": "see_description", "on_demand": "see_description",
    }
    kind = mapping.get(ptype, ptype or "unknown")
    if kind == "free":
        return 0.0, "free"
    if cents and cents > 0:
        return cents / 100.0, kind
    return None, kind


class AdevintaMarketplace:
    """Configurable Adevinta marketplace source (Marktplaats / 2dehands / 2ememain)."""

    requires_browser = False

    def __init__(
        self,
        name: str,
        host: str,
        site_code: str,
        country: str,
        accept_language: str,
        request_interval: float = 1.0,
    ):
        self.name = name
        self.host = host
        self.site_code = site_code
        self.country = country
        self.accept_language = accept_language
        self.search_url = f"https://{host}/lrp/api/search"

        self._client = httpx.AsyncClient(
            timeout=20.0,
            headers={
                "Accept": "application/json",
                "Accept-Language": accept_language,
            },
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
        resp = await self._client.get(
            self.search_url, params=params, headers={"User-Agent": _ua()},
        )
        if resp.status_code in (429, 403):
            log.warning("adevinta_throttled", site=self.name, status=resp.status_code)
            resp.raise_for_status()
        resp.raise_for_status()
        return resp.json()

    def _parse_listing(self, item: dict) -> Listing | None:
        item_id = item.get("itemId") or item.get("id")
        if not item_id:
            return None
        price, ptype = _parse_price(item.get("priceInfo"))

        url_path = item.get("vipUrl") or ""
        url = f"https://{self.host}{url_path}" if url_path.startswith("/") else url_path

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

        # Prefix item_id with site_code so it's globally unique across sources.
        prefixed_id = (f"{self.site_code}:{item_id}"
                       if self.site_code != "mkpl" else str(item_id))

        return Listing(
            item_id=prefixed_id,
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
            site=self.name,
            country=self.country,
            raw=item,
        )

    async def search(
        self,
        query: str,
        max_pages: int = 2,
        category_id: int | None = None,
        page_size: int = 30,
    ) -> AsyncIterator[Listing]:
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
                log.error("adevinta_search_failed", site=self.name,
                          query=query, error=str(e))
                return

            items = data.get("listings") or data.get("items") or []
            if not items:
                return

            for raw in items:
                if raw.get("priorityProduct") in ("DAGTOPPER", "ADMARKT"):
                    continue
                listing = self._parse_listing(raw)
                if listing is not None:
                    yield listing

    # backwards-compat alias used by valuation/marktplaats_median.py
    search_newest = search


# Convenience factories — keep call sites simple in main.py.

def make_marktplaats(request_interval: float = 1.0) -> AdevintaMarketplace:
    return AdevintaMarketplace(
        name="marktplaats", host="www.marktplaats.nl", site_code="mkpl",
        country="NL", accept_language="nl-NL,nl;q=0.9",
        request_interval=request_interval,
    )


def make_2dehands(request_interval: float = 1.5) -> AdevintaMarketplace:
    return AdevintaMarketplace(
        name="2dehands", host="www.2dehands.be", site_code="2dh",
        country="BE", accept_language="nl-BE,nl;q=0.9,fr-BE;q=0.7",
        request_interval=request_interval,
    )


def make_2ememain(request_interval: float = 1.5) -> AdevintaMarketplace:
    return AdevintaMarketplace(
        name="2ememain", host="www.2ememain.be", site_code="2em",
        country="BE", accept_language="fr-BE,fr;q=0.9,nl-BE;q=0.7",
        request_interval=request_interval,
    )


# Back-compat alias so existing imports (and tests) still work.
class MarktplaatsClient(AdevintaMarketplace):
    def __init__(self, request_interval: float = 1.0):
        super().__init__(
            name="marktplaats", host="www.marktplaats.nl", site_code="mkpl",
            country="NL", accept_language="nl-NL,nl;q=0.9",
            request_interval=request_interval,
        )
