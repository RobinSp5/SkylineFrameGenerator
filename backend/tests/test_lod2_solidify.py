import manifold3d as m3d
import pytest
import trimesh
import numpy as np

from skylineframe.lod2.solidify import (
    SIMPLIFY_MIN_VOLUME,
    SOLID_SIMPLIFY_MM,
    faces_of,
    footprint_of,
    height_range,
    simplified,
    to_solid,
)

# A unit cube as six faces, every ring wound counter-clockwise seen from outside — which is how
# an LoD2 model comes off the wire.
CUBE = (
    ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)),  # bottom, outward normal -z
    ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)),  # top, outward normal +z
    ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)),
    ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)),
    ((1, 1, 0), (0, 1, 0), (0, 1, 1), (1, 1, 1)),
    ((0, 1, 0), (0, 0, 0), (0, 0, 1), (0, 1, 1)),
)


def as_trimesh(solid: m3d.Manifold) -> trimesh.Trimesh:
    mesh = solid.to_mesh()
    return trimesh.Trimesh(
        vertices=np.asarray(mesh.vert_properties)[:, :3],
        faces=np.asarray(mesh.tri_verts),
        process=False,
    )


# --- the small, exact cases --------------------------------------------


def test_cube_becomes_a_closed_solid_of_volume_one():
    solid = to_solid(CUBE)
    assert solid is not None
    assert solid.status() == m3d.Error.NoError
    assert solid.volume() == pytest.approx(1.0, abs=1e-6)
    assert as_trimesh(solid).is_watertight
    # The body stands on z = 0: prepare hands it on as an ordinary building.
    assert solid.bounding_box()[2] == pytest.approx(0.0, abs=1e-6)
    assert solid.bounding_box()[5] == pytest.approx(1.0, abs=1e-6)


def test_a_missing_ground_face_is_healed_rather_than_rejected():
    # This is the whole point of spec §5: the ground is cut, not repaired.
    solid = to_solid(CUBE[1:])
    assert solid is not None and solid.volume() == pytest.approx(1.0, abs=1e-6)


def test_footprint_and_height_of_the_cube():
    assert footprint_of(CUBE).area == pytest.approx(1.0, abs=1e-6)
    assert height_range(CUBE) == (0.0, 1.0)


def test_walls_without_a_roof_are_rejected():
    walls = (((0, 0, 0), (1, 0, 0), (1, 0, 10), (0, 0, 10)), ((1, 0, 0), (1, 1, 0), (1, 1, 10), (1, 0, 10)))
    assert to_solid(walls) is None


def test_a_model_whose_only_upward_face_is_the_ground_is_rejected():
    # The roof body then hangs entirely below the ground plane and nothing survives the cut.
    model = (
        ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)),
        ((0, 0, 0), (1, 0, 0), (1, 0, 5), (0, 0, 5)),
        ((1, 0, 0), (1, 1, 0), (1, 1, 5), (1, 0, 5)),
        ((1, 1, 0), (0, 1, 0), (0, 1, 5), (1, 1, 5)),
        ((0, 1, 0), (0, 0, 0), (0, 0, 5), (0, 1, 5)),
    )
    assert to_solid(model) is None


def test_degenerate_rings_are_dropped_and_leave_nothing_behind():
    assert faces_of((((0, 0, 0), (1, 1, 1)),)) == []
    assert faces_of((((0, 0, 0), (0, 0, 0), (0, 0, 0)),)) == []
    assert to_solid((((0, 0, 0), (1, 1, 1)),)) is None
    assert footprint_of(()) is None
    assert height_range(()) is None


def test_faces_of_drops_the_closing_vertex():
    ring = ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 0, 0))
    assert faces_of((ring,)) == [[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0)]]


def test_a_flat_model_has_no_height_and_is_rejected():
    assert to_solid((((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)),)) is None


def test_a_point_that_is_not_a_triple_is_skipped_rather_than_raised_on():
    # A bulk run counts a malformed model as discarded; it must not stop on it (spec §5.5).
    assert faces_of((((0, 0), (1, 0), (1, 1), (0, 1)),)) == []
    assert to_solid((((0, 0), (1, 0), (1, 1)),)) is None


# --- overlapping upward faces (the tents are unioned, not concatenated) -


