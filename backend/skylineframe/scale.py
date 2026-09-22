"""Convert prepared features from local metres to print millimetres."""

from dataclasses import dataclass, field

import shapely.affinity
from shapely.geometry import Polygon

from .features import Building
from .prepare import Prepared
from .roofs import MIN_ROOF_MM
from .spec import FrameSpec


@dataclass
class ScaledRoof:
    rect_mm: tuple[tuple[float, float], ...]  # four corners of the minimum rotated rectangle
    shape: str
    z_eaves_mm: float
    z_ridge_mm: float
    direction_deg: float | None = None


@dataclass
class Prism:
    geom: Polygon  # footprint in mm, centred on the plate
    height_mm: float  # top of the vertical body (the eaves, when there is a roof)
    z0_mm: float = 0.0  # bottom of the body; > 0 only for a part that stands on something
    roof: ScaledRoof | None = None


@dataclass
class Scaled:
    buildings: list[Prism]
    blocks: list[Prism] = field(default_factory=list)
    roads: list[Polygon] = field(default_factory=list)
    water: list[Polygon] = field(default_factory=list)


def building_height_mm(height_m: float, spec: FrameSpec) -> float:
    """Print height of one building, never below the printable minimum and never above the plate.

    The upper cap is the last line of defence against a mistagged height: a tower taller than
    the plate is wide turns the model into an unprintable spike.
    """
    raw = round(height_m * spec.scale * spec.z_exaggeration, 2)
    return min(max(raw, spec.min_building_height_mm), spec.plate_size_mm)


def raw_height_mm(height_m: float, spec: FrameSpec) -> float:
    """Same scaling without the printable minimum: for bottoms and ridges.

    A min_height run through building_height_mm would lift every ground-level footprint by
    min_building_height_mm and tear the model off the plate.
    """
    return min(round(height_m * spec.scale * spec.z_exaggeration, 2), spec.plate_size_mm)


def _scale_geom(geom: Polygon, factor: float) -> Polygon:
    return shapely.affinity.scale(geom, xfact=factor, yfact=factor, origin=(0, 0))


def _by_outline(buildings: list[Building]) -> dict[str, list[Building]]:
    groups: dict[str, list[Building]] = {}
    for b in buildings:
        if b.outline_id is not None:
            groups.setdefault(b.outline_id, []).append(b)
    return groups


def _is_supported(b: Building, siblings: list[Building]) -> bool:
    """True when another footprint of the same outline reaches up to the bottom of `b`.

    Parts start in the air by design (a setback tower stands on its base). A part with nothing
    below it is a tagging artefact, and printing it floating is impossible, so it is extended
    down to the plate instead (spec §8).
    """
    for other in siblings:
        if other is b:
            continue
        if other.min_height_m < b.min_height_m and other.eaves_m >= b.min_height_m:
            if other.geom.intersection(b.geom).area > 0:
                return True
    return False


def _roof_of(b: Building, eaves_mm: float, spec: FrameSpec) -> ScaledRoof | None:
    if b.roof is None or len(b.rect) != 4:
        return None
    ridge_mm = max(raw_height_mm(b.ridge_m, spec), eaves_mm)
    if ridge_mm - eaves_mm < MIN_ROOF_MM:
        return None
    s = spec.scale
    return ScaledRoof(
        rect_mm=tuple((x * s, y * s) for x, y in b.rect),
        shape=b.roof.shape,
        z_eaves_mm=eaves_mm,
        z_ridge_mm=ridge_mm,
        direction_deg=b.roof.direction_deg,
    )


def _building_prism(b: Building, groups: dict[str, list[Building]], spec: FrameSpec) -> Prism:
    eaves_mm = building_height_mm(b.eaves_m, spec)
    z0_mm = 0.0
    if b.min_height_m > 0 and b.outline_id is not None and _is_supported(b, groups.get(b.outline_id, [])):
        z0_mm = raw_height_mm(b.min_height_m, spec)
        if z0_mm >= eaves_mm:  # mistagged: the part would have no body at all
            z0_mm = 0.0
    return Prism(
        geom=_scale_geom(b.geom, spec.scale),
        height_mm=eaves_mm,
        z0_mm=z0_mm,
        roof=_roof_of(b, eaves_mm, spec),
    )


def scale_features(prepared: Prepared, spec: FrameSpec) -> Scaled:
    s = spec.scale
    groups = _by_outline(prepared.buildings)
    return Scaled(
        buildings=[_building_prism(b, groups, spec) for b in prepared.buildings],
        blocks=[Prism(_scale_geom(bl.geom, s), building_height_mm(bl.height_m, spec)) for bl in prepared.blocks],
        roads=[_scale_geom(p, s) for p in prepared.roads],
        water=[_scale_geom(p, s) for p in prepared.water],
    )
