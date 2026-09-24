import logging
from collections.abc import Callable
from pathlib import Path

import httpx
import numpy as np
import pytest
import tifffile

import skylineframe.terrain.dem as dem
from skylineframe.spec import FrameSpec
from skylineframe.terrain.dem import (
    TERRAIN_CELL_MM,
    TILE_URL,
    terrain_heightfield,
    tile_for,
    tile_name,
    tile_url,
    tiles_for_bbox,
)
from skylineframe.terrain.heightfield import Heightfield

LOGGER = "skylineframe.terrain.dem"
EPPSTEIN = FrameSpec(center_lat=50.1413, center_lon=8.3925, side_m=1500, plate_size_mm=100)
# A 1500 m square straddling the N49/N50 tile border, where GLO-30 also changes its
# longitude pixel size (1/3600 deg below 50 N, 1/2400 deg from 50 N on).
ON_THE_BORDER = FrameSpec(center_lat=50.0, center_lon=8.5, side_m=1500, plate_size_mm=100)
# Metres per degree of longitude at 50 N, near enough for expected values.
M_PER_DEG_LON_50 = 111_320.0 * np.cos(np.radians(50.0))

Height = Callable[[np.ndarray, np.ndarray], np.ndarray]  # (lon, lat) -> metres


def write_tile(
    path: Path,
    lat: int,
    lon: int,
    height: Height,
    rows: int = 720,
    cols: int = 480,
    point: bool = True,
    tiled: bool = True,
) -> Path:
    """A GLO-30-shaped GeoTIFF: one degree, tie point at the top-left corner, float32, deflate.

    Coarser than the real 3600 x 2400 so the suite stays fast; the georeference is read from the
    tags, so the reader cannot tell. `point` selects RasterPixelIsPoint (the tie point is the
    centre of the first pixel) over RasterPixelIsArea (it is that pixel's outer corner).
    """
    offset = 0.0 if point else 0.5
    lons = lon + (np.arange(cols) + offset) / cols
    lats = lat + 1 - (np.arange(rows) + offset) / rows
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    data = np.asarray(height(lon_grid, lat_grid), dtype=np.float32)
    raster_type = 2 if point else 1
    geokeys = (1, 1, 0, 1, 1025, 0, 1, raster_type)
    tifffile.imwrite(
        path,
        data,
        tile=(64, 64) if tiled else None,
        compression="deflate",
        predictor=3,
        extratags=[
            (33550, "d", 3, (1 / cols, 1 / rows, 0.0), False),
            (33922, "d", 6, (0.0, 0.0, 0.0, float(lon), float(lat + 1), 0.0), False),
            (34735, "H", len(geokeys), geokeys, False),
        ],
    )
    return path


class TileServer:
    """A MockTransport serving tiles by URL; anything it does not have is a 404, like the bucket."""

    def __init__(self, tmp_path: Path):
        self.dir = tmp_path / "server"
        self.dir.mkdir(parents=True)
        self.tiles: dict[str, bytes] = {}
        self.calls: list[httpx.Request] = []

    def add(self, lat: int, lon: int, height: Height, **kwargs) -> None:
        path = write_tile(self.dir / f"{tile_name(lat, lon)}.tif", lat, lon, height, **kwargs)
        self.tiles[tile_url(lat, lon)] = path.read_bytes()

    def client(self) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            body = self.tiles.get(str(request.url))
            if body is None:
                return httpx.Response(404, text="NoSuchKey")
            return httpx.Response(200, content=body, headers={"content-type": "image/tiff"})

        return httpx.Client(transport=httpx.MockTransport(handler))


def ramp_east(slope_per_deg: float, base: float = 100.0) -> Height:
    return lambda lon, lat: base + slope_per_deg * (lon - 8.0)


def ramp_north(slope_per_deg: float, base: float = 100.0) -> Height:
    return lambda lon, lat: base + slope_per_deg * (lat - 49.0)


@pytest.fixture
def server(tmp_path) -> TileServer:
    return TileServer(tmp_path)


@pytest.fixture
def cache(tmp_path) -> Path:
    return tmp_path / "cache"


# --- tile names ----------------------------------------------------------


