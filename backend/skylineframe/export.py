"""Write STL (single colour), 3MF (named parts) and GLB (coloured preview) after verifying the solid."""

import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from xml.sax import saxutils

import numpy as np
import trimesh

from .errors import ExportError
from .lod2.sources import SOURCES_FILENAME, sources_text
from .mesh import MeshSet, printable, to_trimesh
from .naming import slugify
from .spec import FrameSpec

PART_COLORS: dict[str, tuple[int, int, int, int]] = {
    "base": (232, 228, 217, 255),
    "buildings": (201, 124, 93, 255),
    "buildings_verified": (139, 69, 19, 255),
    "water": (63, 127, 191, 255),
    "roads": (43, 43, 43, 255),
    "trees": (78, 125, 58, 255),
}
# Multicolour 3MF (FrameSpec.multicolor): the AMS filament each part prints with, numbered the
# way Bambu Studio numbers them, and the colour of each filament. One filament per part, so a
# slicer or a print shows the city's structure — buildings, roads, water, trees — at a glance.
# buildings_verified keeps 6 last rather than slotting in after buildings: the five original
# filament numbers stay exactly what they were before this part existed.
PART_ORDER = ("base", "buildings", "roads", "trees", "water", "buildings_verified")
PART_FILAMENTS: dict[str, int] = {"base": 1, "buildings": 2, "roads": 3, "trees": 4, "water": 5, "buildings_verified": 6}
FILAMENT_COLORS = ("#E8E4D9", "#C97C5D", "#2B2B2B", "#4E7D3A", "#3F7FBF", "#8B4513")
FILAMENT_NAMES = ("stone", "terracotta", "charcoal", "green", "blue", "rust")
DEFAULT_OBJECT_NAME = "Skyline Frame"
BAMBU_BED_MM = 256.0  # the X2D bed is square
BAMBU_BACK_MARGIN_MM = 2.0
# Front-left corner of the prime tower: centred left to right in front of the model. The tower is
# 35 mm wide (prime_tower_width in the template); a 200 mm model leaves it 49 mm of depth.
BAMBU_PRIME_TOWER_MM = (BAMBU_BED_MM / 2 - 17.5, 5.0)
BAMBU_APPLICATION = "BambuStudio-02.08.02.61"  # the version the project settings come from
BAMBU_NAMESPACE = "http://schemas.bambulab.com/package/2021"
PROJECT_SETTINGS = Path(__file__).with_name("bambu_x2d_project_settings.json")
OBJECT_TAG = re.compile(rb'<object id="(\d+)" name="([^"]*)"')
SIZE_TOLERANCE_MM = 0.01
DEGENERATE_AREA_MM2 = 1e-9
STL_HEADER_LEN = 80
STL_HEADER_PREFIX = "Skyline Frame "


@dataclass
class ExportPaths:
    stl: Path
    threemf: Path
    glb: Path
    diagnostics: dict = field(default_factory=dict)
    sources: Path | None = None  # SOURCES.txt, written next to the model (spec §7)


def verify_single(tm: trimesh.Trimesh, spec: FrameSpec) -> None:
    if not tm.is_watertight or not tm.is_volume:
        raise ExportError("Resulting mesh is not watertight; please try a slightly different area.")
    size = spec.plate_size_mm
    if abs(tm.extents[0] - size) > SIZE_TOLERANCE_MM or abs(tm.extents[1] - size) > SIZE_TOLERANCE_MM:
        raise ExportError(f"Model footprint {tm.extents[0]:.2f}x{tm.extents[1]:.2f} mm does not match plate size {size} mm.")
    if tm.volume <= size * size * spec.plate_thickness_mm * 0.5:
        raise ExportError("Model has no volume above the plate.")


def mesh_diagnostics(tm: trimesh.Trimesh) -> dict:
    """Slicer-style health check: weld the vertices first, then count broken topology.

    Verification runs on the manifold topology, where solids that merely touch keep their own
    vertices. An STL has no indices, so a slicer welds by position — exactly, in float32, which
    is all the file stores — and only then sees whether an edge is shared by exactly two faces.
    A face whose corners weld together counts as degenerate, and its edges as non-manifold.
    export_all runs every mesh through mesh.printable first, so both numbers are 0 there.
    """
    _, inverse = np.unique(np.asarray(tm.vertices, dtype=np.float32), axis=0, return_inverse=True)
    faces = inverse.ravel()[np.asarray(tm.faces)]
    edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    corners = np.asarray(tm.vertices, dtype=np.float32).astype(np.float64)[np.asarray(tm.faces)]
    area = 0.5 * np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1)
    return {
        "nonmanifold_edges": int(np.count_nonzero(counts != 2)),
        "degenerate_faces": int(np.count_nonzero(area < DEGENERATE_AREA_MM2)),
    }


