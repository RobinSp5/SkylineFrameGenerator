import pytest
from shapely.geometry import LineString, MultiPolygon, Polygon, box
from shapely.ops import unary_union

from skylineframe.features import Building, Features, Lod2Building, Road, RoofSpec, Water
from skylineframe.prepare import (
    LOD2_DISPLACE_FRACTION,
    SIMPLIFY_TOLERANCE_MM,
    _clip,
    assign_parts,
    default_roof_height_m,
    drop_covered,
    lod2_buildings,
    minimum_rect,
    polygons_of,
    prepare,
    weighted_percentile,
)
from skylineframe.project import square_local
from skylineframe.scale import building_height_mm
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
    # The block is the house plus the BLOCK_HAIR_MM the close leaves it (0.02 mm => 0.2 m at
    # this scale); abs: the close reproduces the outline only to its own chord simplify.
    assert [b.geom.bounds for b in out.blocks] == [pytest.approx((19.8, 19.8, 40.2, 40.2), abs=0.1)]


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
    # This house stands entirely inside the water surface, so its block is cut back to exactly
    # the footprint — the 0.2 m block hair lies over open water and goes with it. What stops
    # the water under the house is therefore 20x20 m and not the 20.4x20.4 m a block on dry
    # land would have, and the road is a 10 m wide strip. Each is then grown by the 0.5 m
    # (0.05 mm in print space) clearance that keeps recess walls off the walls that bound them.
    # The weld is a shape operation, so the outline is only accurate to its own tolerance.
    assert water.area == pytest.approx(200 * 200 - 21 * 21 - 200 * 11, rel=2e-4)


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


def test_missing_height_is_estimated_before_the_clip():
    # 21 x 21 = 441 m² of building, of which 9 x 21 = 189 m² lies inside the square. The area
    # rule is a property of the building, not of the cut-out: 441 m² lands in the "< 1500" row
    # => 12 m, while the clipped 189 m² would have landed in "< 400" => 8 m (spec §6.2).
    feats = Features(buildings=[Building(box(491, 0, 512, 21), height_m=0.0, kind="yes")])
    out = prepare(feats, spec())
    assert out.buildings[0].geom.area == pytest.approx(189)
    assert out.buildings[0].height_m == 12.0


def test_multipolygon_building_gets_one_estimate_for_all_its_pieces():
    # One untagged building with two 15 x 15 m lobes: 450 m² => 12 m. Estimated lobe by lobe
    # each would be 225 m² => 8 m, so both pieces must carry the one estimate of the building.
    mp = unary_union([box(0, 0, 15, 15), box(50, 50, 65, 65)])
    out = prepare(Features(buildings=[Building(mp, height_m=0.0, kind="yes")]), spec())
    assert len(out.buildings) == 2
    assert {b.height_m for b in out.buildings} == {12.0}


def test_prepare_leaves_the_input_buildings_alone():
    # The estimate is filled in place, so prepare works on copies: a caller that reuses its
    # Features (the API keeps one fetch for several specs) must not see filled-in heights.
    original = Building(box(0, 0, 20, 20), height_m=0.0, kind="yes")
    prepare(Features(buildings=[original]), spec())
    assert original.height_m == 0.0 and original.eaves_m == 0.0


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
    # 31 x 10 = 310 including both gaps, plus the ~8 m² the 0.2 m block hair adds around the
    # 82 m of outline, minus the ~1.8 m² the chord simplify shaves off the welded corners
    # (measured 317.80 with shapely 2.1.2). How much a corner loses depends on where the ring
    # the close produced happens to start, so the tolerance is generous.
    assert out.blocks[0].geom.area == pytest.approx(317.8, abs=1.5)
    # equal areas => the 25th percentile is the lowest of the three eaves heights
    assert out.blocks[0].height_m == pytest.approx(12.0)
    assert len(out.buildings) == 3  # each house is printable on its own as well


