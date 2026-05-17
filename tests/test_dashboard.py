from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from treasure_scanner.config import Config, KeywordSweep
from treasure_scanner.dashboard import build_app
from treasure_scanner.db import Database


@pytest.fixture
def app(tmp_path: Path):
    cfg = Config(
        watchers=[], keyword_sweep=KeywordSweep(),
        db_path=tmp_path / "t.db",
        telegram_token="", telegram_chat_id="", telegram_admin_user_id=None,
        ebay_app_id=None, log_level="INFO",
        live_poll_min=180, live_poll_max=300, recheck_interval_hours=6,
        dashboard_enabled=True, dashboard_host="127.0.0.1", dashboard_port=8765,
        stealth_browser_enabled=False, stealth_browser_headless=True,
        bricklink_consumer_key=None, bricklink_consumer_secret=None,
        bricklink_token=None, bricklink_token_secret=None, reverb_token=None,
    )
    db = Database(cfg.db_path)
    return build_app(cfg, db), db


def test_index_renders(app):
    application, _ = app
    client = TestClient(application)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Treasure Scanner" in resp.text


def test_listings_page(app):
    application, _ = app
    client = TestClient(application)
    resp = client.get("/listings")
    assert resp.status_code == 200


def test_watchers_add_and_delete(app):
    application, db = app
    client = TestClient(application)

    resp = client.post(
        "/watchers/adhoc", data={"query": "ThinkPad P1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    items = db.list_adhoc_watches_full()
    assert len(items) == 1
    assert items[0]["query"] == "ThinkPad P1"

    resp = client.post(
        f"/watchers/adhoc/{items[0]['id']}/delete", follow_redirects=False,
    )
    assert resp.status_code == 303
    assert db.list_adhoc_watches_full() == []


def test_mutes_add_and_delete(app):
    application, db = app
    client = TestClient(application)

    resp = client.post(
        "/mutes", data={"pattern": "ruilen"}, follow_redirects=False,
    )
    assert resp.status_code == 303
    assert db.list_mutes() == [("ruilen", "text")]

    resp = client.post(
        "/mutes/delete", data={"pattern": "ruilen"}, follow_redirects=False,
    )
    assert resp.status_code == 303
    assert db.list_mutes() == []


def test_healthz(app):
    application, _ = app
    client = TestClient(application)
    assert client.get("/healthz").json() == {"ok": True}
