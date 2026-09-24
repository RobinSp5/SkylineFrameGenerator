import numpy as np
import pytest

from skylineframe.terrain.ground import GROUND_WINDOW_M, footprint, ground_surface, window_px


def _grid(n: int, pixel_m: float) -> tuple[np.ndarray, np.ndarray]:
    """Metric coordinates of an n x n grid centred on the middle pixel."""
    axis = (np.arange(n) - n // 2) * pixel_m
    return np.meshgrid(axis, axis, indexing="ij")


def _raised_cosine(r: np.ndarray, width_m: float, height_m: float) -> np.ndarray:
    """A smooth round hill: height at the centre, zero from r = width / 2 outwards."""
    inside = r < width_m / 2
    return np.where(inside, height_m * np.cos(np.pi * r / width_m) ** 2, 0.0)


def _bell(r: np.ndarray, width_m: float, height_m: float) -> np.ndarray:
    """A Gaussian hill, width_m wide at half its height."""
    sigma = width_m / (2 * np.sqrt(2 * np.log(2)))
    return height_m * np.exp(-(r**2) / (2 * sigma**2))


# --- window --------------------------------------------------------------


def test_the_window_is_about_100_m():
    assert GROUND_WINDOW_M == 100.0
    assert window_px(10.0) == 11  # 10 px would be even
    assert window_px(20.0) == 5
    assert window_px(25.0) == 5  # 4 is even


def test_the_window_is_odd_and_at_least_three_pixels():
    for pixel_m in (0.5, 1.0, 7.0, 12.5, 30.0, 33.3, 71.5, 250.0, 5000.0):
        n = window_px(pixel_m)
        assert n % 2 == 1
        assert n >= 3
    # Copernicus GLO-30: 100 m is only 3.3 pixels, so the smallest window it can have.
    assert window_px(30.9) == 3


def test_a_nonpositive_pixel_is_rejected():
    with pytest.raises(ValueError):
        window_px(0.0)


# --- filter --------------------------------------------------------------


@pytest.mark.parametrize("pixel_m", [10.0, 30.0])
def test_a_60_m_wide_30_m_tall_bump_is_removed(pixel_m):
    # A tree line or a tower block in the surface model: it has no business lifting the ground.
    x, y = _grid(61, pixel_m)
    dem = np.full(x.shape, 120.0)
    # Half-open, so the block is exactly 60 m: 2 pixels at 30 m, 6 at 10 m.
    inside = (x >= -30.0) & (x < 30.0) & (y >= -30.0) & (y < 30.0)
    dem[inside] += 30.0
    ground = ground_surface(dem, pixel_m)
    assert ground.shape == dem.shape
    assert np.max(np.abs(ground - 120.0)) < 3.0


@pytest.mark.parametrize("pixel_m", [10.0, 30.0])
def test_a_500_m_wide_100_m_tall_hill_keeps_its_height(pixel_m):
    x, y = _grid(151, pixel_m)
    dem = 200.0 + _bell(np.hypot(x, y), 500.0, 100.0)
    ground = ground_surface(dem, pixel_m)
    assert ground.max() - 200.0 >= 90.0
    assert ground.max() <= dem.max() + 1e-9  # an opening and a mean never add height


def test_even_a_hill_500_m_across_at_its_foot_keeps_its_height_at_glo30_resolution():
    # The stricter reading of "500 m wide": zero height 250 m from the top. At the 30.9 m
    # pixels the DEM actually has, the 3-pixel window only shaves the very top.
    x, y = _grid(61, 30.9)
    dem = _raised_cosine(np.hypot(x, y), 500.0, 100.0)
    assert ground_surface(dem, 30.9).max() >= 90.0


def test_a_hill_keeps_its_height_on_anisotropic_pixels():
    # GLO-30 pixels are 30.9 m north-south but only ~20 m east-west at 50 N, band 1/3600.
    row_m, col_m = 30.9, 20.0
    rows = (np.arange(81) - 40) * row_m
    cols = (np.arange(121) - 60) * col_m
    y, x = np.meshgrid(rows, cols, indexing="ij")
    dem = _bell(np.hypot(x, y), 500.0, 100.0)
    assert ground_surface(dem, (row_m, col_m)).max() >= 90.0


def test_the_footprint_is_round_in_metres():
    # A square window would cut a ridge running diagonally harder than one running north-south,
    # so the model would depend on the rotation of the square.
    fp = footprint(10.0, 10.0)
    assert fp.shape == (11, 11)
    assert fp[5, 5] and fp[0, 5] and fp[5, 0] and fp[10, 5] and fp[5, 10]
    assert not fp[0, 0] and not fp[10, 10]
    np.testing.assert_array_equal(fp, fp.T)
    # Anisotropic pixels: the same 100 m in both directions, so fewer pixels along the longer one.
    assert footprint(30.9, 10.0).shape == (3, 11)
    # Three pixels is the smallest window; there every pixel is within reach.
    assert footprint(30.0, 30.0).all()


def test_a_plane_is_left_as_it_is():
    x, y = _grid(41, 30.0)
    dem = 150.0 + 0.05 * x - 0.02 * y
    # Away from the raster edge, where "nearest" padding flattens the slope; the caller pads the
    # raster by more than a window for exactly this reason.
    np.testing.assert_allclose(ground_surface(dem, 30.0)[3:-3, 3:-3], dem[3:-3, 3:-3], atol=1e-9)


def test_the_input_is_not_modified():
    dem = np.zeros((21, 21))
    dem[10, 10] = 50.0
    before = dem.copy()
    ground_surface(dem, 30.0)
    np.testing.assert_array_equal(dem, before)


def test_the_result_is_float64():
    dem = np.full((9, 9), 100.0, dtype=np.float32)
    assert ground_surface(dem, 30.0).dtype == np.float64
