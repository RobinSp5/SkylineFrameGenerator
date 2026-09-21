"""Nominatim place search with in-memory cache and polite rate limiting."""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "SkylineFrameGenerator/0.1 (local dev tool)"


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
        self._cache: dict[str, tuple[float, list[GeocodeResult]]] = {}
        self._last_request: float | None = None
        self._lock = threading.Lock()  # FastAPI runs sync handlers in a thread pool

    def search(self, q: str, limit: int = 5) -> list[GeocodeResult]:
        with self._lock:
            return self._search(q, limit)

    def _search(self, q: str, limit: int) -> list[GeocodeResult]:
        key = q.strip().lower()
        cached = self._cache.get(key)
        if cached and self._now() - cached[0] < self._ttl_s:
            return cached[1]

        if self._last_request is not None:
            wait = self._min_interval_s - (self._now() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._now()

        response = self._client.get(
            NOMINATIM_URL,
            params={"q": q.strip(), "format": "jsonv2", "limit": limit},
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        results = [
            GeocodeResult(name=item["display_name"], lat=float(item["lat"]), lon=float(item["lon"]))
            for item in response.json()
        ]
        self._cache[key] = (self._now(), results)
        return results
