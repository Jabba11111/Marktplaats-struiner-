"""Reverb price guide for guitars, audio gear, synths."""
from __future__ import annotations

import httpx
import structlog

from ..models import Listing, Valuation
from .base import ValuationSource, median_price

log = structlog.get_logger(__name__)


class ReverbValuation(ValuationSource):
    name = "reverb"
    cache_hours = 24 * 3
    API_BASE = "https://api.reverb.com/api"

    def __init__(self, db, token: str | None):
        super().__init__(db)
        self.token = token

    async def _compute(self, listing: Listing) -> Valuation | None:
        if not self.token:
            return None

        # Reverb expects english-ish queries; brand names usually carry across.
        q = " ".join([t for t in listing.title.split() if len(t) > 2][:5])
        if not q:
            return None

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/hal+json",
            "Accept-Version": "3.0",
            "Content-Type": "application/json",
        }
        params = {
            "query": q,
            "state": "sold",
            "per_page": 30,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
                resp = await client.get(f"{self.API_BASE}/listings", params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            log.warning("reverb_fetch_failed", error=str(e), q=q)
            return None

        prices: list[float] = []
        for item in data.get("listings", []):
            price = item.get("price") or {}
            try:
                amount = float(price.get("amount", 0))
                currency = price.get("currency", "USD")
                # Crude EUR conversion; production should use an FX feed.
                if currency == "USD":
                    amount *= 0.93
                elif currency == "GBP":
                    amount *= 1.17
                if amount > 0:
                    prices.append(amount)
            except (TypeError, ValueError):
                continue

        m = median_price(prices)
        if m is None:
            return None
        return Valuation(
            estimated_value=m,
            confidence=min(1.0, len(prices) / 10),
            source=self.name,
            sample_size=len(prices),
            note=f"Reverb sold median (n={len(prices)}, EUR-approx)",
        )
