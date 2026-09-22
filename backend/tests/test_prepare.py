import pytest
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from skylineframe.features import Building, Features, Road, Water
from skylineframe.prepare import SIMPLIFY_TOLERANCE_MM, polygons_of, prepare
from skylineframe.spec import FrameSpec, Mode


def spec(**kw) -> FrameSpec:
    # scale = 0.1 mm per metre
    return FrameSpec(center_lat=50, center_lon=8, side_m=1000, plate_size_mm=100, **kw)


def bld(geom, h=10.0) -> Building:
    return Building(geom, h)


def test_polygons_of_repairs_bowtie():
    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])
    parts = polygons_of(bowtie)
    assert len(parts) == 2
    assert all(p.is_valid and p.area > 0 for p in parts)


def test_polygons_of_drops_lines_and_empty():
    assert polygons_of(LineString([(0, 0), (1, 1)])) == []
    assert polygons_of(Polygon()) == []
    assert polygons_of(None) == []


def test_building_crossing_edge_is_clipped():
    feats = Features(buildings=[bld(box(400, -50, 600, 50))])
    out = prepare(feats, spec())
    assert len(out.buildings) == 1
    assert out.buildings[0].geom.bounds == pytest.approx((400, -50, 500, 50))
    assert out.buildings[0].height_m == 10.0


def test_building_outside_square_is_dropped():
    out = prepare(Features(buildings=[bld(box(600, 600, 700, 700))]), spec())
    assert out.buildings == []


def test_tiny_footprint_is_dropped():
    # 1 mm² at scale 0.1 => 100 m² threshold
    feats = Features(buildings=[bld(box(0, 0, 5, 5)), bld(box(20, 20, 40, 40))])
    out = prepare(feats, spec())
    assert len(out.buildings) == 1
    assert out.buildings[0].geom.area == pytest.approx(400)


def test_sliver_footprint_is_dropped():
    # 2 m x 300 m wall: area 600 m² passes the area filter but is 0.2 mm wide at scale 0.1 => unprintable
    feats = Features(buildings=[bld(box(0, 0, 2, 300)), bld(box(20, 20, 40, 40))])
    out = prepare(feats, spec())
    assert len(out.buildings) == 1
    assert out.buildings[0].geom.bounds == pytest.approx((20, 20, 40, 40))


def test_multipolygon_building_is_split():
    mp = unary_union([box(0, 0, 20, 20), box(50, 50, 70, 70)])
    out = prepare(Features(buildings=[bld(mp, 7.0)]), spec())
    assert len(out.buildings) == 2
    assert {b.height_m for b in out.buildings} == {7.0}


def test_simplified_footprint_is_revalidated():
    # Simplification runs after the repair step, so its own output must be re-checked:
    # courtyard rings that nearly touch the shell, a hole ring thinner than the tolerance,
    # and a near-degenerate spike are the shapes where simplify can return junk.
    courtyard = Polygon([(0, 0), (60, 0), (60, 60), (0, 60)], [[(5, 5), (55, 5), (55, 59.6), (5, 59.6)]])
    thin_slot = Polygon([(100, 0), (160, 0), (160, 60), (100, 60)], [[(110, 10), (150, 10), (150, 10.2), (110, 10.2)]])
    spike = Polygon([(200, 0), (300, 0.3), (300, 20), (200, 20)])
    feats = Features(buildings=[bld(courtyard), bld(thin_slot), bld(spike)])
    out = prepare(feats, spec())
    assert out.buildings
    for b in out.buildings:
        assert isinstance(b.geom, Polygon)
        assert b.geom.is_valid
        assert not b.geom.is_empty
        assert b.geom.area > 0


def test_simple_mode_ignores_roads_and_water():
    feats = Features(
        buildings=[bld(box(0, 0, 20, 20))],
        roads=[Road(LineString([(-100, 0), (100, 0)]), "residential")],
        water=[Water(box(-100, -100, -50, -50))],
    )
    out = prepare(feats, spec(mode=Mode.simple))
    assert out.roads == [] and out.water == []


