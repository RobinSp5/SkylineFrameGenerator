"""Write STL (single colour), 3MF (named parts) and GLB (coloured preview) after verifying the solid."""

from dataclasses import dataclass
from pathlib import Path

import trimesh

from .errors import ExportError
from .mesh import MeshSet, to_trimesh
from .spec import FrameSpec

PART_COLORS: dict[str, tuple[int, int, int, int]] = {
    "base": (200, 200, 200, 255),
    "buildings": (255, 255, 255, 255),
    "water": (70, 130, 220, 255),
    "roads": (90, 90, 90, 255),
}
SIZE_TOLERANCE_MM = 0.01


@dataclass
class ExportPaths:
    stl: Path
    threemf: Path
    glb: Path


def verify_single(tm: trimesh.Trimesh, spec: FrameSpec) -> None:
    if not tm.is_watertight or not tm.is_volume:
        raise ExportError("Resulting mesh is not watertight; please try a slightly different area.")
    size = spec.plate_size_mm
    if abs(tm.extents[0] - size) > SIZE_TOLERANCE_MM or abs(tm.extents[1] - size) > SIZE_TOLERANCE_MM:
        raise ExportError(f"Model footprint {tm.extents[0]:.2f}x{tm.extents[1]:.2f} mm does not match plate size {size} mm.")
    if tm.volume <= size * size * spec.plate_thickness_mm * 0.5:
        raise ExportError("Model has no volume above the plate.")


def verify_part(tm: trimesh.Trimesh, name: str) -> None:
    if not tm.is_watertight or not tm.is_volume:
        raise ExportError(f"Part '{name}' is not watertight; please try a slightly different area.")


def export_all(meshset: MeshSet, spec: FrameSpec, out_dir: Path) -> ExportPaths:
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
    return paths
