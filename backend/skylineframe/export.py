"""Write STL (single colour), 3MF (named parts) and GLB (coloured preview) after verifying the solid."""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import trimesh

from .errors import ExportError
from .lod2.sources import SOURCES_FILENAME, sources_text
from .mesh import MeshSet, printable, to_trimesh
from .naming import slugify
from .spec import FrameSpec

PART_COLORS: dict[str, tuple[int, int, int, int]] = {
    "base": (200, 200, 200, 255),
    "buildings": (255, 255, 255, 255),
    "water": (70, 130, 220, 255),
    "roads": (90, 90, 90, 255),
}
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


def threemf_scene(parts: dict[str, trimesh.Trimesh], name: str | None) -> trimesh.Scene:
    """The parts as a 3MF scene; with a name, grouped under one object that carries it.

    Trimesh writes a node with children as a 3MF component object and puts only the nodes on the
    base frame into the build, so the slicer lists a single object named after the place with the
    parts inside it. Without a name the parts stay separate top-level objects, as before.
    """
    if name is None:
        return trimesh.Scene(parts)
    scene = trimesh.Scene()
    scene.graph.update(frame_from=scene.graph.base_frame, frame_to=name)
    for part, tm in parts.items():
        scene.add_geometry(tm, geom_name=part, node_name=part, parent_node_name=name)
    return scene


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
