"""Source protocol — every listing source implements this interface."""
from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

from ..models import Listing


@runtime_checkable
class Source(Protocol):
    """A listing source (marketplace or auction site)."""
    name: str
    country: str
    site_code: str            # short id, used as item_id prefix
    requires_browser: bool    # True if site needs StealthBrowser

    async def search(
        self,
        query: str,
        max_pages: int = 1,
        category_id: int | None = None,
    ) -> AsyncIterator[Listing]:
        """Yield listings, newest-first."""
        ...

    async def aclose(self) -> None: ...
