"""Overpass API access: query building, disk cache with retries, and parsing into Features."""

import hashlib
import json
import logging
import math
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx
import osm2geojson
from shapely.geometry import shape

from .errors import FetchError
from .features import Building, Features, Lod2Building, Road, RoofSpec, Water
from .lod2.provider import select_provider
from .project import query_bbox
from .spec import LEVEL_HEIGHT_M, ROAD_CLASSES, FrameSpec, Mode
from .trees.defaults import TREE_CROWN_M
from .trees.model import OsmTree

log = logging.getLogger(__name__)

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
# The LoD2 cache lives beside the Overpass cache in its own directory, keyed per bounding box.
# One 1500 m Frankfurt square is about 145 MB in there.
LOD2_CACHE_DIRNAME = "lod2"
WATER_SELECTORS = (
    '["natural"="water"]',
    '["waterway"="riverbank"]',
    '["landuse"="reservoir"]',
    '["natural"="bay"]',
)

MAX_ROOF_HEIGHT_M = 100.0
# The tallest trees on earth are about 116 m tall with crowns of about 60 m; beyond is a typo.
MAX_TREE_HEIGHT_M = 150.0
MAX_CROWN_M = 60.0
M_PER_DEG_LAT = 111_320.0

# roof:shape values we can build (spec §7) plus the common synonyms; everything else is flat.
ROOF_SHAPE_MAP: dict[str, str] = {
    "gabled": "gabled",
    "pitched": "gabled",
    "hipped": "hipped",
    "half-hipped": "half_hipped",
    "half_hipped": "half_hipped",
    "pyramidal": "pyramidal",
    "cone": "pyramidal",
    "skillion": "skillion",
    "mansard": "mansard",
    "gambrel": "gambrel",
    "dome": "dome",
    "onion": "dome",
    "round": "round",
}

COMPASS_DEG: dict[str, float] = {
    "N": 0.0, "NNE": 22.5, "NE": 45.0, "ENE": 67.5,
    "E": 90.0, "ESE": 112.5, "SE": 135.0, "SSE": 157.5,
    "S": 180.0, "SSW": 202.5, "SW": 225.0, "WSW": 247.5,
    "W": 270.0, "WNW": 292.5, "NW": 315.0, "NNW": 337.5,
}


def build_query(bbox: tuple[float, float, float, float], mode: Mode, trees: bool = False) -> str:
    """The Overpass query for one square. trees=False is, character for character, the query of
    before phase 6, so its cache entries stay valid."""
    south, west, north, east = bbox
    bb = f"({south:.6f},{west:.6f},{north:.6f},{east:.6f})"
    parts = [
        f'way["building"]{bb};',
        f'relation["building"]["type"="multipolygon"]{bb};',
        # Parts are the setbacks and rooftop boxes of a tagged outline (spec §4). They are their
        # own elements, so without this pair the MVP query never saw a single one of them.
        f'way["building:part"]{bb};',
        f'relation["building:part"]["type"="multipolygon"]{bb};',
    ]
    if mode == Mode.full:
        classes = "|".join(ROAD_CLASSES)
        parts.append(f'way["highway"~"^({classes})$"]{bb};')
        for selector in WATER_SELECTORS:
            parts.append(f"way{selector}{bb};")
            parts.append(f"relation{selector}{bb};")
    if trees:
        parts.append(f'node["natural"="tree"]{bb};')
        parts.append(f'way["natural"="tree_row"]{bb};')
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


def _metres(raw: str | None, limit: float) -> float | None:
    """A non-negative length in metres from an OSM value, or None when it is unusable."""
    if not raw:
        return None
    try:
        value = float(raw.strip().removesuffix("m").strip())
    except ValueError:
        return None
    return value if 0 <= value <= limit else None


