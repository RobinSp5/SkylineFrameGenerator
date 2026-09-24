"""The forest canopy (spec 6 §5): dense trees print as one billowing cloud, not an egg carton."""

import numpy as np
import pytest
from scipy import ndimage
from shapely.geometry import Point, box

import skylineframe.trees.forest as forest_module
from skylineframe.spec import FrameSpec, Mode
from skylineframe.trees.defaults import TREE_CROWN_M, TREE_HEIGHT_M
from skylineframe.trees.forest import (
    FLORET_MAX_MM,
    FLORET_SHARE,
    FOREST_CROWN_M,
    FOREST_RELIEF,
    FOREST_WAVELENGTHS_M,
    billow,
    fbm,
    floret_shapes,
    forest_canopy,
    forest_wavelengths_m,
    is_forest,
)
from skylineframe.trees.geometry import (
    MIN_CAP_MM,
    TREE_CLEARANCE_MM,
    canopy,
    canopy_grid,
    fitted_trees,
    min_crown_mm,
)
from skylineframe.trees.model import Tree


def spec(side_m: float = 500, plate_mm: float = 100, **kw) -> FrameSpec:
    return FrameSpec(center_lat=50, center_lon=8, side_m=side_m, plate_size_mm=plate_mm, mode=Mode.full, **kw)


def woods(s: FrameSpec, x0_m: float, y0_m: float, x1_m: float, y1_m: float) -> list[Tree]:
    """WorldCover trees the way layer.py places them: one per 8 m cell, jittered, 12 m +-20 %."""
    i, j = np.meshgrid(np.arange(x0_m, x1_m, TREE_CROWN_M), np.arange(y0_m, y1_m, TREE_CROWN_M))
    i, j = i.ravel(), j.ravel()
    x = i + TREE_CROWN_M * (0.25 + 0.5 * (0.5 + 0.5 * np.sin(12.9898 * i + 78.233 * j)))
    y = j + TREE_CROWN_M * (0.25 + 0.5 * (0.5 + 0.5 * np.sin(39.3468 * i + 11.135 * j)))
    h = TREE_HEIGHT_M * (1 + 0.2 * np.sin(3.7 * i + 1.3 * j))
    z = s.scale * s.z_exaggeration
    return [
        Tree(float(a) * s.scale, float(b) * s.scale, TREE_CROWN_M * s.scale, float(c) * z)
        for a, b, c in zip(x, y, h, strict=True)
    ]


# --- the noise ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("side", "plate", "optimized"), [(1500, 100, True), (500, 100, True), (1200, 150, True), (1500, 100, False)]
)
def test_no_octave_is_finer_than_the_printable_floor(side, plate, optimized):
    s = spec(side, plate, print_optimized=optimized)
    kept = forest_wavelengths_m(s)
    floor = 2.5 * s.min_line_mm
    assert kept, "the coarsest octave always stays"
    assert all(w * s.scale >= floor - 1e-9 for w in kept)
    assert all(w * s.scale < floor for w in FOREST_WAVELENGTHS_M if w not in kept)


def test_the_octaves_at_the_default_scales():
    assert forest_wavelengths_m(spec(1500, 100)) == [80.0, 35.0]
    assert forest_wavelengths_m(spec(1200, 150)) == [80.0, 35.0]
    assert forest_wavelengths_m(spec(500, 100)) == [80.0, 35.0, 15.0]


def test_the_noise_is_deterministic_bounded_and_smooth():
    x = np.linspace(-300, 300, 20001)  # 3 cm steps in metres
    y = np.full_like(x, 17.0)
    n = fbm(x, y, [80.0, 35.0, 15.0])
    assert np.array_equal(n, fbm(x, y, [80.0, 35.0, 15.0]))
    assert np.all(np.abs(n) < 1)
    assert n.max() > 0.5 and n.min() < -0.5  # it does use its range
    # No spike finer than the shortest wavelength: over 15 m / 50 it moves a little at most.
    step = int(round(15.0 / 50 / (x[1] - x[0])))
    assert np.abs(n[step:] - n[:-step]).max() < 0.2


def test_billows_are_round_on_top_creased_below_and_centred():
    waves = [80.0, 35.0]
    x = np.linspace(-2000, 2000, 400001)  # 1 cm steps
    b = billow(x, np.full_like(x, -23.0), waves)
    assert np.array_equal(b, billow(x, np.full_like(x, -23.0), waves))
    assert np.all(np.abs(b) < 1)
    assert abs(b.mean()) < 0.1  # the canopy keeps about the data height
    # Every hill is round on top: within a tenth of the shortest wavelength it drops but a little.
    # (The creases between them are sharp, and they point down: nothing narrow sticks up.)
    tops = np.flatnonzero((b[1:-1] > b[:-2]) & (b[1:-1] >= b[2:])) + 1
    reach = int(35.0 / 10 / 0.01)
    tops = tops[(tops > reach) & (tops < len(b) - reach)]
    assert len(tops) > 20
    assert max(b[k] - min(b[k - reach], b[k + reach]) for k in tops) < 0.35
    creases = np.flatnonzero((b[1:-1] < b[:-2]) & (b[1:-1] <= b[2:])) + 1
    assert len(creases) > 20


