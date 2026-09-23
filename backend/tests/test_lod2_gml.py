import io
import xml.etree.ElementTree as ET

import pytest

from skylineframe.lod2.gml import (
    building_of,
    exterior_rings,
    iter_buildings,
    local_name,
    parse_buildings,
    poslist_triples,
)

# A three-ring stand-in for the shapes the real service emits: a closed exterior ring, a ring
# with an interior (which must be ignored — only the exterior carries the face) and a
# gml:LineString curve member, which a naive posList scan would pick up and which breaks
# everything downstream because it is not a face at all.
MINI = """<?xml version='1.0' encoding='UTF-8'?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0"
    xmlns:gml="http://www.opengis.net/gml/3.2"
    xmlns:bu-core3d="http://inspire.ec.europa.eu/schemas/bu-core3d/4.0"
    xmlns:base="http://inspire.ec.europa.eu/schemas/base/3.3">
  <wfs:member>
    <bu-core3d:Building gml:id="Building_A">
      <gml:name>(1:Haus)</gml:name>
      <bu-base:inspireId xmlns:bu-base="http://inspire.ec.europa.eu/schemas/bu-base/4.0">
        <base:Identifier><base:localId>Building_A</base:localId></base:Identifier>
      </bu-base:inspireId>
      <gml:MultiSurface>
        <gml:surfaceMember>
          <gml:Polygon srsName="urn:ogc:def:crs:EPSG::7423">
            <gml:exterior><gml:LinearRing><gml:posList>
              50.0 8.0 10.0 50.0 8.001 10.0 50.001 8.001 10.0 50.001 8.0 10.0 50.0 8.0 10.0
            </gml:posList></gml:LinearRing></gml:exterior>
            <gml:interior><gml:LinearRing><gml:posList>
              50.0004 8.0004 10.0 50.0004 8.0006 10.0 50.0006 8.0006 10.0 50.0004 8.0004 10.0
            </gml:posList></gml:LinearRing></gml:interior>
          </gml:Polygon>
        </gml:surfaceMember>
        <gml:surfaceMember>
          <gml:Polygon>
            <gml:exterior><gml:LinearRing><gml:posList>
              50.0 8.0 0.0 50.0 8.001 0.0 50.0 8.001 10.0 50.0 8.0 10.0 50.0 8.0 0.0
            </gml:posList></gml:LinearRing></gml:exterior>
          </gml:Polygon>
        </gml:surfaceMember>
        <gml:surfaceMember>
          <gml:Polygon>
            <gml:exterior><gml:LinearRing><gml:posList>
              50.0 8.0 0.0 50.0 8.0 0.0 50.0 8.0 0.0
            </gml:posList></gml:LinearRing></gml:exterior>
          </gml:Polygon>
        </gml:surfaceMember>
      </gml:MultiSurface>
      <bu-core3d:terrainIntersection>
        <gml:MultiCurve><gml:curveMember><gml:LineString><gml:posList>
          50.0 8.0 0.0 50.0 8.001 0.0 50.001 8.001 0.0
        </gml:posList></gml:LineString></gml:curveMember></gml:MultiCurve>
      </bu-core3d:terrainIntersection>
    </bu-core3d:Building>
  </wfs:member>
</wfs:FeatureCollection>
"""


def test_local_name_survives_comments():
    assert local_name("{http://www.opengis.net/gml/3.2}Polygon") == "Polygon"
    assert local_name("Polygon") == "Polygon"
    # ET gives comments a callable tag; without the guard the parser blows up on the first one,
    # and the recorded Hessen response starts with a comment.
    assert local_name(ET.Comment) == ""


def test_poslist_triples_reads_groups_of_three():
    assert poslist_triples(" 1 2 3\n4 5 6 ") == [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)]
    assert poslist_triples("") == []


