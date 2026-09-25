import logging

import duckdb
import httpx
import pytest
import shapely.wkb
from shapely.geometry import box

from skylineframe.features import RoofSpec
from skylineframe.overture import provider
from skylineframe.overture.parse import building_of, parse_buildings

RELEASE = "2026-09-23.0"
BBOX = (50.105, 8.678, 50.116, 8.688)  # Frankfurt Altstadt, project.query_bbox order
FOOTPRINT = box(8.6800, 50.1050, 8.6801, 50.1051)


def row(
    id_: str = "b1",
    height: float | None = None,
    num_floors: int | None = None,
    roof_shape: str | None = None,
    roof_height: float | None = None,
    roof_direction: float | None = None,
    geom=FOOTPRINT,
) -> dict:
    return {
        "id": id_,
        "height": height,
        "num_floors": num_floors,
        "roof_shape": roof_shape,
        "roof_height": roof_height,
        "roof_direction": roof_direction,
        "roof_color": None,
        "class": None,
        "geom_wkb_hex": shapely.wkb.dumps(geom).hex(),
    }


def stub_release(monkeypatch, release: str = RELEASE) -> None:
    monkeypatch.setattr(provider, "current_release", lambda cache_dir, client=None: release)


# --- parse: roof_shape -> RoofSpec -----------------------------------------


def test_a_shape_in_our_nine_becomes_a_matching_roofspec():
    b = building_of(row(roof_shape="gabled", roof_height=3.5, roof_direction=90.0), RELEASE)
    assert b.roof == RoofSpec(shape="gabled", height_m=3.5, direction_deg=90.0)


def test_flat_is_no_roof_shape_info_like_an_untagged_osm_building():
    assert building_of(row(roof_shape="flat"), RELEASE).roof is None


def test_an_overture_shape_outside_our_nine_is_treated_as_untagged():
    assert building_of(row(roof_shape="saltbox"), RELEASE).roof is None


def test_a_null_roof_shape_is_none():
    assert building_of(row(roof_shape=None), RELEASE).roof is None


def test_a_recognised_shape_without_a_roof_height_defaults_to_zero():
    assert building_of(row(roof_shape="hipped", roof_height=None), RELEASE).roof.height_m == 0.0


def test_direction_passes_through_as_a_bare_bearing():
    assert building_of(row(roof_shape="skillion", roof_direction=270.0), RELEASE).roof.direction_deg == 270.0


# --- parse: the rest of the record ------------------------------------------


def test_height_m_is_none_when_overture_carries_no_height():
    assert building_of(row(height=None), RELEASE).height_m is None
    assert building_of(row(height=12.5), RELEASE).height_m == 12.5


def test_osm_id_is_namespaced_by_source_and_release():
    assert building_of(row(id_="abc123"), RELEASE).osm_id == "overture/2026-09-23.0/abc123"


def test_geometry_round_trips_through_wkb():
    b = building_of(row(geom=FOOTPRINT), RELEASE)
    assert b.geom.equals(FOOTPRINT)


def test_parse_buildings_keeps_row_order():
    rows = [row(id_="a"), row(id_="b"), row(id_="c")]
    assert [b.osm_id.rsplit("/", 1)[-1] for b in parse_buildings(rows, RELEASE)] == ["a", "b", "c"]


# --- provider.fetch: degrades to [] rather than raising ---------------------


def test_a_release_resolution_failure_degrades_to_empty(tmp_path, caplog, monkeypatch):
    def boom(cache_dir, client=None):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(provider, "current_release", boom)
    with caplog.at_level(logging.WARNING, logger="skylineframe.overture.provider"):
        assert provider.fetch(BBOX, tmp_path) == []
    assert "Overture" in caplog.text


def test_a_duckdb_query_failure_degrades_to_empty(tmp_path, caplog, monkeypatch):
    stub_release(monkeypatch)

    def boom(release, bbox):
        raise duckdb.IOException("HTTP 403 Forbidden")

    monkeypatch.setattr(provider, "_run_query", boom)
    with caplog.at_level(logging.WARNING, logger="skylineframe.overture.provider"):
        assert provider.fetch(BBOX, tmp_path) == []
    assert "403" in caplog.text


def test_a_malformed_row_degrades_to_empty_instead_of_crashing_the_run(tmp_path, caplog, monkeypatch):
    stub_release(monkeypatch)
    monkeypatch.setattr(provider, "_run_query", lambda release, bbox: [{"id": "x"}])  # no geom_wkb_hex
    with caplog.at_level(logging.WARNING, logger="skylineframe.overture.provider"):
        assert provider.fetch(BBOX, tmp_path) == []
    # Unparseable rows are never persisted: a schema drift must surface again on the next call
    # instead of being pinned as a silent, permanent empty answer.
    assert list((tmp_path / "overture").glob("*.json")) == []


def test_an_oversized_result_is_abandoned_not_truncated(tmp_path, caplog, monkeypatch):
    stub_release(monkeypatch)
    monkeypatch.setattr(provider, "MAX_BUILDINGS", 2)
    monkeypatch.setattr(provider, "_run_query", lambda release, bbox: [row("a"), row("b"), row("c")])
    with caplog.at_level(logging.WARNING, logger="skylineframe.overture.provider"):
        assert provider.fetch(BBOX, tmp_path) == []
    assert "over" in caplog.text.lower()
    # Not cached: a genuinely too-large answer must not be pinned as a permanent empty result.
    assert list((tmp_path / "overture").glob("*.json")) == []


# --- provider.fetch: caching -------------------------------------------------


def test_a_successful_fetch_is_cached_and_reused_without_a_second_query(tmp_path, monkeypatch):
    stub_release(monkeypatch)
    calls: list[tuple] = []

    def fake_query(release, bbox):
        calls.append((release, bbox))
        return [row("a")]

    monkeypatch.setattr(provider, "_run_query", fake_query)
    first = provider.fetch(BBOX, tmp_path)
    second = provider.fetch(BBOX, tmp_path)
    assert [b.osm_id for b in first] == [b.osm_id for b in second] == ["overture/2026-09-23.0/a"]
    assert len(calls) == 1
    # current_release is stubbed above, so this is the query result alone, content-addressed.
    assert len(list((tmp_path / "overture").glob("*.json"))) == 1


def test_a_different_bbox_is_a_different_cache_entry(tmp_path, monkeypatch):
    stub_release(monkeypatch)
    calls: list[tuple] = []
    monkeypatch.setattr(provider, "_run_query", lambda release, bbox: calls.append((release, bbox)) or [row("a")])
    provider.fetch(BBOX, tmp_path)
    provider.fetch((50.000, 8.000, 50.010, 8.010), tmp_path)
    assert len(calls) == 2
    assert len(list((tmp_path / "overture").glob("*.json"))) == 2


def test_a_different_release_is_a_different_cache_entry(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(provider, "_run_query", lambda release, bbox: calls.append(release) or [row("a")])
    monkeypatch.setattr(provider, "current_release", lambda cache_dir, client=None: "2026-08-19.0")
    provider.fetch(BBOX, tmp_path)
    monkeypatch.setattr(provider, "current_release", lambda cache_dir, client=None: "2026-09-23.0")
    provider.fetch(BBOX, tmp_path)
    assert calls == ["2026-08-19.0", "2026-09-23.0"]
