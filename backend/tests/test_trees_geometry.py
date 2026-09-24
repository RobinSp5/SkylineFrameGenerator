"""The geometry half of the trees (spec 6 §5): fitting, the canopy height field and its solid.

Every test works on synthetic Tree lists; the data half (WorldCover, OSM) is tested on its own.
"""

import manifold3d as m3d
import numpy as np
import pytest
from shapely.geometry import box

from skylineframe.grid import masked_grid_solid
from skylineframe.spec import FrameSpec, Mode
from skylineframe.terrain.heightfield import Heightfield
from skylineframe.trees.crown import HEIGHT_JITTER, LOBES_MAX, crown_lobes
from skylineframe.trees.geometry import (
    MIN_CAP_MM,
    TREE_CELL_MM,
    TREE_CLEARANCE_MM,
    cap_height,
    canopy,
    fitted_trees,
    min_crown_mm,
    min_tree_height_mm,
)
from skylineframe.trees.model import Tree


# A lone crown's top against the data height: a little lower at most.
LOW, HIGH = 1 - HEIGHT_JITTER, 1.0


def spec(**kw) -> FrameSpec:
    return FrameSpec(center_lat=50, center_lon=8, side_m=1000, plate_size_mm=100, mode=Mode.full, **kw)


# --- the dome ---------------------------------------------------------------------------------


def test_a_low_dome_is_a_spherical_cap():
    # Base radius 1, height 0.5: sphere radius (1 + 0.25) / 1 = 1.25, centre 0.75 below the top.
    r = np.array([0.0, 0.5, 1.0, 1.5])
    z = cap_height(r, crown_mm=2.0, height_mm=0.5)
    assert z[0] == pytest.approx(0.5)
    assert z[1] == pytest.approx(0.5 - 1.25 + np.sqrt(1.25**2 - 0.25))
    assert z[2] == pytest.approx(0.0, abs=1e-12)
    assert z[3] == 0.0  # outside the crown


def test_a_tall_dome_is_a_stretched_hemisphere_without_overhang():
    # Taller than its base radius, a spherical cap would bulge out past its base: an overhang. The
    # dome keeps its footprint and stretches instead, so it stays a height field (2.5D).
    r = np.linspace(0.0, 0.5, 11)
    z = cap_height(r, crown_mm=1.0, height_mm=1.2)
    assert z[0] == pytest.approx(1.2)
    assert z[-1] == pytest.approx(0.0, abs=1e-12)
    assert np.all(np.diff(z) <= 0)
    assert z[5] == pytest.approx(1.2 * np.sqrt(1 - 0.25**2 / 0.5**2))


# --- fitting ------------------------------------------------------------------------------------


def test_print_minimums_follow_the_line_width_and_the_building_minimum():
    s = spec()
    assert min_crown_mm(s) == pytest.approx(s.min_line_mm * 1.25)
    assert min_tree_height_mm(s) == pytest.approx(s.min_building_height_mm * 0.5)


def test_a_free_tree_keeps_its_size():
    tree = Tree(0.0, 0.0, 3.0, 2.0)
    assert fitted_trees([tree], [], spec()) == [tree]


def test_print_optimized_lifts_a_small_tree_to_the_minimum():
    s = spec()
    (tree,) = fitted_trees([Tree(0.0, 0.0, 0.5, 0.1)], [], s)
    assert tree.crown_mm == pytest.approx(min_crown_mm(s))
    assert tree.height_mm == pytest.approx(min_tree_height_mm(s))


def test_without_print_optimization_a_small_tree_stays_small():
    (tree,) = fitted_trees([Tree(0.0, 0.0, 0.6, 0.1)], [], spec(print_optimized=False))
    assert (tree.crown_mm, tree.height_mm) == (0.6, 0.1)


def test_a_crown_touching_a_building_is_shrunk_until_it_is_clear():
    # Centre 2 mm from the wall: the crown radius may be at most 2 - clearance.
    house = box(2.0, -5.0, 10.0, 5.0)
    (tree,) = fitted_trees([Tree(0.0, 0.0, 6.0, 3.0)], [house], spec())
    assert tree.crown_mm == pytest.approx(2 * (2.0 - TREE_CLEARANCE_MM))
    assert tree.height_mm == 3.0  # the height is the tree's, only the crown has to fit


