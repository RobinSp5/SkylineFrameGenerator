"""Plain containers for map features; geometry CRS depends on the pipeline stage."""

from dataclasses import dataclass, field

import manifold3d as m3d
from shapely.geometry.base import BaseGeometry

from .trees.model import OsmTree

# One ring of an LoD2 face: (x, y, z) triples, closing vertex already dropped. Tuples rather
# than lists because these sit in dataclass fields that carry a default.
Ring = tuple[tuple[float, float, float], ...]


@dataclass
class RoofSpec:
    shape: str  # gabled | hipped | half_hipped | pyramidal | skillion | mansard | gambrel | dome | round
    height_m: float = 0.0  # 0.0 = untagged; prepare derives it from the footprint (spec §7)
    direction_deg: float | None = None  # roof:direction as a compass bearing, None = use the long axis


@dataclass
class Lod2Building:
    """One official LoD2 model, still a surface model (spec §4).

    surfaces are in the CRS of the stage: WGS84 lon/lat plus z in metres above sea level after
    fetch, local metres after project_features. prepare turns them into a footprint and — for the
    buildings that are printed on their own — into a watertight body.
    """

    osm_id: str  # f"lod2/{provider}/{localId}"
    surfaces: tuple[Ring, ...] = ()
    name: str | None = None


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
    # "way/123" / "relation/456" for OSM, "lod2/hessen/Building_X" for LoD2: way and relation ids
    # are separate number spaces and do collide, so the source is part of the id (spec §3).
    osm_id: str = ""
    is_part: bool = False
    outline_id: str | None = None  # osm_id of the outline this footprint belongs to
    kind: str = ""  # value of the building / building:part tag, input of estimate_height_m

    # Filled by prepare, in local metres.
    eaves_m: float = 0.0
    ridge_m: float = 0.0
    rect: tuple[tuple[float, float], ...] = ()  # 4 corners of the minimum rotated rectangle, or ()

    # --- LoD2 (spec §5/§6) ---
    lod2: bool = False  # footprint and height come from an official LoD2 model, not from OSM tags
    # The faces this footprint was derived from, in local metres. Only the copy that is printed
    # individually keeps them; prepare turns exactly those into solid_m and leaves the rest empty,
    # because a body costs two orders of magnitude more than the footprint work (spec §5).
    surfaces: tuple[Ring, ...] = ()
    solid_m: m3d.Manifold | None = None  # watertight body in local metres, z = 0 at the ground
    # roof:shape was tagged at all, flat and unsupported shapes included. `roof` is None for both
    # "tagged flat" and "untagged", and only the untagged house may get a default roof (spec 4a §2.4).
    roof_tagged: bool = False


@dataclass
class Block:
    """A welded group of footprints, extruded as one sockel under its houses (spec §6.4).

    No height: a sockel is SOCKEL_MM tall in print space whatever stands on it (spec 4a §2.3).
    """

    geom: BaseGeometry  # Polygon


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
    # Raw LoD2 models as they came off the provider; prepare makes buildings out of them.
    lod2: list[Lod2Building] = field(default_factory=list)
    lod2_source: str = ""  # provider name, "" when no LoD2 data is in play (spec §8)
    # natural=tree nodes and the sampled points of natural=tree_row ways (spec 6 §4.3).
    trees: list[OsmTree] = field(default_factory=list)
