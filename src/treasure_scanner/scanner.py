from __future__ import annotations

import asyncio
import random

import structlog

from .config import Config, Watcher
from .db import Database
from .evaluator import evaluate, listing_passes_watcher, matches_keyword_sweep
from .models import Listing
from .sources.marktplaats import MarktplaatsClient
from .sources.troostwijk import TroostwijkClient
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
        troostwijk: TroostwijkClient | None = None,
    ):
        self.cfg = cfg
        self.db = db
        self.client = client
        self.notifier = notifier
        self.valuation_sources = valuation_sources
        self.troostwijk = troostwijk

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
        dropped_from: float | None = None,
    ) -> None:
        if self.db.is_muted(f"{listing.title} {listing.description}"):
            return

        # Already alerted? Skip unless price dropped substantially.
        already_alerted = self.db.has_alerted(listing.item_id, watcher.name)
        if already_alerted and dropped_from is None:
            return

        passes, _ = listing_passes_watcher(listing, watcher)
        if not passes:
            return

        valuation = await self._value_listing(listing, watcher.value_sources)
        ev = evaluate(listing, watcher, valuation)

        if dropped_from is not None:
            pct = (dropped_from - (listing.price or 0)) / dropped_from * 100
            ev.reasons.insert(
                0, f"PRIJSDROP: €{dropped_from:.0f} → €{listing.price:.0f} (-{pct:.0f}%)"
            )
            # Boost score so a drop becomes notable.
            ev.score = min(100, ev.score + 10)

        self.db.record_evaluation(ev)

        if ev.score >= watcher.min_score:
            log.info("alert", watcher=watcher.name, score=ev.score,
                     title=listing.title[:60], price=listing.price,
                     new=is_new, drop=dropped_from)
            await self.notifier.send_alert(ev)

    async def scan_watcher(self, watcher: Watcher, max_pages: int = 2,
                           source: str = "marktplaats") -> int:
        count = 0
        consecutive_seen = 0
        try:
            if source == "marktplaats":
                iterator = self.client.search_newest(
                    query=watcher.query,
                    category_id=watcher.category_id,
                    max_pages=max_pages,
                )
            elif source == "troostwijk":
                if self.troostwijk is None:
                    return 0
                iterator = self.troostwijk.search(
                    query=watcher.query, max_pages=max_pages,
                )
            else:
                return 0

            async for listing in iterator:
                is_new, dropped_from = self.db.upsert_listing(listing)
                if not is_new and dropped_from is None:
                    consecutive_seen += 1
                    if consecutive_seen >= 8 and max_pages <= 2:
                        break
                else:
                    consecutive_seen = 0
                await self._process_listing(
                    listing, watcher, is_new, dropped_from,
                )
                count += 1
        except Exception as e:
            log.error("scan_watcher_failed", watcher=watcher.name,
                      source=source, error=str(e))
        return count

    async def keyword_sweep_pass(self) -> None:
        sweep = self.cfg.keyword_sweep
        if not sweep.enabled:
            return
        queries = ["nalatenschap", "zolderopruiming", "geërfd", "moet weg"]
        for q in queries:
            try:
                async for listing in self.client.search_newest(
                    query=q, category_id=None, max_pages=1,
                ):
                    is_new, dropped_from = self.db.upsert_listing(listing)
                    if not is_new and dropped_from is None:
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
                    if self.db.has_alerted(listing.item_id, fake_watcher.name) \
                            and dropped_from is None:
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
        while True:
            for watcher in self.cfg.watchers:
                await self.scan_watcher(watcher, max_pages=2,
                                        source="marktplaats")
            for q in self.db.list_adhoc_watches():
                w = Watcher(name=f"ad-hoc: {q}", query=q, min_score=40)
                await self.scan_watcher(w, max_pages=1, source="marktplaats")
            await self.keyword_sweep_pass()

            delay = random.randint(self.cfg.live_poll_min, self.cfg.live_poll_max)
            log.info("live_cycle_done", sleep_seconds=delay)
            await asyncio.sleep(delay)

    async def batch_loop(self) -> None:
        await asyncio.sleep(60)
        while True:
            for watcher in self.cfg.watchers:
                log.info("batch_scan", watcher=watcher.name)
                await self.scan_watcher(watcher, max_pages=10,
                                        source="marktplaats")
                await asyncio.sleep(30)
            await asyncio.sleep(24 * 3600)

    async def troostwijk_loop(self) -> None:
        """Lower-priority auction scan. Hourly because auctions move slower."""
        if self.troostwijk is None:
            return
        await asyncio.sleep(120)
        while True:
            for watcher in self.cfg.watchers:
                # Auction-relevant watchers only: hardware/audio/antiek.
                if "antiek" in watcher.name.lower() \
                        or "marantz" in watcher.name.lower() \
                        or "workstation" in watcher.name.lower() \
                        or "rtx" in watcher.name.lower() \
                        or "tesla" in watcher.name.lower() \
                        or "mcintosh" in watcher.name.lower():
                    log.info("troostwijk_scan", watcher=watcher.name)
                    await self.scan_watcher(watcher, max_pages=2,
                                            source="troostwijk")
                    await asyncio.sleep(15)
            await asyncio.sleep(3600)  # hourly

    async def recheck_loop(self) -> None:
        """Periodic re-scan of recently-active queries to catch price drops.

        Cheap: we re-run the watcher's search query, which naturally
        re-encounters the listings; `upsert_listing` returns a non-None
        `dropped_from` when the new price is at least 5% lower, which
        triggers re-evaluation in `_process_listing`.
        """
        await asyncio.sleep(self.cfg.recheck_interval_hours * 60)
        interval_s = self.cfg.recheck_interval_hours * 3600
        while True:
            log.info("recheck_pass_start")
            for watcher in self.cfg.watchers:
                await self.scan_watcher(watcher, max_pages=3,
                                        source="marktplaats")
            log.info("recheck_pass_done")
            await asyncio.sleep(interval_s)