def test_a_tree_that_would_fall_below_the_minimum_is_dropped():
    s = spec()
    gap = TREE_CLEARANCE_MM + min_crown_mm(s) / 2 - 0.05
    house = box(gap, -5.0, 10.0, 5.0)
    assert fitted_trees([Tree(0.0, 0.0, 6.0, 3.0)], [house], s) == []


def test_a_tree_standing_on_a_road_is_dropped():
    road = box(-50.0, -1.0, 50.0, 1.0)
    assert fitted_trees([Tree(0.0, 0.0, 1.0, 1.0)], [road], spec()) == []


def test_a_crown_stays_on_the_plate():
    (tree,) = fitted_trees([Tree(48.0, 0.0, 6.0, 3.0)], [], spec())
    assert tree.x_mm + tree.crown_mm / 2 <= 50.0 - TREE_CLEARANCE_MM + 1e-9
    assert fitted_trees([Tree(49.9, 0.0, 6.0, 3.0)], [], spec()) == []


def test_fitting_nothing_gives_nothing():
    assert fitted_trees([], [box(0, 0, 1, 1)], spec()) == []


def test_the_dome_takes_one_shape_per_point():
    # The canopy evaluates every lobe of the forest in one call: crown and height per point.
    r = np.array([0.0, 0.5, 0.0, 0.25])
    crown = np.array([2.0, 2.0, 1.0, 1.0])
    height = np.array([0.5, 0.5, 1.2, 1.2])
    z = cap_height(r, crown, height)
    assert z[0] == pytest.approx(0.5)
    assert z[1] == pytest.approx(cap_height(np.array([0.5]), 2.0, 0.5)[0])
    assert z[2] == pytest.approx(1.2)
    assert z[3] == pytest.approx(cap_height(np.array([0.25]), 1.0, 1.2)[0])


# --- the crown ----------------------------------------------------------------------------------


def forest(n: int = 400, crown: float = 1.0, height: float = 1.2) -> list[Tree]:
    """Trees on a jittered grid half a crown apart, the way layer.py places a WorldCover forest."""
    side = int(np.sqrt(n))
    step = crown * 0.53
    i, j = (a.ravel() for a in np.meshgrid(np.arange(side), np.arange(side)))
    x = -10 + i * step + 0.1 * np.sin(7 * i + 3 * j)
    y = -10 + j * step + 0.1 * np.cos(5 * i + j)
    return [Tree(float(xi), float(yi), crown, height) for xi, yi in zip(x, y, strict=True)]


def test_a_crown_is_a_cloud_of_lobes():
    s = spec()
    tree = Tree(0.3, -0.7, 4.0, 2.0)
    lobes = crown_lobes([tree], s)
    assert 5 <= len(lobes.radius) <= 2 + LOBES_MAX
    assert np.all(lobes.tree == 0)
    # The tallest lobe is the top of the tree, a little lower than the data at most.
    assert tree.height_mm * LOW - 1e-9 <= lobes.height.max() <= tree.height_mm * HIGH


def test_every_lobe_is_printable_and_inside_the_cleared_crown():
    s = spec()
    min_lobe = s.min_line_mm / 2
    places = [(-7.1, 3.3), (2.0, 2.0), (11.7, -4.2), (0.01, 0.0)]
    shapes = [(min_crown_mm(s), 0.4), (1.0, 1.2), (1.6, 0.5), (4.0, 2.0), (9.0, 3.0)]
    trees = [Tree(x, y, c, h) for x, y in places for c, h in shapes]
    lobes = crown_lobes(trees, s)
    for k, tree in enumerate(trees):
        mine = lobes.tree == k
        x, y, r, h = lobes.x[mine], lobes.y[mine], lobes.radius[mine], lobes.height[mine]
        # No lobe narrower than a line at its base, so none of them is a pin sticking up.
        assert np.all(r >= min_lobe - 1e-9)
        # The crown never grows: every lobe stays inside the crown the fitting cleared.
        assert np.all(np.hypot(x - tree.x_mm, y - tree.y_mm) + r <= tree.crown_mm / 2 + 1e-9)
        # And it never shrinks below the printable crown: the widest lobe spans that on its own.
        assert 2 * r.max() >= min_crown_mm(s) - 1e-9
        assert h.max() >= min_tree_height_mm(s) - 1e-9


