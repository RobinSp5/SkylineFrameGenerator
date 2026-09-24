"""Thicken the parts of a LoD2 body that are too thin to print, in print millimetres.

A church spire at 1:15 000 is a pin of half a millimetre that tapers to a point, and a slicer drops
what a nozzle line cannot fill; Bambu Studio then reports "floating regions" for the rest of it.
Cutting the spire off would take the landmark with it, so it is widened instead: every part keeps
its height, and nothing thinner than a line is left standing (print_optimized, spec 4a §2.2).

A LoD2 body is 2.5D — a roof over its footprint, no overhangs — so it is handled as the height
field of its top surface. A part is thin where a flat disk of the line width cannot be pushed up
to its top (the grey opening of the field), which is true of a spire and of a pinnacle. It is also
true of the ridge of every gabled roof, which prints perfectly well, so a second test lets a cell
pass when a cone of slope MAX_ROOF_SLOPE fits under it: a roof is at most that steep, a spire's
flanks are far steeper. What fails both is raised to the height of the thin part within a line
radius (a grey dilation of the thin cells only) and added to the body, clipped to the body's own
outline, so nothing grows past the walls.
"""

import manifold3d as m3d
import numpy as np
from scipy import ndimage

from .grid import grid_solid
from .lod2.solidify import simplified
from .terrain.heightfield import Heightfield

CELL_MM = 0.1  # grid of the top surface: a quarter of a 0.4 mm nozzle line
# Steepest roof, as print z per print xy, that is not a spire: a 60 degree roof at the default
# z_exaggeration of 1.5 is 2.6. A spire's flanks rise at 10 and more.
MAX_ROOF_SLOPE = 3.0
# How far a part may stand above what the line width and the roof slope carry before it counts as
# thin: half a 0.2 mm layer. Measured on the Dreikönigskirche, whose side chapels carry a row of
# 0.4 mm gables that rise 0.1 to 0.3 mm above that: at 0.2 or 0.4 Bambu Studio still reports a
# "floating cantilever" there, at 0.1 the Frankfurt square slices clean.
TOLERANCE_MM = 0.1
# A cell is raised when the thickening lifts it by more than this, float noise and no more. Not
# TOLERANCE_MM: a cell that stayed just below the raised top would leave a ring of notches around
# the spire, thin parts of the thickening's own making.
_RAISE_MM = 0.01
# The grid piece is two triangles per cell, flat plateaus included; merged down at this tolerance
# a thickened body carries about half the triangles, and the line width loses nothing that shows.
_FLAT_MM = 0.01
_BELOW_MM = 1.0  # the grid piece away from the thin parts sinks below z = 0 and is cut off there


def _disk(radius_mm: float, cell_mm: float) -> tuple[np.ndarray, np.ndarray]:
    """A disk footprint on the grid and the distance of each of its cells from the centre, in mm."""
    k = int(np.floor(radius_mm / cell_mm + 1e-9))
    y, x = np.mgrid[-k : k + 1, -k : k + 1] * cell_mm
    dist = np.hypot(x, y)
    return dist <= radius_mm + 1e-9, dist


def _top_surface(
    verts: np.ndarray, tris: np.ndarray, origin: tuple[float, float], shape: tuple[int, int], cell_mm: float
) -> tuple[np.ndarray, np.ndarray]:
    """(height, peak) per grid node: the highest upward-facing triangle over the node, 0 where the
    body does not reach, and the highest vertex nearest to the node.

    The peak is what a thin part is raised to. A spire's apex falls between the nodes, and its
    flanks drop so steeply that the nearest node sits half a millimetre below it; raised to that,
    the widened spire would end short of its own tip.
    """
    v = verts[tris]
    e1, e2 = v[:, 1] - v[:, 0], v[:, 2] - v[:, 0]
    v = v[e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0] > 1e-12]  # facing up, not a wall
    fx = (v[:, :, 0] - origin[0]) / cell_mm
    fy = (v[:, :, 1] - origin[1]) / cell_mm
    i0, i1 = np.ceil(fx.min(1)).astype(int), np.floor(fx.max(1)).astype(int)
    j0, j1 = np.ceil(fy.min(1)).astype(int), np.floor(fy.max(1)).astype(int)
    wi, wj = np.clip(i1 - i0 + 1, 0, None), np.clip(j1 - j0 + 1, 0, None)
    n = wi * wj
    # Every (triangle, node of its bounding box) pair at once: one pass of numpy per body.
    t = np.repeat(np.arange(len(v)), n)
    k = np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)
    i, j = i0[t] + k % wi[t], j0[t] + k // wi[t]
    ax, ay, bx, by, cx, cy = fx[t, 0], fy[t, 0], fx[t, 1], fy[t, 1], fx[t, 2], fy[t, 2]
    den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    l1 = ((by - cy) * (i - cx) + (cx - bx) * (j - cy)) / den
    l2 = ((cy - ay) * (i - cx) + (ax - cx) * (j - cy)) / den
    l3 = 1.0 - l1 - l2
    inside = (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)
    z = l1 * v[t, 0, 2] + l2 * v[t, 1, 2] + l3 * v[t, 2, 2]
    h = np.zeros(shape)
    np.maximum.at(h, (j[inside], i[inside]), z[inside])
    peak = h.copy()
    np.maximum.at(peak, (np.rint(fy).astype(int).ravel(), np.rint(fx).astype(int).ravel()), v[:, :, 2].ravel())
    return h, peak


