from __future__ import annotations

import asyncio
import logging
import signal
import sys

import structlog

from .config import load_config
from .db import Database
from .scanner import Scanner
from .sources.marktplaats import MarktplaatsClient
from .telegram_bot import TelegramNotifier
from .valuation.ebay_sold import EbaySoldValuation
from .valuation.marktplaats_median import MarktplaatsMedianValuation


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

    log.info(
        "starting",
        watchers=len(cfg.watchers),
        sweep_enabled=cfg.keyword_sweep.enabled,
        telegram_configured=bool(cfg.telegram_token),
    )

    db = Database(cfg.db_path)
    client = MarktplaatsClient(request_interval=1.0)
    notifier = TelegramNotifier(cfg, db)
    valuation_sources = {
        "marktplaats_median": MarktplaatsMedianValuation(db, client),
        "ebay_sold": EbaySoldValuation(db, cfg.ebay_app_id),
    }
    scanner = Scanner(cfg, db, client, notifier, valuation_sources)

    await notifier.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    live_task = asyncio.create_task(scanner.live_loop(), name="live")
    batch_task = asyncio.create_task(scanner.batch_loop(), name="batch")

    await stop.wait()
    log.info("shutting_down")

    for t in (live_task, batch_task):
        t.cancel()
    await asyncio.gather(live_task, batch_task, return_exceptions=True)
    await notifier.stop()
    await client.aclose()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
