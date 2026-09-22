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
    stats: dict[str, float]  # counts are ints, footprint_coverage is a ratio


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
    # A square of nothing but small sheds has no individual buildings but still has blocks,
    # and a block alone is a perfectly good model.
    if not prepared.buildings and not prepared.blocks:
        raise PipelineError("No buildings found in the selected area. Try a denser part of the city.")
    scaled = scale_features(prepared, spec)

    report("mesh", f"Building solids for {len(prepared.buildings)} buildings in {len(prepared.blocks)} blocks")
    meshes = build_meshes(scaled, spec)

    report("export", "Writing STL, 3MF and preview")
    paths = export_all(meshes, spec, out_dir)

    individual = len(prepared.buildings)
    stats: dict[str, float] = {
        # `buildings` stays the headline number the API, the frontend and the Playwright test
        # already read; buildings_individual is the same count under the spec's name.
        "buildings": individual,
        "buildings_individual": individual,
        "blocks": len(prepared.blocks),
        "parts": sum(1 for b in prepared.buildings if b.is_part),
        # Counted on the scaled prisms, not on the prepared footprints: prepare only decides
        # which footprints are eligible for a roof, and scale drops every roof whose ridge
        # stays below MIN_ROOF_MM. The number reported is the number of roofs actually built.
        "roofs": sum(1 for p in scaled.buildings if p.roof is not None),
        "footprint_coverage": round(prepared.footprint_coverage, 4),
        "roads": len(prepared.roads),
        # Groove area on the plate: the number that shows whether the blocks left the streets
        # intact (spec §6.4). prepared.roads is in metres, so it is scaled here.
        "road_area_mm2": round(sum(p.area for p in prepared.roads) * spec.scale**2, 1),
        "water": len(prepared.water),
        "stl_bytes": paths.stl.stat().st_size,
        "threemf_bytes": paths.threemf.stat().st_size,
        **paths.diagnostics,
    }
    return RunResult(paths=paths, stats=stats)