def test_block_height_is_floored_in_print_space_not_in_metres():
    # prepare reports the plain percentile in metres; the printable minimum is applied once,
    # by scale.building_height_mm, so z_exaggeration cannot act on it twice (spec §6.4).
    out = prepare(Features(buildings=[bld(box(0, 0, 20, 20), 1.0)]), spec())
    assert out.blocks[0].height_m == pytest.approx(1.0)
    assert building_height_mm(out.blocks[0].height_m, spec()) == pytest.approx(spec().min_building_height_mm)


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
    # The corridor covers y in [-5, 5], so each block starts where the street ends — minus the
    # 0.2 m block hair, which the chord simplify rounds back to about 0.19 m.
    assert sorted(b.geom.bounds[1] for b in out.blocks) == pytest.approx([-15.2, 4.8], abs=0.05)
    assert sorted(b.geom.bounds[3] for b in out.blocks) == pytest.approx([-4.8, 15.2], abs=0.05)

    roads = unary_union(out.roads)
    # Buildings keep precedence over roads, blocks too: neither is grooved.
    assert roads.intersection(unary_union([b.geom for b in out.blocks])).area == pytest.approx(0, abs=1e-6)
    assert roads.intersection(unary_union(houses)).area == pytest.approx(0, abs=1e-6)
    # Corridor 200 x 10 = 2000 m², minus the strips the four grown houses and the two grown
    # blocks take out of it (~130 m²); measured 1870.2 with shapely 2.1.2.
    assert roads.area == pytest.approx(1870, rel=5e-3)
    # The middle of the street is untouched: 30 m of x by 4 m of y = 120 m².
    assert roads.intersection(box(-30, -2, 0, 2)).area == pytest.approx(120.0)


def test_a_canal_keeps_the_two_rows_in_separate_blocks():
    # The same shape as the street test with a 10 m canal instead of a road. The close (radius
    # 4 m, bridges anything below 8 m) welds the two rows across the 6 m of open water between
    # their overhangs; cutting `water - footprints` out of the closed area removes that bridge
    # again, because a bridge across a canal is by construction water with nothing on it.
    houses = [
        box(-30, 3, -18, 15), box(-12, 3, 0, 15),  # north bank
        box(-30, -15, -18, -3), box(-12, -15, 0, -3),  # south bank
    ]
    feats = Features(buildings=[bld(g) for g in houses], water=[Water(box(-100, -5, 100, 5))])
    out = prepare(feats, spec(mode=Mode.full))

    assert len(out.blocks) == 2
    # Each block keeps the 2 m its houses overhang the bank — they stand in the water and the
    # block layer carries them — and stops at the last footprint, not at the kerb of the canal.
    assert sorted(b.geom.bounds[1] for b in out.blocks) == pytest.approx([-15.2, 3.0], abs=0.05)
    assert sorted(b.geom.bounds[3] for b in out.blocks) == pytest.approx([-3.0, 15.2], abs=0.05)
    assert len(out.buildings) == 4 and out.footprint_coverage == pytest.approx(1.0, abs=1e-3)

    assert len(out.water) == 1  # one canal, not two pools either side of a block across it
    water = out.water[0]
    assert water.intersection(unary_union([b.geom for b in out.blocks])).area == pytest.approx(0, abs=1e-6)
    assert water.bounds == pytest.approx((-100, -5, 100, 5), abs=0.01)
    # 200 x 10 = 2000 m² of canal inside the square, and what it loses is only what stands in
    # it: the four houses overhang it by 2 m over 12 m each, 96.0 m², grown by the 0.5 m
    # (0.05 mm in print space) recess clearance to 135.3 m². Measured 1865.01 with shapely
    # 2.1.2; the weld reproduces the outline only to its own tolerance.
    assert water.area == pytest.approx(2000 - 135.3, rel=1e-3)


def test_a_house_standing_in_the_water_keeps_its_block():
    # Water is subtracted from the closed block area, not from the footprints that go into it,
    # so a pier or a riverbank house does not disappear from the block layer (spec §6.4). The
    # block stops exactly at the footprint: the 0.2 m block hair lies over open water and is
    # cut away with the rest of the canal.
    feats = Features(buildings=[bld(box(-10, -10, 10, 10))], water=[Water(box(-100, -100, 100, 100))])
    out = prepare(feats, spec(mode=Mode.full))
    assert len(out.blocks) == 1
    assert out.blocks[0].geom.bounds == pytest.approx((-10, -10, 10, 10), abs=0.05)
    assert len(out.buildings) == 1
    assert out.footprint_coverage == pytest.approx(1.0, abs=1e-3)


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