def test_tile_names_follow_the_bucket():
    assert tile_name(50, 8) == "Copernicus_DSM_COG_10_N50_00_E008_00_DEM"
    assert tile_name(-1, -1) == "Copernicus_DSM_COG_10_S01_00_W001_00_DEM"
    assert tile_name(0, 0) == "Copernicus_DSM_COG_10_N00_00_E000_00_DEM"
    assert tile_name(-34, 151) == "Copernicus_DSM_COG_10_S34_00_E151_00_DEM"
    assert tile_name(64, -180) == "Copernicus_DSM_COG_10_N64_00_W180_00_DEM"


def test_tile_url_repeats_the_name_as_folder_and_file():
    name = "Copernicus_DSM_COG_10_N50_00_E008_00_DEM"
    assert tile_url(50, 8) == f"https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif"
    assert TILE_URL.startswith("https://copernicus-dem-30m.s3.amazonaws.com/")


def test_a_tile_is_named_after_its_lower_left_corner():
    assert tile_for(50.1413, 8.3925) == (50, 8)
    # Negative coordinates floor away from zero: -0.5 lies in the tile from -1 to 0.
    assert tile_for(-0.5, -0.5) == (-1, -1)
    assert tile_name(*tile_for(-0.5, -0.5)) == "Copernicus_DSM_COG_10_S01_00_W001_00_DEM"
    assert tile_for(0.0, 0.0) == (0, 0)
    assert tile_for(-33.87, 151.21) == (-34, 151)


def test_tiles_for_a_bbox():
    assert tiles_for_bbox((50.13, 8.38, 50.15, 8.40)) == [(50, 8)]
    assert tiles_for_bbox((49.99, 8.49, 50.01, 8.51)) == [(49, 8), (50, 8)]
    assert tiles_for_bbox((49.99, 7.99, 50.01, 8.01)) == [(49, 7), (49, 8), (50, 7), (50, 8)]
    assert tiles_for_bbox((-0.01, -0.01, 0.01, 0.01)) == [(-1, -1), (-1, 0), (0, -1), (0, 0)]


# --- the heightfield -----------------------------------------------------


def test_the_cell_is_half_a_millimetre():
    assert TERRAIN_CELL_MM == 0.5


def test_the_grid_covers_the_plate_edge_to_edge(server, cache):
    server.add(50, 8, ramp_east(5000.0))
    field = terrain_heightfield(EPPSTEIN, cache, client=server.client())
    assert isinstance(field, Heightfield)
    assert field.cell_mm == pytest.approx(TERRAIN_CELL_MM)
    assert field.z_mm.shape == (201, 201)
    assert field.origin_mm == (-50.0, -50.0)
    assert field.origin_mm[0] + (field.z_mm.shape[1] - 1) * field.cell_mm == pytest.approx(50.0)
    assert field.z_mm.dtype == np.float64


def test_the_lowest_node_is_exactly_zero(server, cache):
    server.add(50, 8, ramp_east(5000.0, base=312.5))
    field = terrain_heightfield(EPPSTEIN, cache, client=server.client())
    assert field.z_mm.min() == 0.0
    assert np.all(np.isfinite(field.z_mm))


def test_a_slope_to_the_east_rises_along_x(server, cache):
    slope = 5000.0  # metres per degree of longitude: ~0.07 m per metre at 50 N
    server.add(50, 8, ramp_east(slope))
    field = terrain_heightfield(EPPSTEIN, cache, client=server.client())
    z = field.z_mm
    # Columns run along +x = east; every row sees the same ramp.
    assert np.all(np.diff(z, axis=1) > 0)
    np.testing.assert_allclose(z, z[:1, :].repeat(z.shape[0], axis=0), atol=0.01)
    # 1500 m of ramp at 1:15 000.
    relief_m = slope / M_PER_DEG_LON_50 * EPPSTEIN.side_m
    assert z.max() == pytest.approx(relief_m * EPPSTEIN.scale, rel=0.03)


def test_a_square_rotated_by_90_degrees_sees_the_slope_along_y(server, cache):
    server.add(50, 8, ramp_east(5000.0))
    rotated = EPPSTEIN.model_copy(update={"rotation_deg": 90.0})
    field = terrain_heightfield(rotated, cache, client=server.client())
    z = field.z_mm
    # The print frame is the world turned by +90 degrees, so east now points along +y (rows).
    assert np.all(np.diff(z, axis=0) > 0)
    np.testing.assert_allclose(z, z[:, :1].repeat(z.shape[1], axis=1), atol=0.01)
    straight = terrain_heightfield(EPPSTEIN, cache, client=server.client())
    np.testing.assert_allclose(z, straight.z_mm.T, atol=0.01)


