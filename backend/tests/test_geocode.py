import httpx
import pytest
from fastapi.testclient import TestClient

from app.geocode import USER_AGENT, Geocoder
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


def make_failing_geocoder(**kw) -> Geocoder:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream is unhappy")

    return Geocoder(client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def test_search_parses_results():
    calls: list[httpx.Request] = []
    results = make_geocoder(calls).search("Frankfurt")
    assert [r.name for r in results][0].startswith("Frankfurt am Main")
    assert results[0].lat == 50.1106444 and results[0].lon == 8.6820917
    assert calls[0].headers["user-agent"] == USER_AGENT
    assert calls[0].url.params["q"] == "Frankfurt" and calls[0].url.params["format"] == "jsonv2"


def test_search_is_cached():
    calls: list[httpx.Request] = []
    g = make_geocoder(calls)
    g.search("Frankfurt")
    g.search("frankfurt ")
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


def test_geocode_endpoint(tmp_path):
    store = JobStore(tmp_path / "jobs", tmp_path / "cache", runner=fake_runner)
    client = TestClient(create_app(store=store, geocoder=make_geocoder([]), allowed_hosts=TEST_HOSTS))
    r = client.get("/api/geocode", params={"q": "Frankfurt"})
    assert r.status_code == 200
    assert r.json()[0] == {
        "name": "Frankfurt am Main, Hessen, Deutschland",
        "lat": 50.1106444,
        "lon": 8.6820917,
    }
    assert client.get("/api/geocode", params={"q": "F"}).status_code == 422


def test_geocode_endpoint_reports_upstream_failure_as_502(tmp_path):
    store = JobStore(tmp_path / "jobs", tmp_path / "cache", runner=fake_runner)
    client = TestClient(create_app(store=store, geocoder=make_failing_geocoder(), allowed_hosts=TEST_HOSTS))
    r = client.get("/api/geocode", params={"q": "Frankfurt"})
    assert r.status_code == 502
    assert r.json()["detail"] == "Place search is temporarily unavailable."
