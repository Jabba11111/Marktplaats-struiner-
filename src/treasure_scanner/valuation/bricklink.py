"""BrickLink price guide for LEGO sets (last-6-months sold average).

BrickLink API is OAuth1; requires consumer key/secret + token/secret
registered at https://www.bricklink.com/v2/api/welcome.page.
"""
from __future__ import annotations

import re

import httpx
import structlog

from ..models import Listing, Valuation
from .base import ValuationSource

log = structlog.get_logger(__name__)

SET_RE = re.compile(r"\b(\d{4,5})(?:-\d)?\b")  # LEGO set numbers, e.g. 10220, 75192-1


class BrickLinkValuation(ValuationSource):
    name = "bricklink"
    cache_hours = 24 * 3

    def __init__(self, db, consumer_key: str | None, consumer_secret: str | None,
                 token: str | None, token_secret: str | None):
        super().__init__(db)
        self.key = consumer_key
        self.secret = consumer_secret
        self.token = token
        self.token_secret = token_secret

    def _is_configured(self) -> bool:
        return all([self.key, self.secret, self.token, self.token_secret])

    def _extract_set_number(self, listing: Listing) -> str | None:
        text = f"{listing.title} {listing.description}"
        m = SET_RE.search(text)
        return m.group(1) if m else None

    async def _compute(self, listing: Listing) -> Valuation | None:
        if not self._is_configured():
            return None
        set_no = self._extract_set_number(listing)
        if not set_no:
            return None

        try:
            # Lazy import: requests-oauthlib is hairy to async; for the
            # tiny request volume here we wrap a sync call.
            from requests_oauthlib import OAuth1Session  # type: ignore
        except ImportError:
            log.warning("bricklink_oauth_lib_missing")
            return None

        url = f"https://api.bricklink.com/api/store/v1/items/SET/{set_no}-1/price"
        params = {"guide_type": "sold", "new_or_used": "U"}
        try:
            session = OAuth1Session(
                self.key, client_secret=self.secret,
                resource_owner_key=self.token,
                resource_owner_secret=self.token_secret,
            )
            # 6-second timeout; we accept failure silently
            resp = await self._sync_to_async(session, url, params)
            if resp is None:
                return None
            data = resp.json()
        except Exception as e:
            log.warning("bricklink_fetch_failed", error=str(e), set_no=set_no)
            return None

        try:
            avg = float(data["data"]["avg_price"])
            qty = int(data["data"]["unit_quantity"])
        except (KeyError, ValueError, TypeError):
            return None
        if avg <= 0 or qty == 0:
            return None

        return Valuation(
            estimated_value=avg,
            confidence=min(1.0, qty / 20),
            source=self.name,
            sample_size=qty,
            note=f"BrickLink 6mo sold avg, set {set_no} (n={qty})",
        )

    @staticmethod
    async def _sync_to_async(session, url, params):
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: session.get(url, params=params, timeout=10),
        )
