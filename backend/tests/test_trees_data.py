"""WorldCover tiles and tree placement (spec 6 §4). No network: tiles are small synthetic
GeoTIFFs served through httpx.MockTransport, like the DEM tests."""

import math
from pathlib import Path

import httpx
import numpy as np
import pytest
import tifffile

from skylineframe.project import local_transformer
from skylineframe.spec import FrameSpec
from skylineframe.trees.defaults import TREE_CROWN_M, TREE_HEIGHT_M
from skylineframe.trees.layer import TreeLayer, tree_layer
from skylineframe.trees.model import OsmTree
from skylineframe.trees.worldcover import TILE_URL, tile_for, tile_name, tile_url, tiles_for_bbox

PX = 1 / 12000  # the WorldCover pixel: 3 degrees in 36 000 pixels
TREE, GRASS, SHRUB = 10, 30, 20
LOGGER = "skylineframe.trees"

SPEC = FrameSpec(center_lat=49.5, center_lon=7.5, side_m=400, plate_size_mm=100)
HALF_M = SPEC.side_m / 2


# --- helpers ---------------------------------------------------------------


def to_local_xy(spec: FrameSpec, lon, lat) -> tuple[np.ndarray, np.ndarray]:
    e, n = local_transformer(spec).transform(np.asarray(lon, float), np.asarray(lat, float))
    a = math.radians(spec.rotation_deg)
    return e * math.cos(a) - n * math.sin(a), e * math.sin(a) + n * math.cos(a)


def to_lonlat(spec: FrameSpec, x, y) -> tuple[np.ndarray, np.ndarray]:
    a = math.radians(spec.rotation_deg)
    x, y = np.asarray(x, float), np.asarray(y, float)
    e, n = x * math.cos(a) + y * math.sin(a), -x * math.sin(a) + y * math.cos(a)
    return local_transformer(spec).transform(e, n, direction="INVERSE")


class Cover:
    """A WorldCover-shaped raster: uint8 classes, PX pixels, tie point at the top-left corner.

    Much smaller than a real 36 000 x 36 000 tile; the reader takes the extent from the tags.
    """

    def __init__(self, west: float, north: float, rows: int = 256, cols: int = 256, point: bool = False):
        self.west, self.north, self.point = west, north, point
        self.data = np.full((rows, cols), GRASS, dtype=np.uint8)
        # Pixel centres, whatever the raster type: the tie point moves, the pixels do not.
        self.lons = west + (np.arange(cols) + 0.5) * PX
        self.lats = north - (np.arange(rows) + 0.5) * PX

    def centres(self) -> tuple[np.ndarray, np.ndarray]:
        return np.meshgrid(self.lons, self.lats)

    def write(self, path: Path, tiled: bool = True) -> Path:
        # PixelIsPoint ties the centre of pixel (0, 0), PixelIsArea its outer corner.
        tie = (self.west + PX / 2, self.north - PX / 2) if self.point else (self.west, self.north)
        geokeys = (1, 1, 0, 1, 1025, 0, 1, 2 if self.point else 1)
        tifffile.imwrite(
            path,
            self.data,
            tile=(64, 64) if tiled else None,
            compression="deflate",
            extratags=[
                (33550, "d", 3, (PX, PX, 0.0), False),
                (33922, "d", 6, (0.0, 0.0, 0.0, tie[0], tie[1], 0.0), False),
                (34735, "H", len(geokeys), geokeys, False),
            ],
        )
        return path


def cover_around(spec: FrameSpec, **kw) -> Cover:
    """A raster about 1.5 x 2.4 km centred on the square."""
    return Cover(spec.center_lon - 128 * PX, spec.center_lat + 128 * PX, **kw)


class Server:
    """Serves tiles by URL; anything it does not have is a 404, like the bucket."""

    def __init__(self, tmp_path: Path):
        self.dir = tmp_path / "server"
        self.dir.mkdir()
        self.tiles: dict[str, bytes] = {}
        self.calls: list[str] = []

    def add(self, spec_or_tile, cover: Cover, **kw) -> None:
        tile = spec_or_tile if isinstance(spec_or_tile, tuple) else tile_for(spec_or_tile.center_lat, spec_or_tile.center_lon)
        path = cover.write(self.dir / f"{tile_name(*tile)}.tif", **kw)
        self.tiles[tile_url(*tile)] = path.read_bytes()

    def client(self) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(str(request.url))
            body = self.tiles.get(str(request.url))
            if body is None:
                return httpx.Response(404, text="NoSuchKey")
            return httpx.Response(200, content=body)

        return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def server(tmp_path) -> Server:
    return Server(tmp_path)


