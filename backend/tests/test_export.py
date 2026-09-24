import dataclasses
import json
import re
import xml.etree.ElementTree as ET
import zipfile

import manifold3d as m3d
import numpy as np
import pytest
import trimesh
from shapely.geometry import Polygon, box

from skylineframe.errors import ExportError
from skylineframe.export import (
    FILAMENT_COLORS,
    PART_FILAMENTS,
    export_all,
    mesh_diagnostics,
    verify_part,
    verify_single,
)
from skylineframe.mesh import build_meshes, to_trimesh
from skylineframe.scale import Prism, Scaled
from skylineframe.spec import FrameSpec, Mode

NS = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
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


def _3mf_model(path) -> ET.Element:
    with zipfile.ZipFile(path) as z:
        return ET.fromstring(z.read("3D/3dmodel.model"))


def test_named_3mf_is_one_object_carrying_the_label(tmp_path, meshset):
    label = "Frankfurt am Main – Altstadt"
    paths = export_all(meshset, spec(), tmp_path, name=label)
    model = _3mf_model(paths.threemf)
    objects = {o.get("id"): o for o in model.iter(f"{NS}object")}
    items = list(model.iter(f"{NS}item"))
    # One build item: the slicer lists one object named after the place, with the parts inside.
    assert len(items) == 1
    top = objects[items[0].get("objectid")]
    assert top.get("name") == label
    children = [objects[c.get("objectid")].get("name") for c in top.iter(f"{NS}component")]
    assert sorted(children) == ["base", "buildings", "roads", "water"]
    # Trimesh (and so any other reader) still sees the four named parts.
    scene = trimesh.load(paths.threemf, file_type="3mf")
    assert set(scene.geometry) == {"base", "buildings", "water", "roads"}
    assert sum(g.volume for g in scene.geometry.values()) == pytest.approx(
        sum(to_trimesh(m).volume for m in meshset.parts().values()), rel=1e-6
    )


def test_unnamed_3mf_keeps_one_build_item_per_part(tmp_path, meshset):
    paths = export_all(meshset, spec(), tmp_path)
    items = list(_3mf_model(paths.threemf).iter(f"{NS}item"))
    assert len(items) == 4


def test_named_stl_carries_the_slug_in_its_header(tmp_path, meshset):
    paths = export_all(meshset, spec(), tmp_path, name="Frankfurt am Main – Altstadt")
    header = paths.stl.read_bytes()[:80]
    assert header.rstrip(b"\0 ").decode("ascii") == "Skyline Frame Frankfurt-am-Main_Altstadt"
    # A binary STL must never open with "solid", or readers take it for ASCII.
    assert not header.startswith(b"solid")
    tm = trimesh.load(paths.stl, file_type="stl")
    assert tm.is_watertight and len(tm.faces) == len(to_trimesh(meshset.single).faces)


def test_named_stl_header_is_capped_at_80_bytes(tmp_path, meshset):
    paths = export_all(meshset, spec(), tmp_path, name="Sehr langer Ortsname " * 10)
    tm = trimesh.load(paths.stl, file_type="stl")
    assert tm.is_watertight
    assert tm.extents[0] == pytest.approx(100, abs=0.01)


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


def _welded_faces(tm: trimesh.Trimesh) -> np.ndarray:
    """Faces over vertices welded by exact float32 position, the way a slicer reads an STL."""
    _, inverse = np.unique(np.asarray(tm.vertices, dtype=np.float32), axis=0, return_inverse=True)
    return inverse.ravel()[tm.faces]


def _welded_nonmanifold_edges(tm: trimesh.Trimesh) -> int:
    """Every welded edge not shared by exactly two faces; a face that collapses counts too."""
    faces = _welded_faces(tm)
    edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return int(np.count_nonzero(counts != 2))


