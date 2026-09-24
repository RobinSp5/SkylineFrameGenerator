"""Trees in the model (spec 6 §5): one canopy solid, on the plate or on the relief, and exported."""

import numpy as np
import pytest
import trimesh
from shapely.geometry import box

from skylineframe.export import PART_COLORS, export_all
from skylineframe.mesh import build_meshes, printable, to_trimesh
from skylineframe.scale import Prism, Scaled, scale_features
from skylineframe.spec import FrameSpec, Mode
from skylineframe.terrain.heightfield import Heightfield
from skylineframe.trees.geometry import TREE_CLEARANCE_MM
from skylineframe.trees.model import Tree

SIZE = 100.0
THICKNESS = 3.0
PLATE_VOLUME = SIZE * SIZE * THICKNESS
HOUSE = Prism(box(-5, -5, 5, 5), 10.0)


def spec(**kw) -> FrameSpec:
    return FrameSpec(
        center_lat=50, center_lon=8, side_m=1000, plate_size_mm=SIZE, plate_thickness_mm=THICKNESS, mode=Mode.full, **kw
    )


def cap_volume(crown: float, height: float) -> float:
    a = crown / 2
    return np.pi * height * (3 * a * a + height * height) / 6


def slope(cell: float = 0.5, rise: float = 0.1) -> Heightfield:
    flat = Heightfield.flat(SIZE, cell)
    n = flat.z_mm.shape[0]
    x = flat.origin_mm[0] + np.arange(n) * flat.cell_mm
    return Heightfield(np.tile(rise * (x + SIZE / 2), (n, 1)), flat.cell_mm, flat.origin_mm)


def assert_supported_from_below(man, bottom: float = -THICKNESS) -> None:
    """2.5D: the only faces looking down are the flat bottom of the plate."""
    tm = to_trimesh(man)
    down = tm.face_normals[:, 2] < -1e-6
    assert np.allclose(tm.triangles_center[down][:, 2], bottom, atol=1e-4)


def test_a_tree_becomes_its_own_part_on_the_flat_plate():
    tree = Tree(20.0, 20.0, 4.0, 1.5)
    ms = build_meshes(Scaled(buildings=[HOUSE], trees=[tree]), spec())
    assert list(ms.parts()) == ["base", "buildings", "trees"]
    # A 0.2 mm grid under a 4 mm dome: within a few percent of the exact cap.
    assert ms.trees.volume() == pytest.approx(cap_volume(4.0, 1.5), rel=0.05)
    x0, y0, z0, x1, y1, z1 = ms.trees.bounding_box()
    assert z0 == pytest.approx(0.0, abs=1e-6)  # flush on the plate, like the buildings part
    assert z1 == pytest.approx(1.5, abs=1e-3)
    assert (x0 + x1) / 2 == pytest.approx(20.0, abs=0.05)
    assert x1 - x0 <= 4.0 + 0.6
    assert ms.single.volume() == pytest.approx(PLATE_VOLUME + 1000 + ms.trees.volume(), rel=1e-4)
    assert len(ms.single.decompose()) == 1
    assert_supported_from_below(ms.single)


def test_without_trees_nothing_changes():
    scaled = Scaled(buildings=[HOUSE], roads=[box(-50, 10, 50, 11)])
    ms = build_meshes(scaled, spec())
    assert ms.trees is None
    assert list(ms.parts()) == ["base", "buildings", "roads"]


def test_a_forest_of_thousands_of_trees_is_one_solid():
    rng = np.random.default_rng(6)
    xy = rng.uniform(-45, 45, size=(3000, 2))
    xy = xy[np.max(np.abs(xy), axis=1) > 7]  # keep the house free
    trees = [Tree(float(x), float(y), 1.0, 1.2) for x, y in xy]
    ms = build_meshes(Scaled(buildings=[HOUSE], trees=trees), spec())
    assert len(ms.single.decompose()) == 1
    assert ms.trees.bounding_box()[5] == pytest.approx(1.2, abs=0.01)
    assert_supported_from_below(ms.single)
    printable(ms.single)


