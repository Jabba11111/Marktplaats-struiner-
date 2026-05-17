from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Listing:
    """Normalized Marktplaats listing."""
    item_id: str
    title: str
    description: str
    price: float | None  # None for "bieden", "n.o.t.k.", etc.
    price_type: str  # "fixed", "bidding", "free", "see_description", "reserved"
    url: str
    seller_name: str | None
    location: str | None
    posted_at: datetime
    thumbnail_url: str | None
    category_id: int | None
    category_name: str | None
    raw: dict = field(default_factory=dict)


@dataclass
class Valuation:
    estimated_value: float
    confidence: float  # 0..1
    source: str
    sample_size: int
    note: str = ""


@dataclass
class Evaluation:
    listing: Listing
    watcher_name: str
    score: int  # 0..100
    valuation: Valuation | None
    reasons: list[str] = field(default_factory=list)
    priority: str = "medium"

    @property
    def deal_margin(self) -> float | None:
        if not self.valuation or self.listing.price is None:
            return None
        v = self.valuation.estimated_value
        if v <= 0:
            return None
        return (v - self.listing.price) / v
