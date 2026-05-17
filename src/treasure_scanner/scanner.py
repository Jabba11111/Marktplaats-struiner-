from __future__ import annotations

import asyncio
import hashlib
import random
import re
from typing import TYPE_CHECKING

import structlog

from .config import Config, Watcher
from .db import Database
from .evaluator import evaluate, listing_passes_watcher, matches_keyword_sweep
from .models import Listing
from .sources.base import Source
from .utils.location import LocationResolver
from .utils.phash import compute_phash, phash_fingerprint
from .valuation.base import ValuationSource

if TYPE_CHECKING:
    from .telegram_bot import TelegramNotifier

log = structlog.get_logger(__name__)


def _fingerprint(listing: Listing) -> str:
    """Cross-site dedup key: normalized title + rounded price.

    Catches the same item posted on Marktplaats + 2dehands by the same
    seller. Not perfect (no image hash yet) but good enough to suppress
    obvious duplicates without missing real listings.
    """
    title = re.sub(r"[^a-z0-9 ]", " ", listing.title.lower())
    title = re.sub(r"\s+", " ", title).strip()
    # Round price to nearest €5 so small differences don't bust the hash.
    price = "noprice"
    if listing.price is not None:
        price = str(round(listing.price / 5) * 5)
    key = f"{title[:80]}|{price}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


