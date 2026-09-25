import manifold3d as m3d
import pytest
from shapely.geometry import Polygon, box

from skylineframe.errors import MeshError
from skylineframe.mesh import BUILDING_SINK_MM, _bodies, build_meshes, to_trimesh
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


# --- LoD2 bodies --------------------------------------------------------


def lod2_prism(size_mm: float, height_mm: float) -> Prism:
    """A Prism whose geometry is a ready-made body, the way scale hands LoD2 buildings on.

    The body deliberately disagrees with geom and height_mm: it is one and a half times as wide
    and it carries a tower that doubles its height. The prism path can only extrude geom to
    height_mm, so every assertion on this body's volume or bounding box fails unless mesh really
    reads solid_mm.
    """
    base = m3d.Manifold.cube((size_mm * 1.5, size_mm * 1.5, height_mm), center=True).translate(
        (0.0, 0.0, height_mm / 2)
    )
    tower = m3d.Manifold.cube((size_mm / 2, size_mm / 2, height_mm), center=True).translate(
        (0.0, 0.0, height_mm * 1.5)
    )
    return Prism(
        geom=box(-size_mm / 2, -size_mm / 2, size_mm / 2, size_mm / 2),
        height_mm=height_mm,
        solid_mm=base + tower,
    )


# The fixture at (10, 4): a 15 x 15 x 4 base plus a 5 x 5 x 4 tower, reaching z = 8. The prism
# path would build 10 x 10 x 4 = 400 mm³ reaching z = 4 instead.
BODY_VOLUME = 15 * 15 * 4 + 5 * 5 * 4
BODY_BOX = (-7.5, -7.5, 0.0, 7.5, 7.5, 8.0)


def test_a_body_is_used_instead_of_the_extrusion():
    prism = lod2_prism(10.0, 4.0)
    meshes = build_meshes(Scaled(buildings=[prism]), spec())
    # Both the volume and the bounding box are the body's, not the extrusion's.
    assert meshes.buildings.volume() == pytest.approx(BODY_VOLUME, rel=1e-6)
    assert meshes.buildings.bounding_box() == pytest.approx(BODY_BOX, abs=1e-6)
    assert meshes.buildings.volume() == pytest.approx(prism.solid_mm.volume(), rel=1e-6)


def test_the_body_is_sunk_for_the_single_colour_union_only():
    prism = lod2_prism(10.0, 4.0)
    # The exported part stays flush with the plate top so the 3MF parts never overlap; the
    # single-colour union gets the same body sunk by BUILDING_SINK_MM, so the boolean never
    # relies on a pure face contact at z = 0. The displacement itself is the assertion.
    assert _bodies([prism], 0.0)[0].bounding_box()[2] == pytest.approx(0.0, abs=1e-6)
    assert _bodies([prism], BUILDING_SINK_MM)[0].bounding_box()[2] == pytest.approx(-BUILDING_SINK_MM, abs=1e-6)
    meshes = build_meshes(Scaled(buildings=[prism]), spec())
    assert meshes.buildings.bounding_box() == pytest.approx(BODY_BOX, abs=1e-6)
    # The sunk body loses exactly the slab that now sits inside the plate: 15 x 15 x 0.2.
    sunk_into_plate = 15 * 15 * BUILDING_SINK_MM
    assert meshes.single.volume() == pytest.approx(PLATE_VOLUME + BODY_VOLUME - sunk_into_plate, rel=1e-6)
    assert to_trimesh(meshes.single).is_watertight


def test_a_body_and_a_prism_live_side_by_side():
    scaled = Scaled(buildings=[lod2_prism(10.0, 4.0), Prism(box(20, 20, 30, 30), 6.0)])
    meshes = build_meshes(scaled, spec())
    # The body and the prism now export as two separate parts: "buildings" is everything
    # extruded from attributes, "buildings_verified" everything with a real LoD2 body.
    assert to_trimesh(meshes.buildings).is_watertight
    assert to_trimesh(meshes.buildings_verified).is_watertight
    assert meshes.buildings.volume() == pytest.approx(10 * 10 * 6, rel=1e-6)
    assert meshes.buildings_verified.volume() == pytest.approx(BODY_VOLUME, rel=1e-6)
    assert meshes.buildings_verified.bounding_box()[5] == pytest.approx(BODY_BOX[5], abs=1e-6)


def test_parts_gain_buildings_verified_only_when_a_body_and_a_prism_are_both_present():
    mixed = Scaled(
        buildings=[lod2_prism(10.0, 4.0), Prism(box(20, 20, 30, 30), 6.0)],
        roads=[box(-50, 10, 50, 11)],
        water=[box(-50, -50, -20, -20)],
    )
    assert set(build_meshes(mixed, spec()).parts()) == {"base", "buildings", "buildings_verified", "roads", "water"}

    # Same scene minus the LoD2 body: no LoD2 or Overture data reached this model, so the part
    # is absent rather than exported empty.
    plain = Scaled(
        buildings=[Prism(box(20, 20, 30, 30), 6.0)],
        roads=[box(-50, 10, 50, 11)],
        water=[box(-50, -50, -20, -20)],
    )
    ms = build_meshes(plain, spec())
    assert set(ms.parts()) == {"base", "buildings", "roads", "water"}
    assert ms.buildings_verified is None


def test_parts_stay_at_one_buildings_part_when_everything_is_verified():
    # Every printable body in the square is LoD2-verified and there is no sockel: nothing to
    # contrast the split with, so it all prints as the one required "buildings" part.
    ms = build_meshes(Scaled(buildings=[lod2_prism(10.0, 4.0)]), spec())
    assert set(ms.parts()) == {"base", "buildings"}
    assert ms.buildings_verified is None
    assert ms.buildings.volume() == pytest.approx(BODY_VOLUME, rel=1e-6)


def test_print_optimized_thickens_a_spiky_osm_roof():
    # A pyramid roof on a 1 mm tower rises 6 mm to a point: from 3 mm up it is thinner than two
    # nozzle lines. Print-optimized it is widened to 0.8 mm up to its tip and keeps its height;
    # in detail mode it stays the pure pyramid (mesh._roof_bodies).
    from shapely.geometry import box as _box

    from skylineframe.mesh import _roof_bodies

    tower = _box(0, 0, 1, 1)
    rect = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    p = Prism(tower, 2.0, roof=ScaledRoof(rect_mm=rect, shape="pyramidal", z_eaves_mm=2.0, z_ridge_mm=8.0))
    plain = _roof_bodies([p])[0]
    thick = _roof_bodies([p], 0.8)[0]
    assert thick.bounding_box()[5] == pytest.approx(plain.bounding_box()[5], abs=1e-3)
    near_tip = thick.slice(7.5).bounds()
    assert min(near_tip[2] - near_tip[0], near_tip[3] - near_tip[1]) >= 0.8 - 1e-3
    tip = plain.slice(7.5).bounds()
    assert tip[2] - tip[0] < 0.2
    # Nothing grows past the tower's own walls.
    x0, y0, _, x1, y1, _ = thick.bounding_box()
    assert (x0, y0) >= (-1e-3, -1e-3) and (x1, y1) <= (1 + 1e-3, 1 + 1e-3)
