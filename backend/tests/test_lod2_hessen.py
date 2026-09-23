import logging

import httpx
import pytest

import skylineframe.lod2.hessen as hessen
from skylineframe.lod2.hessen import (
    MAX_RESPONSE_BYTES,
    OUTPUT_CRS,
    QUERY_CRS,
    TYPE_NAMES,
    WFS_URL,
    HessenProvider,
    build_url,
    covers_bbox,
)
from skylineframe.lod2.provider import REGISTRY, select_provider
from skylineframe.lod2.sources import HESSEN

FRANKFURT = (50.1080, 8.6790, 50.1130, 8.6850)  # (south, west, north, east)
BERLIN = (52.5100, 13.3800, 52.5200, 13.4000)
BORDER = (49.9000, 7.5000, 49.9500, 7.8500)  # half of it is west of the Hessen coverage
# What a WFS sends when it dislikes a parameter — sometimes with HTTP 400, sometimes with 200.
EXCEPTION_REPORT = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/1.1" version="2.0.0">'
    b'<ows:Exception exceptionCode="InvalidParameterValue" locator="srsName">'
    b"<ows:ExceptionText>Unknown CRS</ows:ExceptionText></ows:Exception></ows:ExceptionReport>"
)


def make_client(body: bytes | int, calls: list[httpx.Request]) -> httpx.Client:
    """An int is a status code, bytes a 200 with that body."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if isinstance(body, int):
            return httpx.Response(body, text="error")
        return httpx.Response(200, content=body, headers={"content-type": "application/gml+xml"})

    return httpx.Client(transport=httpx.MockTransport(handler))


# --- coverage ----------------------------------------------------------


def test_covers_only_a_bbox_that_lies_entirely_inside_hessen():
    assert covers_bbox(FRANKFURT) is True
    assert covers_bbox(BERLIN) is False
    # Half outside is not covered: mixing LoD2 and estimated heights inside one square would be
    # a visible step in the model, and which half wins would depend on the state border.
    assert covers_bbox(BORDER) is False


def test_select_provider_is_deterministic():
    provider = select_provider(FRANKFURT)
    assert provider is not None and provider.name == "hessen"
    assert provider.attribution is HESSEN
    assert select_provider(BERLIN) is None
    # The registry order is the resolution order (spec §4).
    assert [p.name for p in REGISTRY][0] == "hessen"


# --- url ---------------------------------------------------------------


def test_build_url_uses_lat_lon_order_and_the_3d_crs():
    url = httpx.URL(build_url(FRANKFURT))
    assert str(url).startswith(WFS_URL)
    assert url.params["service"] == "WFS"
    assert url.params["version"] == "2.0.0"
    assert url.params["request"] == "GetFeature"
    assert url.params["typeNames"] == TYPE_NAMES == "bu-core3d:Building"
    # south,west,north,east = lat,lon,lat,lon, then the CRS the order belongs to.
    assert url.params["bbox"] == f"50.108,8.679,50.113,8.685,{QUERY_CRS}"
    assert QUERY_CRS == "urn:ogc:def:crs:EPSG::4326"
    # EPSG::7423 is ETRS89 + DHHN2016: without it the service answers in 2D and every z is gone.
    assert url.params["srsName"] == OUTPUT_CRS == "urn:ogc:def:crs:EPSG::7423"
    assert "count" not in url.params


def test_build_url_is_stable_for_the_same_bbox():
    assert build_url(FRANKFURT) == build_url(FRANKFURT)
    assert build_url(FRANKFURT) != build_url(BERLIN)


# --- fetch -------------------------------------------------------------


def test_fetch_parses_the_recorded_response(tmp_path, lod2_xml_path):
    calls: list[httpx.Request] = []
    client = make_client(lod2_xml_path.read_bytes(), calls)
    buildings = HessenProvider().fetch(FRANKFURT, tmp_path, client=client)
    assert [b.name for b in buildings] == ["(1:Kulturschirn)", "(1:Paulskirche)", None]
    assert len(calls) == 1
    assert "bbox=" in str(calls[0].url)


def test_fetch_uses_the_disk_cache_on_the_second_call(tmp_path, lod2_xml_path):
    calls: list[httpx.Request] = []
    client = make_client(lod2_xml_path.read_bytes(), calls)
    provider = HessenProvider()
    first = provider.fetch(FRANKFURT, tmp_path, client=client)
    second = provider.fetch(FRANKFURT, tmp_path, client=client)
    assert [b.osm_id for b in first] == [b.osm_id for b in second]
    assert len(calls) == 1
    # Keyed per bbox, like the Overpass cache; a 1500 m Frankfurt square is ~145 MB in here.
    assert len(list(tmp_path.glob("*.xml"))) == 1


def test_a_different_bbox_is_a_different_cache_entry(tmp_path, lod2_xml_path):
    calls: list[httpx.Request] = []
    client = make_client(lod2_xml_path.read_bytes(), calls)
    provider = HessenProvider()
    provider.fetch(FRANKFURT, tmp_path, client=client)
    provider.fetch((50.1000, 8.6000, 50.1050, 8.6100), tmp_path, client=client)
    assert len(calls) == 2
    assert len(list(tmp_path.glob("*.xml"))) == 2


def test_a_network_error_gives_an_empty_list_and_a_warning(tmp_path, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with caplog.at_level(logging.WARNING, logger="skylineframe.lod2.hessen"):
        assert HessenProvider().fetch(FRANKFURT, tmp_path, client=client) == []
    assert "LoD2" in caplog.text
    assert list(tmp_path.iterdir()) == []  # nothing half-written is left behind


def test_an_http_error_gives_an_empty_list(tmp_path, caplog):
    client = make_client(503, [])
    with caplog.at_level(logging.WARNING, logger="skylineframe.lod2.hessen"):
        assert HessenProvider().fetch(FRANKFURT, tmp_path, client=client) == []
    assert "503" in caplog.text
    assert list(tmp_path.iterdir()) == []


def test_an_oversized_response_is_abandoned(tmp_path, caplog, monkeypatch):
    monkeypatch.setattr("skylineframe.lod2.hessen.MAX_RESPONSE_BYTES", 16)
    client = make_client(b"<x>" + b"y" * 1000 + b"</x>", [])
    with caplog.at_level(logging.WARNING, logger="skylineframe.lod2.hessen"):
        assert HessenProvider().fetch(FRANKFURT, tmp_path, client=client) == []
    assert "too large" in caplog.text
    assert list(tmp_path.iterdir()) == []


def test_an_unreadable_body_gives_an_empty_list_and_drops_the_cache_entry(tmp_path, caplog):
    client = make_client(b"<not xml", [])
    with caplog.at_level(logging.WARNING, logger="skylineframe.lod2.hessen"):
        assert HessenProvider().fetch(FRANKFURT, tmp_path, client=client) == []
    # A broken answer must not be served from the cache for the rest of the day.
    assert list(tmp_path.iterdir()) == []


def test_a_200_exception_report_is_not_taken_for_an_empty_answer(tmp_path, caplog):
    # A WFS may return an error document with HTTP 200. It is well-formed XML with zero buildings,
    # so caching it would leave this bbox silently on estimated heights for the life of the cache.
    client = make_client(EXCEPTION_REPORT, [])
    with caplog.at_level(logging.WARNING, logger="skylineframe.lod2.hessen"):
        assert HessenProvider().fetch(FRANKFURT, tmp_path, client=client) == []
    assert "ExceptionReport" in caplog.text
    assert list(tmp_path.iterdir()) == []


def test_a_corrupt_cache_entry_is_fetched_again_in_the_same_run(tmp_path, lod2_xml_path, caplog):
    calls: list[httpx.Request] = []
    client = make_client(lod2_xml_path.read_bytes(), calls)
    provider = HessenProvider()
    provider.fetch(FRANKFURT, tmp_path, client=client)
    next(iter(tmp_path.glob("*.xml"))).write_bytes(b"<truncated")  # e.g. a crash mid-write
    with caplog.at_level(logging.WARNING, logger="skylineframe.lod2.hessen"):
        buildings = provider.fetch(FRANKFURT, tmp_path, client=client)
    # A bad entry is a miss, like in the Overpass cache: this run recovers instead of the next one.
    assert [b.name for b in buildings] == ["(1:Kulturschirn)", "(1:Paulskirche)", None]
    assert len(calls) == 2
    assert "cache entry" in caplog.text


def test_the_size_limit_is_generous_enough_for_a_real_square():
    # Measured: a 1500 m Frankfurt square is 145 MB.
    assert MAX_RESPONSE_BYTES >= 256 * 1024 * 1024


def test_a_failed_write_falls_back_to_openstreetmap(tmp_path, lod2_xml_path, monkeypatch, caplog):
    # Streaming 152 MB to disk can fail on a full volume or a quota long after the request
    # succeeded. That is an OSError, not an HTTPError, and it must cost the run its heights
    # rather than the run itself (spec §9).
    calls: list[httpx.Request] = []
    client = make_client(lod2_xml_path.read_bytes(), calls)

    def no_space(src, dst):
        raise OSError("No space left on device")

    monkeypatch.setattr(hessen.os, "replace", no_space)
    with caplog.at_level(logging.WARNING, logger="skylineframe.lod2.hessen"):
        assert HessenProvider().fetch(FRANKFURT, tmp_path, client=client) == []
    assert "No space left on device" in caplog.text
    assert list(tmp_path.iterdir()) == []  # the staged file is cleaned up