def test_a_small_tree_is_not_jittered_below_the_print_minimum():
    s = spec()
    tree = Tree(5.0, 5.0, min_crown_mm(s), min_tree_height_mm(s))
    lobes = crown_lobes([tree], s)
    assert min_tree_height_mm(s) <= lobes.height.max() <= min_tree_height_mm(s) * HIGH
    assert 2 * lobes.radius.max() == pytest.approx(min_crown_mm(s))


def test_the_crown_is_the_same_every_time_and_differs_from_tree_to_tree():
    s = spec()
    trees = [Tree(0.0, 0.0, 4.0, 2.0), Tree(10.0, 0.0, 4.0, 2.0)]
    first, again = canopy(trees, s), canopy(list(trees), s)
    assert np.array_equal(first.z_mm, again.z_mm)
    # Same tree, 10 mm (50 cells) apart: the two crowns are not copies of each other.
    left = first.z_mm[230:271, 230:271]
    right = first.z_mm[230:271, 280:321]
    assert np.abs(left - right).max() > 0.1


def neighbour_max(z: np.ndarray) -> np.ndarray:
    """The highest of the eight neighbours of every inner node."""
    ny, nx = z.shape
    shifted = [z[1 + dj : ny - 1 + dj, 1 + di : nx - 1 + di] for dj in (-1, 0, 1) for di in (-1, 0, 1) if dj or di]
    return np.max(shifted, axis=0)


def local_maxima(z: np.ndarray) -> int:
    inner = z[1:-1, 1:-1]
    neighbours = neighbour_max(z)
    return int(np.sum((inner > neighbours) & (inner > 0)))


def test_a_large_crown_is_lumpy_not_a_dome():
    s = spec()
    field = canopy([Tree(0.0, 0.0, 6.0, 2.5)], s)
    assert local_maxima(field.z_mm) >= 3
    # Not radially symmetric: the height on a ring round the centre varies from side to side.
    angles = np.linspace(0, 2 * np.pi, 36, endpoint=False)
    ring = field.sample(2.0 * np.cos(angles), 2.0 * np.sin(angles))
    assert ring.max() - ring.min() > 0.3


def test_the_footprint_stays_inside_the_crown_and_covers_the_minimum():
    s = spec()
    for tree in [Tree(0.0, 0.0, min_crown_mm(s), 1.2), Tree(0.0, 0.0, 3.0, 1.0), Tree(0.0, 0.0, 6.0, 2.5)]:
        field = canopy([tree], s)
        n = field.z_mm.shape[0]
        g = field.origin_mm[0] + np.arange(n) * field.cell_mm
        gx, gy = np.meshgrid(g, g)
        r = np.hypot(gx - tree.x_mm, gy - tree.y_mm)
        assert np.all(r[field.z_mm > 0] < tree.crown_mm / 2)
        # The middle of a crown as wide as the printable minimum is solid.
        assert np.all(field.z_mm[r < min_crown_mm(s) / 2 - field.cell_mm] >= MIN_CAP_MM)


def test_a_tall_crown_is_still_a_height_field_with_its_top_inside():
    s = spec()
    field = canopy([Tree(0.0, 0.0, 1.0, 3.0)], s)
    assert 3.0 * LOW - 1e-9 <= field.z_mm.max() <= 3.0 * HIGH


def dome_canopy(trees: list[Tree], s: FrameSpec) -> np.ndarray:
    """The canopy of before the lobes: one dome per tree, the highest winning."""
    grid = canopy([], s)
    n = grid.z_mm.shape[0]
    g = grid.origin_mm[0] + np.arange(n) * grid.cell_mm
    gx, gy = np.meshgrid(g, g)
    z = np.zeros_like(grid.z_mm)
    for t in trees:
        z = np.maximum(z, cap_height(np.hypot(gx - t.x_mm, gy - t.y_mm), t.crown_mm, t.height_mm))
    return z


