"""Parser tests for Sprint D sources (Hood, Aukro, Quoka, Auctionet,
Lot-tissimo, MyDealz). Real HTML/RSS changes; selectors are
defensive but if a site rewrites their markup these tests stay green
as long as the parsers no-op rather than crash."""
from treasure_scanner.sources.auctionet import AuctionetSource
from treasure_scanner.sources.aukro import AukroSource
from treasure_scanner.sources.hood import HoodSource
from treasure_scanner.sources.lottissimo import LotTissimoSource
from treasure_scanner.sources.mydealz import MyDealzSource
from treasure_scanner.sources.quoka import QuokaSource


def test_hood_parses_item_anchor():
    html = """
    <html><body>
    <div class="card">
      <a href="/item/123456789-vintage-receiver">Marantz 2230 Vintage Receiver</a>
      <span class="price">325,00 €</span>
    </div>
    </body></html>
    """
    out = HoodSource()._parse(html)
    assert len(out) == 1
    r = out[0]
    assert r.item_id == "hd:123456789"
    assert r.price == 325.0
    assert r.site == "hood"
    assert r.country == "DE"


def test_hood_auction_price_marked_bidding():
    html = """
    <html><body><div>
      <a href="/item/55555">Test</a>
      <span class="price">Gebot ab 10 €</span>
    </div></body></html>
    """
    out = HoodSource()._parse(html)
    assert out[0].price_type == "bidding"


def test_quoka_parses_anzeige():
    html = """
    <html><body>
    <article>
      <a href="/anzeige/dell-precision/9988776">Dell Precision T7910</a>
      <span class="price">1.200 €</span>
      <span class="location">Munich</span>
    </article>
    </body></html>
    """
    out = QuokaSource()._parse(html)
    assert len(out) == 1
    assert out[0].item_id == "qk:9988776"
    assert out[0].price == 1200.0
    assert out[0].location == "Munich"


def test_aukro_silent_on_empty_html():
    out = AukroSource()._parse("<html></html>")
    assert out == []


def test_auctionet_parse_json_item():
    raw = {
        "id": 555111,
        "title": "Antikes Silber Salzfass",
        "slug": "antikes-silber",
        "current_bid": 85,
        "currency": "EUR",
        "ends_at": "2026-06-15T18:00:00Z",
        "images": [{"normal": "https://img/auctionet.jpg"}],
        "company": {"name": "Hamburger Auktionshaus"},
        "city": "Hamburg",
    }
    listing = AuctionetSource()._parse(raw)
    assert listing.item_id == "an:555111"
    assert listing.price == 85.0
    assert listing.price_type == "bidding"
    assert listing.site == "auctionet"
    assert listing.seller_name == "Hamburger Auktionshaus"
    assert listing.location == "Hamburg"


def test_auctionet_sek_to_eur_conversion():
    raw = {"id": 1, "title": "x", "current_bid": 1000, "currency": "SEK"}
    listing = AuctionetSource()._parse(raw)
    # 1000 SEK ≈ 87 EUR
    assert listing.price is not None
    assert 80 < listing.price < 100


def test_lottissimo_parses_lot():
    html = """
    <html><body>
    <div class="lot">
      <a href="/de/lot/4455667/some-slug">Meissen Tasse</a>
      <span class="estimate">€ 60</span>
    </div>
    </body></html>
    """
    out = LotTissimoSource()._parse(html)
    assert len(out) == 1
    assert out[0].item_id == "lt:4455667"
    assert out[0].site == "lottissimo"


def test_mydealz_parses_rss():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
      <item>
        <title>SSD 2TB Samsung 990 Pro - 149€</title>
        <link>https://www.mydealz.de/deals/ssd-2tb-2228899</link>
        <guid>https://www.mydealz.de/deals/ssd-2tb-2228899</guid>
        <description>Bei Mindfactory für 149€ statt UVP 230€.</description>
        <pubDate>Mon, 12 May 2026 09:23:00 +0000</pubDate>
      </item>
    </channel></rss>
    """
    out = MyDealzSource()._parse(xml)
    assert len(out) == 1
    r = out[0]
    assert r.item_id == "md:2228899"
    assert r.price == 149.0
    assert r.site == "mydealz"
    assert "Samsung" in r.title


def test_mydealz_no_price_in_text():
    xml = """<?xml version="1.0"?>
    <rss><channel><item>
      <title>Nice deal</title>
      <link>https://www.mydealz.de/deals/x-1</link>
      <guid>https://www.mydealz.de/deals/x-1</guid>
      <description>Geen prijs.</description>
    </item></channel></rss>
    """
    [r] = MyDealzSource()._parse(xml)
    assert r.price is None