def test_road_is_buffered_to_class_width():
    # residential = 1.0 mm => 10 m wide in the city => ±5 m
    feats = Features(
        buildings=[bld(box(200, 200, 220, 220))],
        roads=[Road(LineString([(-100, 0), (100, 0)]), "residential")],
    )
    out = prepare(feats, spec(mode=Mode.full))
    area = unary_union(out.roads)
    assert area.bounds == pytest.approx((-100, -5, 100, 5), abs=0.01)


def test_road_is_clipped_to_square():
    feats = Features(
        buildings=[bld(box(200, 200, 220, 220))],
        roads=[Road(LineString([(-900, 0), (900, 0)]), "primary")],
    )
    out = prepare(feats, spec(mode=Mode.full))
    assert unary_union(out.roads).bounds[0] == pytest.approx(-500)
    assert unary_union(out.roads).bounds[2] == pytest.approx(500)


def test_road_is_cut_out_under_building():
    feats = Features(
        buildings=[bld(box(-10, -10, 10, 10))],
        roads=[Road(LineString([(-100, 0), (100, 0)]), "residential")],
    )
    out = prepare(feats, spec(mode=Mode.full))
    overlap = unary_union(out.roads).intersection(box(-10, -10, 10, 10)).area
    assert overlap == pytest.approx(0, abs=1e-6)


def test_road_pockets_touching_at_a_point_are_welded():
    # Two residential segments (1.0 mm => 10 m wide) whose buffers meet in the single point
    # (0, 5). Extruded as two prisms that would leave a zero-thickness plate wall between them,
    # so prepare welds them into one pocket.
    feats = Features(
        buildings=[bld(box(200, 200, 220, 220))],
        roads=[
            Road(LineString([(-100, 0), (0, 0)]), "residential"),
            Road(LineString([(0, 10), (100, 10)]), "residential"),
        ],
    )
    out = prepare(feats, spec(mode=Mode.full))
    assert len(out.roads) == 1


def test_recesses_keep_a_hairline_clearance_from_buildings():
    # A recess wall that coincides exactly with a building wall is a zero-thickness plate wall,
    # so pockets stop RECESS_CLEARANCE_MM short of whatever blocks them.
    feats = Features(
        buildings=[bld(box(-10, -10, 10, 10))],
        roads=[Road(LineString([(-100, 0), (100, 0)]), "residential")],
        water=[Water(box(-400, -400, 400, 400))],
    )
    out = prepare(feats, spec(mode=Mode.full))
    clearance_m = SIMPLIFY_TOLERANCE_MM / spec(mode=Mode.full).scale
    building = box(-10, -10, 10, 10)
    roads = unary_union(out.roads)
    water = unary_union(out.water)
    # The weld collapses the chords of its own round joins afterwards, which can eat a fraction
    # of the gap; what matters is that a gap of roughly the nominal size is there at all.
    assert roads.distance(building) == pytest.approx(clearance_m, rel=0.1)
    assert water.distance(building) == pytest.approx(clearance_m, rel=0.1)
    assert water.distance(roads) == pytest.approx(clearance_m, rel=0.1)


def test_water_is_cut_out_under_buildings_and_roads():
    feats = Features(
        buildings=[bld(box(-10, -10, 10, 10))],
        roads=[Road(LineString([(-100, 50), (100, 50)]), "residential")],
        water=[Water(box(-100, -100, 100, 100))],
    )
    out = prepare(feats, spec(mode=Mode.full))
    water = unary_union(out.water)
    assert water.intersection(box(-10, -10, 10, 10)).area == pytest.approx(0, abs=1e-6)
    assert water.intersection(unary_union(out.roads)).area == pytest.approx(0, abs=1e-6)
    # The building blocks 20x20 m and the road a 10 m wide strip, each grown by the 0.5 m
    # (0.05 mm in print space) clearance that keeps recess walls off the walls that bound them.
    # The weld is a shape operation, so the outline is only accurate to its own tolerance.
    assert water.area == pytest.approx(200 * 200 - 21 * 21 - 200 * 11, rel=1e-4)


def test_water_is_clipped_to_square():
    feats = Features(buildings=[bld(box(0, 0, 20, 20))], water=[Water(box(-2000, -2000, 2000, -400))])
    out = prepare(feats, spec(mode=Mode.full))
    # abs: the weld reproduces the clipped outline to its own tolerance, not to the last bit.
    assert unary_union(out.water).bounds == pytest.approx((-500, -500, 500, -400), abs=0.01)
