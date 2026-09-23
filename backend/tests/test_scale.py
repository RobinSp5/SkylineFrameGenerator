import pytest
from shapely.geometry import box

from skylineframe.features import Block, Building, RoofSpec
from skylineframe.fetch import parse_overpass
from skylineframe.lod2.solidify import to_solid
from skylineframe.prepare import Prepared, prepare
from skylineframe.project import project_features
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


def test_part_on_a_point_contact_is_not_supported():
    # The sibling below covers 10 % of the part — a ledge, not a foundation. Below
    # SUPPORT_FRACTION the part is grounded instead of balancing on that sliver (spec §8).
    ledge = building(box(-50, 0, 10, 100), height_m=40.0, eaves_m=40.0, ridge_m=40.0, outline_id="way/7")
    tower = building(
        box(0, 0, 100, 100), height_m=80.0, eaves_m=80.0, ridge_m=80.0, min_height_m=40.0, is_part=True, outline_id="way/7"
    )
    out = scale_features(Prepared(buildings=[ledge, tower]), spec())
    assert out.buildings[1].z0_mm == 0.0


def test_part_carried_by_two_siblings_is_lifted():
    # 30 % of the part rests on each of the two bodies below it, so it keeps its min_height.
    west = building(box(-50, 0, 30, 100), height_m=40.0, eaves_m=40.0, ridge_m=40.0, outline_id="way/7")
    east = building(box(70, 0, 150, 100), height_m=40.0, eaves_m=40.0, ridge_m=40.0, outline_id="way/7")
    tower = building(
        box(0, 0, 100, 100), height_m=80.0, eaves_m=80.0, ridge_m=80.0, min_height_m=40.0, is_part=True, outline_id="way/7"
    )
    out = scale_features(Prepared(buildings=[west, east, tower]), spec())
    assert out.buildings[2].z0_mm == pytest.approx(6.0)  # 40 m x 0.15


def test_support_is_measured_on_the_union_of_the_bodies_below():
    # 15 % each, so neither sibling reaches SUPPORT_FRACTION alone; together they carry 30 %.
    west = building(box(-50, 0, 15, 100), height_m=40.0, eaves_m=40.0, ridge_m=40.0, outline_id="way/7")
    east = building(box(85, 0, 150, 100), height_m=40.0, eaves_m=40.0, ridge_m=40.0, outline_id="way/7")
    tower = building(
        box(0, 0, 100, 100), height_m=80.0, eaves_m=80.0, ridge_m=80.0, min_height_m=40.0, is_part=True, outline_id="way/7"
    )
    out = scale_features(Prepared(buildings=[west, east, tower]), spec())
    assert out.buildings[2].z0_mm == pytest.approx(6.0)
    # One of the two alone stays below the threshold, so the union is what makes the difference.
    assert scale_features(Prepared(buildings=[west, tower]), spec()).buildings[1].z0_mm == 0.0


def test_commerzbank_parts_touching_only_at_a_corner_are_grounded(bankenviertel_data, bankenviertel_spec):
    # The two upper Commerzbank parts share ~0.05 mm² of footprint with the bodies below them,
    # which used to be enough to start them in the air (spec §8).
    features = project_features(parse_overpass(bankenviertel_data, bankenviertel_spec), bankenviertel_spec)
    prepared = prepare(features, bankenviertel_spec)
    scaled = scale_features(prepared, bankenviertel_spec)
    floating = {"way/279967653", "way/279967656"}
    z0 = {b.osm_id: p.z0_mm for b, p in zip(prepared.buildings, scaled.buildings) if b.osm_id in floating}
    assert set(z0) == floating  # both survive prepare, so the assertion below has something to say
    assert list(z0.values()) == [0.0, 0.0]


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


# --- LoD2 bodies --------------------------------------------------------


def lod2_solid_building(x0, y0, x1, y1, height_m) -> Building:
    """A box body in local metres, exactly as prepare hands it on."""
    z0, z1 = 0.0, height_m
    surfaces = (
        ((x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)),
        ((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)),
        ((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)),
        ((x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)),
        ((x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1)),
        ((x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1)),
    )
    b = Building(box(x0, y0, x1, y1), height_m=height_m, height_is_top=True, lod2=True, surfaces=surfaces)
    b.eaves_m = b.ridge_m = height_m
    b.solid_m = to_solid(surfaces)
    assert b.solid_m is not None
    return b