def _eroded(h: np.ndarray, covered: np.ndarray, footprint: np.ndarray, structure: np.ndarray | None = None) -> np.ndarray:
    """Grey erosion over the body only, 0 off it.

    Outside the body the erosion sees +inf, so a disk that hangs over the wall still fits: a thin
    body is prepare's business (it widens the footprint), a thin part on a roof is ours.
    """
    eroded = ndimage.grey_erosion(np.where(covered, h, np.inf), footprint=footprint, structure=structure)
    return np.where(covered, eroded, 0.0)


def thickened(solid: m3d.Manifold, min_line_mm: float, cell_mm: float = CELL_MM) -> m3d.Manifold:
    """`solid` with every part thinner than min_line_mm widened to it; `solid` itself when none is."""
    mesh = solid.to_mesh()
    verts = np.asarray(mesh.vert_properties, dtype=float)[:, :3]
    tris = np.asarray(mesh.tri_verts, dtype=np.int64)
    radius = min_line_mm / 2
    line, _ = _disk(radius, cell_mm)
    near, _ = _disk(2 * radius, cell_mm)
    pad = near.shape[0]
    x0, y0 = verts[:, 0].min() - pad * cell_mm, verts[:, 1].min() - pad * cell_mm
    shape = (
        int(np.ceil((verts[:, 1].max() - y0) / cell_mm)) + pad + 1,
        int(np.ceil((verts[:, 0].max() - x0) / cell_mm)) + pad + 1,
    )
    h, peak = _top_surface(verts, tris, (x0, y0), shape, cell_mm)
    covered = h > 0
    # Flat disk first: it passes every flat roof and every wall, and most bodies end here. The
    # opening is dilated by `near` instead of `line`, one more line radius: a disk cannot reach
    # into the corner of a tower, and a corner next to a wide part at its height is not thin.
    thin = covered & (h > ndimage.grey_dilation(_eroded(h, covered, line), footprint=near) + TOLERANCE_MM)
    if not thin.any():
        return solid
    _, dist = _disk(radius, cell_mm)
    cone = np.where(line, -MAX_ROOF_SLOPE * dist, 0.0)
    roof = ndimage.grey_dilation(_eroded(h, covered, line, cone), footprint=line, structure=cone)
    thin &= h > roof + TOLERANCE_MM
    if not thin.any():
        return solid
    # One cell more than the line radius. The mesh runs straight from node to node, so a cell only
    # stands at full height when all four of its corners are raised, and a disk of exactly the
    # radius leaves a staircase inside the line: measured, a tapering spire then held a disk of
    # only 0.74 mm below its tip, with the extra cell 0.81 mm.
    reach, _ = _disk(radius + cell_mm, cell_mm)
    raised = ndimage.grey_dilation(np.where(thin, peak, 0.0), footprint=reach)
    grow = covered & (raised > h + _RAISE_MM)
    if not grow.any():
        return solid
    # Only the window around the growth becomes a mesh, one cell of margin for the slope down.
    rows, cols = np.nonzero(grow)
    r0, r1 = max(rows.min() - 1, 0), min(rows.max() + 2, shape[0])
    c0, c1 = max(cols.min() - 1, 0), min(cols.max() + 2, shape[1])
    top = np.where(grow, raised, -_BELOW_MM)[r0:r1, c0:c1]
    hf = Heightfield(top, cell_mm, (x0 + c0 * cell_mm, y0 + r0 * cell_mm))
    piece = grid_solid(hf, top, -2 * _BELOW_MM)
    outline = solid.project().extrude(float(top.max()) + 1.0)
    out = simplified(solid + (piece ^ outline), _FLAT_MM)
    if out.status() != m3d.Error.NoError or out.is_empty():
        return solid
    return out
