"""Catawiki closed-auction prices for antiek/kunst/horloges.

No public API. Uses StealthBrowser to scrape search results.
"""
from __future__ import annotations

import re
from urllib.parse import quote

import structlog
from selectolax.parser import HTMLParser

from ..browser import StealthBrowser, BrowserClosedError
from ..models import Listing, Valuation
from .base import ValuationSource, median_price

log = structlog.get_logger(__name__)

PRICE_RE = re.compile(r"€\s*([\d.,]+)")


class CatawikiValuation(ValuationSource):
    name = "catawiki"
    cache_hours = 24 * 7

    def __init__(self, db, browser: StealthBrowser | None):
        super().__init__(db)
        self.browser = browser

    async def _compute(self, listing: Listing) -> Valuation | None:
        if self.browser is None:
            return None

        q = " ".join([t for t in listing.title.split() if len(t) > 2][:5])
        if not q:
            return None

        url = f"https://www.catawiki.com/nl/s?q={quote(q)}"
        try:
            html = await self.browser.html(url, wait_for="main")
        except BrowserClosedError:
            return None
        except Exception as e:
            log.warning("catawiki_fetch_failed", error=str(e), q=q)
            return None

        prices = self._extract_prices(html)
        m = median_price(prices)
        if m is None:
            return None
        return Valuation(
            estimated_value=m,
            confidence=min(1.0, len(prices) / 8),
            source=self.name,
            sample_size=len(prices),
            note=f"Catawiki median (n={len(prices)}) for '{q}'",
        )

    @staticmethod
    def _extract_prices(html: str) -> list[float]:
        tree = HTMLParser(html)
        prices: list[float] = []
        # Catawiki lot cards mark price with data-testid or class containing "price"
        for node in tree.css("[data-testid*='price'], [class*='Price'], [class*='price']"):
            text = node.text(strip=True)
            for m in PRICE_RE.finditer(text):
                try:
                    raw = m.group(1).replace(".", "").replace(",", ".")
                    p = float(raw)
                    if 10 <= p <= 50_000:
                        prices.append(p)
                except ValueError:
                    continue
        # Dedup while preserving order; cap at 20.
        seen = set()
        unique = []
        for p in prices:
            if p in seen:
                continue
            seen.add(p)
            unique.append(p)
            if len(unique) >= 20:
                break
        return unique
