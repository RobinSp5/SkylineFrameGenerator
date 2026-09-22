import pytest
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from skylineframe.features import Building, Features, Road, RoofSpec, Water
from skylineframe.prepare import (
    SIMPLIFY_TOLERANCE_MM,
    assign_parts,
    default_roof_height_m,
    minimum_rect,
    polygons_of,
    prepare,
    weighted_percentile,
)
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


def test_tiny_footprint_reaches_the_model_through_its_block():
    # 0.25 mm² at scale 0.1 => 25 m², and the 0.8 mm feature width => 8 m. The 5x5 shed is
    # neither, so it is no longer its own solid — but it is 1 m away from the house, so the
    # close (radius 4 m) welds both into one block and none of its area is lost (spec §6.5).
    feats = Features(buildings=[bld(box(0, 0, 10, 10)), bld(box(11, 0, 16, 5))])
    out = prepare(feats, spec())
    assert len(out.buildings) == 1
    assert out.buildings[0].geom.area == pytest.approx(100)
    assert len(out.blocks) == 1
    assert out.blocks[0].geom.area > 125
    # abs: the chord simplify of the close shaves ~0.05 m² off the block corners, so the
    # coverage is 1.0 up to that tolerance and never exactly 1.0.
    assert out.footprint_coverage == pytest.approx(1.0, abs=1e-3)


def test_sliver_footprint_is_dropped():
    # 2 m x 300 m wall: area 600 m² passes the area filter but is 0.2 mm wide at scale 0.1 =>
    # unprintable, and its block (the wall alone) is unprintable too, so it disappears.
    feats = Features(buildings=[bld(box(0, 0, 2, 300)), bld(box(20, 20, 40, 40))])
    out = prepare(feats, spec())
    assert len(out.buildings) == 1
    assert out.buildings[0].geom.bounds == pytest.approx((20, 20, 40, 40))
    # abs: the close reproduces the outline only to the tolerance of its own chord simplify.
    assert [b.geom.bounds for b in out.blocks] == [pytest.approx((20, 20, 40, 40), abs=0.01)]


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


# --- heights ------------------------------------------------------------


def test_missing_height_is_estimated_by_type():
    feats = Features(buildings=[Building(box(0, 0, 20, 20), height_m=0.0, kind="church")])
    out = prepare(feats, spec())
    assert out.buildings[0].height_m == 18.0
    assert out.buildings[0].eaves_m == 18.0


def test_missing_height_falls_back_to_the_area_rule():
    # kind "yes" is not in the type table; 20 x 20 = 400 m² lands in the "< 1500" row => 12 m.
    feats = Features(buildings=[Building(box(0, 0, 20, 20), height_m=0.0, kind="yes")])
    out = prepare(feats, spec())
    assert out.buildings[0].height_m == 12.0


def test_tagged_height_is_kept():
    feats = Features(buildings=[Building(box(0, 0, 20, 20), height_m=9.0, kind="yes")])
    out = prepare(feats, spec())
    assert out.buildings[0].height_m == 9.0


# --- building parts -----------------------------------------------------


def outline(geom, height=20.0, osm_id="way/1") -> Building:
    return Building(geom, height_m=height, height_is_top=True, osm_id=osm_id, kind="yes")


def part(geom, height=0.0, osm_id="way/2", min_height=0.0, kind="yes") -> Building:
    return Building(geom, height_m=height, height_is_top=height > 0, osm_id=osm_id, is_part=True, min_height_m=min_height, kind=kind)


def test_part_inside_outline_replaces_it_together_with_the_remainder():
    feats = Features(buildings=[outline(box(0, 0, 40, 40)), part(box(10, 10, 30, 30), height=30.0, min_height=10.0)])
    out = prepare(feats, spec())
    by_area = sorted(out.buildings, key=lambda b: b.geom.area)
    assert [round(b.geom.area) for b in by_area] == [400, 1200]  # the part and the frame around it
    p, remainder = by_area
    assert p.is_part is True and p.outline_id == "way/1" and p.min_height_m == 10.0 and p.height_m == 30.0
    assert remainder.is_part is False and remainder.outline_id == "way/1" and remainder.height_m == 20.0
    assert remainder.geom.interiors  # the frame keeps the hole where the part stands


