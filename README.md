# Marktplaats Treasure Scanner

Continuous scanner that watches Marktplaats for under-priced items —
AI workstations, vintage hifi, antiek — and pushes alerts to Telegram.

## Wat het doet

- **Live polling**: elke 3–5 min nieuwste listings per watcher (config in `config/watchers.yaml`).
- **Batch backfill**: 1× per 24u diepere paginering voor bestaande listings.
- **Evaluator**: regex-filters per watcher + AI-server specs parser
  (RAM/VRAM/GPU) + waardevergelijking.
- **Waardebronnen**: mediaan van vergelijkbare actieve Marktplaats listings
  (default) en eBay sold-listings (optioneel met `EBAY_APP_ID`).
- **Telegram bot** met commands: `/stats`, `/recent`, `/watch`,
  `/watches`, `/mute`, `/unmute`, `/mutes`. Inline buttons op elke alert.
- **Mutes** per merk/woord persisteren in SQLite.

## Setup

1. `cp .env.example .env` en vul `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in.
   - Bot maken: chat met `@BotFather`, `/newbot`.
   - Chat id vinden: stuur bericht naar `@userinfobot`.
2. Optioneel: registreer bij [eBay Developer](https://developer.ebay.com/)
   en zet `EBAY_APP_ID`.
3. `docker compose up -d --build`.
4. Bot reageert op `/start`.

Of lokaal zonder Docker:

```bash
pip install -e .
treasure-scanner
```

## Watchers tunen

`config/watchers.yaml` definieert de zoekopdrachten. Edit en herstart
(of laat Docker auto-restart het oppakken). Patterns zijn Python regex.

Drempels die ertoe doen:

- `min_score` per watcher: minimum totaalscore om te alerten (0–100).
- AI-server bonus: ≥32GB RAM = +5, ≥64GB = +15, GPU ≥12GB VRAM = +10,
  ≥24GB VRAM = nog +5.
- Waarde-bonus: tot +45 voor hoge marges.

## Waardebronnen — uitbreiden

Nieuwe bron toevoegen:

1. Subclass `ValuationSource` in `src/treasure_scanner/valuation/`.
2. Implementeer `_compute(listing) -> Valuation | None`.
3. Registreer in `main.py` onder `valuation_sources`.
4. Refereer per watcher via `value_sources: [naam]`.

Geplande bronnen: Tweakers Pricewatch, BrickLink, Reverb, Catawiki.

## Risico's

- Marktplaats blokkeert agressieve scrapers. Standaard tempo is
  voorzichtig (≥1s tussen requests, jitter, retry-backoff). Roterende
  proxy is bij blokkades de volgende stap.
- Géén officiële API — bij site-changes kan de fetcher breken.
- Houd dit op persoonlijk gebruik.

## Layout

```
src/treasure_scanner/
  main.py                    entrypoint
  config.py                  YAML + env loader
  models.py                  dataclasses
  db.py                      SQLite layer
  parser.py                  RAM/VRAM/GPU extractor
  evaluator.py               scoring
  scanner.py                 live + batch loops
  sources/marktplaats.py     LRP API client
  valuation/                 base + sources
  telegram_bot.py            bot + alerts + commands
config/watchers.yaml         watcher definitions
```