def test_the_exaggeration_scales_the_relief(server, cache):
    server.add(50, 8, ramp_east(5000.0))
    plain = terrain_heightfield(EPPSTEIN, cache, client=server.client())
    doubled_spec = EPPSTEIN.model_copy(update={"terrain_exaggeration": 2.0})
    doubled = terrain_heightfield(doubled_spec, cache, client=server.client())
    np.testing.assert_allclose(doubled.z_mm, 2.0 * plain.z_mm)


def test_a_tower_in_the_surface_model_does_not_lift_the_ground(server, cache):
    def with_tower(lon, lat):
        h = np.full(lon.shape, 150.0)
        near = (np.abs(lon - EPPSTEIN.center_lon) < 1e-3) & (np.abs(lat - EPPSTEIN.center_lat) < 1e-3)
        return np.where(near, 350.0, h)  # a single 200 m spike in one pixel

    server.add(50, 8, with_tower)
    field = terrain_heightfield(EPPSTEIN, cache, client=server.client())
    assert field.z_mm.max() < 0.05


def test_pixel_is_area_and_pixel_is_point_give_the_same_ground(tmp_path):
    # The same landscape sampled under both conventions: honouring GTRasterTypeGeoKey puts each
    # value back where it was measured, so the two fields agree.
    fields = []
    for point in (True, False):
        server = TileServer(tmp_path / ("point" if point else "area"))
        server.add(50, 8, lambda lon, lat: 100 + 3000 * (lon - 8) ** 2 + 2000 * (lat - 50), point=point)
        fields.append(terrain_heightfield(EPPSTEIN, tmp_path / f"cache{point}", client=server.client()))
    np.testing.assert_allclose(fields[0].z_mm, fields[1].z_mm, atol=0.02)


def test_a_striped_tiff_is_read_too(server, cache, tmp_path):
    server.add(50, 8, ramp_east(5000.0), tiled=False)
    striped = terrain_heightfield(EPPSTEIN, cache, client=server.client())
    tiled_server = TileServer(tmp_path / "t")
    tiled_server.add(50, 8, ramp_east(5000.0))
    tiled = terrain_heightfield(EPPSTEIN, tmp_path / "c2", client=tiled_server.client())
    np.testing.assert_allclose(striped.z_mm, tiled.z_mm)


def test_a_mosaic_across_a_tile_border_with_two_pixel_sizes_has_no_seam(server, cache):
    slope = 4000.0  # metres per degree of latitude: ~0.036 m per metre
    server.add(49, 8, ramp_north(slope), cols=720)  # finer in longitude, like GLO-30 below 50 N
    server.add(50, 8, ramp_north(slope), cols=480)
    field = terrain_heightfield(ON_THE_BORDER, cache, client=server.client())
    assert len(server.calls) == 2
    z = field.z_mm
    assert np.all(np.diff(z, axis=0) > 0)
    # A straight ramp, straight across the border: every step the same size.
    steps = np.diff(z[:, 100])
    np.testing.assert_allclose(steps, steps.mean(), rtol=0.05)
    relief_m = slope / 111_320.0 * ON_THE_BORDER.side_m
    assert z.max() == pytest.approx(relief_m * ON_THE_BORDER.scale, rel=0.03)


# --- missing tiles and failures -------------------------------------------


def test_a_missing_tile_is_sea_level(server, cache):
    # The bucket has no tiles for open ocean and answers 404: that is height 0, not an error.
    server.add(50, 8, lambda lon, lat: np.full(lon.shape, 40.0))
    field = terrain_heightfield(ON_THE_BORDER, cache, client=server.client())  # N49 is "ocean"
    assert field is not None
    z = field.z_mm
    # Rows more than 450 m from the border: the test tiles have 150 m pixels, so the 3-pixel
    # ground filter and the interpolation blur the coast over about 300 m.
    south, north = z[:40, :], z[-40:, :]
    assert np.all(south == 0.0)
    assert north.min() == pytest.approx(40.0 * ON_THE_BORDER.scale, rel=0.01)


