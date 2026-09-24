"""The shape of a crown: a cloud of overlapping domes instead of one smooth dome.

A real crown in miniature reads as a cauliflower: a top lobe near the middle, a ring of smaller,
lower lobes round it and a low base lobe that gives the crown its body. How many side lobes there
are, where they sit, how wide and how high they are, and a slight shrink of the whole tree all
come from a hash of the tree's position: the same tree always gets the same crown, two trees
never get the same one, and there is no random state.

Dense trees are a forest and print as one canopy instead (forest.py); these crowns are for the
trees that stand alone, in a row or in a park.

Printable by construction (spec 6 §2.3):
- every lobe is a dome at least a line wide at its base (radius >= min_line_mm / 2), so no lobe
  is a pin sticking up on its own;
- the base lobe alone is as wide as the narrowest printed crown, so the crown still is;
- every lobe stays inside the crown the fitting cleared, and the crown only ever shrinks, down
  to the printed minimum and never below it; the same holds for the height;
- the canopy is still the maximum of domes on one grid, a height field, so nothing overhangs.
"""

from typing import NamedTuple

import numpy as np

from ..spec import FrameSpec
from .model import Tree
from .noise import unit_hash

LOBES_MIN, LOBES_MAX = 3, 6  # side lobes round the top one
SIZE_JITTER = 0.1  # a crown is 0-10 % narrower than the data
HEIGHT_JITTER = 0.1  # and 0-10 % lower


class Lobes(NamedTuple):
    """Every lobe of every crown, flat: centre, base radius and top height, and whose it is."""

    x: np.ndarray
    y: np.ndarray
    radius: np.ndarray
    height: np.ndarray
    tree: np.ndarray


def min_crown_mm(spec: FrameSpec) -> float:
    """Narrowest printed crown: a dome is wider than a line at the top, so its base must be wider
    still (spec 6 §2.3). Without print optimization, one line."""
    return spec.min_line_mm * 1.25 if spec.print_optimized else spec.min_line_mm


def min_tree_height_mm(spec: FrameSpec) -> float:
    """Lowest printed tree: lower than the lowest house, but still a visible bump."""
    return spec.min_building_height_mm * 0.5 if spec.print_optimized else 0.0


def crown_lobes(trees: list[Tree], spec: FrameSpec) -> Lobes:
    """The lobes of every crown: a base, a top and LOBES_MIN..LOBES_MAX side lobes per tree."""
    n = len(trees)
    x = np.array([t.x_mm for t in trees], dtype=float)
    y = np.array([t.y_mm for t in trees], dtype=float)
    a = np.array([t.crown_mm / 2 for t in trees], dtype=float)
    h = np.array([t.height_mm for t in trees], dtype=float)
    # Keyed on the position to the micrometre: stable, and distinct for any two trees.
    kx, ky = np.round(x * 1000).astype(np.int64), np.round(y * 1000).astype(np.int64)

    def u(salt: int) -> np.ndarray:
        return unit_hash(kx, ky, salt)

    # The whole tree a little smaller and lower, never below what was printable before.
    a = np.maximum(a * (1 - SIZE_JITTER * u(1)), np.minimum(a, min_crown_mm(spec) / 2))
    h = np.maximum(h * (1 - HEIGHT_JITTER * u(2)), np.minimum(h, min_tree_height_mm(spec)))
    r_min = np.minimum(spec.min_line_mm / 2, a)

    def lobe_radius(share: np.ndarray) -> np.ndarray:
        return np.clip(a * share, r_min, a)

    # Base: centred, low, and on its own as wide as the narrowest printed crown.
    base_r = np.clip(np.maximum(0.7 * a, min_crown_mm(spec) / 2), r_min, a)
    parts = [(x, y, base_r, h * (0.55 + 0.15 * u(3)), np.arange(n))]
    # Top: the highest lobe, a little off the middle.
    top_r = lobe_radius(0.45 + 0.15 * u(4))
    off, turn = np.minimum(a - top_r, 0.2 * a) * u(5), 2 * np.pi * u(6)
    parts.append((x + off * np.cos(turn), y + off * np.sin(turn), top_r, h, np.arange(n)))
    # Side lobes: spread round the crown, each touching its rim, lower than the top.
    count = LOBES_MIN + np.floor((LOBES_MAX - LOBES_MIN + 1) * u(7)).astype(int)
    start = 2 * np.pi * u(8)
    for k in range(LOBES_MAX):
        has = k < count
        r = lobe_radius(0.32 + 0.2 * u(10 + 4 * k))
        turn = start + 2 * np.pi * (k + 0.35 * (u(11 + 4 * k) - 0.5)) / count
        d = a - r
        z = h * (0.55 + 0.3 * u(12 + 4 * k))
        parts.append(((x + d * np.cos(turn))[has], (y + d * np.sin(turn))[has], r[has], z[has], np.flatnonzero(has)))
    return Lobes(*(np.concatenate(column) for column in zip(*parts, strict=True)))
