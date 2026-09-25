"""Overture query rows -> OvertureBuilding."""

import shapely.wkb

from ..features import OvertureBuilding, RoofSpec
from ..roofs import SHAPES


def _roof(row: dict) -> RoofSpec | None:
    """The roof of one row, or None for flat / unsupported / untagged shapes.

    Overture's roof_shape vocabulary overlaps ours by name for every shape we can build (gabled,
    hipped, half_hipped, pyramidal, skillion, mansard, gambrel, dome, round), so a value is either
    one of those verbatim or it is something this project has no geometry for — "flat" and
    Overture-only shapes like "saltbox" included — and both count as untagged, exactly like an
    OSM building without a roof:shape tag (fetch.py's parse_roof).
    """
    shape = row.get("roof_shape")
    if shape not in SHAPES:
        return None
    return RoofSpec(shape=shape, height_m=row.get("roof_height") or 0.0, direction_deg=row.get("roof_direction"))


def building_of(row: dict, release: str) -> OvertureBuilding:
    return OvertureBuilding(
        osm_id=f"overture/{release}/{row['id']}",
        geom=shapely.wkb.loads(bytes.fromhex(row["geom_wkb_hex"])),
        height_m=row.get("height"),
        roof=_roof(row),
    )


def parse_buildings(rows: list[dict], release: str) -> list[OvertureBuilding]:
    return [building_of(row, release) for row in rows]