@pytest.fixture
def cache(tmp_path) -> Path:
    return tmp_path / "cache"


def forest(spec: FrameSpec = SPEC, where=None, **kw) -> Cover:
    cover = cover_around(spec, **kw)
    lon, lat = cover.centres()
    cover.data[:] = TREE if where is None else np.where(where(lon, lat), TREE, GRASS)
    return cover


def metres(layer: TreeLayer, spec: FrameSpec = SPEC) -> np.ndarray:
    return np.array([(t.x_mm / spec.scale, t.y_mm / spec.scale) for t in layer.trees]).reshape(-1, 2)


def run(spec: FrameSpec, cache: Path, server: Server, osm: list[OsmTree] | None = None) -> TreeLayer:
    with server.client() as client:
        return tree_layer(spec, osm or [], cache, client=client)


# --- tile names --------------------------------------------------------------


def test_tiles_are_named_after_their_lower_left_corner_on_a_3_degree_grid():
    assert tile_for(40.7644, -73.9730) == (39, -75)
    assert tile_for(50.1413, 8.3925) == (48, 6)
    assert tile_for(51.0, 9.0) == (51, 9)  # on the corner: the tile to the north-east
    assert tile_for(-0.5, -0.5) == (-3, -3)
    assert tile_for(-33.87, 151.21) == (-36, 150)
    assert tile_for(0.0, -180.0) == (0, -180)
    assert tile_name(39, -75) == "ESA_WorldCover_10m_2021_v200_N39W075_Map"
    assert tile_name(48, 6) == "ESA_WorldCover_10m_2021_v200_N48E006_Map"
    assert tile_name(-3, -3) == "ESA_WorldCover_10m_2021_v200_S03W003_Map"
    assert tile_name(-36, 150) == "ESA_WorldCover_10m_2021_v200_S36E150_Map"
    assert tile_name(0, 0) == "ESA_WorldCover_10m_2021_v200_N00E000_Map"


def test_tile_url_is_the_verified_bucket_path():
    assert tile_url(39, -75) == (
        "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N39W075_Map.tif"
    )
    assert TILE_URL.startswith("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/")


def test_tiles_for_a_bbox():
    assert tiles_for_bbox((50.13, 8.38, 50.15, 8.40)) == [(48, 6)]
    assert tiles_for_bbox((50.99, 8.99, 51.01, 9.01)) == [(48, 6), (48, 9), (51, 6), (51, 9)]
    assert tiles_for_bbox((-0.01, -0.01, 0.01, 0.01)) == [(-3, -3), (-3, 0), (0, -3), (0, 0)]


# --- placement ---------------------------------------------------------------


def test_a_forest_is_filled_on_the_crown_grid(server, cache):
    server.add(SPEC, forest())
    layer = run(SPEC, cache, server)
    assert layer.source == "worldcover"
    xy = metres(layer)
    # One tree per 8 m cell, every centre inside the square.
    cells = (SPEC.side_m / TREE_CROWN_M) ** 2
    assert 0.95 * cells <= len(xy) <= cells + 2 * SPEC.side_m / TREE_CROWN_M
    assert np.abs(xy).max() <= HALF_M
    # Jittered, not a lattice, and no two trees closer than half a crown.
    from scipy.spatial import cKDTree

    d, _ = cKDTree(xy).query(xy, k=2)
    assert d[:, 1].min() >= TREE_CROWN_M / 2 - 1e-9
    assert len(np.unique(np.round(xy[:, 0] % TREE_CROWN_M, 3))) > 100


def test_trees_are_in_print_millimetres(server, cache):
    server.add(SPEC, forest())
    layer = run(SPEC, cache, server)
    crowns = np.array([t.crown_mm for t in layer.trees])
    assert crowns == pytest.approx(TREE_CROWN_M * SPEC.scale)
    heights = np.array([t.height_mm for t in layer.trees]) / (SPEC.scale * SPEC.z_exaggeration)
    assert heights.min() >= TREE_HEIGHT_M * 0.8 - 1e-9 and heights.max() <= TREE_HEIGHT_M * 1.2 + 1e-9
    assert heights.std() > 1.0  # varied, not one height
    assert heights.mean() == pytest.approx(TREE_HEIGHT_M, rel=0.03)


