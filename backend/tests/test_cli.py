from pathlib import Path

from typer.testing import CliRunner

from skylineframe import cli
from skylineframe.errors import AreaError, FetchError
from skylineframe.export import ExportPaths
from skylineframe.pipeline import RunResult

runner = CliRunner()


def test_generate_prints_paths(monkeypatch, tmp_path):
    captured = {}

    def fake_run(spec, out_dir, cache_dir, progress=None):
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
    def failing_run(spec, out_dir, cache_dir, progress=None):
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
    def failing_run(spec, out_dir, cache_dir, progress=None):
        raise AreaError("Areas crossing the antimeridian (±180° longitude) are not supported.")

    monkeypatch.setattr(cli, "run", failing_run)
    result = runner.invoke(cli.app, ["--lat", "50.1", "--lon", "8.6", "--out", str(tmp_path)])
    assert result.exit_code == 1
    assert "antimeridian" in result.output


def run_cli(monkeypatch, tmp_path, args: list[str]) -> tuple[object, object]:
    """Invoke the CLI with a stubbed pipeline and return (captured spec, result)."""
    captured = {}

    def fake_run(spec, out_dir, cache_dir, progress=None):
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
    assert "Footprint coverage: 97.0%" in result.output
