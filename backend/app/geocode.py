"""Nominatim place search and reverse lookup with in-memory cache and polite rate limiting."""

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from skylineframe.naming import LABEL_SEPARATOR

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
# Zoom 14 is Nominatim's "suburb" level: the right grain for a square of one or two kilometres.
REVERSE_ZOOM = 14
REVERSE_TIMEOUT_S = 3.0
REVERSE_CACHE_DECIMALS = 3  # ~100 m
# Most useful first. Suburb is the name people use ("Altstadt"); city_district is often an
# administrative unit ("Innenstadt 1") and only stands in when there is no suburb.
DISTRICT_KEYS = ("suburb", "city_district", "quarter", "neighbourhood")
PLACE_KEYS = ("city", "town", "village", "municipality")
USER_AGENT = "SkylineFrameGenerator/0.1 (local dev tool)"
# The cache is a convenience, not a store: bounded so a long-running server cannot grow it
# without limit by being asked for ever new places.
MAX_CACHE_ENTRIES = 512
MAX_NAME_LEN = 300
MAX_LIMIT = 20
# Nominatim asks clients that are told to slow down to back off properly, not to retry at once.
BACKOFF_S = 60.0
BACKOFF_STATUS = (429, 503)


class GeocodeError(RuntimeError):
    """The upstream service answered, but not with something we can use."""


@dataclass
class GeocodeResult:
    name: str
    lat: float
    lon: float


class Geocoder:
    def __init__(
        self,
        client: httpx.Client | None = None,
        ttl_s: float = 600,
        min_interval_s: float = 1.0,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client or httpx.Client(timeout=10, headers={"User-Agent": USER_AGENT})
        self._ttl_s = ttl_s
        self._min_interval_s = min_interval_s
        self._now = now
        self._sleep = sleep
        self._cache: dict[tuple[str, int], tuple[float, list[GeocodeResult]]] = {}
        self._reverse_cache: dict[tuple[float, float], tuple[float, str | None]] = {}
        self._last_request: float | None = None
        self._lock = threading.Lock()  # FastAPI runs sync handlers in a thread pool

    def search(self, q: str, limit: int = 5) -> list[GeocodeResult]:
        # One request at a time, so the cache and the rate-limit clock stay consistent. The
        # wait is bounded: a caller queueing behind a slow request is told to retry instead of
        # occupying a thread-pool worker until the pool is exhausted.
        if not self._lock.acquire(timeout=self._min_interval_s + 1):
            raise GeocodeError("Place search is busy; try again.")
        try:
            return self._search(q, limit)
        finally:
            self._lock.release()

    def _search(self, q: str, limit: int) -> list[GeocodeResult]:
        limit = max(1, min(int(limit), MAX_LIMIT))
        normalised = " ".join(q.split()).casefold()
        key = (normalised, limit)
        cached = self._cache.get(key)
        if cached and self._now() - cached[0] < self._ttl_s:
            return cached[1]

        response = self._get(NOMINATIM_URL, {"q": normalised, "format": "jsonv2", "limit": limit})
        results = _parse(response, limit)
        _remember(self._cache, key, (self._now(), results))
        return results

    def reverse(self, lat: float, lon: float) -> str | None:
        """Place label for a point ("Frankfurt am Main – Altstadt"), or None when there is none.

        Raises like search() when Nominatim cannot be reached or asks us to slow down; the caller
        decides what a missing name means. The request is time-boxed to REVERSE_TIMEOUT_S.
        """
        if not self._lock.acquire(timeout=self._min_interval_s + 1):
            raise GeocodeError("Place search is busy; try again.")
        try:
            return self._reverse(lat, lon)
        finally:
            self._lock.release()

    def _reverse(self, lat: float, lon: float) -> str | None:
        # Rounded to ~100 m: regenerating the same square with other settings, or nudging it a
        # little, is the common case and must not cost another upstream request.
        key = (round(lat, REVERSE_CACHE_DECIMALS), round(lon, REVERSE_CACHE_DECIMALS))
        cached = self._reverse_cache.get(key)
        if cached and self._now() - cached[0] < self._ttl_s:
            return cached[1]

        response = self._get(
            NOMINATIM_REVERSE_URL,
            {"lat": lat, "lon": lon, "format": "jsonv2", "zoom": REVERSE_ZOOM, "accept-language": "de"},
            timeout=REVERSE_TIMEOUT_S,
        )
        try:
            payload = response.json()
        except ValueError:
            raise GeocodeError("non-JSON upstream body") from None
        address = payload.get("address") if isinstance(payload, dict) else None
        label = place_label(address) if isinstance(address, dict) else None
        _remember(self._reverse_cache, key, (self._now(), label))
        return label

    def _get(self, url: str, params: dict, timeout: float | None = None) -> httpx.Response:
        """One upstream request under the shared politeness rules. Call with the lock held."""
        if self._last_request is not None:
            wait = self._min_interval_s - (self._now() - self._last_request)
            if wait > self._min_interval_s:
                # Only a backoff window pushes the wait beyond the polite interval. Sitting it
                # out would hold a thread-pool worker for a minute, so the caller is told instead.
                raise GeocodeError(f"Place search is rate limited; try again in {int(wait)} s.")
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._now()

        extra = {"timeout": timeout} if timeout is not None else {}
        response = self._client.get(url, params=params, headers={"User-Agent": USER_AGENT}, **extra)
        if response.status_code in BACKOFF_STATUS:
            # Being told to slow down costs us the next minute, not just the next second.
            self._last_request = self._now() + BACKOFF_S
            raise GeocodeError("Place search is rate limited upstream.")
        response.raise_for_status()
        return response


def _remember(cache: dict, key, value) -> None:
    cache[key] = value
    while len(cache) > MAX_CACHE_ENTRIES:
        cache.pop(next(iter(cache)))  # dicts keep insertion order: oldest first


def _address_part(address: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = address.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return None


def place_label(address: dict) -> str | None:
    """"City – district" from a Nominatim address, the city alone when the district repeats it.

    None when the address names neither: a state or a country is too coarse to name a model by.
    """
    district = _address_part(address, DISTRICT_KEYS)
    place = _address_part(address, PLACE_KEYS)
    if place and district and district.casefold() != place.casefold():
        return f"{place}{LABEL_SEPARATOR}{district}"
    return place or district


def _parse(response: httpx.Response, limit: int) -> list[GeocodeResult]:
    """Turn an upstream 200 into results, treating anything unexpected as an upstream fault."""
    try:
        payload = response.json()
    except ValueError:
        raise GeocodeError("non-JSON upstream body") from None
    if not isinstance(payload, list):
        raise GeocodeError("unexpected upstream shape")

    results = []
    for item in payload[:limit]:
        try:
            name = str(item["display_name"])[:MAX_NAME_LEN]
            lat = float(item["lat"])
            lon = float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue  # one unusable row does not spoil the rest of the list
        if not (math.isfinite(lat) and math.isfinite(lon)):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        results.append(GeocodeResult(name=name, lat=lat, lon=lon))
    return results