def test_exterior_rings_swaps_lat_lon_drops_the_closing_vertex_and_skips_line_strings():
    feature = next(e for e in ET.fromstring(MINI).iter() if local_name(e.tag) == "Building")
    rings = exterior_rings(feature)
    # Two usable faces: the roof and one wall. The interior ring, the degenerate ring (one
    # distinct point) and the terrain intersection LineString are all out.
    assert len(rings) == 2
    roof = rings[0]
    assert len(roof) == 4  # closing vertex dropped
    assert roof[0] == (8.0, 50.0, 10.0)  # lat lon z -> (lon, lat, z)
    assert roof[1] == (8.001, 50.0, 10.0)


# A GML 3.2 exterior may hold a <gml:Ring> of curve members instead of a <gml:LinearRing>.
# It is a curve, not a face, and only the LinearRing step in exterior_rings keeps it out.
CURVE_RING = """<?xml version='1.0' encoding='UTF-8'?>
<bu-core3d:Building xmlns:gml="http://www.opengis.net/gml/3.2"
    xmlns:bu-core3d="http://inspire.ec.europa.eu/schemas/bu-core3d/4.0" gml:id="Building_C">
  <gml:MultiSurface><gml:surfaceMember><gml:Polygon>
    <gml:exterior><gml:Ring><gml:curveMember><gml:LineString><gml:posList>
      50.0 8.0 10.0 50.0 8.001 10.0 50.001 8.001 10.0 50.0 8.0 10.0
    </gml:posList></gml:LineString></gml:curveMember></gml:Ring></gml:exterior>
  </gml:Polygon></gml:surfaceMember></gml:MultiSurface>
</bu-core3d:Building>
"""

# Six 2D vertices: twelve tokens, a multiple of three even though the points are pairs, so the
# divisibility check cannot see the problem and only srsDimension tells this ring from a 3D one.
TWO_D = """<?xml version='1.0' encoding='UTF-8'?>
<bu-core3d:Building xmlns:gml="http://www.opengis.net/gml/3.2"
    xmlns:bu-core3d="http://inspire.ec.europa.eu/schemas/bu-core3d/4.0" gml:id="Building_D">
  <gml:MultiSurface><gml:surfaceMember><gml:Polygon>
    <gml:exterior><gml:LinearRing {ring_attr}><gml:posList {pos_attr}>
      50.0 8.0 50.0 8.001 50.001 8.001 50.001 8.0 50.0005 8.0005 50.0 8.0
    </gml:posList></gml:LinearRing></gml:exterior>
  </gml:Polygon></gml:surfaceMember></gml:MultiSurface>
</bu-core3d:Building>
"""

# Two features with neither a localId nor a gml:id. Without the index fallback both answer to
# "lod2/hessen/" and the second silently overwrites the first wherever buildings are keyed by id.
NAMELESS = """<?xml version='1.0' encoding='UTF-8'?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0"
    xmlns:gml="http://www.opengis.net/gml/3.2"
    xmlns:bu-core3d="http://inspire.ec.europa.eu/schemas/bu-core3d/4.0">
  <wfs:member><bu-core3d:Building/></wfs:member>
  <wfs:member><bu-core3d:Building/></wfs:member>
</wfs:FeatureCollection>
"""


def test_exterior_rings_ignores_a_ring_built_from_curve_members():
    feature = ET.fromstring(CURVE_RING)
    # The spec rule is absolute: a ring comes from gml:LinearRing or it does not come at all.
    assert exterior_rings(feature) == ()


def test_exterior_rings_skips_a_two_dimensional_poslist():
    on_poslist = ET.fromstring(TWO_D.format(ring_attr="", pos_attr='srsDimension="2"'))
    on_ring = ET.fromstring(TWO_D.format(ring_attr='srsDimension="2"', pos_attr=""))
    assert exterior_rings(on_poslist) == ()
    assert exterior_rings(on_ring) == ()
    # A missing attribute stays 3D — the Hessen service never writes one.
    assert len(exterior_rings(ET.fromstring(TWO_D.format(ring_attr="", pos_attr="")))) == 1


