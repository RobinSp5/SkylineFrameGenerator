import json

import httpx
import pytest

from skylineframe.errors import FetchError
from skylineframe.features import RoofSpec
from skylineframe.fetch import (
    DEFAULT_OVERPASS_URL,
    MAX_ELEMENTS,
    USER_AGENT,
    _cache_path,
    build_query,
    fetch_overpass,
    overpass_url,
    parse_direction,
    parse_height,
    parse_min_height,
    parse_overpass,
    parse_roof,
)
from skylineframe.spec import FrameSpec, Mode

SAMPLE = {
    "version": 0.6,
    "elements": [
        {"type": "node", "id": 1, "lat": 50.0, "lon": 8.0},
        {"type": "node", "id": 2, "lat": 50.0, "lon": 8.001},
        {"type": "node", "id": 3, "lat": 50.001, "lon": 8.001},
        {"type": "node", "id": 4, "lat": 50.001, "lon": 8.0},
        {"type": "way", "id": 10, "nodes": [1, 2, 3, 4, 1], "tags": {"building": "yes", "height": "12 m"}},
        {"type": "way", "id": 11, "nodes": [1, 3], "tags": {"highway": "residential"}},
        {"type": "way", "id": 13, "nodes": [2, 4], "tags": {"highway": "footway"}},
        {"type": "node", "id": 5, "lat": 50.0002, "lon": 8.0002},
        {"type": "node", "id": 6, "lat": 50.0002, "lon": 8.0004},
        {"type": "node", "id": 7, "lat": 50.0004, "lon": 8.0004},
        {"type": "node", "id": 8, "lat": 50.0004, "lon": 8.0002},
        {"type": "way", "id": 12, "nodes": [5, 6, 7, 8, 5]},
        {
            "type": "relation",
            "id": 20,
            "members": [{"type": "way", "ref": 10, "role": "outer"}, {"type": "way", "ref": 12, "role": "inner"}],
            "tags": {"type": "multipolygon", "natural": "water"},
        },
    ],
}


def spec(**kw) -> FrameSpec:
    return FrameSpec(center_lat=50.0, center_lon=8.0, **kw)


# --- query -------------------------------------------------------------


def test_build_query_simple_only_buildings():
    q = build_query((49.9, 7.9, 50.1, 8.1), Mode.simple)
    assert 'way["building"](49.900000,7.900000,50.100000,8.100000);' in q
    assert 'relation["building"]["type"="multipolygon"]' in q
    assert "highway" not in q and "water" not in q
    assert q.strip().endswith("(._;>;);\nout body qt;")


def test_build_query_full_adds_roads_and_water():
    q = build_query((49.9, 7.9, 50.1, 8.1), Mode.full)
    assert 'way["highway"~"^(motorway|trunk|primary|secondary|tertiary|residential|unclassified|living_street|pedestrian|service)$"]' in q
    assert 'way["natural"="water"]' in q and 'relation["waterway"="riverbank"]' in q


# --- height ------------------------------------------------------------


@pytest.mark.parametrize(
    "tags,expected",
    [
        ({"height": "12"}, 12.0),
        ({"height": "12.5 m"}, 12.5),
        ({"height": "40m"}, 40.0),
        ({"building:levels": "5"}, 16.0),
        ({"height": "tall", "building:levels": "2"}, 6.4),
        ({"height": "abc", "building:levels": "x"}, 8.0),
        ({}, 8.0),
        # Mistagged heights (millimetres, centimetres, a typo) are worse than no height at all:
        # one of them alone decides the z scale of the whole model.
        ({"height": "200000"}, 8.0),
        ({"height": "0"}, 8.0),
        ({"height": "-5"}, 8.0),
        ({"height": "200000", "building:levels": "3"}, 9.6),
        ({"building:levels": "999"}, 8.0),
        ({"building:levels": "0"}, 8.0),
    ],
)
def test_parse_height(tags, expected):
    assert parse_height(tags, default_m=8.0) == pytest.approx(expected)


# --- parsing -----------------------------------------------------------


def test_parse_overpass_extracts_buildings_roads_water():
    feats = parse_overpass(SAMPLE, spec(mode=Mode.full))
    assert len(feats.buildings) == 1
    assert feats.buildings[0].height_m == 12.0
    assert feats.buildings[0].geom.geom_type == "Polygon"
    assert [r.cls for r in feats.roads] == ["residential"]  # footway ignored
    assert len(feats.water) == 1
    assert feats.water[0].geom.geom_type == "MultiPolygon"


