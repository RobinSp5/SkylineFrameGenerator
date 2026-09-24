"""Where the trees stand: WorldCover tree cover plus OSM trees, as Trees in print mm (spec 6 §4).

Placement: the square is cut into TREE_CROWN_M cells in local metres, and every cell gets one
candidate point, jittered inside the middle half of the cell by a hash of the cell index. No
random state: the same square always gets the same trees, and two neighbours are never closer
than half a crown. A candidate is a tree when the WorldCover pixel under it is tree cover.

That alone would miss a lone street-tree pixel: at 50 N a pixel is 9 x 6 m, smaller than a cell,
and the candidate may land next to it. So a cell whose candidate is no tree, but which contains
the centre of a tree pixel, puts its tree on the tree-pixel centre nearest its candidate. Every
tree pixel whose centre lies in the square therefore carries a tree, and no cell carries two.

An OSM tree is mapped, a WorldCover tree is inferred: an OSM tree removes every WorldCover tree
within its crown radius and stands in their place, with its own height and crown when tagged.
"""

import logging
import math
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
from scipy.spatial import cKDTree

from ..project import local_transformer
from ..spec import FrameSpec
from ..terrain.dem import TileError
from .defaults import TREE_CROWN_M, TREE_HEIGHT_JITTER, TREE_HEIGHT_M
from .model import OsmTree, Tree
from .worldcover import TIMEOUT_S, TreeCover, tree_cover

log = logging.getLogger(__name__)

SOURCE_WORLDCOVER = "worldcover"
_MASK64 = (1 << 64) - 1


@dataclass(frozen=True)
class TreeLayer:
    trees: list[Tree]
    source: str  # "worldcover" when WorldCover trees are among them, else ""
    # Set when WorldCover could not be read; the pipeline passes it on as stats["trees_note"].
    note: str = ""


# --- frames ------------------------------------------------------------------


