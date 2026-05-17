from treasure_scanner.parser import parse_specs


def test_parses_ram_and_vram():
    specs = parse_specs(
        "Workstation 64GB DDR4 RTX 3090",
        "Krachtig systeem met 24GB VRAM en NVMe SSD.",
    )
    assert specs.ram_gb == 64
    assert specs.vram_gb == 24
    assert specs.gpu_model == "rtx 3090"
    assert specs.meets_minimum_ram
    assert specs.has_useful_gpu


def test_picks_largest_ram():
    specs = parse_specs(
        "Server 32GB upgradable",
        "Komt met 128GB RAM geinstalleerd.",
        )
    assert specs.ram_gb == 128


def test_no_gpu_no_vram():
    specs = parse_specs("Dell OptiPlex 32GB RAM", "Geen aparte GPU.")
    assert specs.ram_gb == 32
    assert specs.vram_gb is None
    assert not specs.has_useful_gpu


def test_tesla_p40_recognized():
    specs = parse_specs("AI server Tesla P40", "24GB voor inference")
    assert specs.gpu_model == "tesla p40"
    assert specs.vram_gb == 24
