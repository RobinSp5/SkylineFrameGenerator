"""Wire the stages together: fetch -> project -> prepare -> scale -> mesh -> export."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .errors import PipelineError
from .export import ExportPaths, export_all
from .features import Features
from .fetch import fetch_features
from .mesh import build_meshes
from .prepare import prepare
from .project import project_features
from .scale import scale_features
from .spec import FrameSpec

ProgressCallback = Callable[[str, str], None]
FetchFn = Callable[[FrameSpec, Path], Features]


@dataclass
class RunResult:
    paths: ExportPaths
    stats: dict[str, int]


def run(
    spec: FrameSpec,
    out_dir: Path,
    cache_dir: Path,
    progress: ProgressCallback | None = None,
    fetch: FetchFn = fetch_features,
) -> RunResult:
    def report(stage: str, message: str) -> None:
        if progress:
            progress(stage, message)

    report("fetch", "Loading OpenStreetMap data")
    raw = fetch(spec, cache_dir)

    report("prepare", "Clipping and cleaning geometry")
    prepared = prepare(project_features(raw, spec), spec)
    if not prepared.buildings:
        raise PipelineError("No buildings found in the selected area. Try a denser part of the city.")
    scaled = scale_features(prepared, spec)

    report("mesh", f"Building solids for {len(prepared.buildings)} buildings")
    meshes = build_meshes(scaled, spec)

    report("export", "Writing STL, 3MF and preview")
    paths = export_all(meshes, spec, out_dir)

    stats = {
        "buildings": len(prepared.buildings),
        "roads": len(prepared.roads),
        "water": len(prepared.water),
        "stl_bytes": paths.stl.stat().st_size,
        "threemf_bytes": paths.threemf.stat().st_size,
    }
    return RunResult(paths=paths, stats=stats)
