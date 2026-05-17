from treasure_scanner.sources.troostwijk import TroostwijkClient


def test_parse_minimal_lot():
    raw = {
        "id": 12345,
        "title": "Dell Precision T7910 64GB RAM",
        "description": "Werkstation",
        "currentBid": 250,
        "slug": "dell-precision-t7910",
        "endsAt": "2026-06-01T12:00:00Z",
        "images": [{"url": "https://x/img.jpg"}],
    }
    listing = TroostwijkClient()._parse(raw)
    assert listing is not None
    assert listing.item_id == "tba:12345"
    assert listing.site == "troostwijk"
    assert listing.country == "NL"
    assert listing.title.startswith("Dell")
    assert listing.price == 250.0
    assert listing.price_type == "bidding"
    assert listing.thumbnail_url == "https://x/img.jpg"
    assert listing.raw["auction_source"] == "troostwijk"
    assert listing.raw["ends_at"]


def test_parse_missing_id_returns_none():
    assert TroostwijkClient()._parse({"title": "x"}) is None
