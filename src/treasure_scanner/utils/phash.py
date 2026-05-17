"""Async perceptual image hashing for cross-site dedup.

If Pillow / imagehash aren't installed, every function returns None
and dedup silently falls back to title+price hashes.
"""
from __future__ import annotations

import asyncio
import io

import httpx
import structlog

log = structlog.get_logger(__name__)

try:
    import imagehash
    from PIL import Image
    _HAVE_HASH = True
except ImportError:
    imagehash = None  # type: ignore
    Image = None  # type: ignore
    _HAVE_HASH = False


async def compute_phash(
    url: str | None,
    client: httpx.AsyncClient | None = None,
    timeout: float = 5.0,
) -> str | None:
    """Download `url` and return a 16-char hex perceptual hash.

    Skips silently on any failure: missing libs, network errors, broken
    image, content-type that isn't an image. dedup is best-effort.
    """
    if not url or not _HAVE_HASH:
        return None

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 TreasureScanner"},
            follow_redirects=True,
        )
    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            return None
        content_type = resp.headers.get("content-type", "")
        if "image" not in content_type.lower():
            return None
        data = resp.content
        if len(data) < 200 or len(data) > 5_000_000:
            return None
    except Exception as e:
        log.debug("phash_download_failed", url=url, error=str(e))
        return None
    finally:
        if owns_client:
            await client.aclose()

    try:
        return await asyncio.get_event_loop().run_in_executor(
            None, _hash_bytes, data,
        )
    except Exception as e:
        log.debug("phash_compute_failed", error=str(e))
        return None


def _hash_bytes(data: bytes) -> str | None:
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGB")
            # 8x8 → 64-bit phash, 16 hex chars
            return str(imagehash.phash(img))
    except Exception:
        return None


def phash_fingerprint(phash_hex: str) -> str:
    """Wrap a phash so the dedup table can distinguish it from
    title-hash fingerprints."""
    return f"phash:{phash_hex}"
