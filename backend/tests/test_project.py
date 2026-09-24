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


def test_project_features_keeps_building_detail_fields():
    from shapely.geometry import box

    from skylineframe.features import Building, Features, RoofSpec

    spec = FrameSpec(center_lat=50.0, center_lon=8.0, side_m=1000)
    building = Building(
        geom=box(8.0, 50.0, 8.001, 50.001),
        height_m=42.0,
        height_is_top=True,
        min_height_m=12.0,
        roof=RoofSpec(shape="gabled", height_m=3.0, direction_deg=45.0),
        osm_id="way/123",
        is_part=True,
        outline_id="relation/456",
        kind="office",
    )
    out = project_features(Features(buildings=[building]), spec)
    got = out.buildings[0]
    assert got.height_m == 42.0
    assert got.height_is_top is True
    assert got.min_height_m == 12.0
    assert got.roof == RoofSpec(shape="gabled", height_m=3.0, direction_deg=45.0)
    assert (got.osm_id, got.is_part, got.outline_id, got.kind) == ("way/123", True, "relation/456", "office")
    assert got.geom.geom_type == "Polygon"
    assert got.geom is not building.geom  # projected, not the original


def test_project_features_projects_lod2_surfaces_and_keeps_the_source():
    from skylineframe.features import Features, Lod2Building

    spec = FrameSpec(center_lat=50.0, center_lon=8.0, side_m=1000)
    raw = Lod2Building(
        osm_id="lod2/hessen/Building_1",
        surfaces=(((8.0, 50.0, 100.0), (8.001, 50.0, 100.0), (8.001, 50.001, 110.0)),),
        name="Test",
    )
    out = project_features(Features(lod2=[raw], lod2_source="hessen"), spec)
    assert out.lod2_source == "hessen"
    got = out.lod2[0]
    assert got.osm_id == "lod2/hessen/Building_1" and got.name == "Test"
    x0, y0, z0 = got.surfaces[0][0]
    # The centre of the spec projects to the origin, and z is carried through untouched.
    assert (x0, y0, z0) == pytest.approx((0.0, 0.0, 100.0), abs=1e-6)
    x1, _y1, _z1 = got.surfaces[0][1]
    assert x1 == pytest.approx(71.6, abs=1.0)  # 0.001 deg of longitude at 50 N


def test_project_lod2_rotates_like_to_local():
    from shapely.geometry import Point

    from skylineframe.features import Lod2Building
    from skylineframe.project import local_transformer, project_lod2, to_local

    spec = FrameSpec(center_lat=50.0, center_lon=8.0, side_m=1000, rotation_deg=30)
    raw = Lod2Building(osm_id="lod2/hessen/x", surfaces=(((8.002, 50.002, 5.0),) * 3,))
    got = project_lod2(raw, local_transformer(spec), spec.rotation_deg)
    reference = to_local(Point(8.002, 50.002), spec)
    assert got.surfaces[0][0][:2] == pytest.approx((reference.x, reference.y), abs=1e-6)


def test_project_features_keeps_the_lod2_fields_of_a_building():
    from shapely.geometry import box

    from skylineframe.features import Building, Features

    spec = FrameSpec(center_lat=50.0, center_lon=8.0, side_m=1000)
    surfaces = (((8.0, 50.0, 1.0), (8.001, 50.0, 1.0), (8.001, 50.001, 2.0)),)
    building = Building(geom=box(8.0, 50.0, 8.001, 50.001), height_m=12.0, lod2=True, surfaces=surfaces)
    got = project_features(Features(buildings=[building]), spec).buildings[0]
    assert got.lod2 is True
    # Building.surfaces is not projected here: prepare reads them from Features.lod2, and
    # projecting the same rings twice would silently double the transform.
    assert got.surfaces == surfaces


def test_project_features_projects_and_rotates_osm_trees():
    from skylineframe.trees.model import OsmTree

    s = FrameSpec(center_lat=50.0, center_lon=8.0, side_m=1000, rotation_deg=30)
    feats = Features(trees=[OsmTree(8.002, 50.001, 11.0, 5.0), OsmTree(8.0, 50.0)])
    got = project_features(feats, s).trees
    reference = to_local(Point(8.002, 50.001), s)
    assert (got[0].x, got[0].y) == pytest.approx((reference.x, reference.y), abs=1e-6)
    assert (got[0].height_m, got[0].crown_m) == (11.0, 5.0)
    assert (got[1].x, got[1].y) == pytest.approx((0.0, 0.0), abs=1e-6)
    assert got[1].height_m is None and got[1].crown_m is None


def test_project_features_without_trees():
    assert project_features(Features(), spec()).trees == []
