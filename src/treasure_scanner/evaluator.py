from __future__ import annotations

import structlog

from .config import KeywordSweep, Watcher
from .models import Evaluation, Listing, Valuation
from .parser import parse_specs

log = structlog.get_logger(__name__)


def _matches_all(patterns, text: str) -> bool:
    return all(p.search(text) for p in patterns)


def _matches_any(patterns, text: str) -> bool:
    if not patterns:
        return True
    return any(p.search(text) for p in patterns)


def _matches_none(patterns, text: str) -> bool:
    return not any(p.search(text) for p in patterns)


def listing_passes_watcher(listing: Listing, watcher: Watcher) -> tuple[bool, list[str]]:
    """Returns (passes, reasons-against-or-positive-signals)."""
    reasons: list[str] = []
    text = f"{listing.title}\n{listing.description}"

    if watcher.min_price is not None and listing.price is not None \
            and listing.price < watcher.min_price:
        return False, [f"price < {watcher.min_price}"]
    if watcher.max_price is not None and listing.price is not None \
            and listing.price > watcher.max_price:
        return False, [f"price > {watcher.max_price}"]

    if not _matches_all(watcher.require, text):
        return False, ["missing required pattern"]
    if not _matches_any(watcher.require_any, text):
        return False, ["no require_any match"]
    if not _matches_none(watcher.blacklist, text):
        return False, ["blacklist match"]

    reasons.append(f"watcher:{watcher.name}")
    return True, reasons


def evaluate(
    listing: Listing,
    watcher: Watcher,
    valuation: Valuation | None,
) -> Evaluation:
    """Score 0..100. Higher = better deal."""
    score = 40  # base for matching a watcher
    reasons: list[str] = [f"matched watcher '{watcher.name}'"]

    # AI-server specific bonuses
    specs = parse_specs(listing.title, listing.description)
    if specs.ram_gb:
        if specs.ram_gb >= 64:
            score += 15
            reasons.append(f"RAM {specs.ram_gb}GB (>=64)")
        elif specs.ram_gb >= 32:
            score += 5
            reasons.append(f"RAM {specs.ram_gb}GB (>=32)")
    if specs.vram_gb and specs.vram_gb >= 12:
        score += 10
        reasons.append(f"VRAM {specs.vram_gb}GB ({specs.gpu_model or 'gpu'})")
    if specs.vram_gb and specs.vram_gb >= 24:
        score += 5

    if valuation and listing.price is not None and valuation.estimated_value > 0:
        margin = (valuation.estimated_value - listing.price) / valuation.estimated_value
        # margin 0 -> +0, margin 0.5 -> +25, margin 0.8 -> +40, capped at +45
        bonus = max(0, min(45, int(margin * 50)))
        score += bonus
        reasons.append(
            f"value ~€{valuation.estimated_value:.0f} (n={valuation.sample_size}, "
            f"{valuation.source}) → margin {margin*100:.0f}% (+{bonus})"
        )
    elif listing.price is None:
        reasons.append("price unknown (bidding/n.o.t.k.)")

    score = max(0, min(100, score))

    return Evaluation(
        listing=listing,
        watcher_name=watcher.name,
        score=score,
        valuation=valuation,
        reasons=reasons,
        priority=watcher.priority,
    )


def matches_keyword_sweep(listing: Listing, sweep: KeywordSweep) -> tuple[bool, list[str]]:
    if not sweep.enabled:
        return False, []
    if listing.price is not None:
        if sweep.min_price is not None and listing.price < sweep.min_price:
            return False, []
        if sweep.max_price is not None and listing.price > sweep.max_price:
            return False, []
    text = f"{listing.title}\n{listing.description}"
    hits = [p.pattern for p in sweep.patterns if p.search(text)]
    return (bool(hits), hits)