def test_parse_overpass_keeps_building_that_is_also_relation_member():
    # way 10 is both a tagged building and the outer ring of the water relation
    feats = parse_overpass(SAMPLE, spec())
    assert len(feats.buildings) == 1


def test_parse_overpass_rejects_an_oversized_response():
    # Guard before osm2geojson: a few hundred thousand elements would otherwise be turned into
    # GeoJSON and shapely geometries before anything noticed the area is far too large.
    huge = {"elements": [{"type": "node", "id": i, "lat": 0.0, "lon": 0.0} for i in range(MAX_ELEMENTS + 1)]}
    with pytest.raises(FetchError, match="too much map data"):
        parse_overpass(huge, spec(mode=Mode.full))


# --- http + cache ------------------------------------------------------


def make_client(responses: list[int | str | dict], calls: list[tuple[str, str | None]]) -> httpx.Client:
    """Records (request body, User-Agent) per call; MockTransport ignores headers, so the
    User-Agent must be asserted explicitly or the header could be dropped unnoticed.

    An int is a status code, a dict a JSON body, a str a 200 with a non-JSON body.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.content.decode(), request.headers.get("user-agent")))
        r = responses.pop(0)
        if isinstance(r, int):
            return httpx.Response(r, text="error")
        if isinstance(r, str):
            return httpx.Response(200, text=r, headers={"content-type": "text/html"})
        return httpx.Response(200, json=r)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_overpass_uses_cache(tmp_path):
    calls: list[tuple[str, str | None]] = []
    client = make_client([SAMPLE], calls)
    first = fetch_overpass("q1", tmp_path, client=client)
    second = fetch_overpass("q1", tmp_path, client=client)
    assert first == second == SAMPLE
    assert len(calls) == 1
    assert calls[0][1] == USER_AGENT  # overpass-api.de answers 406 to unidentified clients
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_fetch_overpass_refetches_when_cache_is_corrupt(tmp_path):
    # A crash mid-write can leave a truncated cache entry; it must not poison the query forever.
    calls: list[tuple[str, str | None]] = []
    client = make_client([SAMPLE], calls)
    path = _cache_path(tmp_path, "q7")
    path.write_text("{not json")
    assert fetch_overpass("q7", tmp_path, client=client) == SAMPLE
    assert len(calls) == 1
    assert json.loads(path.read_text()) == SAMPLE


def test_fetch_overpass_retries_on_429(tmp_path):
    calls: list[tuple[str, str | None]] = []
    sleeps: list[float] = []
    client = make_client([429, SAMPLE], calls)
    data = fetch_overpass("q2", tmp_path, client=client, sleep=sleeps.append)
    assert data == SAMPLE
    assert sleeps == [2.0]


def test_fetch_overpass_gives_up_after_retries(tmp_path):
    sleeps: list[float] = []
    client = make_client([504, 504, 504], [])
    with pytest.raises(FetchError, match="Overpass"):
        fetch_overpass("q3", tmp_path, client=client, sleep=sleeps.append)
    assert sleeps == [2.0, 4.0]  # no sleep after the final attempt
    assert list(tmp_path.glob("*.json")) == []


def test_fetch_overpass_retries_on_runtime_error_remark(tmp_path):
    # Overpass reports query timeouts as HTTP 200 with a "remark" and an empty element list.
    timed_out = {
        "version": 0.6,
        "elements": [],
        "remark": 'runtime error: Query timed out in "query" at line 3 after 90 seconds.',
    }
    calls: list[tuple[str, str | None]] = []
    sleeps: list[float] = []
    client = make_client([timed_out, SAMPLE], calls)
    data = fetch_overpass("q6", tmp_path, client=client, sleep=sleeps.append)
    assert data == SAMPLE
    assert sleeps == [2.0]
    cached = list(tmp_path.glob("*.json"))
    assert len(cached) == 1
    assert json.loads(cached[0].read_text()) == SAMPLE  # the remark payload was never cached


def test_fetch_overpass_retries_on_a_non_json_body(tmp_path):
    # An overloaded Overpass instance answers 200 with an HTML error page. That is transient,
    # so it is retried and never cached.
    calls: list[tuple[str, str | None]] = []
    sleeps: list[float] = []
    client = make_client(["<html>Gateway overloaded</html>", SAMPLE], calls)
    assert fetch_overpass("q8", tmp_path, client=client, sleep=sleeps.append) == SAMPLE
    assert sleeps == [2.0]
    cached = list(tmp_path.glob("*.json"))
    assert len(cached) == 1
    assert json.loads(cached[0].read_text()) == SAMPLE


def test_fetch_overpass_does_not_retry_client_errors(tmp_path):
    sleeps: list[float] = []
    client = make_client([400], [])
    with pytest.raises(FetchError):
        fetch_overpass("q4", tmp_path, client=client, sleep=sleeps.append)
    assert sleeps == []


def test_fetch_overpass_retries_on_network_error(tmp_path):
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json=SAMPLE)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch_overpass("q5", tmp_path, client=client, sleep=lambda s: None) == SAMPLE


def test_overpass_url_from_env(monkeypatch):
    monkeypatch.delenv("SKYLINE_OVERPASS_URL", raising=False)
    assert overpass_url() == DEFAULT_OVERPASS_URL
    monkeypatch.setenv("SKYLINE_OVERPASS_URL", "http://localhost:12345/api/interpreter")
    assert overpass_url() == "http://localhost:12345/api/interpreter"


# --- recorded fixture --------------------------------------------------


def test_frankfurt_fixture_parses(frankfurt_spec, frankfurt_data):
    feats = parse_overpass(frankfurt_data, frankfurt_spec)
    assert len(feats.buildings) > 20
    assert len(feats.roads) > 5
    assert len(feats.water) >= 1
    assert sum(1 for b in feats.buildings if b.roof is not None) > 20


def test_bankenviertel_fixture_has_building_parts(bankenviertel_spec, bankenviertel_data):
    feats = parse_overpass(bankenviertel_data, bankenviertel_spec)
    assert sum(1 for b in feats.buildings if b.is_part) > 0
    assert all(b.osm_id.startswith(("way/", "relation/")) for b in feats.buildings)


# --- building parts, roofs, estimates ----------------------------------

PARTS_SAMPLE = {
    "version": 0.6,
    "elements": [
        # outline 200:
        {"type": "node", "id": 101, "lat": 50.0000, "lon": 8.0000},
        {"type": "node", "id": 102, "lat": 50.0000, "lon": 8.0020},
        {"type": "node", "id": 103, "lat": 50.0020, "lon": 8.0020},
        {"type": "node", "id": 104, "lat": 50.0020, "lon": 8.0000},
        {"type": "way", "id": 200, "nodes": [101, 102, 103, 104, 101], "tags": {"building": "yes", "height": "20"}},
        # part 201 inside the outline, starting at 10 m, with a gabled roof
        {"type": "node", "id": 105, "lat": 50.0005, "lon": 8.0005},
        {"type": "node", "id": 106, "lat": 50.0005, "lon": 8.0015},
        {"type": "node", "id": 107, "lat": 50.0015, "lon": 8.0015},
        {"type": "node", "id": 108, "lat": 50.0015, "lon": 8.0005},
        {
            "type": "way",
            "id": 201,
            "nodes": [105, 106, 107, 108, 105],
            "tags": {
                "building:part": "yes",
                "min_height": "10 m",
                "height": "40",
                "roof:shape": "gabled",
                "roof:height": "3",
                "roof:direction": "NE",
            },
        },
        # 202: a canopy -> dropped
        {"type": "node", "id": 109, "lat": 50.0030, "lon": 8.0000},
        {"type": "node", "id": 110, "lat": 50.0030, "lon": 8.0010},
        {"type": "node", "id": 111, "lat": 50.0040, "lon": 8.0010},
        {"type": "node", "id": 112, "lat": 50.0040, "lon": 8.0000},
        {"type": "way", "id": 202, "nodes": [109, 110, 111, 112, 109], "tags": {"building": "roof"}},
        # 203: building:part=no -> dropped
        {"type": "node", "id": 113, "lat": 50.0050, "lon": 8.0000},
        {"type": "node", "id": 114, "lat": 50.0050, "lon": 8.0010},
        {"type": "node", "id": 115, "lat": 50.0060, "lon": 8.0010},
        {"type": "node", "id": 116, "lat": 50.0060, "lon": 8.0000},
        {"type": "way", "id": 203, "nodes": [113, 114, 115, 116, 113], "tags": {"building:part": "no"}},
        # 204: part without any height, with building:min_level
        {"type": "node", "id": 117, "lat": 50.0070, "lon": 8.0000},
        {"type": "node", "id": 118, "lat": 50.0070, "lon": 8.0010},
        {"type": "node", "id": 119, "lat": 50.0080, "lon": 8.0010},
        {"type": "node", "id": 120, "lat": 50.0080, "lon": 8.0000},
        {
            "type": "way",
            "id": 204,
            "nodes": [117, 118, 119, 120, 117],
            "tags": {"building:part": "yes", "building:min_level": "5", "roof:shape": "onion"},
        },
        # 205: house without any height tag -> height_m 0.0, kind "house"
        {"type": "node", "id": 121, "lat": 50.0090, "lon": 8.0000},
        {"type": "node", "id": 122, "lat": 50.0090, "lon": 8.0010},
        {"type": "node", "id": 123, "lat": 50.0100, "lon": 8.0010},
        {"type": "node", "id": 124, "lat": 50.0100, "lon": 8.0000},
        {"type": "way", "id": 205, "nodes": [121, 122, 123, 124, 121], "tags": {"building": "house", "roof:shape": "brezel"}},
    ],
}


def by_id(feats) -> dict[str, object]:
    return {b.osm_id: b for b in feats.buildings}


def test_build_query_asks_for_building_parts():
    q = build_query((49.9, 7.9, 50.1, 8.1), Mode.simple)
    assert 'way["building:part"](49.900000,7.900000,50.100000,8.100000);' in q
    assert 'relation["building:part"]["type"="multipolygon"]' in q


def test_parse_overpass_reads_outline_and_part():
    feats = parse_overpass(PARTS_SAMPLE, spec())
    buildings = by_id(feats)
    # ids carry the element type: way and relation ids are separate number spaces (spec §3).
    assert set(buildings) == {"way/200", "way/201", "way/204", "way/205"}

    outline = buildings["way/200"]
    assert outline.is_part is False
    assert outline.height_m == 20.0 and outline.height_is_top is True
    assert outline.kind == "yes" and outline.min_height_m == 0.0 and outline.roof is None

    part = buildings["way/201"]
    assert part.is_part is True and part.kind == "yes"
    assert part.height_m == 40.0 and part.height_is_top is True
    assert part.min_height_m == 10.0
    assert part.roof == RoofSpec(shape="gabled", height_m=3.0, direction_deg=45.0)


def test_parse_overpass_reads_min_level_and_maps_roof_aliases():
    buildings = by_id(parse_overpass(PARTS_SAMPLE, spec()))
    part = buildings["way/204"]
    assert part.min_height_m == pytest.approx(16.0)  # 5 levels x 3.2 m
    assert part.height_m == 0.0  # unknown; prepare fills it from the outline or the default
    assert part.roof is not None and part.roof.shape == "dome"  # onion -> dome
    assert part.roof.height_m == 0.0  # untagged; prepare derives it from the footprint


def test_parse_overpass_leaves_unknown_roof_and_height_flat():
    house = by_id(parse_overpass(PARTS_SAMPLE, spec()))["way/205"]
    assert house.roof is None  # "brezel" is not a supported shape -> flat
    assert house.height_m == 0.0 and house.height_is_top is False
    assert house.kind == "house"


@pytest.mark.parametrize(
    "tags,expected",
    [
        ({"min_height": "12"}, 12.0),
        ({"min_height": "12.5 m"}, 12.5),
        ({"building:min_level": "4"}, 12.8),
        ({"min_height": "junk", "building:min_level": "2"}, 6.4),
        ({}, 0.0),
        ({"min_height": "-3"}, 0.0),
        ({"min_height": "5000"}, 0.0),
    ],
)
def test_parse_min_height(tags, expected):
    assert parse_min_height(tags) == pytest.approx(expected)


@pytest.mark.parametrize(
    "raw,expected",
    [("45", 45.0), ("45.5", 45.5), ("N", 0.0), ("NE", 45.0), ("SSW", 202.5), ("e", 90.0), ("400", 40.0), ("", None), (None, None), ("uphill", None)],
)
def test_parse_direction(raw, expected):
    assert parse_direction(raw) == expected


@pytest.mark.parametrize(
    "tags,shape,height",
    [
        ({"roof:shape": "gabled"}, "gabled", 0.0),
        ({"roof:shape": "half-hipped"}, "half_hipped", 0.0),
        ({"roof:shape": "HIPPED"}, "hipped", 0.0),
        ({"roof:shape": "gabled", "roof:height": "4 m"}, "gabled", 4.0),
        ({"roof:shape": "gabled", "roof:levels": "2"}, "gabled", 6.4),
        ({"roof:shape": "gabled", "roof:height": "500"}, "gabled", 0.0),
    ],
)
def test_parse_roof(tags, shape, height):
    roof = parse_roof(tags)
    assert roof is not None
    assert roof.shape == shape
    assert roof.height_m == pytest.approx(height)


@pytest.mark.parametrize("tags", [{}, {"roof:shape": "flat"}, {"roof:shape": "something"}])
def test_parse_roof_returns_none_for_flat_and_unknown(tags):
    assert parse_roof(tags) is None
