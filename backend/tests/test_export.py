import pytest
import trimesh
from shapely.geometry import box

from skylineframe.errors import ExportError
from skylineframe.export import export_all, mesh_diagnostics, verify_part, verify_single
from skylineframe.mesh import build_meshes, to_trimesh
from skylineframe.scale import Prism, Scaled
from skylineframe.spec import FrameSpec, Mode

DEFAULTS = dict(center_lat=50, center_lon=8, side_m=1000, plate_size_mm=100, plate_thickness_mm=3.0, mode=Mode.full)


def spec(**kw) -> FrameSpec:
    return FrameSpec(**{**DEFAULTS, **kw})


@pytest.fixture
def meshset():
    scaled = Scaled(
        buildings=[Prism(box(-5, -5, 5, 5), 10.0), Prism(box(40, 40, 50, 50), 2.0)],
        roads=[box(-50, 10, 50, 11)],
        water=[box(-50, -50, -20, -20)],
    )
    return build_meshes(scaled, spec())


def test_export_writes_three_files(tmp_path, meshset):
    paths = export_all(meshset, spec(), tmp_path)
    assert paths.stl == tmp_path / "model.stl"
    assert paths.threemf == tmp_path / "model.3mf"
    assert paths.glb == tmp_path / "preview.glb"
    for p in (paths.stl, paths.threemf, paths.glb):
        assert p.exists() and p.stat().st_size > 0


def test_stl_reloads_watertight_with_plate_extents(tmp_path, meshset):
    paths = export_all(meshset, spec(), tmp_path)
    tm = trimesh.load(paths.stl, file_type="stl")
    assert tm.is_watertight
    assert tm.extents[0] == pytest.approx(100, abs=0.01)
    assert tm.extents[1] == pytest.approx(100, abs=0.01)
    assert tm.extents[2] == pytest.approx(13.0, abs=0.01)


def test_3mf_contains_named_parts(tmp_path, meshset):
    paths = export_all(meshset, spec(), tmp_path)
    scene = trimesh.load(paths.threemf, file_type="3mf")
    assert set(scene.geometry) == {"base", "buildings", "water", "roads"}


def test_glb_contains_all_parts(tmp_path, meshset):
    paths = export_all(meshset, spec(), tmp_path)
    scene = trimesh.load(paths.glb, file_type="glb")
    assert len(scene.geometry) == 4


def test_nothing_is_written_when_a_part_fails_verification(tmp_path, meshset, monkeypatch):
    def boom(tm, name):
        raise ExportError("boom")

    monkeypatch.setattr("skylineframe.export.verify_part", boom)
    out_dir = tmp_path / "job"
    with pytest.raises(ExportError, match="boom"):
        export_all(meshset, spec(), out_dir)
    assert list(out_dir.iterdir()) == []


def test_verify_rejects_wrong_plate_size(meshset):
    tm = to_trimesh(meshset.single)
    with pytest.raises(ExportError, match="plate"):
        verify_single(tm, spec(plate_size_mm=120))


def test_verify_part_rejects_non_watertight(meshset):
    tm = to_trimesh(meshset.base)
    broken = trimesh.Trimesh(vertices=tm.vertices, faces=tm.faces[:-1], process=False)
    with pytest.raises(ExportError, match="base"):
        verify_part(broken, "base")


def test_verify_rejects_non_watertight(meshset):
    tm = to_trimesh(meshset.single)
    broken = trimesh.Trimesh(vertices=tm.vertices, faces=tm.faces[:-1], process=False)
    with pytest.raises(ExportError, match="watertight"):
        verify_single(broken, spec())


def test_mesh_diagnostics_reports_a_clean_solid(meshset):
    assert mesh_diagnostics(to_trimesh(meshset.single)) == {"nonmanifold_edges": 0, "degenerate_faces": 0}


def test_export_all_reports_diagnostics(tmp_path, meshset):
    paths = export_all(meshset, spec(), tmp_path)
    assert paths.diagnostics["nonmanifold_edges"] == 0
    assert paths.diagnostics["degenerate_faces"] == 0


def test_export_writes_sources_txt(tmp_path, meshset):
    paths = export_all(meshset, spec(), tmp_path, sources="Zeile eins.\nZeile zwei.\n")
    assert paths.sources == tmp_path / "SOURCES.txt"
    assert paths.sources.read_text(encoding="utf-8") == "Zeile eins.\nZeile zwei.\n"


def test_export_writes_the_osm_notice_even_without_an_argument(tmp_path, meshset):
    paths = export_all(meshset, spec(), tmp_path)
    text = paths.sources.read_text(encoding="utf-8")
    assert "© OpenStreetMap-Mitwirkende, ODbL" in text
    assert "LoD2" not in text


def test_sources_txt_is_written_only_after_verification(tmp_path, meshset, monkeypatch):
    def boom(tm, name):
        raise ExportError("boom")

    monkeypatch.setattr("skylineframe.export.verify_part", boom)
    out_dir = tmp_path / "job"
    with pytest.raises(ExportError):
        export_all(meshset, spec(), out_dir, sources="x\n")
    assert list(out_dir.iterdir()) == []
