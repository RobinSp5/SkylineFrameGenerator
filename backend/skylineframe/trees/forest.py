"""The forest canopy: dense trees print as one billowing cloud, not as an egg carton of domes.

At 1:8 000 to 1:15 000 every WorldCover tree is the same minimum dome on a jittered 8 m grid, and
a forest of them reads as a uniform texture. Seen from above, a real forest is clouds of crowns
of different sizes with a rounded, lumpy edge. So where trees stand dense:

- the fitting gives the WorldCover trees a spread of crowns, FOREST_CROWN_M, big ones rarer, so
  their union has an irregular outline (fitted_trees in geometry.py);
- the canopy is the local data height, smoothed over the trees, times 1 + FOREST_RELIEF * billow:
  cloud noise (fBm of |noise|: round billows, creases between them) in local metres with
  wavelengths FOREST_WAVELENGTHS_M, of which only those at least NOISE_FLOOR_LINES lines long on
  the print are kept. No billow is narrower than that, and the creases point down;
- on the billows, every tree is a floret: a low cap about its own crown wide and FLORET_SHARE of
  the canopy height high, never more than FLORET_MAX_MM (one layer pair), both hashed per tree.
  Neighbours overlap, so the top reads as packed crowns with shallow crevices between them; the
  florets are relief about the billow surface, which keeps its mean;
- towards the edge of the union, and of every clearing or road, it falls off in a rounded
  shoulder about EDGE_M wide, whose start the same kind of noise moves inwards by up to half that;
- whatever of the footprint is narrower than one line is opened away.

Printable by construction: the canopy only ever covers nodes inside the fitted crowns, so the
clearance around buildings, roads and water is the fitting's as before; it is one height field on
the canopy grid (2.5D); and no part of it, hill, floret or footprint, is narrower than a line.
"""

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from ..spec import FrameSpec
from ..terrain.heightfield import Heightfield
from .crown import min_crown_mm
from .defaults import TREE_CROWN_M
from .discs import MIN_CAP_MM, cap_height, disc_nodes
from .model import Tree
from .noise import billow, fbm, unit_hash

__all__ = ["billow", "fbm", "floret_shapes", "forest_canopy", "forest_crowns_mm", "forest_wavelengths_m", "is_forest"]

FOREST_WAVELENGTHS_M = (80.0, 35.0, 15.0)  # clumps of three sizes, in metres of city
NOISE_FLOOR_LINES = 2.5  # no wavelength shorter than this many lines on the print
FOREST_RELIEF = 0.35  # the canopy rises and falls up to 35 % about the data height
# Crown diameters of forest trees. At least half the placement grid's diagonal again (8 m cells,
# jittered), so neighbouring crowns always merge: the only gaps are where a tree is missing.
FOREST_CROWN_M = (12.0, 20.0)
FOREST_NEIGHBOURS = 3  # a tree with this many others within FOREST_RADIUS crowns is forest
FOREST_RADIUS = 1.5
EDGE_M = 6.0  # width of the rounded shoulder at a forest edge
HEIGHT_SMOOTH_M = 15.0  # the data height is averaged over about this distance
FLORET_WIDTH = (0.75, 1.0)  # a floret is this share of its tree's crown wide, at least a crown
FLORET_SHARE = (0.15, 0.25)  # and this share of the local canopy high
FLORET_MAX_MM = 0.4  # but never higher than a layer pair: no deep crevice between two florets


def forest_wavelengths_m(spec: FrameSpec) -> list[float]:
    """The octaves long enough to print; if none is, one at the floor itself."""
    floor_mm = NOISE_FLOOR_LINES * spec.min_line_mm
    kept = [w for w in FOREST_WAVELENGTHS_M if w * spec.scale >= floor_mm - 1e-9]
    return kept or [floor_mm / spec.scale]


def is_forest(trees: list[Tree], spec: FrameSpec) -> np.ndarray:
    """True for each tree with FOREST_NEIGHBOURS others within FOREST_RADIUS default crowns.

    A WorldCover forest has about seven; a lone tree none and a tree row two."""
    if not trees:
        return np.zeros(0, dtype=bool)
    xy = np.array([(t.x_mm, t.y_mm) for t in trees], dtype=float)
    radius = FOREST_RADIUS * TREE_CROWN_M * spec.scale
    others = cKDTree(xy).query_ball_point(xy, r=radius, return_length=True) - 1
    return others >= FOREST_NEIGHBOURS