def test_part_without_height_inherits_the_outline_height():
    feats = Features(buildings=[outline(box(0, 0, 40, 40), height=25.0), part(box(10, 10, 30, 30))])
    out = prepare(feats, spec())
    p = next(b for b in out.buildings if b.is_part)
    assert p.height_m == 25.0
    assert p.eaves_m == 25.0


def test_part_without_outline_is_treated_like_a_building():
    # An orphan part is an ordinary building, so it gets the height estimate and not the bare
    # spec default. 21 x 21 = 441 m² sits clear of the 400 m² boundary of the area rule => 12 m.
    feats = Features(buildings=[part(box(0, 0, 21, 21))])
    out = prepare(feats, spec())
    assert len(out.buildings) == 1
    p = out.buildings[0]
    assert p.outline_id is None
    assert p.height_m == 12.0


def test_part_without_outline_is_estimated_from_its_own_type():
    # The type table wins over the area rule for an orphan part too.
    out = prepare(Features(buildings=[part(box(0, 0, 21, 21), kind="church")]), spec())
    assert len(out.buildings) == 1
    assert out.buildings[0].outline_id is None
    assert out.buildings[0].height_m == 18.0


def test_parts_can_be_switched_off():
    feats = Features(buildings=[outline(box(0, 0, 40, 40)), part(box(10, 10, 30, 30), height=30.0)])
    out = prepare(feats, spec(parts=False))
    assert len(out.buildings) == 1
    assert out.buildings[0].geom.area == pytest.approx(1600)
    assert not out.buildings[0].geom.interiors


def test_assign_parts_keeps_everything_when_there_is_no_part():
    buildings = [outline(box(0, 0, 10, 10)), outline(box(20, 20, 30, 30), osm_id="way/3")]
    out = assign_parts(buildings, 8.0)
    assert [id(b) for b in out] == [id(b) for b in buildings]  # same objects, new list


def test_part_is_assigned_by_an_interior_point_not_the_centroid():
    # A part that covers its whole U-shaped outline. The centroid of that U is POINT (30, 18)
    # — inside the notch and therefore outside both polygons, so a centroid lookup would find
    # no outline at all. representative_point() is inside by construction (spec §6.3).
    u_shape = Polygon([(0, 0), (60, 0), (60, 40), (40, 40), (40, 15), (20, 15), (20, 40), (0, 40)])
    assert not u_shape.contains(u_shape.centroid)
    out = prepare(Features(buildings=[outline(u_shape), part(u_shape, height=30.0)]), spec())
    assert len(out.buildings) == 1  # the part covers the outline, so there is no remainder
    assigned = out.buildings[0]
    assert assigned.is_part is True
    assert assigned.outline_id == "way/1"


# --- blocks -------------------------------------------------------------


def test_three_row_houses_form_one_block():
    # 0.5 m gaps are far below the close radius of MIN_FEATURE_MM / 2 / scale = 4 m.
    feats = Features(
        buildings=[
            bld(box(0, 0, 10, 10), 12.0),
            bld(box(10.5, 0, 20.5, 10), 20.0),
            bld(box(21, 0, 31, 10), 30.0),
        ]
    )
    out = prepare(feats, spec())
    assert len(out.blocks) == 1
    # 31 x 10 = 310 including both gaps, minus the ~1.8 m² the chord simplify shaves off the
    # welded corners (measured 308.20 with shapely 2.1.2). How much a corner loses depends on
    # where the ring the close produced happens to start, so the tolerance is generous.
    assert out.blocks[0].geom.area == pytest.approx(308.2, abs=1.5)
    # equal areas => the 25th percentile is the lowest of the three eaves heights
    assert out.blocks[0].height_m == pytest.approx(12.0)
    assert len(out.buildings) == 3  # each house is printable on its own as well


