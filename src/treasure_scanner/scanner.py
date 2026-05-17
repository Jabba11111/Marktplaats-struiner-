from __future__ import annotations

import asyncio
import random

import structlog

from .config import Config, Watcher
from .db import Database
from .evaluator import evaluate, listing_passes_watcher, matches_keyword_sweep
from .models import Listing
from .sources.marktplaats import MarktplaatsClient
from .telegram_bot import TelegramNotifier
from .valuation.base import ValuationSource

log = structlog.get_logger(__name__)


class Scanner:
    def __init__(
        self,
        cfg: Config,
        db: Database,
        client: MarktplaatsClient,
        notifier: TelegramNotifier,
        valuation_sources: dict[str, ValuationSource],
    ):
        self.cfg = cfg
        self.db = db
        self.client = client
        self.notifier = notifier
        self.valuation_sources = valuation_sources

    async def _value_listing(self, listing: Listing, sources: list[str]):
        for name in sources:
            src = self.valuation_sources.get(name)
            if not src:
                continue
            try:
                v = await src.value(listing)
            except Exception as e:
                log.warning("valuation_failed", source=name, error=str(e))
                continue
            if v and v.sample_size > 0:
                return v
        return None

    async def _process_listing(
        self,
        listing: Listing,
        watcher: Watcher,
        is_new: bool,
    ) -> None:
        # Skip muted titles entirely.
        if self.db.is_muted(f"{listing.title} {listing.description}"):
            return

        # Already alerted for this (item, watcher)? skip.
        if self.db.has_alerted(listing.item_id, watcher.name):
            return

        passes, _reasons = listing_passes_watcher(listing, watcher)
        if not passes:
            return

        valuation = await self._value_listing(listing, watcher.value_sources)
        ev = evaluate(listing, watcher, valuation)
        self.db.record_evaluation(ev)

        if ev.score >= watcher.min_score:
            log.info(
                "alert",
                watcher=watcher.name, score=ev.score, title=listing.title[:60],
                price=listing.price, new=is_new,
            )
            await self.notifier.send_alert(ev)

    async def scan_watcher(self, watcher: Watcher, max_pages: int = 2) -> int:
        """One pass over a watcher's query. Returns number of evaluated items."""
        count = 0
        consecutive_seen = 0
        try:
            async for listing in self.client.search_newest(
                query=watcher.query,
                category_id=watcher.category_id,
                max_pages=max_pages,
            ):
                is_new = self.db.upsert_listing(listing)
                if not is_new:
                    consecutive_seen += 1
                    # We've reached items we've seen before — only useful in live mode.
                    if consecutive_seen >= 8 and max_pages <= 2:
                        break
                else:
                    consecutive_seen = 0
                await self._process_listing(listing, watcher, is_new)
                count += 1
        except Exception as e:
            log.error("scan_watcher_failed", watcher=watcher.name, error=str(e))
        return count

    async def keyword_sweep_pass(self) -> None:
        """Scan recent listings across broad queries for nalatenschap-signals."""
        sweep = self.cfg.keyword_sweep
        if not sweep.enabled:
            return
        # Use the most distinctive sweep keywords as queries.
        queries = ["nalatenschap", "zolderopruiming", "geërfd", "moet weg"]
        for q in queries:
            try:
                async for listing in self.client.search_newest(
                    query=q, category_id=None, max_pages=1,
                ):
                    is_new = self.db.upsert_listing(listing)
                    if not is_new:
                        continue
                    if self.db.is_muted(f"{listing.title} {listing.description}"):
                        continue
                    matched, hits = matches_keyword_sweep(listing, sweep)
                    if not matched:
                        continue
                    fake_watcher = Watcher(
                        name=f"Sweep: {', '.join(hits[:2])}",
                        query=q,
                        min_score=sweep.min_score,
                        priority=sweep.priority,
                        value_sources=["marktplaats_median"],
                    )
                    if self.db.has_alerted(listing.item_id, fake_watcher.name):
                        continue
                    valuation = await self._value_listing(
                        listing, fake_watcher.value_sources,
                    )
                    ev = evaluate(listing, fake_watcher, valuation)
                    self.db.record_evaluation(ev)
                    if ev.score >= sweep.min_score:
                        log.info("alert_sweep", score=ev.score,
                                 title=listing.title[:60])
                        await self.notifier.send_alert(ev)
            except Exception as e:
                log.warning("sweep_query_failed", q=q, error=str(e))

    async def live_loop(self) -> None:
        """Continuous polling of all watchers + ad-hoc watches."""
        while True:
            for watcher in self.cfg.watchers:
                await self.scan_watcher(watcher, max_pages=2)
            # Ad-hoc watches (from /watch command)
            for q in self.db.list_adhoc_watches():
                w = Watcher(name=f"ad-hoc: {q}", query=q, min_score=40)
                await self.scan_watcher(w, max_pages=1)
            # Periodic keyword sweep (once every few cycles)
            await self.keyword_sweep_pass()

            delay = random.randint(self.cfg.live_poll_min, self.cfg.live_poll_max)
            log.info("live_cycle_done", sleep_seconds=delay)
            await asyncio.sleep(delay)

    async def batch_loop(self) -> None:
        """Slower deep-pagination backfill for existing listings."""
        # Wait 60s after startup so live loop gets first dibs.
        await asyncio.sleep(60)
        while True:
            for watcher in self.cfg.watchers:
                log.info("batch_scan", watcher=watcher.name)
                await self.scan_watcher(watcher, max_pages=10)
                # Be polite between watchers
                await asyncio.sleep(30)
            # One full backfill per 24h
            await asyncio.sleep(24 * 3600)
