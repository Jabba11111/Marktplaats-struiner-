# Fase 2 — Multi-site uitbreiding (NL / BE / DE)

Status: **planning**, nog niet geïmplementeerd. Volgorde aanbevolen.

## 1. Doel

Naast Marktplaats en Troostwijk dezelfde watcher/evaluator-pipeline
voeden vanuit alle relevante marktplaats- en veilingsites in NL, BE,
DE. Eén alert per uniek item, zelfs als hetzelfde aanbod op meerdere
sites verschijnt.

## 2. Bron-inventaris

### Marktplaats-achtigen (vaste prijs / "bieden")

| Site | Land | API? | Stealth nodig? | Volume | Prio |
|---|---|---|---|---|---|
| **2dehands.be** | BE | Zelfde stack als Marktplaats (Adevinta-platform) | Nee | hoog | **P1** |
| **2ememain.be** | BE/FR | Idem (FR-locale van 2dehands) | Nee | hoog | **P1** |
| **Kleinanzeigen.de** | DE | Privé JSON (`api.kleinanzeigen.de`), of HTML | Soms | zeer hoog | **P1** |
| **Quoka.de** | DE | HTML | Nee | middel | P3 |
| **eBay Kleinanzeigen → Kleinanzeigen** | DE | (zie hierboven) | — | — | — |
| **Speurders.nl** | NL | HTML | Nee | laag | P4 |
| **Vraagenaanbod.nl** | NL | HTML, veel doorverwijzingen | Nee | laag | P4 |
| **Gomarkt.nl / Cocoflo.nl** | NL | HTML, klein | Nee | laag | skip |
| **Hood.de** | DE | XML feeds | Nee | middel | P3 |

### Veiling-sites (tijdgebonden, lagere live-prio, hogere batch-prio)

| Site | Land | Toegang | Notes | Prio |
|---|---|---|---|---|
| **Troostwijk / TBAuctions** | NL/BE/DE | JSON (`api/search-api/lots`) | reeds geïmplementeerd | — |
| **Catawiki** | NL (intl) | Stealth-browser (geen API) | reeds als waardebron, ook als source toevoegen | **P2** |
| **BVA-Auctions** | NL | HTML, deels JSON | grote NL inventaris | **P2** |
| **Onlineveilingmeester (OVM)** | NL | HTML | brede catalogus | **P2** |
| **Vavato** | BE (TBAuctions) | Zelfde API als Troostwijk | site=`vavato-be` | **P2** |
| **Moyersoen** | BE | HTML, JSON-LD | bedrijfsveilingen | P3 |
| **De Eland Auctioneers** | NL | HTML | meubel/antiek | P4 |
| **Aukro.de** | DE | HTML | breed | P3 |
| **Auction.de / Online-Auktion.de** | DE | HTML | breed | P3 |
| **Auctionet** | DE/SE | publieke JSON | kunst/antiek | P3 |
| **Lot-tissimo** | DE | HTML feed | kunst-veilingen aggregator | P3 |
| **Auctionata / LiveAuctioneers EU** | DE | HTML (LA heeft API met fee) | premium kunst | P4 |
| **Whisky.auction / Catawiki-niche** | intl | HTML | niche (skip tenzij watcher matcht) | skip |

### Hardware/IT-specifieke marktplaatsen

| Site | Land | Toegang | Notes | Prio |
|---|---|---|---|---|
| **Tweakers V&A** | NL | HTML, login optioneel | sterk voor RTX/server | **P1** |
| **Hardware.Info marktplaats** | NL | HTML | klein | P4 |
| **Computeruniverse Marketplace** | DE | API (affiliate) | retail-only | skip |
| **MyDealz / Pepper.de** | DE | RSS + HTML | aggregator van deals (retail flash-sales) | **P2** |

## 3. Architectuur-impact

### 3.1 Source-abstractie

Vandaag is `Listing` met `item_id` site-agnostisch maar zonder
site-marker. Voorstel:

```python
@dataclass
class Listing:
    site: str               # "marktplaats" | "2dehands" | "kleinanzeigen" | ...
    item_id: str            # primary id within that site
    # ... rest unchanged
    @property
    def global_id(self) -> str:
        return f"{self.site}:{self.item_id}"
```

DB-migratie: rename `listings.item_id` PK → `listings.global_id`
(of voeg `site` toe als kolom en gebruik composite key). Backfill
bestaande rows als `marktplaats:<id>`.

### 3.2 Source-registry

```python
class Source(Protocol):
    name: str
    country: str  # "NL"|"BE"|"DE"
    async def search(self, query: str, max_pages: int = 1) -> AsyncIterator[Listing]: ...
```

`Scanner.live_loop` itereert over geregistreerde sources × watchers,
met per-source `request_interval` en max_pages. Polling-budget per
source is configureerbaar (en wordt automatisch verlaagd bij 429s).

### 3.3 Cross-site dedup

Eén verkoper plaatst hetzelfde object vaak op 2dehands.be ÉN
Marktplaats. Trigger maar één alert per "echt object":

- Bereken fingerprint = `sha256(normalized_title + price_rounded + first_image_phash)`
- DB-tabel `dedup_fingerprints (fp, first_global_id)` — bij hit:
  registreer maar suppress alert.
- pHash van thumbnail via `imagehash` (kleine afhankelijkheid) — niet
  cruciaal voor v1, kan met alleen titel+prijs.