def test_block_height_has_a_floor():
    # min_building_height_mm / scale = 0.8 / 0.1 = 8 m
    out = prepare(Features(buildings=[bld(box(0, 0, 20, 20), 3.0)]), spec())
    assert out.blocks[0].height_m == pytest.approx(8.0)


def test_weighted_percentile_uses_the_areas():
    # Unweighted the 25th percentile would be 12; weighted, the 12 m footprint carries 20 of
    # 400 m², so the first value whose cumulative area reaches 100 m² is 40.
    assert weighted_percentile([40.0, 12.0], [380.0, 20.0], 0.25) == 40.0
    assert weighted_percentile([40.0, 12.0], [100.0, 300.0], 0.25) == 12.0
    assert weighted_percentile([7.0], [1.0], 0.25) == 7.0


def test_unprintable_block_is_dropped_and_lowers_the_coverage():
    # The 5x5 shed stands alone, so its block is the shed itself: 25 m² is exactly the area
    # threshold, but eroding by 4 m leaves nothing, so the block goes and 25 of 125 m² of
    # building area are missing from the model.
    feats = Features(buildings=[bld(box(0, 0, 10, 10)), bld(box(200, 200, 205, 205))])
    out = prepare(feats, spec())
    assert len(out.blocks) == 1
    assert len(out.buildings) == 1
    assert out.footprint_coverage == pytest.approx(0.8)


def test_a_street_keeps_the_two_rows_in_separate_blocks():
    # Two rows of 12 x 12 m houses, 6 m apart across the street. The close radius is 4 m, so it
    # bridges anything below 8 m: without the road corridors subtracted first, both rows would
    # weld into one block and the road pocket would be cut away by it (spec §6.4).
    houses = [
        box(-30, 3, -18, 15), box(-12, 3, 0, 15),  # north row, 6 m gap between the two houses
        box(-30, -15, -18, -3), box(-12, -15, 0, -3),  # south row
    ]
    feats = Features(
        buildings=[bld(g) for g in houses],
        roads=[Road(LineString([(-100, 0), (100, 0)]), "residential")],  # 1.0 mm => 10 m wide
    )
    out = prepare(feats, spec(mode=Mode.full))

    assert len(out.blocks) == 2
    # The corridor covers y in [-5, 5], so each block starts where the street ends.
    assert sorted(round(b.geom.bounds[1], 2) for b in out.blocks) == [-15.0, 5.0]
    assert sorted(round(b.geom.bounds[3], 2) for b in out.blocks) == [-5.0, 15.0]

    roads = unary_union(out.roads)
    # Buildings keep precedence over roads, blocks too: neither is grooved.
    assert roads.intersection(unary_union([b.geom for b in out.blocks])).area == pytest.approx(0, abs=1e-6)
    assert roads.intersection(unary_union(houses)).area == pytest.approx(0, abs=1e-6)
    # Corridor 200 x 10 = 2000 m², minus the strips the four grown houses and the two grown
    # blocks take out of it (~130 m²); measured 1870.2 with shapely 2.1.2.
    assert roads.area == pytest.approx(1870, rel=5e-3)
    # The middle of the street is untouched: 30 m of x by 4 m of y = 120 m².
    assert roads.intersection(box(-30, -2, 0, 2)).area == pytest.approx(120.0)


def test_block_around_a_single_house_is_not_split_without_roads():
    # Same houses, simple mode: no corridors, so the close welds all four into one block and
    # the road subtraction is provably the only reason for the split above.
    houses = [box(-30, 3, -18, 15), box(-12, 3, 0, 15), box(-30, -15, -18, -3), box(-12, -15, 0, -3)]
    out = prepare(Features(buildings=[bld(g) for g in houses]), spec(mode=Mode.simple))
    assert len(out.blocks) == 1