def _levels(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        count = float(raw)
    except ValueError:
        return None
    return count if 0 <= count <= MAX_LEVELS else None


def parse_min_height(tags: dict) -> float:
    """Bottom of a building part: min_height, else building:min_level x 3.2, else 0 (spec §4)."""
    value = _metres(tags.get("min_height"), MAX_HEIGHT_M)
    if value is not None:
        return value
    levels = _levels(tags.get("building:min_level"))
    return levels * LEVEL_HEIGHT_M if levels is not None else 0.0


def parse_direction(raw: str | None) -> float | None:
    """roof:direction as a compass bearing in degrees (0 = north, clockwise), or None."""
    if not raw:
        return None
    text = raw.strip().upper()
    if text in COMPASS_DEG:
        return COMPASS_DEG[text]
    try:
        value = float(text)
    except ValueError:
        return None
    return value % 360


def parse_roof(tags: dict) -> RoofSpec | None:
    """The roof of one element, or None for flat / unsupported / untagged shapes (spec §7).

    height_m stays 0.0 when neither roof:height nor roof:levels is tagged; prepare then derives
    it from the footprint, because the rule needs the short side in local metres.
    """
    shape = ROOF_SHAPE_MAP.get((tags.get("roof:shape") or "").strip().lower())
    if shape is None:
        return None
    height = _metres(tags.get("roof:height"), MAX_ROOF_HEIGHT_M)
    if height is None:
        levels = _levels(tags.get("roof:levels"))
        height = levels * LEVEL_HEIGHT_M if levels else None
    return RoofSpec(shape=shape, height_m=height or 0.0, direction_deg=parse_direction(tags.get("roof:direction")))


def osm_id(properties: dict) -> str:
    """Stable id of one element: way and relation ids are separate number spaces (spec §3)."""
    return f"{properties.get('type', 'way')}/{properties.get('id', 0)}"


def _building_tags(tags: dict) -> tuple[str, bool] | None:
    """(kind, is_part) for a building or a building part, or None when the element is neither.

    building=roof is a carport or a canopy with no walls, and every =no is an explicit
    "there is nothing here"; both are dropped (spec §4).
    """
    value = tags.get("building")
    if value and value not in ("no", "roof"):
        return value, False
    part = tags.get("building:part")
    if part and part != "no":
        return part, True
    return None


def _is_water(tags: dict) -> bool:
    return (
        tags.get("natural") in ("water", "bay")
        or tags.get("waterway") == "riverbank"
        or tags.get("landuse") == "reservoir"
    )


def _positive_metres(raw: str | None, limit: float) -> float | None:
    """Like _metres, but 0 is a typo too: a tree without height or crown is no tree."""
    value = _metres(raw, limit)
    return value or None


def _tree_row(coords: list[tuple[float, float]], height: float | None, crown: float | None) -> list[OsmTree]:
    """Points along a natural=tree_row way, one per crown diameter (spec 6 §4.3).

    The row is cut into n equal pieces, n the length over the crown rounded, and a tree stands in
    the middle of each: evenly spaced, and never on an end node, which the next row may share.
    Lengths are equirectangular metres at the row's latitude, plenty for a few hundred metres.
    """
    cos_lat = math.cos(math.radians(sum(lat for _lon, lat in coords) / len(coords)))
    seg = [
        math.hypot((b[0] - a[0]) * M_PER_DEG_LAT * cos_lat, (b[1] - a[1]) * M_PER_DEG_LAT)
        for a, b in zip(coords, coords[1:])
    ]
    length = sum(seg)
    n = max(1, round(length / (crown or TREE_CROWN_M)))
    trees: list[OsmTree] = []
    i, start = 0, 0.0  # current segment and the distance at which it starts
    for k in range(n):
        at = (k + 0.5) * length / n
        while i < len(seg) - 1 and start + seg[i] < at:
            start += seg[i]
            i += 1
        if not seg:
            lon, lat = coords[0]
        else:
            t = (at - start) / seg[i] if seg[i] > 0 else 0.0
            (lon0, lat0), (lon1, lat1) = coords[i], coords[i + 1]
            lon, lat = lon0 + t * (lon1 - lon0), lat0 + t * (lat1 - lat0)
        trees.append(OsmTree(lon, lat, height, crown))
    return trees


def parse_trees(data: dict) -> list[OsmTree]:
    """natural=tree nodes and sampled natural=tree_row ways, in lon/lat, straight off the raw
    elements: osm2geojson has nothing to add to a point, and a row needs only its node list."""
    elements = data.get("elements", [])
    nodes = {e["id"]: (e["lon"], e["lat"]) for e in elements if e.get("type") == "node" and "lon" in e}
    trees: list[OsmTree] = []
    for e in elements:
        tags = e.get("tags") or {}
        natural = tags.get("natural")
        if natural not in ("tree", "tree_row"):
            continue
        height = _positive_metres(tags.get("height"), MAX_TREE_HEIGHT_M)
        crown = _positive_metres(tags.get("diameter_crown"), MAX_CROWN_M)
        if natural == "tree" and e.get("type") == "node" and "lon" in e:
            trees.append(OsmTree(e["lon"], e["lat"], height, crown))
        elif natural == "tree_row" and e.get("type") == "way":
            refs = e.get("nodes") or []
            if refs and all(ref in nodes for ref in refs):
                trees.extend(_tree_row([nodes[ref] for ref in refs], height, crown))
    return trees


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
        classified = _building_tags(tags)
        if classified is not None and kind in ("Polygon", "MultiPolygon"):
            building_kind, is_part = classified
            tagged_height = _metres(tags.get("height"), MAX_HEIGHT_M)
            feats.buildings.append(
                Building(
                    geom=geom,
                    # 0.0 means "no height information at all"; prepare fills it with the
                    # estimate (buildings) or the outline height (parts), spec §5/§6.
                    height_m=parse_height(tags, 0.0),
                    height_is_top=bool(tagged_height),
                    min_height_m=parse_min_height(tags) if is_part else 0.0,
                    roof=parse_roof(tags),
                    osm_id=osm_id(feature["properties"]),
                    is_part=is_part,
                    kind=building_kind,
                    roof_tagged=bool((tags.get("roof:shape") or "").strip()),
                )
            )
        elif tags.get("highway") in ROAD_CLASSES and kind in ("LineString", "MultiLineString"):
            feats.roads.append(Road(geom, tags["highway"]))
        elif _is_water(tags) and kind in ("Polygon", "MultiPolygon"):
            feats.water.append(Water(geom))
    feats.trees = parse_trees(data)
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
    # uuid4, not the pid: the API runs the pipeline on a thread pool inside one process, so two
    # jobs for the same square would otherwise stage under the same name and truncate each other.
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(data))
        os.replace(tmp, path)
    finally:
        # A unique name means a failed write leaves a fresh orphan every time instead of one file
        # per path that the next attempt overwrote, so the cleanup that the LoD2 download already
        # does is no longer optional. After os.replace there is nothing left to unlink.
        tmp.unlink(missing_ok=True)


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


