"""Aukro.de — German auction site.

NOTE: the Aukro.de domain is in a transitional state (the brand is
active in CZ/PL/SK; .de may redirect). Source returns no results if
the site responds with a redirect/404 — that is the correct silent-
failure mode. Enable only when you confirm the site is reachable.
"""
from __future__ import annotations

import asyncio
import random
import re
from datetime import datetime, timezone
from typing import AsyncIterator
from urllib.parse import quote_plus

import httpx
import structlog
from selectolax.parser import HTMLParser

from ..models import Listing
from ..utils.throttle import Throttle

log = structlog.get_logger(__name__)

BASE = "https://www.aukro.de"
PRICE_RE = re.compile(r"([\d.,]+)\s*€|€\s*([\d.,]+)")
OFFER_RE = re.compile(r"/offer/[^/]*?(\d+)")


class AukroSource:
    name = "aukro"
    country = "DE"
    site_code = "ak"
    requires_browser = False

    def __init__(self, request_interval: float = 4.0):
        self._client = httpx.AsyncClient(
            timeout=20.0,
            headers={
                "Accept": "text/html",
                "Accept-Language": "de-DE,de;q=0.9",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            },
            follow_redirects=True,
        )
        self.throttle = Throttle(request_interval)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(
        self,
        query: str,
        max_pages: int = 1,
        category_id: int | None = None,
    ) -> AsyncIterator[Listing]:
        for page in range(1, max_pages + 1):
            url = f"{BASE}/listing?search={quote_plus(query)}&page={page}"
            await self.throttle.wait()
            try:
                resp = await self._client.get(url)
                self.throttle.record_response(resp.status_code)
                if resp.status_code >= 400:
                    log.info("aukro_unavailable", status=resp.status_code)
                    return
                html = resp.text
            except Exception as e:
                self.throttle.record_failure()
                log.warning("aukro_fetch_failed", error=str(e))
                return
            for listing in self._parse(html):
                yield listing

    def _parse(self, html: str) -> list[Listing]:
        tree = HTMLParser(html)
        out: list[Listing] = []
        seen: set[str] = set()
        for a in tree.css("a[href*='/offer/']"):
            href = a.attributes.get("href", "")
            m = OFFER_RE.search(href)
            if not m:
                continue
            offer_id = m.group(1)
            if offer_id in seen:
                continue
            seen.add(offer_id)
            url = href if href.startswith("http") else f"{BASE}{href}"
            title = a.text(strip=True)[:120] or "Aukro offer"

            card = a
            for _ in range(4):
                if card.parent is None:
                    break
                card = card.parent
            price_node = card.css_first("[class*='price'], [class*='Price']")
            price = None
            if price_node:
                pm = PRICE_RE.search(price_node.text(strip=True))
                if pm:
                    raw = pm.group(1) or pm.group(2) or ""
                    try:
                        price = float(raw.replace(".", "").replace(",", "."))
                    except ValueError:
                        pass

            img = card.css_first("img")
            thumb = img.attributes.get("src") if img else None

            out.append(Listing(
                item_id=f"{self.site_code}:{offer_id}",
                title=title, description="",
                price=price, price_type="bidding",
                url=url, seller_name=None,
                location=None, posted_at=datetime.now(timezone.utc),
                thumbnail_url=thumb, category_id=None, category_name=None,
                site=self.name, country=self.country,
                raw={"offer_id": offer_id, "auction_source": "aukro"},
            ))
            if len(out) >= 30:
                break
        return out
