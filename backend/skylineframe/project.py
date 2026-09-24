"""WGS84 -> local metric frame centred on the spec centre, rotated so the target square is axis-aligned."""

import math
from dataclasses import replace

import shapely.affinity
import shapely.ops
from pyproj import CRS, Transformer
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry

from .errors import AreaError
from .features import Features, Lod2Building, Road, Water
from .spec import FrameSpec
from .trees.model import OsmTree


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


def project_lod2(building: Lod2Building, tr: Transformer, rotation_deg: float) -> Lod2Building:
    """The same WGS84 -> local metres mapping to_local applies, for raw LoD2 rings.

    One transformer call per building instead of one per vertex: a 1500 m Frankfurt square has
    about 600 000 vertices, and a Python-level call each would dominate the whole run. The
    rotation is applied by hand for the same reason — shapely would need a geometry per ring.
    """
    rad = math.radians(rotation_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    flat = [point for ring in building.surfaces for point in ring]
    if not flat:
        return replace(building, surfaces=())
    xs, ys = tr.transform([p[0] for p in flat], [p[1] for p in flat])
    rings: list[tuple[tuple[float, float, float], ...]] = []
    start = 0
    for ring in building.surfaces:
        stop = start + len(ring)
        rings.append(
            tuple(
                (x * cos_a - y * sin_a, x * sin_a + y * cos_a, point[2])
                for x, y, point in zip(xs[start:stop], ys[start:stop], ring)
            )
        )
        start = stop
    return replace(building, surfaces=tuple(rings))


def project_trees(trees: list[OsmTree], tr: Transformer, rotation_deg: float) -> list[OsmTree]:
    """The to_local mapping for OSM tree points, in one transformer call (a New York square has
    tens of thousands of mapped street trees)."""
    if not trees:
        return []
    rad = math.radians(rotation_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    xs, ys = tr.transform([t.x for t in trees], [t.y for t in trees])
    return [
        replace(t, x=x * cos_a - y * sin_a, y=x * sin_a + y * cos_a)
        for t, x, y in zip(trees, xs, ys)
    ]


def project_features(features: Features, spec: FrameSpec) -> Features:
    tr = local_transformer(spec)
    # replace() instead of a positional rebuild: Building carries a dozen fields now, and a
    # forgotten one would silently drop roofs or part heights on the way to prepare.
    return Features(
        buildings=[replace(b, geom=to_local(b.geom, spec, tr)) for b in features.buildings],
        roads=[Road(to_local(r.geom, spec, tr), r.cls) for r in features.roads],
        water=[Water(to_local(w.geom, spec, tr)) for w in features.water],
        lod2=[project_lod2(b, tr, spec.rotation_deg) for b in features.lod2],
        lod2_source=features.lod2_source,
        trees=project_trees(features.trees, tr, spec.rotation_deg),
    )