def fetch_lod2(
    spec: FrameSpec,
    cache_dir: Path,
    client: httpx.Client | None = None,
) -> tuple[list[Lod2Building], str]:
    """The official LoD2 models for this square plus the provider name, or ([], "").

    A missing provider, a switched-off flag and an unreachable service are all the same thing
    here: the run continues on OpenStreetMap alone and says so through the empty source name
    (spec §9). Nothing in this function may raise.

    The bare `except` is deliberate and is what makes that promise true. A provider handles its
    own network errors, but creating the cache directory and streaming 152 MB into it are plain
    filesystem work: a full volume, a quota or a read-only cache raises OSError right past every
    handler the provider has, and a missing height model must never cost the run the model.
    """
    if not spec.lod2:
        return [], ""
    bbox = query_bbox(spec)
    provider = select_provider(bbox)
    if provider is None:
        return [], ""
    try:
        buildings = provider.fetch(bbox, cache_dir / LOD2_CACHE_DIRNAME, client=client)
    except Exception as exc:  # noqa: BLE001 - degrading to OSM is always better than failing
        log.warning("LoD2 %s: %s; continuing with OpenStreetMap", provider.name, exc)
        return [], ""
    return (buildings, provider.name) if buildings else ([], "")


def fetch_features(
    spec: FrameSpec,
    cache_dir: Path,
    url: str | None = None,
    client: httpx.Client | None = None,
    lod2_client: httpx.Client | None = None,
) -> Features:
    # FrameSpec.trees lands with the geometry half of phase 6; until then trees are always asked for.
    query = build_query(query_bbox(spec), spec.mode, trees=getattr(spec, "trees", True))
    feats = parse_overpass(
        fetch_overpass(query, cache_dir, url=url or overpass_url(), client=client), spec
    )
    # LoD2 is fetched raw here and turned into geometry in prepare: only the buildings that end
    # up printed individually are ever solidified (spec §5).
    feats.lod2, feats.lod2_source = fetch_lod2(spec, cache_dir, client=lod2_client)
    return feats
