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