def test_only_class_10_is_a_tree(server, cache):
    # Tree cover west of the centre, shrubs east of it: shrubland is not a forest.
    cover = forest(where=lambda lon, lat: lon < SPEC.center_lon)
    lon, _lat = cover.centres()
    cover.data[lon >= SPEC.center_lon] = SHRUB
    server.add(SPEC, cover)
    xy = metres(run(SPEC, cache, server))
    pixel_m = PX * 111_320 * math.cos(math.radians(SPEC.center_lat))
    assert xy[:, 0].max() <= pixel_m
    assert xy[:, 0].min() < -HALF_M + TREE_CROWN_M
    assert 0.45 * (SPEC.side_m / TREE_CROWN_M) ** 2 <= len(xy) <= 0.55 * (SPEC.side_m / TREE_CROWN_M) ** 2


def test_no_tree_cover_means_no_worldcover_trees(server, cache):
    server.add(SPEC, cover_around(SPEC))
    layer = run(SPEC, cache, server)
    assert layer.trees == [] and layer.source == ""


def test_placement_is_deterministic(server, cache, tmp_path):
    server.add(SPEC, forest(where=lambda lon, lat: (lon - SPEC.center_lon) > (lat - SPEC.center_lat)))
    first = run(SPEC, cache, server)
    second = run(SPEC, tmp_path / "other", server)
    assert first == second and len(first.trees) > 100


@pytest.mark.parametrize("rotation", [0.0, 37.0])
@pytest.mark.parametrize("point", [False, True])
def test_every_lone_tree_pixel_gets_a_tree_on_it(server, cache, rotation, point):
    spec = SPEC.model_copy(update={"rotation_deg": rotation})
    cover = cover_around(spec, point=point)
    lon, lat = cover.centres()
    x, y = to_local_xy(spec, lon, lat)
    # Pixels at least 40 m apart, all well inside the square: street trees in a car park.
    inside = (np.abs(x) < HALF_M - 5) & (np.abs(y) < HALF_M - 5)
    rows, cols = np.nonzero(inside)
    keep = (rows % 5 == 0) & (cols % 7 == 0)
    rows, cols = rows[keep], cols[keep]
    cover.data[rows, cols] = TREE
    server.add(spec, cover)

    xy = metres(run(spec, cache, server), spec)
    tree_lon, tree_lat = to_lonlat(spec, xy[:, 0], xy[:, 1])
    # Every tree stands inside a tree pixel's rectangle, and every tree pixel carries one. A
    # 9 x 6 m pixel may take the jittered points of two 8 m cells, so "one" means "at least one".
    col = np.floor((np.asarray(tree_lon) - cover.west) / PX).astype(int)
    row = np.floor((cover.north - np.asarray(tree_lat)) / PX).astype(int)
    assert (cover.data[row, col] == TREE).all()
    assert set(zip(row.tolist(), col.tolist())) == set(zip(rows.tolist(), cols.tolist()))
    assert len(rows) > 50 and len(xy) <= 2 * len(rows)


def test_rotation_turns_the_forest_with_the_square(server, cache):
    # Forest west of the centre. The square turned by 90 degrees puts the world's west at the
    # bottom of the print (project.to_local turns the world counter-clockwise).
    spec = SPEC.model_copy(update={"rotation_deg": 90.0})
    server.add(spec, forest(spec, where=lambda lon, lat: lon < spec.center_lon))
    xy = metres(run(spec, cache, server), spec)
    assert len(xy) > 1000
    assert xy[:, 1].max() <= 7.0
    assert xy[:, 0].min() < -HALF_M + TREE_CROWN_M and xy[:, 0].max() > HALF_M - TREE_CROWN_M


def test_a_square_across_a_tile_border_reads_both_tiles(server, cache):
    spec = FrameSpec(center_lat=51.0, center_lon=7.5, side_m=400, plate_size_mm=100)
    north = Cover(spec.center_lon - 128 * PX, 51.0 + 128 * PX, rows=128)
    south = Cover(spec.center_lon - 128 * PX, 51.0, rows=128)
    north.data[:] = TREE
    south.data[:] = TREE
    server.add((51, 6), north)
    server.add((48, 6), south)
    xy = metres(run(spec, cache, server), spec)
    assert len(xy) >= 0.95 * (spec.side_m / TREE_CROWN_M) ** 2
    assert (xy[:, 1] < 0).sum() == pytest.approx((xy[:, 1] > 0).sum(), rel=0.1)


# --- OSM trees -----------------------------------------------------------------