def test_only_ocean_is_a_flat_field(server, cache):
    field = terrain_heightfield(EPPSTEIN, cache, client=server.client())
    assert field is not None
    assert np.all(field.z_mm == 0.0)
    # A 404 leaves nothing in the cache, but within one run the bucket is asked only once.
    urls = [str(r.url) for r in server.calls]
    assert urls == [tile_url(50, 8)]


def test_a_network_error_gives_none(cache, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert terrain_heightfield(EPPSTEIN, cache, client=client) is None
    assert "no route to host" in caplog.text
    assert list((cache / "dem").iterdir()) == []  # nothing half-written is left behind


@pytest.mark.parametrize("status", [403, 500, 503])
def test_an_http_error_other_than_404_gives_none(cache, caplog, status):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, text="no")))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert terrain_heightfield(EPPSTEIN, cache, client=client) is None
    assert str(status) in caplog.text
    assert list((cache / "dem").iterdir()) == []


def test_a_body_that_is_not_a_geotiff_gives_none_and_is_not_cached(cache, caplog):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"<html>")))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert terrain_heightfield(EPPSTEIN, cache, client=client) is None
    assert list((cache / "dem").iterdir()) == []


def test_a_tiff_without_georeference_gives_none(server, cache, caplog):
    plain = server.dir / "plain.tif"
    tifffile.imwrite(plain, np.zeros((10, 10), dtype=np.float32))
    server.tiles[tile_url(50, 8)] = plain.read_bytes()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert terrain_heightfield(EPPSTEIN, cache, client=server.client()) is None
    assert list((cache / "dem").iterdir()) == []


def test_an_oversized_tile_is_abandoned(server, cache, caplog, monkeypatch):
    monkeypatch.setattr(dem, "MAX_TILE_BYTES", 16)
    server.add(50, 8, ramp_east(5000.0))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert terrain_heightfield(EPPSTEIN, cache, client=server.client()) is None
    assert "too large" in caplog.text
    assert list((cache / "dem").iterdir()) == []


def test_a_failed_write_gives_none(server, cache, caplog, monkeypatch):
    server.add(50, 8, ramp_east(5000.0))

    def no_space(src, dst):
        raise OSError("No space left on device")

    monkeypatch.setattr(dem.os, "replace", no_space)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert terrain_heightfield(EPPSTEIN, cache, client=server.client()) is None
    assert "No space left on device" in caplog.text
    assert list((cache / "dem").iterdir()) == []


# --- cache ---------------------------------------------------------------


def test_tiles_are_cached_under_their_name(server, cache):
    server.add(50, 8, ramp_east(5000.0))
    first = terrain_heightfield(EPPSTEIN, cache, client=server.client())
    second = terrain_heightfield(EPPSTEIN, cache, client=server.client())
    assert len(server.calls) == 1
    np.testing.assert_array_equal(first.z_mm, second.z_mm)
    assert [p.name for p in (cache / "dem").iterdir()] == [f"{tile_name(50, 8)}.tif"]


def test_a_warm_cache_needs_no_network(server, cache):
    server.add(50, 8, ramp_east(5000.0))
    terrain_heightfield(EPPSTEIN, cache, client=server.client())

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    field = terrain_heightfield(EPPSTEIN, cache, client=httpx.Client(transport=httpx.MockTransport(offline)))
    assert field is not None


def test_a_corrupt_cache_entry_is_fetched_again_in_the_same_run(server, cache, caplog):
    server.add(50, 8, ramp_east(5000.0))
    good = terrain_heightfield(EPPSTEIN, cache, client=server.client())
    entry = cache / "dem" / f"{tile_name(50, 8)}.tif"
    entry.write_bytes(entry.read_bytes()[:5000])  # e.g. a copy cut short
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        again = terrain_heightfield(EPPSTEIN, cache, client=server.client())
    assert again is not None
    np.testing.assert_array_equal(again.z_mm, good.z_mm)
    assert len(server.calls) == 2
    assert "cache entry" in caplog.text


def test_the_tile_request_names_the_generator(server, cache):
    server.add(50, 8, ramp_east(5000.0))
    terrain_heightfield(EPPSTEIN, cache, client=server.client())
    assert server.calls[0].headers["user-agent"].startswith("skylineframe/")