# --- LoD2 ---------------------------------------------------------------


def lod2_box(x0, y0, x1, y1, z0, z1, osm_id="lod2/hessen/B1", closed=True) -> Lod2Building:
    """A box as separate faces in local metres, wound the way an LoD2 model is.

    closed=False leaves the roof off: the footprint and the height are still there, but nothing
    can be cut out of it — that is the rejection path of spec §5.5.
    """
    bottom = ((x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0))
    top = ((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))
    walls = (
        ((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)),
        ((x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)),
        ((x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1)),
        ((x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1)),
    )
    surfaces = (bottom, top, *walls) if closed else (bottom, *walls)
    return Lod2Building(osm_id=osm_id, surfaces=surfaces)


def test_lod2_model_becomes_a_building_with_its_real_height():
    out = prepare(Features(lod2=[lod2_box(-10, -10, 10, 10, 100.0, 130.0)]), spec())
    assert len(out.buildings) == 1
    got = out.buildings[0]
    assert got.lod2 is True
    assert got.geom.area == pytest.approx(400, rel=0.02)
    # The ground is levelled: the height is ridge minus ground, not metres above sea level.
    assert got.height_m == pytest.approx(30.0, abs=0.1)
    assert got.height_is_top is True and got.roof is None
    assert got.eaves_m == got.ridge_m == got.height_m
    assert got.osm_id == "lod2/hessen/B1"
    assert got.solid_m is not None
    assert got.solid_m.volume() == pytest.approx(400 * 30, rel=0.02)
    assert out.lod2_rejected == 0


def test_lod2_buildings_skips_a_model_without_a_usable_footprint():
    # Vertical walls only: every face projects to a line, so there is no ground plan at all.
    wall = Lod2Building(osm_id="lod2/hessen/W", surfaces=(((0, 0, 0), (10, 0, 0), (10, 0, 5), (0, 0, 5)),))
    assert lod2_buildings(Features(lod2=[wall])) == []


def test_an_unclosable_model_keeps_its_prism_and_is_counted():
    out = prepare(Features(lod2=[lod2_box(-10, -10, 10, 10, 100.0, 130.0, closed=False)]), spec())
    assert out.lod2_rejected == 1
    assert len(out.buildings) == 1
    kept = out.buildings[0]
    assert kept.lod2 is True and kept.solid_m is None
    # The run never stops and never leaves a hole (spec §5.5/§9).
    assert kept.height_m == pytest.approx(30.0, abs=0.1)
    assert out.footprint_coverage == pytest.approx(1.0)


def test_an_osm_building_under_a_lod2_footprint_is_dropped():
    feats = Features(
        buildings=[Building(box(-10, -10, 10, 10), height_m=8.0, osm_id="way/1", kind="yes")],
        lod2=[lod2_box(-10, -10, 10, 10, 100.0, 130.0)],
    )
    out = prepare(feats, spec())
    assert [b.osm_id for b in out.buildings] == ["lod2/hessen/B1"]


def test_an_osm_building_that_only_touches_the_lod2_footprint_survives():
    # The OSM footprint is 1000 m² and the 400 m² LoD2 box sits inside it: 40 % covered, below
    # the 50 % threshold of spec §6.3, so it stays.
    assert LOD2_DISPLACE_FRACTION == 0.5
    feats = Features(
        buildings=[Building(box(-10, -10, 40, 10), height_m=8.0, osm_id="way/1", kind="yes")],
        lod2=[lod2_box(-10, -10, 10, 10, 100.0, 130.0)],
    )
    out = prepare(feats, spec())
    assert {b.osm_id for b in out.buildings} == {"way/1", "lod2/hessen/B1"}


