"""Command line entry point: skylineframe --lat 50.11 --lon 8.68 --mode full --out ./out"""

from pathlib import Path
from typing import Annotated

import typer

from .errors import SkylineError
from .pipeline import run
from .spec import FrameSpec, Mode

app = typer.Typer(add_completion=False)


@app.command()
def generate(
    lat: Annotated[float, typer.Option(help="Centre latitude (WGS84)")],
    lon: Annotated[float, typer.Option(help="Centre longitude (WGS84)")],
    side: Annotated[float, typer.Option(help="Edge length of the city square in metres")] = 1500,
    plate: Annotated[float, typer.Option(help="Plate edge length in mm")] = 100,
    thickness: Annotated[float, typer.Option(help="Plate thickness in mm")] = 3.0,
    mode: Annotated[Mode, typer.Option(help="simple = buildings only, full = roads and water too")] = Mode.simple,
    rotation: Annotated[float, typer.Option(help="Clockwise rotation of the square in degrees")] = 0,
    z: Annotated[float, typer.Option(help="Height exaggeration factor")] = 1.5,
    out: Annotated[Path, typer.Option(help="Output directory")] = Path("out"),
    cache: Annotated[Path, typer.Option(help="Overpass cache directory")] = Path(".cache/overpass"),
) -> None:
    spec = FrameSpec(
        center_lat=lat,
        center_lon=lon,
        side_m=side,
        plate_size_mm=plate,
        plate_thickness_mm=thickness,
        mode=mode,
        rotation_deg=rotation,
        z_exaggeration=z,
    )
    try:
        result = run(spec, out, cache, progress=lambda stage, msg: typer.echo(f"[{stage}] {msg}"))
    except SkylineError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Buildings: {result.stats.get('buildings', 0)}")
    typer.echo(f"STL: {result.paths.stl}")
    typer.echo(f"3MF: {result.paths.threemf}")


if __name__ == "__main__":
    app()
