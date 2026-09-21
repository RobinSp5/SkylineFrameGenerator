import httpx
import pytest
from fastapi.testclient import TestClient

from app import geocode as geocode_module
from app.geocode import USER_AGENT, GeocodeError, Geocoder
from app.jobs import JobStore
from app.main import create_app
from tests.test_api import TEST_HOSTS
from tests.test_jobs import fake_runner

NOMINATIM_BODY = [
    {"display_name": "Frankfurt am Main, Hessen, Deutschland", "lat": "50.1106444", "lon": "8.6820917"},
    {"display_name": "Frankfurt (Oder), Brandenburg, Deutschland", "lat": "52.3412", "lon": "14.5498"},
]


def make_geocoder(calls: list[httpx.Request], **kw) -> Geocoder:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=NOMINATIM_BODY)

    return Geocoder(client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def make_replying_geocoder(*responses: httpx.Response, **kw) -> Geocoder:
    """A geocoder answering with the given responses in order (the last one repeats)."""
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return Geocoder(client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def endpoint_client(tmp_path, geocoder: Geocoder) -> TestClient:
    store = JobStore(tmp_path / "jobs", tmp_path / "cache", runner=fake_runner)
    return TestClient(create_app(store=store, geocoder=geocoder, allowed_hosts=TEST_HOSTS))


def test_search_parses_results():
    calls: list[httpx.Request] = []
    results = make_geocoder(calls).search("Frankfurt")
    assert [r.name for r in results][0].startswith("Frankfurt am Main")
    assert results[0].lat == 50.1106444 and results[0].lon == 8.6820917
    assert calls[0].headers["user-agent"] == USER_AGENT
    # The normalised query is what goes upstream, so equivalent spellings share a cache entry.
    assert calls[0].url.params["q"] == "frankfurt" and calls[0].url.params["format"] == "jsonv2"


def test_search_is_cached():
    calls: list[httpx.Request] = []
    g = make_geocoder(calls)
    g.search("Frankfurt")
    g.search("frankfurt ")
    g.search("  FRANKFURT\t")
    assert len(calls) == 1


def test_search_rate_limits():
    calls: list[httpx.Request] = []
    clock = {"t": 100.0}
    sleeps: list[float] = []
    g = make_geocoder(calls, now=lambda: clock["t"], sleep=sleeps.append)
    g.search("Berlin")
    clock["t"] += 0.3
    g.search("Hamburg")
    assert len(sleeps) == 1 and sleeps[0] == pytest.approx(0.7)


def test_search_backs_off_after_429():
    clock = {"t": 100.0}
    sleeps: list[float] = []
    g = make_replying_geocoder(
        httpx.Response(429, text="slow down"),
        httpx.Response(200, json=NOMINATIM_BODY),
        now=lambda: clock["t"],
        sleep=sleeps.append,
    )
    with pytest.raises(GeocodeError):
        g.search("Berlin")
    g.search("Hamburg")
    assert sleeps[-1] == pytest.approx(61.0, abs=0.01)


def test_search_rejects_a_busy_lock_instead_of_blocking():
    g = make_geocoder([], min_interval_s=0.01)
    assert g._lock.acquire()
    try:
        with pytest.raises(GeocodeError):
            g.search("Frankfurt")
    finally:
        g._lock.release()


def test_search_evicts_the_oldest_cache_entry(monkeypatch):
    monkeypatch.setattr(geocode_module, "MAX_CACHE_ENTRIES", 2)
    g = make_geocoder([])
    g.search("Berlin")
    g.search("Hamburg")
    g.search("Bremen")
    assert len(g._cache) == 2
    assert ("berlin", 5) not in g._cache
    assert ("hamburg", 5) in g._cache and ("bremen", 5) in g._cache


def test_search_skips_unusable_rows():
    g = make_replying_geocoder(
        httpx.Response(
            200,
            json=[
                {"display_name": "no coordinates here"},
                {"display_name": "Not A Number", "lat": "nan", "lon": "8.68"},
                {"display_name": "Off The Globe", "lat": "91.0", "lon": "8.68"},
                "a bare string",
                {"display_name": "Frankfurt am Main", "lat": "50.11", "lon": "8.68"},
            ],
        )
    )
    results = g.search("Frankfurt")
    assert [r.name for r in results] == ["Frankfurt am Main"]
    assert results[0].lat == 50.11


def test_geocode_endpoint(tmp_path):
    client = endpoint_client(tmp_path, make_geocoder([]))
    r = client.get("/api/geocode", params={"q": "Frankfurt"})
    assert r.status_code == 200
    assert r.json()[0] == {
        "name": "Frankfurt am Main, Hessen, Deutschland",
        "lat": 50.1106444,
        "lon": 8.6820917,
    }
    assert client.get("/api/geocode", params={"q": "F"}).status_code == 422


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="upstream is unhappy"),
        httpx.Response(200, text="<html>not json at all</html>"),
        httpx.Response(200, json={"error": "x"}),
        httpx.Response(429, text="slow down"),
    ],
    ids=["server-error", "non-json-body", "wrong-shape", "rate-limited"],
)
def test_geocode_endpoint_reports_upstream_failure_as_502(tmp_path, response):
    client = endpoint_client(tmp_path, make_replying_geocoder(response))
    r = client.get("/api/geocode", params={"q": "Frankfurt"})
    assert r.status_code == 502
    assert r.json()["detail"] == "Place search is temporarily unavailable."
