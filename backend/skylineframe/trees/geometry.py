"""How trees print (spec 6 §5): fitted between what else is on the plate, then one height field.

A crown is a cloud of overlapping domes, its lobes (crown.py). All of them together are a single
canopy height field on a grid of at most TREE_CELL_MM, the maximum of their domes, and mesh.py
turns that into one solid: a forest is tens of thousands of trees, and a solid per tree would be
tens of thousands of booleans.
"""

import numpy as np
import shapely
from shapely.geometry import Polygon

from ..spec import FrameSpec
from ..terrain.heightfield import Heightfield
from .crown import crown_lobes, min_crown_mm, min_tree_height_mm
from .model import Tree

TREE_CELL_MM = 0.2  # canopy grid: half a nozzle line, five nodes across the smallest dome
# Gap between a crown and a building, a groove or the plate edge. The canopy surface runs straight
# from node to node, so it reaches up to one cell diagonal (0.28 mm) past the dome it samples;
# this keeps even that off every wall and out of every groove, so the trees never share a face
# with anything and never fill a recess.
TREE_CLEARANCE_MM = 0.3
# A grid node where the dome is lower than this counts as ground. On terrain the dome sits on a
# relief sampled on a coarser grid, and the rim of the dome would otherwise leave a skin a few
# microns above the surface, too thin to print and a source of slivers in the union.
MIN_CAP_MM = 0.05


def cap_height(r: np.ndarray, crown_mm: float | np.ndarray, height_mm: float | np.ndarray) -> np.ndarray:
    """Height of a dome at distance r from its centre, 0 outside the crown.

    Up to a hemisphere this is the spherical cap through the crown rim and the top. A tree taller
    than its crown radius would need a cap bulging out past its own base, an overhang, so it is a
    hemisphere stretched upwards instead: the same at height == radius, and still a height field.
    Crown and height are one dome, or one per point of r.
    """
    r = np.asarray(r, dtype=float)
    a, h = np.broadcast_arrays(np.asarray(crown_mm, dtype=float) / 2, np.asarray(height_mm, dtype=float), r)[:2]
    inside = r < a
    low = h <= a
    safe_h = np.where(low, h, 1.0)
    big_r = (a * a + h * h) / (2 * safe_h)
    cap = h - big_r + np.sqrt(np.maximum(big_r * big_r - r * r, 0.0))
    stretched = h * np.sqrt(np.maximum(1.0 - (r / np.where(inside, a, 1.0)) ** 2, 0.0))
    z = np.where(low, cap, stretched)
    return np.where(inside, np.maximum(z, 0.0), 0.0)


def fitted_trees(trees: list[Tree], obstacles: list[Polygon], spec: FrameSpec) -> list[Tree]:
    """The trees that print, each shrunk until its crown is clear of every obstacle (spec 6 §5.2).

    Obstacles are building footprints, sockel blocks, road and water areas in print millimetres;
    the plate edge counts as one too. A tree whose centre is on an obstacle, or whose crown would
    have to shrink below the printable minimum, is dropped. In print mode a smaller tree is first
    lifted to the minimum, so an 8 m crown at 1:15 000 still prints as a dome.
    """
    if not trees:
        return []
    min_crown, min_height = min_crown_mm(spec), min_tree_height_mm(spec)
    xy = np.array([(t.x_mm, t.y_mm) for t in trees], dtype=float)
    half = spec.plate_size_mm / 2
    room = half - np.abs(xy).max(axis=1)
    polys = [p for p in obstacles if not p.is_empty and p.area > 0]
    if polys:
        tree_index = shapely.STRtree(polys)
        points = shapely.points(xy)
        (which, _), dist = tree_index.query_nearest(points, return_distance=True, all_matches=False)
        nearest = np.full(len(trees), np.inf)
        nearest[which] = dist
        room = np.minimum(room, nearest)
    fitted = []
    for tree, space in zip(trees, room, strict=True):
        crown = min(max(tree.crown_mm, min_crown), 2 * (space - TREE_CLEARANCE_MM))
        if crown < min_crown or crown <= 0:
            continue
        height = max(tree.height_mm, min_height)
        if height <= 0:
            continue
        if (crown, height) == (tree.crown_mm, tree.height_mm):
            fitted.append(tree)
        else:
            fitted.append(Tree(tree.x_mm, tree.y_mm, crown, height))
    return fitted


