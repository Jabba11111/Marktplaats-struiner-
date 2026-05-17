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
    last_price     REAL,
    site           TEXT DEFAULT 'marktplaats',
    country        TEXT DEFAULT 'NL'
);
CREATE INDEX IF NOT EXISTS idx_listings_site ON listings(site);

CREATE TABLE IF NOT EXISTS dedup_fingerprints (
    fingerprint    TEXT PRIMARY KEY,
    first_item_id  TEXT NOT NULL,
    first_seen_at  TEXT NOT NULL
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
            self._migrate(conn)

    @staticmethod
    def _migrate(conn) -> None:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(listings)")}
        if "site" not in cols:
            conn.execute("ALTER TABLE listings ADD COLUMN site TEXT DEFAULT 'marktplaats'")
        if "country" not in cols:
            conn.execute("ALTER TABLE listings ADD COLUMN country TEXT DEFAULT 'NL'")

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

    def upsert_listing(self, listing) -> tuple[bool, float | None]:
        """Returns (is_new, previous_price_if_dropped).

        previous_price_if_dropped is the prior price when the new price
        is at least 5% lower; None otherwise. Callers use this to trigger
        a re-alert on substantial price drops.
        """
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
                        category_id, first_seen_at, last_seen_at, last_price,
                        site, country)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        listing.item_id, listing.title, listing.description,
                        listing.price, listing.price_type, listing.url,
                        listing.seller_name, listing.location,
                        listing.posted_at.isoformat() if listing.posted_at else None,
                        listing.thumbnail_url, listing.category_id,
                        now, now, listing.price,
                        getattr(listing, "site", "marktplaats"),
                        getattr(listing, "country", "NL"),
                    ),
                )
                return True, None

            prev_price = row["last_price"]
            dropped_from: float | None = None
            if (prev_price and listing.price
                    and listing.price < prev_price * 0.95):
                dropped_from = float(prev_price)

            conn.execute(
                """UPDATE listings
                   SET last_seen_at = ?, last_price = ?,
                       title = ?, description = ?, price = ?, price_type = ?
                   WHERE item_id = ?""",
                (now, listing.price, listing.title, listing.description,
                 listing.price, listing.price_type, listing.item_id),
            )
            return False, dropped_from

    def listings_seen_within(self, hours: int, limit: int = 500) -> list[dict]:
        """Recently-active listings, for the recheck job."""
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT item_id, title, url, last_price, category_id
                   FROM listings WHERE last_seen_at >= ? AND url IS NOT NULL
                   ORDER BY last_seen_at DESC LIMIT ?""",
                (cutoff, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def recent_listings(self, limit: int = 100,
                        min_score: int | None = None,
                        site: str | None = None,
                        country: str | None = None) -> list[dict]:
        """For dashboard: listings with their best evaluation."""
        with self.connect() as conn:
            sql = """
                SELECT l.item_id, l.title, l.description, l.price, l.price_type,
                       l.url, l.location, l.thumbnail_url, l.first_seen_at,
                       l.last_price, l.site, l.country,
                       (SELECT MAX(score) FROM evaluations e WHERE e.item_id = l.item_id) AS best_score,
                       (SELECT estimated_value FROM evaluations e
                        WHERE e.item_id = l.item_id ORDER BY score DESC LIMIT 1) AS est_value,
                       (SELECT watcher_name FROM evaluations e
                        WHERE e.item_id = l.item_id ORDER BY score DESC LIMIT 1) AS watcher
                FROM listings l
                WHERE 1=1
            """
            params: list = []
            if site:
                sql += " AND l.site = ?"
                params.append(site)
            if country:
                sql += " AND l.country = ?"
                params.append(country)
            sql += " ORDER BY l.first_seen_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            out = [dict(r) for r in rows]
            if min_score is not None:
                out = [r for r in out if (r["best_score"] or 0) >= min_score]
            return out

    def distinct_sites(self) -> list[str]:
        with self.connect() as conn:
            return [r["site"] for r in conn.execute(
                "SELECT DISTINCT site FROM listings ORDER BY site"
            ) if r["site"]]

    def recent_alerts(self, limit: int = 50) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT a.score, a.watcher_name, a.sent_at,
                          l.title, l.url, l.price, l.thumbnail_url, l.location
                   FROM alerts a JOIN listings l ON l.item_id = a.item_id
                   ORDER BY a.id DESC LIMIT ?""", (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def remove_adhoc_watch(self, watch_id: int) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "DELETE FROM ad_hoc_watches WHERE id = ?", (watch_id,),
            )
            return cur.rowcount

    def list_adhoc_watches_full(self) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT id, query, created_at FROM ad_hoc_watches ORDER BY id"
            )]

    # --- cross-site dedup ---

    def check_and_register_fingerprint(
        self, fingerprint: str, item_id: str,
    ) -> str | None:
        """Atomic check-and-set.

        Returns None if this is the first time we see this fingerprint
        (caller should proceed to alert). Returns the prior item_id if
        we've seen it before (caller should suppress the alert).
        """
        with self.connect() as conn:
            row = conn.execute(
                "SELECT first_item_id FROM dedup_fingerprints WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if row:
                return row["first_item_id"]
            conn.execute(
                """INSERT INTO dedup_fingerprints
                   (fingerprint, first_item_id, first_seen_at) VALUES (?,?,?)""",
                (fingerprint, item_id, now_iso()),
            )
            return None

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