def test_trees_never_reach_into_a_road_groove_or_a_building():
    s = spec()
    road = box(-50, 10, 50, 11)
    trees = [Tree(float(x), 11.0 + d, 3.0, 2.0) for x, d in ((-20.0, 0.2), (0.0, 1.0), (20.0, 3.0))]
    trees += [Tree(6.0, 0.0, 4.0, 2.0)]  # next to the house
    raw = Scaled(buildings=[HOUSE], roads=[road])
    scaled = Scaled(buildings=raw.buildings, roads=raw.roads, trees=_fit(trees, raw, s))
    ms = build_meshes(scaled, s)
    assert ms.trees is not None
    groove = box(-50, 10, 50, 11).buffer(0.0)
    from skylineframe.mesh import prism

    column = prism(groove, 20.0, z0=-s.road_depth_mm)
    assert (ms.single ^ column).volume() == pytest.approx(0.0, abs=1e-6)
    assert (ms.trees ^ ms.buildings).volume() == pytest.approx(0.0, abs=1e-6)


def _fit(trees, scaled, s):
    from skylineframe.trees.geometry import fitted_trees

    obstacles = [p.geom for p in scaled.buildings] + list(scaled.roads) + list(scaled.water)
    return fitted_trees(trees, obstacles, s)


def test_scale_features_fits_the_trees_around_what_is_in_the_square():
    from skylineframe.features import Building
    from skylineframe.prepare import Prepared

    s = spec()  # 0.1 mm per metre
    prepared = Prepared(buildings=[Building(geom=box(-50, -50, 50, 50), height_m=10.0)], roads=[box(-500, 100, 500, 110)])
    trees = [
        Tree(0.0, 0.0, 3.0, 2.0),  # on the house: dropped
        Tree(6.0, 0.0, 3.0, 2.0),  # 1 mm from the house: shrunk
        Tree(-20.0, -20.0, 3.0, 2.0),  # free
        Tree(0.0, 10.5, 1.0, 1.0),  # on the road: dropped
    ]
    scaled = scale_features(prepared, s, trees)
    assert [t.x_mm for t in scaled.trees] == [6.0, -20.0]
    assert scaled.trees[0].crown_mm == pytest.approx(2 * (1.0 - TREE_CLEARANCE_MM))
    assert scale_features(prepared, s).trees == []


def test_on_terrain_the_trees_stand_on_the_relief():
    hf = slope()
    tree = Tree(0.0, 20.0, 4.0, 1.5)
    ms = build_meshes(Scaled(buildings=[HOUSE], trees=[tree]), spec(), hf)
    ground = float(hf.sample(0.0, 20.0))  # 5 mm at the centre of the plate
    # The top is the dome plus the relief under it, which rises a little towards the uphill side.
    assert ms.trees.bounding_box()[5] == pytest.approx(ground + 1.5, abs=0.05)
    # The part follows the slope: its lowest point is the downhill foot of the crown.
    assert ms.trees.bounding_box()[2] == pytest.approx(ground - 0.1 * 2.0, abs=0.05)
    # The exported part is cut off at the relief, so it holds the dome and nothing more.
    assert ms.trees.volume() == pytest.approx(cap_volume(4.0, 1.5), rel=0.05)
    assert len(ms.single.decompose()) == 1
    assert_supported_from_below(ms.single)
    printable(ms.single)


def test_export_writes_the_trees_part_in_green(tmp_path):
    ms = build_meshes(Scaled(buildings=[HOUSE], trees=[Tree(20.0, 20.0, 4.0, 1.5)]), spec())
    paths = export_all(ms, spec(), tmp_path)
    assert PART_COLORS["trees"] == (84, 130, 53, 255)
    scene = trimesh.load(paths.threemf, file_type="3mf")
    assert set(scene.geometry) == {"base", "buildings", "trees"}
    glb = trimesh.load(paths.glb, file_type="glb")
    assert tuple(glb.geometry["trees"].visual.face_colors[0]) == (84, 130, 53, 255)
    stl = trimesh.load(paths.stl, file_type="stl")
    assert stl.is_watertight
    assert stl.extents[2] == pytest.approx(13.0, abs=0.01)