def test_a_building_part_over_a_lod2_building_is_ignored():
    # The setbacks are already in the LoD2 body; a part on top of it would be a second tower
    # inside the first one (spec §6.4).
    feats = Features(
        buildings=[
            Building(box(-10, -10, 10, 10), height_m=20.0, height_is_top=True, osm_id="way/1", kind="yes"),
            Building(box(-6, -6, 6, 6), height_m=40.0, height_is_top=True, osm_id="way/2", is_part=True, kind="yes"),
        ],
        lod2=[lod2_box(-10, -10, 10, 10, 100.0, 130.0)],
    )
    out = prepare(feats, spec())
    assert [b.osm_id for b in out.buildings] == ["lod2/hessen/B1"]
    assert not any(b.is_part for b in out.buildings)


def test_drop_covered_keeps_everything_without_lod2():
    osm = [Building(box(0, 0, 10, 10), height_m=8.0, osm_id="way/1")]
    assert drop_covered(osm, []) == (osm, [])


def test_drop_covered_keeps_the_uncovered_remainder():
    # A 40x30 m OSM building with a 40x20 m LoD2 model over it: 67 % covered, so the LoD2 model
    # takes over — but only the part it actually covers. The 40x10 m the LoD2 stock does not know
    # about is still a building and stays (spec §6.3).
    osm = [
        Building(box(-20, -15, 20, 15), height_m=8.0, osm_id="way/1", kind="yes", roof=RoofSpec("gabled")),
        Building(box(100, 100, 120, 120), height_m=8.0, osm_id="way/2", kind="yes"),
    ]
    kept, displaced = drop_covered(osm, lod2_buildings(Features(lod2=[lod2_box(-20, -15, 20, 5, 100.0, 130.0)])))
    # One remainder, not three: the two outlines describe the same wall to different precision,
    # and the square-micrometre corner triangles their difference leaves are round-off.
    assert [b.osm_id for b in kept] == ["way/1", "way/2"]
    remainder = kept[0]
    assert remainder.geom.bounds == pytest.approx((-20, 5, 20, 15), abs=0.2)
    assert remainder.geom.area == pytest.approx(400, rel=0.05)
    assert remainder.height_m == 8.0
    # Like the remainder of an outline with parts: the roof described the whole building, and on
    # a leftover strip it would be a spike.
    assert remainder.roof is None and remainder.rect == ()
    # Only what the LoD2 model genuinely replaced is displaced; the remainder is not lost.
    assert [g.area for g in displaced] == [pytest.approx(800, rel=0.05)]


def test_drop_covered_splits_a_remainder_that_falls_into_two_pieces():
    # The LoD2 model runs through the middle of the OSM footprint, so the remainder is a
    # MultiPolygon and both ends have to survive it.
    osm = [Building(box(-30, -10, 30, 10), height_m=8.0, osm_id="way/1", kind="yes")]
    kept, _ = drop_covered(osm, lod2_buildings(Features(lod2=[lod2_box(-20, -10, 20, 10, 100.0, 130.0)])))
    assert len(kept) == 2
    assert all(b.osm_id == "way/1" and b.height_m == 8.0 for b in kept)
    assert all(b.geom.geom_type == "Polygon" for b in kept)
    assert all(b.geom.area == pytest.approx(200, rel=0.05) for b in kept)


def test_the_uncovered_remainder_stays_in_the_model():
    # The same 40x30 m probe through the whole pipeline: nothing is lost any more, because the
    # 40x10 m remainder is a printable footprint of its own (spec §6.5).
    feats = Features(
        buildings=[Building(box(-20, -15, 20, 15), height_m=8.0, osm_id="way/1", kind="yes")],
        lod2=[lod2_box(-20, -15, 20, 5, 100.0, 130.0)],
    )
    out = prepare(feats, spec())
    assert {b.osm_id for b in out.buildings} == {"way/1", "lod2/hessen/B1"}
    assert out.footprint_coverage == pytest.approx(1.0, abs=1e-3)


