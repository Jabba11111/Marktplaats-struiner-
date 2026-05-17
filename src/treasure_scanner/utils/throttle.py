"""Adaptive throttle. Doubles wait on consecutive blocks, decays on success.

Sources call::

    await throttle.wait()
    try:
        resp = await client.get(...)
        throttle.record_response(resp.status_code)
    except Exception:
        throttle.record_failure()

Pattern:
- Base interval, e.g. 3.0s for HTML sources.
- After 3 consecutive 429/403/5xx/network errors: interval *= 2,
  capped at `max_interval` (default 300s).
- On any success: failure counter resets; current interval decays
  10% toward base. Slow back to normal so we don't immediately
  re-aggrieve the site after a block.
"""
from __future__ import annotations

import asyncio
import random


class Throttle:
    def __init__(self, base_interval: float, max_interval: float = 300.0):
        self.base = float(base_interval)
        self.max = float(max_interval)
        self.current = float(base_interval)
        self.consecutive_failures = 0
        self._last_at = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            loop = asyncio.get_event_loop()
            wait = self.current - (loop.time() - self._last_at)
            if wait > 0:
                await asyncio.sleep(wait + random.uniform(0, 0.8))
            self._last_at = asyncio.get_event_loop().time()

    def record_response(self, status_code: int) -> None:
        if status_code in (429, 403) or 500 <= status_code < 600:
            self.record_failure()
        else:
            self.record_success()

    def record_success(self) -> None:
        self.consecutive_failures = 0
        if self.current > self.base:
            self.current = max(self.base, self.current * 0.9)

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= 3:
            self.current = min(self.max, max(self.base * 2, self.current * 2))
