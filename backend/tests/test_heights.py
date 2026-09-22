from pathlib import Path

import pytest

from skylineframe.heights import estimate_height_m


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


def test_heights_module_has_no_package_dependencies():
    # prepare imports this module; pulling fetch (httpx, osm2geojson) in through the back door
    # would make the geometry stage depend on the network stack.
    import skylineframe.heights as heights

    # read_text() rather than open(): the suite turns warnings into errors and an unclosed
    # file handle would fail this test on a ResourceWarning instead of on its assertion.
    source = Path(heights.__file__).read_text()
    assert not any(line.startswith("from .") for line in source.splitlines())
