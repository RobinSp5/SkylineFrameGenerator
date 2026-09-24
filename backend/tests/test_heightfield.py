import numpy as np
import pytest

from skylineframe.terrain.heightfield import Heightfield


def test_flat_covers_the_plate_edge_to_edge():
    hf = Heightfield.flat(100.0, 0.5)
    assert hf.z_mm.shape == (201, 201)
    assert hf.origin_mm == (-50.0, -50.0)
    assert hf.origin_mm[0] + (hf.z_mm.shape[1] - 1) * hf.cell_mm == pytest.approx(50.0)


def test_sample_is_bilinear_and_rows_run_along_y():
    # z = x + 10 y on a 2x3 grid with 1 mm cells, origin (0, 0).
    z = np.array([[0.0, 1.0, 2.0], [10.0, 11.0, 12.0]])
    hf = Heightfield(z, 1.0, (0.0, 0.0))
    assert hf.sample(1.5, 0.5) == pytest.approx(6.5)
    assert hf.sample(np.array([0.0, 2.0]), np.array([1.0, 0.0])).tolist() == [10.0, 2.0]


def test_sample_clamps_outside_the_grid():
    z = np.array([[0.0, 1.0], [2.0, 3.0]])
    hf = Heightfield(z, 1.0, (0.0, 0.0))
    assert hf.sample(-5.0, -5.0) == pytest.approx(0.0)
    assert hf.sample(9.0, 9.0) == pytest.approx(3.0)