def _to_lonlat(spec: FrameSpec, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Local metres (the rotated print frame) -> WGS84; project.to_local backwards."""
    a = math.radians(spec.rotation_deg)
    east = x * math.cos(a) + y * math.sin(a)
    north = -x * math.sin(a) + y * math.cos(a)
    lon, lat = local_transformer(spec).transform(east, north, direction="INVERSE")
    return np.asarray(lon), np.asarray(lat)


def _to_local(spec: FrameSpec, lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """WGS84 -> local metres, as project.to_local does it."""
    east, north = local_transformer(spec).transform(lon, lat)
    east, north = np.asarray(east), np.asarray(north)
    a = math.radians(spec.rotation_deg)
    return east * math.cos(a) - north * math.sin(a), east * math.sin(a) + north * math.cos(a)


# --- placement -----------------------------------------------------------------


def _unit(i: np.ndarray, j: np.ndarray, salt: int) -> np.ndarray:
    """A hash of the cell (i, j) in [0, 1): splitmix64 of the packed index. Same cell, same value."""
    z = (i.astype(np.int64).view(np.uint64) << np.uint64(32)) ^ (
        j.astype(np.int64).view(np.uint64) & np.uint64(0xFFFFFFFF)
    )
    z = z + np.uint64((salt * 0x9E3779B97F4A7C15) & _MASK64)
    z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    z = z ^ (z >> np.uint64(31))
    return (z >> np.uint64(11)).astype(np.float64) / float(1 << 53)


def _place(spec: FrameSpec, cover: TreeCover) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(x, y, height) in local metres of the WorldCover trees inside the square."""
    s, half = TREE_CROWN_M, spec.side_m / 2
    index = np.arange(math.floor(-half / s), math.ceil(half / s))
    n = len(index)
    ci, cj = (a.ravel() for a in np.meshgrid(index, index, indexing="ij"))  # cell k = a * n + b
    x = (ci + 0.25 + 0.5 * _unit(ci, cj, 1)) * s
    y = (cj + 0.25 + 0.5 * _unit(ci, cj, 2)) * s
    height = TREE_HEIGHT_M * (1 + TREE_HEIGHT_JITTER * (2 * _unit(ci, cj, 3) - 1))

    inside = (np.abs(x) <= half) & (np.abs(y) <= half)
    placed = inside & cover.is_tree(*_to_lonlat(spec, x, y))

    # Cells without a tree that hold the centre of a tree pixel: the lone street tree.
    px, py = _to_local(spec, *cover.tree_pixels())
    keep = (np.abs(px) <= half) & (np.abs(py) <= half)
    px, py = px[keep], py[keep]
    a = np.clip(np.floor(px / s).astype(np.int64) - index[0], 0, n - 1)
    b = np.clip(np.floor(py / s).astype(np.int64) - index[0], 0, n - 1)
    cell = a * n + b
    free = ~placed[cell]
    px, py, cell = px[free], py[free], cell[free]
    d2 = (px - x[cell]) ** 2 + (py - y[cell]) ** 2
    order = np.lexsort((d2, cell))  # by cell, nearest to the candidate first
    _cells, first = np.unique(cell[order], return_index=True)
    pick = order[first]

    return (
        np.concatenate([x[placed], px[pick]]),
        np.concatenate([y[placed], py[pick]]),
        np.concatenate([height[placed], height[cell[pick]]]),
    )


def _worldcover_trees(
    spec: FrameSpec, cache_dir: Path, client: httpx.Client | None
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Placed WorldCover trees in local metres, or None when WorldCover could not be read."""
    owns_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT_S, follow_redirects=True)
    try:
        cover = tree_cover(spec, cache_dir, client)
    # OSError: a cache directory that cannot be created or written is no reason to fail the run.
    except (TileError, OSError) as exc:
        log.warning("Trees: ESA WorldCover not available (%s); OpenStreetMap trees only", exc)
        return None
    finally:
        if owns_client:
            client.close()
    return _place(spec, cover)


def tree_layer(
    spec: FrameSpec, osm_trees: list[OsmTree], cache_dir: Path, client: httpx.Client | None = None
) -> TreeLayer:
    """Every tree whose centre lies in the square, in print millimetres (spec 6 §3/§4).

    osm_trees are in local metres (after project_features). Never raises for missing WorldCover
    data: the layer then holds the OSM trees alone and its source is "".
    """
    half = spec.side_m / 2
    placed = _worldcover_trees(spec, cache_dir, client)
    note = "" if placed is not None else "Tree cover not available, OpenStreetMap trees only"
    empty = np.empty(0)
    x, y, height = placed if placed is not None else (empty, empty, empty)
    osm = [t for t in osm_trees if abs(t.x) <= half and abs(t.y) <= half]

    keep = np.ones(len(x), dtype=bool)
    if osm and len(x):
        radii = np.array([(t.crown_m or TREE_CROWN_M) / 2 for t in osm])
        hits = cKDTree(np.column_stack([x, y])).query_ball_point([(t.x, t.y) for t in osm], r=radii)
        for hit in hits:
            keep[hit] = False

    scale, z = spec.scale, spec.scale * spec.z_exaggeration
    crown_mm = TREE_CROWN_M * scale
    trees = [
        Tree(x_mm=float(xi) * scale, y_mm=float(yi) * scale, crown_mm=crown_mm, height_mm=float(hi) * z)
        for xi, yi, hi in zip(x[keep], y[keep], height[keep])
    ]
    source = SOURCE_WORLDCOVER if trees else ""
    trees += [
        Tree(
            x_mm=t.x * scale,
            y_mm=t.y * scale,
            crown_mm=(t.crown_m or TREE_CROWN_M) * scale,
            height_mm=(t.height_m or TREE_HEIGHT_M) * z,
        )
        for t in osm
    ]
    return TreeLayer(trees=trees, source=source, note=note)
