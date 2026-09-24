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
from skylineframe.trees.geometry import (
    TREE_CELL_MM,
    TREE_CLEARANCE_MM,
    cap_height,
    canopy,
    fitted_trees,
    min_crown_mm,
    min_tree_height_mm,
)
from skylineframe.trees.model import Tree


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


# --- the canopy -------------------------------------------------------------------------------


def test_the_canopy_is_one_height_field_on_a_fine_grid_with_the_highest_crown_winning():
    s = spec()
    field = canopy([Tree(0.0, 0.0, 4.0, 1.0), Tree(1.0, 0.0, 2.0, 3.0), Tree(-30.0, 20.0, 2.0, 1.5)], s)
    assert field.cell_mm <= TREE_CELL_MM
    assert field.origin_mm == (-50.0, -50.0)
    assert field.z_mm.shape == (501, 501)
    assert field.sample(1.0, 0.0) == pytest.approx(3.0)  # the taller tree stands in the lower one
    assert field.sample(-1.0, 0.0) == pytest.approx(cap_height(np.array([1.0]), 4.0, 1.0)[0], abs=1e-9)
    assert field.sample(-30.0, 20.0) == pytest.approx(1.5)
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
