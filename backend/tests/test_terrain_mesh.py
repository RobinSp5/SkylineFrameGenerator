"""The terrain half of mesh.py (spec 4b §5): a relief plate and everything standing on it.

Every test injects a synthetic Heightfield; the DEM side is tested on its own.
"""

import manifold3d as m3d
import numpy as np
import pytest
from shapely.geometry import Polygon, box

from skylineframe.export import export_all
from skylineframe.mesh import (
    BUILDING_SINK_MM,
    _footprint_bases,
    build_meshes,
    footprint_base,
    plate,
    terrain_solid,
    to_trimesh,
)
from skylineframe.scale import Prism, Scaled, ScaledRoof
from skylineframe.spec import SOCKEL_MM, FrameSpec, Mode
from skylineframe.terrain.heightfield import Heightfield

SIZE = 100.0
THICKNESS = 3.0
PLATE_VOLUME = SIZE * SIZE * THICKNESS
SLOPE = 0.1  # mm of height per mm of x: 0 mm at the west edge, 10 mm at the east edge


def spec(**kw) -> FrameSpec:
    return FrameSpec(
        center_lat=50, center_lon=8, side_m=1000, plate_size_mm=SIZE, plate_thickness_mm=THICKNESS, mode=Mode.full, **kw
    )


def slope(cell: float = 0.5) -> Heightfield:
    """A plane rising along +x. The grid triangulation reproduces a plane exactly, so every
    volume on it has a closed form."""
    flat = Heightfield.flat(SIZE, cell)
    n = flat.z_mm.shape[0]
    x = flat.origin_mm[0] + np.arange(n) * flat.cell_mm
    return Heightfield(np.tile(SLOPE * (x + SIZE / 2), (n, 1)), flat.cell_mm, flat.origin_mm)


def hill(height: float = 10.0, cell: float = 0.5) -> Heightfield:
    flat = Heightfield.flat(SIZE, cell)
    n = flat.z_mm.shape[0]
    c = flat.origin_mm[0] + np.arange(n) * flat.cell_mm
    x, y = np.meshgrid(c, c)  # rows along y, columns along x, like z_mm
    z = height * np.exp(-((x - 10) ** 2 + (y + 5) ** 2) / (2 * 20.0**2))
    return Heightfield(z - z.min(), flat.cell_mm, flat.origin_mm)


def slope_volume() -> float:
    """The relief plate on `slope()`: the flat plate plus a wedge of mean height 5 mm."""
    return PLATE_VOLUME + SIZE * SIZE * SLOPE * SIZE / 2


def assert_one_watertight_solid(man: m3d.Manifold) -> None:
    assert man.status() == m3d.Error.NoError
    tm = to_trimesh(man)
    assert tm.is_watertight and tm.is_volume
    assert len(man.decompose()) == 1  # nothing floats


# --- the relief plate ------------------------------------------------------------------------


def test_flat_field_gives_exactly_todays_plate():
    solid = terrain_solid(Heightfield.flat(SIZE), THICKNESS)
    assert solid.status() == m3d.Error.NoError
    assert solid.volume() == pytest.approx(plate(spec()).volume(), rel=1e-9)
    assert solid.bounding_box() == pytest.approx(plate(spec()).bounding_box(), abs=1e-6)


def test_relief_plate_follows_the_grid_and_keeps_a_flat_bottom():
    solid = terrain_solid(slope(), THICKNESS)
    assert_one_watertight_solid(solid)
    assert solid.volume() == pytest.approx(slope_volume(), rel=1e-6)
    x0, y0, z0, x1, y1, z1 = solid.bounding_box()
    assert (x0, y0, x1, y1) == pytest.approx((-50, -50, 50, 50), abs=1e-5)
    assert (z0, z1) == pytest.approx((-THICKNESS, SLOPE * SIZE), abs=1e-5)


