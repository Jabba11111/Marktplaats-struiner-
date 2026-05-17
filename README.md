# Marktplaats Treasure Scanner

Continue scanner die Marktplaats én Troostwijk watcht voor
ondergewaardeerde items — AI-workstations, vintage hifi, antiek, LEGO —
en alerts naar Telegram pusht. Met werkende web-dashboard, stealth
browser voor sites met anti-bot, en zes waardebronnen.

## Features

- **16 actieve sources** verspreid over NL/BE/DE:
  - **Marktplaatsen**: Marktplaats.nl, 2dehands.be, 2ememain.be,
    Kleinanzeigen.de, Tweakers V&A, Hood.de, Aukro.de, Quoka.de
  - **Veilingen**: Troostwijk, Vavato (BE), BVA-Auctions, Onlineveiling-
    meester (OVM), Catawiki (via stealth browser), Auctionet,
    Lot-tissimo
  - **Deal-aggregator**: MyDealz.de (RSS)
- **Live polling** marktplaatsen (3–5 min), per land geconfigureerd
  per watcher via `countries: [NL, BE, DE]`.
- **Auction loop** elk uur (lagere prio, lots met `ends_at`).
- **Batch backfill** dagelijks, dieper pagineren.
- **Price-drop recheck** elke 6 uur — alert opnieuw als prijs ≥5% zakt.
- **Cross-site dedup**: zelfde item op meerdere sites → één alert.
  Twee-laags: pHash van thumbnail (Pillow + imagehash) als sterkste
  signaal, val terug op hash van `genormaliseerde_titel + prijs_op_5`.
- **Postcode-distance filter**: per watcher of globaal. Resolved via
  `pgeocode` (NL/BE/DE postcodes). Vereist `HOME_POSTCODE` in `.env`.
- **Adaptive throttle**: per source verdubbelt de wachttijd bij 3
  opeenvolgende 429/403/5xx, vervalt langzaam (10%/req) terug naar
  baseline na succes. Geconfigureerd op alle 10 HTML/RSS sources.
- **Stealth browser** (patchright + fingerprint rotatie) voor sites die
  plain HTTP-clients blokkeren. Geïnspireerd op CloakBrowser.
- **6 waardebronnen**:
  - `marktplaats_median` — mediaan van actieve vergelijkbare listings
  - `ebay_sold` — eBay sold-listings (Finding API, EUR-approx)
  - `tweakers` — Pricewatch retail-mediaan (hardware)
  - `bricklink` — 6-mnd sold avg (LEGO sets, OAuth1)
  - `reverb` — sold median (audio/instrumenten, Bearer token)
  - `catawiki` — closed-lot scraping via stealth browser (antiek/kunst)
- **Telegram bot** met `/start /help /stats /recent /watch /watches
  /mute /unmute /mutes` + inline action buttons op iedere alert.
- **Web dashboard** op `:8765` met statspagina, listings-filter
  (site, land, score), alerts-grid, watcher-beheer
  (toevoegen/verwijderen ad-hoc), mute-beheer, en een **health-pagina**
  die per source toont: listings in 1u/24u/7d, laatst gezien,
  actuele throttle-interval en status (ok/quiet/silent/throttled).

## Setup

1. `cp .env.example .env` en vul tenminste `TELEGRAM_BOT_TOKEN` +
   `TELEGRAM_CHAT_ID` in.
   - Bot maken: chat met `@BotFather`, `/newbot`.
   - Chat-id: stuur bericht naar `@userinfobot`.
2. Optioneel: `EBAY_APP_ID`, `BRICKLINK_*`, `REVERB_TOKEN` voor extra
   waardebronnen.
3. `docker compose up -d --build`.
4. Open `http://NAS:8765` voor het dashboard.
5. Bot reageert op `/start` in Telegram.

Lokaal zonder Docker:

```bash
pip install -e .
patchright install chromium    # voor stealth browser
treasure-scanner
```

## Architectuur

```
┌──────────────────┐
│ live_loop        │──┐
│ batch_loop       │  │      ┌──────────┐    ┌─────────┐
│ recheck_loop     │──┼─▶ Scanner ─▶ Evaluator ─▶ Telegram
│ troostwijk_loop  │  │      │          │    │  bot    │
└──────────────────┘  │      │          │    └─────────┘
                      │      │          │         │
   ┌──────────┐       │      │  Valuation         ▼
   │ Sources: │───────┘      │  Sources    ┌─────────┐
   │ Mkpl     │              │   (6)       │ SQLite  │
   │ Troostw  │              └──────────┘  └─────────┘
   └──────────┘                                  ▲
                                                 │
                                          ┌──────┴────┐
                                          │ Dashboard │
                                          │ (FastAPI) │
                                          └───────────┘
```

## Watchers tunen

`config/watchers.yaml` definieert zoekopdrachten. Velden:

- `query`, `category_id`, `min_price`, `max_price`
- `require` / `require_any` / `blacklist` (regex)
- `value_sources` — geordende lijst, eerste succesvolle telt
- `min_score` (0–100) — alert-drempel
- `priority` — `high`/`medium`/`low` (kleur in Telegram + dashboard)

