from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Watcher:
    name: str
    query: str
    category_id: int | None = None
    min_price: float | None = None
    max_price: float | None = None
    require: list[re.Pattern] = field(default_factory=list)
    require_any: list[re.Pattern] = field(default_factory=list)
    blacklist: list[re.Pattern] = field(default_factory=list)
    value_sources: list[str] = field(default_factory=lambda: ["marktplaats_median"])
    min_score: int = 50
    priority: str = "medium"
    countries: list[str] = field(default_factory=lambda: ["NL"])
    query_overrides: dict[str, str] = field(default_factory=dict)
    sources: list[str] | None = None  # None = all sources matching countries

    def query_for_country(self, country: str) -> str:
        return self.query_overrides.get(country, self.query)

    @classmethod
    def from_dict(cls, d: dict) -> "Watcher":
        compile_list = lambda xs: [re.compile(p) for p in (xs or [])]
        return cls(
            name=d["name"],
            query=d["query"],
            category_id=d.get("category_id"),
            min_price=d.get("min_price"),
            max_price=d.get("max_price"),
            require=compile_list(d.get("require")),
            require_any=compile_list(d.get("require_any")),
            blacklist=compile_list(d.get("blacklist")),
            value_sources=d.get("value_sources", ["marktplaats_median"]),
            min_score=d.get("min_score", 50),
            priority=d.get("priority", "medium"),
            countries=d.get("countries", ["NL"]),
            query_overrides=d.get("query_overrides", {}),
            sources=d.get("sources"),
        )


@dataclass
class KeywordSweep:
    enabled: bool = False
    patterns: list[re.Pattern] = field(default_factory=list)
    min_price: float | None = None
    max_price: float | None = None
    min_score: int = 35
    priority: str = "low"

    @classmethod
    def from_dict(cls, d: dict | None) -> "KeywordSweep":
        if not d:
            return cls()
        return cls(
            enabled=d.get("enabled", False),
            patterns=[re.compile(p) for p in d.get("patterns", [])],
            min_price=d.get("min_price"),
            max_price=d.get("max_price"),
            min_score=d.get("min_score", 35),
            priority=d.get("priority", "low"),
        )


@dataclass
class Config:
    watchers: list[Watcher]
    keyword_sweep: KeywordSweep
    db_path: Path
    telegram_token: str
    telegram_chat_id: str
    telegram_admin_user_id: int | None
    ebay_app_id: str | None
    log_level: str
    live_poll_min: int
    live_poll_max: int
    recheck_interval_hours: int
    dashboard_enabled: bool
    dashboard_host: str
    dashboard_port: int
    stealth_browser_enabled: bool
    stealth_browser_headless: bool
    bricklink_consumer_key: str | None
    bricklink_consumer_secret: str | None
    bricklink_token: str | None
    bricklink_token_secret: str | None
    reverb_token: str | None


def load_config(watchers_path: Path = Path("config/watchers.yaml")) -> Config:
    raw = yaml.safe_load(watchers_path.read_text())
    watchers = [Watcher.from_dict(w) for w in raw.get("watchers", [])]
    sweep = KeywordSweep.from_dict(raw.get("keyword_sweep"))

    db_path = Path(os.getenv("DB_PATH", "./data/scanner.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)

    admin_raw = os.getenv("TELEGRAM_ADMIN_USER_ID", "").strip()
    admin = int(admin_raw) if admin_raw else None

    def _bool(name: str, default: bool) -> bool:
        return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "on")

    return Config(
        watchers=watchers,
        keyword_sweep=sweep,
        db_path=db_path,
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        telegram_admin_user_id=admin,
        ebay_app_id=os.getenv("EBAY_APP_ID") or None,
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        live_poll_min=int(os.getenv("LIVE_POLL_MIN_SECONDS", "180")),
        live_poll_max=int(os.getenv("LIVE_POLL_MAX_SECONDS", "300")),
        recheck_interval_hours=int(os.getenv("RECHECK_INTERVAL_HOURS", "6")),
        dashboard_enabled=_bool("DASHBOARD_ENABLED", True),
        dashboard_host=os.getenv("DASHBOARD_HOST", "0.0.0.0"),
        dashboard_port=int(os.getenv("DASHBOARD_PORT", "8765")),
        stealth_browser_enabled=_bool("STEALTH_BROWSER_ENABLED", False),
        stealth_browser_headless=_bool("STEALTH_BROWSER_HEADLESS", True),
        bricklink_consumer_key=os.getenv("BRICKLINK_CONSUMER_KEY") or None,
        bricklink_consumer_secret=os.getenv("BRICKLINK_CONSUMER_SECRET") or None,
        bricklink_token=os.getenv("BRICKLINK_TOKEN") or None,
        bricklink_token_secret=os.getenv("BRICKLINK_TOKEN_SECRET") or None,
        reverb_token=os.getenv("REVERB_TOKEN") or None,
    )
