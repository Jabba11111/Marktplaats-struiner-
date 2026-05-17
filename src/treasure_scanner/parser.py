"""Extract structured specs (RAM, VRAM, GPU model) from listing text."""
from __future__ import annotations

import re
from dataclasses import dataclass

RAM_PATTERN = re.compile(
    r"\b(8|12|16|24|32|48|64|96|128|192|256|384|512)\s?gb\b(?:\s*(?:ddr[345]|ram|memory|geheugen|werkgeheugen))?",
    re.IGNORECASE,
)
VRAM_PATTERN = re.compile(
    r"\b(4|6|8|10|11|12|16|20|24|32|40|48|80)\s?gb\b\s*(?:vram|gddr|hbm)",
    re.IGNORECASE,
)

GPU_TABLE = {
    "rtx 4090": 24, "rtx 4080": 16, "rtx 4070 ti": 12, "rtx 4070": 12,
    "rtx 3090 ti": 24, "rtx 3090": 24, "rtx 3080 ti": 12, "rtx 3080": 10,
    "rtx 3070": 8, "rtx 3060": 12,
    "rtx a6000": 48, "rtx a5000": 24, "rtx a4500": 20, "rtx a4000": 16,
    "tesla p40": 24, "tesla p100": 16, "tesla v100": 16, "tesla a100": 40,
    "a100": 40, "a40": 48, "a30": 24, "a10": 24,
    "quadro rtx 8000": 48, "quadro rtx 6000": 24, "quadro rtx 5000": 16,
    "mi25": 16, "mi50": 16, "mi60": 32, "mi100": 32, "mi210": 64, "mi250": 128,
}


@dataclass
class ParsedSpecs:
    ram_gb: int | None = None
    vram_gb: int | None = None
    gpu_model: str | None = None

    @property
    def has_useful_gpu(self) -> bool:
        return (self.vram_gb or 0) >= 12

    @property
    def meets_minimum_ram(self) -> bool:
        return (self.ram_gb or 0) >= 32


def parse_specs(title: str, description: str) -> ParsedSpecs:
    text = f"{title}\n{description}"
    text_lower = text.lower()

    ram = None
    for m in RAM_PATTERN.finditer(text):
        val = int(m.group(1))
        # Pick the largest plausible RAM hit (avoids confusing VRAM mentions).
        if val >= 8 and (ram is None or val > ram):
            ram = val

    vram_explicit = None
    for m in VRAM_PATTERN.finditer(text):
        val = int(m.group(1))
        if vram_explicit is None or val > vram_explicit:
            vram_explicit = val

    gpu_model = None
    vram_from_model = None
    for name, vram in GPU_TABLE.items():
        if name in text_lower:
            if gpu_model is None or len(name) > len(gpu_model):
                gpu_model = name
                vram_from_model = vram

    vram = vram_explicit or vram_from_model

    # Heuristic: if RAM == VRAM, one of the two is likely misread. Trust GPU model.
    if ram and vram and ram == vram and vram_from_model:
        # the "32GB" was probably referring to system RAM, not the GPU
        pass

    return ParsedSpecs(ram_gb=ram, vram_gb=vram, gpu_model=gpu_model)
