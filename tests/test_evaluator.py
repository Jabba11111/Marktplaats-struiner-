from datetime import datetime, timezone

from treasure_scanner.config import Watcher
from treasure_scanner.evaluator import evaluate, listing_passes_watcher
from treasure_scanner.models import Listing, Valuation


def _listing(title="Test", desc="", price=500.0, item_id="1"):
    return Listing(
        item_id=item_id, title=title, description=desc, price=price,
        price_type="fixed", url="https://example", seller_name=None, location=None,
        posted_at=datetime.now(timezone.utc), thumbnail_url=None,
        category_id=None, category_name=None,
    )


def _watcher(**kw):
    import re
    base = dict(
        name="t", query="q", require=[], require_any=[], blacklist=[],
        value_sources=["marktplaats_median"], min_score=50, priority="medium",
    )
    # compile pattern lists if provided as strings
    for key in ("require", "require_any", "blacklist"):
        if key in kw:
            kw[key] = [re.compile(p) for p in kw[key]]
    base.update(kw)
    return Watcher(**base)


def test_listing_passes_basic():
    w = _watcher(require=[r"(?i)rtx"])
    passes, _ = listing_passes_watcher(_listing(title="RTX 3090 deal"), w)
    assert passes


def test_listing_blocked_by_blacklist():
    w = _watcher(require=[r"(?i)rtx"], blacklist=[r"(?i)defect"])
    passes, reasons = listing_passes_watcher(
        _listing(title="RTX 3090 defect"), w,
    )
    assert not passes


def test_listing_price_bounds():
    w = _watcher(max_price=200)
    passes, _ = listing_passes_watcher(_listing(price=300), w)
    assert not passes
    passes, _ = listing_passes_watcher(_listing(price=100), w)
    assert passes


def test_evaluate_gives_value_bonus():
    w = _watcher()
    listing = _listing(price=100)
    val = Valuation(estimated_value=400, confidence=0.8,
                    source="x", sample_size=10)
    ev = evaluate(listing, w, val)
    # base 40 + margin 0.75 → +37
    assert ev.score >= 70


def test_evaluate_ai_workstation_bonus():
    w = _watcher()
    listing = _listing(
        title="Workstation 64GB RAM RTX 3090",
        desc="24GB VRAM, top staat",
        price=1000,
    )
    ev = evaluate(listing, w, None)
    # base 40 + RAM>=64 (+15) + GPU>=12 (+10) + GPU>=24 (+5) = 70
    assert ev.score == 70
