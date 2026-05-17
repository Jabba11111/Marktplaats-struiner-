from __future__ import annotations

import asyncio
import logging
import signal
import sys

import structlog

from .browser import StealthBrowser
from .config import load_config
from .dashboard import run_dashboard
from .db import Database
from .scanner import Scanner
from .sources import (
    BVASource, CatawikiSource, KleinanzeigenSource, OVMSource,
    TroostwijkClient, TweakersVASource, VavatoSource,
    make_2dehands, make_2ememain, make_marktplaats,
)
from .sources.base import Source
from .telegram_bot import TelegramNotifier
from .valuation.bricklink import BrickLinkValuation
from .valuation.catawiki import CatawikiValuation
from .valuation.ebay_sold import EbaySoldValuation
from .valuation.marktplaats_median import MarktplaatsMedianValuation
from .valuation.reverb import ReverbValuation
from .valuation.tweakers import TweakersValuation


def setup_logging(level: str) -> None:
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
        stream=sys.stdout,
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
    )


async def run() -> None:
    cfg = load_config()
    setup_logging(cfg.log_level)
    log = structlog.get_logger(__name__)

    log.info("starting", watchers=len(cfg.watchers),
             dashboard_enabled=cfg.dashboard_enabled,
             stealth_browser_enabled=cfg.stealth_browser_enabled)

    db = Database(cfg.db_path)
    notifier = TelegramNotifier(cfg, db)

    browser: StealthBrowser | None = None
    if cfg.stealth_browser_enabled:
        browser = StealthBrowser(
            profile_dir=cfg.db_path.parent / "browser_profile",
            headless=cfg.stealth_browser_headless,
        )
        try:
            await browser.start()
        except Exception as e:
            log.error("stealth_browser_start_failed", error=str(e))
            browser = None

    # Build source registry. Lighter sites first, heavier later.
    marktplaats = make_marktplaats()
    sources: list[Source] = [
        marktplaats,
        make_2dehands(),
        make_2ememain(),
        KleinanzeigenSource(browser=browser, use_browser=False),
        TweakersVASource(),
        TroostwijkClient(),
        VavatoSource(),
        BVASource(browser=browser, use_browser=False),
        OVMSource(),
    ]
    if browser is not None:
        sources.append(CatawikiSource(browser=browser))

    valuation_sources = {
        "marktplaats_median": MarktplaatsMedianValuation(db, marktplaats),
        "ebay_sold": EbaySoldValuation(db, cfg.ebay_app_id),
        "tweakers": TweakersValuation(db),
        "bricklink": BrickLinkValuation(
            db, cfg.bricklink_consumer_key, cfg.bricklink_consumer_secret,
            cfg.bricklink_token, cfg.bricklink_token_secret,
        ),
        "reverb": ReverbValuation(db, cfg.reverb_token),
        "catawiki": CatawikiValuation(db, browser),
    }
    scanner = Scanner(cfg, db, sources, notifier, valuation_sources)

    await notifier.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    tasks: list[asyncio.Task] = [
        asyncio.create_task(scanner.live_loop(), name="live"),
        asyncio.create_task(scanner.batch_loop(), name="batch"),
        asyncio.create_task(scanner.recheck_loop(), name="recheck"),
        asyncio.create_task(scanner.auction_loop(), name="auction"),
    ]
    if cfg.dashboard_enabled:
        tasks.append(asyncio.create_task(run_dashboard(cfg, db), name="dashboard"))

    log.info("sources_registered",
             names=[s.name for s in sources],
             countries=sorted({s.country for s in sources}))

    await stop.wait()
    log.info("shutting_down")

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await notifier.stop()
    for src in sources:
        try:
            await src.aclose()
        except Exception:
            pass
    if browser is not None:
        await browser.stop()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