def canopy_grid(spec: FrameSpec) -> Heightfield:
    """An all-zero grid over the whole plate, edge to edge, cells no coarser than TREE_CELL_MM."""
    n = int(np.ceil(spec.plate_size_mm / TREE_CELL_MM - 1e-9)) + 1
    half = spec.plate_size_mm / 2
    return Heightfield(np.zeros((n, n)), spec.plate_size_mm / (n - 1), (-half, -half))


def canopy(trees: list[Tree], spec: FrameSpec) -> Heightfield:
    """The height of the highest lobe over every node of the plate grid, 0 where no tree stands.

    Every (lobe, node of its bounding box) pair is evaluated at once, the way thicken.py samples
    a roof: one pass of numpy for the whole forest.
    """
    grid = canopy_grid(spec)
    if not trees:
        return grid
    ny, nx = grid.z_mm.shape
    cell, (ox, oy) = grid.cell_mm, grid.origin_mm
    lobes = crown_lobes(trees, spec)
    x, y, a = lobes.x, lobes.y, lobes.radius
    i0 = np.clip(np.ceil((x - a - ox) / cell), 0, nx - 1).astype(int)
    i1 = np.clip(np.floor((x + a - ox) / cell), 0, nx - 1).astype(int)
    j0 = np.clip(np.ceil((y - a - oy) / cell), 0, ny - 1).astype(int)
    j1 = np.clip(np.floor((y + a - oy) / cell), 0, ny - 1).astype(int)
    wi, wj = np.clip(i1 - i0 + 1, 0, None), np.clip(j1 - j0 + 1, 0, None)
    count = wi * wj
    t = np.repeat(np.arange(len(x)), count)
    k = np.arange(count.sum()) - np.repeat(np.cumsum(count) - count, count)
    i, j = i0[t] + k % wi[t], j0[t] + k // wi[t]
    r = np.hypot(ox + i * cell - x[t], oy + j * cell - y[t])
    z = cap_height(r, 2 * a[t], lobes.height[t])
    field = np.zeros((ny, nx))
    np.maximum.at(field, (j, i), z)
    return Heightfield(field, cell, grid.origin_mm)


def surface_z(hf: Heightfield, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Height of the relief exactly as grid_solid triangulates it, not bilinear.

    grid_solid splits each cell along its (0, 0)-(1, 1) diagonal. Standing a dome on the bilinear
    surface would leave it a hair above or below the printed one wherever the two differ; on the
    triangles themselves the tree foot and the relief are the same surface.
    """
    ny, nx = hf.z_mm.shape
    fx = np.clip((np.asarray(x, float) - hf.origin_mm[0]) / hf.cell_mm, 0.0, nx - 1.0)
    fy = np.clip((np.asarray(y, float) - hf.origin_mm[1]) / hf.cell_mm, 0.0, ny - 1.0)
    i0 = np.minimum(np.floor(fx).astype(int), nx - 2)
    j0 = np.minimum(np.floor(fy).astype(int), ny - 2)
    tx, ty = fx - i0, fy - j0
    z = hf.z_mm
    za, zb = z[j0, i0], z[j0, i0 + 1]
    zc, zd = z[j0 + 1, i0 + 1], z[j0 + 1, i0]
    lower = za + tx * (zb - za) + ty * (zc - zb)  # triangle (a, b, c), tx >= ty
    upper = za + tx * (zc - zd) + ty * (zd - za)  # triangle (a, c, d), ty >= tx
    return np.where(tx >= ty, lower, upper)
