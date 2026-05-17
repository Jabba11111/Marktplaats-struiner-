"""MyDealz.de — German deal aggregator (Pepper.de family).

Pulls the RSS feed, which is the only stable + bot-friendly interface.
We treat each deal as a "listing" with merchant URL.
"""
from __future__ import annotations

import asyncio
import random
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import AsyncIterator
from urllib.parse import quote_plus

import httpx
import structlog

from ..models import Listing
from ..utils.throttle import Throttle

log = structlog.get_logger(__name__)

RSS_BASE = "https://www.mydealz.de/rss"
PRICE_RE = re.compile(r"([\d.,]+)\s*€|€\s*([\d.,]+)")


class MyDealzSource:
    name = "mydealz"
    country = "DE"
    site_code = "md"
    requires_browser = False

    def __init__(self, request_interval: float = 5.0):
        self._client = httpx.AsyncClient(
            timeout=20.0,
            headers={
                "Accept": "application/rss+xml,application/xml,text/xml",
                "Accept-Language": "de-DE,de;q=0.9",
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
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
        # MyDealz exposes per-search RSS at /rss/search?q=...
        url = f"{RSS_BASE}/search?q={quote_plus(query)}"
        await self.throttle.wait()
        try:
            resp = await self._client.get(url)
            self.throttle.record_response(resp.status_code)
            resp.raise_for_status()
            xml_text = resp.text
        except Exception as e:
            self.throttle.record_failure()
            log.warning("mydealz_fetch_failed", error=str(e), q=query)
            return
        for listing in self._parse(xml_text):
            yield listing

    def _parse(self, xml_text: str) -> list[Listing]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            log.warning("mydealz_parse_failed", error=str(e))
            return []
        out: list[Listing] = []
        for item in root.iter("item"):
            link = (item.findtext("link") or "").strip()
            title = (item.findtext("title") or "").strip()
            guid = (item.findtext("guid") or link).strip()
            if not guid:
                continue
            # Use the numeric tail of the guid/url as the id.
            m = re.search(r"(\d+)$", guid)
            deal_id = m.group(1) if m else guid.rsplit("/", 1)[-1]

            description = (item.findtext("description") or "").strip()
            pub = item.findtext("pubDate")
            try:
                posted_at = parsedate_to_datetime(pub) if pub else \
                            datetime.now(timezone.utc)
            except (TypeError, ValueError):
                posted_at = datetime.now(timezone.utc)

            price = self._extract_price(f"{title} {description}")

            out.append(Listing(
                item_id=f"{self.site_code}:{deal_id}",
                title=title[:140],
                description=description[:500],
                price=price,
                price_type="fixed",
                url=link,
                seller_name=None,
                location=None,
                posted_at=posted_at,
                thumbnail_url=None,
                category_id=None,
                category_name=None,
                site=self.name,
                country=self.country,
                raw={"deal_id": deal_id, "guid": guid},
            ))
            if len(out) >= 50:
                break
        return out

    @staticmethod
    def _extract_price(text: str) -> float | None:
        m = PRICE_RE.search(text)
        if not m:
            return None
        raw = m.group(1) or m.group(2) or ""
        try:
            return float(raw.replace(".", "").replace(",", "."))
        except ValueError:
            return None
