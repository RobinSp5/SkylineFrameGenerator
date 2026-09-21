import pytest
from shapely.geometry import box

from skylineframe.features import Building
from skylineframe.prepare import Prepared
from skylineframe.scale import building_height_mm, scale_features
from skylineframe.spec import FrameSpec


def spec(**kw) -> FrameSpec:
    return FrameSpec(center_lat=50, center_lon=8, side_m=1000, plate_size_mm=100, **kw)  # 0.1 mm/m


def test_height_applies_scale_and_exaggeration():
    assert building_height_mm(10.0, spec(z_exaggeration=1.5)) == pytest.approx(1.5)


def test_height_enforces_minimum():
    assert building_height_mm(2.0, spec(min_building_height_mm=0.8)) == pytest.approx(0.8)


def test_height_rounds_to_hundredth():
    assert building_height_mm(3.333, spec(z_exaggeration=1.0)) == pytest.approx(0.8)  # 0.3333 -> min
    assert building_height_mm(12.345, spec(z_exaggeration=1.0)) == pytest.approx(1.23)


def test_footprints_are_scaled_about_origin():
    prepared = Prepared(buildings=[Building(box(-500, -500, 500, 500), 10.0)])
    out = scale_features(prepared, spec())
    assert out.buildings[0].geom.bounds == pytest.approx((-50, -50, 50, 50))
    assert out.buildings[0].height_mm == pytest.approx(1.5)


def test_roads_and_water_are_scaled():
    prepared = Prepared(buildings=[], roads=[box(0, 0, 100, 10)], water=[box(-500, -500, 0, 0)])
    out = scale_features(prepared, spec())
    assert out.roads[0].bounds == pytest.approx((0, 0, 10, 1))
    assert out.water[0].bounds == pytest.approx((-50, -50, 0, 0))
