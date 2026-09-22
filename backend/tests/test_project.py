import pytest
from shapely.geometry import LineString, Point, Polygon

from skylineframe.errors import AreaError
from skylineframe.features import Building, Features, Road
from skylineframe.project import (
    local_transformer,
    project_features,
    query_bbox,
    square_local,
    square_wgs84,
    to_local,
)
from skylineframe.spec import FrameSpec


def spec(**kw) -> FrameSpec:
    return FrameSpec(center_lat=50.0, center_lon=8.0, **kw)


def test_center_maps_to_origin():
    p = to_local(Point(8.0, 50.0), spec())
    assert p.x == pytest.approx(0, abs=1e-6)
    assert p.y == pytest.approx(0, abs=1e-6)


def test_one_km_east_is_about_1000_m():
    p = to_local(Point(8.0140, 50.0), spec())
    assert p.x == pytest.approx(1003.7, abs=1.0)
    assert abs(p.y) < 1.0


def test_north_is_positive_y():
    p = to_local(Point(8.0, 50.009), spec())
    assert p.y == pytest.approx(1001, abs=1.0)
    assert abs(p.x) < 0.01


def test_rotation_aligns_rotated_square_with_axes():
    s = spec(rotation_deg=45)
    tr = local_transformer(s)
    lon, lat = tr.transform(707.107, 707.107, direction="INVERSE")  # 1000 m towards north-east
    p = to_local(Point(lon, lat), s)
    assert p.x == pytest.approx(0, abs=0.5)
    assert p.y == pytest.approx(1000, abs=0.5)


def test_square_local_bounds():
    assert square_local(spec(side_m=1000)).bounds == (-500, -500, 500, 500)


def test_square_wgs84_roundtrips_to_axis_aligned_square():
    s = spec(side_m=1000, rotation_deg=30)
    back = to_local(square_wgs84(s), s)
    assert back.bounds == pytest.approx((-500, -500, 500, 500), abs=0.01)


def test_query_bbox_encloses_square_with_margin():
    s = spec(side_m=1000, rotation_deg=20)
    south, west, north, east = query_bbox(s, margin_m=100)
    minx, miny, maxx, maxy = square_wgs84(s).bounds
    assert west < minx and south < miny and east > maxx and north > maxy
    assert south < north and west < east


def test_project_features_keeps_attributes():
    feats = Features(
        buildings=[Building(Polygon([(8.0, 50.0), (8.001, 50.0), (8.001, 50.001)]), 12.0)],
        roads=[Road(LineString([(8.0, 50.0), (8.001, 50.0)]), "primary")],
    )
    local = project_features(feats, spec())
    assert local.buildings[0].height_m == 12.0
    assert local.roads[0].cls == "primary"
    assert local.buildings[0].geom.bounds[2] == pytest.approx(71.7, abs=1.0)


def test_query_bbox_rejects_antimeridian_crossing():
    s = FrameSpec(center_lat=0.0, center_lon=179.999, side_m=1000)
    with pytest.raises(AreaError, match="antimeridian"):
        query_bbox(s)
