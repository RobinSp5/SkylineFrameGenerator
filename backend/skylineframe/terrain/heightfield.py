"""The terrain surface in print space: the one interface between the DEM data and the mesh (spec 4b §3).

Deliberately free of any network or raster import: mesh.py and its tests only ever need this class.
"""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Heightfield:
    """Terrain height above the plate top on a regular grid over the whole plate.

    z_mm[j, i] is the height at x = origin_mm[0] + i * cell_mm, y = origin_mm[1] + j * cell_mm, so
    rows run along +y and columns along +x, in the same centred print frame as every Prism. The
    grid covers the plate edge to edge (origin_mm = (-size/2, -size/2), last node at +size/2), and
    its lowest node is exactly 0.0: the lowest point of the city sits on the plate top of today's
    flat model, and plate_thickness_mm stays below it.
    """

    z_mm: np.ndarray  # shape (ny, nx), float64
    cell_mm: float
    origin_mm: tuple[float, float]

    def sample(self, x_mm: np.ndarray | float, y_mm: np.ndarray | float) -> np.ndarray:
        """Bilinear height at the given print coordinates; points off the grid are clamped to its edge."""
        x = np.asarray(x_mm, dtype=float)
        y = np.asarray(y_mm, dtype=float)
        ny, nx = self.z_mm.shape
        fx = np.clip((x - self.origin_mm[0]) / self.cell_mm, 0.0, nx - 1.0)
        fy = np.clip((y - self.origin_mm[1]) / self.cell_mm, 0.0, ny - 1.0)
        i0 = np.minimum(np.floor(fx).astype(int), nx - 2) if nx > 1 else np.zeros_like(fx, dtype=int)
        j0 = np.minimum(np.floor(fy).astype(int), ny - 2) if ny > 1 else np.zeros_like(fy, dtype=int)
        tx = fx - i0
        ty = fy - j0
        i1 = np.minimum(i0 + 1, nx - 1)
        j1 = np.minimum(j0 + 1, ny - 1)
        z = self.z_mm
        return (
            z[j0, i0] * (1 - tx) * (1 - ty)
            + z[j0, i1] * tx * (1 - ty)
            + z[j1, i0] * (1 - tx) * ty
            + z[j1, i1] * tx * ty
        )

    @classmethod
    def flat(cls, plate_size_mm: float, cell_mm: float = 0.5) -> "Heightfield":
        """An all-zero field, the terrain of today's flat plate. Handy in tests."""
        n = int(round(plate_size_mm / cell_mm)) + 1
        half = plate_size_mm / 2
        return cls(np.zeros((n, n)), plate_size_mm / (n - 1), (-half, -half))
