"""Plain containers for OSM features; geometry CRS depends on the pipeline stage."""

from dataclasses import dataclass, field

from shapely.geometry.base import BaseGeometry


@dataclass
class RoofSpec:
    shape: str  # gabled | hipped | half_hipped | pyramidal | skillion | mansard | gambrel | dome | round
    height_m: float = 0.0  # 0.0 = untagged; prepare derives it from the footprint (spec §7)
    direction_deg: float | None = None  # roof:direction as a compass bearing, None = use the long axis


@dataclass
class Building:
    geom: BaseGeometry  # Polygon or MultiPolygon

    # height_m carries two different meanings, and height_is_top says which one (spec §4):
    # with a `height` tag it is the top of the roof, otherwise it is the eaves height and the
    # roof sits on top of it. prepare resolves both into eaves_m / ridge_m.
    height_m: float
    height_is_top: bool = False

    min_height_m: float = 0.0
    roof: RoofSpec | None = None  # None = flat
    # "way/123" / "relation/456": way and relation ids are separate number spaces and do
    # collide, so the element type is part of the id (spec §3).
    osm_id: str = ""
    is_part: bool = False
    outline_id: str | None = None  # osm_id of the outline this footprint belongs to
    kind: str = ""  # value of the building / building:part tag, input of estimate_height_m

    # Filled by prepare, in local metres.
    eaves_m: float = 0.0
    ridge_m: float = 0.0
    rect: tuple[tuple[float, float], ...] = ()  # 4 corners of the minimum rotated rectangle, or ()


@dataclass
class Block:
    """A welded group of footprints, extruded as one solid (spec §6.4)."""

    geom: BaseGeometry  # Polygon
    height_m: float


@dataclass
class Road:
    geom: BaseGeometry  # LineString or MultiLineString
    cls: str  # one of spec.ROAD_CLASSES


@dataclass
class Water:
    geom: BaseGeometry  # Polygon or MultiPolygon


@dataclass
class Features:
    buildings: list[Building] = field(default_factory=list)  # parts included, is_part=True
    roads: list[Road] = field(default_factory=list)
    water: list[Water] = field(default_factory=list)
