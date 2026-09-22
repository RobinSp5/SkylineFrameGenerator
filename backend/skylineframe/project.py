"""WGS84 -> local metric frame centred on the spec centre, rotated so the target square is axis-aligned."""

from dataclasses import replace

import shapely.affinity
import shapely.ops
from pyproj import CRS, Transformer
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry

from .errors import AreaError
from .features import Features, Road, Water
from .spec import FrameSpec


def local_transformer(spec: FrameSpec) -> Transformer:
    crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={spec.center_lat} +lon_0={spec.center_lon} +datum=WGS84 +units=m +no_defs"
    )
    return Transformer.from_crs("EPSG:4326", crs, always_xy=True)


def to_local(geom: BaseGeometry, spec: FrameSpec, transformer: Transformer | None = None) -> BaseGeometry:
    tr = transformer or local_transformer(spec)
    projected = shapely.ops.transform(tr.transform, geom)
    # Square is rotated clockwise by rotation_deg on the map; rotate the world counter-clockwise to align it.
    return shapely.affinity.rotate(projected, spec.rotation_deg, origin=(0, 0))


def square_local(spec: FrameSpec) -> Polygon:
    h = spec.side_m / 2
    return box(-h, -h, h, h)


def _rotated_square_metric(spec: FrameSpec) -> Polygon:
    return shapely.affinity.rotate(square_local(spec), -spec.rotation_deg, origin=(0, 0))


def _to_wgs84(geom: BaseGeometry, spec: FrameSpec) -> BaseGeometry:
    tr = local_transformer(spec)
    return shapely.ops.transform(lambda x, y: tr.transform(x, y, direction="INVERSE"), geom)


def square_wgs84(spec: FrameSpec) -> Polygon:
    """The (rotated) target square as a WGS84 polygon, e.g. for map display."""
    return _to_wgs84(_rotated_square_metric(spec), spec)


def query_bbox(spec: FrameSpec, margin_m: float = 100.0) -> tuple[float, float, float, float]:
    """(south, west, north, east) in WGS84 enclosing the rotated square plus a margin.

    Areas crossing the antimeridian are rejected rather than wrapped.
    """
    grown = _rotated_square_metric(spec).buffer(margin_m, join_style="mitre")
    minx, miny, maxx, maxy = _to_wgs84(grown, spec).bounds
    if maxx - minx > 180:
        raise AreaError("Areas crossing the antimeridian (±180° longitude) are not supported.")
    return (miny, minx, maxy, maxx)


def project_features(features: Features, spec: FrameSpec) -> Features:
    tr = local_transformer(spec)
    # replace() instead of a positional rebuild: Building carries a dozen fields now, and a
    # forgotten one would silently drop roofs or part heights on the way to prepare.
    return Features(
        buildings=[replace(b, geom=to_local(b.geom, spec, tr)) for b in features.buildings],
        roads=[Road(to_local(r.geom, spec, tr), r.cls) for r in features.roads],
        water=[Water(to_local(w.geom, spec, tr)) for w in features.water],
    )
