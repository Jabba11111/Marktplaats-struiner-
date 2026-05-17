from datetime import datetime, timezone
from pathlib import Path

from treasure_scanner.db import Database
from treasure_scanner.models import Listing


def _listing(item_id, price, title="Test item"):
    return Listing(
        item_id=item_id, title=title, description="", price=price,
        price_type="fixed", url=f"https://x/{item_id}", seller_name=None,
        location=None, posted_at=datetime.now(timezone.utc),
        thumbnail_url=None, category_id=None, category_name=None,
    )


def test_upsert_new_listing(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    is_new, dropped = db.upsert_listing(_listing("1", 100))
    assert is_new is True
    assert dropped is None


def test_upsert_existing_no_drop(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    db.upsert_listing(_listing("1", 100))
    is_new, dropped = db.upsert_listing(_listing("1", 100))
    assert is_new is False
    assert dropped is None


def test_upsert_detects_price_drop(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    db.upsert_listing(_listing("1", 100))
    is_new, dropped = db.upsert_listing(_listing("1", 70))  # 30% drop
    assert is_new is False
    assert dropped == 100.0


def test_upsert_ignores_tiny_drop(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    db.upsert_listing(_listing("1", 100))
    is_new, dropped = db.upsert_listing(_listing("1", 97))  # 3% drop
    assert dropped is None


def test_mute_round_trip(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    db.add_mute("ruilen", "text")
    assert db.is_muted("alleen ruilen voor iets")
    assert not db.is_muted("rt 3090 nieuw in doos")
    assert db.remove_mute("ruilen") == 1


def test_adhoc_watch(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    db.add_adhoc_watch("ThinkPad P1", user_id=42)
    full = db.list_adhoc_watches_full()
    assert len(full) == 1
    assert full[0]["query"] == "ThinkPad P1"
    db.remove_adhoc_watch(full[0]["id"])
    assert db.list_adhoc_watches_full() == []
