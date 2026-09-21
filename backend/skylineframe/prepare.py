"""Clip features to the target square, repair geometry and derive road/water areas (local metres)."""

from dataclasses import dataclass, field

from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import make_valid

from .features import Building, Features, Road, Water
from .project import square_local
from .spec import MIN_FEATURE_MM, FrameSpec, Mode

SIMPLIFY_TOLERANCE_MM = 0.05


@dataclass
class Prepared:
    buildings: list[Building]  # each geom is a valid Polygon in local metres
    roads: list[Polygon] = field(default_factory=list)
    water: list[Polygon] = field(default_factory=list)


def polygons_of(geom: BaseGeometry | None) -> list[Polygon]:
    """Repair and flatten any geometry into a list of non-empty, valid Polygons."""
    if geom is None or geom.is_empty:
        return []
    geom = make_valid(geom)
    if isinstance(geom, Polygon):
        parts = [geom]
    elif isinstance(geom, (MultiPolygon, GeometryCollection)):
        parts = []
        for g in geom.geoms:
            if isinstance(g, Polygon):
                parts.append(g)
            elif isinstance(g, MultiPolygon):
                parts.extend(g.geoms)
    else:
        return []
    return [p for p in parts if not p.is_empty and p.area > 0]


def _is_printable(poly: Polygon, min_area_m2: float, half_feature_m: float) -> bool:
    """Large enough, and thick enough somewhere: eroding by half the minimum feature must leave something."""
    return poly.area >= min_area_m2 and not poly.buffer(-half_feature_m).is_empty


def _clip_buildings(
    buildings: list[Building], square: Polygon, min_area_m2: float, tol_m: float, half_feature_m: float
) -> list[Building]:
    out: list[Building] = []
    for b in buildings:
        for poly in polygons_of(b.geom.intersection(square)):
            poly = poly.simplify(tol_m, preserve_topology=True)
            if _is_printable(poly, min_area_m2, half_feature_m):
                out.append(Building(poly, b.height_m))
    return out


def _road_areas(
    roads: list[Road], spec: FrameSpec, square: Polygon, blocked: BaseGeometry, min_area_m2: float
) -> list[Polygon]:
    if not roads:
        return []
    buffered = [
        r.geom.buffer(spec.road_width_mm[r.cls] / spec.scale / 2, cap_style="flat", join_style="round")
        for r in roads
    ]
    area = unary_union(buffered).intersection(square).difference(blocked)
    return [p for p in polygons_of(area) if p.area >= min_area_m2]


def _water_areas(
    water: list[Water], square: Polygon, blocked: BaseGeometry, min_area_m2: float
) -> list[Polygon]:
    if not water:
        return []
    area = unary_union([w.geom for w in water]).intersection(square).difference(blocked)
    return [p for p in polygons_of(area) if p.area >= min_area_m2]


def prepare(features: Features, spec: FrameSpec) -> Prepared:
    square = square_local(spec)
    scale = spec.scale
    buildings = _clip_buildings(
        features.buildings,
        square,
        min_area_m2=spec.min_footprint_area_mm2 / scale**2,
        tol_m=SIMPLIFY_TOLERANCE_MM / scale,
        half_feature_m=MIN_FEATURE_MM / 2 / scale,
    )
    if spec.mode != Mode.full:
        return Prepared(buildings=buildings)

    min_area_m2 = MIN_FEATURE_MM**2 / scale**2
    building_union = unary_union([b.geom for b in buildings]) if buildings else Polygon()
    roads = _road_areas(features.roads, spec, square, building_union, min_area_m2)
    blocked_for_water = unary_union([building_union, *roads])
    water = _water_areas(features.water, square, blocked_for_water, min_area_m2)
    return Prepared(buildings=buildings, roads=roads, water=water)