def verify_part(tm: trimesh.Trimesh, name: str) -> None:
    if not tm.is_watertight or not tm.is_volume:
        raise ExportError(f"Part '{name}' is not watertight; please try a slightly different area.")


def threemf_scene(
    parts: dict[str, trimesh.Trimesh], name: str | None, offset: tuple[float, float, float] | None = None
) -> trimesh.Scene:
    """The parts as a 3MF scene; with a name, grouped under one object that carries it.

    Trimesh writes a node with children as a 3MF component object and puts only the nodes on the
    base frame into the build, so the slicer lists a single object named after the place with the
    parts inside it. Without a name the parts stay separate top-level objects, as before. `offset`
    moves the named object in the build item's transform; the meshes themselves stay put.
    """
    if name is None:
        return trimesh.Scene(parts)
    scene = trimesh.Scene()
    matrix = None if offset is None else trimesh.transformations.translation_matrix(offset)
    scene.graph.update(frame_from=scene.graph.base_frame, frame_to=name, matrix=matrix)
    for part, tm in parts.items():
        scene.add_geometry(tm, geom_name=part, node_name=part, parent_node_name=name)
    return scene


def filament_summary() -> str:
    """Which spool goes where, in Bambu Studio's filament numbering."""
    groups = []
    for number, colour in enumerate(FILAMENT_NAMES, start=1):
        parts = ", ".join(p for p in PART_ORDER if PART_FILAMENTS[p] == number)
        groups.append(f"{number} {colour} ({parts})")
    return ", ".join(groups)


def bambu_offset(parts: dict[str, trimesh.Trimesh]) -> tuple[float, float, float]:
    """Translation that puts the model at the back of the X2D bed, centred left to right.

    Bambu Studio re-centres a foreign 3MF, but takes a project of its own as it is. The back and
    not the middle: the prime tower stands in front (BAMBU_PRIME_TOWER_MM), and a 200 mm plate in
    the middle of the 256 mm bed leaves it no room — Bambu Studio reports a path conflict.
    """
    bounds = np.vstack([tm.bounds for tm in parts.values()])
    lo, hi = bounds.min(axis=0), bounds.max(axis=0)
    return (BAMBU_BED_MM / 2 - (lo[0] + hi[0]) / 2, BAMBU_BED_MM - BAMBU_BACK_MARGIN_MM - hi[1], -lo[2])


def _xml(root: ET.Element) -> bytes:
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def model_settings(object_id: str, name: str, part_ids: dict[str, str]) -> bytes:
    """Bambu's Metadata/model_settings.config: one object, each part on its filament ("extruder")."""
    config = ET.Element("config")
    obj = ET.SubElement(config, "object", id=object_id)
    ET.SubElement(obj, "metadata", key="name", value=name)
    ET.SubElement(obj, "metadata", key="extruder", value="1")
    for part, part_id in part_ids.items():
        element = ET.SubElement(obj, "part", id=part_id, subtype="normal_part")
        ET.SubElement(element, "metadata", key="name", value=part)
        ET.SubElement(element, "metadata", key="extruder", value=str(PART_FILAMENTS[part]))
    plate = ET.SubElement(config, "plate")
    for key, value in (("plater_id", "1"), ("plater_name", ""), ("locked", "false")):
        ET.SubElement(plate, "metadata", key=key, value=value)
    instance = ET.SubElement(plate, "model_instance")
    ET.SubElement(instance, "metadata", key="object_id", value=object_id)
    ET.SubElement(instance, "metadata", key="instance_id", value="0")
    return _xml(config)


def project_settings() -> bytes:
    """Bambu's Metadata/project_settings.config: the X2D, 0.20 mm Standard and five PLA spools.

    The template is a full Bambu Studio 02.08.02.61 project config: a partial one is filled with
    generic defaults instead of the X2D's, and every per-filament list must name the same number
    of filaments, or the slicer fails. It was cut out of an X2D project with nine filaments down
    to three "Bambu PLA Basic @BBL X2D 0.4 nozzle" entries, then each per-filament block extended
    to five slots (backend/scripts/regen_bambu_filaments.py).
    """
    settings = json.loads(PROJECT_SETTINGS.read_text(encoding="utf-8"))
    settings["filament_colour"] = list(FILAMENT_COLORS)
    settings["filament_multi_colour"] = list(FILAMENT_COLORS)
    settings["wipe_tower_x"] = [f"{BAMBU_PRIME_TOWER_MM[0]:g}"]
    settings["wipe_tower_y"] = [f"{BAMBU_PRIME_TOWER_MM[1]:g}"]
    return json.dumps(settings, indent=4, ensure_ascii=False).encode("utf-8")


