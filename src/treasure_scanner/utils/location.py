"""Postcode/city → coordinates resolver with distance filtering.

Uses `pgeocode` (which bundles GeoNames postal data per country) if
installed; otherwise the resolver returns None for every query, which
effectively disables the distance filter.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache

import structlog

log = structlog.get_logger(__name__)

try:
    import pgeocode  # type: ignore
    _HAVE_PGEOCODE = True
except ImportError:
    pgeocode = None  # type: ignore
    _HAVE_PGEOCODE = False


# Postcode regex per country.
_POSTCODE_PATTERNS = {
    "NL": re.compile(r"\b(\d{4})\s?[A-Z]{2}\b|\b(\d{4})\b"),
    "BE": re.compile(r"\b(\d{4})\b"),
    "DE": re.compile(r"\b(\d{5})\b"),
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p = math.pi / 180
    a = (0.5 - math.cos((lat2 - lat1) * p) / 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) *
         (1 - math.cos((lon2 - lon1) * p)) / 2)
    return 2 * r * math.asin(math.sqrt(a))


def extract_postcode(text: str | None, country: str) -> str | None:
    if not text:
        return None
    pat = _POSTCODE_PATTERNS.get(country.upper())
    if pat is None:
        return None
    m = pat.search(text)
    if not m:
        return None
    return m.group(1) or m.group(2) if pat.groups > 1 else m.group(1)


class LocationResolver:
    """Resolves (postcode, country) → (lat, lon) using pgeocode.

    Cached in-process; pgeocode itself caches the downloaded postal CSV
    in ~/.cache/pgeocode after the first lookup.
    """

    def __init__(self, home_postcode: str | None, home_country: str = "NL"):
        self.home_postcode = (home_postcode or "").strip() or None
        self.home_country = home_country.upper()
        self._geo_cache: dict[str, object] = {}
        self._home_coords: tuple[float, float] | None = None
        if self.home_postcode and _HAVE_PGEOCODE:
            self._home_coords = self._coords(self.home_postcode, self.home_country)
            if self._home_coords is None:
                log.warning("home_postcode_not_resolved",
                            postcode=self.home_postcode,
                            country=self.home_country)

    @property
    def enabled(self) -> bool:
        return _HAVE_PGEOCODE and self._home_coords is not None

    def _nominatim(self, country: str):
        country = country.upper()
        if country not in self._geo_cache:
            self._geo_cache[country] = pgeocode.Nominatim(country)
        return self._geo_cache[country]

    @lru_cache(maxsize=2048)
    def _coords(self, postcode: str, country: str) -> tuple[float, float] | None:
        if not _HAVE_PGEOCODE:
            return None
        try:
            nomi = self._nominatim(country)
            row = nomi.query_postal_code(postcode)
            lat, lon = float(row["latitude"]), float(row["longitude"])
            if math.isnan(lat) or math.isnan(lon):
                return None
            return (lat, lon)
        except Exception as e:
            log.debug("pgeocode_lookup_failed",
                      postcode=postcode, country=country, error=str(e))
            return None

    def distance_km(self, location: str | None, country: str = "NL") -> float | None:
        """Distance from home to the listing's location. None when unknown."""
        if not self.enabled:
            return None
        postcode = extract_postcode(location, country)
        if not postcode:
            return None
        coords = self._coords(postcode, country.upper())
        if coords is None:
            return None
        return haversine_km(
            self._home_coords[0], self._home_coords[1],
            coords[0], coords[1],
        )