def test_relief_plate_on_a_non_square_grid_and_a_hill():
    # Rows along y, columns along x: a 3 x 5 grid must not come out transposed.
    hf = Heightfield(np.array([[0.0, 1, 2, 1, 0], [0, 2, 4, 2, 0], [0, 1, 2, 1, 0]]), 25.0, (-50.0, -25.0))
    solid = terrain_solid(hf, 2.0)
    assert_one_watertight_solid(solid)
    assert solid.bounding_box() == pytest.approx((-50, -25, -2, 50, 25, 4), abs=1e-5)
    assert_one_watertight_solid(terrain_solid(hill(), THICKNESS))


# --- flat field and no terrain ---------------------------------------------------------------


def scene() -> Scaled:
    roof = ScaledRoof(((-20, 20), (-10, 20), (-10, 30), (-20, 30)), "gabled", 3.0, 5.0)
    return Scaled(
        buildings=[
            Prism(box(-5, -5, 5, 5), 10.0),
            Prism(box(-2, -2, 2, 2), 14.0, z0_mm=10.0),  # a tower standing on the first body
            Prism(box(-20, 20, -10, 30), 3.0, roof=roof),
        ],
        blocks=[Prism(box(-8, -8, 8, 8), SOCKEL_MM)],
        roads=[box(-50, 10, 50, 11)],
        water=[box(-50, -50, -20, -20)],
    )


def test_flat_field_gives_the_volumes_of_the_flat_path():
    flat = build_meshes(scene(), spec())
    on_flat = build_meshes(scene(), spec(), terrain=Heightfield.flat(SIZE))
    for name in ("base", "buildings", "single", "roads", "water"):
        assert getattr(on_flat, name).volume() == pytest.approx(getattr(flat, name).volume(), rel=1e-6), name
    assert list(on_flat.parts()) == list(flat.parts())


def test_no_terrain_is_the_flat_path_to_the_last_byte():
    # Spec 4b §2: terrain=None must not even pass through the new code.
    before = build_meshes(scene(), spec())
    after = build_meshes(scene(), spec(), terrain=None)
    for name in ("base", "buildings", "single", "roads", "water"):
        a, b = getattr(before, name).to_mesh(), getattr(after, name).to_mesh()
        assert np.array_equal(np.asarray(a.vert_properties), np.asarray(b.vert_properties)), name
        assert np.array_equal(np.asarray(a.tri_verts), np.asarray(b.tri_verts)), name


# --- buildings on a slope --------------------------------------------------------------------


def test_footprint_base_is_the_lowest_terrain_under_the_outline():
    hf = slope()
    assert footprint_base(hf, box(10, -5, 20, 5)) == pytest.approx(SLOPE * 60)
    # A grid node inside the footprint can be lower than every outline vertex: a pit.
    z = np.full((201, 201), 5.0)
    z[100, 100] = 0.0  # the node at (0, 0)
    pit = Heightfield(z, 0.5, (-50.0, -50.0))
    assert footprint_base(pit, box(-3, -3, 3, 3)) == pytest.approx(0.0)
    assert footprint_base(pit, box(10, 10, 13, 13)) == pytest.approx(5.0)


def test_building_on_a_slope_stands_on_its_lowest_point():
    footprint = box(10, -5, 20, 5)  # terrain 6 mm on the west wall, 7 mm on the east wall
    ms = build_meshes(Scaled(buildings=[Prism(footprint, 5.0)]), spec(), terrain=slope())
    z_base = SLOPE * 60
    # The height counts from z_base (spec 4b §5.2) ...
    assert ms.buildings.bounding_box()[5] == pytest.approx(z_base + 5.0, abs=1e-5)
    # ... and the exported part follows the terrain down to its downhill edge without a gap.
    assert ms.buildings.bounding_box()[2] == pytest.approx(z_base, abs=1e-5)
    above_terrain = 100 * (z_base + 5.0 - SLOPE * 65)  # mean terrain under the footprint: 6.5 mm
    assert ms.buildings.volume() == pytest.approx(above_terrain, rel=1e-5)
    assert ms.single.volume() == pytest.approx(slope_volume() + above_terrain, rel=1e-6)
    assert_one_watertight_solid(ms.single)


