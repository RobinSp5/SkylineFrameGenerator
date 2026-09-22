import pytest
from shapely.geometry import box

from skylineframe.features import Block, Building, RoofSpec
from skylineframe.prepare import Prepared
from skylineframe.scale import building_height_mm, raw_height_mm, scale_features
from skylineframe.spec import FrameSpec


def spec(**kw) -> FrameSpec:
    return FrameSpec(center_lat=50, center_lon=8, side_m=1000, plate_size_mm=100, **kw)  # 0.1 mm/m


def test_height_applies_scale_and_exaggeration():
    assert building_height_mm(10.0, spec(z_exaggeration=1.5)) == pytest.approx(1.5)


def test_height_enforces_minimum():
    assert building_height_mm(2.0, spec(min_building_height_mm=0.8)) == pytest.approx(0.8)


def test_height_is_capped_at_the_plate_size():
    # A mistagged 5000 m tower would be 750 mm tall on a 100 mm plate; the cap keeps the model
    # printable even when one building survives the fetch-side sanity check.
    assert building_height_mm(5000.0, spec(z_exaggeration=1.5)) == pytest.approx(100.0)


def test_height_rounds_to_hundredth():
    assert building_height_mm(3.333, spec(z_exaggeration=1.0)) == pytest.approx(0.8)  # 0.3333 -> min
    assert building_height_mm(12.345, spec(z_exaggeration=1.0)) == pytest.approx(1.23)


def building(geom, **kw) -> Building:
    """A prepared building: prepare always fills eaves_m/ridge_m, so the tests must too."""
    values = {"height_m": 20.0, "eaves_m": 20.0, "ridge_m": 20.0}
    values.update(kw)
    return Building(geom, **values)


def test_footprints_are_scaled_about_origin():
    prepared = Prepared(buildings=[building(box(-500, -500, 500, 500), height_m=10.0, eaves_m=10.0, ridge_m=10.0)])
    out = scale_features(prepared, spec())
    assert out.buildings[0].geom.bounds == pytest.approx((-50, -50, 50, 50))
    assert out.buildings[0].height_mm == pytest.approx(1.5)


def test_roads_and_water_are_scaled():
    prepared = Prepared(buildings=[], roads=[box(0, 0, 100, 10)], water=[box(-500, -500, 0, 0)])
    out = scale_features(prepared, spec())
    assert out.roads[0].bounds == pytest.approx((0, 0, 10, 1))
    assert out.water[0].bounds == pytest.approx((-50, -50, 0, 0))


def test_eaves_ridge_and_rect_are_scaled():
    b = building(
        box(-100, -100, 100, 100),
        height_m=20.0,
        eaves_m=20.0,
        ridge_m=26.0,
        roof=RoofSpec(shape="gabled", height_m=6.0, direction_deg=45.0),
        rect=((-100.0, -100.0), (100.0, -100.0), (100.0, 100.0), (-100.0, 100.0)),
    )
    out = scale_features(Prepared(buildings=[b]), spec())
    p = out.buildings[0]
    assert p.height_mm == pytest.approx(3.0)  # 20 m x 0.1 mm/m x 1.5
    assert p.z0_mm == 0.0
    assert p.roof is not None
    assert p.roof.shape == "gabled" and p.roof.direction_deg == 45.0
    assert p.roof.z_eaves_mm == pytest.approx(3.0)
    assert p.roof.z_ridge_mm == pytest.approx(3.9)  # 26 m x 0.15
    # pytest.approx rejects nested tuples, so the corners are compared flat.
    flat = [value for corner in p.roof.rect_mm for value in corner]
    assert flat == pytest.approx([-10.0, -10.0, 10.0, -10.0, 10.0, 10.0, -10.0, 10.0])


