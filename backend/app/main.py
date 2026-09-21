"""FastAPI application: job endpoints, geocoding and (in production) the built frontend."""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from skylineframe.spec import FrameSpec

from .jobs import JobStore

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_JOBS_DIR = BACKEND_DIR / ".jobs"
DEFAULT_CACHE_DIR = BACKEND_DIR / ".cache" / "overpass"
DEFAULT_FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"

MEDIA_TYPES = {
    "model.stl": "model/stl",
    "model.3mf": "model/3mf",
    "preview.glb": "model/gltf-binary",
}


def create_app(store: JobStore | None = None, geocoder=None, frontend_dist: Path | None = None) -> FastAPI:
    store = store or JobStore(DEFAULT_JOBS_DIR, DEFAULT_CACHE_DIR)
    store.cleanup()
    app = FastAPI(title="Skyline Frame Generator")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/api/jobs", status_code=202)
    def create_job(spec: FrameSpec) -> dict:
        return {"id": store.create(spec).id}

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
        return FileResponse(path, media_type=MEDIA_TYPES[filename], filename=filename)

    if geocoder is not None:

        @app.get("/api/geocode")
        def geocode(q: str = Query(min_length=2, max_length=200)) -> list[dict]:
            return [r.__dict__ for r in geocoder.search(q)]

    dist = frontend_dist or DEFAULT_FRONTEND_DIST
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app
