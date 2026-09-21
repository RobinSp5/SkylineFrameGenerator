"""Convert prepared features from local metres to print millimetres."""

from dataclasses import dataclass, field

import shapely.affinity
from shapely.geometry import Polygon

from .prepare import Prepared
from .spec import FrameSpec


@dataclass
class Prism:
    geom: Polygon  # footprint in mm, centred on the plate
    height_mm: float


@dataclass
class Scaled:
    buildings: list[Prism]
    roads: list[Polygon] = field(default_factory=list)
    water: list[Polygon] = field(default_factory=list)


def building_height_mm(height_m: float, spec: FrameSpec) -> float:
    raw = round(height_m * spec.scale * spec.z_exaggeration, 2)
    return max(raw, spec.min_building_height_mm)


def _scale_geom(geom: Polygon, factor: float) -> Polygon:
    return shapely.affinity.scale(geom, xfact=factor, yfact=factor, origin=(0, 0))


def scale_features(prepared: Prepared, spec: FrameSpec) -> Scaled:
    s = spec.scale
    return Scaled(
        buildings=[Prism(_scale_geom(b.geom, s), building_height_mm(b.height_m, spec)) for b in prepared.buildings],
        roads=[_scale_geom(p, s) for p in prepared.roads],
        water=[_scale_geom(p, s) for p in prepared.water],
    )