def test_no_building_floats_on_a_hill():
    hf = hill()
    footprints = [box(x, y, x + 6, y + 4) for x in range(-45, 40, 9) for y in range(-45, 40, 11)]
    ms = build_meshes(Scaled(buildings=[Prism(f, 4.0) for f in footprints]), spec(), terrain=hf)
    for f in footprints:
        xs, ys = np.asarray(f.exterior.coords).T
        # Spec 4b §6: the bottom is at or below the terrain under every outline vertex.
        assert footprint_base(hf, f) <= hf.sample(xs, ys).min() + 1e-12
    # Every building is welded to the relief: one solid, as many shells as the plate alone.
    assert_one_watertight_solid(ms.single)
    # The exported part never overlaps the relief plate.
    assert (ms.base + ms.buildings).volume() == pytest.approx(ms.base.volume() + ms.buildings.volume(), rel=1e-6)


def test_parts_roofs_and_lod2_bodies_move_with_their_footprint():
    hf = slope()
    roof = ScaledRoof(((10, -5), (20, -5), (20, 5), (10, 5)), "gabled", 5.0, 8.0)
    lod2 = m3d.Manifold.cube((4, 4, 6)).translate((-30, 0, 0))  # stands on z = 0 at x -30..-26
    scaled = Scaled(
        buildings=[
            Prism(box(10, -5, 20, 5), 5.0, roof=roof),
            Prism(box(-30, 0, -26, 4), 6.0, solid_mm=lod2),
        ]
    )
    ms = build_meshes(scaled, spec(), terrain=slope())
    body, roofed = sorted(ms.buildings.decompose(), key=lambda m: m.bounding_box()[0])  # west to east
    assert roofed.bounding_box()[5] == pytest.approx(SLOPE * 60 + 8.0, abs=1e-4)  # ridge from z_base
    assert body.bounding_box()[5] == pytest.approx(SLOPE * 20 + 6.0, abs=1e-4)  # LoD2 top from z_base
    assert_one_watertight_solid(ms.single)


def test_a_part_in_the_air_stays_on_the_body_below_it():
    # The tower sits on the uphill half of its base: its own footprint alone would lift it by
    # the slope across the base and leave a gap under it.
    base = box(0, -5, 20, 5)
    tower = box(12, -2, 18, 2)
    scaled = Scaled(buildings=[Prism(base, 4.0), Prism(tower, 10.0, z0_mm=4.0)])
    ms = build_meshes(scaled, spec(), terrain=slope())
    assert_one_watertight_solid(ms.single)
    assert ms.buildings.bounding_box()[5] == pytest.approx(SLOPE * 50 + 10.0, abs=1e-5)



def test_a_part_on_a_raised_part_inherits_the_lowered_base():
    # Review finding: B stands on A and is lowered to A's base; C stands only on B, and used to
    # take B's *own* base, leaving it 0.4 mm above B's roof on this slope (spec 4b §5.2).
    a = Prism(box(0, -5, 10, 5), 6.0)
    b = Prism(box(8, -5, 14, 5), 10.0, z0_mm=3.0)
    c = Prism(box(12, -5, 14, 5), 12.0, z0_mm=8.0)
    bases = _footprint_bases([a, b, c], slope())
    assert bases[1] == pytest.approx(bases[0])
    assert bases[2] == pytest.approx(bases[0])
    ms = build_meshes(Scaled(buildings=[a, b, c]), spec(), terrain=slope())
    assert_one_watertight_solid(ms.single)


# --- sockel, roads and water -----------------------------------------------------------------


