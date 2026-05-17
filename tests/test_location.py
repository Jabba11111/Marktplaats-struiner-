from treasure_scanner.utils.location import (
    extract_postcode, haversine_km, LocationResolver,
)


def test_haversine_amsterdam_to_berlin():
    # Amsterdam (52.37, 4.90) → Berlin (52.52, 13.40) is ~575 km.
    d = haversine_km(52.37, 4.90, 52.52, 13.40)
    assert 560 < d < 590


def test_haversine_same_point_is_zero():
    assert haversine_km(50.0, 5.0, 50.0, 5.0) == 0.0


def test_extract_postcode_nl():
    assert extract_postcode("Amsterdam 1011 AB", "NL") == "1011"
    assert extract_postcode("Te koop in Utrecht 3511", "NL") == "3511"
    assert extract_postcode("alleen stad", "NL") is None


def test_extract_postcode_be():
    assert extract_postcode("Antwerpen 2000", "BE") == "2000"
    # NL postcodes are 4 digits too — BE pattern accepts them. That's
    # fine since the resolver passes country through to pgeocode.
    assert extract_postcode("Gent", "BE") is None


def test_extract_postcode_de():
    assert extract_postcode("10115 Berlin", "DE") == "10115"
    assert extract_postcode("80331 Muenchen", "DE") == "80331"
    assert extract_postcode("Berlin", "DE") is None


def test_extract_postcode_unknown_country():
    assert extract_postcode("anything", "XX") is None


def test_resolver_disabled_without_postcode():
    r = LocationResolver(home_postcode=None, home_country="NL")
    assert not r.enabled
    assert r.distance_km("1011 AB Amsterdam", "NL") is None
