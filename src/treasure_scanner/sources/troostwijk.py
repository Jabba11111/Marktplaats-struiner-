"""TBAuctions family — Troostwijk (NL) and Vavato (BE).

Same backend API, different `site` parameter.
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


class TBAuctionsBase:
    requires_browser = False

    def __init__(
        self,
        name: str,
        site_code: str,
        country: str,
        tba_site: str,
        accept_language: str,
        request_interval: float = 2.0,
    ):
        self.name = name
        self.site_code = site_code
        self.country = country
        self.tba_site = tba_site
        self.request_interval = request_interval
        self._client = httpx.AsyncClient(
            timeout=20.0,
            headers={
                "Accept": "application/json",
                "Accept-Language": accept_language,
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            },
            follow_redirects=True,
        )

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
        category_id: int | None = None,
        page_size: int = 30,
    ) -> AsyncIterator[Listing]:
        for page in range(max_pages):
            params = {
                "q": query,
                "offset": page * page_size,
                "limit": page_size,
                "sort": "starting_at_desc",
                "site": self.tba_site,
            }
            try:
                data = await self._get(params)
            except Exception as e:
                log.warning("tba_search_failed", site=self.name,
                            q=query, error=str(e))
                return
            for raw in data.get("lots", []) or data.get("results", []):
                listing = self._parse(raw)
                if listing is not None:
                    yield listing

    def _parse(self, raw: dict) -> Listing | None:
        lot_id = raw.get("id") or raw.get("lotId")
        if not lot_id:
            return None
        title = raw.get("title") or raw.get("name") or ""
        description = raw.get("description") or ""

        bid = raw.get("currentBid") or raw.get("startingBid") or 0
        try:
            price = (float(bid) / 100
                     if isinstance(bid, int) and bid > 1000 else float(bid))
        except (TypeError, ValueError):
            price = None

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

        return Listing(
            item_id=f"{self.site_code}:{lot_id}",
            title=title,
            description=description,
            price=price,
            price_type="bidding",
            url=url,
            seller_name=self.name.title(),
            location=raw.get("location"),
            posted_at=datetime.now(timezone.utc),
            thumbnail_url=thumb,
            category_id=None,
            category_name=raw.get("categoryName"),
            site=self.name,
            country=self.country,
            raw={**raw, "ends_at": ends_at.isoformat() if ends_at else None,
                 "auction_source": self.name},
        )


class TroostwijkClient(TBAuctionsBase):
    def __init__(self, request_interval: float = 2.0):
        super().__init__(
            name="troostwijk", site_code="tba", country="NL",
            tba_site="tba-nl", accept_language="nl-NL,nl;q=0.9",
            request_interval=request_interval,
        )


class VavatoSource(TBAuctionsBase):
    def __init__(self, request_interval: float = 2.0):
        super().__init__(
            name="vavato", site_code="vav", country="BE",
            tba_site="vavato-be", accept_language="nl-BE,nl;q=0.9,fr-BE;q=0.7",
            request_interval=request_interval,
        )
