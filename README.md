# Marktplaats Treasure Scanner

Continue scanner die Marktplaats én Troostwijk watcht voor
ondergewaardeerde items — AI-workstations, vintage hifi, antiek, LEGO —
en alerts naar Telegram pusht. Met werkende web-dashboard, stealth
browser voor sites met anti-bot, en zes waardebronnen.

## Features

- **Live polling** Marktplaats (3–5 min) per watcher.
- **Batch backfill** dagelijks, dieper pagineren.
- **Price-drop recheck** elke 6 uur — alert opnieuw als prijs ≥5% zakt.
- **Troostwijk auction loop** ieder uur (lagere prio, lots met `ends_at`).
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
- **Web dashboard** op `:8765` met statspagina, listings-filter,
  alerts-grid, watcher-beheer (toevoegen/verwijderen ad-hoc),
  mute-beheer.

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
- ✅ Stealth browser

**Volgende fase**: zie [`docs/PHASE2_PLAN.md`](docs/PHASE2_PLAN.md)
voor uitbreiding naar NL/BE/DE marktplaatsen en veilingsites
(2dehands, Kleinanzeigen, BVA, Vavato, OVM, etc.).

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
  sources/                    listing sources
    marktplaats.py
    troostwijk.py
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
