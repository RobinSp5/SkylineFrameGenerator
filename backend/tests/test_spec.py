import pytest
from pydantic import ValidationError

from skylineframe.spec import DEFAULT_ROAD_WIDTH_MM, FrameSpec, Mode


def test_defaults():
    s = FrameSpec(center_lat=50.11, center_lon=8.68)
    assert s.side_m == 1500
    assert s.plate_size_mm == 100
    assert s.plate_thickness_mm == 3.0
    assert s.mode == Mode.simple
    assert s.z_exaggeration == 1.5
    assert s.road_width_mm == DEFAULT_ROAD_WIDTH_MM


def test_scale_is_mm_per_metre():
    s = FrameSpec(center_lat=50.11, center_lon=8.68, side_m=1000, plate_size_mm=100)
    assert s.scale == pytest.approx(0.1)


def test_rejects_side_out_of_range():
    with pytest.raises(ValidationError):
        FrameSpec(center_lat=50, center_lon=8, side_m=100)
    with pytest.raises(ValidationError):
        FrameSpec(center_lat=50, center_lon=8, side_m=6000)


def test_rejects_unknown_road_class():
    with pytest.raises(ValidationError):
        FrameSpec(center_lat=50, center_lon=8, road_width_mm={"autobahn": 2.0})


def test_rejects_road_width_below_min_feature():
    with pytest.raises(ValidationError):
        FrameSpec(center_lat=50, center_lon=8, road_width_mm={"primary": 0.5})


def test_partial_road_widths_merge_with_defaults():
    s = FrameSpec(center_lat=50, center_lon=8, road_width_mm={"primary": 2.5})
    assert s.road_width_mm["primary"] == 2.5
    assert s.road_width_mm["service"] == DEFAULT_ROAD_WIDTH_MM["service"]


def test_rejects_recess_deeper_than_plate():
    with pytest.raises(ValidationError):
        FrameSpec(center_lat=50, center_lon=8, plate_thickness_mm=1.0, water_depth_mm=1.0)
    with pytest.raises(ValidationError):
        FrameSpec(center_lat=50, center_lon=8, plate_thickness_mm=1.0, road_depth_mm=1.5)


def test_rejects_unknown_field():
    # The spec is built straight from the request body, so an unknown key is a typo in the
    # client (or a stale field name) and must not be silently ignored.
    with pytest.raises(ValidationError):
        FrameSpec(center_lat=50, center_lon=8, plate_size_cm=10)


def test_mode_accepts_string():
    s = FrameSpec(center_lat=50, center_lon=8, mode="full")
    assert s.mode is Mode.full
