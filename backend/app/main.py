"""FastAPI application: job endpoints, geocoding and (in production) the built frontend."""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from skylineframe.spec import FrameSpec

from .jobs import JobQueueFull, JobStore

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_JOBS_DIR = BACKEND_DIR / ".jobs"
DEFAULT_CACHE_DIR = BACKEND_DIR / ".cache" / "overpass"
DEFAULT_FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"
# The server is meant to be reached on the loopback interface only; a Host header naming any
# other name is a DNS-rebinding attempt and is rejected before CORS is ever consulted.
DEFAULT_ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

MEDIA_TYPES = {
    "model.stl": "model/stl",
    "model.3mf": "model/3mf",
    "preview.glb": "model/gltf-binary",
}


def create_app(
    store: JobStore | None = None,
    geocoder=None,
    frontend_dist: Path | None = None,
    allowed_hosts: list[str] | None = None,
) -> FastAPI:
    store = store or JobStore(DEFAULT_JOBS_DIR, DEFAULT_CACHE_DIR)
    store.cleanup()
    app = FastAPI(title="Skyline Frame Generator")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Added last, so it is the outermost layer: a bad Host never reaches a route or CORS.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or DEFAULT_ALLOWED_HOSTS)

    @app.post("/api/jobs", status_code=202)
    def create_job(spec: FrameSpec) -> dict:
        try:
            return {"id": store.create(spec).id}
        except JobQueueFull:
            raise HTTPException(429, "Too many jobs in progress; try again in a moment.") from None

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return job.to_dict()

    @app.get("/api/jobs/{job_id}/{filename}")
    def get_job_file(job_id: str, filename: str) -> FileResponse:
        path = store.file(job_id, filename)
        if path is None:
            raise HTTPException(404, "file not available")
        return FileResponse(path, media_type=MEDIA_TYPES.get(filename, "application/octet-stream"), filename=filename)

    if geocoder is not None:

        @app.get("/api/geocode")
        def geocode(q: str = Query(min_length=2, max_length=200)) -> list[dict]:
            return [r.__dict__ for r in geocoder.search(q)]

    dist = frontend_dist or DEFAULT_FRONTEND_DIST
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app
