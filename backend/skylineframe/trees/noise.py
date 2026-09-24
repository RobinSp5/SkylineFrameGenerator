"""Deterministic hashes and smooth noise for the tree shapes, in numpy alone.

No random state anywhere: a value is a splitmix64 hash of integer coordinates and a salt, so the
same place always gets the same value. Noise is value noise (one hash per lattice node, eased in
between) summed over octaves (fBm), and squashed by tanh into (-1, 1). tanh is monotone, so it
moves no extremum and adds none: the narrowest hill is still set by the shortest wavelength.
"""

import numpy as np

_MASK64 = (1 << 64) - 1
GAIN = 2.5  # value noise sums rarely leave +-0.4; this spreads them over most of (-1, 1)


def unit_hash(kx: np.ndarray, ky: np.ndarray, salt: int) -> np.ndarray:
    """A hash of the integer pair (kx, ky) in [0, 1): splitmix64, as layer.py places the trees."""
    z = (np.asarray(kx).astype(np.int64).view(np.uint64) << np.uint64(32)) ^ (
        np.asarray(ky).astype(np.int64).view(np.uint64) & np.uint64(0xFFFFFFFF)
    )
    z = z + np.uint64((salt * 0x9E3779B97F4A7C15) & _MASK64)
    z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    z = z ^ (z >> np.uint64(31))
    return (z >> np.uint64(11)).astype(np.float64) / float(1 << 53)


def value_noise(x: np.ndarray, y: np.ndarray, wavelength: float, salt: int) -> np.ndarray:
    """Smooth noise in [-1, 1] with hills and hollows about `wavelength` apart."""
    fx, fy = np.asarray(x, float) / wavelength, np.asarray(y, float) / wavelength
    ix, iy = np.floor(fx).astype(np.int64), np.floor(fy).astype(np.int64)
    tx, ty = fx - ix, fy - iy
    tx, ty = tx * tx * (3 - 2 * tx), ty * ty * (3 - 2 * ty)
    v00, v10 = unit_hash(ix, iy, salt), unit_hash(ix + 1, iy, salt)
    v01, v11 = unit_hash(ix, iy + 1, salt), unit_hash(ix + 1, iy + 1, salt)
    v = (v00 * (1 - tx) + v10 * tx) * (1 - ty) + (v01 * (1 - tx) + v11 * tx) * ty
    return 2 * v - 1


def fbm(x: np.ndarray, y: np.ndarray, wavelengths: list[float], salt: int = 0) -> np.ndarray:
    """Octaves of value noise, each weighted by the root of its wavelength, squashed into (-1, 1)."""
    weights = np.sqrt(wavelengths) / np.sum(np.sqrt(wavelengths))
    octaves = enumerate(zip(wavelengths, weights, strict=True))
    n = sum(wt * value_noise(x, y, w, salt + 101 * k) for k, (w, wt) in octaves)
    return np.tanh(GAIN * n)


BILLOW_MEAN = 0.365  # mean of |value noise|, measured over a large field; centres the billows


def billow(x: np.ndarray, y: np.ndarray, wavelengths: list[float], salt: int = 0) -> np.ndarray:
    """Cloud noise in (-1, 1): octaves of |value noise|, the billows of a cumulus or a cauliflower.

    Each octave is round where the noise is far from zero and creased where it crosses it, and
    the creases point down, between the billows: nothing narrow ever sticks up. Centred on
    BILLOW_MEAN so that its mean is about 0, and squashed like fbm.
    """
    weights = np.sqrt(wavelengths) / np.sum(np.sqrt(wavelengths))
    octaves = enumerate(zip(wavelengths, weights, strict=True))
    n = sum(wt * np.abs(value_noise(x, y, w, salt + 101 * k)) for k, (w, wt) in octaves)
    return np.tanh(GAIN * (n - BILLOW_MEAN))
