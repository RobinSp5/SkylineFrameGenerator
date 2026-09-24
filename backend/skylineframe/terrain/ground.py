"""Bare ground from a surface model (spec 4b §4.5).

Copernicus GLO-30 is a surface model: trees, tower blocks and bridges are part of its heights. A
grey opening with a window of about 100 m removes everything narrower than that — a tree line, a
high-rise — while a hill, hundreds of metres across, keeps its shape. The opening leaves flat
steps where it cut, so a mean over the same window smooths them out again.
"""

import numpy as np
from scipy.ndimage import grey_opening, uniform_filter

GROUND_WINDOW_M = 100.0


def window_px(pixel_m: float) -> int:
    """Pixels in a GROUND_WINDOW_M window: odd, so it has a centre, and at least 3."""
    if pixel_m <= 0:
        raise ValueError(f"pixel size must be positive, got {pixel_m}")
    n = int(round(GROUND_WINDOW_M / pixel_m))
    if n % 2 == 0:
        n += 1
    return max(3, n)


def footprint(row_m: float, col_m: float) -> np.ndarray:
    """The opening's structuring element: the ellipse inscribed in the window, a disc in metres.

    Round rather than square, so a ridge running diagonally across the raster loses no more
    height than one running along it, and the model does not depend on the square's rotation.
    A square window cuts a 500 m hill to about 82 % at 10 m pixels; the disc keeps about 88 %.
    """
    n_rows, n_cols = window_px(row_m), window_px(col_m)
    di = (np.arange(n_rows) - n_rows // 2) / (n_rows / 2)
    dj = (np.arange(n_cols) - n_cols // 2) / (n_cols / 2)
    return di[:, None] ** 2 + dj[None, :] ** 2 <= 1.0


def ground_surface(dem: np.ndarray, pixel_m: float | tuple[float, float]) -> np.ndarray:
    """The ground under a surface model, as a new float64 array of the same shape.

    `pixel_m` is the pixel size in metres, either one value or (along rows, along columns) for
    the non-square pixels of a lat/lon raster. Within a window of the raster edge the result is
    biased towards flat ("nearest" padding), so callers pad what they need by more than that.
    """
    row_m, col_m = (pixel_m, pixel_m) if np.isscalar(pixel_m) else pixel_m
    fp = footprint(float(row_m), float(col_m))
    surface = np.asarray(dem, dtype=np.float64)
    # mode="nearest": the raster edge continues flat instead of mirroring a hill into itself.
    opened = grey_opening(surface, footprint=fp, mode="nearest")
    return uniform_filter(opened, size=fp.shape, mode="nearest")
