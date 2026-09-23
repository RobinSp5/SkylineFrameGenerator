"""Write STL (single colour), 3MF (named parts) and GLB (coloured preview) after verifying the solid."""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import trimesh

from .errors import ExportError
from .lod2.sources import SOURCES_FILENAME, sources_text
from .mesh import MeshSet, to_trimesh
from .spec import FrameSpec

PART_COLORS: dict[str, tuple[int, int, int, int]] = {
    "base": (200, 200, 200, 255),
    "buildings": (255, 255, 255, 255),
    "water": (70, 130, 220, 255),
    "roads": (90, 90, 90, 255),
}
SIZE_TOLERANCE_MM = 0.01
DEGENERATE_AREA_MM2 = 1e-9


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
    """Slicer-style health check: merge coincident vertices first, then count broken topology.

    Verification runs on the manifold topology, where buildings that merely touch keep their own
    vertices. A slicer merges those first, and only then can it see whether an edge is shared by
    exactly two faces. These numbers are reported, not enforced: touching buildings legitimately
    share edges after a merge.
    """
    merged = tm.copy()
    merged.merge_vertices()
    _, counts = np.unique(merged.edges_sorted, axis=0, return_counts=True)
    return {
        "nonmanifold_edges": int(np.count_nonzero(counts != 2)),
        "degenerate_faces": int(np.count_nonzero(merged.area_faces < DEGENERATE_AREA_MM2)),
    }


def verify_part(tm: trimesh.Trimesh, name: str) -> None:
    if not tm.is_watertight or not tm.is_volume:
        raise ExportError(f"Part '{name}' is not watertight; please try a slightly different area.")


def export_all(meshset: MeshSet, spec: FrameSpec, out_dir: Path, sources: str | None = None) -> ExportPaths:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = ExportPaths(stl=out_dir / "model.stl", threemf=out_dir / "model.3mf", glb=out_dir / "preview.glb")

    # Verify everything first, so a failure never leaves a half-written set of files behind.
    single = to_trimesh(meshset.single)
    verify_single(single, spec)
    parts = {name: to_trimesh(man) for name, man in meshset.parts().items()}
    for name, tm in parts.items():
        verify_part(tm, name)

    single.export(str(paths.stl), file_type="stl")
    trimesh.Scene(parts).export(str(paths.threemf), file_type="3mf")

    # Colours are for the preview only — the 3MF is already written at this point.
    for name, tm in parts.items():
        tm.visual.face_colors = PART_COLORS[name]
    trimesh.Scene(parts).export(str(paths.glb), file_type="glb")

    # Provenance travels with the model (spec §7). Written unconditionally: the ODbL notice is
    # mandatory for anyone who sells, passes on or publishes a print, and a caller that forgets the
    # argument must still get it.
    paths.sources = out_dir / SOURCES_FILENAME
    text = sources if sources is not None else sources_text(date.today().isoformat())
    paths.sources.write_text(text, encoding="utf-8")

    paths.diagnostics = mesh_diagnostics(single)
    return paths