def test_a_lod2_building_that_only_reaches_a_block_is_never_solidified(monkeypatch):
    # 5x5 m: the area passes, but eroding by 4 m leaves nothing, so it is not printable on its
    # own. It stands 1 m from a 20x20 house, so the close (radius 4 m) welds both into one block
    # and none of its area is lost. Solidifying it would be two orders of magnitude of work for
    # geometry that is thrown away (spec §5).
    calls = []
    import skylineframe.prepare as prepare_module

    original = prepare_module.to_solid

    def spy(surfaces):
        calls.append(surfaces)
        return original(surfaces)

    monkeypatch.setattr(prepare_module, "to_solid", spy)
    feats = Features(
        buildings=[Building(box(6, 0, 26, 20), height_m=10.0, osm_id="way/9", kind="yes")],
        lod2=[lod2_box(0, 0, 5, 5, 100.0, 110.0)],
    )
    out = prepare(feats, spec())
    assert calls == []
    assert [b.osm_id for b in out.buildings] == ["way/9"]
    assert all(b.solid_m is None for b in out.buildings)
    # It still reaches the model through its block, exactly like a small OSM footprint: one block
    # around both footprints, and no area lost.
    assert len(out.blocks) == 1
    assert out.blocks[0].geom.area > 425  # 400 m² house + 25 m² shed + the 1 m the close filled
    assert out.footprint_coverage == pytest.approx(1.0)


def test_only_the_largest_clipped_piece_keeps_the_surfaces():
    # A LoD2 footprint cut into two by the square would otherwise be solidified twice; the body
    # is clipped to the plate later and covers both pieces anyway.
    surfaces = lod2_box(0, 0, 10, 10, 0.0, 5.0).surfaces
    building = Building(
        geom=MultiPolygon([box(0, 0, 10, 10), box(20, 20, 25, 25)]),
        height_m=5.0,
        lod2=True,
        surfaces=surfaces,
    )
    pieces = _clip([building], square_local(spec()), 0.0)
    by_area = sorted(pieces, key=lambda b: b.geom.area)
    assert [round(b.geom.area) for b in by_area] == [25, 100]
    assert by_area[0].surfaces == ()
    assert by_area[1].surfaces == surfaces


def test_a_surviving_osm_part_never_shreds_a_lod2_building():
    # The part covers 41 % of the LoD2 footprint, so drop_covered keeps it — but its
    # representative point lands inside the LoD2 outline. Were the LoD2 building treated as the
    # owner, it would be replaced by remainder polygons and each of them would carry a copy of
    # the faces, so the same body would be built again per piece (spec §6.4).
    feats = Features(
        buildings=[
            Building(box(-8, -8, 20, 20), height_m=30.0, height_is_top=True, osm_id="way/2", is_part=True, kind="yes")
        ],
        lod2=[lod2_box(-10, -10, 10, 10, 100.0, 130.0)],
    )
    out = prepare(feats, spec())
    kept = [b for b in out.buildings if b.lod2]
    assert len(kept) == 1
    assert kept[0].geom.area == pytest.approx(400, rel=0.02)
    assert not kept[0].geom.interiors  # no hole punched by the part
    assert kept[0].outline_id is None
    assert kept[0].solid_m is not None


def test_the_flag_is_honoured_even_when_lod2_data_is_handed_in():
    feats = Features(
        buildings=[Building(box(-10, -10, 10, 10), height_m=8.0, osm_id="way/1", kind="yes")],
        lod2=[lod2_box(-10, -10, 10, 10, 100.0, 130.0)],
    )
    out = prepare(feats, spec(lod2=False))
    assert [b.osm_id for b in out.buildings] == ["way/1"]
    assert out.buildings[0].height_m == 8.0


def test_without_lod2_data_nothing_changes():
    feats = Features(buildings=[Building(box(0, 0, 20, 20), height_m=12.0, osm_id="way/1", kind="yes")])
    out = prepare(feats, spec())
    assert [b.osm_id for b in out.buildings] == ["way/1"]
    assert out.lod2_rejected == 0
    assert all(b.solid_m is None and b.surfaces == () for b in out.buildings)
