from datetime import datetime, timezone

from treasure_scanner.models import Listing
from treasure_scanner.scanner import _fingerprint


def _l(title, price, item_id="x", site="marktplaats"):
    return Listing(
        item_id=item_id, title=title, description="", price=price,
        price_type="fixed", url="https://x", seller_name=None, location=None,
        posted_at=datetime.now(timezone.utc), thumbnail_url=None,
        category_id=None, category_name=None, site=site,
    )


def test_fingerprint_same_listing_same_hash():
    a = _l("Dell Precision T7910 64GB", 1250)
    b = _l("Dell Precision T7910 64GB", 1250, item_id="y", site="2dehands")
    assert _fingerprint(a) == _fingerprint(b)


def test_fingerprint_different_title_different_hash():
    a = _l("Dell Precision T7910 64GB", 1250)
    b = _l("HP Z840 64GB", 1250)
    assert _fingerprint(a) != _fingerprint(b)


def test_fingerprint_rounds_small_price_diff():
    a = _l("Dell Precision T7910 64GB", 1250)
    b = _l("Dell Precision T7910 64GB", 1252)  # rounded to nearest €5 → 1250
    assert _fingerprint(a) == _fingerprint(b)


def test_fingerprint_large_price_diff_differs():
    a = _l("Dell Precision T7910 64GB", 1250)
    b = _l("Dell Precision T7910 64GB", 800)
    assert _fingerprint(a) != _fingerprint(b)


def test_fingerprint_case_and_punct_insensitive():
    a = _l("Dell Precision T7910, 64GB!", 1250)
    b = _l("DELL precision t7910 64gb", 1250)
    assert _fingerprint(a) == _fingerprint(b)