# --- which trees are a forest -------------------------------------------------------------------


def test_dense_trees_are_a_forest_and_lone_trees_and_rows_are_not():
    s = spec()
    forest = woods(s, -80, -80, 80, 80)
    lone = [Tree(-40.0, 40.0, 1.6, 2.0)]
    row = [Tree(40.0 + k * TREE_CROWN_M * s.scale, 40.0, 1.6, 2.0) for k in range(10)]
    dense = is_forest(forest + lone + row, s)
    xy = np.array([(t.x_mm, t.y_mm) for t in forest])
    inner = np.max(np.abs(xy), axis=1) < 80 * s.scale - 3
    assert dense[: len(forest)][inner].all()
    assert not dense[len(forest) :].any()


# --- fitting ------------------------------------------------------------------------------------


def test_forest_trees_get_varied_crowns_mostly_small():
    s = spec()
    trees = woods(s, -150, -150, 150, 150)
    fitted = fitted_trees(trees, [], s)
    assert len(fitted) == len(trees)
    crowns_m = (np.array([t.crown_mm for t in fitted]) / s.scale)[is_forest(trees, s)]
    lo, hi = FOREST_CROWN_M
    assert crowns_m.min() >= lo - 1e-6 and crowns_m.max() <= hi + 1e-6
    assert crowns_m.max() - crowns_m.min() > 0.6 * (hi - lo)
    assert np.mean(crowns_m > (lo + hi) / 2) < 0.35  # big trees are fewer
    # The same forest fits the same way every time.
    assert fitted == fitted_trees(list(trees), [], s)


def test_lone_and_tagged_trees_keep_their_crowns():
    s = spec()
    trees = woods(s, -60, -60, 60, 60)
    tagged = Tree(0.3, 0.1, 2.3, 2.0)  # an OSM tree with its own diameter_crown, inside the wood
    lone = Tree(40.0, 40.0, TREE_CROWN_M * s.scale, 2.0)
    fitted = fitted_trees([*trees, tagged, lone], [], s)
    assert tagged in fitted and lone in fitted


def test_forest_crowns_still_keep_clear_of_a_house():
    s = spec()
    house = box(0.0, -30.0, 10.0, 30.0)
    trees = [t for t in woods(s, -100, -100, 100, 100) if not house.buffer(0.01).contains(Point(t.x_mm, t.y_mm))]
    for t in fitted_trees(trees, [house], s):
        assert house.distance(Point(t.x_mm, t.y_mm)) - t.crown_mm / 2 >= TREE_CLEARANCE_MM - 1e-9


# --- the canopy ---------------------------------------------------------------------------------


def forest_field(s: FrameSpec, trees: list[Tree]) -> np.ndarray:
    return forest_canopy(trees, s, canopy_grid(s))


def node_xy(s: FrameSpec):
    grid = canopy_grid(s)
    n = grid.z_mm.shape[0]
    g = grid.origin_mm[0] + np.arange(n) * grid.cell_mm
    return np.meshgrid(g, g)


def crown_cover(s: FrameSpec, trees: list[Tree]) -> np.ndarray:
    gx, gy = node_xy(s)
    inside = np.zeros(gx.shape, bool)
    for t in trees:
        inside |= np.hypot(gx - t.x_mm, gy - t.y_mm) < t.crown_mm / 2
    return inside


def test_the_forest_canopy_stays_inside_the_crowns_and_has_no_slivers():
    s = spec()
    trees = fitted_trees(woods(s, -120, -120, 120, 120) + woods(s, 150, -40, 200, 40), [], s)
    z = forest_field(s, trees)
    foot = z >= MIN_CAP_MM
    assert foot.any()
    assert not (foot & ~crown_cover(s, trees)).any()
    assert np.all(z[~foot] == 0)
    # Every node of the footprint lies in a disc one line wide inside the footprint.
    r = int(np.ceil(s.min_line_mm / 2 / canopy_grid(s).cell_mm))
    yy, xx = np.mgrid[-r : r + 1, -r : r + 1]
    disc = xx * xx + yy * yy <= r * r
    assert np.array_equal(ndimage.binary_opening(foot, structure=disc), foot)


