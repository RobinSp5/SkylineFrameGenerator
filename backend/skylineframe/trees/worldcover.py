"""ESA WorldCover 10 m tiles -> which ground under the square is tree cover (spec 6 §4.1).

WorldCover v200 (2021) is one land-cover class per pixel, 1/12 000 degree, published as 3 x 3
degree Cloud-Optimised GeoTIFFs in a public S3 bucket (57-94 MB each). Like the DEM, a tile is
downloaded whole into the cache and read back only in the internal tiles the square touches.
Download, cache, georeference and window reading are the DEM's (terrain/dem.py); only the
naming, the size limit and what is read out differ. Class 10 is "Tree cover".

The classes are categorical: a point takes the class of the pixel it lies in, nothing is
interpolated.
"""

import logging
import math
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
import tifffile

from ..project import query_bbox
from ..spec import FrameSpec
from ..terrain.dem import _OCEAN, TileError, _Georef, _download, _georef, _read_window

log = logging.getLogger(__name__)

TILE_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/{name}.tif"
TILE_DEG = 3
TREE_CLASS = 10
CACHE_DIRNAME = "worldcover"
# Measured: N39W075 is 56.9 MB, N48E006 94 MB. Room for denser tiles, not for a runaway body.
MAX_TILE_BYTES = 256 * 1024 * 1024
TIMEOUT_S = 120.0
# Read beyond the square by two pixels, so a pixel whose centre is just inside is in the window.
MARGIN_M = 20.0


def tile_for(lat: float, lon: float) -> tuple[int, int]:
    """(lat, lon) of the lower-left corner of the 3-degree tile containing a point."""
    return math.floor(lat / TILE_DEG) * TILE_DEG, math.floor(lon / TILE_DEG) * TILE_DEG


def tile_name(lat: int, lon: int) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"ESA_WorldCover_10m_2021_v200_{ns}{abs(lat):02d}{ew}{abs(lon):03d}_Map"


def tile_url(lat: int, lon: int) -> str:
    return TILE_URL.format(name=tile_name(lat, lon))


def tiles_for_bbox(bbox: tuple[float, float, float, float]) -> list[tuple[int, int]]:
    """Every tile a (south, west, north, east) box touches, sorted by (lat, lon)."""
    south, west, north, east = bbox
    lat0, lon0 = tile_for(south, west)
    lat1, lon1 = tile_for(north, east)
    return [
        (lat, lon)
        for lat in range(lat0, lat1 + 1, TILE_DEG)
        for lon in range(lon0, lon1 + 1, TILE_DEG)
    ]


@dataclass(frozen=True)
class _Window:
    """The classes of one tile over the square: rows r0.., columns c0.. of the raster."""

    geo: _Georef
    r0: int
    c0: int
    classes: np.ndarray

    def lookup(self, lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(row, col, inside) of the pixel containing each point, relative to the window."""
        # geo.lat0/lon0 are pixel centres, so the pixel containing a point is the nearest centre.
        row = np.floor((self.geo.lat0 - lat) / self.geo.dlat + 0.5).astype(np.int64) - self.r0
        col = np.floor((lon - self.geo.lon0) / self.geo.dlon + 0.5).astype(np.int64) - self.c0
        rows, cols = self.classes.shape
        inside = (row >= 0) & (row < rows) & (col >= 0) & (col < cols)
        return row, col, inside


@dataclass(frozen=True)
class TreeCover:
    """Tree cover around the square, from every WorldCover tile it touches."""

    windows: tuple[_Window, ...]

    def is_tree(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        """Whether the pixel under each (lon, lat) is tree cover. Outside every tile: no."""
        out = np.zeros(np.shape(lon), dtype=bool)
        for window in self.windows:
            row, col, inside = window.lookup(lon, lat)
            out[inside] |= window.classes[row[inside], col[inside]] == TREE_CLASS
        return out

    def tree_pixels(self) -> tuple[np.ndarray, np.ndarray]:
        """(lon, lat) of the centre of every tree-cover pixel."""
        lons, lats = [np.empty(0)], [np.empty(0)]
        for window in self.windows:
            rows, cols = np.nonzero(window.classes == TREE_CLASS)
            lats.append(window.geo.lat0 - (rows + window.r0) * window.geo.dlat)
            lons.append(window.geo.lon0 + (cols + window.c0) * window.geo.dlon)
        return np.concatenate(lons), np.concatenate(lats)


def _pixel_range(lo: float, hi: float, n: int) -> tuple[int, int]:
    """Pixels [a, b) whose area meets the raster positions lo..hi (pixel-centre units)."""
    a = int(np.clip(math.floor(lo + 0.5), 0, n))
    b = int(np.clip(math.floor(hi + 0.5) + 1, 0, n))
    return a, b


def _read(path: Path, bbox: tuple[float, float, float, float]) -> _Window | None:
    """The part of one cached tile inside bbox, or None when the tile does not reach into it."""
    south, west, north, east = bbox
    try:
        with tifffile.TiffFile(path) as tif:
            page = tif.pages.first
            geo = _georef(page)
            r0, r1 = _pixel_range((geo.lat0 - north) / geo.dlat, (geo.lat0 - south) / geo.dlat, geo.rows)
            c0, c1 = _pixel_range((west - geo.lon0) / geo.dlon, (east - geo.lon0) / geo.dlon, geo.cols)
            if r0 >= r1 or c0 >= c1:
                return None
            classes = _read_window(tif, page, r0, r1, c0, c1)
    except TileError:
        raise
    except (OSError, ValueError, KeyError, IndexError, RuntimeError) as exc:
        # tifffile.TiffFileError is a ValueError, the imagecodecs errors are RuntimeErrors.
        raise TileError(str(exc) or type(exc).__name__) from exc
    return _Window(geo=geo, r0=r0, c0=c0, classes=classes)


def _window(path: Path, url: str, bbox: tuple[float, float, float, float], client: httpx.Client) -> _Window | None:
    try:
        return _read(path, bbox)
    except TileError as exc:
        # A crash mid-copy leaves a broken entry; it counts as a miss and is fetched again.
        log.warning("Trees: unreadable cache entry %s (%s); fetching it again", path.name, exc)
        path.unlink(missing_ok=True)
    if _download(url, path, client, MAX_TILE_BYTES) == _OCEAN:
        return None
    return _read(path, bbox)


def tree_cover(spec: FrameSpec, cache_dir: Path, client: httpx.Client) -> TreeCover:
    """Tree cover over the square. Raises TileError or OSError when WorldCover is not available.

    A 404 is a tile the bucket does not have: open sea, no trees. When every tile is missing that
    is as likely a moved bucket as the sea, and it is reported as a failure, like the DEM does.
    """
    directory = cache_dir / CACHE_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    bbox = query_bbox(spec, margin_m=MARGIN_M)
    tiles = tiles_for_bbox(bbox)
    windows: list[_Window] = []
    missing = 0
    for tile in tiles:
        path = directory / f"{tile_name(*tile)}.tif"
        url = tile_url(*tile)
        if not path.is_file() and _download(url, path, client, MAX_TILE_BYTES) == _OCEAN:
            missing += 1
            continue
        window = _window(path, url, bbox, client)
        if window is not None:
            windows.append(window)
    if missing == len(tiles):
        raise TileError("no ESA WorldCover tile covers the square")
    return TreeCover(tuple(windows))
