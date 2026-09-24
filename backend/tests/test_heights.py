import subprocess
import sys

import pytest

from skylineframe.features import RoofSpec
from skylineframe.heights import default_roof, estimate_height_m


@pytest.mark.parametrize(
    "kind,area,expected",
    [
        ("cathedral", 5000.0, 35.0),
        ("church", 800.0, 18.0),
        ("office", 5000.0, 20.0),
        ("apartments", 300.0, 15.0),
        ("retail", 2000.0, 10.0),
        ("house", 90.0, 7.0),
        ("garage", 30.0, 3.0),
        ("yes", 50.0, 5.0),
        ("yes", 100.0, 8.0),
        ("yes", 399.0, 8.0),
        ("yes", 400.0, 12.0),
        ("yes", 1499.0, 12.0),
        ("yes", 1500.0, 15.0),
        ("", 20000.0, 15.0),
    ],
)
def test_estimate_height_m(kind, area, expected):
    assert estimate_height_m(kind, area) == pytest.approx(expected)


@pytest.mark.parametrize("kind", ["house", "detached", "semidetached_house", "bungalow"])
@pytest.mark.parametrize("area", [50.0, 199.0, 200.0, 1000.0])
def test_default_roof_house_kinds_get_a_gable_at_any_area(kind, area):
    assert default_roof(kind, area) == RoofSpec(shape="gabled")


@pytest.mark.parametrize(
    "area,gabled",
    [(50.0, True), (199.0, True), (199.99, True), (200.0, False), (201.0, False), (5000.0, False)],
)
def test_default_roof_residential_only_below_200_m2(area, gabled):
    roof = default_roof("residential", area)
    assert (roof == RoofSpec(shape="gabled")) if gabled else roof is None


@pytest.mark.parametrize("kind", ["apartments", "garage", "", "yes", "terrace", "House", "church"])
def test_default_roof_other_kinds_stay_flat(kind):
    assert default_roof(kind, 100.0) is None


def test_default_roof_leaves_height_and_direction_to_prepare():
    roof = default_roof("house", 120.0)
    assert isinstance(roof, RoofSpec)
    assert roof.shape == "gabled"
    assert roof.height_m == 0.0  # derived from the footprint later (default_roof_height_m)
    assert roof.direction_deg is None  # long axis of the rectangle


def test_default_roof_returns_a_fresh_spec_each_call():
    # RoofSpec is mutable; a shared instance would let one building's roof edit leak to all.
    assert default_roof("house", 100.0) is not default_roof("house", 100.0)


def test_heights_module_does_not_pull_in_the_network_stack():
    # prepare imports this module; pulling fetch (httpx, osm2geojson) in through the back door
    # would make the geometry stage depend on the network stack. A fresh interpreter, because
    # other tests in this session have long since imported fetch.
    code = (
        "import sys, skylineframe.heights\n"
        "bad = [m for m in ('httpx', 'osm2geojson', 'skylineframe.fetch') if m in sys.modules]\n"
        "print(','.join(bad))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == ""


@pytest.mark.parametrize(
    ("area", "freestanding", "gabled"),
    [(120.0, True, True), (29.9, True, False), (30.0, True, True), (200.0, True, False), (120.0, False, False)],
)
def test_default_roof_yes_only_when_freestanding_and_house_sized(area, freestanding, gabled):
    assert (default_roof("yes", area, freestanding) is not None) is gabled
