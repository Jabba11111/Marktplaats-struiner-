"""Parser tests for new sources. HTML fixtures stay small but realistic."""
from treasure_scanner.sources.bva import BVASource
from treasure_scanner.sources.kleinanzeigen import KleinanzeigenSource
from treasure_scanner.sources.ovm import OVMSource
from treasure_scanner.sources.troostwijk import TroostwijkClient, VavatoSource


def test_kleinanzeigen_parses_card():
    html = """
    <html><body>
    <article class="aditem" data-adid="2871234567" data-href="/s-anzeige/dell/2871234567">
      <h2><a href="/s-anzeige/dell/2871234567">Dell Precision T7910 64GB RAM RTX 3090</a></h2>
      <p class="aditem-main--middle--description">Sehr guter Zustand</p>
      <p class="aditem-main--middle--price-shipping--price">1.250 € VB</p>
      <div class="aditem-main--top--left">10115 Berlin</div>
      <img src="https://img.example/x.jpg">
    </article>
    </body></html>
    """
    src = KleinanzeigenSource()
    results = src._parse_results(html)
    assert len(results) == 1
    r = results[0]
    assert r.item_id == "kln:2871234567"
    assert r.price == 1250.0
    assert r.price_type == "bidding"
    assert r.site == "kleinanzeigen"
    assert r.country == "DE"
    assert "Berlin" in r.location


def test_kleinanzeigen_free_item():
    html = """
    <html><body>
    <article class="aditem" data-adid="111" data-href="/s-anzeige/x/111">
      <h2><a href="/s-anzeige/x/111">Alte Bücher</a></h2>
      <p class="price">Zu verschenken</p>
    </article>
    </body></html>
    """
    src = KleinanzeigenSource()
    [r] = src._parse_results(html)
    assert r.price == 0.0
    assert r.price_type == "free"


def test_bva_jsonld_extraction():
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type":"ItemList","itemListElement":[
      {"@type":"ListItem","item":{
        "@type":"Product","name":"Vintage Marantz 2230",
        "url":"https://www.bva-auctions.com/nl/lot/123456",
        "image":"https://img/x.jpg",
        "offers":{"@type":"Offer","price":"175"}
      }}
    ]}
    </script></head><body></body></html>
    """
    src = BVASource()
    out = src._parse(html)
    assert len(out) == 1
    r = out[0]
    assert r.item_id == "bva:123456"
    assert r.price == 175.0
    assert r.site == "bva"


def test_ovm_parses_kavel_anchor():
    html = """
    <html><body>
    <div class="lot-card">
      <a href="/kavels/987654/marantz">Marantz 2230 receiver</a>
      <span class="price">€ 120</span>
    </div>
    </body></html>
    """
    src = OVMSource()
    out = src._parse(html)
    assert len(out) == 1
    assert out[0].item_id == "ovm:987654"
    assert out[0].site == "ovm"


def test_troostwijk_subclass_country_codes():
    t = TroostwijkClient()
    v = VavatoSource()
    assert t.name == "troostwijk" and t.country == "NL" and t.site_code == "tba"
    assert v.name == "vavato" and v.country == "BE" and v.site_code == "vav"
    assert t.tba_site == "tba-nl"
    assert v.tba_site == "vavato-be"
