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
MAX_ACTIVE_JOBS = 8  # queued + running; one browser must not be able to fill the machine
ACTIVE_STATUSES: tuple[str, ...] = ("queued", "running")
JobStatus = Literal["queued", "running", "done", "error"]
Runner = Callable[[FrameSpec, Path, Path, ProgressCallback], RunResult]


class JobQueueFull(RuntimeError):
    """More jobs are already queued or running than this server accepts."""


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
            active = sum(1 for other in self._jobs.values() if other.status in ACTIVE_STATUSES)
            if active >= MAX_ACTIVE_JOBS:
                raise JobQueueFull(f"{active} jobs already queued or running")
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
        """Drop job directories older than max_age_s, never one of a job still queued or running."""
        removed = 0
        cutoff = time.time() - max_age_s
        with self._lock:
            active = {job_id for job_id, job in self._jobs.items() if job.status in ACTIVE_STATUSES}
            for child in self._root.iterdir():
                if child.name in active:
                    continue
                try:
                    if not child.is_dir() or child.stat().st_mtime >= cutoff:
                        continue
                except OSError:  # vanished or unreadable between iterdir() and stat()
                    continue
                shutil.rmtree(child, ignore_errors=True)
                self._jobs.pop(child.name, None)
                removed += 1
        return removed

    def _execute(self, job: Job) -> None:
        def progress(stage: str, message: str) -> None:
            job.stage, job.message = stage, message

        job.status = "running"
        # Every branch assigns message (and stats) before status: a poller that sees a terminal
        # status must never read the message of the previous one.
        try:
            result = self._runner(job.spec, job.dir, self._cache_dir, progress)
        except SkylineError as exc:
            # Every expected failure of the pipeline is a SkylineError and carries a message
            # written for the user; anything else is a bug and is hidden below.
            log.warning("job %s failed: %s", job.id, exc)
            job.message, job.status = str(exc), "error"
        except Exception:
            log.exception("job %s crashed", job.id)
            job.message, job.status = "Unexpected error during generation. See server log.", "error"
        else:
            job.stats = result.stats
            job.message, job.status = "Ready", "done"