def test_osm_trees_replace_worldcover_trees_within_their_crown(server, cache):
    server.add(SPEC, forest())
    osm = [OsmTree(0.0, 0.0, 20.0, 16.0), OsmTree(50.0, -30.0)]
    layer = run(SPEC, cache, server, osm)
    by_pos = {(round(t.x_mm, 6), round(t.y_mm, 6)): t for t in layer.trees}
    big = by_pos[(0.0, 0.0)]
    assert big.crown_mm == pytest.approx(16.0 * SPEC.scale)
    assert big.height_mm == pytest.approx(20.0 * SPEC.scale * SPEC.z_exaggeration)
    small = by_pos[(round(50 * SPEC.scale, 6), round(-30 * SPEC.scale, 6))]
    assert small.crown_mm == pytest.approx(TREE_CROWN_M * SPEC.scale)
    assert small.height_mm == pytest.approx(TREE_HEIGHT_M * SPEC.scale * SPEC.z_exaggeration)

    xy = metres(layer)
    for (cx, cy), crown in (((0.0, 0.0), 16.0), ((50.0, -30.0), TREE_CROWN_M)):
        d = np.hypot(xy[:, 0] - cx, xy[:, 1] - cy)
        assert (d < crown / 2).sum() == 1  # the OSM tree itself, nothing else
    assert layer.source == "worldcover"


def test_osm_trees_outside_the_square_are_dropped(server, cache):
    osm = [OsmTree(HALF_M + 1, 0.0), OsmTree(0.0, -HALF_M - 1), OsmTree(HALF_M - 1, HALF_M - 1)]
    layer = run(SPEC, cache, server, osm)  # no tile: OSM only
    assert [(t.x_mm, t.y_mm) for t in layer.trees] == [pytest.approx(((HALF_M - 1) * SPEC.scale,) * 2)]


# --- failures ------------------------------------------------------------------


OSM = [OsmTree(10.0, 10.0, 15.0, 6.0)]


def assert_osm_only(layer: TreeLayer) -> None:
    assert layer.source == ""
    assert layer.note  # the pipeline shows it, so a silent fallback never looks like a forest-free place
    assert len(layer.trees) == 1
    assert layer.trees[0].x_mm == pytest.approx(10.0 * SPEC.scale)


def test_no_tile_means_osm_only(server, cache, caplog):
    assert_osm_only(run(SPEC, cache, server, OSM))
    assert "WorldCover" in caplog.text


@pytest.mark.parametrize("status", [403, 500, 503])
def test_a_server_error_means_osm_only(cache, status, caplog):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status)))
    assert_osm_only(tree_layer(SPEC, OSM, cache, client=client))
    assert f"HTTP {status}" in caplog.text


def test_a_network_error_means_osm_only(cache):
    def fail(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    client = httpx.Client(transport=httpx.MockTransport(fail))
    assert_osm_only(tree_layer(SPEC, OSM, cache, client=client))


def test_a_broken_download_means_osm_only_and_is_not_cached(cache):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"<html>nope</html>")))
    assert_osm_only(tree_layer(SPEC, OSM, cache, client=client))
    assert not list((cache / "worldcover").glob("*"))


def test_a_tile_without_georeference_means_osm_only(server, cache, tmp_path):
    path = tmp_path / "plain.tif"
    tifffile.imwrite(path, np.full((64, 64), TREE, dtype=np.uint8))
    server.tiles[tile_url(*tile_for(SPEC.center_lat, SPEC.center_lon))] = path.read_bytes()
    assert_osm_only(run(SPEC, cache, server, OSM))


def test_an_unwritable_cache_means_osm_only(server, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    server.add(SPEC, forest())
    assert_osm_only(run(SPEC, blocker, server, OSM))


# --- cache ---------------------------------------------------------------------


def test_the_tile_is_downloaded_once(server, cache):
    server.add(SPEC, forest())
    first = run(SPEC, cache, server)
    calls = len(server.calls)
    assert calls == 1
    assert (cache / "worldcover" / f"{tile_name(*tile_for(SPEC.center_lat, SPEC.center_lon))}.tif").is_file()
    assert run(SPEC, cache, server) == first
    assert len(server.calls) == calls
    assert not list((cache / "worldcover").glob("*.tmp"))


def test_a_broken_cache_entry_is_fetched_again(server, cache):
    # Speckled, so the tile does not compress to nothing and half of it is really cut off.
    server.add(SPEC, forest(where=lambda lon, lat: np.sin(lon * 9e4) * np.sin(lat * 7e4) > 0))
    first = run(SPEC, cache, server)
    assert len(first.trees) > 100
    entry = cache / "worldcover" / f"{tile_name(*tile_for(SPEC.center_lat, SPEC.center_lon))}.tif"
    body = entry.read_bytes()
    entry.write_bytes(body[: len(body) // 2])  # a crash mid-copy
    assert run(SPEC, cache, server) == first
    assert len(server.calls) == 2


def test_an_untiled_raster_is_read_too(server, cache):
    server.add(SPEC, forest(), tiled=False)
    assert len(run(SPEC, cache, server).trees) > 2000
