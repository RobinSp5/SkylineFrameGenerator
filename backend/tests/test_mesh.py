import pytest
from shapely.geometry import Polygon, box

from skylineframe.errors import MeshError
from skylineframe.mesh import build_meshes, to_trimesh
from skylineframe.scale import Prism, Scaled, ScaledRoof
from skylineframe.spec import FrameSpec, Mode

PLATE_VOLUME = 100 * 100 * 3


def spec(**kw) -> FrameSpec:
    return FrameSpec(center_lat=50, center_lon=8, side_m=1000, plate_size_mm=100, plate_thickness_mm=3.0, mode=Mode.full, **kw)


def test_single_building_adds_its_volume_to_plate():
    ms = build_meshes(Scaled(buildings=[Prism(box(-5, -5, 5, 5), 10.0)]), spec())
    assert ms.single.volume() == pytest.approx(PLATE_VOLUME + 100 * 10, rel=1e-6)
    # the exported part sits flush on the plate (no overlap with `base` in the 3MF)
    assert ms.buildings.volume() == pytest.approx(100 * 10, rel=1e-6)
    assert ms.buildings.bounding_box()[2] == pytest.approx(0.0)
    assert ms.water is None and ms.roads is None
    assert list(ms.parts()) == ["base", "buildings"]


def test_l_shaped_building():
    l_shape = Polygon([(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)])  # area 64
    ms = build_meshes(Scaled(buildings=[Prism(l_shape, 3.0)]), spec())
    assert ms.single.volume() == pytest.approx(PLATE_VOLUME + 64 * 3, rel=1e-6)


def test_building_flush_with_plate_edge():
    ms = build_meshes(Scaled(buildings=[Prism(box(40, -50, 50, -40), 5.0)]), spec())
    assert ms.single.volume() == pytest.approx(PLATE_VOLUME + 100 * 5, rel=1e-6)
    tm = to_trimesh(ms.single)
    assert tm.is_watertight and tm.is_volume
    assert tm.extents[0] == pytest.approx(100, abs=1e-6)


def test_zero_area_footprints_are_skipped():
    degenerate = Polygon([(0, 0), (10, 0), (10, 0), (0, 0)])
    ms = build_meshes(Scaled(buildings=[Prism(degenerate, 3.0), Prism(box(0, 0, 5, 5), 2.0)]), spec())
    assert ms.single.volume() == pytest.approx(PLATE_VOLUME + 25 * 2, rel=1e-6)


def test_building_with_hole():
    ring = Polygon([(-5, -5), (5, -5), (5, 5), (-5, 5)], holes=[[(-1, -1), (1, -1), (1, 1), (-1, 1)]])
    ms = build_meshes(Scaled(buildings=[Prism(ring, 5.0)]), spec())
    assert ms.single.volume() == pytest.approx(PLATE_VOLUME + 96 * 5, rel=1e-6)


def test_overlapping_buildings_do_not_double_count():
    ms = build_meshes(Scaled(buildings=[Prism(box(0, 0, 10, 10), 4.0), Prism(box(5, 0, 15, 10), 8.0)]), spec())
    expected = 100 * 4 + 100 * 8 - 50 * 4  # overlap counted once, at the taller height
    assert ms.single.volume() == pytest.approx(PLATE_VOLUME + expected, rel=1e-6)


def test_single_bounding_box_matches_plate():
    ms = build_meshes(Scaled(buildings=[Prism(box(40, 40, 50, 50), 3.0)]), spec())
    xmin, ymin, zmin, xmax, ymax, zmax = ms.single.bounding_box()  # flat 6-tuple
    assert (xmin, xmax) == pytest.approx((-50, 50))
    assert (ymin, ymax) == pytest.approx((-50, 50))
    assert (zmin, zmax) == pytest.approx((-3.0, 3.0))


def test_road_recess_is_cut_and_inlay_fills_it():
    road = box(-50, -0.5, 50, 0.5)
    ms = build_meshes(Scaled(buildings=[Prism(box(20, 20, 30, 30), 2.0)], roads=[road]), spec())
    assert ms.base.volume() == pytest.approx(PLATE_VOLUME - 100 * 1 * 0.4, rel=1e-6)
    assert ms.roads.volume() == pytest.approx(100 * 1 * 0.4, rel=1e-6)
    assert (ms.base + ms.roads).volume() == pytest.approx(PLATE_VOLUME, rel=1e-6)
    assert ms.single.volume() == pytest.approx(PLATE_VOLUME - 40 + 100 * 2, rel=1e-6)
    assert list(ms.parts()) == ["base", "buildings", "roads"]


def test_water_recess_uses_water_depth():
    water = box(-50, -50, 0, 0)
    ms = build_meshes(Scaled(buildings=[Prism(box(20, 20, 30, 30), 2.0)], water=[water]), spec())
    assert ms.water.volume() == pytest.approx(2500 * 0.6, rel=1e-6)
    assert (ms.base + ms.water).volume() == pytest.approx(PLATE_VOLUME, rel=1e-6)
    assert list(ms.parts()) == ["base", "buildings", "water"]


def test_all_degenerate_recess_polygons_are_treated_as_absent():
    degenerate = Polygon([(0, 0), (10, 0), (10, 0), (0, 0)])
    ms = build_meshes(Scaled(buildings=[Prism(box(0, 0, 5, 5), 2.0)], roads=[degenerate]), spec())
    assert ms.roads is None
    assert list(ms.parts()) == ["base", "buildings"]
    assert ms.base.volume() == pytest.approx(PLATE_VOLUME, rel=1e-6)


