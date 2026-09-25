"""Overture Maps Buildings fetch: DuckDB against the public S3 release, one bbox at a time.

Never raises: a network hiccup, a DuckDB error or a malformed release all degrade to an empty
list with a warning, exactly like the LoD2 providers (spec §9) — Overture is meant to enrich a
run, never to be a reason one fails.
"""

import hashlib
import json
import logging
import os
import uuid
from pathlib import Path

import duckdb
import httpx

from ..features import OvertureBuilding
from .parse import parse_buildings
from .release import current_release

log = logging.getLogger(__name__)

Bbox = tuple[float, float, float, float]  # (south, west, north, east), as project.query_bbox gives it

BUCKET = "overturemaps-us-west-2"
S3_REGION = "us-west-2"
CACHE_DIRNAME = "overture"
# theme=buildings/type=building is one row per building already, unlike an Overpass answer (which
# multiplies ways, relations and their member nodes), so this sits at the same order of magnitude
# as MAX_ELEMENTS in fetch.py without ever being within reach of a legitimate print square.
MAX_BUILDINGS = 250_000
SELECT_COLUMNS = (
    "id, height, num_floors, roof_shape, roof_height, roof_direction, roof_color, class, "
    "ST_AsWKB(geometry) AS geom_wkb"
)


def _cache_key(release: str, bbox: Bbox) -> str:
    south, west, north, east = bbox
    return f"{release}|{south:.6f}|{west:.6f}|{north:.6f}|{east:.6f}"


def _cache_path(cache_dir: Path, release: str, bbox: Bbox) -> Path:
    return cache_dir / (hashlib.sha256(_cache_key(release, bbox).encode()).hexdigest() + ".json")


def _read_cache(path: Path) -> list[dict] | None:
    """The cached rows, or None when there is no usable entry (mirrors fetch.py's Overpass cache)."""
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return None


def _write_cache(path: Path, rows: list[dict]) -> None:
    """Write atomically: a reader either sees the previous entry or the complete new one."""
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(rows))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _row_to_record(row: tuple) -> dict:
    id_, height, num_floors, roof_shape, roof_height, roof_direction, roof_color, cls, geom_wkb = row
    return {
        "id": id_,
        "height": height,
        "num_floors": num_floors,
        "roof_shape": roof_shape,
        "roof_height": roof_height,
        "roof_direction": roof_direction,
        "roof_color": roof_color,
        "class": cls,
        # bytes -> hex so the row survives the JSON cache round trip; parse.py reverses it.
        "geom_wkb_hex": bytes(geom_wkb).hex(),
    }


def _run_query(release: str, bbox: Bbox) -> list[dict]:
    """The raw building rows for `bbox` at `release`, straight off DuckDB against S3.

    theme=buildings/type=building is Hive-partitioned by theme and type only, not by geography,
    but every row group carries bbox statistics, so this prunes on read instead of scanning the
    whole theme — Overture's own documented access pattern, not a workaround. The spatial
    extension has no bundled copy and must be installed before it can load; httpfs's is bundled,
    but is installed the same way regardless, so a missing cache never breaks the query.
    """
    south, west, north, east = bbox
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(f"SET s3_region='{S3_REGION}';")
    path = f"s3://{BUCKET}/release/{release}/theme=buildings/type=building/*"
    query = (
        f"SELECT {SELECT_COLUMNS} FROM read_parquet(?, filename=true, hive_partitioning=1) "
        "WHERE bbox.xmin BETWEEN ? AND ? AND bbox.ymin BETWEEN ? AND ? LIMIT ?"
    )
    rows = con.execute(query, [path, west, east, south, north, MAX_BUILDINGS + 1]).fetchall()
    return [_row_to_record(r) for r in rows]


def fetch(bbox: Bbox, cache_dir: Path, client: httpx.Client | None = None) -> list[OvertureBuilding]:
    """Overture buildings covering `bbox`, or [] (with a warning) on any failure.

    cache_dir is the project's whole cache directory; the release lookup and the per-bbox query
    result both live under cache_dir/overture, content-addressed like the Overpass and LoD2
    caches, so a repeated square costs one query instead of one per run.
    """
    overture_dir = cache_dir / CACHE_DIRNAME
    try:
        overture_dir.mkdir(parents=True, exist_ok=True)
        release = current_release(overture_dir, client=client)
        path = _cache_path(overture_dir, release, bbox)
        rows = _read_cache(path)
        if rows is None:
            rows = _run_query(release, bbox)
            if len(rows) > MAX_BUILDINGS:
                # Not cached: like an oversized Hessen response, this is retried rather than
                # pinned as a permanent empty answer, in case a tighter square is queried next.
                log.warning(
                    "Overture: bbox %s has over %d buildings; continuing with OpenStreetMap", bbox, MAX_BUILDINGS
                )
                return []
            buildings = parse_buildings(rows, release)  # raises before anything is cached, not after
            _write_cache(path, rows)
            return buildings
        return parse_buildings(rows, release)
    except Exception as exc:  # noqa: BLE001 - degrading to OSM is always better than failing (spec §9)
        log.warning("Overture: %s; continuing with OpenStreetMap", exc)
        return []
