"""Overpass API access: query building, disk cache with retries, and parsing into Features."""

import hashlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import osm2geojson
from shapely.geometry import shape

from .errors import FetchError
from .features import Building, Features, Road, Water
from .project import query_bbox
from .spec import LEVEL_HEIGHT_M, ROAD_CLASSES, FrameSpec, Mode

DEFAULT_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# overpass-api.de rejects unidentified clients with HTTP 406 (both the httpx default UA and
# browser UA strings), so identify the application as the Overpass usage policy asks.
USER_AGENT = "skylineframe/0.1.0 (Skyline Frame Generator; 3D-printable OSM city squares)"
RETRY_STATUSES = {429, 502, 503, 504}
MAX_HEIGHT_M = 1000.0  # the tallest building on earth is ~830 m; anything above is mistagged
MAX_LEVELS = 300.0
# Soft limit on one Overpass answer. Beyond this the run would spend minutes in osm2geojson and
# shapely before the mesh stage ever starts, so the area is rejected up front.
MAX_ELEMENTS = 250_000
WATER_SELECTORS = (
    '["natural"="water"]',
    '["waterway"="riverbank"]',
    '["landuse"="reservoir"]',
    '["natural"="bay"]',
)


def build_query(bbox: tuple[float, float, float, float], mode: Mode) -> str:
    south, west, north, east = bbox
    bb = f"({south:.6f},{west:.6f},{north:.6f},{east:.6f})"
    parts = [f'way["building"]{bb};', f'relation["building"]["type"="multipolygon"]{bb};']
    if mode == Mode.full:
        classes = "|".join(ROAD_CLASSES)
        parts.append(f'way["highway"~"^({classes})$"]{bb};')
        for selector in WATER_SELECTORS:
            parts.append(f"way{selector}{bb};")
            parts.append(f"relation{selector}{bb};")
    body = "\n  ".join(parts)
    # (._;>;) unions the matched elements with everything they reference, so every way and node
    # is emitted exactly once *with* tags. The classic "out body; >; out skel qt;" would emit member
    # ways twice (once tagged, once as tagless skeleton) and osm2geojson could shadow the tagged copy.
    return f"[out:json][timeout:90];\n(\n  {body}\n);\n(._;>;);\nout body qt;\n"


def parse_height(tags: dict, default_m: float) -> float:
    """Height in metres, falling back to building:levels and then to the spec default.

    A value outside MAX_HEIGHT_M / MAX_LEVELS is a tagging mistake (millimetres, a stray zero),
    and a single one of them would stretch the z scale of the whole model, so it is treated
    exactly like an unparsable one.
    """
    raw = tags.get("height")
    if raw:
        try:
            height = float(raw.strip().removesuffix("m").strip())
        except ValueError:
            pass
        else:
            if 0 < height <= MAX_HEIGHT_M:
                return height
    levels = tags.get("building:levels")
    if levels:
        try:
            count = float(levels)
        except ValueError:
            pass
        else:
            if 0 < count <= MAX_LEVELS:
                return count * LEVEL_HEIGHT_M
    return default_m


def _is_water(tags: dict) -> bool:
    return (
        tags.get("natural") in ("water", "bay")
        or tags.get("waterway") == "riverbank"
        or tags.get("landuse") == "reservoir"
    )


def parse_overpass(data: dict, spec: FrameSpec) -> Features:
    if len(data.get("elements", [])) > MAX_ELEMENTS:
        raise FetchError("The selected area contains too much map data; choose a smaller square or the simple mode.")
    # filter_used_refs=False keeps tagged ways that are also relation members (e.g. a building
    # that is the outer ring of a multipolygon); untagged members are dropped below.
    geojson = osm2geojson.json2geojson(data, filter_used_refs=False, log_level="ERROR")
    feats = Features()
    for feature in geojson["features"]:
        tags = feature["properties"].get("tags") or {}
        if not tags:
            continue
        geom = shape(feature["geometry"])
        kind = geom.geom_type
        if "building" in tags and kind in ("Polygon", "MultiPolygon"):
            feats.buildings.append(Building(geom, parse_height(tags, spec.default_building_height_m)))
        elif tags.get("highway") in ROAD_CLASSES and kind in ("LineString", "MultiLineString"):
            feats.roads.append(Road(geom, tags["highway"]))
        elif _is_water(tags) and kind in ("Polygon", "MultiPolygon"):
            feats.water.append(Water(geom))
    return feats


def _cache_path(cache_dir: Path, query: str) -> Path:
    return cache_dir / (hashlib.sha256(query.encode()).hexdigest() + ".json")


def _read_cache(path: Path) -> dict | None:
    """Cached response, or None when there is no usable entry.

    A crash or a full disk mid-write can leave a truncated file; treating that as a miss keeps
    one bad write from poisoning a query forever.
    """
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return None


def _write_cache(path: Path, data: dict) -> None:
    """Write atomically: a reader either sees the previous entry or the complete new one."""
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def fetch_overpass(
    query: str,
    cache_dir: Path,
    url: str = DEFAULT_OVERPASS_URL,
    client: httpx.Client | None = None,
    retries: int = 3,
    backoff_s: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, query)
    cached = _read_cache(path)
    if cached is not None:
        return cached

    owns_client = client is None
    client = client or httpx.Client(timeout=120)
    last_error = "no attempt made"
    try:
        for attempt in range(retries):
            try:
                response = client.post(url, data={"data": query}, headers={"User-Agent": USER_AGENT})
            except httpx.TransportError as exc:
                last_error = f"network error: {exc}"
            else:
                if response.status_code == 200:
                    try:
                        data = response.json()
                    except ValueError:
                        # An overloaded instance can answer 200 with an HTML error page. Like the
                        # remark below that is transient, so it is retried and never cached.
                        data = None
                    if data is None:
                        last_error = "Overpass returned a non-JSON body"
                    else:
                        # Overpass reports query timeouts and out-of-memory as HTTP 200 with a
                        # "remark" and an empty element list. Caching that would make a transient
                        # failure permanent, so treat it exactly like a retryable server error.
                        remark = data.get("remark", "")
                        if "runtime error" in remark.lower():
                            last_error = f"Overpass remark: {remark}"
                        else:
                            _write_cache(path, data)
                            return data
                else:
                    last_error = f"HTTP {response.status_code}"
                    if response.status_code not in RETRY_STATUSES:
                        break
            if attempt < retries - 1:
                sleep(backoff_s * 2**attempt)
    finally:
        if owns_client:
            client.close()
    raise FetchError(f"Overpass request failed ({last_error}). Please try again in a minute.")


def overpass_url() -> str:
    """Endpoint override via SKYLINE_OVERPASS_URL (e.g. a self-hosted Overpass instance)."""
    return os.environ.get("SKYLINE_OVERPASS_URL", DEFAULT_OVERPASS_URL)


def fetch_features(
    spec: FrameSpec,
    cache_dir: Path,
    url: str | None = None,
    client: httpx.Client | None = None,
) -> Features:
    query = build_query(query_bbox(spec), spec.mode)
    return parse_overpass(fetch_overpass(query, cache_dir, url=url or overpass_url(), client=client), spec)
