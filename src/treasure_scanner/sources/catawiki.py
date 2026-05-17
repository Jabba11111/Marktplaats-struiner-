"""Catawiki source — uses StealthBrowser (no public API)."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import AsyncIterator
from urllib.parse import quote

import structlog
from selectolax.parser import HTMLParser

from ..browser import StealthBrowser, BrowserClosedError
from ..models import Listing

log = structlog.get_logger(__name__)

PRICE_RE = re.compile(r"€\s*([\d.,]+)")
LOT_LINK_RE = re.compile(r"/l/(\d+)-")


class CatawikiSource:
    name = "catawiki"
    country = "NL"   # international, but NL-locale by default
    site_code = "cat"
    requires_browser = True

    def __init__(self, browser: StealthBrowser | None):
        self.browser = browser

    async def aclose(self) -> None:
        pass  # browser is shared, closed by main.py

    async def search(
        self,
        query: str,
        max_pages: int = 1,
        category_id: int | None = None,
    ) -> AsyncIterator[Listing]:
        if self.browser is None:
            return
        for page in range(1, max_pages + 1):
            page_q = f"&page={page}" if page > 1 else ""
            url = f"https://www.catawiki.com/nl/s?q={quote(query)}{page_q}"
            try:
                html = await self.browser.html(url, wait_for="main")
            except BrowserClosedError:
                return
            except Exception as e:
                log.warning("catawiki_search_failed", error=str(e), q=query)
                return
            for listing in self._parse(html):
                yield listing

    def _parse(self, html: str) -> list[Listing]:
        tree = HTMLParser(html)
        out: list[Listing] = []
        # Lot cards: <a href="/l/{id}-{slug}">...</a>
        seen_ids: set[str] = set()
        for card in tree.css("a[href*='/l/']"):
            href = card.attributes.get("href", "")
            m = LOT_LINK_RE.search(href)
            if not m:
                continue
            lot_id = m.group(1)
            if lot_id in seen_ids:
                continue
            seen_ids.add(lot_id)

            url = href if href.startswith("http") else f"https://www.catawiki.com{href}"

            title_node = card.css_first("h3, h4, [class*='title']")
            title = title_node.text(strip=True) if title_node else card.text(strip=True)[:120]

            price_node = card.css_first("[class*='Price'], [class*='price'], [data-testid*='price']")
            price = None
            ptype = "bidding"  # Catawiki lots are auctions
            if price_node:
                pm = PRICE_RE.search(price_node.text(strip=True))
                if pm:
                    try:
                        raw = pm.group(1).replace(".", "").replace(",", ".")
                        price = float(raw)
                    except ValueError:
                        pass

            img_node = card.css_first("img")
            thumb = None
            if img_node:
                thumb = (img_node.attributes.get("src")
                         or img_node.attributes.get("data-src"))

            out.append(Listing(
                item_id=f"{self.site_code}:{lot_id}",
                title=title,
                description="",
                price=price,
                price_type=ptype,
                url=url,
                seller_name="Catawiki",
                location=None,
                posted_at=datetime.now(timezone.utc),
                thumbnail_url=thumb,
                category_id=None,
                category_name=None,
                site=self.name,
                country=self.country,
                raw={"lot_id": lot_id, "auction_source": "catawiki"},
            ))
            if len(out) >= 30:
                break
        return out
