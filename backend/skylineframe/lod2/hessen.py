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
from .gml import parse_buildings
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
        "bbox": f"{south:g},{west:g},{north:g},{east:g},{QUERY_CRS}",
        "srsName": OUTPUT_CRS,
    }
    return str(httpx.URL(WFS_URL, params=params))


def _cache_path(cache_dir: Path, url: str) -> Path:
    return cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".xml")


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
        if not path.is_file() and not _download(url, path, client):
            return []
        try:
            return parse_buildings(str(path), self.name)
        except ET.ParseError as exc:
            log.warning("LoD2 Hessen: unreadable response (%s); continuing with OpenStreetMap", exc)
            path.unlink(missing_ok=True)
            return []
