from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    item_id        TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    description    TEXT,
    price          REAL,
    price_type     TEXT,
    url            TEXT,
    seller_name    TEXT,
    location       TEXT,
    posted_at      TEXT,
    thumbnail_url  TEXT,
    category_id    INTEGER,
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    last_price     REAL
);

CREATE TABLE IF NOT EXISTS evaluations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id        TEXT NOT NULL,
    watcher_name   TEXT NOT NULL,
    score          INTEGER NOT NULL,
    estimated_value REAL,
    valuation_source TEXT,
    reasons        TEXT,
    evaluated_at   TEXT NOT NULL,
    FOREIGN KEY(item_id) REFERENCES listings(item_id)
);
CREATE INDEX IF NOT EXISTS idx_eval_item ON evaluations(item_id);

CREATE TABLE IF NOT EXISTS alerts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id        TEXT NOT NULL,
    watcher_name   TEXT NOT NULL,
    score          INTEGER NOT NULL,
    sent_at        TEXT NOT NULL,
    telegram_message_id INTEGER,
    UNIQUE(item_id, watcher_name)
);

CREATE TABLE IF NOT EXISTS mutes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern        TEXT NOT NULL UNIQUE,
    kind           TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS valuation_cache (
    cache_key      TEXT PRIMARY KEY,
    estimated_value REAL,
    sample_size    INTEGER,
    source         TEXT,
    cached_at      TEXT NOT NULL,
    expires_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ad_hoc_watches (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    query          TEXT NOT NULL,
    added_by       INTEGER,
    created_at     TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._init()

    def _init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- listings ---

    def upsert_listing(self, listing) -> bool:
        """Returns True if this is a new listing."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT item_id, last_price FROM listings WHERE item_id = ?",
                (listing.item_id,),
            ).fetchone()
            now = now_iso()
            if row is None:
                conn.execute(
                    """INSERT INTO listings
                       (item_id, title, description, price, price_type, url,
                        seller_name, location, posted_at, thumbnail_url,
                        category_id, first_seen_at, last_seen_at, last_price)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        listing.item_id, listing.title, listing.description,
                        listing.price, listing.price_type, listing.url,
                        listing.seller_name, listing.location,
                        listing.posted_at.isoformat() if listing.posted_at else None,
                        listing.thumbnail_url, listing.category_id,
                        now, now, listing.price,
                    ),
                )
                return True
            conn.execute(
                "UPDATE listings SET last_seen_at = ?, last_price = ? WHERE item_id = ?",
                (now, listing.price, listing.item_id),
            )
            return False

    def has_alerted(self, item_id: str, watcher_name: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM alerts WHERE item_id = ? AND watcher_name = ?",
                (item_id, watcher_name),
            ).fetchone()
            return row is not None

    def record_alert(self, item_id: str, watcher_name: str, score: int,
                     telegram_message_id: int | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO alerts
                   (item_id, watcher_name, score, sent_at, telegram_message_id)
                   VALUES (?,?,?,?,?)""",
                (item_id, watcher_name, score, now_iso(), telegram_message_id),
            )

    def record_evaluation(self, ev) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO evaluations
                   (item_id, watcher_name, score, estimated_value,
                    valuation_source, reasons, evaluated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    ev.listing.item_id, ev.watcher_name, ev.score,
                    ev.valuation.estimated_value if ev.valuation else None,
                    ev.valuation.source if ev.valuation else None,
                    " | ".join(ev.reasons), now_iso(),
                ),
            )

    # --- mutes ---

    def add_mute(self, pattern: str, kind: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO mutes (pattern, kind, created_at) VALUES (?,?,?)",
                (pattern, kind, now_iso()),
            )

    def remove_mute(self, pattern: str) -> int:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM mutes WHERE pattern = ?", (pattern,))
            return cur.rowcount

    def list_mutes(self) -> list[tuple[str, str]]:
        with self.connect() as conn:
            return [(r["pattern"], r["kind"]) for r in
                    conn.execute("SELECT pattern, kind FROM mutes")]

    def is_muted(self, text: str) -> bool:
        text_lower = text.lower()
        for pattern, _ in self.list_mutes():
            if pattern.lower() in text_lower:
                return True
        return False

    # --- ad-hoc watches ---

    def add_adhoc_watch(self, query: str, user_id: int | None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO ad_hoc_watches (query, added_by, created_at) VALUES (?,?,?)",
                (query, user_id, now_iso()),
            )

    def list_adhoc_watches(self) -> list[str]:
        with self.connect() as conn:
            return [r["query"] for r in
                    conn.execute("SELECT query FROM ad_hoc_watches ORDER BY id")]

    # --- valuation cache ---

    def get_cached_valuation(self, key: str) -> tuple[float, int, str] | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT estimated_value, sample_size, source FROM valuation_cache
                   WHERE cache_key = ? AND expires_at > ?""",
                (key, now_iso()),
            ).fetchone()
            return (row["estimated_value"], row["sample_size"], row["source"]) if row else None

    def cache_valuation(self, key: str, value: float, sample_size: int,
                        source: str, expires_at_iso: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO valuation_cache
                   (cache_key, estimated_value, sample_size, source, cached_at, expires_at)
                   VALUES (?,?,?,?,?,?)""",
                (key, value, sample_size, source, now_iso(), expires_at_iso),
            )

    # --- stats ---

    def stats(self, hours: int = 24) -> dict:
        with self.connect() as conn:
            cutoff = (datetime.now(timezone.utc).timestamp() - hours * 3600)
            cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
            scanned = conn.execute(
                "SELECT COUNT(*) c FROM listings WHERE first_seen_at >= ?",
                (cutoff_iso,),
            ).fetchone()["c"]
            alerted = conn.execute(
                "SELECT COUNT(*) c FROM alerts WHERE sent_at >= ?",
                (cutoff_iso,),
            ).fetchone()["c"]
            evaluated = conn.execute(
                "SELECT COUNT(*) c FROM evaluations WHERE evaluated_at >= ?",
                (cutoff_iso,),
            ).fetchone()["c"]
            return {
                "scanned": scanned,
                "evaluated": evaluated,
                "alerted": alerted,
                "hours": hours,
            }