AI-server scoring:
- ≥32GB RAM: +5 · ≥64GB: +15
- GPU ≥12GB VRAM: +10 · ≥24GB: nog +5
- Waarde-marge: tot +45 (margin × 50, gecapt op 45)
- Prijsdrop: extra +10

## Eigen waardebron toevoegen

1. Subclass `ValuationSource` in `src/treasure_scanner/valuation/`.
2. Implementeer `async def _compute(listing) -> Valuation | None`.
3. Registreer in `main.py` onder `valuation_sources`.
4. Refereer per watcher via `value_sources: [naam]`.

## Stealth browser

`STEALTH_BROWSER_ENABLED=true` activeert patchright (een gepatchte
Playwright fork zonder de meest gangbare `navigator.webdriver`-leaks).
Roteert per sessie tussen Chrome/Firefox/Safari fingerprints met
passende locale, viewport en timezone. Persistent profile in
`data/browser_profile/` zodat cookies + cache blijven (looking like a
returning visitor in plaats van fresh headless). Min 4s interval tussen
nav's, met jitter en kleine muis/scroll-bewegingen.

In Docker is alles voorgeïnstalleerd (zie `Dockerfile`). Lokaal: na
`pip install` ook `patchright install chromium` draaien.

## Roadmap

Klaar (huidige branch):

- ✅ 4 nieuwe waardebronnen: Tweakers, BrickLink, Reverb, Catawiki
- ✅ Troostwijk source
- ✅ Price-drop recheck loop
- ✅ Werkend dashboard (FastAPI + Jinja, dark theme)
- ✅ Stealth browser (patchright)
- ✅ **Sprint A**: source-protocol + `site`/`country` op listings + DB-migratie
- ✅ **Sprint B (P1)**: 2dehands.be, 2ememain.be, Kleinanzeigen.de,
  Tweakers V&A
- ✅ **Sprint C (P2)**: Catawiki source, Vavato (BE), BVA-Auctions, OVM
- ✅ Cross-site dedup (titel+prijs hash)
- ✅ `countries` + `query_overrides` per watcher
- ✅ **Sprint D**: Hood.de, Aukro.de, Quoka.de, Auctionet,
  Lot-tissimo, MyDealz RSS
- ✅ **Sprint E**: pHash image dedup, `max_distance_km` postcode-filter,
  adaptive throttle bij 429/403/5xx

Fase 2 compleet. Zie [`docs/PHASE2_PLAN.md`](docs/PHASE2_PLAN.md) voor
de oorspronkelijke planning.

## Layout

```
src/treasure_scanner/
  main.py                     entrypoint, wires everything
  config.py                   YAML + env loader
  models.py                   Listing, Valuation, Evaluation dataclasses
  db.py                       SQLite layer
  parser.py                   RAM/VRAM/GPU extractor
  evaluator.py                scoring
  scanner.py                  live / batch / recheck / troostwijk loops
  telegram_bot.py             bot + alerts + commands
  browser/                    stealth Playwright wrapper
    stealth.py
    fingerprints.py
  dashboard/                  FastAPI web UI
    app.py
    templates/*.html
    static/style.css
  utils/                      shared helpers
    phash.py                  async perceptual image hash
    location.py               postcode → coords + haversine
    throttle.py               adaptive interval per source
  sources/                    listing sources (16 total)
    base.py                   Source protocol
    marktplaats.py            Adevinta family (mkpl, 2dh, 2em)
    troostwijk.py             TBAuctions family (tba, vav)
    kleinanzeigen.py          Kleinanzeigen.de
    tweakers_va.py            Tweakers Vraag & Aanbod
    catawiki.py               Catawiki (via stealth browser)
    bva.py                    BVA-Auctions
    ovm.py                    Onlineveilingmeester
    hood.py                   Hood.de
    aukro.py                  Aukro.de
    quoka.py                  Quoka.de
    auctionet.py              Auctionet (JSON API)
    lottissimo.py             Lot-tissimo art auctions
    mydealz.py                MyDealz RSS feed
  valuation/                  six value sources
    base.py
    marktplaats_median.py
    ebay_sold.py
    tweakers.py
    bricklink.py
    reverb.py
    catawiki.py
config/watchers.yaml          watcher definitions
docs/PHASE2_PLAN.md           NL/BE/DE multi-site plan
```

## Risico's

- **ToS**: Marktplaats en de meeste veilingsites verbieden scraping
  technisch. Defensieve rate-limits + persoonlijk gebruik. Geen
  herdistributie.
- **API-instabiliteit**: geen officiële API → bij site-changes kan een
  source breken; check dashboard `/healthz` en logs.
- **Stealth browser** maakt detectie moeilijker maar niet onmogelijk.
  Bij ban op één IP: zet `STEALTH_BROWSER_HEADLESS=false` voor debug,
  of voeg een proxy toe.
