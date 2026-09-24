"""Estimates for untagged buildings: height (spec §5) and a default roof (phase 4a spec §2.4).

Deliberately free of fetch: prepare needs these estimates, and pulling them out of fetch would
drag httpx and osm2geojson into the geometry stage. The only package import is the RoofSpec
data class from features, which does not touch the network stack.
"""

from .features import RoofSpec

# Height by building type, then by footprint area. Buildings without any height tag are ~70 %
# of the data, and a flat 8 m for all of them is what flattened the MVP models.
TYPE_HEIGHT_M: dict[str, float] = {
    "cathedral": 35.0,
    "church": 18.0, "chapel": 18.0, "mosque": 18.0, "synagogue": 18.0, "temple": 18.0,
    "office": 20.0, "hotel": 20.0, "hospital": 20.0, "university": 20.0,
    "apartments": 15.0, "dormitory": 15.0, "civic": 15.0, "public": 15.0, "government": 15.0,
    "commercial": 10.0, "retail": 10.0, "school": 10.0, "industrial": 10.0,
    "warehouse": 10.0, "supermarket": 10.0,
    "house": 7.0, "detached": 7.0, "semidetached_house": 7.0, "terrace": 7.0,
    "residential": 7.0, "bungalow": 7.0,
    "garage": 3.0, "garages": 3.0, "shed": 3.0, "hut": 3.0, "carport": 3.0,
    "kiosk": 3.0, "service": 3.0,
}
AREA_HEIGHT_M: tuple[tuple[float, float], ...] = ((100.0, 5.0), (400.0, 8.0), (1500.0, 12.0))
AREA_HEIGHT_FALLBACK_M = 15.0


def estimate_height_m(kind: str, area_m2: float) -> float:
    """Height of a building without height tags, by type and then by footprint area (spec §5)."""
    by_type = TYPE_HEIGHT_M.get(kind)
    if by_type is not None:
        return by_type
    for limit, height in AREA_HEIGHT_M:
        if area_m2 < limit:
            return height
    return AREA_HEIGHT_FALLBACK_M


# Most OSM houses carry no roof:shape, so without a default the residential areas print flat.
# Like the height table this is an estimate: a gable for typical houses, and for "residential"
# only below a footprint where it is still a house rather than a block.
DEFAULT_GABLE_KINDS: frozenset[str] = frozenset({"house", "detached", "semidetached_house", "bungalow"})
DEFAULT_GABLE_RESIDENTIAL_MAX_M2 = 200.0


def default_roof(kind: str, area_m2: float) -> RoofSpec | None:
    """Roof for an OSM building without roof:shape, or None for flat (phase 4a spec §2.4).

    height_m stays 0.0 and direction_deg None: prepare derives both from the footprint.
    """
    if kind in DEFAULT_GABLE_KINDS:
        return RoofSpec(shape="gabled")
    if kind == "residential" and area_m2 < DEFAULT_GABLE_RESIDENTIAL_MAX_M2:
        return RoofSpec(shape="gabled")
    return None
