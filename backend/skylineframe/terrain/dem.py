"""Copernicus GLO-30 tiles -> a Heightfield over the print plate (spec 4b §4).

The DEM is the Copernicus 30 m surface model, published as one-degree Cloud-Optimised GeoTIFFs
in a public S3 bucket. Tiles are downloaded whole into the cache (30 MB each around 50 N) and
read back window by window: a 1500 m square needs a few dozen of the file's internal tiles, not
the whole 3600 x 2400 raster.

Georeference: tag 33922 (ModelTiepoint) ties raster (I, J) to (lon, lat), tag 33550
(ModelPixelScale) gives the pixel size in degrees. The longitude pixel size depends on the
latitude band, so it is always read, never assumed. GTRasterTypeGeoKey (1025 in the
GeoKeyDirectory, tag 34735) says whether the tie point is a pixel's centre (PixelIsPoint, what
GLO-30 declares) or its outer corner (PixelIsArea); both are honoured, and a file without the key
is taken as PixelIsArea, the GeoTIFF default.
"""

import logging
import math
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
import tifffile
from scipy.ndimage import map_coordinates

from ..fetch import USER_AGENT
from ..project import local_transformer, query_bbox
from ..spec import FrameSpec
from .ground import ground_surface, window_px
from .heightfield import Heightfield

log = logging.getLogger(__name__)

TILE_URL = "https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif"
TERRAIN_CELL_MM = 0.5  # print-grid spacing of the Heightfield
# Around the rotated square: more than the ground filter's window (100 m, spec §4.5), so the
# filter's flat-padded edge stays outside everything that is sampled.
MARGIN_M = 200.0
TIMEOUT_S = 120.0
MAX_TILE_BYTES = 128 * 1024 * 1024  # measured: N50_00_E008_00 is 30.7 MB
M_PER_DEG_LAT = 111_320.0
# GLO-30 marks voids with a large negative value; nothing on land is below -500 m.
NODATA_BELOW_M = -1000.0

TAG_PIXEL_SCALE = 33550
TAG_TIEPOINT = 33922
TAG_GEOKEYS = 34735
GEOKEY_RASTER_TYPE = 1025
RASTER_PIXEL_IS_POINT = 2

_OCEAN = "ocean"  # a tile the bucket does not have: open sea, height 0


class TileError(Exception):
    """A tile that cannot be downloaded or read."""


def tile_for(lat: float, lon: float) -> tuple[int, int]:
    """The (lat, lon) of the lower-left corner of the one-degree tile containing a point."""
    return math.floor(lat), math.floor(lon)


def tile_name(lat: int, lon: int) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM"


def tile_url(lat: int, lon: int) -> str:
    return TILE_URL.format(name=tile_name(lat, lon))


def tiles_for_bbox(bbox: tuple[float, float, float, float]) -> list[tuple[int, int]]:
    """Every tile a (south, west, north, east) box touches, sorted by (lat, lon)."""
    south, west, north, east = bbox
    lat0, lon0 = tile_for(south, west)
    lat1, lon1 = tile_for(north, east)
    return [(lat, lon) for lat in range(lat0, lat1 + 1) for lon in range(lon0, lon1 + 1)]


# --- one tile --------------------------------------------------------------


@dataclass(frozen=True)
class _Georef:
    """Pixel-centre coordinates of pixel (0, 0) and the pixel size, in degrees."""

    lon0: float
    lat0: float
    dlon: float
    dlat: float
    rows: int
    cols: int


def _georef(page: tifffile.TiffPage) -> _Georef:
    tags = page.tags
    if TAG_PIXEL_SCALE not in tags or TAG_TIEPOINT not in tags:
        raise TileError("no GeoTIFF georeference")
    sx, sy = tags[TAG_PIXEL_SCALE].value[:2]
    i, j, _k, x, y = tags[TAG_TIEPOINT].value[:5]
    point = False
    if TAG_GEOKEYS in tags:
        keys = tags[TAG_GEOKEYS].value
        for n in range(4, len(keys) - 3, 4):
            if keys[n] == GEOKEY_RASTER_TYPE and keys[n + 1] == 0:
                point = keys[n + 3] == RASTER_PIXEL_IS_POINT
    if sx <= 0 or sy <= 0:
        raise TileError(f"bad pixel scale {sx}, {sy}")
    # Raster position of the centre of pixel (0, 0): 0 under PixelIsPoint, 0.5 under PixelIsArea.
    centre = 0.0 if point else 0.5
    return _Georef(
        lon0=x + (centre - i) * sx,
        lat0=y - (centre - j) * sy,
        dlon=sx,
        dlat=sy,
        rows=page.imagelength,
        cols=page.imagewidth,
    )