def test_sockel_is_a_layer_of_constant_thickness_on_the_surface():
    block = box(-30, -10, 30, 10)
    scaled = Scaled(buildings=[Prism(box(-5, -5, 5, 5), 3.0)], blocks=[Prism(block, SOCKEL_MM)])
    ms = build_meshes(scaled, spec(), terrain=slope())
    house_above_sockel = 100 * (SLOPE * 45 + 3.0 - SLOPE * 50) - 100 * SOCKEL_MM
    assert ms.buildings.volume() == pytest.approx(block.area * SOCKEL_MM + house_above_sockel, rel=1e-5)
    x0, _, z0, x1, _, z1 = ms.buildings.bounding_box()
    assert z0 == pytest.approx(SLOPE * 20, abs=1e-5)  # the sockel starts on the surface at x = -30 ...
    assert (x0, x1) == pytest.approx((-30, 30), abs=1e-5)
    assert_one_watertight_solid(ms.single)


def test_road_depth_is_constant_relative_to_the_surface():
    hf = slope()
    road = box(-50, -0.5, 50, 0.5)
    scaled = Scaled(buildings=[Prism(box(20, 20, 30, 30), 2.0)], roads=[road])
    ms = build_meshes(scaled, spec(), terrain=hf)
    depth = spec().road_depth_mm
    assert ms.roads.volume() == pytest.approx(road.area * depth, rel=1e-6)
    assert ms.base.volume() == pytest.approx(slope_volume() - road.area * depth, rel=1e-6)
    # Every vertex of the inlay lies on the surface or exactly `depth` below it.
    v = np.asarray(ms.roads.to_mesh().vert_properties)[:, :3]
    offset = hf.sample(v[:, 0], v[:, 1]) - v[:, 2]
    assert np.all(np.isclose(offset, 0.0, atol=1e-4) | np.isclose(offset, depth, atol=1e-4))
    for x in (-40.0, -5.0, 25.0, 45.0):  # and the groove floor is there along the whole road
        near = np.abs(v[:, 0] - x) < 1.0
        assert np.min(v[near, 2]) == pytest.approx(hf.sample(x, 0.0) - depth, abs=SLOPE * 1.0 + 1e-4)
    assert (ms.base + ms.roads).volume() == pytest.approx(slope_volume(), rel=1e-6)


def test_water_on_a_hill_uses_water_depth_and_stays_watertight():
    water = box(-50, -50, -20, -20)
    scaled = Scaled(buildings=[Prism(box(20, 20, 30, 30), 2.0)], water=[water], roads=[box(-50, 10, 50, 11)])
    ms = build_meshes(scaled, spec(), terrain=hill())
    assert ms.water.volume() == pytest.approx(water.area * spec().water_depth_mm, rel=1e-5)
    assert_one_watertight_solid(ms.single)
    for part in ms.parts().values():
        assert to_trimesh(part).is_watertight


def test_relief_model_passes_the_export_checks(tmp_path):
    # verify_single only assumes the x/y footprint of the plate and a volume above half of it;
    # a relief plate satisfies both, so the checks need no terrain branch (spec 4b §5.5).
    ms = build_meshes(scene(), spec(), terrain=hill())
    paths = export_all(ms, spec(), tmp_path)
    assert paths.stl.exists()
    assert paths.diagnostics["nonmanifold_edges"] == 0


def test_sink_stays_relative_to_the_footprint_base():
    hf = slope()
    ms = build_meshes(Scaled(buildings=[Prism(box(10, -5, 20, 5), 5.0)]), spec(), terrain=hf)
    # The single union carries the building down to z_base - sink inside the relief; nothing of
    # it may poke out below the plate or change the top.
    assert ms.single.bounding_box()[2] == pytest.approx(-THICKNESS, abs=1e-6)
    assert ms.single.bounding_box()[5] == pytest.approx(max(SLOPE * 60 + 5.0, SLOPE * SIZE), abs=1e-5)
    assert BUILDING_SINK_MM < SLOPE * 60  # the sunk bottom stays inside the relief in this setup
