"""phash tests — only run when Pillow + ImageHash are installed."""
import asyncio
import io

import pytest

phash_mod = pytest.importorskip("treasure_scanner.utils.phash")
Image = pytest.importorskip("PIL.Image")


def _png_bytes(color):
    img = Image.new("RGB", (64, 64), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_phash_same_image_same_hash():
    a = phash_mod._hash_bytes(_png_bytes((255, 0, 0)))
    b = phash_mod._hash_bytes(_png_bytes((255, 0, 0)))
    assert a == b
    assert a is not None
    assert len(a) == 16  # 64-bit hex


def test_phash_returns_none_on_bad_bytes():
    assert phash_mod._hash_bytes(b"not an image") is None


def test_phash_fingerprint_prefix():
    fp = phash_mod.phash_fingerprint("abc123")
    assert fp.startswith("phash:")
    assert "abc123" in fp


def test_compute_phash_skips_on_no_url():
    result = asyncio.run(phash_mod.compute_phash(None))
    assert result is None
