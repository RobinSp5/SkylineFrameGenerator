import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.jobs import MAX_ACTIVE_JOBS, JobStore
from app.main import create_app
from tests.test_jobs import fake_runner, failing_runner

SPEC = {"center_lat": 50.11, "center_lon": 8.68, "side_m": 1000, "mode": "full"}
# TestClient talks to http://testserver, which the production host allowlist rightly rejects.
TEST_HOSTS = ["testserver"]


@pytest.fixture
def client(tmp_path):
    store = JobStore(tmp_path / "jobs", tmp_path / "cache", runner=fake_runner)
    return TestClient(create_app(store=store, allowed_hosts=TEST_HOSTS))


def poll(client: TestClient, job_id: str) -> dict:
    for _ in range(100):
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] in ("done", "error"):
            return body
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_create_job_returns_id(client):
    r = client.post("/api/jobs", json=SPEC)
    assert r.status_code == 202
    assert "id" in r.json()


def test_invalid_spec_is_422(client):
    r = client.post("/api/jobs", json={**SPEC, "side_m": 10})
    assert r.status_code == 422


def test_job_lifecycle_and_downloads(client):
    job_id = client.post("/api/jobs", json=SPEC).json()["id"]
    body = poll(client, job_id)
    assert body["status"] == "done" and body["stats"] == {"buildings": 5}
    for name, ctype in [("model.stl", "model/stl"), ("model.3mf", "model/3mf"), ("preview.glb", "model/gltf-binary")]:
        r = client.get(f"/api/jobs/{job_id}/{name}")
        assert r.status_code == 200, name
        assert r.headers["content-type"].startswith(ctype)
        assert r.content == b"x" * 10


def test_unknown_job_is_404(client):
    assert client.get("/api/jobs/doesnotexist").status_code == 404
    assert client.get("/api/jobs/doesnotexist/model.stl").status_code == 404


def test_error_job_exposes_message(tmp_path):
    store = JobStore(tmp_path / "jobs", tmp_path / "cache", runner=failing_runner)
    client = TestClient(create_app(store=store, allowed_hosts=TEST_HOSTS))
    job_id = client.post("/api/jobs", json=SPEC).json()["id"]
    body = poll(client, job_id)
    assert body["status"] == "error" and body["message"] == "Overpass down"
    assert client.get(f"/api/jobs/{job_id}/model.stl").status_code == 404


def test_foreign_host_header_is_rejected(client):
    assert client.get("/api/jobs/x", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/api/jobs/x").status_code == 404  # the allowed host still works


def test_default_allowed_hosts_exclude_testserver(tmp_path):
    store = JobStore(tmp_path / "jobs", tmp_path / "cache", runner=fake_runner)
    client = TestClient(create_app(store=store))
    assert client.get("/api/jobs/x").status_code == 400
    assert client.get("/api/jobs/x", headers={"Host": "localhost:8000"}).status_code == 404


def test_too_many_jobs_is_429(tmp_path):
    gate = threading.Event()

    def blocking_runner(spec, out_dir, cache_dir, progress):
        assert gate.wait(10), "gate was never released"
        return fake_runner(spec, out_dir, cache_dir, progress)

    store = JobStore(tmp_path / "jobs", tmp_path / "cache", runner=blocking_runner)
    client = TestClient(create_app(store=store, allowed_hosts=TEST_HOSTS))
    try:
        responses = [client.post("/api/jobs", json=SPEC) for _ in range(MAX_ACTIVE_JOBS + 1)]
    finally:
        gate.set()
    codes = [r.status_code for r in responses]
    assert codes[:MAX_ACTIVE_JOBS] == [202] * MAX_ACTIVE_JOBS
    assert codes[-1] == 429
    for r in responses[:MAX_ACTIVE_JOBS]:  # drain, so no worker outlives the test
        assert poll(client, r.json()["id"])["status"] == "done"


def test_create_job_accepts_the_detail_flags(client):
    r = client.post("/api/jobs", json={**SPEC, "roofs": False, "parts": False})
    assert r.status_code == 202


def test_create_job_still_rejects_unknown_fields(client):
    # FrameSpec forbids extras, so the frontend may only send fields the backend knows.
    r = client.post("/api/jobs", json={**SPEC, "preset": "detail"})
    assert r.status_code == 422


def test_spec_accepts_the_lod2_flag(client):
    response = client.post("/api/jobs", json={"center_lat": 50.11, "center_lon": 8.68, "lod2": False})
    assert response.status_code == 202


def test_sources_txt_is_downloadable():
    from app.jobs import JOB_FILES
    from app.main import MEDIA_TYPES

    assert "SOURCES.txt" in JOB_FILES
    assert MEDIA_TYPES["SOURCES.txt"].startswith("text/plain")
