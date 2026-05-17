from __future__ import annotations

from ..models import Listing, Valuation
from ..sources.marktplaats import MarktplaatsClient
from .base import ValuationSource, median_price


class MarktplaatsMedianValuation(ValuationSource):
    """Median price of comparable active Marktplaats listings."""
    name = "marktplaats_median"

    def __init__(self, db, client: MarktplaatsClient):
        super().__init__(db)
        self.client = client

    def _comparable_query(self, listing: Listing) -> str:
        # Use first 6 significant words from title.
        words = [w for w in listing.title.split() if len(w) > 2]
        return " ".join(words[:6])

    async def _compute(self, listing: Listing) -> Valuation | None:
        query = self._comparable_query(listing)
        if not query:
            return None
        prices: list[float] = []
        async for cand in self.client.search_newest(
            query=query, category_id=listing.category_id, max_pages=2,
        ):
            if cand.item_id == listing.item_id:
                continue
            if cand.price and cand.price > 0:
                prices.append(cand.price)
            if len(prices) >= 20:
                break

        m = median_price(prices)
        if m is None:
            return None
        return Valuation(
            estimated_value=m,
            confidence=min(1.0, len(prices) / 10),
            source=self.name,
            sample_size=len(prices),
            note=f"median of {len(prices)} active listings",
        )