def bambu_3mf(model_3mf: bytes, name: str) -> bytes:
    """Turn trimesh's 3MF of one named object into a Bambu Studio project with coloured parts.

    Bambu reads Metadata/*.config only from a file whose model names BambuStudio as its
    application; anything else it imports as bare geometry on filament 1.
    """
    source = zipfile.ZipFile(io.BytesIO(model_3mf))
    model = source.read("3D/3dmodel.model")
    (item_id,) = re.findall(rb'<item [^>]*?objectid="(\d+)"', model)
    names = {oid.decode(): saxutils.unescape(n.decode(), {"&quot;": '"'}) for oid, n in OBJECT_TAG.findall(model)}
    top = item_id.decode()
    part_ids = {names[oid]: oid for oid in names if oid != top}

    start = model.index(b"<model ")
    end = model.index(b">", start)
    model = (
        model[:end]
        + f' xmlns:BambuStudio="{BAMBU_NAMESPACE}">'.encode()
        + f'<metadata name="Application">{BAMBU_APPLICATION}</metadata>'.encode()
        + b'<metadata name="BambuStudio:3mfVersion">1</metadata>'
        + model[end + 1 :]
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in source.infolist():
            z.writestr(info, model if info.filename == "3D/3dmodel.model" else source.read(info))
        z.writestr("Metadata/model_settings.config", model_settings(top, names[top], part_ids))
        z.writestr("Metadata/project_settings.config", project_settings())
    return out.getvalue()


def write_stl_header(path: Path, name: str) -> None:
    """Put the ASCII slug of the name into the 80-byte header of a binary STL.

    The prefix keeps the header from ever starting with "solid", which would make readers take the
    binary file for an ASCII one.
    """
    header = f"{STL_HEADER_PREFIX}{slugify(name)}".encode("ascii")[:STL_HEADER_LEN]
    with path.open("r+b") as f:
        f.write(header.ljust(STL_HEADER_LEN, b"\0"))


def export_all(
    meshset: MeshSet, spec: FrameSpec, out_dir: Path, sources: str | None = None, name: str | None = None
) -> ExportPaths:
    """Write the model files. `name` is the place label the 3MF object and the STL header carry."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = ExportPaths(stl=out_dir / "model.stl", threemf=out_dir / "model.3mf", glb=out_dir / "preview.glb")

    # Verify everything first, so a failure never leaves a half-written set of files behind.
    # printable() on every mesh: a slicer welds the STL by position and may do the same with the
    # 3MF objects, and neither may then find an edge with more than two faces.
    single = to_trimesh(printable(meshset.single))
    verify_single(single, spec)
    parts = {part: to_trimesh(printable(man)) for part, man in meshset.parts().items()}
    for part, tm in parts.items():
        verify_part(tm, part)

    single.export(str(paths.stl), file_type="stl")
    if name is not None:
        write_stl_header(paths.stl, name)
    if spec.multicolor:
        label = name if name is not None else DEFAULT_OBJECT_NAME
        scene = threemf_scene(parts, label, offset=bambu_offset(parts))
        paths.threemf.write_bytes(bambu_3mf(scene.export(file_type="3mf"), label))
    else:
        threemf_scene(parts, name).export(str(paths.threemf), file_type="3mf")

    # Colours are for the preview only — the 3MF is already written at this point.
    for part, tm in parts.items():
        tm.visual.face_colors = PART_COLORS[part]
    trimesh.Scene(parts).export(str(paths.glb), file_type="glb")

    # Provenance travels with the model (spec §7). Written unconditionally: the ODbL notice is
    # mandatory for anyone who sells, passes on or publishes a print, and a caller that forgets the
    # argument must still get it.
    paths.sources = out_dir / SOURCES_FILENAME
    text = sources if sources is not None else sources_text(date.today().isoformat())
    paths.sources.write_text(text, encoding="utf-8")

    paths.diagnostics = mesh_diagnostics(single)
    part_diagnostics = [mesh_diagnostics(tm) for tm in parts.values()]
    paths.diagnostics["nonmanifold_edges_3mf"] = sum(d["nonmanifold_edges"] for d in part_diagnostics)
    paths.diagnostics["degenerate_faces_3mf"] = sum(d["degenerate_faces"] for d in part_diagnostics)
    return paths
