"""Tweakers Pricewatch median price (NL hardware reference)."""
from __future__ import annotations

import re

import httpx
import structlog
from selectolax.parser import HTMLParser

from ..models import Listing, Valuation
from .base import ValuationSource, median_price

log = structlog.get_logger(__name__)

SEARCH_URL = "https://tweakers.net/pricewatch/zoeken/"
PRICE_RE = re.compile(r"€\s*([\d.,]+)")


class TweakersValuation(ValuationSource):
    """Scrape Pricewatch search-result prices for hardware/electronics.

    Pricewatch is a price-comparison index (retailer prices, not used),
    so this gives a RETAIL ceiling. For deal detection we use it as
    "max sensible price" — anything well below it on Marktplaats is
    interesting.
    """
    name = "tweakers"
    cache_hours = 24

    def __init__(self, db):
        super().__init__(db)

    def _query(self, listing: Listing) -> str:
        # Pricewatch matches on product name + spec. Strip noise words.
        noise = {"te", "koop", "voor", "met", "in", "goede", "staat", "nieuw"}
        tokens = [t for t in listing.title.lower().split()
                  if t not in noise and len(t) > 2]
        return " ".join(tokens[:6])

    async def _compute(self, listing: Listing) -> Valuation | None:
        q = self._query(listing)
        if not q:
            return None
        try:
            async with httpx.AsyncClient(
                timeout=15.0,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "nl-NL,nl;q=0.9",
                },
                follow_redirects=True,
            ) as client:
                resp = await client.get(SEARCH_URL, params={"keyword": q})
                resp.raise_for_status()
                html = resp.text
        except Exception as e:
            log.warning("tweakers_fetch_failed", error=str(e), q=q)
            return None

        prices = self._extract_prices(html)
        m = median_price(prices)
        if m is None:
            return None
        return Valuation(
            estimated_value=m,
            confidence=min(1.0, len(prices) / 5),
            source=self.name,
            sample_size=len(prices),
            note=f"Tweakers Pricewatch median of {len(prices)} retail listings",
        )

    @staticmethod
    def _extract_prices(html: str) -> list[float]:
        tree = HTMLParser(html)
        prices: list[float] = []
        # Pricewatch search-result cards have a price element.
        for node in tree.css("td.price, .price, [data-pricetype='lowest']"):
            text = node.text(strip=True)
            m = PRICE_RE.search(text)
            if not m:
                continue
            try:
                # Dutch format: "1.234,56" -> 1234.56
                raw = m.group(1).replace(".", "").replace(",", ".")
                price = float(raw)
                if 5 <= price <= 20_000:
                    prices.append(price)
            except ValueError:
                continue
        # Limit to first 15 so a noisy long-tail doesn't skew the median.
        return prices[:15]