def _welded_pinched_vertices(tm: trimesh.Trimesh) -> int:
    """Welded vertices whose faces do not form one fan: two solids that touch in a point.
    Collapsed faces are left out; _welded_nonmanifold_edges already counts them."""
    faces = _welded_faces(tm)
    faces = faces[(faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 2] != faces[:, 0])]
    pinched = 0
    for vertex in np.unique(faces):
        links = [tuple(int(w) for w in face if w != vertex) for face in faces if vertex in face]
        graph = {}
        for a, b in links:
            graph.setdefault(a, set()).add(b)
            graph.setdefault(b, set()).add(a)
        seen, stack = set(), [next(iter(graph))]
        while stack:
            node = stack.pop()
            if node not in seen:
                seen.add(node)
                stack.extend(graph[node] - seen)
        pinched += len(seen) != len(graph)
    return pinched


# Two footprints that touch along one vertical edge, and a raised part that touches its base
# only in one corner point: the pinches manifold3d keeps as coincident but distinct vertices.
# "sliver" has an outline edge of 0.1 µm, below what float32 resolves at 10 mm, the way the
# footprint cut leaves them on LoD2 bodies: welded, its wall faces collapse.
PINCHES = {
    "edge": [Prism(box(-10, -10, 0, 0), 5.0), Prism(box(0, 0, 10, 10), 5.0)],
    "corner": [Prism(box(-10, -10, 0, 0), 5.0), Prism(box(0, 0, 10, 10), 8.0, z0_mm=5.0)],
    "sliver": [Prism(Polygon([(0, 0), (10, 0), (10, 10), (10 - 1e-7, 10), (0, 10)]), 5.0)],
}


@pytest.mark.parametrize("case", sorted(PINCHES))
def test_pinch_cases_break_a_slicer_weld_as_modelled(case):
    single = to_trimesh(build_meshes(Scaled(buildings=PINCHES[case]), spec(mode=Mode.simple)).single)
    assert _welded_nonmanifold_edges(single) + _welded_pinched_vertices(single) > 0


@pytest.mark.parametrize("case", sorted(PINCHES))
def test_exported_stl_is_two_manifold_after_welding(tmp_path, case):
    meshes = build_meshes(Scaled(buildings=PINCHES[case]), spec(mode=Mode.simple))
    paths = export_all(meshes, spec(mode=Mode.simple), tmp_path)
    tm = trimesh.load(paths.stl, file_type="stl", process=False)
    assert _welded_nonmanifold_edges(tm) == 0
    assert _welded_pinched_vertices(tm) == 0
    assert paths.diagnostics["nonmanifold_edges"] == 0
    assert paths.diagnostics["degenerate_faces"] == 0


@pytest.mark.parametrize("case", sorted(PINCHES))
def test_exported_3mf_parts_are_two_manifold_after_welding(tmp_path, case):
    meshes = build_meshes(Scaled(buildings=PINCHES[case]), spec(mode=Mode.simple))
    paths = export_all(meshes, spec(mode=Mode.simple), tmp_path)
    scene = trimesh.load(paths.threemf, file_type="3mf", process=False)
    for name, part in scene.geometry.items():
        assert _welded_nonmanifold_edges(part) == 0, name
        assert _welded_pinched_vertices(part) == 0, name
    assert paths.diagnostics["nonmanifold_edges_3mf"] == 0


def test_mesh_diagnostics_counts_a_pinch_as_a_slicer_does():
    # The raw union: the edge the two boxes share is one edge for a slicer, with four faces.
    meshes = build_meshes(Scaled(buildings=PINCHES["edge"]), spec(mode=Mode.simple))
    assert mesh_diagnostics(to_trimesh(meshes.single))["nonmanifold_edges"] > 0


def test_export_keeps_the_shape_of_a_pinched_model(tmp_path):
    meshes = build_meshes(Scaled(buildings=PINCHES["edge"]), spec(mode=Mode.simple))
    paths = export_all(meshes, spec(mode=Mode.simple), tmp_path)
    tm = trimesh.load(paths.stl, file_type="stl")  # merges vertices, so watertight is meaningful
    raw = to_trimesh(meshes.single)
    assert tm.is_watertight
    assert np.allclose(tm.bounds, raw.bounds, atol=0.01)
    assert tm.volume == pytest.approx(raw.volume, abs=0.01)


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


