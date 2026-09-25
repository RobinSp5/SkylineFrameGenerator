import time

import httpx
import pytest

from skylineframe.overture.release import CACHE_FILENAME, STAC_CATALOG_URL, current_release

# A trimmed but real shape of https://stac.overturemaps.org/catalog.json: the "registry" block
# (a manifest of thousands of parquet parts) is real too but nothing here reads it, so it is left
# out rather than carried as dead weight in a fixture.
CATALOG = {
    "type": "Catalog",
    "id": "Overture Releases",
    "stac_version": "1.1.0",
    "links": [
        {"rel": "root", "href": STAC_CATALOG_URL, "type": "application/json", "title": "Overture Releases"},
        {
            "rel": "child",
            "href": "https://stac.overturemaps.org/2026-08-19.0/catalog.json",
            "type": "application/json",
            "title": "2026-08-19.0 Overture Release",
        },
        {"rel": "self", "href": STAC_CATALOG_URL, "type": "application/json"},
        {
            "rel": "child",
            "href": "https://stac.overturemaps.org/2026-09-23.0/catalog.json",
            "type": "application/json",
            "title": "2026-09-23.0 Overture Release",
            "latest": True,
        },
    ],
    "latest": "2026-09-23.0",
}


def make_client(body: dict | int, calls: list[httpx.Request]) -> httpx.Client:
    """An int is a status code, a dict a 200 with that body as JSON."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if isinstance(body, int):
            return httpx.Response(body, text="error")
        return httpx.Response(200, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_resolves_from_the_top_level_latest_field(tmp_path):
    calls: list[httpx.Request] = []
    release = current_release(tmp_path, client=make_client(CATALOG, calls))
    assert release == "2026-09-23.0"
    assert len(calls) == 1
    assert (tmp_path / CACHE_FILENAME).is_file()


def test_falls_back_to_the_latest_child_link_when_the_top_level_field_is_missing(tmp_path):
    catalog = {k: v for k, v in CATALOG.items() if k != "latest"}
    release = current_release(tmp_path, client=make_client(catalog, []))
    assert release == "2026-09-23.0"


def test_a_malformed_top_level_field_still_resolves_from_the_links(tmp_path):
    catalog = {**CATALOG, "latest": "not-a-release"}
    assert current_release(tmp_path, client=make_client(catalog, [])) == "2026-09-23.0"


def test_the_second_call_is_served_from_the_disk_cache(tmp_path):
    calls: list[httpx.Request] = []
    client = make_client(CATALOG, calls)
    first = current_release(tmp_path, client=client)
    second = current_release(tmp_path, client=client)
    assert first == second == "2026-09-23.0"
    assert len(calls) == 1


def test_a_stale_cache_entry_is_re_resolved(tmp_path):
    calls: list[httpx.Request] = []
    client = make_client(CATALOG, calls)
    current_release(tmp_path, client=client, max_age_s=1)
    time.sleep(1.01)
    current_release(tmp_path, client=client, max_age_s=1)
    assert len(calls) == 2


def test_a_fresh_cache_entry_is_reused_even_with_a_short_max_age(tmp_path):
    calls: list[httpx.Request] = []
    client = make_client(CATALOG, calls)
    current_release(tmp_path, client=client, max_age_s=86400)
    current_release(tmp_path, client=client, max_age_s=86400)
    assert len(calls) == 1


def test_a_network_error_raises_rather_than_returning_a_sentinel(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.ConnectError):
        current_release(tmp_path, client=client)
    assert list(tmp_path.iterdir()) == []


def test_an_http_error_raises(tmp_path):
    with pytest.raises(httpx.HTTPStatusError):
        current_release(tmp_path, client=make_client(503, []))


def test_a_catalog_without_any_resolvable_release_raises(tmp_path):
    catalog = {"type": "Catalog", "links": []}
    with pytest.raises(ValueError, match="latest"):
        current_release(tmp_path, client=make_client(catalog, []))


def test_a_truncated_cache_entry_is_a_miss_not_a_poisoned_answer(tmp_path):
    # Like the Overpass and LoD2 caches: a crash mid-write leaves a file this call recovers from
    # instead of failing on forever.
    (tmp_path / CACHE_FILENAME).write_text("{not json")
    calls: list[httpx.Request] = []
    release = current_release(tmp_path, client=make_client(CATALOG, calls))
    assert release == "2026-09-23.0"
    assert len(calls) == 1