### 3.4 Watchers per land

Voeg `countries` toe aan watcher-config:

```yaml
- name: "RTX 4090 los"
  query: "RTX 4090"
  countries: [NL, BE, DE]    # default NL
  ...
```

Sommige queries (DE) hebben localized varianten — laat `query_overrides`
per land toe:

```yaml
- name: "Antiek zilver"
  query: "zilver antiek"
  query_overrides:
    DE: "antikes silber"
    BE: "zilver antiek"
```

### 3.5 Locatie/verzending

Een DE-listing in München is meestal niet bruikbaar zonder
verzendkosten. Voeg een `max_distance_km` filter per watcher toe
(t.o.v. een postcode in `.env`: `HOME_POSTCODE=1011`). Distance via
postcode-lookup (offline CSV) of bij twijfel: alleen verzendkosten
toetsen via listing-tekst.

## 4. Anti-bot strategie

Kleinanzeigen, Catawiki, BVA en OVM blokkeren veelal plain HTTP
clients. Strategie:

1. **Eerst plain `httpx`** met goed UA + Accept-Language + jitter.
   Werkt voor 70% van de sites in de tabel.
2. **Stealth browser** (`browser/stealth.py` met patchright) voor
   sites die actief detecteren — Catawiki bevestigd, Kleinanzeigen
   waarschijnlijk, BVA waarschijnlijk.
3. **Residential proxy pool** (optioneel) achter de stealth browser
   voor IP-rotatie als sites IP-bans uitdelen. Configureerbaar via
   `STEALTH_PROXY=http://user:pass@host:port` per source.
4. **Polite back-off**: bij 429 of CF-challenge: `request_interval *=
   2`, max 5 minuten. Auto-recovery na succesvolle request.

Eén `StealthBrowser`-instantie voor heel het proces; iedere source
krijgt zijn eigen `request_interval` parameter.

## 5. Implementatie-volgorde (concrete tickets)

### Sprint A — fundament (1–2 dagen werk)

1. **DB-migratie**: `site` kolom + `global_id` composite key.
   Backfill bestaande rows.
2. **Source-protocol** + registry. Refactor `MarktplaatsClient` en
   `TroostwijkClient` naar `Source` interface zonder gedragsverandering.
3. **Watchers config**: `countries` + `query_overrides` velden,
   defaulten naar `[NL]`.
4. **Dashboard**: voeg `site` filter aan `/listings` toe; toon site-badge.

### Sprint B — P1 sites (NL/BE marktplaats-achtigen)

5. **2dehands.be / 2ememain.be** — vrijwel zeker dezelfde LRP-API,
   alleen `site` parameter wijzigt. Subclass `MarktplaatsClient`.
6. **Kleinanzeigen.de** — bestaande Python `kleinanzeigen-api` library
   bekijken of eigen JSON-endpoint scrapen. Begin met HTML, escaleer naar
   stealth indien geblokkeerd.
7. **Tweakers V&A** — HTML scraping, alleen voor hardware-watchers.

### Sprint C — veilingsites (P2)

8. **Catawiki source** (al hebben we waardebron — hergebruik
   browser fetcher voor lot-listings met `ends_at`).
9. **Vavato** — subclass van Troostwijk met `site=vavato-be`.
10. **BVA-Auctions** — HTML/JSON-LD.
11. **Onlineveilingmeester** — HTML.

### Sprint D — DE long-tail (P3)

12. Hood.de, Aukro.de, Auction.de, Auctionet, Lot-tissimo.
13. Quoka.de.
14. MyDealz/Pepper RSS-feed (deal-aggregator: filter op price-drop %).

### Sprint E — kwaliteit

15. **Cross-site dedup** met titel+prijs hash; later pHash.
16. **`max_distance_km`** filter met postcode-lookup.
17. **Auto-tuning request_interval** bij 429s per source.

## 6. Risico's en mitigaties

- **Site-ToS**: persoonlijk gebruik, defensieve rate-limits,
  geen herdistributie. Geen account-creatie/credentials inzet zonder
  expliciete toestemming.
- **GDPR/persoonsgegevens**: verkopers-naam en locatie alleen lokaal
  in SQLite, niet naar derde partijen.
- **API-instabiliteit**: per source een `last_success_at` veld in DB
  → dashboard waarschuwt als een source > 24u stilstaat.
- **Catawiki/Kleinanzeigen anti-bot escalatie**: stealth browser is
  duurder (CPU/RAM); pool van max 2 concurrent pages per browser.

## 7. Geschatte cijfers

- ~12 actieve sources gepland, gem. 5 minuten poll-interval.
- ~3000 requests/uur ruwweg verdeeld over alle hosts (per host
  ≤ 1 req per 4s) — ruim onder elke "redelijke" drempel.
- Database: 100k listings na 30 dagen, ~50MB SQLite. Migratie naar
  Postgres pas nodig bij >500k of voor remote dashboard.
- Telegram-rate: max 30 alerts/sec server-side; we komen daar niet bij.

## 8. Wat we NIET doen (out-of-scope v2)

- Automatisch bieden of kopen.
- Account-login op enige site.
- Cross-source FX/import-tax-berekening (alleen ruwe EUR-schatting).
- Internationale veilingen buiten DACH/Benelux.