def test_the_forest_canopy_billows_about_the_data_height():
    s = spec()
    trees = fitted_trees(woods(s, -240, -240, 240, 240), [], s)
    z = forest_field(s, trees)
    data = np.mean([t.height_mm for t in trees])
    cell = canopy_grid(s).cell_mm
    inner = ndimage.distance_transform_edt(ndimage.binary_fill_holes(z > 0)) * cell > 20 * s.scale  # 20 m in
    assert np.mean(z[inner]) == pytest.approx(data, rel=0.1)
    # Large clouds of varied height ...
    smooth = ndimage.gaussian_filter(z, 20 * s.scale / cell)
    assert smooth[inner].std() > 0.1 * FOREST_RELIEF * data
    assert np.percentile(z[inner], 95) - np.percentile(z[inner], 5) > 0.3 * data


# --- the florets --------------------------------------------------------------------------------


def test_every_floret_is_a_printable_crown_of_its_own_size():
    s = spec(1500, 100)
    trees = fitted_trees(woods(s, -600, -600, 600, 600), [], s)
    diameter, share = floret_shapes(trees, s)
    crowns = np.array([t.crown_mm for t in trees])
    assert np.all(diameter >= min_crown_mm(s) - 1e-9)  # its top is wider than a line
    assert np.all(diameter <= crowns + 1e-9)  # and it never leaves its crown
    lo, hi = FLORET_SHARE
    assert np.all((share >= lo) & (share <= hi))
    assert diameter.std() > 0.02 and share.std() > 0.02  # irregular, not a grid
    assert np.array_equal(diameter, floret_shapes(list(trees), s)[0])


@pytest.mark.parametrize(("side", "plate"), [(1500, 100), (1200, 150)])
def test_the_canopy_is_a_cauliflower_florets_on_billows(side, plate, monkeypatch):
    s = spec(side, plate)
    half = side * 0.4
    trees = fitted_trees(woods(s, -half, -half, half, half), [], s)
    z = forest_field(s, trees)
    cell = canopy_grid(s).cell_mm
    depth = ndimage.distance_transform_edt(ndimage.binary_fill_holes(z > 0)) * cell
    inner = depth > 20 * s.scale
    n_inner = np.sum([inner[int(round((t.y_mm + plate / 2) / cell)), int(round((t.x_mm + plate / 2) / cell))] for t in trees])
    # The trees are back as florets: a peak for every few trees, not one per tree (egg carton)
    # and not one per hundred (jelly).
    peaks = (z == ndimage.maximum_filter(z, size=3)) & inner
    assert 0.1 * n_inner < peaks.sum() < 0.9 * n_inner
    # The florets ride on the billows: they move the surface up or down by one layer pair at
    # most, so no crevice between them is deeper than that.
    monkeypatch.setattr(forest_module, "FLORET_SHARE", (0.0, 0.0))
    billows = forest_field(s, trees)
    assert np.abs(z - billows)[inner].max() <= FLORET_MAX_MM + 1e-9
    assert np.abs(z - billows)[inner].max() > 0.5 * FLORET_MAX_MM * FLORET_SHARE[0] / FLORET_SHARE[1]
    # And a floret is no pin: the relief it adds is a fraction of the canopy height.
    relief = z - billows
    assert np.percentile(relief[inner], 99) < FLORET_SHARE[1] * z[inner].mean()
    assert relief[inner].std() > 0.01 * z[inner].mean()


def test_the_forest_edge_is_rounded_not_a_step():
    s = spec()
    trees = fitted_trees(woods(s, -120, -120, 120, 120), [], s)
    z = forest_field(s, trees)
    cell = canopy_grid(s).cell_mm
    depth = ndimage.distance_transform_edt(ndimage.binary_fill_holes(z > 0)) * cell
    edge = (depth > 0) & (depth <= cell * 1.01)
    inner = depth > 20 * s.scale
    assert np.max(z[edge]) < 0.75 * np.median(z[inner])
    # Deeper in, higher: the shoulder rises over more than one cell.
    band = (depth > 2.5 * cell) & (depth <= 3.5 * cell)
    assert np.median(z[band]) > np.median(z[edge]) * 1.2


def test_the_forest_canopy_is_deterministic():
    s = spec(1500, 100)
    trees = fitted_trees(woods(s, -300, -300, 300, 300), [], s)
    assert np.array_equal(forest_field(s, trees), forest_field(s, list(trees)))


def test_lone_trees_keep_their_lobed_crowns_beside_a_forest():
    s = spec()
    wood = fitted_trees(woods(s, -120, -120, 0, 0), [], s)
    lone = Tree(30.0, 30.0, 6.0, 2.5)
    alone = canopy([lone], s).z_mm
    together = canopy([*wood, lone], s).z_mm
    gx, gy = node_xy(s)
    near = np.hypot(gx - 30.0, gy - 30.0) < 3.5
    assert np.array_equal(together[near], alone[near])
    assert forest_field(s, [lone]).max() == 0.0