# --- multicolour 3MF for Bambu Studio -------------------------------------------------------------

BAMBU_ENTRIES = {"Metadata/model_settings.config", "Metadata/project_settings.config"}


@pytest.fixture
def meshset_with_trees(meshset):
    # A free-standing canopy on the plate: its own watertight part, like the real tree layer.
    return dataclasses.replace(meshset, trees=m3d.Manifold.cube((4.0, 4.0, 2.0)).translate((20.0, -30.0, 3.0)))


def _zip_entries(path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def _without_uuids(data: bytes) -> bytes:
    return re.sub(rb' p:UUID="[^"]*"', b"", data)


def _model_settings(path) -> ET.Element:
    with zipfile.ZipFile(path) as z:
        return ET.fromstring(z.read("Metadata/model_settings.config"))


def _project_settings(path) -> dict:
    with zipfile.ZipFile(path) as z:
        return json.loads(z.read("Metadata/project_settings.config"))


def test_multicolor_is_off_by_default_and_leaves_the_3mf_as_before(tmp_path, meshset):
    label = "Frankfurt am Main"
    before = export_all(meshset, spec(), tmp_path / "a", name=label)
    off = export_all(meshset, spec(multicolor=False), tmp_path / "b", name=label)
    a, b = _zip_entries(before.threemf), _zip_entries(off.threemf)
    assert a.keys() == b.keys() == {"3D/3dmodel.model", "_rels/.rels", "[Content_Types].xml"}
    # Trimesh writes a random p:UUID per object; everything else is the same, byte for byte.
    assert {n: _without_uuids(d) for n, d in a.items()} == {n: _without_uuids(d) for n, d in b.items()}
    assert b"BambuStudio" not in b["3D/3dmodel.model"]


def test_multicolor_3mf_is_one_bambu_object_with_parts_on_their_filaments(tmp_path, meshset_with_trees):
    label = "Frankfurt am Main – Altstadt & Main"
    paths = export_all(meshset_with_trees, spec(multicolor=True), tmp_path, name=label)
    assert BAMBU_ENTRIES <= _zip_entries(paths.threemf).keys()
    model = _3mf_model(paths.threemf)
    apps = [m.text for m in model.iter(f"{NS}metadata") if m.get("name") == "Application"]
    assert len(apps) == 1 and apps[0].startswith("BambuStudio-")  # Bambu reads its own configs only then
    items = list(model.iter(f"{NS}item"))
    assert len(items) == 1
    objects = {o.get("id"): o for o in model.iter(f"{NS}object")}
    top = objects[items[0].get("objectid")]
    part_ids = {objects[c.get("objectid")].get("name"): c.get("objectid") for c in top.iter(f"{NS}component")}
    assert set(part_ids) == {"base", "buildings", "roads", "water", "trees"}

    settings = _model_settings(paths.threemf)
    (obj,) = settings.findall("object")
    assert obj.get("id") == top.get("id")
    meta = {m.get("key"): m.get("value") for m in obj.findall("metadata")}
    assert meta["name"] == label
    parts = {p.get("id"): p for p in obj.findall("part")}
    assert set(parts) == set(part_ids.values())
    extruder = {}
    for name, pid in part_ids.items():
        pmeta = {m.get("key"): m.get("value") for m in parts[pid].findall("metadata")}
        assert parts[pid].get("subtype") == "normal_part"
        assert pmeta["name"] == name
        extruder[name] = pmeta["extruder"]
    assert extruder == {"base": "1", "buildings": "1", "roads": "1", "trees": "2", "water": "3"}


def test_multicolor_project_sets_the_x2d_and_three_coloured_pla_filaments(tmp_path, meshset_with_trees):
    paths = export_all(meshset_with_trees, spec(multicolor=True), tmp_path, name="Frankfurt")
    project = _project_settings(paths.threemf)
    assert project["printer_settings_id"] == "Bambu Lab X2D 0.4 nozzle"
    assert project["printer_model"] == "Bambu Lab X2D"
    assert project["filament_colour"] == list(FILAMENT_COLORS)
    assert project["filament_multi_colour"] == list(FILAMENT_COLORS)
    assert project["filament_type"] == ["PLA", "PLA", "PLA"]
    assert len(project["filament_settings_id"]) == 3
    # Every per-filament list must agree on three filaments, or Bambu Studio fails to slice. The
    # filament_mixed_* lists hold one global entry in every Bambu project, whatever the count.
    per_filament = [k for k, v in project.items() if k.startswith("filament_") and isinstance(v, list) and len(v) > 1]
    assert len(per_filament) > 50
    assert all(len(project[k]) % 3 == 0 for k in per_filament)
    assert FILAMENT_COLORS == ("#F2F2F2", "#4E7D3A", "#3F7FBF")
    assert PART_FILAMENTS == {"base": 1, "buildings": 1, "roads": 1, "trees": 2, "water": 3}


@pytest.mark.parametrize("plate_mm", [100, 200])
def test_multicolor_puts_the_model_at_the_back_and_the_prime_tower_in_front(tmp_path, plate_mm):
    # A Bambu project is not re-centred on import: the build item must put the model on the bed.
    # A 200 mm model in the middle of the 256 mm X2D bed leaves no room for the 35 mm prime
    # tower, and Bambu Studio reports a conflict; at the back it leaves 50 mm in front.
    s = spec(plate_size_mm=plate_mm, side_m=plate_mm * 10)
    meshes = build_meshes(Scaled(buildings=[Prism(box(-5, -5, 5, 5), 10.0)]), s)
    paths = export_all(meshes, s.model_copy(update={"multicolor": True}), tmp_path, name="Frankfurt")
    lo, hi = trimesh.load(paths.threemf, file_type="3mf").bounds
    assert (lo[0] + hi[0]) / 2 == pytest.approx(128.0, abs=1e-6)
    assert hi[1] == pytest.approx(254.0, abs=1e-6)
    assert lo[2] == pytest.approx(0.0, abs=1e-6)
    project = _project_settings(paths.threemf)
    assert project["prime_tower_width"] == "35"
    assert (project["wipe_tower_x"], project["wipe_tower_y"]) == (["110.5"], ["5"])


def test_multicolor_keeps_the_geometry_and_the_stl(tmp_path, meshset_with_trees):
    off = export_all(meshset_with_trees, spec(), tmp_path / "a", name="Frankfurt")
    on = export_all(meshset_with_trees, spec(multicolor=True), tmp_path / "b", name="Frankfurt")
    assert off.stl.read_bytes() == on.stl.read_bytes()
    scene = trimesh.load(on.threemf, file_type="3mf")
    assert set(scene.geometry) == {"base", "buildings", "water", "roads", "trees"}
    assert sum(g.volume for g in scene.geometry.values()) == pytest.approx(
        sum(to_trimesh(m).volume for m in meshset_with_trees.parts().values()), rel=1e-6
    )


def test_multicolor_without_a_name_is_still_one_object(tmp_path, meshset):
    paths = export_all(meshset, spec(multicolor=True), tmp_path)
    assert len(list(_3mf_model(paths.threemf).iter(f"{NS}item"))) == 1
    (obj,) = _model_settings(paths.threemf).findall("object")
    assert {m.get("key"): m.get("value") for m in obj.findall("metadata")}["name"] == "Skyline Frame"


def test_printable_drops_shells_without_material():
    # Boolean artefacts: closed shells of practically zero volume a slicer counts as extra parts.
    from skylineframe.mesh import MIN_SHELL_VOLUME_MM3, printable

    body = m3d.Manifold.cube((10.0, 10.0, 5.0))
    sliver = m3d.Manifold.cube((0.1, 0.1, 1e-4)).translate((20.0, 0.0, 0.0))
    out = printable(m3d.Manifold.compose([body, sliver]))
    assert len(out.decompose()) == 1
    assert out.volume() == pytest.approx(500.0)
    assert sliver.volume() < MIN_SHELL_VOLUME_MM3