class Scanner:
    """Multi-source scanner.

    sources: list of `Source` objects (one per host).
    """

    def __init__(
        self,
        cfg: Config,
        db: Database,
        sources: list[Source],
        notifier: "TelegramNotifier",
        valuation_sources: dict[str, ValuationSource],
        priority_source: str = "marktplaats",
        location_resolver: LocationResolver | None = None,
    ):
        self.cfg = cfg
        self.db = db
        self.sources = sources
        self.notifier = notifier
        self.valuation_sources = valuation_sources
        self.priority_source = priority_source
        self.location_resolver = location_resolver or LocationResolver(
            cfg.home_postcode, cfg.home_country,
        )

    # --- helpers ---

    def _sources_for_watcher(self, watcher: Watcher) -> list[Source]:
        out = []
        for s in self.sources:
            if watcher.sources and s.name not in watcher.sources:
                continue
            if s.country not in watcher.countries:
                continue
            out.append(s)
        return out

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

    def _passes_distance(self, listing: Listing, watcher: Watcher) -> bool:
        max_km = watcher.max_distance_km if watcher.max_distance_km is not None \
                 else self.cfg.max_distance_km
        if not max_km or not self.location_resolver.enabled:
            return True
        d = self.location_resolver.distance_km(listing.location, listing.country)
        if d is None:
            # Location not resolvable -> let it through, evaluator notes it.
            return True
        return d <= max_km

    async def _maybe_compute_phash(self, listing: Listing, is_new: bool) -> str | None:
        if not self.cfg.image_dedup_enabled:
            return None
        if not is_new or not listing.thumbnail_url:
            return None
        phash = await compute_phash(listing.thumbnail_url)
        if phash:
            self.db.set_image_phash(listing.item_id, phash)
        return phash

    async def _process_listing(
        self,
        listing: Listing,
        watcher: Watcher,
        is_new: bool,
        dropped_from: float | None = None,
    ) -> None:
        if self.db.is_muted(f"{listing.title} {listing.description}"):
            return

        already_alerted = self.db.has_alerted(listing.item_id, watcher.name)
        if already_alerted and dropped_from is None:
            return

        passes, _ = listing_passes_watcher(listing, watcher)
        if not passes:
            return

        if not self._passes_distance(listing, watcher):
            return

        valuation = await self._value_listing(listing, watcher.value_sources)
        ev = evaluate(listing, watcher, valuation)

        if dropped_from is not None and listing.price is not None:
            pct = (dropped_from - listing.price) / dropped_from * 100
            ev.reasons.insert(
                0, f"PRIJSDROP: €{dropped_from:.0f} → €{listing.price:.0f} (-{pct:.0f}%)"
            )
            ev.score = min(100, ev.score + 10)

        self.db.record_evaluation(ev)

        if ev.score < watcher.min_score:
            return

        # Cross-site dedup — only on initial alerts, never on price drops.
        if dropped_from is None:
            phash = await self._maybe_compute_phash(listing, is_new)

            existing: str | None = None
            # 1. Image-hash match (strongest signal)
            if phash:
                existing = self.db.find_by_phash(phash, exclude_item_id=listing.item_id)
                if existing is None:
                    # Register the phash as a fingerprint too so a later
                    # listing without phash but identical title still dedups.
                    self.db.check_and_register_fingerprint(
                        phash_fingerprint(phash), listing.item_id,
                    )

            # 2. Title+price hash fallback
            if existing is None:
                fp = _fingerprint(listing)
                existing = self.db.check_and_register_fingerprint(fp, listing.item_id)

            if existing and existing != listing.item_id:
                log.info("dedup_suppressed", watcher=watcher.name,
                         site=listing.site, title=listing.title[:60],
                         prior=existing,
                         method="phash" if phash else "title")
                self.db.record_alert(listing.item_id, watcher.name,
                                     ev.score, telegram_message_id=None)
                return

        log.info("alert", watcher=watcher.name, source=listing.site,
                 score=ev.score, title=listing.title[:60],
                 price=listing.price, new=is_new, drop=dropped_from)
        await self.notifier.send_alert(ev)

    async def scan_watcher_on_source(
        self, watcher: Watcher, source: Source, max_pages: int = 2,
    ) -> int:
        count = 0
        consecutive_seen = 0
        query = watcher.query_for_country(source.country)
        try:
            async for listing in source.search(
                query=query, category_id=watcher.category_id,
                max_pages=max_pages,
            ):
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
            log.error("scan_failed", watcher=watcher.name,
                      source=source.name, error=str(e))
        return count

    async def scan_watcher(self, watcher: Watcher, max_pages: int = 2) -> int:
        total = 0
        for src in self._sources_for_watcher(watcher):
            total += await self.scan_watcher_on_source(
                watcher, src, max_pages=max_pages,
            )
        return total

    async def keyword_sweep_pass(self) -> None:
        sweep = self.cfg.keyword_sweep
        if not sweep.enabled:
            return
        # Sweep only on the primary Marktplaats source — cheaper and
        # avoids duplicate German "nalatenschap" hits on Kleinanzeigen.
        primary = next((s for s in self.sources
                        if s.name == self.priority_source), None)
        if primary is None:
            return
        queries = ["nalatenschap", "zolderopruiming", "geërfd", "moet weg"]
        for q in queries:
            try:
                async for listing in primary.search(query=q, max_pages=1):
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
                        query=q, min_score=sweep.min_score,
                        priority=sweep.priority,
                        value_sources=["marktplaats_median"],
                        countries=["NL"],
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

    # --- loops ---

    AUCTION_SITES = {
        "troostwijk", "vavato", "catawiki", "bva", "ovm",
        "auctionet", "lottissimo", "aukro",
    }

    @property
    def _live_sources(self) -> list[Source]:
        """Marketplace + deal sources. Polled in live_loop."""
        return [s for s in self.sources if s.name not in self.AUCTION_SITES]

    @property
    def _auction_sources(self) -> list[Source]:
        return [s for s in self.sources if s.name in self.AUCTION_SITES]

    async def live_loop(self) -> None:
        while True:
            for watcher in self.cfg.watchers:
                for src in self._sources_for_watcher(watcher):
                    if src in self._live_sources:
                        await self.scan_watcher_on_source(
                            watcher, src, max_pages=2,
                        )
            for q in self.db.list_adhoc_watches():
                w = Watcher(name=f"ad-hoc: {q}", query=q, min_score=40,
                            countries=["NL", "BE", "DE"])
                for src in self._sources_for_watcher(w):
                    if src in self._live_sources:
                        await self.scan_watcher_on_source(w, src, max_pages=1)
            await self.keyword_sweep_pass()

            delay = random.randint(self.cfg.live_poll_min, self.cfg.live_poll_max)
            log.info("live_cycle_done", sleep_seconds=delay)
            await asyncio.sleep(delay)

    async def batch_loop(self) -> None:
        await asyncio.sleep(60)
        while True:
            for watcher in self.cfg.watchers:
                log.info("batch_scan", watcher=watcher.name)
                for src in self._sources_for_watcher(watcher):
                    if src in self._live_sources:
                        await self.scan_watcher_on_source(
                            watcher, src, max_pages=10,
                        )
                        await asyncio.sleep(20)
            await asyncio.sleep(24 * 3600)

    async def auction_loop(self) -> None:
        if not self._auction_sources:
            return
        await asyncio.sleep(120)
        while True:
            for watcher in self.cfg.watchers:
                relevant_auction_keywords = [
                    "antiek", "marantz", "workstation", "rtx", "tesla",
                    "mcintosh", "zilver", "meissen", "technics",
                ]
                if not any(k in watcher.name.lower()
                           for k in relevant_auction_keywords):
                    continue
                for src in self._auction_sources:
                    log.info("auction_scan", watcher=watcher.name,
                             source=src.name)
                    await self.scan_watcher_on_source(
                        watcher, src, max_pages=2,
                    )
                    await asyncio.sleep(10)
            await asyncio.sleep(3600)

    async def recheck_loop(self) -> None:
        await asyncio.sleep(self.cfg.recheck_interval_hours * 60)
        interval_s = self.cfg.recheck_interval_hours * 3600
        while True:
            log.info("recheck_pass_start")
            for watcher in self.cfg.watchers:
                for src in self._sources_for_watcher(watcher):
                    if src in self._live_sources:
                        await self.scan_watcher_on_source(
                            watcher, src, max_pages=3,
                        )
            log.info("recheck_pass_done")
            await asyncio.sleep(interval_s)
