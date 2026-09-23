"""GML 3.2 and CityGML surface geometry -> building face rings (spec §4).

Pure parsing: no network, no shapely, no projection. Two rules carry the whole module:

* Rings come only from <gml:Polygon> -> <gml:exterior> -> <gml:posList>. A scan for posList alone
  also collects the <gml:LineString> curve members of the terrain intersection (212 of them in the
  recorded three-building response against 360 faces), and those are not faces at all.
* The parse streams. A 1500 m Frankfurt square is a 128 MB document with 97 614 polygons, so
  iterparse yields one building at a time and the root is cleared after each one; the resident set
  stays flat no matter how large the document is.
"""

import xml.etree.ElementTree as ET
from collections.abc import Iterator

from ..features import Lod2Building, Ring

BUILDING_TAG = "Building"
MIN_RING_POINTS = 3  # fewer distinct points is a line or a point, not a face


def local_name(tag: object) -> str:
    """The tag without its namespace; "" for comments and processing instructions.

    ET gives those a callable tag, and the recorded Hessen response opens with a comment.
    """
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def poslist_triples(text: str) -> list[tuple[float, float, float]]:
    values = [float(v) for v in text.split()]
    return [(values[i], values[i + 1], values[i + 2]) for i in range(0, len(values) - 2, 3)]


def _srs_dimension(*elements: ET.Element) -> int | None:
    """The first srsDimension declared on `elements`, or None when none of them declares one.

    The attribute sits on the posList or on the ring around it, depending on the writer.
    """
    for element in elements:
        value = (element.get("srsDimension") or "").strip()
        if value.isdigit():
            return int(value)
    return None


def exterior_rings(feature: ET.Element, lat_first: bool = True) -> tuple[Ring, ...]:
    """Every exterior ring under `feature`, closing vertex dropped.

    lat_first=True is the Hessen WFS in EPSG::7423, whose posList is `lat lon z`; the rings come
    back as (lon, lat, z). lat_first=False keeps the file order, which is what a projected
    CityGML tile needs.
    """
    rings: list[Ring] = []
    for polygon in feature.iter():
        if local_name(polygon.tag) != "Polygon":
            continue
        for child in polygon:
            # Direct children only: an interior ring is a hole in the face, not a face, and it
            # would punch a hole through the roof body of the building it belongs to.
            if local_name(child.tag) != "exterior":
                continue
            for linear_ring in child.iter():
                # Only a LinearRing carries a face. GML 3.2 also allows an exterior to hold a
                # <gml:Ring> of <gml:curveMember><gml:LineString>, and reading the posList of
                # that would hand a curve to the solidifier as if it were a face.
                if local_name(linear_ring.tag) != "LinearRing":
                    continue
                for node in linear_ring.iter():
                    if local_name(node.tag) != "posList" or not node.text:
                        continue
                    # A missing srsDimension means 3D: the Hessen service omits the attribute on
                    # every one of its posLists. A declared 2D ring carries no height and a token
                    # count that is not a multiple of three is not a triple list at all; either
                    # one would be regrouped into nonsense points, so the ring is dropped.
                    dimension = _srs_dimension(node, linear_ring)
                    if dimension is not None and dimension != 3:
                        continue
                    if len(node.text.split()) % 3:
                        continue
                    points = poslist_triples(node.text)
                    if len(points) > 1 and points[0] == points[-1]:
                        points = points[:-1]
                    if lat_first:
                        points = [(lon, lat, z) for lat, lon, z in points]
                    # Distinct, not rounded: these are still degrees here, and rounding degrees
                    # to millimetres would collapse every ring of a building into one point. The
                    # metric weld happens in solidify, after the projection.
                    if len(set(points)) >= MIN_RING_POINTS:
                        rings.append(tuple(points))
    return tuple(rings)


def _gml_id(feature: ET.Element) -> str:
    for key, value in feature.attrib.items():
        if local_name(key) == "id":
            return value
    return ""


def building_of(
    feature: ET.Element, provider: str, lat_first: bool = True, index: int = 0
) -> Lod2Building:
    """One Lod2Building from a <Building> element, parts included.

    The faces of a building live in its <BuildingPart> children as often as in the building
    itself (17 of the 18 geometries in the recorded response), so every Polygon below the element
    counts — the parts are the building, not neighbours of it.

    `index` is the position of the feature in its document and is only ever used when the feature
    carries neither a localId nor a gml:id: without it every such building would answer to the
    same osm_id and they would collide wherever buildings are keyed by id.
    """
    name: str | None = None
    local_id = ""
    for node in feature.iter():
        tag = local_name(node.tag)
        if tag == "name" and name is None:
            name = (node.text or "").strip() or None
        elif tag == "localId" and not local_id:
            local_id = (node.text or "").strip()
    return Lod2Building(
        osm_id=f"lod2/{provider}/{local_id or _gml_id(feature) or f'#{index}'}",
        surfaces=exterior_rings(feature, lat_first),
        name=name,
    )


def iter_buildings(
    source,
    provider: str,
    *,
    lat_first: bool = True,
    feature_tag: str = BUILDING_TAG,
) -> Iterator[Lod2Building]:
    """Stream `source` (path, or any binary stream) and yield one building at a time."""
    root: ET.Element | None = None
    index = 0
    for event, elem in ET.iterparse(source, events=("start", "end")):
        if root is None:
            root = elem  # the first start event is the document element
            continue
        if event != "end" or local_name(elem.tag) != feature_tag:
            continue
        yield building_of(elem, provider, lat_first, index)
        index += 1
        # Drop the finished feature and the wfs:member that wrapped it. This is the documented
        # incremental-parsing idiom; without it the parser keeps the entire document alive.
        elem.clear()
        root.clear()


def parse_buildings(
    source,
    provider: str,
    *,
    lat_first: bool = True,
    feature_tag: str = BUILDING_TAG,
) -> list[Lod2Building]:
    return list(iter_buildings(source, provider, lat_first=lat_first, feature_tag=feature_tag))
