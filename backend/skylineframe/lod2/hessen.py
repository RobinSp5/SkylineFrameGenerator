"""Hessen LoD2 provider: INSPIRE WFS 2.0.0, one GetFeature per bounding box (spec §3.1).

The response is streamed to a cache file and parsed from there: a 1500 m Frankfurt square is
5 302 buildings in 128 MB of GML, which has no business sitting in memory twice.
"""

import hashlib
import logging
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from ..features import Lod2Building
from .gml import local_name, parse_buildings
from .sources import HESSEN, Attribution

log = logging.getLogger(__name__)

WFS_URL = "https://inspire-hessen.de/ows/services/org.2.ef07833e-78a6-4c2c-a895-e31de788aac3_wfs"
TYPE_NAMES = "bu-core3d:Building"
# The bbox is given in EPSG::4326 with the CRS spelled out, which is what fixes the axis order to
# lat,lon; EPSG::7423 (ETRS89 + DHHN2016) is the three-dimensional output, and without it the
# service answers in 2D and every height is gone.
QUERY_CRS = "urn:ogc:def:crs:EPSG::4326"
OUTPUT_CRS = "urn:ogc:def:crs:EPSG::7423"
# (south, west, north, east) of the published coverage, spec §3.1.
COVERAGE = (49.396, 7.777, 51.655, 10.224)
MAX_RESPONSE_BYTES = 256 * 1024 * 1024  # ~a 2000 m square; beyond that we fall back to OSM
TIMEOUT_S = 120.0
# The document element of a successful GetFeature. Anything else is an error document.
FEATURE_COLLECTION_TAG = "FeatureCollection"


def covers_bbox(bbox: tuple[float, float, float, float]) -> bool:
    """True when the whole query box lies inside the coverage.

    Not "intersects": a square on the state border would come back half LoD2 and half estimated,
    and the step between the two halves would be the most visible thing in the print.
    """
    south, west, north, east = bbox
    c_south, c_west, c_north, c_east = COVERAGE
    return c_south <= south and c_west <= west and north <= c_north and east <= c_east


def build_url(bbox: tuple[float, float, float, float]) -> str:
    south, west, north, east = bbox
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": TYPE_NAMES,
        # .10g keeps the full precision of a query_bbox corner while dropping trailing zeros;
        # plain %g would round to six significant digits, which is metres on the ground and the
        # 100 m margin of query_bbox is only a default, not a guarantee.
        "bbox": f"{south:.10g},{west:.10g},{north:.10g},{east:.10g},{QUERY_CRS}",
        "srsName": OUTPUT_CRS,
    }
    return str(httpx.URL(WFS_URL, params=params))


def _cache_path(cache_dir: Path, url: str) -> Path:
    return cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".xml")


def _document_element(path: Path) -> str:
    """Local name of the document element, or "" when the file is not readable XML.

    Only the first start event is pulled, so this costs one tag no matter how large the file is.
    """
    try:
        for _event, element in ET.iterparse(path, events=("start",)):
            return local_name(element.tag)
    except (ET.ParseError, OSError):
        return ""
    return ""


def _parse(path: Path, provider: str, what: str) -> list[Lod2Building] | None:
    """The buildings in `path`, or None (with a warning) when it cannot be read."""
    try:
        return parse_buildings(str(path), provider)
    except (ET.ParseError, OSError) as exc:
        log.warning("LoD2 Hessen: unreadable %s (%s)", what, exc)
        return None


def _download(url: str, path: Path, client: httpx.Client | None) -> bool:
    """Stream the response into `path`. False (with a warning) when anything went wrong."""
    owns_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT_S, follow_redirects=True)
    staged = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        with client.stream("GET", url) as response:
            if response.status_code != 200:
                log.warning("LoD2 Hessen: HTTP %s; continuing with OpenStreetMap", response.status_code)
                return False
            size = 0
            with staged.open("wb") as handle:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        log.warning(
                            "LoD2 Hessen: response too large (over %d bytes); continuing with OpenStreetMap",
                            MAX_RESPONSE_BYTES,
                        )
                        return False
                    handle.write(chunk)
        root = _document_element(staged)
        if root != FEATURE_COLLECTION_TAG:
            # A WFS is free to answer an <ows:ExceptionReport> with HTTP 200. It is well-formed
            # XML holding zero buildings, so without this check it would be cached as a perfectly
            # good empty answer and this bounding box would quietly stay on estimated heights for
            # the whole life of the cache, with nothing in the log to say why.
            log.warning(
                "LoD2 Hessen: response is %s, not a FeatureCollection; continuing with OpenStreetMap",
                root or "not readable XML",
            )
            return False
        # Move into place only once the body is complete: a truncated cache entry would poison
        # this bounding box for as long as the cache lives.
        os.replace(staged, path)
        return True
    except httpx.HTTPError as exc:
        log.warning("LoD2 Hessen: %s; continuing with OpenStreetMap", exc)
        return False
    finally:
        staged.unlink(missing_ok=True)
        if owns_client:
            client.close()


class HessenProvider:
    """LoD2 for the state of Hessen (spec §3.1)."""

    name = "hessen"
    attribution: Attribution = HESSEN

    def covers(self, bbox: tuple[float, float, float, float]) -> bool:
        return covers_bbox(bbox)

    def fetch(
        self,
        bbox: tuple[float, float, float, float],
        cache_dir: Path,
        client: httpx.Client | None = None,
    ) -> list[Lod2Building]:
        cache_dir.mkdir(parents=True, exist_ok=True)
        url = build_url(bbox)
        path = _cache_path(cache_dir, url)
        if path.is_file():
            cached = _parse(path, self.name, "cache entry")
            if cached is not None:
                return cached
            # A crash mid-write leaves a truncated entry. Like the Overpass cache, that counts as
            # a miss and is fetched again in this very run, instead of costing the run its heights.
            path.unlink(missing_ok=True)
        if not _download(url, path, client):
            return []
        buildings = _parse(path, self.name, "response")
        if buildings is None:
            # The server just answered with something unreadable; asking it again straight away
            # would only repeat it, so this run continues on OpenStreetMap.
            path.unlink(missing_ok=True)
            return []
        return buildings
