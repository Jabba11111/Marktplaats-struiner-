"""Realistic browser fingerprints. Rotated per session."""
from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass
class Fingerprint:
    user_agent: str
    viewport: dict
    locale: str
    timezone: str
    platform: str
    accept_language: str
    device_scale_factor: float
    color_scheme: str


# Curated set — recent stable Chrome/Firefox versions, common desktop sizes
# in NL/BE/DE markets. Keep this list rotating; outdated UAs leak.
PROFILES: list[Fingerprint] = [
    Fingerprint(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1920, "height": 1080},
        locale="nl-NL",
        timezone="Europe/Amsterdam",
        platform="Win32",
        accept_language="nl-NL,nl;q=0.9,en;q=0.8",
        device_scale_factor=1.0,
        color_scheme="light",
    ),
    Fingerprint(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
        ),
        viewport={"width": 1680, "height": 1050},
        locale="nl-NL",
        timezone="Europe/Amsterdam",
        platform="MacIntel",
        accept_language="nl-NL,nl;q=0.9,en;q=0.7",
        device_scale_factor=2.0,
        color_scheme="light",
    ),
    Fingerprint(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
            "Gecko/20100101 Firefox/128.0"
        ),
        viewport={"width": 1536, "height": 864},
        locale="de-DE",
        timezone="Europe/Berlin",
        platform="Win32",
        accept_language="de-DE,de;q=0.9,en;q=0.7",
        device_scale_factor=1.25,
        color_scheme="light",
    ),
    Fingerprint(
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1920, "height": 1200},
        locale="nl-BE",
        timezone="Europe/Brussels",
        platform="Linux x86_64",
        accept_language="nl-BE,nl;q=0.9,fr-BE;q=0.7,en;q=0.6",
        device_scale_factor=1.0,
        color_scheme="light",
    ),
]


def random_fingerprint() -> Fingerprint:
    fp = random.choice(PROFILES)
    # Small viewport jitter to avoid hash-based fingerprinting.
    return Fingerprint(
        user_agent=fp.user_agent,
        viewport={
            "width": fp.viewport["width"] + random.randint(-10, 10),
            "height": fp.viewport["height"] + random.randint(-10, 10),
        },
        locale=fp.locale,
        timezone=fp.timezone,
        platform=fp.platform,
        accept_language=fp.accept_language,
        device_scale_factor=fp.device_scale_factor,
        color_scheme=fp.color_scheme,
    )