def test_overlapping_roof_faces_are_not_counted_twice():
    # A 2x2x2 m building with a 0.5x0.5 m dormer 1 m above its roof: the dormer's top face and
    # the main roof overlap in plan. Concatenating the tents measured 8.75 m³ — the dormer's
    # column was counted on top of the main body instead of inside it.
    dormer = (
        ((0, 0, 2), (2, 0, 2), (2, 2, 2), (0, 2, 2)),
        ((0.5, 0.5, 3), (1.0, 0.5, 3), (1.0, 1.0, 3), (0.5, 1.0, 3)),
        ((0, 0, 0), (2, 0, 0), (2, 0, 2), (0, 0, 2)),
        ((2, 0, 0), (2, 2, 0), (2, 2, 2), (2, 0, 2)),
        ((2, 2, 0), (0, 2, 0), (0, 2, 2), (2, 2, 2)),
        ((0, 2, 0), (0, 0, 0), (0, 0, 2), (0, 2, 2)),
    )
    solid = to_solid(dormer)
    assert solid.volume() == pytest.approx(8.25, abs=1e-3)  # 2*2*2 plus 0.5*0.5*1
    assert solid.genus() == 0


def test_a_duplicated_roof_face_does_not_double_the_volume():
    # Measured with concatenated tents: 1.999999 for a unit cube.
    assert to_solid(CUBE + (CUBE[1],)).volume() == pytest.approx(1.0, abs=1e-5)


# --- the simplify guard (spec §5) --------------------------------------


def test_simplify_keeps_a_result_that_holds_its_volume():
    solid = to_solid(CUBE).scale((10.0, 10.0, 10.0))
    reduced = simplified(solid, SOLID_SIMPLIFY_MM)
    assert reduced.volume() >= SIMPLIFY_MIN_VOLUME * solid.volume()
    assert reduced.num_tri() <= solid.num_tri()


def test_simplify_refuses_a_tolerance_that_dissolves_the_body():
    # Measured: simplify(4.5) turns a 12-triangle solid into 0 triangles. Without the guard the
    # building would silently vanish from the model.
    solid = m3d.Manifold.cube((3.0, 3.0, 1.0))
    assert solid.simplify(4.5).is_empty()
    assert simplified(solid, 4.5) is solid


def test_the_simplify_guard_holds_on_a_recorded_building(lod2_local):
    # The Schirn at the skyline preset: 100 mm on 1500 m, z exaggeration 1.5. Measured: 776
    # triangles scaled, fewer after simplify, and the volume holds to well inside the guard.
    s = 100.0 / 1500.0
    solid = to_solid(lod2_local[0].surfaces).scale((s, s, s * 1.5))
    reduced = simplified(solid, SOLID_SIMPLIFY_MM)
    assert reduced.num_tri() < solid.num_tri()
    assert reduced.volume() == pytest.approx(solid.volume(), rel=0.05)
    assert reduced.volume() >= SIMPLIFY_MIN_VOLUME * solid.volume()
    # The outline stays where it was: the body still sits on its footprint and under the plate cap.
    assert reduced.bounding_box() == pytest.approx(solid.bounding_box(), abs=SOLID_SIMPLIFY_MM)


def test_simplify_is_a_no_op_for_a_non_positive_tolerance():
    solid = m3d.Manifold.cube((3.0, 3.0, 1.0))
    assert simplified(solid, 0.0) is solid


# --- the three recorded buildings --------------------------------------


@pytest.mark.parametrize(
    "index,name,area_m2,height_m,volume_m3,tris",
    [
        (0, "(1:Kulturschirn)", 1014.0, 24.21, 20995.0, 776),
        (1, "(1:Paulskirche)", 1324.0, 56.51, 40709.0, 1120),
        (2, None, 50.0, 3.84, 194.0, 48),
    ],
)
def test_recorded_buildings_become_watertight_solids(
    lod2_local, index, name, area_m2, height_m, volume_m3, tris
):
    building = lod2_local[index]
    assert building.name == name
    footprint = footprint_of(building.surfaces)
    span = height_range(building.surfaces)
    assert footprint.area == pytest.approx(area_m2, rel=0.02)
    assert span[1] - span[0] == pytest.approx(height_m, abs=0.1)

    solid = to_solid(building.surfaces)
    assert solid is not None and solid.status() == m3d.Error.NoError
    mesh = as_trimesh(solid)
    assert mesh.is_watertight
    # Watertight alone is also true of a heap of disconnected shells — a concatenation of the
    # roof tents measured genus -81 over 82 shells here and was still "watertight". One body.
    assert mesh.body_count == 1
    assert solid.genus() == 0
    assert solid.bounding_box()[2] == pytest.approx(0.0, abs=1e-3)
    assert solid.bounding_box()[5] == pytest.approx(height_m, abs=0.1)
    # The body is the cut of a prism with the roof, so it never spreads beyond its footprint.
    assert solid.volume() <= footprint.area * (span[1] - span[0]) * 1.001
    # ... and a lower bound too: an inflated volume is exactly what the concatenated tents gave.
    assert solid.volume() == pytest.approx(volume_m3, rel=0.02)
    assert solid.num_tri() == pytest.approx(tris, abs=25)


