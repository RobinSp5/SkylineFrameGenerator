import re

import pytest

from skylineframe.naming import MAX_STEM_LEN, coordinate_label, file_stem, scale_tag, slugify
from skylineframe.spec import FrameSpec

SAFE = re.compile(r"^[A-Za-z0-9._-]+$")


@pytest.mark.parametrize(
    ("label", "slug"),
    [
        ("Eppstein", "Eppstein"),
        ("Frankfurt am Main – Altstadt", "Frankfurt-am-Main_Altstadt"),
        ("Königstein im Taunus", "Koenigstein-im-Taunus"),
        ("Gießen – Nordstadt", "Giessen_Nordstadt"),
        ("Überlingen – Süd", "Ueberlingen_Sued"),
        ("ÄÖÜ äöü ß", "AeOeUe-aeoeue-ss"),
        ("Bad Homburg v. d. Höhe", "Bad-Homburg-v-d-Hoehe"),
        ("Sachsenhausen-Nord", "Sachsenhausen-Nord"),
        ("  Frankfurt  (Oder) / Słubice  ", "Frankfurt-Oder-Slubice"),
        ("Café Crème", "Cafe-Creme"),
        ("50.1413N 8.3925E", "50.1413N-8.3925E"),
        ("東京 – Shibuya", "Shibuya"),
        ("../../etc/passwd", "etc-passwd"),
    ],
)
def test_slugify(label, slug):
    assert slugify(label) == slug
    assert SAFE.match(slug)


def test_slugify_of_nothing_usable_is_empty():
    assert slugify("東京") == ""
    assert slugify("  – ") == ""


def test_slugify_caps_the_length_without_a_dangling_separator():
    slug = slugify("Sehr langer Ortsname " * 10, max_len=30)
    assert len(slug) <= 30
    assert not slug.endswith(("-", "_", "."))


@pytest.mark.parametrize(
    ("side_m", "plate_mm", "tag"),
    [(1500, 100, "1500m_10cm"), (800, 100, "800m_10cm"), (1500, 200, "1500m_20cm"), (1234.6, 125, "1235m_125mm")],
)
def test_scale_tag(side_m, plate_mm, tag):
    spec = FrameSpec(center_lat=50.1, center_lon=8.6, side_m=side_m, plate_size_mm=plate_mm)
    assert scale_tag(spec) == tag


def test_file_stem_joins_place_and_scale():
    spec = FrameSpec(center_lat=50.1, center_lon=8.6, side_m=1500, plate_size_mm=100)
    assert file_stem("Frankfurt am Main – Innenstadt", spec) == "Frankfurt-am-Main_Innenstadt_1500m_10cm"


def test_file_stem_is_capped_and_keeps_the_scale():
    spec = FrameSpec(center_lat=50.1, center_lon=8.6)
    stem = file_stem("Ein wirklich außerordentlich langer Stadtteilname " * 5, spec)
    assert len(stem) <= MAX_STEM_LEN
    assert stem.endswith("_1500m_10cm")
    assert "__" not in stem and "-_" not in stem
    assert SAFE.match(stem)


def test_file_stem_without_a_usable_place_falls_back_to_a_generic_name():
    spec = FrameSpec(center_lat=50.1, center_lon=8.6)
    assert file_stem("東京", spec) == "Skyline_1500m_10cm"


@pytest.mark.parametrize(
    ("lat", "lon", "label"),
    [(50.1413, 8.3925, "50.1413N 8.3925E"), (-33.85678, -70.6, "33.8568S 70.6000W"), (0, 0, "0.0000N 0.0000E")],
)
def test_coordinate_label(lat, lon, label):
    assert coordinate_label(lat, lon) == label
