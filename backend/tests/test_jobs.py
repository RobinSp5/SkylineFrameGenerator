import os
import threading
import time
from pathlib import Path

import httpx
import pytest

from app.geocode import GeocodeError
from app.jobs import JOB_FILES, MAX_ACTIVE_JOBS, Job, JobQueueFull, JobStore
from skylineframe.errors import AreaError, FetchError
from skylineframe.export import ExportPaths
from skylineframe.pipeline import RunResult
from skylineframe.spec import FrameSpec


def spec() -> FrameSpec:
    return FrameSpec(center_lat=50.1, center_lon=8.6)


def fake_runner(spec, out_dir: Path, cache_dir: Path, progress, name=None):
    progress("fetch", "loading")
    out_dir.mkdir(parents=True, exist_ok=True)
    for filename in JOB_FILES:
        (out_dir / filename).write_bytes(b"x" * 10)
    return RunResult(ExportPaths(out_dir / "model.stl", out_dir / "model.3mf", out_dir / "preview.glb"), {"buildings": 5})


def failing_runner(spec, out_dir, cache_dir, progress, name=None):
    raise FetchError("Overpass down")


def crashing_runner(spec, out_dir, cache_dir, progress, name=None):
    raise ZeroDivisionError("bug")


def antimeridian_runner(spec, out_dir, cache_dir, progress, name=None):
    raise AreaError("Areas crossing the antimeridian (±180° longitude) are not supported.")


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


def test_area_error_message_reaches_user(tmp_path):
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
    stamp = time.time() - 2 * 86400
    os.utime(old, (stamp, stamp))
    store = JobStore(root, tmp_path / "cache", runner=fake_runner)
    removed = store.cleanup(max_age_s=86400)
    assert removed == 1 and not old.exists()


def test_to_dict_shape(tmp_path):
    store = JobStore(tmp_path / "jobs", tmp_path / "cache", runner=fake_runner)
    job = wait_done(store, store.create(spec()).id)
    d = job.to_dict()
    assert set(d) == {"id", "status", "stage", "message", "stats", "name", "file_stem"}


def test_queue_is_bounded(tmp_path):
    gate = threading.Event()

    def blocking_runner(spec, out_dir, cache_dir, progress, name=None):
        assert gate.wait(10), "gate was never released"
        return fake_runner(spec, out_dir, cache_dir, progress)

    store = JobStore(tmp_path / "jobs", tmp_path / "cache", runner=blocking_runner)
    try:
        ids = [store.create(spec()).id for _ in range(MAX_ACTIVE_JOBS)]
        for _ in range(2):
            with pytest.raises(JobQueueFull):
                store.create(spec())
    finally:
        gate.set()
    for job_id in ids:
        assert wait_done(store, job_id, timeout_s=10).status == "done"
    assert store.create(spec()).id not in ids  # slots are free again


def test_cleanup_keeps_running_jobs_and_forgets_removed_ones(tmp_path):
    root = tmp_path / "jobs"
    store = JobStore(root, tmp_path / "cache", runner=fake_runner)
    stamp = time.time() - 2 * 86400

    busy = root / "busy00000000"
    busy.mkdir(parents=True)
    os.utime(busy, (stamp, stamp))
    store._jobs[busy.name] = Job(id=busy.name, spec=spec(), dir=busy, status="running")

    finished = wait_done(store, store.create(spec()).id)
    os.utime(finished.dir, (stamp, stamp))

    assert store.cleanup(max_age_s=86400) == 1
    assert busy.is_dir()
    assert store.get(busy.name) is not None
    assert not finished.dir.exists()
    assert store.get(finished.id) is None


# --- naming: the place a model is saved under -----------------------------------------------


def recording_runner(seen: dict):
    def runner(spec, out_dir, cache_dir, progress, name=None):
        seen["name"] = name
        return fake_runner(spec, out_dir, cache_dir, progress)

    return runner


def test_job_is_named_after_the_place(tmp_path):
    seen: dict = {}
    asked: list[tuple[float, float]] = []

    def namer(lat, lon):
        asked.append((lat, lon))
        return "Frankfurt am Main – Altstadt"

    store = JobStore(tmp_path / "jobs", tmp_path / "cache", runner=recording_runner(seen), namer=namer)
    job = wait_done(store, store.create(spec()).id)
    assert asked == [(50.1, 8.6)]
    assert job.name == "Frankfurt am Main – Altstadt"
    assert job.file_stem == "Frankfurt-am-Main_Altstadt_1500m_10cm"
    assert seen["name"] == "Frankfurt am Main – Altstadt"  # the 3MF object carries it too
    assert job.to_dict()["name"] == job.name and job.to_dict()["file_stem"] == job.file_stem


@pytest.mark.parametrize(
    "namer",
    [
        None,
        lambda lat, lon: None,
        lambda lat, lon: "   ",
        lambda lat, lon: (_ for _ in ()).throw(httpx.ConnectError("offline")),
        lambda lat, lon: (_ for _ in ()).throw(GeocodeError("rate limited")),
        lambda lat, lon: 1 / 0,
    ],
    ids=["no-namer", "nothing-found", "blank", "offline", "rate-limited", "bug"],
)
def test_naming_falls_back_to_coordinates_and_never_fails_the_job(tmp_path, namer):
    seen: dict = {}
    store = JobStore(tmp_path / "jobs", tmp_path / "cache", runner=recording_runner(seen), namer=namer)
    job = wait_done(store, store.create(spec()).id)
    assert job.status == "done"
    assert job.name == "50.1000N 8.6000E"
    assert job.file_stem == "50.1000N-8.6000E_1500m_10cm"
    assert seen["name"] == job.name


def test_download_names(tmp_path):
    store = JobStore(tmp_path / "jobs", tmp_path / "cache", runner=fake_runner, namer=lambda lat, lon: "Eppstein")
    job = wait_done(store, store.create(spec()).id)
    assert job.download_name("model.stl") == "Eppstein_1500m_10cm.stl"
    assert job.download_name("model.3mf") == "Eppstein_1500m_10cm.3mf"
    assert job.download_name("SOURCES.txt") == "Eppstein_1500m_10cm_SOURCES.txt"
    assert job.download_name("preview.glb") == "preview.glb"  # only the browser fetches it


def test_download_names_before_the_job_is_named(tmp_path):
    job = Job(id="x", spec=spec(), dir=tmp_path)
    assert job.download_name("model.stl") == "model.stl"
