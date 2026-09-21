import time
from pathlib import Path

import pytest

from app.jobs import JOB_FILES, JobStore
from skylineframe.errors import FetchError
from skylineframe.export import ExportPaths
from skylineframe.pipeline import RunResult
from skylineframe.spec import FrameSpec


def spec() -> FrameSpec:
    return FrameSpec(center_lat=50.1, center_lon=8.6)


def fake_runner(spec, out_dir: Path, cache_dir: Path, progress):
    progress("fetch", "loading")
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in JOB_FILES:
        (out_dir / name).write_bytes(b"x" * 10)
    return RunResult(ExportPaths(out_dir / "model.stl", out_dir / "model.3mf", out_dir / "preview.glb"), {"buildings": 5})


def failing_runner(spec, out_dir, cache_dir, progress):
    raise FetchError("Overpass down")


def crashing_runner(spec, out_dir, cache_dir, progress):
    raise ZeroDivisionError("bug")


def antimeridian_runner(spec, out_dir, cache_dir, progress):
    raise ValueError("Areas crossing the antimeridian (±180° longitude) are not supported.")


def wait_done(store: JobStore, job_id: str, timeout_s: float = 5.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        job = store.get(job_id)
        if job.status in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_create_and_complete_job(tmp_path):
    store = JobStore(tmp_path / "jobs", tmp_path / "cache", runner=fake_runner)
    job = store.create(spec())
    assert job.status in ("queued", "running")
    job = wait_done(store, job.id)
    assert job.status == "done"
    assert job.stats == {"buildings": 5}
    assert job.stage == "fetch"
    assert store.file(job.id, "model.stl") == tmp_path / "jobs" / job.id / "model.stl"


def test_files_unavailable_until_done(tmp_path):
    store = JobStore(tmp_path / "jobs", tmp_path / "cache", runner=failing_runner)
    job = wait_done(store, store.create(spec()).id)
    assert job.status == "error"
    assert job.message == "Overpass down"
    assert store.file(job.id, "model.stl") is None


def test_unexpected_exception_is_hidden(tmp_path):
    store = JobStore(tmp_path / "jobs", tmp_path / "cache", runner=crashing_runner)
    job = wait_done(store, store.create(spec()).id)
    assert job.status == "error"
    assert "bug" not in job.message
    assert "Unexpected" in job.message


def test_value_error_message_reaches_user(tmp_path):
    store = JobStore(tmp_path / "jobs", tmp_path / "cache", runner=antimeridian_runner)
    job = wait_done(store, store.create(spec()).id)
    assert job.status == "error"
    assert "antimeridian" in job.message


def test_file_rejects_unknown_names(tmp_path):
    store = JobStore(tmp_path / "jobs", tmp_path / "cache", runner=fake_runner)
    job = wait_done(store, store.create(spec()).id)
    assert store.file(job.id, "../secret.txt") is None
    assert store.file(job.id, "model.obj") is None
    assert store.file("nope", "model.stl") is None


def test_cleanup_removes_old_dirs(tmp_path):
    root = tmp_path / "jobs"
    old = root / "old123"
    old.mkdir(parents=True)
    (old / "model.stl").write_bytes(b"x")
    import os

    stamp = time.time() - 2 * 86400
    os.utime(old, (stamp, stamp))
    store = JobStore(root, tmp_path / "cache", runner=fake_runner)
    removed = store.cleanup(max_age_s=86400)
    assert removed == 1 and not old.exists()


def test_to_dict_shape(tmp_path):
    store = JobStore(tmp_path / "jobs", tmp_path / "cache", runner=fake_runner)
    job = wait_done(store, store.create(spec()).id)
    d = job.to_dict()
    assert set(d) == {"id", "status", "stage", "message", "stats"}
