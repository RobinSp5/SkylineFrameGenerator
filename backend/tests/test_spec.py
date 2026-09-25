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


def test_detail_defaults():
    s = FrameSpec(center_lat=50, center_lon=8)
    assert s.roofs is True
    assert s.parts is True
    assert s.min_footprint_area_mm2 == 0.25


def test_detail_flags_can_be_switched_off():
    s = FrameSpec(center_lat=50, center_lon=8, roofs=False, parts=False)
    assert s.roofs is False and s.parts is False


def test_presets_match_the_spec_table():
    from skylineframe.spec import PRESETS, Preset

    assert PRESETS[Preset.skyline] == (1500.0, 100.0)
    assert PRESETS[Preset.detail] == (800.0, 100.0)
    assert PRESETS[Preset.gross] == (1500.0, 200.0)
    assert set(PRESETS) == {"skyline", "detail", "gross"}


def test_lod2_is_on_by_default_and_can_be_switched_off():
    assert FrameSpec(center_lat=50, center_lon=8).lod2 is True
    assert FrameSpec(center_lat=50, center_lon=8, lod2=False).lod2 is False


def test_unknown_field_is_still_forbidden():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FrameSpec(center_lat=50, center_lon=8, lod3=True)


def test_terrain_is_on_by_default_with_its_own_exaggeration():
    # Spec 4b §2: terrain is on by default and scaled separately from the buildings.
    spec = FrameSpec(center_lat=50, center_lon=8)
    assert spec.terrain is True
    assert spec.terrain_exaggeration == 1.0
    off = FrameSpec(center_lat=50, center_lon=8, terrain=False, terrain_exaggeration=5)
    assert off.terrain is False and off.terrain_exaggeration == 5


@pytest.mark.parametrize("value", [0, -1, 5.01])
def test_terrain_exaggeration_is_limited_to_zero_to_five(value):
    with pytest.raises(ValidationError):
        FrameSpec(center_lat=50, center_lon=8, terrain_exaggeration=value)


def test_multicolor_is_on_by_default():
    # The default print is the Bambu Studio colour project; the switch only changes the 3MF.
    assert FrameSpec(center_lat=50, center_lon=8).multicolor is True
    assert FrameSpec(center_lat=50, center_lon=8, multicolor=False).multicolor is False


def test_overture_is_off_by_default_and_can_be_switched_on():
    assert FrameSpec(center_lat=50, center_lon=8).overture is False
    assert FrameSpec(center_lat=50, center_lon=8, overture=True).overture is True


def test_trees_are_on_by_default():
    # Spec 6 §2: trees belong in the model everywhere; trees=False is the model of before.
    assert FrameSpec(center_lat=50, center_lon=8).trees is True
    assert FrameSpec(center_lat=50, center_lon=8, trees=False).trees is False