# --- roofs --------------------------------------------------------------


def roofed(geom, shape="gabled", height=12.0, height_is_top=False, roof_height=0.0) -> Building:
    return Building(
        geom,
        height_m=height,
        height_is_top=height_is_top,
        roof=RoofSpec(shape=shape, height_m=roof_height),
        kind="yes",
    )


def test_minimum_rect_returns_four_corners_without_numpy_warnings():
    # shapely's oriented_envelope divides by zero on axis-aligned input and the suite turns
    # warnings into errors, so this call would fail the whole run if it were not guarded.
    rect = minimum_rect(box(0, 0, 10, 6))
    assert len(rect) == 4
    assert Polygon(rect).area == pytest.approx(60)


def test_rectangular_footprint_keeps_its_roof():
    out = prepare(Features(buildings=[roofed(box(0, 0, 20, 10))]), spec())
    b = out.buildings[0]
    assert b.roof is not None and b.roof.shape == "gabled"
    assert len(b.rect) == 4
    assert b.eaves_m == pytest.approx(12.0)
    # untagged roof height over a 10 m short side: 0.29 * 10 = 2.9, inside the 2..6 m clamp
    assert b.roof.height_m == pytest.approx(2.9)
    assert b.ridge_m == pytest.approx(14.9)


def test_l_shaped_footprint_stays_flat():
    l_shape = Polygon([(0, 0), (20, 0), (20, 10), (10, 10), (10, 20), (0, 20)])  # 300 of 400 m² => 0.75
    out = prepare(Features(buildings=[roofed(l_shape)]), spec())
    b = out.buildings[0]
    assert b.roof is None
    assert b.rect == ()
    assert b.eaves_m == b.ridge_m == 12.0


def test_height_tag_puts_the_roof_below_the_top():
    out = prepare(Features(buildings=[roofed(box(0, 0, 20, 10), height=20.0, height_is_top=True, roof_height=6.0)]), spec())
    b = out.buildings[0]
    assert b.ridge_m == pytest.approx(20.0)
    assert b.eaves_m == pytest.approx(14.0)


def test_roof_never_eats_more_than_half_the_tagged_height():
    out = prepare(Features(buildings=[roofed(box(0, 0, 20, 10), height=20.0, height_is_top=True, roof_height=15.0)]), spec())
    b = out.buildings[0]
    assert b.eaves_m == pytest.approx(10.0)  # 0.5 x height, not 5
    assert b.roof.height_m == pytest.approx(10.0)
    assert b.ridge_m == pytest.approx(20.0)


@pytest.mark.parametrize(
    "shape,short,expected",
    [
        ("gabled", 6.0, 2.0),  # 0.29 * 6 = 1.74 -> clamped to 2
        ("gabled", 20.0, 5.8),
        ("gabled", 30.0, 6.0),  # 8.7 -> clamped to 6
        ("dome", 10.0, 5.0),
        ("round", 8.0, 4.0),
    ],
)
def test_default_roof_height_m(shape, short, expected):
    assert default_roof_height_m(shape, short) == pytest.approx(expected)


def test_roofs_can_be_switched_off():
    out = prepare(Features(buildings=[roofed(box(0, 0, 20, 10))]), spec(roofs=False))
    b = out.buildings[0]
    assert b.roof is None and b.ridge_m == b.eaves_m == 12.0


def test_roof_direction_is_rotated_into_the_local_frame():
    # The world is rotated by -rotation_deg to align the square, so a compass bearing in the
    # local frame is the tagged bearing minus rotation_deg.
    feats = Features(buildings=[Building(box(0, 0, 20, 10), height_m=12.0, roof=RoofSpec("gabled", 3.0, 90.0), kind="yes")])
    out = prepare(feats, spec(rotation_deg=30))
    assert out.buildings[0].roof.direction_deg == pytest.approx(60.0)
