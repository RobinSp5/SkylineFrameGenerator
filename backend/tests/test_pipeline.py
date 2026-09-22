import pytest

from skylineframe.errors import PipelineError
from skylineframe.features import Features
from skylineframe.fetch import parse_overpass
from skylineframe.pipeline import run
from skylineframe.spec import FrameSpec, Mode


def test_run_frankfurt_offline(tmp_path, frankfurt_spec, frankfurt_data):
    stages: list[str] = []
    result = run(
        frankfurt_spec,
        out_dir=tmp_path / "out",
        cache_dir=tmp_path / "cache",
        progress=lambda stage, msg: stages.append(stage),
        fetch=lambda spec, cache_dir: parse_overpass(frankfurt_data, spec),
    )
    assert stages == ["fetch", "prepare", "mesh", "export"]
    assert result.paths.stl.exists() and result.paths.threemf.exists() and result.paths.glb.exists()
    assert result.stats["buildings"] > 20
    assert result.stats["buildings_individual"] == result.stats["buildings"]
    assert result.stats["blocks"] >= 1
    assert result.stats["roofs"] > 0  # the fixture has 60 roof:shape buildings
    assert result.stats["roads"] >= 1
    # The blocks are built around the road corridors, so the grooves survive (spec §6.4).
    assert result.stats["road_area_mm2"] > 100
    assert result.stats["stl_bytes"] > 10_000
    # Spec §6: the model must carry at least 95 % of the building area in the square.
    assert result.stats["footprint_coverage"] >= 0.95


def test_run_bankenviertel_has_building_parts(tmp_path, bankenviertel_spec, bankenviertel_data):
    result = run(
        bankenviertel_spec,
        tmp_path / "out",
        tmp_path / "cache",
        fetch=lambda s, c: parse_overpass(bankenviertel_data, s),
    )
    assert result.stats["parts"] > 0
    assert result.stats["footprint_coverage"] >= 0.95


def test_run_without_roofs_and_parts(tmp_path, bankenviertel_spec, bankenviertel_data):
    spec = bankenviertel_spec.model_copy(update={"roofs": False, "parts": False})
    result = run(spec, tmp_path / "out", tmp_path / "cache", fetch=lambda s, c: parse_overpass(bankenviertel_data, s))
    assert result.stats["roofs"] == 0
    assert result.stats["parts"] == 0
    assert result.stats["buildings"] > 0


def test_run_simple_mode_has_no_roads(tmp_path, frankfurt_spec, frankfurt_data):
    spec = frankfurt_spec.model_copy(update={"mode": Mode.simple})
    result = run(spec, tmp_path / "out", tmp_path / "cache", fetch=lambda s, c: parse_overpass(frankfurt_data, s))
    assert result.stats["roads"] == 0 and result.stats["water"] == 0


def test_run_without_buildings_raises_pipeline_error(tmp_path):
    spec = FrameSpec(center_lat=0, center_lon=0)
    with pytest.raises(PipelineError, match="No buildings"):
        run(spec, tmp_path / "out", tmp_path / "cache", fetch=lambda s, c: Features())