def forest_crowns_mm(trees: list[Tree], spec: FrameSpec) -> np.ndarray:
    """The crown each tree asks the fitting for: a WorldCover tree in a forest gets one from
    FOREST_CROWN_M, skewed to the small end and hashed from its place; any other tree, and an
    OSM tree with its own diameter_crown, keeps its own."""
    crowns = np.array([t.crown_mm for t in trees], dtype=float)
    default = TREE_CROWN_M * spec.scale
    varied = is_forest(trees, spec) & np.isclose(crowns, default, rtol=1e-9, atol=1e-12)
    if varied.any():
        xy_cm = np.round(np.array([(t.x_mm, t.y_mm) for t in trees])[varied] / spec.scale * 100).astype(np.int64)
        u = unit_hash(xy_cm[:, 0], xy_cm[:, 1], 7)
        lo, hi = FOREST_CROWN_M
        crowns[varied] = (lo + (hi - lo) * u**2.5) * spec.scale
    return crowns


def floret_shapes(trees: list[Tree], spec: FrameSpec) -> tuple[np.ndarray, np.ndarray]:
    """Diameter (mm) and height share of each tree's floret, hashed from its place in metres.

    At least as wide as the narrowest printed crown, so its top is wider than a line, and never
    wider than its own crown, so it stays inside what the fitting cleared."""
    xy_cm = np.round(np.array([(t.x_mm, t.y_mm) for t in trees]) / spec.scale * 100).astype(np.int64)
    crown = np.array([t.crown_mm for t in trees])
    lo, hi = FLORET_WIDTH
    width = crown * (lo + (hi - lo) * unit_hash(xy_cm[:, 0], xy_cm[:, 1], 11))
    diameter = np.clip(width, np.minimum(min_crown_mm(spec), crown), crown)
    lo, hi = FLORET_SHARE
    share = lo + (hi - lo) * unit_hash(xy_cm[:, 0], xy_cm[:, 1], 12)
    return diameter, share


def _disc(radius_mm: float, cell: float) -> np.ndarray:
    r = int(np.ceil(radius_mm / cell - 1e-9))
    yy, xx = np.mgrid[-r : r + 1, -r : r + 1]
    return xx * xx + yy * yy <= r * r


def forest_canopy(trees: list[Tree], spec: FrameSpec, grid: Heightfield) -> np.ndarray:
    """The forest's height above ground over every node of `grid`, 0 away from the forest."""
    z = np.zeros(grid.z_mm.shape)
    dense = is_forest(trees, spec)
    if not dense.any():
        return z
    forest = [t for t, d in zip(trees, dense, strict=True) if d]
    x = np.array([t.x_mm for t in forest])
    y = np.array([t.y_mm for t in forest])
    a = np.array([t.crown_mm / 2 for t in forest])
    h = np.array([t.height_mm for t in forest])
    t, i, j, r = disc_nodes(x, y, a, grid)
    inside = r < a[t]
    t, i, j = t[inside], i[inside], j[inside]
    total, count = np.zeros(z.shape), np.zeros(z.shape)
    np.add.at(total, (j, i), h[t])
    np.add.at(count, (j, i), 1.0)
    cover = count > 0
    if not cover.any():
        return z

    cell, (ox, oy) = grid.cell_mm, grid.origin_mm
    # The data height, the mean of the crowns over a node, averaged over the forest only.
    sigma = HEIGHT_SMOOTH_M * spec.scale / cell
    mean = np.where(cover, total / np.maximum(count, 1.0), 0.0)
    weight = ndimage.gaussian_filter(cover.astype(float), sigma)
    base = ndimage.gaussian_filter(mean, sigma)[cover] / np.maximum(weight[cover], 1e-12)

    jj, ii = np.nonzero(cover)
    x_m, y_m = (ox + ii * cell) / spec.scale, (oy + jj * cell) / spec.scale
    waves = forest_wavelengths_m(spec)
    top = base * (1 + FOREST_RELIEF * billow(x_m, y_m, waves, salt=1))

    # Florets: a cap per tree on the billows, as relief about its local mean.
    diameter, share = floret_shapes(forest, spec)
    t, i, j, r = disc_nodes(x, y, diameter / 2, grid)
    under = np.zeros(z.shape)
    under[cover] = top
    bump = cap_height(r, diameter[t], np.minimum(share[t] * under[j, i], FLORET_MAX_MM))
    florets = np.zeros(z.shape)
    np.maximum.at(florets, (j, i), bump)
    mean = ndimage.gaussian_filter(florets, sigma) / np.maximum(weight, 1e-12)
    top = top + (florets - mean)[cover]

    # A rounded shoulder at every edge, its start pushed inwards by noise: lumpy, never outwards.
    edge = max(EDGE_M * spec.scale, min_crown_mm(spec) / 2)
    depth = ndimage.distance_transform_edt(cover)[cover] * cell
    depth = depth - 0.5 * edge * (0.5 + 0.5 * fbm(x_m, y_m, waves, salt=2))
    s = np.clip(depth / edge, 0.0, 1.0)
    z[cover] = top * np.sqrt(1 - (1 - s) ** 2)

    # Nothing narrower than a line: open the footprint with a disc one line across.
    foot = ndimage.binary_opening(z >= MIN_CAP_MM, structure=_disc(spec.min_line_mm / 2, cell))
    z[~foot] = 0.0
    return z
