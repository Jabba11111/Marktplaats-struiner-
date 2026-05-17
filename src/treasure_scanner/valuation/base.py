from __future__ import annotations

import statistics
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

from ..db import Database
from ..models import Listing, Valuation


class ValuationSource(ABC):
    name: str = "base"
    cache_hours: int = 24 * 7  # 7 days

    def __init__(self, db: Database):
        self.db = db

    def _cache_key(self, listing: Listing) -> str:
        # Normalize: source + first 80 chars of title (lower, alnum)
        title = "".join(c.lower() for c in listing.title if c.isalnum() or c.isspace())
        title = " ".join(title.split())[:80]
        return f"{self.name}::{title}"

    async def value(self, listing: Listing) -> Valuation | None:
        key = self._cache_key(listing)
        cached = self.db.get_cached_valuation(key)
        if cached:
            est, sample, src = cached
            return Valuation(
                estimated_value=est, confidence=min(1.0, sample / 10),
                source=src, sample_size=sample, note="cached",
            )

        result = await self._compute(listing)
        if result and result.sample_size > 0:
            expires = (datetime.now(timezone.utc) +
                       timedelta(hours=self.cache_hours)).isoformat()
            self.db.cache_valuation(
                key, result.estimated_value, result.sample_size,
                result.source, expires,
            )
        return result

    @abstractmethod
    async def _compute(self, listing: Listing) -> Valuation | None: ...


def median_price(prices: list[float]) -> float | None:
    valid = [p for p in prices if p is not None and p > 0]
    if not valid:
        return None
    return float(statistics.median(valid))
