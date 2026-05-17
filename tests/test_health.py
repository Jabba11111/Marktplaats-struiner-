from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from treasure_scanner.config import Config, KeywordSweep
from treasure_scanner.dashboard import build_app
from treasure_scanner.dashboard.app import _classify
from treasure_scanner.db import Database
from treasure_scanner.models import Listing


def _l(item_id, site, country, seen_minutes_ago=0):
    return Listing(
        item_id=item_id, title="t", description="", price=10.0,
        price_type="fixed", url="https://x", seller_name=None, location=None,
        posted_at=datetime.now(timezone.utc) - timedelta(minutes=seen_minutes_ago),
        thumbnail_url=None, category_id=None, category_name=None,
        site=site, country=country,
    )


class _FakeThrottle:
    def __init__(self, base, current, failures):
        self.base = base
        self.current = current
        self.consecutive_failures = failures


class _FakeSource:
    requires_browser = False
    def __init__(self, name, country, throttle=None):
        self.name = name
        self.country = country
        self.throttle = throttle
    async def search(self, query, max_pages=1, category_id=None):
        if False:
            yield
    async def aclose(self):
        pass


def _cfg(tmp_path):
    return Config(
        watchers=[], keyword_sweep=KeywordSweep(),
        db_path=tmp_path / "t.db",
        telegram_token="", telegram_chat_id="", telegram_admin_user_id=None,
        ebay_app_id=None, log_level="INFO",
        live_poll_min=180, live_poll_max=300, recheck_interval_hours=6,
        dashboard_enabled=True, dashboard_host="127.0.0.1", dashboard_port=8765,
        stealth_browser_enabled=False, stealth_browser_headless=True,
        bricklink_consumer_key=None, bricklink_consumer_secret=None,
        bricklink_token=None, bricklink_token_secret=None, reverb_token=None,
        home_postcode=None, home_country="NL", max_distance_km=None,
        image_dedup_enabled=False,
    )


def test_classify_ok():
    assert _classify({"count_24h": 5, "count_7d": 5}) == "ok"


def test_classify_quiet():
    assert _classify({"count_24h": 0, "count_7d": 3}) == "quiet"


def test_classify_silent():
    assert _classify({"count_24h": 0, "count_7d": 0}) == "silent"


def test_classify_throttled_wins():
    assert _classify({"count_24h": 5, "throttle_failures": 4}) == "throttled"


def test_source_health_db(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    db.upsert_listing(_l("mkpl:1", "marktplaats", "NL"))
    db.upsert_listing(_l("mkpl:2", "marktplaats", "NL"))
    db.upsert_listing(_l("hd:1", "hood", "DE"))
    rows = db.source_health()
    by_site = {r["site"]: r for r in rows}
    assert by_site["marktplaats"]["count_24h"] == 2
    assert by_site["hood"]["count_24h"] == 1


def test_health_page_lists_configured_and_silent(tmp_path: Path):
    cfg = _cfg(tmp_path)
    db = Database(cfg.db_path)
    db.upsert_listing(_l("mkpl:1", "marktplaats", "NL"))
    sources = [
        _FakeSource("marktplaats", "NL"),
        _FakeSource("hood", "DE", _FakeThrottle(3.0, 12.0, 5)),
        _FakeSource("quoka", "DE"),  # silent
    ]
    client = TestClient(build_app(cfg, db, sources=sources))
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.text
    assert "marktplaats" in body
    assert "hood" in body
    assert "quoka" in body
    # Hood has failures=5 → throttled
    assert "throttled" in body
    # Quoka never seen → silent
    assert "silent" in body


def test_healthz_json(tmp_path: Path):
    cfg = _cfg(tmp_path)
    db = Database(cfg.db_path)
    db.upsert_listing(_l("mkpl:1", "marktplaats", "NL"))
    client = TestClient(build_app(cfg, db, sources=[]))
    payload = client.get("/healthz").json()
    assert payload["ok"] is True
    assert any(s["site"] == "marktplaats" for s in payload["sources"])
