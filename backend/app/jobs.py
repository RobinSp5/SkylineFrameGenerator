"""In-process job store: one background thread pool runs the pipeline, files live under root/<job id>/."""

import logging
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from skylineframe.errors import SkylineError
from skylineframe.pipeline import ProgressCallback, RunResult, run
from skylineframe.spec import FrameSpec

log = logging.getLogger(__name__)

JOB_FILES: tuple[str, ...] = ("model.stl", "model.3mf", "preview.glb")
JobStatus = Literal["queued", "running", "done", "error"]
Runner = Callable[[FrameSpec, Path, Path, ProgressCallback], RunResult]


@dataclass
class Job:
    id: str
    spec: FrameSpec
    dir: Path
    status: JobStatus = "queued"
    stage: str = ""
    message: str = ""
    stats: dict[str, int] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"id": self.id, "status": self.status, "stage": self.stage, "message": self.message, "stats": self.stats}


def _default_runner(spec: FrameSpec, out_dir: Path, cache_dir: Path, progress: ProgressCallback) -> RunResult:
    return run(spec, out_dir, cache_dir, progress=progress)


class JobStore:
    def __init__(self, root: Path, cache_dir: Path, runner: Runner = _default_runner, workers: int = 2) -> None:
        self._root = root
        self._cache_dir = cache_dir
        self._runner = runner
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="skyline-job")
        self._root.mkdir(parents=True, exist_ok=True)

    def create(self, spec: FrameSpec) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job = Job(id=job_id, spec=spec, dir=self._root / job_id)
        with self._lock:
            self._jobs[job_id] = job
        self._pool.submit(self._execute, job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def file(self, job_id: str, name: str) -> Path | None:
        job = self.get(job_id)
        if job is None or job.status != "done" or name not in JOB_FILES:
            return None
        path = job.dir / name
        return path if path.is_file() else None

    def cleanup(self, max_age_s: float = 86400) -> int:
        removed = 0
        cutoff = time.time() - max_age_s
        for child in self._root.iterdir():
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        return removed

    def _execute(self, job: Job) -> None:
        def progress(stage: str, message: str) -> None:
            job.stage, job.message = stage, message

        job.status = "running"
        try:
            result = self._runner(job.spec, job.dir, self._cache_dir, progress)
        except (SkylineError, ValueError) as exc:
            # Both carry a message written for the user (ValueError e.g. from the projection stage).
            job.status, job.message = "error", str(exc)
        except Exception:
            log.exception("job %s crashed", job.id)
            job.status, job.message = "error", "Unexpected error during generation. See server log."
        else:
            job.stats = result.stats
            job.status, job.message = "done", "Ready"
