import time

import pytest
from fastapi.testclient import TestClient

from app.jobs import JobStore
from app.main import create_app
from tests.test_jobs import fake_runner, failing_runner

SPEC = {"center_lat": 50.11, "center_lon": 8.68, "side_m": 1000, "mode": "full"}


@pytest.fixture
def client(tmp_path):
    store = JobStore(tmp_path / "jobs", tmp_path / "cache", runner=fake_runner)
    return TestClient(create_app(store=store))


def poll(client: TestClient, job_id: str) -> dict:
    for _ in range(100):
        body = client.get(f"/api/jobs/{job_id}").json()
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
    client = TestClient(create_app(store=store))
    job_id = client.post("/api/jobs", json=SPEC).json()["id"]
    body = poll(client, job_id)
    assert body["status"] == "error" and body["message"] == "Overpass down"
    assert client.get(f"/api/jobs/{job_id}/model.stl").status_code == 404