def test_a_lumpy_forest_costs_no_more_triangles_than_the_domes():
    from skylineframe.mesh import tree_solid

    s = spec()
    trees = forest()
    lumpy = tree_solid(trees, s, None)
    grid = canopy([], s)
    z = dome_canopy(trees, s)
    node = z >= MIN_CAP_MM
    top = np.where(node, z, -1.0)
    cells = node[:-1, :-1] | node[:-1, 1:] | node[1:, :-1] | node[1:, 1:]
    domes = masked_grid_solid(grid, top, -2.0, cells)
    assert lumpy.num_tri() <= 1.10 * domes.num_tri()


def test_the_canopy_is_one_height_field_on_a_fine_grid_with_the_highest_crown_winning():
    s = spec()
    field = canopy([Tree(0.0, 0.0, 4.0, 1.0), Tree(1.0, 0.0, 2.0, 3.0), Tree(-30.0, 20.0, 2.0, 1.5)], s)
    assert field.cell_mm <= TREE_CELL_MM
    assert field.origin_mm == (-50.0, -50.0)
    assert field.z_mm.shape == (501, 501)
    # The taller tree stands in the lower one: its top wins, a little below the data at most.
    near = field.z_mm[245:256, 250:261]
    assert 3.0 * LOW - 1e-9 <= near.max() <= 3.0 * HIGH
    assert 1.5 * LOW - 1e-9 <= field.sample(-30.0, 20.0) <= 1.5 * HIGH
    assert field.z_mm.max() == near.max()
    assert field.sample(20.0, 20.0) == 0.0


def test_the_canopy_grid_never_gets_coarser_than_a_fifth_of_a_millimetre():
    odd = FrameSpec(center_lat=50, center_lon=8, side_m=1000, plate_size_mm=99.9)
    field = canopy([Tree(0.0, 0.0, 2.0, 1.0)], odd)
    assert field.cell_mm <= TREE_CELL_MM
    assert field.origin_mm[0] + (field.z_mm.shape[1] - 1) * field.cell_mm == pytest.approx(99.9 / 2)


# --- the masked grid solid --------------------------------------------------------------------


def unit_grid(n: int = 5) -> Heightfield:
    return Heightfield(np.zeros((n, n)), 1.0, (0.0, 0.0))


def test_a_masked_grid_solid_only_covers_the_chosen_cells():
    hf = unit_grid()
    cells = np.zeros((4, 4), bool)
    cells[1, 1] = cells[1, 2] = True
    solid = masked_grid_solid(hf, np.full((5, 5), 2.0), -1.0, cells)
    assert solid.status() == m3d.Error.NoError
    assert solid.volume() == pytest.approx(2 * 3.0)
    assert solid.bounding_box() == pytest.approx((1.0, 1.0, -1.0, 3.0, 2.0, 2.0))


def test_a_masked_grid_solid_follows_its_top_surface():
    hf = unit_grid(3)
    top = np.array([[1.0, 1.0, 1.0], [1.0, 3.0, 1.0], [1.0, 1.0, 1.0]])
    solid = masked_grid_solid(hf, top, 0.0, np.ones((2, 2), bool))
    # Every cell is split along its (0, 0)-(1, 1) diagonal. The two cells whose diagonal ends in
    # the peak have it in both triangles (mean 5/3 each), the other two in one (5/3 and 1).
    assert solid.volume() == pytest.approx(2 * 5 / 3 + 2 * 4 / 3)


def test_cells_that_only_touch_at_a_corner_still_give_a_valid_solid():
    # Two cells meeting in one grid node would pinch the surface there; the mask is closed first.
    hf = unit_grid(6)
    cells = np.zeros((5, 5), bool)
    cells[0, 0] = cells[1, 1] = True
    cells[4, 3] = cells[3, 4] = True
    solid = masked_grid_solid(hf, np.full((6, 6), 1.0), 0.0, cells)
    assert solid.status() == m3d.Error.NoError
    assert len(solid.decompose()) == 2
    assert solid.volume() == pytest.approx(8.0)  # each pair closed to a 2 x 2 block


def test_a_ring_of_cells_keeps_its_hole():
    hf = unit_grid()
    cells = np.ones((4, 4), bool)
    cells[1:3, 1:3] = False
    solid = masked_grid_solid(hf, np.full((5, 5), 1.0), 0.0, cells)
    assert solid.status() == m3d.Error.NoError
    assert solid.volume() == pytest.approx(12.0)
    assert solid.genus() == 1