def test_degenerate_recess_polygons_are_skipped_next_to_a_real_one():
    degenerate = Polygon([(0, 0), (10, 0), (10, 0), (0, 0)])
    road = box(-50, -0.5, 50, 0.5)
    ms = build_meshes(Scaled(buildings=[Prism(box(20, 20, 30, 30), 2.0)], roads=[degenerate, road]), spec())
    assert ms.roads is not None
    assert ms.roads.volume() == pytest.approx(100 * 1 * 0.4, rel=1e-6)


def test_no_buildings_raises():
    with pytest.raises(MeshError, match="No buildings"):
        build_meshes(Scaled(buildings=[]), spec())


def test_to_trimesh_is_watertight_volume():
    ms = build_meshes(Scaled(buildings=[Prism(box(-5, -5, 5, 5), 10.0)], roads=[box(-50, 10, 50, 11)]), spec())
    tm = to_trimesh(ms.single)
    assert tm.is_watertight and tm.is_volume
    assert tm.volume == pytest.approx(ms.single.volume(), rel=1e-6)


RECT_MM = ((-5.0, -3.0), (5.0, -3.0), (5.0, 3.0), (-5.0, 3.0))


def test_block_and_building_are_unioned():
    scaled = Scaled(
        buildings=[Prism(box(-5, -5, 5, 5), 10.0)],
        blocks=[Prism(box(-10, -10, 10, 10), 4.0)],
    )
    ms = build_meshes(scaled, spec())
    # block 20x20x4 = 1600, building 10x10x10 = 1000, overlap 10x10x4 = 400 counted once
    assert ms.buildings.volume() == pytest.approx(1600 + 1000 - 400, rel=1e-6)
    assert ms.single.volume() == pytest.approx(PLATE_VOLUME + 2200, rel=1e-6)
    tm = to_trimesh(ms.single)
    assert tm.is_watertight and tm.is_volume


def test_block_alone_is_enough_for_a_model():
    ms = build_meshes(Scaled(buildings=[], blocks=[Prism(box(-10, -10, 10, 10), 2.0)]), spec())
    assert ms.buildings.volume() == pytest.approx(400 * 2, rel=1e-6)


def test_part_standing_on_a_lower_part_is_one_body():
    scaled = Scaled(
        buildings=[Prism(box(-10, -10, 10, 10), 5.0), Prism(box(-5, -5, 5, 5), 12.0, z0_mm=5.0)]
    )
    ms = build_meshes(scaled, spec())
    assert ms.buildings.volume() == pytest.approx(400 * 5 + 100 * 7, rel=1e-6)
    tm = to_trimesh(ms.single)
    assert tm.is_watertight and tm.is_volume


def test_roof_volume_is_added_on_top_of_the_body():
    roof = ScaledRoof(rect_mm=RECT_MM, shape="gabled", z_eaves_mm=2.0, z_ridge_mm=5.0)
    ms = build_meshes(Scaled(buildings=[Prism(box(-5, -3, 5, 3), 2.0, roof=roof)]), spec())
    # body 10 x 6 x 2 = 120, gabled roof over the same rectangle at 3 mm = 90.
    # rel=1e-4: the body reaches EPS into the roof, so the union keeps the sliver of body that
    # the sloping roof does not cover — a second-order term of about base length x EPS^2.
    assert ms.buildings.volume() == pytest.approx(120 + 90, rel=1e-4)
    xmin, ymin, zmin, xmax, ymax, zmax = ms.buildings.bounding_box()
    assert zmax == pytest.approx(5.0)
    tm = to_trimesh(ms.single)
    assert tm.is_watertight and tm.is_volume


def test_roof_is_clipped_to_a_footprint_that_is_smaller_than_its_rectangle():
    # The footprint is the left half of the rectangle, so only half the roof survives.
    roof = ScaledRoof(rect_mm=RECT_MM, shape="gabled", z_eaves_mm=2.0, z_ridge_mm=5.0)
    ms = build_meshes(Scaled(buildings=[Prism(box(-5, -3, 0, 3), 2.0, roof=roof)]), spec())
    # rel=1e-4 for the same EPS overlap as in the test above.
    assert ms.buildings.volume() == pytest.approx(5 * 6 * 2 + 45, rel=1e-4)


def test_everything_together_stays_watertight():
    roof = ScaledRoof(rect_mm=RECT_MM, shape="hipped", z_eaves_mm=3.0, z_ridge_mm=6.0)
    scaled = Scaled(
        buildings=[
            Prism(box(-5, -3, 5, 3), 3.0, roof=roof),
            Prism(box(-3, -2, 3, 2), 9.0, z0_mm=3.0),
        ],
        blocks=[Prism(box(-12, -12, 12, 12), 1.5)],
        roads=[box(-50, 20, 50, 21)],
        water=[box(-50, -50, -30, -30)],
    )
    ms = build_meshes(scaled, spec())
    tm = to_trimesh(ms.single)
    assert tm.is_watertight and tm.is_volume
    assert tm.volume == pytest.approx(ms.single.volume(), rel=1e-6)
    assert list(ms.parts()) == ["base", "buildings", "water", "roads"]
