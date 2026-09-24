"""Command line entry point: skylineframe --lat 50.11 --lon 8.68 --mode full --out ./out"""

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from .errors import SkylineError
from .pipeline import run
from .spec import PRESETS, FrameSpec, Mode, Preset

app = typer.Typer(add_completion=False)
DEFAULT_SIDE_M, DEFAULT_PLATE_MM = PRESETS[Preset.skyline]


def _message(exc: Exception) -> str:
    """One readable line per problem; pydantic's full repr is too noisy for a terminal."""
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
    return str(exc)


@app.command()
def generate(
    lat: Annotated[float, typer.Option(help="Centre latitude (WGS84)")],
    lon: Annotated[float, typer.Option(help="Centre longitude (WGS84)")],
    preset: Annotated[
        Preset | None,
        typer.Option(help="skyline = 1500 m on 100 mm, detail = 800/100, gross = 1500/200"),
    ] = None,
    side: Annotated[float | None, typer.Option(help="Edge length of the city square in metres (wins over --preset)")] = None,
    plate: Annotated[float | None, typer.Option(help="Plate edge length in mm (wins over --preset)")] = None,
    thickness: Annotated[float, typer.Option(help="Plate thickness in mm")] = 3.0,
    mode: Annotated[Mode, typer.Option(help="simple = buildings only, full = roads and water too")] = Mode.simple,
    rotation: Annotated[float, typer.Option(help="Clockwise rotation of the square in degrees")] = 0,
    z: Annotated[float, typer.Option(help="Height exaggeration factor")] = 1.5,
    roofs: Annotated[bool, typer.Option("--roofs/--no-roofs", help="Build roof bodies from roof:shape")] = True,
    parts: Annotated[bool, typer.Option("--parts/--no-parts", help="Render building:part instead of one box per outline")] = True,
    lod2: Annotated[bool, typer.Option("--lod2/--no-lod2", help="Use official LoD2 building models where available")] = True,
    terrain: Annotated[bool, typer.Option("--terrain/--no-terrain", help="Model the terrain relief (Copernicus DEM)")] = False,
    terrain_z: Annotated[float, typer.Option(help="Terrain exaggeration factor, separate from --z")] = 1.0,
    out: Annotated[Path, typer.Option(help="Output directory")] = Path("out"),
    cache: Annotated[Path, typer.Option(help="Overpass cache directory")] = Path(".cache/overpass"),
) -> None:
    preset_side, preset_plate = PRESETS.get(preset, (DEFAULT_SIDE_M, DEFAULT_PLATE_MM))
    # Spec construction is inside the try: out-of-range options must read as an error line,
    # not as a pydantic traceback. Pydantic's ValidationError is named next to SkylineError
    # because it is the one expected failure that is not part of our own hierarchy.
    try:
        spec = FrameSpec(
            center_lat=lat,
            center_lon=lon,
            side_m=side if side is not None else preset_side,
            plate_size_mm=plate if plate is not None else preset_plate,
            plate_thickness_mm=thickness,
            mode=mode,
            rotation_deg=rotation,
            z_exaggeration=z,
            roofs=roofs,
            parts=parts,
            lod2=lod2,
            terrain=terrain,
            terrain_exaggeration=terrain_z,
        )
        result = run(spec, out, cache, progress=lambda stage, msg: typer.echo(f"[{stage}] {msg}"))
    except (SkylineError, ValidationError) as exc:
        typer.echo(f"Error: {_message(exc)}", err=True)
        raise typer.Exit(code=1)
    stats = result.stats
    typer.echo(f"Buildings: {int(stats.get('buildings', 0))}")
    typer.echo(f"Blocks: {int(stats.get('blocks', 0))}")
    typer.echo(f"Parts: {int(stats.get('parts', 0))}")
    typer.echo(f"Roofs: {int(stats.get('roofs', 0))}")
    typer.echo(f"Roads: {int(stats.get('roads', 0))} ({stats.get('road_area_mm2', 0.0):.0f} mm²)")
    typer.echo(f"Footprint coverage: {stats.get('footprint_coverage', 0.0):.2%}")
    source = str(stats.get("lod2_source", "") or "")
    if source:
        typer.echo(f"LoD2 source: {source}")
        # Both numbers on one line: "buildings" is the count that really carries roof geometry,
        # and the rest fell back to an extruded footprint at the same height.
        typer.echo(
            f"LoD2 buildings: {int(stats.get('lod2_buildings', 0))} with a body, "
            f"{int(stats.get('lod2_rejected', 0))} fell back to a prism"
        )
        typer.echo(f"LoD2 triangles: {int(stats.get('lod2_triangles', 0))}")
    else:
        typer.echo("LoD2 source: none (OpenStreetMap only)")
    # Only when terrain was asked for: a flat run has nothing to say about it. A failed DEM fetch
    # is not an error (spec 4b §5.6) — the model is flat and the note says why.
    terrain_source = str(stats.get("terrain_source", "") or "")
    if terrain_source:
        typer.echo(f"Terrain: {terrain_source}, relief {float(stats.get('terrain_relief_mm', 0.0)):.2f} mm")
    elif stats.get("terrain_note"):
        typer.echo(f"Terrain: {stats['terrain_note']} (flat plate)")
    typer.echo(f"Non-manifold edges after vertex merge: {int(stats.get('nonmanifold_edges', 0))}")
    typer.echo(f"Degenerate faces after vertex merge: {int(stats.get('degenerate_faces', 0))}")
    typer.echo(f"STL: {result.paths.stl}")
    typer.echo(f"3MF: {result.paths.threemf}")


if __name__ == "__main__":
    app()