def test_the_body_is_scaled_in_x_y_by_scale_and_in_z_by_scale_times_exaggeration():
    s = spec(z_exaggeration=2.0)  # scale = 0.1 mm per metre
    prepared = Prepared(buildings=[lod2_solid_building(-10, -10, 10, 10, 30.0)])
    prism = scale_features(prepared, s).buildings[0]
    assert prism.solid_mm is not None
    x0, y0, z0, x1, y1, z1 = prism.solid_mm.bounding_box()
    assert (x0, x1) == pytest.approx((-1.0, 1.0), abs=1e-3)
    assert (y0, y1) == pytest.approx((-1.0, 1.0), abs=1e-3)
    assert z0 == pytest.approx(0.0, abs=1e-3)
    assert z1 == pytest.approx(30.0 * 0.1 * 2.0, abs=1e-3)


def test_the_body_is_capped_at_the_plate_size():
    # 4000 m at scale 0.1 and z 1.5 would be 600 mm on a 100 mm plate — an unprintable spike.
    s = spec()
    prepared = Prepared(buildings=[lod2_solid_building(-10, -10, 10, 10, 4000.0)])
    prism = scale_features(prepared, s).buildings[0]
    assert prism.solid_mm.bounding_box()[5] == pytest.approx(s.plate_size_mm, abs=1e-3)


def test_the_body_is_clipped_to_the_plate_square():
    # The footprint reaches past the edge of the 1000 m square; the plate is exactly 100 mm.
    s = spec()
    prepared = Prepared(buildings=[lod2_solid_building(400, -50, 700, 50, 20.0)])
    prism = scale_features(prepared, s).buildings[0]
    assert prism.solid_mm.bounding_box()[3] <= s.plate_size_mm / 2 + 1e-6


def test_a_body_inside_the_plate_keeps_its_volume():
    s = spec()
    building = lod2_solid_building(-10, -10, 10, 10, 30.0)
    prism = scale_features(Prepared(buildings=[building]), s).buildings[0]
    expected = building.solid_m.scale((s.scale, s.scale, s.scale * s.z_exaggeration))
    # Trimmed to its own footprint and simplified, but nothing of the building is lost.
    assert prism.solid_mm.volume() == pytest.approx(expected.volume(), rel=0.05)
    assert prism.solid_mm.bounding_box() == pytest.approx(expected.bounding_box(), abs=0.01)


def test_the_body_is_trimmed_to_the_simplified_footprint():
    # prepare simplifies the outline by SIMPLIFY_TOLERANCE_MM / scale and the recess clearance is
    # grown by exactly that much, so a wall outside the simplified outline would eat the gap.
    s = spec()
    building = lod2_solid_building(-10, -10, 10, 10, 30.0)
    building.geom = box(-10, -10, 0, 10)  # what _clip would have left of it
    prism = scale_features(Prepared(buildings=[building]), s).buildings[0]
    assert prism.solid_mm.bounding_box()[3] <= 0.0 + 1e-6
    assert prism.solid_mm.volume() == pytest.approx(200 * 30 * s.scale**2 * s.scale * s.z_exaggeration, rel=0.05)


def test_a_body_below_the_printable_minimum_falls_back_to_the_prism():
    # 2 m at scale 0.1 and z 1.5 is 0.3 mm, below min_building_height_mm of 0.8: a real building
    # would print as a bump. The prism path clamps, the body cannot.
    s = spec()
    prism = scale_features(Prepared(buildings=[lod2_solid_building(-10, -10, 10, 10, 2.0)]), s).buildings[0]
    assert prism.solid_mm is None
    assert prism.height_mm == pytest.approx(s.min_building_height_mm)


def test_a_building_without_a_body_still_becomes_a_plain_prism():
    plain = Building(box(0, 0, 20, 20), height_m=10.0)
    plain.eaves_m = plain.ridge_m = 10.0
    prism = scale_features(Prepared(buildings=[plain]), spec()).buildings[0]
    assert prism.solid_mm is None
    assert prism.height_mm == pytest.approx(building_height_mm(10.0, spec()))
