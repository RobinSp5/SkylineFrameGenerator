from pathlib import Path

from typer.testing import CliRunner

from skylineframe import cli
from skylineframe.errors import AreaError, FetchError
from skylineframe.export import ExportPaths
from skylineframe.pipeline import RunResult

runner = CliRunner()


def test_generate_prints_paths(monkeypatch, tmp_path):
    captured = {}

    def fake_run(spec, out_dir, cache_dir, progress=None, name=None):
        captured["spec"] = spec
        if progress:
            progress("fetch", "loading")
        return RunResult(ExportPaths(out_dir / "model.stl", out_dir / "model.3mf", out_dir / "preview.glb"), {"buildings": 3})

    monkeypatch.setattr(cli, "run", fake_run)
    result = runner.invoke(cli.app, ["--lat", "50.1", "--lon", "8.6", "--side", "800", "--mode", "full", "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert captured["spec"].side_m == 800 and captured["spec"].mode == "full"
    assert "[fetch] loading" in result.output
    assert "model.stl" in result.output


def test_generate_reports_errors(monkeypatch, tmp_path):
    def failing_run(spec, out_dir, cache_dir, progress=None, name=None):
        raise FetchError("Overpass down")

    monkeypatch.setattr(cli, "run", failing_run)
    result = runner.invoke(cli.app, ["--lat", "50.1", "--lon", "8.6", "--out", str(tmp_path)])
    assert result.exit_code == 1
    assert "Overpass down" in result.output


def test_generate_reports_invalid_spec(tmp_path):
    result = runner.invoke(cli.app, ["--lat", "50.1", "--lon", "8.6", "--side", "100", "--out", str(tmp_path)])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "side_m" in result.output
    assert "Traceback" not in result.output


def test_generate_reports_area_error(monkeypatch, tmp_path):
    def failing_run(spec, out_dir, cache_dir, progress=None, name=None):
        raise AreaError("Areas crossing the antimeridian (±180° longitude) are not supported.")

    monkeypatch.setattr(cli, "run", failing_run)
    result = runner.invoke(cli.app, ["--lat", "50.1", "--lon", "8.6", "--out", str(tmp_path)])
    assert result.exit_code == 1
    assert "antimeridian" in result.output


def run_cli(monkeypatch, tmp_path, args: list[str]) -> tuple[object, object]:
    """Invoke the CLI with a stubbed pipeline and return (captured spec, result)."""
    captured = {}

    def fake_run(spec, out_dir, cache_dir, progress=None, name=None):
        captured["spec"] = spec
        return RunResult(
            ExportPaths(out_dir / "model.stl", out_dir / "model.3mf", out_dir / "preview.glb"),
            {
                "buildings": 3,
                "blocks": 2,
                "parts": 1,
                "roofs": 4,
                "roads": 5,
                "road_area_mm2": 1234.5,
                "footprint_coverage": 0.97,
                "lod2_buildings": 612,
                "lod2_source": "hessen",
                "lod2_rejected": 3,
                "lod2_triangles": 412000,
            },
        )

    monkeypatch.setattr(cli, "run", fake_run)
    result = runner.invoke(cli.app, ["--lat", "50.1", "--lon", "8.6", "--out", str(tmp_path), *args])
    assert result.exit_code == 0, result.output
    return captured["spec"], result


def test_preset_sets_side_and_plate(monkeypatch, tmp_path):
    spec, _ = run_cli(monkeypatch, tmp_path, ["--preset", "detail"])
    assert (spec.side_m, spec.plate_size_mm) == (800, 100)
    spec, _ = run_cli(monkeypatch, tmp_path, ["--preset", "gross"])
    assert (spec.side_m, spec.plate_size_mm) == (1500, 200)


def test_explicit_side_and_plate_beat_the_preset(monkeypatch, tmp_path):
    spec, _ = run_cli(monkeypatch, tmp_path, ["--preset", "detail", "--side", "1200", "--plate", "150"])
    assert (spec.side_m, spec.plate_size_mm) == (1200, 150)


def test_defaults_without_preset(monkeypatch, tmp_path):
    spec, _ = run_cli(monkeypatch, tmp_path, [])
    assert (spec.side_m, spec.plate_size_mm) == (1500, 100)
    assert spec.roofs is True and spec.parts is True


def test_roofs_and_parts_can_be_switched_off(monkeypatch, tmp_path):
    spec, _ = run_cli(monkeypatch, tmp_path, ["--no-roofs", "--no-parts"])
    assert spec.roofs is False and spec.parts is False


def test_generate_prints_the_detail_stats(monkeypatch, tmp_path):
    _, result = run_cli(monkeypatch, tmp_path, [])
    assert "Buildings: 3" in result.output
    assert "Blocks: 2" in result.output
    assert "Parts: 1" in result.output
    assert "Roofs: 4" in result.output
    assert "Roads: 5 (1234 mm²)" in result.output  # .0f rounds 1234.5 to even
    assert "Footprint coverage: 97.00%" in result.output


def test_lod2_is_on_by_default_and_can_be_switched_off(monkeypatch, tmp_path):
    spec, _ = run_cli(monkeypatch, tmp_path, [])
    assert spec.lod2 is True
    spec, _ = run_cli(monkeypatch, tmp_path, ["--no-lod2"])
    assert spec.lod2 is False


def test_overture_is_off_by_default_and_can_be_switched_on(monkeypatch, tmp_path):
    spec, _ = run_cli(monkeypatch, tmp_path, [])
    assert spec.overture is False
    spec, _ = run_cli(monkeypatch, tmp_path, ["--overture"])
    assert spec.overture is True


def test_generate_reports_overture_buildings_only_when_asked_for(monkeypatch, tmp_path):
    stats = {"buildings": 3, "overture_buildings": 42, "overture_source": "overture"}
    result = _run_with_stats(monkeypatch, tmp_path, stats, ["--overture"])
    assert "Overture buildings: 42" in result.output
    result = _run_with_stats(monkeypatch, tmp_path, {"buildings": 3, "overture_buildings": 0, "overture_source": ""}, [])
    assert "Overture" not in result.output


def test_generate_prints_the_lod2_source(monkeypatch, tmp_path):
    _, result = run_cli(monkeypatch, tmp_path, [])
    assert "LoD2 source: hessen" in result.output
    assert "LoD2 buildings: 612 with a body, 3 fell back to a prism" in result.output
    assert "LoD2 triangles: 412000" in result.output


def test_generate_says_so_when_no_lod2_source_was_used(monkeypatch, tmp_path):
    def fake_run(spec, out_dir, cache_dir, progress=None, name=None):
        return RunResult(
            ExportPaths(out_dir / "model.stl", out_dir / "model.3mf", out_dir / "preview.glb"),
            {"buildings": 3, "lod2_source": ""},
        )

    monkeypatch.setattr(cli, "run", fake_run)
    result = runner.invoke(cli.app, ["--lat", "50.1", "--lon", "8.6", "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "LoD2 source: none (OpenStreetMap only)" in result.output


def test_terrain_is_on_by_default_and_can_be_switched_off(monkeypatch, tmp_path):
    spec, _ = run_cli(monkeypatch, tmp_path, [])
    assert spec.terrain is True and spec.terrain_exaggeration == 1.0
    spec, _ = run_cli(monkeypatch, tmp_path, ["--no-terrain", "--terrain-z", "2.5"])
    assert spec.terrain is False and spec.terrain_exaggeration == 2.5


def test_print_optimization_is_on_by_default_and_can_be_switched_off(monkeypatch, tmp_path):
    spec, _ = run_cli(monkeypatch, tmp_path, [])
    assert spec.print_optimized is True and spec.min_line_mm == 0.8
    spec, _ = run_cli(monkeypatch, tmp_path, ["--no-optimize"])
    assert spec.print_optimized is False and spec.min_line_mm == 0.4


def _run_with_stats(monkeypatch, tmp_path, stats: dict, args: list[str]):
    def fake_run(spec, out_dir, cache_dir, progress=None, name=None):
        return RunResult(ExportPaths(out_dir / "model.stl", out_dir / "model.3mf", out_dir / "preview.glb"), stats)

    monkeypatch.setattr(cli, "run", fake_run)
    result = runner.invoke(cli.app, ["--lat", "50.1", "--lon", "8.6", "--out", str(tmp_path), *args])
    assert result.exit_code == 0, result.output
    return result


def test_generate_prints_the_terrain_source_and_relief(monkeypatch, tmp_path):
    stats = {"buildings": 3, "terrain_source": "copernicus", "terrain_relief_mm": 10.84}
    result = _run_with_stats(monkeypatch, tmp_path, stats, ["--terrain"])
    assert "Terrain: copernicus, relief 10.84 mm" in result.output


def test_generate_prints_the_note_when_terrain_was_unavailable(monkeypatch, tmp_path):
    # Spec 4b §5.6: offline with --terrain is a flat model plus a note, never an abort.
    stats = {"buildings": 3, "terrain_source": "", "terrain_note": "Gelände nicht verfügbar"}
    result = _run_with_stats(monkeypatch, tmp_path, stats, ["--terrain"])
    assert "Terrain: Gelände nicht verfügbar (flat plate)" in result.output


def test_generate_is_silent_about_terrain_when_it_was_not_asked_for(monkeypatch, tmp_path):
    result = _run_with_stats(monkeypatch, tmp_path, {"buildings": 3, "terrain_source": ""}, [])
    assert "Terrain" not in result.output


def test_trees_are_on_by_default_and_can_be_switched_off(monkeypatch, tmp_path):
    spec, _ = run_cli(monkeypatch, tmp_path, [])
    assert spec.trees is True
    spec, _ = run_cli(monkeypatch, tmp_path, ["--no-trees"])
    assert spec.trees is False


def test_generate_prints_the_trees_and_where_they_come_from(monkeypatch, tmp_path):
    result = _run_with_stats(monkeypatch, tmp_path, {"buildings": 3, "trees": 1234, "trees_source": "worldcover"}, [])
    assert "Trees: 1234 (ESA WorldCover and OpenStreetMap)" in result.output
    result = _run_with_stats(monkeypatch, tmp_path, {"buildings": 3, "trees": 12, "trees_source": ""}, [])
    assert "Trees: 12 (OpenStreetMap)" in result.output
    stats = {"buildings": 3, "trees": 12, "trees_source": "", "trees_note": "WorldCover nicht verfügbar"}
    result = _run_with_stats(monkeypatch, tmp_path, stats, [])
    assert "Trees: 12 (OpenStreetMap), WorldCover nicht verfügbar" in result.output


def test_generate_is_silent_about_trees_when_they_were_switched_off(monkeypatch, tmp_path):
    result = _run_with_stats(monkeypatch, tmp_path, {"buildings": 3, "trees": 0, "trees_source": ""}, ["--no-trees"])
    assert "Trees" not in result.output


def test_multicolor_is_on_by_default_and_can_be_switched_off(monkeypatch, tmp_path):
    spec, result = run_cli(monkeypatch, tmp_path, [])
    assert spec.multicolor is True
    # Tells the user which spools to load, in the order Bambu Studio numbers them.
    assert (
        "Filaments: 1 stone (base), 2 terracotta (buildings), 3 charcoal (roads), 4 green (trees), "
        "5 blue (water), 6 rust (buildings_verified)"
    ) in result.output
    spec, result = run_cli(monkeypatch, tmp_path, ["--no-multicolor"])
    assert spec.multicolor is False
    assert "Filaments" not in result.output


def _captured_name(monkeypatch, tmp_path, args: list[str]):
    captured = {}

    def fake_run(spec, out_dir, cache_dir, progress=None, name=None):
        captured["name"] = name
        return RunResult(ExportPaths(out_dir / "model.stl", out_dir / "model.3mf", out_dir / "preview.glb"), {})

    monkeypatch.setattr(cli, "run", fake_run)
    result = runner.invoke(cli.app, ["--lat", "50.1", "--lon", "8.6", "--out", str(tmp_path), *args])
    assert result.exit_code == 0, result.output
    return captured["name"]


def test_name_is_passed_to_the_pipeline(monkeypatch, tmp_path):
    assert _captured_name(monkeypatch, tmp_path, ["--name", "Frankfurt am Main – Altstadt"]) == "Frankfurt am Main – Altstadt"


def test_without_a_name_the_cli_does_not_look_one_up(monkeypatch, tmp_path):
    # The CLI stays offline for naming: no reverse geocoding, the files keep their model.* names.
    assert _captured_name(monkeypatch, tmp_path, []) is None
