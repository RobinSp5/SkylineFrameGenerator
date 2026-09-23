"""Wire the stages together: fetch -> project -> prepare -> scale -> mesh -> export."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .errors import PipelineError
from .export import ExportPaths, export_all
from .features import Features
from .fetch import fetch_features
from .lod2.sources import ATTRIBUTIONS, sources_text
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
    # Counts are ints, footprint_coverage is a ratio, lod2_source is a provider name (spec §8).
    stats: dict[str, float | str]


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
    # Not raw.lod2_source on its own: the provider answering is not the same as official geometry
    # reaching the model. The flag may be off (prepare then ignores the data), and every model may
    # clip away — the query box carries a 100 m margin, so a square over a park or a river bend
    # comes back full of models that all fall outside it, and a 2D response yields no footprint at
    # all. In both cases the result is byte-identical to the OpenStreetMap-only run, and naming
    # Hessen next to it would be a false attribution in SOURCES.txt and in the status line.
    lod2_source = raw.lod2_source if spec.lod2 and prepared.lod2_footprints else ""
    paths = export_all(
        meshes,
        spec,
        out_dir,
        sources=sources_text(date.today().isoformat(), ATTRIBUTIONS.get(lod2_source)),
    )

    individual = len(prepared.buildings)
    # Counted on the scaled prisms for the same reason as `roofs` below: prepare decides which
    # LoD2 models close into a body, and scale still drops one that stays below the printable
    # minimum or leaves the plate. Such a building prints as a prism, so it belongs with the
    # rejected ones — the two numbers together are every LoD2 model that reached the print.
    bodies_printed = sum(1 for p in scaled.buildings if p.solid_mm is not None)
    # Paired, not subtracted: the difference of the two totals only counts the dropped bodies
    # while scale_features hands back one prism per prepared building. strict=True says that
    # out loud, so a scale stage that ever filters fails here instead of quietly letting the
    # two LoD2 numbers stop partitioning — a subtraction could even go negative.
    bodies_dropped = sum(
        1
        for b, p in zip(prepared.buildings, scaled.buildings, strict=True)
        if b.solid_m is not None and p.solid_mm is None
    )
    stats: dict[str, float | str] = {
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
        # LoD2 (spec §5/§8). lod2_source is empty whenever no official model reached the model,
        # including a provider outage — the run itself never fails for it (spec §9).
        # Only bodies: a model whose body would not close prints as a prism, and the CLI and the
        # status line promise roof geometry. lod2_rejected carries the rest.
        "lod2_buildings": bodies_printed,
        "lod2_source": lod2_source,
        "lod2_rejected": prepared.lod2_rejected + bodies_dropped,
        "lod2_triangles": sum(p.solid_mm.num_tri() for p in scaled.buildings if p.solid_mm is not None),
        "stl_bytes": paths.stl.stat().st_size,
        "threemf_bytes": paths.threemf.stat().st_size,
        **paths.diagnostics,
    }
    return RunResult(paths=paths, stats=stats)
