"""Closed solids between two surfaces over a regular grid, built from arrays instead of booleans.

The terrain plate and every layer on it are such solids (spec 4b §5), and so is the thickening of a
thin LoD2 spire (scale.py). Kept out of mesh.py so scale.py can use it: mesh.py imports scale.py.
"""

import manifold3d as m3d
import numpy as np

from .terrain.heightfield import Heightfield

Surface = np.ndarray | float  # a height per grid node, or one constant height


def _surface(hf: Heightfield, z: Surface, facing_up: bool, offset: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One horizontal side of a grid solid: (vertices, triangles, perimeter ring).

    A grid surface gets two triangles per cell. A constant one only needs its perimeter, and a
    fan from its centre closes it: the flat bottom of a relief plate would otherwise carry as
    many triangles as its top for nothing. The ring runs counter-clockwise seen from above and
    has one vertex per grid node on the plate edge either way, so the walls between two sides
    always pair up vertex for vertex.
    """
    ny, nx = hf.z_mm.shape
    xs = hf.origin_mm[0] + np.arange(nx) * hf.cell_mm
    ys = hf.origin_mm[1] + np.arange(ny) * hf.cell_mm
    # Perimeter grid nodes (j, i), counter-clockwise from the south-west corner, no repeats.
    ring_j = np.concatenate([np.zeros(nx - 1, int), np.arange(ny - 1), np.full(nx - 1, ny - 1), np.arange(ny - 1, 0, -1)])
    ring_i = np.concatenate([np.arange(nx - 1), np.full(ny - 1, nx - 1), np.arange(nx - 1, 0, -1), np.zeros(ny - 1, int)])
    if isinstance(z, np.ndarray):
        gx, gy = np.meshgrid(xs, ys)
        verts = np.column_stack([gx.ravel(), gy.ravel(), z.ravel()])
        node = np.arange(ny * nx).reshape(ny, nx)
        a, b = node[:-1, :-1].ravel(), node[:-1, 1:].ravel()
        c, d = node[1:, 1:].ravel(), node[1:, :-1].ravel()
        tris = np.concatenate([np.column_stack([a, b, c]), np.column_stack([a, c, d])])
        ring = node[ring_j, ring_i]
    else:
        n = len(ring_j)
        verts = np.column_stack([xs[ring_i], ys[ring_j], np.full(n, float(z))])
        verts = np.vstack([verts, [xs.mean(), ys.mean(), float(z)]])
        k = np.arange(n)
        tris = np.column_stack([np.full(n, n), k, (k + 1) % n])
        ring = k
    if not facing_up:
        tris = tris[:, ::-1]
    return verts, tris + offset, ring + offset


def grid_solid(hf: Heightfield, top: Surface, bottom: Surface) -> m3d.Manifold:
    """The closed solid between two surfaces over the whole grid, with vertical walls.

    Built from arrays rather than booleans: a relief plate is one mesh of a few hundred thousand
    triangles, and every layer below (sockel, groove, inlay) is the same grid shifted, so it
    costs no more than the plate itself. Callers keep top above bottom everywhere.
    """
    top_v, top_t, top_ring = _surface(hf, top, facing_up=True, offset=0)
    bot_v, bot_t, bot_ring = _surface(hf, bottom, facing_up=False, offset=len(top_v))
    # Walls: the rings run counter-clockwise, so (b_k, b_k+1, t_k+1) faces outwards.
    t0, t1 = top_ring, np.roll(top_ring, -1)
    b0, b1 = bot_ring, np.roll(bot_ring, -1)
    walls = np.concatenate([np.column_stack([b0, b1, t1]), np.column_stack([b0, t1, t0])])
    mesh = m3d.Mesh(
        vert_properties=np.ascontiguousarray(np.vstack([top_v, bot_v]), dtype=np.float32),
        tri_verts=np.ascontiguousarray(np.concatenate([top_t, bot_t, walls]), dtype=np.uint32),
    )
    return m3d.Manifold(mesh)