def _read_window(tif: tifffile.TiffFile, page: tifffile.TiffPage, r0: int, r1: int, c0: int, c1: int) -> np.ndarray:
    """Rows r0:r1, columns c0:c1 of the page, decoding only the internal tiles they touch."""
    if not page.is_tiled:
        return page.asarray()[r0:r1, c0:c1]
    th, tw = page.tilelength, page.tilewidth
    tiles_across = -(-page.imagewidth // tw)
    out = np.empty((r1 - r0, c1 - c0), dtype=page.dtype)
    fh = tif.filehandle
    for tr in range(r0 // th, (r1 - 1) // th + 1):
        for tc in range(c0 // tw, (c1 - 1) // tw + 1):
            index = tr * tiles_across + tc
            fh.seek(page.dataoffsets[index])
            data = fh.read(page.databytecounts[index])
            segment, _indices, _shape = page.decode(data, index)
            block = segment.reshape(th, tw)
            ya, yb = max(r0, tr * th), min(r1, (tr + 1) * th)
            xa, xb = max(c0, tc * tw), min(c1, (tc + 1) * tw)
            top, left = tr * th, tc * tw
            out[ya - r0 : yb - r0, xa - c0 : xb - c0] = block[ya - top : yb - top, xa - left : xb - left]
    return out


def _span(pos: np.ndarray, n: int) -> tuple[int, int]:
    """The rows (or columns) [a, b) that linear interpolation at `pos` reads from."""
    if n == 1:
        return 0, 1
    a = int(np.clip(np.floor(pos.min()), 0, n - 2))
    b = int(np.clip(np.floor(pos.max()), 0, n - 2)) + 2
    return a, b


def _weights(pos: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Lower index and weight of linear interpolation, extrapolating past the edge centres.

    Pixel centres stop short of the tile edge (half a pixel under PixelIsArea, a whole pixel on
    one side under PixelIsPoint), so a node between two tiles lies beyond the last centre of the
    tile it belongs to. Clamping would leave a flat strip there, a visible seam on a slope; the
    straight continuation of the last two pixels does not. At most one pixel is extrapolated.
    """
    if n == 1:
        return np.zeros(pos.shape, dtype=int), np.zeros(pos.shape)
    p = np.clip(pos, -1.0, float(n))
    i0 = np.clip(np.floor(p), 0, n - 2).astype(int)
    return i0, p - i0


def _sample_tile(path: Path, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    """Linear-interpolated heights on the grid lats x lons (1-D each) within this tile's degree."""
    try:
        with tifffile.TiffFile(path) as tif:
            page = tif.pages.first
            geo = _georef(page)
            rows = (geo.lat0 - lats) / geo.dlat
            cols = (lons - geo.lon0) / geo.dlon
            r0, r1 = _span(rows, geo.rows)
            c0, c1 = _span(cols, geo.cols)
            window = _read_window(tif, page, r0, r1, c0, c1).astype(np.float64)
    except TileError:
        raise
    except (OSError, ValueError, KeyError, IndexError, RuntimeError) as exc:
        # tifffile.TiffFileError is a ValueError, the imagecodecs errors are RuntimeErrors.
        raise TileError(str(exc) or type(exc).__name__) from exc
    window[~np.isfinite(window) | (window < NODATA_BELOW_M)] = 0.0
    # Separable: along the rows first, then along the columns of that result.
    ri, rt = _weights(rows - r0, r1 - r0)
    ci, ct = _weights(cols - c0, c1 - c0)
    ri1 = np.minimum(ri + 1, r1 - r0 - 1)
    ci1 = np.minimum(ci + 1, c1 - c0 - 1)
    along = window[ri, :] * (1 - rt)[:, None] + window[ri1, :] * rt[:, None]
    return along[:, ci] * (1 - ct)[None, :] + along[:, ci1] * ct[None, :]


def _check(path: Path) -> None:
    """Raise TileError unless `path` is a TIFF carrying a georeference. Reads the header only."""
    try:
        with tifffile.TiffFile(path) as tif:
            _georef(tif.pages.first)
    except TileError:
        raise
    except (OSError, ValueError, KeyError, IndexError, RuntimeError) as exc:
        raise TileError(str(exc) or type(exc).__name__) from exc


def _download(url: str, path: Path, client: httpx.Client) -> str | None:
    """Stream the tile into `path`. Returns _OCEAN on a 404; raises TileError on anything else."""
    # uuid4, not the pid: the API runs jobs on a thread pool, so two jobs for the same tile
    # would otherwise stage under one name and truncate each other (see lod2/hessen.py).
    staged = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        with client.stream("GET", url, headers={"User-Agent": USER_AGENT}) as response:
            if response.status_code == 404:
                return _OCEAN
            if response.status_code != 200:
                raise TileError(f"HTTP {response.status_code}")
            size = 0
            with staged.open("wb") as handle:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_TILE_BYTES:
                        raise TileError(f"tile too large (over {MAX_TILE_BYTES} bytes)")
                    handle.write(chunk)
        _check(staged)
        # Into place only once complete and readable: a truncated entry would poison the tile.
        os.replace(staged, path)
        return None
    except (httpx.HTTPError, OSError) as exc:
        # OSError too: a full volume fails the write long after the request succeeded.
        raise TileError(str(exc) or type(exc).__name__) from exc
    finally:
        staged.unlink(missing_ok=True)


def _tile_heights(path: Path, url: str, lons: np.ndarray, lats: np.ndarray, client: httpx.Client) -> np.ndarray:
    """Heights of one cached tile on the lats x lons grid."""
    try:
        return _sample_tile(path, lons, lats)
    except TileError as exc:
        # A crash mid-copy leaves a broken entry; it counts as a miss and is fetched again.
        log.warning("Terrain: unreadable cache entry %s (%s); fetching it again", path.name, exc)
        path.unlink(missing_ok=True)
    if _download(url, path, client) == _OCEAN:  # it was there a moment ago; treat it as sea
        return np.zeros((len(lats), len(lons)))
    return _sample_tile(path, lons, lats)


def _pixel_size(tile_paths: list[Path]) -> tuple[float, float]:
    """The finest (dlat, dlon) among the cached tiles, so the mosaic loses no detail."""
    dlat, dlon = math.inf, math.inf
    for path in tile_paths:
        try:
            with tifffile.TiffFile(path) as tif:
                geo = _georef(tif.pages.first)
        except (TileError, OSError, ValueError, KeyError, IndexError, RuntimeError):
            continue  # a broken entry is dealt with when it is sampled
        dlat, dlon = min(dlat, geo.dlat), min(dlon, geo.dlon)
    return dlat, dlon


# --- the mosaic and the print grid -----------------------------------------


def _pixel_m(dlat: float, dlon: float, lat: float) -> tuple[float, float]:
    """(north-south, east-west) size in metres of a dlat x dlon pixel at `lat`."""
    return dlat * M_PER_DEG_LAT, dlon * M_PER_DEG_LAT * math.cos(math.radians(lat))


def _mosaic(
    spec: FrameSpec, cache_dir: Path, client: httpx.Client
) -> tuple[np.ndarray, float, float, float, float]:
    """Surface heights over query_bbox on one regular lat/lon grid, row 0 at the north edge.

    Returns (heights, north, west, dlat, dlon). Neighbouring tiles may differ in longitude pixel
    size (the GLO-30 bands change at 50 N, 60 N, ...), so every tile is resampled onto the finest
    grid among them instead of being pasted in.
    """
    dem_dir = cache_dir / "dem"
    dem_dir.mkdir(parents=True, exist_ok=True)
    # A 404 is not cached on disk (a tile could still be published), but asked only once per run.
    ocean: set[tuple[int, int]] = set()

    def path_of(tile: tuple[int, int]) -> Path:
        return dem_dir / f"{tile_name(*tile)}.tif"

    def ensure(bbox: tuple[float, float, float, float]) -> list[tuple[int, int]]:
        tiles = tiles_for_bbox(bbox)
        for tile in tiles:
            if tile not in ocean and not path_of(tile).is_file():
                if _download(tile_url(*tile), path_of(tile), client) == _OCEAN:
                    ocean.add(tile)
        return tiles

    # Every tile local first: the mosaic pixel size comes from the tiles themselves.
    south, west, north, east = query_bbox(spec, margin_m=MARGIN_M)
    tiles = ensure((south, west, north, east))
    dlat, dlon = _pixel_size([path_of(tile) for tile in tiles if tile not in ocean])
    if not math.isfinite(dlat):  # nothing but ocean: any grid will do
        dlat = dlon = 1 / 3600
    # Plus two ground-filter windows and a pixel, so the filter's flat-padded border stays out
    # of the square even on a coarse raster. For GLO-30 that is 8 pixels, about 240 m.
    row_m, col_m = _pixel_m(dlat, dlon, spec.center_lat)
    south -= (2 * window_px(row_m) + 2) * dlat
    north += (2 * window_px(row_m) + 2) * dlat
    west -= (2 * window_px(col_m) + 2) * dlon
    east += (2 * window_px(col_m) + 2) * dlon
    tiles = ensure((south, west, north, east))

    lats = north - np.arange(int(math.ceil((north - south) / dlat)) + 1) * dlat
    lons = west + np.arange(int(math.ceil((east - west) / dlon)) + 1) * dlon
    heights = np.zeros((len(lats), len(lons)))
    tile_lats = np.floor(lats).astype(int)
    tile_lons = np.floor(lons).astype(int)
    for tile in tiles:
        rows = np.flatnonzero(tile_lats == tile[0])
        cols = np.flatnonzero(tile_lons == tile[1])
        if tile in ocean or not (len(rows) and len(cols)):
            continue  # sea level, which the zeros already are
        heights[np.ix_(rows, cols)] = _tile_heights(path_of(tile), tile_url(*tile), lons[cols], lats[rows], client)
    return heights, north, west, dlat, dlon


def _grid_lonlat(spec: FrameSpec, n: int) -> tuple[np.ndarray, np.ndarray, float]:
    """WGS84 (lon, lat) of every print-grid node, shape (n, n) with rows along +y, and the cell."""
    half = spec.plate_size_mm / 2
    cell = spec.plate_size_mm / (n - 1)
    axis_m = (-half + np.arange(n) * cell) / spec.scale
    y, x = np.meshgrid(axis_m, axis_m, indexing="ij")
    # The print frame is the world turned by +rotation_deg (project.to_local); turn it back.
    a = math.radians(spec.rotation_deg)
    east = x * math.cos(a) + y * math.sin(a)
    north = -x * math.sin(a) + y * math.cos(a)
    lon, lat = local_transformer(spec).transform(east, north, direction="INVERSE")
    return np.asarray(lon), np.asarray(lat), cell


def terrain_heightfield(spec: FrameSpec, cache_dir: Path, client: httpx.Client | None = None) -> Heightfield | None:
    """The ground under the square as a Heightfield, or None when the DEM is not available.

    None means: build flat and say so (spec §4.3). A missing tile is open sea and counts as 0 m.
    """
    owns_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT_S, follow_redirects=True)
    try:
        surface, north, west, dlat, dlon = _mosaic(spec, cache_dir, client)
    except TileError as exc:
        log.warning("Terrain: Copernicus DEM not available (%s); building a flat plate", exc)
        return None
    finally:
        if owns_client:
            client.close()

    ground = ground_surface(surface, _pixel_m(dlat, dlon, spec.center_lat))

    n = int(round(spec.plate_size_mm / TERRAIN_CELL_MM)) + 1
    lon, lat, cell = _grid_lonlat(spec, n)
    rows = (north - lat) / dlat
    cols = (lon - west) / dlon
    h = map_coordinates(ground, [rows, cols], order=1, mode="nearest")
    # FrameSpec.terrain_exaggeration lands with the geometry half of phase 4b; 1.0 until then.
    exaggeration = getattr(spec, "terrain_exaggeration", 1.0)
    z = (h - h.min()) * spec.scale * exaggeration
    half = spec.plate_size_mm / 2
    return Heightfield(z_mm=z, cell_mm=cell, origin_mm=(-half, -half))
