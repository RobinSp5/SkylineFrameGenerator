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
    # The Schirn at the skyline preset: 100 mm on 1500 m, z exaggeration 1.5. Measured before
    # this ran: 842 triangles and 9.331 mm³ scaled, 676 triangles and 9.005 mm³ simplified.
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
    "index,name,area_m2,height_m",
    [
        (0, "(1:Kulturschirn)", 1014.0, 24.21),
        (1, "(1:Paulskirche)", 1324.0, 56.51),
        (2, None, 50.0, 3.84),
    ],
)
def test_recorded_buildings_become_watertight_solids(lod2_local, index, name, area_m2, height_m):
    building = lod2_local[index]
    assert building.name == name
    footprint = footprint_of(building.surfaces)
    span = height_range(building.surfaces)
    assert footprint.area == pytest.approx(area_m2, rel=0.02)
    assert span[1] - span[0] == pytest.approx(height_m, abs=0.1)

    solid = to_solid(building.surfaces)
    assert solid is not None and solid.status() == m3d.Error.NoError
    assert as_trimesh(solid).is_watertight
    assert solid.bounding_box()[2] == pytest.approx(0.0, abs=1e-3)
    assert solid.bounding_box()[5] == pytest.approx(height_m, abs=0.1)
    # The body is the cut of a prism with the roof, so it never spreads beyond its footprint.
    assert solid.volume() <= footprint.area * (span[1] - span[0]) * 1.001


def test_a_recorded_building_stays_inside_the_triangle_budget(lod2_local):
    # Spec §5: an LoD2 body costs one to two orders of magnitude more than a prism with a roof.
    # The Paulskirche is the worst case in the fixture with 217 faces.
    solid = to_solid(lod2_local[1].surfaces)
    assert 1000 < solid.num_tri() < 3000
