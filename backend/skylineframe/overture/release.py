"""Current Overture Maps release id, resolved from the STAC catalog and cached with a daily refresh.

Overture ships a new release monthly and keeps only the last two reachable (older ones 404,
GDPR-driven), so there is no stable "latest" alias to hardcode: the id is resolved at runtime and
re-resolved once the cache entry is older than max_age_s, which is what lets a pinned release that
has since rotated out heal itself within one refresh interval instead of 404ing forever.
"""

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

import httpx

from ..fetch import USER_AGENT

log = logging.getLogger(__name__)

STAC_CATALOG_URL = "https://stac.overturemaps.org/catalog.json"
CACHE_FILENAME = "release.json"
TIMEOUT_S = 30.0
# yyyy-mm-dd.patch, e.g. "2026-09-23.0" — also guards against a compromised or malformed catalog
# response handing a string straight into an S3 path.
RELEASE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\.\d+")


def _cache_path(cache_dir: Path) -> Path:
    return cache_dir / CACHE_FILENAME


def _cached(path: Path, max_age_s: float) -> str | None:
    """The cached release, or None when there is no entry or it is older than max_age_s."""
    try:
        entry = json.loads(path.read_text())
    except (ValueError, OSError):
        return None
    release, resolved_at = entry.get("release"), entry.get("resolved_at")
    if not isinstance(release, str) or not isinstance(resolved_at, (int, float)):
        return None
    if time.time() - resolved_at > max_age_s:
        return None
    return release


def _write_cache(path: Path, release: str) -> None:
    """Write atomically, like the Overpass and LoD2 caches: a reader never sees a half-written entry."""
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps({"release": release, "resolved_at": time.time()}))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _latest(data: dict) -> str:
    """The current release id out of the STAC root catalog.

    It is carried twice — a top-level "latest" string and a "latest": true flag on the matching
    child link — so a missing or malformed top-level field still resolves from the links.
    """
    latest = data.get("latest")
    if isinstance(latest, str) and RELEASE_RE.fullmatch(latest):
        return latest
    for link in data.get("links", []):
        if link.get("rel") != "child" or link.get("latest") is not True:
            continue
        segments = str(link.get("href", "")).rstrip("/").split("/")
        candidate = segments[-2] if len(segments) >= 2 else ""
        if RELEASE_RE.fullmatch(candidate):
            return candidate
    raise ValueError("STAC catalog has no resolvable latest release")


def current_release(cache_dir: Path, client: httpx.Client | None = None, max_age_s: float = 86400) -> str:
    """The current Overture release id, e.g. "2026-09-23.0".

    Cached under cache_dir so a run touching many bboxes resolves it once rather than once per
    fetch. Raises on failure (network, HTTP status, an unreadable or empty catalog) — the caller
    is the one with an OpenStreetMap fallback to degrade to, not this function.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir)
    cached = _cached(path, max_age_s)
    if cached is not None:
        return cached
    owns_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT_S)
    try:
        response = client.get(STAC_CATALOG_URL, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        release = _latest(response.json())
    finally:
        if owns_client:
            client.close()
    _write_cache(path, release)
    return release