def test_a_recorded_building_stays_inside_the_triangle_budget(lod2_local):
    # Spec §5: an LoD2 body costs one to two orders of magnitude more than a prism with a roof.
    # The Paulskirche is the worst case in the fixture with 217 faces: 1120 triangles unioned,
    # where a concatenation of the tents kept 1622 of them on coincident interior walls.
    solid = to_solid(lod2_local[1].surfaces)
    assert 900 < solid.num_tri() < 2000


# The ground under a 2x2 m building, wound clockwise seen from above (outward normal -z).
GROUND_2X2 = ((0, 0, 0), (0, 2, 0), (2, 2, 0), (2, 0, 0))


def test_a_concave_roof_face_does_not_spill_over_a_lower_wing():
    # An L-shaped main roof at 2 m whose ring starts next to the inner corner, and a 1 m wing in
    # the notch of the L: the shape a fan from the first vertex gets wrong. manifold3d happened to
    # resolve this small case even with fanned tents, so it guards the prism cut rather than
    # reproducing the bug; the recorded Darmstadt building below does reproduce it.
    l_roof = ((2, 1, 2), (1, 1, 2), (1, 2, 2), (0, 2, 2), (0, 0, 2), (2, 0, 2))
    wing_roof = ((1, 1, 1), (2, 1, 1), (2, 2, 1), (1, 2, 1))
    solid = to_solid((l_roof, wing_roof, GROUND_2X2))
    assert solid.volume() == pytest.approx(3 * 2 + 1 * 1, abs=1e-3)
    # And nothing stands above the wing: a slice at 1.5 m is exactly the L.
    assert solid.slice(1.5).area() == pytest.approx(3.0, abs=1e-3)


def test_a_sloped_concave_roof_face_keeps_its_plane():
    # The same L as a shed roof rising from 1 m at y = 0 to 3 m at y = 2, over a flat 0.5 m wing.
    def z(y):
        return 1 + y

    l_roof = tuple((x, y, z(y)) for x, y in ((2, 1), (1, 1), (1, 2), (0, 2), (0, 0), (2, 0)))
    wing_roof = ((1, 1, 0.5), (2, 1, 0.5), (2, 2, 0.5), (1, 2, 0.5))
    solid = to_solid((l_roof, wing_roof, GROUND_2X2))
    # L = 2x1 strip (mean height 1.5) + 1x1 square at y 1..2 (mean height 2.5); wing 1x1 at 0.5.
    assert solid.volume() == pytest.approx(2 * 1.5 + 1 * 2.5 + 0.5, abs=1e-3)


def test_no_recorded_building_grows_wider_upwards(lod2_local):
    # A LoD2 body is the space under its roof faces, so a horizontal cut can only shrink with
    # height. Fanned tents broke that on real concave roofs: a Darmstadt building grew by 11 m²
    # at 3.8 m, which Bambu Studio reports as floating regions (solidify._tent).
    for raw in lod2_local:
        body = to_solid(raw.surfaces)
        if body is None:
            continue
        top = body.bounding_box()[5]
        below = body.slice(0.05)
        for z in np.arange(0.3, top, 0.25):
            cut = body.slice(float(z))
            grown = (cut - below.offset(0.02, m3d.JoinType.Miter)).area()
            assert grown < 1e-3, f"{raw.osm_id} grows by {grown:.3f} m² at {z:.2f} m"
            below = cut


def test_a_recorded_concave_building_does_not_grow_wider_upwards():
    # The Darmstadt building that made Bambu Studio report floating regions: with fanned tents it
    # grew by 11 m² at 3.8 m and lost 1 256 m³; cut from prisms it only ever narrows upwards.
    import json
    from pathlib import Path

    data = json.loads((Path(__file__).parent / "fixtures" / "lod2_darmstadt_concave.json").read_text())
    surfaces = tuple(tuple(tuple(p) for p in ring) for ring in data["surfaces"])
    body = to_solid(surfaces)
    assert body is not None
    below = body.slice(0.05)
    for z in np.arange(0.3, body.bounding_box()[5], 0.25):
        cut = body.slice(float(z))
        assert (cut - below.offset(0.02, m3d.JoinType.Miter)).area() < 1e-3, f"grows at {z:.2f} m"
        below = cut