def test_id_less_features_get_distinct_ids():
    ids = [b.osm_id for b in parse_buildings(io.BytesIO(NAMELESS.encode()), "hessen")]
    assert ids == ["lod2/hessen/#0", "lod2/hessen/#1"]


def test_exterior_rings_can_keep_the_axis_order():
    # CityGML tiles are projected and their posList is x y z, so the swap has to be switchable.
    feature = next(e for e in ET.fromstring(MINI).iter() if local_name(e.tag) == "Building")
    assert exterior_rings(feature, lat_first=False)[0][0] == (50.0, 8.0, 10.0)


def test_building_of_reads_local_id_and_name():
    feature = next(e for e in ET.fromstring(MINI).iter() if local_name(e.tag) == "Building")
    building = building_of(feature, "hessen")
    assert building.osm_id == "lod2/hessen/Building_A"
    assert building.name == "(1:Haus)"


def test_parse_buildings_streams_from_a_byte_stream():
    buildings = parse_buildings(io.BytesIO(MINI.encode()), "hessen")
    assert [b.osm_id for b in buildings] == ["lod2/hessen/Building_A"]


# --- recorded response --------------------------------------------------


def test_fixture_has_the_three_recorded_buildings(lod2_frankfurt):
    assert [b.name for b in lod2_frankfurt] == ["(1:Kulturschirn)", "(1:Paulskirche)", None]
    assert [len(b.surfaces) for b in lod2_frankfurt] == [137, 217, 6]
    assert all(b.osm_id.startswith("lod2/hessen/Building_DEHE") for b in lod2_frankfurt)


def test_fixture_ring_z_ranges_are_metres_above_sea_level(lod2_frankfurt):
    spans = [
        (min(z for r in b.surfaces for _, _, z in r), max(z for r in b.surfaces for _, _, z in r))
        for b in lod2_frankfurt
    ]
    assert spans[0] == pytest.approx((96.421, 120.632), abs=0.001)
    assert spans[1] == pytest.approx((97.611, 154.119), abs=0.001)
    assert spans[2] == pytest.approx((98.031, 101.876), abs=0.001)


def test_fixture_rings_are_lon_lat(lod2_frankfurt):
    for building in lod2_frankfurt:
        for ring in building.surfaces:
            for lon, lat, _z in ring:
                assert 8.6 < lon < 8.8  # Frankfurt, not 50.x
                assert 50.0 < lat < 50.2


def test_fixture_excludes_every_line_string(lod2_xml_path):
    # 360 gml:Polygon and 212 gml:LineString are in the document; a naive posList scan would
    # return 572 rings and the solidifier would choke on the curve members.
    root = ET.parse(lod2_xml_path).getroot()
    polygons = sum(1 for e in root.iter() if local_name(e.tag) == "Polygon")
    line_strings = sum(1 for e in root.iter() if local_name(e.tag) == "LineString")
    assert (polygons, line_strings) == (360, 212)
    parsed = parse_buildings(str(lod2_xml_path), "hessen")
    assert sum(len(b.surfaces) for b in parsed) == polygons


def test_streaming_and_one_shot_agree(lod2_xml_path, lod2_frankfurt):
    # The production path streams; this is the only place where the whole document is held in
    # memory, to prove that clearing the root as we go changes nothing about the result.
    root = ET.parse(lod2_xml_path).getroot()
    one_shot = [building_of(e, "hessen") for e in root.iter() if local_name(e.tag) == "Building"]
    assert lod2_frankfurt == one_shot


def test_iter_buildings_is_lazy(lod2_xml_path):
    # A 1500 m square is a 145 MB document with 6119 buildings; the caller must be able to stop.
    first = next(iter_buildings(str(lod2_xml_path), "hessen"))
    assert first.name == "(1:Kulturschirn)"
