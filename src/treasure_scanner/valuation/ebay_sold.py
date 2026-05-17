from __future__ import annotations

import httpx
import structlog

from ..models import Listing, Valuation
from .base import ValuationSource, median_price

log = structlog.get_logger(__name__)


class EbaySoldValuation(ValuationSource):
    """eBay sold listings via Browse API. Requires EBAY_APP_ID OAuth token.

    NOTE: this is a stub that hits the public Finding API endpoint.
    For real use, register at https://developer.ebay.com/ and obtain an
    OAuth bearer token; swap `findCompletedItems` for the Browse API
    `/buy/browse/v1/item_summary/search?filter=soldItemsOnly:true`.
    """
    name = "ebay_sold"

    FINDING_URL = "https://svcs.ebay.com/services/search/FindingService/v1"

    def __init__(self, db, app_id: str | None):
        super().__init__(db)
        self.app_id = app_id

    async def _compute(self, listing: Listing) -> Valuation | None:
        if not self.app_id:
            return None

        query = " ".join([w for w in listing.title.split() if len(w) > 2][:6])
        if not query:
            return None

        params = {
            "OPERATION-NAME": "findCompletedItems",
            "SERVICE-VERSION": "1.13.0",
            "SECURITY-APPNAME": self.app_id,
            "RESPONSE-DATA-FORMAT": "JSON",
            "keywords": query,
            "itemFilter(0).name": "SoldItemsOnly",
            "itemFilter(0).value": "true",
            "paginationInput.entriesPerPage": "30",
            "GLOBAL-ID": "EBAY-NL",
        }
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(self.FINDING_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            log.warning("ebay_sold_failed", error=str(e))
            return None

        try:
            items = (data["findCompletedItemsResponse"][0]
                     ["searchResult"][0].get("item", []))
        except (KeyError, IndexError):
            return None

        prices: list[float] = []
        for it in items:
            try:
                amount = float(it["sellingStatus"][0]
                               ["currentPrice"][0]["__value__"])
                currency = it["sellingStatus"][0]["currentPrice"][0]["@currencyId"]
                # Crude EUR conversion only for common cases.
                if currency == "USD":
                    amount *= 0.93
                elif currency == "GBP":
                    amount *= 1.17
                prices.append(amount)
            except (KeyError, IndexError, ValueError):
                continue

        m = median_price(prices)
        if m is None:
            return None
        return Valuation(
            estimated_value=m,
            confidence=min(1.0, len(prices) / 10),
            source=self.name,
            sample_size=len(prices),
            note=f"median of {len(prices)} eBay sold (EUR-approx)",
        )