def test_roof_below_the_print_minimum_is_dropped():
    # 20 -> 20.1 m is 0.015 mm of ridge, far below the 0.3 mm a roof needs to be visible.
    b = building(
        box(-100, -100, 100, 100),
        ridge_m=20.1,
        roof=RoofSpec(shape="gabled", height_m=0.1),
        rect=((-100.0, -100.0), (100.0, -100.0), (100.0, 100.0), (-100.0, 100.0)),
    )
    assert scale_features(Prepared(buildings=[b]), spec()).buildings[0].roof is None


def test_supported_part_starts_at_its_min_height():
    lower = building(box(-100, -100, 100, 100), height_m=40.0, eaves_m=40.0, ridge_m=40.0, outline_id="way/7")
    upper = building(
        box(-50, -50, 50, 50), height_m=80.0, eaves_m=80.0, ridge_m=80.0, min_height_m=40.0, is_part=True, outline_id="way/7"
    )
    out = scale_features(Prepared(buildings=[lower, upper]), spec())
    assert out.buildings[1].z0_mm == pytest.approx(6.0)  # 40 m x 0.15
    assert out.buildings[1].height_mm == pytest.approx(12.0)


def test_floating_part_is_extended_down_to_the_plate():
    # Nothing of the same outline stands under it, so min_height is ignored (spec §8).
    lonely = building(box(-50, -50, 50, 50), height_m=80.0, eaves_m=80.0, ridge_m=80.0, min_height_m=40.0, is_part=True, outline_id="way/7")
    out = scale_features(Prepared(buildings=[lonely]), spec())
    assert out.buildings[0].z0_mm == 0.0


def test_part_next_to_its_sibling_is_not_supported_by_it():
    # Same outline, but the two footprints do not overlap, so the upper one would float.
    lower = building(box(-100, -100, -10, 100), height_m=40.0, eaves_m=40.0, ridge_m=40.0, outline_id="way/7")
    beside = building(box(10, -50, 100, 50), height_m=80.0, eaves_m=80.0, ridge_m=80.0, min_height_m=40.0, is_part=True, outline_id="way/7")
    out = scale_features(Prepared(buildings=[lower, beside]), spec())
    assert out.buildings[1].z0_mm == 0.0


def test_part_whose_base_is_above_its_own_top_falls_back_to_the_plate():
    lower = building(box(-100, -100, 100, 100), height_m=90.0, eaves_m=90.0, ridge_m=90.0, outline_id="way/7")
    broken = building(box(-50, -50, 50, 50), height_m=20.0, eaves_m=20.0, ridge_m=20.0, min_height_m=60.0, is_part=True, outline_id="way/7")
    out = scale_features(Prepared(buildings=[lower, broken]), spec())
    assert out.buildings[1].z0_mm == 0.0


def test_part_whose_base_equals_its_own_top_falls_back_to_the_plate():
    # The guard is >=, not >: a zero-height body would be an empty prism, not a thin one.
    lower = building(box(-100, -100, 100, 100), height_m=90.0, eaves_m=90.0, ridge_m=90.0, outline_id="way/7")
    flat = building(box(-50, -50, 50, 50), height_m=40.0, eaves_m=40.0, ridge_m=40.0, min_height_m=40.0, is_part=True, outline_id="way/7")
    out = scale_features(Prepared(buildings=[lower, flat]), spec())
    assert out.buildings[1].z0_mm == 0.0
    assert out.buildings[1].height_mm == pytest.approx(6.0)


def test_blocks_are_scaled_like_buildings():
    prepared = Prepared(buildings=[], blocks=[Block(box(-500, -500, 500, 500), 10.0)])
    out = scale_features(prepared, spec())
    assert out.blocks[0].geom.bounds == pytest.approx((-50, -50, 50, 50))
    assert out.blocks[0].height_mm == pytest.approx(1.5)
    assert out.blocks[0].z0_mm == 0.0 and out.blocks[0].roof is None


def test_raw_height_has_no_minimum_but_keeps_the_cap():
    assert raw_height_mm(0.0, spec()) == 0.0
    assert raw_height_mm(2.0, spec()) == pytest.approx(0.3)  # building_height_mm would return 0.8
    assert raw_height_mm(5000.0, spec()) == pytest.approx(100.0)
