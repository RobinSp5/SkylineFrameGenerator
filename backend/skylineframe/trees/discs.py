"""Grid basics shared by the lone crowns (geometry.py) and the forest canopy (forest.py)."""

import numpy as np

from ..terrain.heightfield import Heightfield

# A grid node where the canopy is lower than this counts as ground. On terrain the canopy sits on
# a relief sampled on a coarser grid, and a rim lower than this would leave a skin a few microns
# above the surface, too thin to print and a source of slivers in the union.
MIN_CAP_MM = 0.05


def disc_nodes(
    x: np.ndarray, y: np.ndarray, radius: np.ndarray, grid: Heightfield
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Every (disc, grid node in its bounding box) pair at once: disc index, node column, node
    row and the node's distance from the disc centre. One pass of numpy for a whole forest, the
    way thicken.py samples a roof."""
    ny, nx = grid.z_mm.shape
    cell, (ox, oy) = grid.cell_mm, grid.origin_mm
    i0 = np.clip(np.ceil((x - radius - ox) / cell), 0, nx - 1).astype(int)
    i1 = np.clip(np.floor((x + radius - ox) / cell), 0, nx - 1).astype(int)
    j0 = np.clip(np.ceil((y - radius - oy) / cell), 0, ny - 1).astype(int)
    j1 = np.clip(np.floor((y + radius - oy) / cell), 0, ny - 1).astype(int)
    wi, wj = np.clip(i1 - i0 + 1, 0, None), np.clip(j1 - j0 + 1, 0, None)
    count = wi * wj
    t = np.repeat(np.arange(len(x)), count)
    k = np.arange(count.sum()) - np.repeat(np.cumsum(count) - count, count)
    i, j = i0[t] + k % wi[t], j0[t] + k // wi[t]
    r = np.hypot(ox + i * cell - x[t], oy + j * cell - y[t])
    return t, i, j, r
