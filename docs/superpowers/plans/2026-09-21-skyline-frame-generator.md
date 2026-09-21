# Skyline Frame Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein lokal laufender Generator, der aus einem quadratischen Stadtausschnitt (OpenStreetMap) ein 3D-druckbares Modell im cityframes-Stil erzeugt: STL (einfarbig) und 3MF (mehrfarbig), bedient über eine Web-UI mit Karte.

**Architecture:** Python-Paket `skylineframe` mit sechs reinen Pipeline-Stufen (fetch → project → prepare → scale → mesh → export), darüber eine FastAPI mit Hintergrund-Jobs, davor ein Vite/TypeScript-Frontend mit MapLibre-Karte und three.js-Vorschau. Geometrie-Booleans laufen in manifold3d (garantiert wasserdicht), trimesh übernimmt nur Export und Verifikation.

**Tech Stack:** Python 3.13, uv, pydantic 2, shapely 2.1, pyproj 3.8, osm2geojson 0.3, manifold3d 3.5, trimesh 5.1, FastAPI, httpx, typer, pytest. Frontend: Vite, TypeScript, maplibre-gl, three, vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-21-skyline-frame-generator-design.md`

## Global Constraints

- Python ≥ 3.13, Paketverwaltung mit `uv` (`backend/pyproject.toml`), Tests mit `uv run pytest -q` aus `backend/`.
- Alle Maße im Mesh in Millimetern, Plattenoberseite bei z = 0, Modell zentriert um (0, 0).
- `scale = plate_size_mm / side_m` (Millimeter pro Meter). Defaults: side_m 1500, plate_size_mm 100, plate_thickness_mm 3.0, z_exaggeration 1.5, min_building_height_mm 0.8, min_footprint_area_mm2 1.0, road_depth_mm 0.4, water_depth_mm 0.6.
- Mindest-Feature 0,8 mm (Straßenbreiten, Gebäudemindesthöhe).
- Rotation: `rotation_deg` ist die Drehung des Quadrats im Uhrzeigersinn gegen Nord. Zum Ausrichten wird die Welt um `+rotation_deg` gegen den Uhrzeigersinn gedreht (shapely-Konvention: positiv = CCW).
- Straßenklassen: motorway, trunk, primary, secondary, tertiary, residential, unclassified, living_street, pedestrian, service.
- Overpass-Default `https://overpass-api.de/api/interpreter`, Cache unter `backend/.cache/overpass/<sha256(query)>.json`, Retry 3× mit Backoff auf 429/502/503/504 und Netzfehlern.
- Nominatim `https://nominatim.openstreetmap.org/search`, User-Agent `SkylineFrameGenerator/0.1 (local dev tool)`, max. 1 Request/s, 10 min Cache.
- 3MF-Objektnamen: `base`, `buildings`, `water`, `roads`. Single-STL = base (mit Vertiefungen) + buildings.
- Tests laufen ohne Netz. Die einzige Netz-Aktion ist das einmalige Aufzeichnen der Fixture (Task 3) und die manuelle Abnahme (Task 15).
- Jeder Task endet mit grünen Tests und einem Commit. Commit-Messages enden mit `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Kein Netz in Unit-Tests: httpx-Aufrufe immer über `httpx.MockTransport` testen.

## Agent-Einsatz (everything-claude-code)

- Umsetzung jedes Tasks: Subagent mit `everything-claude-code:tdd-guide`-Disziplin (Test zuerst).
- Nach jedem Task: `everything-claude-code:code-reviewer` über den Diff.
- Task 9 und 10 zusätzlich: `everything-claude-code:security-reviewer` (Datei-Download-Pfade, Query-Parameter).
- Task 14: `everything-claude-code:e2e-runner` für den Playwright-Smoke-Test.
- Bei roten Builds: `everything-claude-code:build-error-resolver`.

## Dateistruktur

```
backend/
  pyproject.toml
  skylineframe/
    __init__.py
    spec.py        FrameSpec, Mode, ROAD_CLASSES, DEFAULT_ROAD_WIDTH_MM
    errors.py      SkylineError und Unterklassen
    features.py    Building, Road, Water, Features (Rohdaten-Container)
    project.py     local_transformer, to_local, square_local, square_wgs84, query_bbox, project_features
    fetch.py       build_query, parse_height, parse_overpass, fetch_overpass, fetch_features
    prepare.py     polygons_of, prepare -> Prepared
    scale.py       Prism, Scaled, scale_features
    mesh.py        MeshSet, build_meshes, to_trimesh
    export.py      ExportPaths, export_all
    pipeline.py    RunResult, run
    cli.py         typer-App
  app/
    __init__.py
    jobs.py        Job, JobStore
    geocode.py     Geocoder
    main.py        create_app, app
  tests/
    conftest.py
    fixtures/record_frankfurt.py, fixtures/frankfurt_roemer.json
    test_spec.py test_project.py test_fetch.py test_prepare.py test_scale.py test_mesh.py test_export.py test_pipeline.py test_cli.py test_jobs.py test_api.py test_geocode.py
frontend/
  index.html  vite.config.ts  package.json  tsconfig.json
  src/main.ts  src/square.ts  src/api.ts  src/map.ts  src/search.ts  src/controls.ts  src/viewer.ts  src/style.css
  src/square.test.ts  src/api.test.ts
  e2e/smoke.spec.ts  playwright.config.ts
Makefile  README.md  tasks/todo.md
```

---

### Task 1: Backend-Grundgerüst und `FrameSpec`

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/skylineframe/__init__.py`
- Create: `backend/skylineframe/spec.py`
- Create: `backend/tests/__init__.py` (leer)
- Test: `backend/tests/test_spec.py`

**Interfaces:**
- Produces: `FrameSpec` (pydantic BaseModel) mit den Feldern aus der Spec-Tabelle, Property `scale: float`; `Mode` (StrEnum `simple`/`full`); Konstanten `ROAD_CLASSES: tuple[str, ...]`, `DEFAULT_ROAD_WIDTH_MM: dict[str, float]`, `MIN_FEATURE_MM = 0.8`, `LEVEL_HEIGHT_M = 3.2`.

- [ ] **Step 1: pyproject anlegen und Umgebung synchronisieren**

`backend/pyproject.toml`:

```toml
[project]
name = "skylineframe"
version = "0.1.0"
description = "Generate 3D-printable city skyline frames from OpenStreetMap data"
requires-python = ">=3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "pydantic>=2.7",
    "httpx>=0.27",
    "shapely>=2.1",
    "pyproj>=3.7",
    "osm2geojson>=0.3",
    "trimesh>=5.0",
    "manifold3d>=3.0",
    "numpy>=2",
    "scipy>=1.13",
    "networkx>=3",
    "lxml>=5",
    "typer>=0.12",
]

[project.scripts]
skylineframe = "skylineframe.cli:app"

[dependency-groups]
dev = ["pytest>=8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["skylineframe", "app"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`backend/skylineframe/__init__.py`:

```python
"""Skyline Frame Generator core pipeline."""
```

Run: `cd backend && mkdir -p app tests && touch app/__init__.py tests/__init__.py && uv sync`
Expected: `.venv` wird angelegt, alle Pakete installiert, keine Fehler.

- [ ] **Step 2: Failing Tests für FrameSpec schreiben**

`backend/tests/test_spec.py`:

```python
import pytest
from pydantic import ValidationError

from skylineframe.spec import DEFAULT_ROAD_WIDTH_MM, FrameSpec, Mode


def test_defaults():
    s = FrameSpec(center_lat=50.11, center_lon=8.68)
    assert s.side_m == 1500
    assert s.plate_size_mm == 100
    assert s.plate_thickness_mm == 3.0
    assert s.mode == Mode.simple
    assert s.z_exaggeration == 1.5
    assert s.road_width_mm == DEFAULT_ROAD_WIDTH_MM


def test_scale_is_mm_per_metre():
    s = FrameSpec(center_lat=50.11, center_lon=8.68, side_m=1000, plate_size_mm=100)
    assert s.scale == pytest.approx(0.1)


def test_rejects_side_out_of_range():
    with pytest.raises(ValidationError):
        FrameSpec(center_lat=50, center_lon=8, side_m=100)
    with pytest.raises(ValidationError):
        FrameSpec(center_lat=50, center_lon=8, side_m=6000)


def test_rejects_unknown_road_class():
    with pytest.raises(ValidationError):
        FrameSpec(center_lat=50, center_lon=8, road_width_mm={"autobahn": 2.0})


def test_rejects_road_width_below_min_feature():
    with pytest.raises(ValidationError):
        FrameSpec(center_lat=50, center_lon=8, road_width_mm={"primary": 0.5})


def test_partial_road_widths_merge_with_defaults():
    s = FrameSpec(center_lat=50, center_lon=8, road_width_mm={"primary": 2.5})
    assert s.road_width_mm["primary"] == 2.5
    assert s.road_width_mm["service"] == DEFAULT_ROAD_WIDTH_MM["service"]


def test_rejects_recess_deeper_than_plate():
    with pytest.raises(ValidationError):
        FrameSpec(center_lat=50, center_lon=8, plate_thickness_mm=1.0, water_depth_mm=1.0)
    with pytest.raises(ValidationError):
        FrameSpec(center_lat=50, center_lon=8, plate_thickness_mm=1.0, road_depth_mm=1.5)


def test_mode_accepts_string():
    s = FrameSpec(center_lat=50, center_lon=8, mode="full")
    assert s.mode is Mode.full
```

- [ ] **Step 3: Tests laufen lassen, Fehlschlag bestätigen**

Run: `cd backend && uv run pytest tests/test_spec.py -q`
Expected: FAIL mit `ModuleNotFoundError: No module named 'skylineframe.spec'`

- [ ] **Step 4: `spec.py` implementieren**

`backend/skylineframe/spec.py`:

```python
"""Parameters of one frame generation run."""

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator


class Mode(StrEnum):
    simple = "simple"  # plate + buildings
    full = "full"  # plate + buildings + road grooves + water recesses


ROAD_CLASSES: tuple[str, ...] = (
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "residential",
    "unclassified",
    "living_street",
    "pedestrian",
    "service",
)

DEFAULT_ROAD_WIDTH_MM: dict[str, float] = {
    "motorway": 2.0,
    "trunk": 2.0,
    "primary": 1.6,
    "secondary": 1.4,
    "tertiary": 1.2,
    "residential": 1.0,
    "unclassified": 1.0,
    "living_street": 1.0,
    "pedestrian": 0.8,
    "service": 0.8,
}

MIN_FEATURE_MM = 0.8  # two nozzle widths on a 0.4 mm nozzle
LEVEL_HEIGHT_M = 3.2  # metres per building level when only building:levels is tagged


class FrameSpec(BaseModel):
    center_lat: float = Field(ge=-85, le=85)
    center_lon: float = Field(ge=-180, le=180)
    side_m: float = Field(default=1500, ge=200, le=5000)
    rotation_deg: float = Field(default=0, ge=-180, le=180)
    plate_size_mm: float = Field(default=100, ge=40, le=250)
    plate_thickness_mm: float = Field(default=3.0, ge=1.0, le=10.0)
    mode: Mode = Mode.simple
    z_exaggeration: float = Field(default=1.5, gt=0, le=10)
    default_building_height_m: float = Field(default=8.0, gt=0)
    min_building_height_mm: float = Field(default=0.8, ge=0)
    min_footprint_area_mm2: float = Field(default=1.0, ge=0)
    road_depth_mm: float = Field(default=0.4, gt=0)
    water_depth_mm: float = Field(default=0.6, gt=0)
    road_width_mm: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_ROAD_WIDTH_MM))

    @field_validator("road_width_mm")
    @classmethod
    def _validate_road_widths(cls, value: dict[str, float]) -> dict[str, float]:
        unknown = set(value) - set(ROAD_CLASSES)
        if unknown:
            raise ValueError(f"unknown road classes: {sorted(unknown)}")
        too_thin = {k: w for k, w in value.items() if w < MIN_FEATURE_MM}
        if too_thin:
            raise ValueError(f"road widths below {MIN_FEATURE_MM} mm are not printable: {too_thin}")
        return {**DEFAULT_ROAD_WIDTH_MM, **value}

    @model_validator(mode="after")
    def _recesses_fit_plate(self) -> "FrameSpec":
        for name in ("road_depth_mm", "water_depth_mm"):
            if getattr(self, name) >= self.plate_thickness_mm:
                raise ValueError(f"{name} must be smaller than plate_thickness_mm")
        return self

    @property
    def scale(self) -> float:
        """Millimetres of print per metre of city."""
        return self.plate_size_mm / self.side_m
```

- [ ] **Step 5: Tests grün**

Run: `cd backend && uv run pytest tests/test_spec.py -q`
Expected: `8 passed`

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/skylineframe backend/app backend/tests
git commit -m "feat(backend): project scaffold and FrameSpec model

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Feature-Container und lokale Projektion

**Files:**
- Create: `backend/skylineframe/features.py`
- Create: `backend/skylineframe/project.py`
- Test: `backend/tests/test_project.py`

**Interfaces:**
- Consumes: `FrameSpec` (Task 1).
- Produces: `features.py`: Dataclasses `Building(geom, height_m: float)`, `Road(geom, cls: str)`, `Water(geom)`, `Features(buildings: list[Building], roads: list[Road], water: list[Water])`. `project.py`: `local_transformer(spec) -> pyproj.Transformer`, `to_local(geom, spec, transformer=None) -> BaseGeometry`, `square_local(spec) -> Polygon`, `square_wgs84(spec) -> Polygon`, `query_bbox(spec, margin_m=100.0) -> tuple[south, west, north, east]`, `project_features(features, spec) -> Features`.

- [ ] **Step 1: `features.py` anlegen**

```python
"""Plain containers for OSM features; geometry CRS depends on the pipeline stage."""

from dataclasses import dataclass, field

from shapely.geometry.base import BaseGeometry


@dataclass
class Building:
    geom: BaseGeometry  # Polygon or MultiPolygon
    height_m: float


@dataclass
class Road:
    geom: BaseGeometry  # LineString or MultiLineString
    cls: str  # one of spec.ROAD_CLASSES


@dataclass
class Water:
    geom: BaseGeometry  # Polygon or MultiPolygon


@dataclass
class Features:
    buildings: list[Building] = field(default_factory=list)
    roads: list[Road] = field(default_factory=list)
    water: list[Water] = field(default_factory=list)
```

- [ ] **Step 2: Failing Tests für die Projektion**

`backend/tests/test_project.py`:

```python
import pytest
from shapely.geometry import LineString, Point, Polygon

from skylineframe.features import Building, Features, Road
from skylineframe.project import (
    local_transformer,
    project_features,
    query_bbox,
    square_local,
    square_wgs84,
    to_local,
)
from skylineframe.spec import FrameSpec


def spec(**kw) -> FrameSpec:
    return FrameSpec(center_lat=50.0, center_lon=8.0, **kw)


def test_center_maps_to_origin():
    p = to_local(Point(8.0, 50.0), spec())
    assert p.x == pytest.approx(0, abs=1e-6)
    assert p.y == pytest.approx(0, abs=1e-6)


def test_one_km_east_is_about_1000_m():
    p = to_local(Point(8.0140, 50.0), spec())
    assert p.x == pytest.approx(1003.7, abs=1.0)
    assert abs(p.y) < 1.0


def test_north_is_positive_y():
    p = to_local(Point(8.0, 50.009), spec())
    assert p.y == pytest.approx(1001, abs=1.0)
    assert abs(p.x) < 0.01


def test_rotation_aligns_rotated_square_with_axes():
    s = spec(rotation_deg=45)
    tr = local_transformer(s)
    lon, lat = tr.transform(707.107, 707.107, direction="INVERSE")  # 1000 m towards north-east
    p = to_local(Point(lon, lat), s)
    assert p.x == pytest.approx(0, abs=0.5)
    assert p.y == pytest.approx(1000, abs=0.5)


def test_square_local_bounds():
    assert square_local(spec(side_m=1000)).bounds == (-500, -500, 500, 500)


def test_square_wgs84_roundtrips_to_axis_aligned_square():
    s = spec(side_m=1000, rotation_deg=30)
    back = to_local(square_wgs84(s), s)
    assert back.bounds == pytest.approx((-500, -500, 500, 500), abs=0.01)


def test_query_bbox_encloses_square_with_margin():
    s = spec(side_m=1000, rotation_deg=20)
    south, west, north, east = query_bbox(s, margin_m=100)
    minx, miny, maxx, maxy = square_wgs84(s).bounds
    assert west < minx and south < miny and east > maxx and north > maxy
    assert south < north and west < east


def test_project_features_keeps_attributes():
    feats = Features(
        buildings=[Building(Polygon([(8.0, 50.0), (8.001, 50.0), (8.001, 50.001)]), 12.0)],
        roads=[Road(LineString([(8.0, 50.0), (8.001, 50.0)]), "primary")],
    )
    local = project_features(feats, spec())
    assert local.buildings[0].height_m == 12.0
    assert local.roads[0].cls == "primary"
    assert local.buildings[0].geom.bounds[2] == pytest.approx(71.7, abs=1.0)
```

- [ ] **Step 3: Fehlschlag bestätigen**

Run: `cd backend && uv run pytest tests/test_project.py -q`
Expected: FAIL mit `ModuleNotFoundError: No module named 'skylineframe.project'`

- [ ] **Step 4: `project.py` implementieren**

```python
"""WGS84 -> local metric frame centred on the spec centre, rotated so the target square is axis-aligned."""

import shapely.affinity
import shapely.ops
from pyproj import CRS, Transformer
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry

from .features import Building, Features, Road, Water
from .spec import FrameSpec


def local_transformer(spec: FrameSpec) -> Transformer:
    crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={spec.center_lat} +lon_0={spec.center_lon} +datum=WGS84 +units=m +no_defs"
    )
    return Transformer.from_crs("EPSG:4326", crs, always_xy=True)


def to_local(geom: BaseGeometry, spec: FrameSpec, transformer: Transformer | None = None) -> BaseGeometry:
    tr = transformer or local_transformer(spec)
    projected = shapely.ops.transform(tr.transform, geom)
    # Square is rotated clockwise by rotation_deg on the map; rotate the world counter-clockwise to align it.
    return shapely.affinity.rotate(projected, spec.rotation_deg, origin=(0, 0))


def square_local(spec: FrameSpec) -> Polygon:
    h = spec.side_m / 2
    return box(-h, -h, h, h)


def _rotated_square_metric(spec: FrameSpec) -> Polygon:
    return shapely.affinity.rotate(square_local(spec), -spec.rotation_deg, origin=(0, 0))


def _to_wgs84(geom: BaseGeometry, spec: FrameSpec) -> BaseGeometry:
    tr = local_transformer(spec)
    return shapely.ops.transform(lambda x, y: tr.transform(x, y, direction="INVERSE"), geom)


def square_wgs84(spec: FrameSpec) -> Polygon:
    """The (rotated) target square as a WGS84 polygon, e.g. for map display."""
    return _to_wgs84(_rotated_square_metric(spec), spec)


def query_bbox(spec: FrameSpec, margin_m: float = 100.0) -> tuple[float, float, float, float]:
    """(south, west, north, east) in WGS84 enclosing the rotated square plus a margin."""
    grown = _rotated_square_metric(spec).buffer(margin_m, join_style="mitre")
    minx, miny, maxx, maxy = _to_wgs84(grown, spec).bounds
    return (miny, minx, maxy, maxx)


def project_features(features: Features, spec: FrameSpec) -> Features:
    tr = local_transformer(spec)
    return Features(
        buildings=[Building(to_local(b.geom, spec, tr), b.height_m) for b in features.buildings],
        roads=[Road(to_local(r.geom, spec, tr), r.cls) for r in features.roads],
        water=[Water(to_local(w.geom, spec, tr)) for w in features.water],
    )
```

- [ ] **Step 5: Tests grün**

Run: `cd backend && uv run pytest tests/test_project.py -q`
Expected: `8 passed`

- [ ] **Step 6: Commit**

```bash
git add backend/skylineframe/features.py backend/skylineframe/project.py backend/tests/test_project.py
git commit -m "feat(backend): feature containers and local projection

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Overpass-Fetch, Parsing, Cache und Fixture

**Files:**
- Create: `backend/skylineframe/errors.py`
- Create: `backend/skylineframe/fetch.py`
- Create: `backend/tests/fixtures/record_frankfurt.py`
- Create: `backend/tests/fixtures/frankfurt_roemer.json` (aufgezeichnet)
- Create: `backend/tests/conftest.py`
- Test: `backend/tests/test_fetch.py`

**Interfaces:**
- Consumes: `FrameSpec`, `Mode`, `ROAD_CLASSES`, `LEVEL_HEIGHT_M` (Task 1); `Features`, `Building`, `Road`, `Water` (Task 2); `query_bbox` (Task 2).
- Produces: `errors.py`: `SkylineError(RuntimeError)`, `FetchError(SkylineError)`. `fetch.py`: `DEFAULT_OVERPASS_URL`, `build_query(bbox, mode) -> str`, `parse_height(tags: dict, default_m: float) -> float`, `parse_overpass(data: dict, spec) -> Features`, `fetch_overpass(query, cache_dir: Path, url=..., client: httpx.Client | None = None, retries=3, backoff_s=2.0, sleep=time.sleep) -> dict`, `fetch_features(spec, cache_dir, url=..., client=None) -> Features`. conftest: Fixtures `frankfurt_spec` (FrameSpec) und `frankfurt_data` (dict).

- [ ] **Step 1: `errors.py` anlegen**

```python
"""Error hierarchy: every user-facing failure of the pipeline derives from SkylineError."""


class SkylineError(RuntimeError):
    """Base class for errors whose message is safe to show to the user."""


class FetchError(SkylineError):
    """Overpass could not be reached or answered with an error."""
```

- [ ] **Step 2: Failing Tests schreiben**

`backend/tests/test_fetch.py`:

```python
import json

import httpx
import pytest

from skylineframe.errors import FetchError
from skylineframe.fetch import build_query, fetch_overpass, parse_height, parse_overpass
from skylineframe.spec import FrameSpec, Mode

SAMPLE = {
    "version": 0.6,
    "elements": [
        {"type": "node", "id": 1, "lat": 50.0, "lon": 8.0},
        {"type": "node", "id": 2, "lat": 50.0, "lon": 8.001},
        {"type": "node", "id": 3, "lat": 50.001, "lon": 8.001},
        {"type": "node", "id": 4, "lat": 50.001, "lon": 8.0},
        {"type": "way", "id": 10, "nodes": [1, 2, 3, 4, 1], "tags": {"building": "yes", "height": "12 m"}},
        {"type": "way", "id": 11, "nodes": [1, 3], "tags": {"highway": "residential"}},
        {"type": "way", "id": 13, "nodes": [2, 4], "tags": {"highway": "footway"}},
        {"type": "node", "id": 5, "lat": 50.0002, "lon": 8.0002},
        {"type": "node", "id": 6, "lat": 50.0002, "lon": 8.0004},
        {"type": "node", "id": 7, "lat": 50.0004, "lon": 8.0004},
        {"type": "node", "id": 8, "lat": 50.0004, "lon": 8.0002},
        {"type": "way", "id": 12, "nodes": [5, 6, 7, 8, 5]},
        {
            "type": "relation",
            "id": 20,
            "members": [{"type": "way", "ref": 10, "role": "outer"}, {"type": "way", "ref": 12, "role": "inner"}],
            "tags": {"type": "multipolygon", "natural": "water"},
        },
    ],
}


def spec(**kw) -> FrameSpec:
    return FrameSpec(center_lat=50.0, center_lon=8.0, **kw)


# --- query -------------------------------------------------------------


def test_build_query_simple_only_buildings():
    q = build_query((49.9, 7.9, 50.1, 8.1), Mode.simple)
    assert 'way["building"](49.900000,7.900000,50.100000,8.100000);' in q
    assert 'relation["building"]["type"="multipolygon"]' in q
    assert "highway" not in q and "water" not in q
    assert q.strip().endswith("out skel qt;")


def test_build_query_full_adds_roads_and_water():
    q = build_query((49.9, 7.9, 50.1, 8.1), Mode.full)
    assert 'way["highway"~"^(motorway|trunk|primary|secondary|tertiary|residential|unclassified|living_street|pedestrian|service)$"]' in q
    assert 'way["natural"="water"]' in q and 'relation["waterway"="riverbank"]' in q


# --- height ------------------------------------------------------------


@pytest.mark.parametrize(
    "tags,expected",
    [
        ({"height": "12"}, 12.0),
        ({"height": "12.5 m"}, 12.5),
        ({"height": "40m"}, 40.0),
        ({"building:levels": "5"}, 16.0),
        ({"height": "tall", "building:levels": "2"}, 6.4),
        ({"height": "abc", "building:levels": "x"}, 8.0),
        ({}, 8.0),
    ],
)
def test_parse_height(tags, expected):
    assert parse_height(tags, default_m=8.0) == pytest.approx(expected)


# --- parsing -----------------------------------------------------------


def test_parse_overpass_extracts_buildings_roads_water():
    feats = parse_overpass(SAMPLE, spec(mode=Mode.full))
    assert len(feats.buildings) == 1
    assert feats.buildings[0].height_m == 12.0
    assert feats.buildings[0].geom.geom_type == "Polygon"
    assert [r.cls for r in feats.roads] == ["residential"]  # footway ignored
    assert len(feats.water) == 1
    assert feats.water[0].geom.geom_type == "MultiPolygon"


def test_parse_overpass_keeps_building_that_is_also_relation_member():
    # way 10 is both a tagged building and the outer ring of the water relation
    feats = parse_overpass(SAMPLE, spec())
    assert len(feats.buildings) == 1


# --- http + cache ------------------------------------------------------


def make_client(responses: list[int | dict], calls: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.content.decode())
        r = responses.pop(0)
        if isinstance(r, int):
            return httpx.Response(r, text="error")
        return httpx.Response(200, json=r)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_overpass_uses_cache(tmp_path):
    calls: list[str] = []
    client = make_client([SAMPLE], calls)
    first = fetch_overpass("q1", tmp_path, client=client)
    second = fetch_overpass("q1", tmp_path, client=client)
    assert first == second == SAMPLE
    assert len(calls) == 1
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_fetch_overpass_retries_on_429(tmp_path):
    calls: list[str] = []
    sleeps: list[float] = []
    client = make_client([429, SAMPLE], calls)
    data = fetch_overpass("q2", tmp_path, client=client, sleep=sleeps.append)
    assert data == SAMPLE
    assert sleeps == [2.0]


def test_fetch_overpass_gives_up_after_retries(tmp_path):
    sleeps: list[float] = []
    client = make_client([504, 504, 504], [])
    with pytest.raises(FetchError, match="Overpass"):
        fetch_overpass("q3", tmp_path, client=client, sleep=sleeps.append)
    assert sleeps == [2.0, 4.0, 8.0]
    assert list(tmp_path.glob("*.json")) == []


def test_fetch_overpass_does_not_retry_client_errors(tmp_path):
    sleeps: list[float] = []
    client = make_client([400], [])
    with pytest.raises(FetchError):
        fetch_overpass("q4", tmp_path, client=client, sleep=sleeps.append)
    assert sleeps == []


def test_fetch_overpass_retries_on_network_error(tmp_path):
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json=SAMPLE)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch_overpass("q5", tmp_path, client=client, sleep=lambda s: None) == SAMPLE


# --- recorded fixture --------------------------------------------------


def test_frankfurt_fixture_parses(frankfurt_spec, frankfurt_data):
    feats = parse_overpass(frankfurt_data, frankfurt_spec)
    assert len(feats.buildings) > 20
    assert len(feats.roads) > 5
    assert len(feats.water) >= 1
```

- [ ] **Step 3: conftest anlegen**

`backend/tests/conftest.py`:

```python
import json
from pathlib import Path

import pytest

from skylineframe.spec import FrameSpec, Mode

FIXTURES = Path(__file__).parent / "fixtures"

# Same parameters as fixtures/record_frankfurt.py — keep in sync.
FRANKFURT = FrameSpec(center_lat=50.1090, center_lon=8.6820, side_m=400, mode=Mode.full)


@pytest.fixture
def frankfurt_spec() -> FrameSpec:
    return FRANKFURT.model_copy()


@pytest.fixture
def frankfurt_data() -> dict:
    return json.loads((FIXTURES / "frankfurt_roemer.json").read_text())
```

- [ ] **Step 4: Fehlschlag bestätigen**

Run: `cd backend && uv run pytest tests/test_fetch.py -q`
Expected: FAIL mit `ModuleNotFoundError: No module named 'skylineframe.fetch'`

- [ ] **Step 5: `fetch.py` implementieren**

```python
"""Overpass API access: query building, disk cache with retries, and parsing into Features."""

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import osm2geojson
from shapely.geometry import shape

from .errors import FetchError
from .features import Building, Features, Road, Water
from .project import query_bbox
from .spec import LEVEL_HEIGHT_M, ROAD_CLASSES, FrameSpec, Mode

DEFAULT_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
RETRY_STATUSES = {429, 502, 503, 504}
WATER_SELECTORS = (
    '["natural"="water"]',
    '["waterway"="riverbank"]',
    '["landuse"="reservoir"]',
    '["natural"="bay"]',
)


def build_query(bbox: tuple[float, float, float, float], mode: Mode) -> str:
    south, west, north, east = bbox
    bb = f"({south:.6f},{west:.6f},{north:.6f},{east:.6f})"
    parts = [f'way["building"]{bb};', f'relation["building"]["type"="multipolygon"]{bb};']
    if mode == Mode.full:
        classes = "|".join(ROAD_CLASSES)
        parts.append(f'way["highway"~"^({classes})$"]{bb};')
        for selector in WATER_SELECTORS:
            parts.append(f"way{selector}{bb};")
            parts.append(f"relation{selector}{bb};")
    body = "\n  ".join(parts)
    return f"[out:json][timeout:90];\n(\n  {body}\n);\nout body;\n>;\nout skel qt;\n"


def parse_height(tags: dict, default_m: float) -> float:
    raw = tags.get("height")
    if raw:
        try:
            return float(raw.strip().removesuffix("m").strip())
        except ValueError:
            pass
    levels = tags.get("building:levels")
    if levels:
        try:
            return float(levels) * LEVEL_HEIGHT_M
        except ValueError:
            pass
    return default_m


def _is_water(tags: dict) -> bool:
    return (
        tags.get("natural") in ("water", "bay")
        or tags.get("waterway") == "riverbank"
        or tags.get("landuse") == "reservoir"
    )


def parse_overpass(data: dict, spec: FrameSpec) -> Features:
    # filter_used_refs=False keeps tagged ways that are also relation members (e.g. a building
    # that is the outer ring of a multipolygon); untagged members are dropped below.
    geojson = osm2geojson.json2geojson(data, filter_used_refs=False, log_level="ERROR")
    feats = Features()
    for feature in geojson["features"]:
        tags = feature["properties"].get("tags") or {}
        if not tags:
            continue
        geom = shape(feature["geometry"])
        kind = geom.geom_type
        if "building" in tags and kind in ("Polygon", "MultiPolygon"):
            feats.buildings.append(Building(geom, parse_height(tags, spec.default_building_height_m)))
        elif tags.get("highway") in ROAD_CLASSES and kind in ("LineString", "MultiLineString"):
            feats.roads.append(Road(geom, tags["highway"]))
        elif _is_water(tags) and kind in ("Polygon", "MultiPolygon"):
            feats.water.append(Water(geom))
    return feats


def _cache_path(cache_dir: Path, query: str) -> Path:
    return cache_dir / (hashlib.sha256(query.encode()).hexdigest() + ".json")


def fetch_overpass(
    query: str,
    cache_dir: Path,
    url: str = DEFAULT_OVERPASS_URL,
    client: httpx.Client | None = None,
    retries: int = 3,
    backoff_s: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, query)
    if path.exists():
        return json.loads(path.read_text())

    owns_client = client is None
    client = client or httpx.Client(timeout=120)
    last_error = "no attempt made"
    try:
        for attempt in range(retries):
            try:
                response = client.post(url, data={"data": query})
            except httpx.TransportError as exc:
                last_error = f"network error: {exc}"
            else:
                if response.status_code == 200:
                    data = response.json()
                    path.write_text(json.dumps(data))
                    return data
                last_error = f"HTTP {response.status_code}"
                if response.status_code not in RETRY_STATUSES:
                    break
            sleep(backoff_s * 2**attempt)
    finally:
        if owns_client:
            client.close()
    raise FetchError(f"Overpass request failed ({last_error}). Please try again in a minute.")


def fetch_features(
    spec: FrameSpec,
    cache_dir: Path,
    url: str = DEFAULT_OVERPASS_URL,
    client: httpx.Client | None = None,
) -> Features:
    query = build_query(query_bbox(spec), spec.mode)
    return parse_overpass(fetch_overpass(query, cache_dir, url=url, client=client), spec)
```

- [ ] **Step 6: Fixture-Recorder anlegen und einmalig ausführen (Netz nötig)**

`backend/tests/fixtures/record_frankfurt.py`:

```python
"""Record the Overpass response used by offline tests. Run once, with network:

    cd backend && uv run python tests/fixtures/record_frankfurt.py
"""

import json
import shutil
from pathlib import Path

from skylineframe.fetch import build_query, fetch_overpass
from skylineframe.project import query_bbox
from skylineframe.spec import FrameSpec, Mode

# Keep in sync with tests/conftest.py::FRANKFURT (Römer, north bank of the Main).
FRANKFURT = FrameSpec(center_lat=50.1090, center_lon=8.6820, side_m=400, mode=Mode.full)

here = Path(__file__).parent
tmp = here / "_tmp_cache"
data = fetch_overpass(build_query(query_bbox(FRANKFURT), FRANKFURT.mode), cache_dir=tmp)
(here / "frankfurt_roemer.json").write_text(json.dumps(data))
shutil.rmtree(tmp)
print(f"recorded {len(data['elements'])} elements")
```

Run: `cd backend && uv run python tests/fixtures/record_frankfurt.py`
Expected: `recorded <n> elements` mit n > 500, Datei `tests/fixtures/frankfurt_roemer.json` existiert (typisch 1–3 MB).

- [ ] **Step 7: Tests grün**

Run: `cd backend && uv run pytest tests/test_fetch.py -q`
Expected: alle Tests bestehen (`18 passed`).

- [ ] **Step 8: Commit**

```bash
git add backend/skylineframe/errors.py backend/skylineframe/fetch.py backend/tests
git commit -m "feat(backend): Overpass fetch with cache, retries and parsing; record Frankfurt fixture

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Clipping, Reparatur und Flächenaufbereitung (`prepare.py`)

**Files:**
- Create: `backend/skylineframe/prepare.py`
- Test: `backend/tests/test_prepare.py`

**Interfaces:**
- Consumes: `FrameSpec`, `Mode`, `MIN_FEATURE_MM` (Task 1); `Features`, `Building` (Task 2); `square_local` (Task 2).
- Produces: `Prepared(buildings: list[Building], roads: list[Polygon], water: list[Polygon])` mit Geometrien in lokalen Metern, jedes `geom` ein gültiges `Polygon`; `polygons_of(geom) -> list[Polygon]`; `prepare(features: Features, spec) -> Prepared`; Konstante `SIMPLIFY_TOLERANCE_MM = 0.05`.

- [ ] **Step 1: Failing Tests schreiben**

`backend/tests/test_prepare.py`:

```python
import pytest
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from skylineframe.features import Building, Features, Road, Water
from skylineframe.prepare import polygons_of, prepare
from skylineframe.spec import FrameSpec, Mode


def spec(**kw) -> FrameSpec:
    # scale = 0.1 mm per metre
    return FrameSpec(center_lat=50, center_lon=8, side_m=1000, plate_size_mm=100, **kw)


def bld(geom, h=10.0) -> Building:
    return Building(geom, h)


def test_polygons_of_repairs_bowtie():
    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])
    parts = polygons_of(bowtie)
    assert len(parts) == 2
    assert all(p.is_valid and p.area > 0 for p in parts)


def test_polygons_of_drops_lines_and_empty():
    assert polygons_of(LineString([(0, 0), (1, 1)])) == []
    assert polygons_of(Polygon()) == []
    assert polygons_of(None) == []


def test_building_crossing_edge_is_clipped():
    feats = Features(buildings=[bld(box(400, -50, 600, 50))])
    out = prepare(feats, spec())
    assert len(out.buildings) == 1
    assert out.buildings[0].geom.bounds == pytest.approx((400, -50, 500, 50))
    assert out.buildings[0].height_m == 10.0


def test_building_outside_square_is_dropped():
    out = prepare(Features(buildings=[bld(box(600, 600, 700, 700))]), spec())
    assert out.buildings == []


def test_tiny_footprint_is_dropped():
    # 1 mm² at scale 0.1 => 100 m² threshold
    feats = Features(buildings=[bld(box(0, 0, 5, 5)), bld(box(20, 20, 40, 40))])
    out = prepare(feats, spec())
    assert len(out.buildings) == 1
    assert out.buildings[0].geom.area == pytest.approx(400)


def test_multipolygon_building_is_split():
    mp = unary_union([box(0, 0, 20, 20), box(50, 50, 70, 70)])
    out = prepare(Features(buildings=[bld(mp, 7.0)]), spec())
    assert len(out.buildings) == 2
    assert {b.height_m for b in out.buildings} == {7.0}


def test_simple_mode_ignores_roads_and_water():
    feats = Features(
        buildings=[bld(box(0, 0, 20, 20))],
        roads=[Road(LineString([(-100, 0), (100, 0)]), "residential")],
        water=[Water(box(-100, -100, -50, -50))],
    )
    out = prepare(feats, spec(mode=Mode.simple))
    assert out.roads == [] and out.water == []


def test_road_is_buffered_to_class_width():
    # residential = 1.0 mm => 10 m wide in the city => ±5 m
    feats = Features(buildings=[bld(box(200, 200, 220, 220))], roads=[Road(LineString([(-100, 0), (100, 0)]), "residential")])
    out = prepare(feats, spec(mode=Mode.full))
    area = unary_union(out.roads)
    assert area.bounds == pytest.approx((-100, -5, 100, 5), abs=0.01)


def test_road_is_clipped_to_square():
    feats = Features(buildings=[bld(box(200, 200, 220, 220))], roads=[Road(LineString([(-900, 0), (900, 0)]), "primary")])
    out = prepare(feats, spec(mode=Mode.full))
    assert unary_union(out.roads).bounds[0] == pytest.approx(-500)
    assert unary_union(out.roads).bounds[2] == pytest.approx(500)


def test_road_is_cut_out_under_building():
    feats = Features(buildings=[bld(box(-10, -10, 10, 10))], roads=[Road(LineString([(-100, 0), (100, 0)]), "residential")])
    out = prepare(feats, spec(mode=Mode.full))
    overlap = unary_union(out.roads).intersection(box(-10, -10, 10, 10)).area
    assert overlap == pytest.approx(0, abs=1e-6)


def test_water_is_cut_out_under_buildings_and_roads():
    feats = Features(
        buildings=[bld(box(-10, -10, 10, 10))],
        roads=[Road(LineString([(-100, 50), (100, 50)]), "residential")],
        water=[Water(box(-100, -100, 100, 100))],
    )
    out = prepare(feats, spec(mode=Mode.full))
    water = unary_union(out.water)
    assert water.intersection(box(-10, -10, 10, 10)).area == pytest.approx(0, abs=1e-6)
    assert water.intersection(unary_union(out.roads)).area == pytest.approx(0, abs=1e-6)
    assert water.area == pytest.approx(200 * 200 - 20 * 20 - 200 * 10, abs=1.0)


def test_water_is_clipped_to_square():
    feats = Features(buildings=[bld(box(0, 0, 20, 20))], water=[Water(box(-2000, -2000, 2000, -400))])
    out = prepare(feats, spec(mode=Mode.full))
    assert unary_union(out.water).bounds == pytest.approx((-500, -500, 500, -400))
```

- [ ] **Step 2: Fehlschlag bestätigen**

Run: `cd backend && uv run pytest tests/test_prepare.py -q`
Expected: FAIL mit `ModuleNotFoundError: No module named 'skylineframe.prepare'`

- [ ] **Step 3: `prepare.py` implementieren**

```python
"""Clip features to the target square, repair geometry and derive road/water areas (local metres)."""

from dataclasses import dataclass, field

from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import make_valid

from .features import Building, Features, Road, Water
from .project import square_local
from .spec import MIN_FEATURE_MM, FrameSpec, Mode

SIMPLIFY_TOLERANCE_MM = 0.05


@dataclass
class Prepared:
    buildings: list[Building]  # each geom is a valid Polygon in local metres
    roads: list[Polygon] = field(default_factory=list)
    water: list[Polygon] = field(default_factory=list)


def polygons_of(geom: BaseGeometry | None) -> list[Polygon]:
    """Repair and flatten any geometry into a list of non-empty, valid Polygons."""
    if geom is None or geom.is_empty:
        return []
    geom = make_valid(geom)
    if isinstance(geom, Polygon):
        parts = [geom]
    elif isinstance(geom, (MultiPolygon, GeometryCollection)):
        parts = []
        for g in geom.geoms:
            if isinstance(g, Polygon):
                parts.append(g)
            elif isinstance(g, MultiPolygon):
                parts.extend(g.geoms)
    else:
        return []
    return [p for p in parts if not p.is_empty and p.area > 0]


def _clip_buildings(buildings: list[Building], square: Polygon, min_area_m2: float, tol_m: float) -> list[Building]:
    out: list[Building] = []
    for b in buildings:
        for poly in polygons_of(b.geom.intersection(square)):
            poly = poly.simplify(tol_m, preserve_topology=True)
            if poly.area >= min_area_m2:
                out.append(Building(poly, b.height_m))
    return out


def _road_areas(roads: list[Road], spec: FrameSpec, square: Polygon, blocked: BaseGeometry, min_area_m2: float) -> list[Polygon]:
    if not roads:
        return []
    buffered = [
        r.geom.buffer(spec.road_width_mm[r.cls] / spec.scale / 2, cap_style="flat", join_style="round")
        for r in roads
    ]
    area = unary_union(buffered).intersection(square).difference(blocked)
    return [p for p in polygons_of(area) if p.area >= min_area_m2]


def _water_areas(water: list[Water], square: Polygon, blocked: BaseGeometry, min_area_m2: float) -> list[Polygon]:
    if not water:
        return []
    area = unary_union([w.geom for w in water]).intersection(square).difference(blocked)
    return [p for p in polygons_of(area) if p.area >= min_area_m2]


def prepare(features: Features, spec: FrameSpec) -> Prepared:
    square = square_local(spec)
    scale = spec.scale
    buildings = _clip_buildings(
        features.buildings,
        square,
        min_area_m2=spec.min_footprint_area_mm2 / scale**2,
        tol_m=SIMPLIFY_TOLERANCE_MM / scale,
    )
    if spec.mode != Mode.full:
        return Prepared(buildings=buildings)

    min_area_m2 = MIN_FEATURE_MM**2 / scale**2
    building_union = unary_union([b.geom for b in buildings]) if buildings else Polygon()
    roads = _road_areas(features.roads, spec, square, building_union, min_area_m2)
    blocked_for_water = unary_union([building_union, *roads])
    water = _water_areas(features.water, square, blocked_for_water, min_area_m2)
    return Prepared(buildings=buildings, roads=roads, water=water)
```

- [ ] **Step 4: Tests grün**

Run: `cd backend && uv run pytest tests/test_prepare.py -q`
Expected: `12 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/skylineframe/prepare.py backend/tests/test_prepare.py
git commit -m "feat(backend): clip, repair and derive road/water areas

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Skalierung Meter → Millimeter (`scale.py`)

**Files:**
- Create: `backend/skylineframe/scale.py`
- Test: `backend/tests/test_scale.py`

**Interfaces:**
- Consumes: `FrameSpec` (Task 1); `Prepared`, `Building` (Task 4).
- Produces: `Prism(geom: Polygon, height_mm: float)`, `Scaled(buildings: list[Prism], roads: list[Polygon], water: list[Polygon])` in Millimetern, zentriert um (0, 0); `building_height_mm(height_m, spec) -> float`; `scale_features(prepared, spec) -> Scaled`.

- [ ] **Step 1: Failing Tests schreiben**

`backend/tests/test_scale.py`:

```python
import pytest
from shapely.geometry import box

from skylineframe.features import Building
from skylineframe.prepare import Prepared
from skylineframe.scale import building_height_mm, scale_features
from skylineframe.spec import FrameSpec


def spec(**kw) -> FrameSpec:
    return FrameSpec(center_lat=50, center_lon=8, side_m=1000, plate_size_mm=100, **kw)  # 0.1 mm/m


def test_height_applies_scale_and_exaggeration():
    assert building_height_mm(10.0, spec(z_exaggeration=1.5)) == pytest.approx(1.5)


def test_height_enforces_minimum():
    assert building_height_mm(2.0, spec(min_building_height_mm=0.8)) == pytest.approx(0.8)


def test_height_rounds_to_hundredth():
    assert building_height_mm(3.333, spec(z_exaggeration=1.0)) == pytest.approx(0.8)  # 0.3333 -> min
    assert building_height_mm(12.345, spec(z_exaggeration=1.0)) == pytest.approx(1.23)


def test_footprints_are_scaled_about_origin():
    prepared = Prepared(buildings=[Building(box(-500, -500, 500, 500), 10.0)])
    out = scale_features(prepared, spec())
    assert out.buildings[0].geom.bounds == pytest.approx((-50, -50, 50, 50))
    assert out.buildings[0].height_mm == pytest.approx(1.5)


def test_roads_and_water_are_scaled():
    prepared = Prepared(buildings=[], roads=[box(0, 0, 100, 10)], water=[box(-500, -500, 0, 0)])
    out = scale_features(prepared, spec())
    assert out.roads[0].bounds == pytest.approx((0, 0, 10, 1))
    assert out.water[0].bounds == pytest.approx((-50, -50, 0, 0))
```

- [ ] **Step 2: Fehlschlag bestätigen**

Run: `cd backend && uv run pytest tests/test_scale.py -q`
Expected: FAIL mit `ModuleNotFoundError: No module named 'skylineframe.scale'`

- [ ] **Step 3: `scale.py` implementieren**

```python
"""Convert prepared features from local metres to print millimetres."""

from dataclasses import dataclass, field

import shapely.affinity
from shapely.geometry import Polygon

from .prepare import Prepared
from .spec import FrameSpec


@dataclass
class Prism:
    geom: Polygon  # footprint in mm, centred on the plate
    height_mm: float


@dataclass
class Scaled:
    buildings: list[Prism]
    roads: list[Polygon] = field(default_factory=list)
    water: list[Polygon] = field(default_factory=list)


def building_height_mm(height_m: float, spec: FrameSpec) -> float:
    raw = round(height_m * spec.scale * spec.z_exaggeration, 2)
    return max(raw, spec.min_building_height_mm)


def _scale_geom(geom: Polygon, factor: float) -> Polygon:
    return shapely.affinity.scale(geom, xfact=factor, yfact=factor, origin=(0, 0))


def scale_features(prepared: Prepared, spec: FrameSpec) -> Scaled:
    s = spec.scale
    return Scaled(
        buildings=[Prism(_scale_geom(b.geom, s), building_height_mm(b.height_m, spec)) for b in prepared.buildings],
        roads=[_scale_geom(p, s) for p in prepared.roads],
        water=[_scale_geom(p, s) for p in prepared.water],
    )
```

- [ ] **Step 4: Tests grün**

Run: `cd backend && uv run pytest tests/test_scale.py -q`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/skylineframe/scale.py backend/tests/test_scale.py
git commit -m "feat(backend): scale features to print millimetres

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Mesh-Erzeugung mit manifold3d (`mesh.py`)

**Files:**
- Create: `backend/skylineframe/mesh.py`
- Modify: `backend/skylineframe/errors.py` (MeshError ergänzen)
- Test: `backend/tests/test_mesh.py`

**Interfaces:**
- Consumes: `FrameSpec` (Task 1); `Scaled`, `Prism` (Task 5); `SkylineError` (Task 3).
- Produces: `MeshError(SkylineError)`; `MeshSet(base, buildings, single, water=None, roads=None)` mit `manifold3d.Manifold`-Werten und Methode `parts() -> dict[str, Manifold]` (Reihenfolge base, buildings, water, roads; None-Einträge fehlen); `build_meshes(scaled, spec) -> MeshSet`; `to_trimesh(manifold) -> trimesh.Trimesh`; Konstanten `EPS = 0.01`, `BUILDING_SINK_MM = 0.2`.

- [ ] **Step 1: `MeshError` in `errors.py` ergänzen**

An `backend/skylineframe/errors.py` anhängen:

```python


class MeshError(SkylineError):
    """The geometry could not be turned into a valid solid."""
```

- [ ] **Step 2: Failing Tests schreiben**

`backend/tests/test_mesh.py`:

```python
import pytest
from shapely.geometry import Polygon, box

from skylineframe.errors import MeshError
from skylineframe.mesh import BUILDING_SINK_MM, build_meshes, to_trimesh
from skylineframe.scale import Prism, Scaled
from skylineframe.spec import FrameSpec, Mode

PLATE_VOLUME = 100 * 100 * 3


def spec(**kw) -> FrameSpec:
    return FrameSpec(center_lat=50, center_lon=8, side_m=1000, plate_size_mm=100, plate_thickness_mm=3.0, mode=Mode.full, **kw)


def test_single_building_adds_its_volume_to_plate():
    ms = build_meshes(Scaled(buildings=[Prism(box(-5, -5, 5, 5), 10.0)]), spec())
    assert ms.single.volume() == pytest.approx(PLATE_VOLUME + 100 * 10, rel=1e-6)
    assert ms.buildings.volume() == pytest.approx(100 * (10 + BUILDING_SINK_MM), rel=1e-6)
    assert ms.water is None and ms.roads is None
    assert list(ms.parts()) == ["base", "buildings"]


def test_building_with_hole():
    ring = Polygon([(-5, -5), (5, -5), (5, 5), (-5, 5)], holes=[[(-1, -1), (1, -1), (1, 1), (-1, 1)]])
    ms = build_meshes(Scaled(buildings=[Prism(ring, 5.0)]), spec())
    assert ms.single.volume() == pytest.approx(PLATE_VOLUME + 96 * 5, rel=1e-6)


def test_overlapping_buildings_do_not_double_count():
    ms = build_meshes(Scaled(buildings=[Prism(box(0, 0, 10, 10), 4.0), Prism(box(5, 0, 15, 10), 8.0)]), spec())
    expected = 100 * 4 + 100 * 8 - 50 * 4  # overlap counted once, at the taller height
    assert ms.single.volume() == pytest.approx(PLATE_VOLUME + expected, rel=1e-6)


def test_single_bounding_box_matches_plate():
    ms = build_meshes(Scaled(buildings=[Prism(box(40, 40, 50, 50), 3.0)]), spec())
    lo, hi = ms.single.bounding_box()
    assert lo[0] == pytest.approx(-50) and hi[0] == pytest.approx(50)
    assert lo[1] == pytest.approx(-50) and hi[1] == pytest.approx(50)
    assert lo[2] == pytest.approx(-3.0) and hi[2] == pytest.approx(3.0)


def test_road_recess_is_cut_and_inlay_fills_it():
    road = box(-50, -0.5, 50, 0.5)
    ms = build_meshes(Scaled(buildings=[Prism(box(20, 20, 30, 30), 2.0)], roads=[road]), spec())
    assert ms.base.volume() == pytest.approx(PLATE_VOLUME - 100 * 1 * 0.4, rel=1e-6)
    assert ms.roads.volume() == pytest.approx(100 * 1 * 0.4, rel=1e-6)
    assert (ms.base + ms.roads).volume() == pytest.approx(PLATE_VOLUME, rel=1e-6)
    assert ms.single.volume() == pytest.approx(PLATE_VOLUME - 40 + 100 * 2, rel=1e-6)
    assert list(ms.parts()) == ["base", "buildings", "roads"]


def test_water_recess_uses_water_depth():
    water = box(-50, -50, 0, 0)
    ms = build_meshes(Scaled(buildings=[Prism(box(20, 20, 30, 30), 2.0)], water=[water]), spec())
    assert ms.water.volume() == pytest.approx(2500 * 0.6, rel=1e-6)
    assert (ms.base + ms.water).volume() == pytest.approx(PLATE_VOLUME, rel=1e-6)
    assert list(ms.parts()) == ["base", "buildings", "water"]


def test_no_buildings_raises():
    with pytest.raises(MeshError, match="No buildings"):
        build_meshes(Scaled(buildings=[]), spec())


def test_to_trimesh_is_watertight_volume():
    ms = build_meshes(Scaled(buildings=[Prism(box(-5, -5, 5, 5), 10.0)], roads=[box(-50, 10, 50, 11)]), spec())
    tm = to_trimesh(ms.single)
    assert tm.is_watertight and tm.is_volume
    assert tm.volume == pytest.approx(ms.single.volume(), rel=1e-6)
```

- [ ] **Step 3: Fehlschlag bestätigen**

Run: `cd backend && uv run pytest tests/test_mesh.py -q`
Expected: FAIL mit `ModuleNotFoundError: No module named 'skylineframe.mesh'`

- [ ] **Step 4: `mesh.py` implementieren**

```python
"""Solid modelling with manifold3d. Plate top is z = 0; buildings rise above, recesses go below."""

from dataclasses import dataclass

import manifold3d as m3d
import numpy as np
import trimesh
from shapely.geometry import Polygon

from .errors import MeshError
from .scale import Scaled
from .spec import FrameSpec

EPS = 0.01  # overshoot of cutters above the plate top for a clean boolean cut
BUILDING_SINK_MM = 0.2  # buildings are sunk into the plate so the union never relies on face contact only


@dataclass
class MeshSet:
    base: m3d.Manifold
    buildings: m3d.Manifold
    single: m3d.Manifold
    water: m3d.Manifold | None = None
    roads: m3d.Manifold | None = None

    def parts(self) -> dict[str, m3d.Manifold]:
        parts = {"base": self.base, "buildings": self.buildings}
        if self.water is not None:
            parts["water"] = self.water
        if self.roads is not None:
            parts["roads"] = self.roads
        return parts


def cross_section(poly: Polygon) -> m3d.CrossSection:
    rings = [list(poly.exterior.coords)[:-1]] + [list(ring.coords)[:-1] for ring in poly.interiors]
    return m3d.CrossSection(rings, m3d.FillRule.EvenOdd)


def prism(poly: Polygon, height: float, z0: float = 0.0) -> m3d.Manifold:
    return cross_section(poly).extrude(height).translate((0, 0, z0))


def union(parts: list[m3d.Manifold]) -> m3d.Manifold:
    if len(parts) == 1:
        return parts[0]
    return m3d.Manifold.batch_boolean(parts, m3d.OpType.Add)


def _check(man: m3d.Manifold, name: str) -> m3d.Manifold:
    if man.status() != m3d.Error.NoError:
        raise MeshError(f"{name}: manifold error {man.status()}")
    if man.is_empty() or man.volume() <= 0:
        raise MeshError(f"{name}: resulting solid is empty")
    return man


def plate(spec: FrameSpec) -> m3d.Manifold:
    size, t = spec.plate_size_mm, spec.plate_thickness_mm
    return m3d.Manifold.cube((size, size, t), center=True).translate((0, 0, -t / 2))


def recess(polys: list[Polygon], depth: float) -> tuple[m3d.Manifold, m3d.Manifold]:
    """Return (cutter, inlay). The cutter overshoots above z=0; the inlay fills the recess exactly."""
    cutter = union([prism(p, depth + EPS, z0=-depth) for p in polys])
    inlay = union([prism(p, depth, z0=-depth) for p in polys])
    return cutter, inlay


def build_meshes(scaled: Scaled, spec: FrameSpec) -> MeshSet:
    if not scaled.buildings:
        raise MeshError("No buildings in the selected area.")

    base = plate(spec)
    buildings = _check(
        union([prism(b.geom, b.height_mm + BUILDING_SINK_MM, z0=-BUILDING_SINK_MM) for b in scaled.buildings]),
        "buildings",
    )

    water = roads = None
    if scaled.water:
        cutter, water = recess(scaled.water, spec.water_depth_mm)
        base = base - cutter
        _check(water, "water")
    if scaled.roads:
        cutter, roads = recess(scaled.roads, spec.road_depth_mm)
        base = base - cutter
        _check(roads, "roads")
    _check(base, "base")

    single = _check(base + buildings, "single")
    return MeshSet(base=base, buildings=buildings, single=single, water=water, roads=roads)


def to_trimesh(man: m3d.Manifold) -> trimesh.Trimesh:
    mesh = man.to_mesh()
    vertices = np.asarray(mesh.vert_properties)[:, :3]
    faces = np.asarray(mesh.tri_verts)
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
```

- [ ] **Step 5: Tests grün**

Run: `cd backend && uv run pytest tests/test_mesh.py -q`
Expected: `8 passed`

- [ ] **Step 6: Commit**

```bash
git add backend/skylineframe/mesh.py backend/skylineframe/errors.py backend/tests/test_mesh.py
git commit -m "feat(backend): build watertight solids with manifold3d

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Export STL / 3MF / GLB mit Verifikation (`export.py`)

**Files:**
- Create: `backend/skylineframe/export.py`
- Modify: `backend/skylineframe/errors.py` (ExportError ergänzen)
- Test: `backend/tests/test_export.py`

**Interfaces:**
- Consumes: `MeshSet`, `to_trimesh` (Task 6); `FrameSpec` (Task 1); `SkylineError` (Task 3).
- Produces: `ExportError(SkylineError)`; `ExportPaths(stl: Path, threemf: Path, glb: Path)`; `PART_COLORS: dict[str, tuple[int,int,int,int]]`; `verify_single(tm, spec) -> None`; `export_all(meshset, spec, out_dir: Path) -> ExportPaths`. Dateinamen: `model.stl`, `model.3mf`, `preview.glb`.

- [ ] **Step 1: `ExportError` in `errors.py` ergänzen**

```python


class ExportError(SkylineError):
    """The final mesh failed verification or could not be written."""
```

- [ ] **Step 2: Failing Tests schreiben**

`backend/tests/test_export.py`:

```python
import pytest
import trimesh
from shapely.geometry import box

from skylineframe.errors import ExportError
from skylineframe.export import export_all, verify_single
from skylineframe.mesh import build_meshes, to_trimesh
from skylineframe.scale import Prism, Scaled
from skylineframe.spec import FrameSpec, Mode


def spec(**kw) -> FrameSpec:
    return FrameSpec(center_lat=50, center_lon=8, side_m=1000, plate_size_mm=100, plate_thickness_mm=3.0, mode=Mode.full, **kw)


@pytest.fixture
def meshset():
    scaled = Scaled(
        buildings=[Prism(box(-5, -5, 5, 5), 10.0), Prism(box(40, 40, 50, 50), 2.0)],
        roads=[box(-50, 10, 50, 11)],
        water=[box(-50, -50, -20, -20)],
    )
    return build_meshes(scaled, spec())


def test_export_writes_three_files(tmp_path, meshset):
    paths = export_all(meshset, spec(), tmp_path)
    assert paths.stl == tmp_path / "model.stl"
    assert paths.threemf == tmp_path / "model.3mf"
    assert paths.glb == tmp_path / "preview.glb"
    for p in (paths.stl, paths.threemf, paths.glb):
        assert p.exists() and p.stat().st_size > 0


def test_stl_reloads_watertight_with_plate_extents(tmp_path, meshset):
    paths = export_all(meshset, spec(), tmp_path)
    tm = trimesh.load(paths.stl, file_type="stl")
    assert tm.is_watertight
    assert tm.extents[0] == pytest.approx(100, abs=0.01)
    assert tm.extents[1] == pytest.approx(100, abs=0.01)
    assert tm.extents[2] == pytest.approx(13.0, abs=0.01)


def test_3mf_contains_named_parts(tmp_path, meshset):
    paths = export_all(meshset, spec(), tmp_path)
    scene = trimesh.load(paths.threemf, file_type="3mf")
    assert set(scene.geometry) == {"base", "buildings", "water", "roads"}


def test_glb_contains_all_parts(tmp_path, meshset):
    paths = export_all(meshset, spec(), tmp_path)
    scene = trimesh.load(paths.glb, file_type="glb")
    assert len(scene.geometry) == 4


def test_verify_rejects_wrong_plate_size(meshset):
    tm = to_trimesh(meshset.single)
    with pytest.raises(ExportError, match="plate"):
        verify_single(tm, spec(plate_size_mm=120))


def test_verify_rejects_non_watertight(meshset):
    tm = to_trimesh(meshset.single)
    broken = trimesh.Trimesh(vertices=tm.vertices, faces=tm.faces[:-1], process=False)
    with pytest.raises(ExportError, match="watertight"):
        verify_single(broken, spec())
```

- [ ] **Step 3: Fehlschlag bestätigen**

Run: `cd backend && uv run pytest tests/test_export.py -q`
Expected: FAIL mit `ModuleNotFoundError: No module named 'skylineframe.export'`

- [ ] **Step 4: `export.py` implementieren**

```python
"""Write STL (single colour), 3MF (named parts) and GLB (coloured preview) after verifying the solid."""

from dataclasses import dataclass
from pathlib import Path

import trimesh

from .errors import ExportError
from .mesh import MeshSet, to_trimesh
from .spec import FrameSpec

PART_COLORS: dict[str, tuple[int, int, int, int]] = {
    "base": (200, 200, 200, 255),
    "buildings": (255, 255, 255, 255),
    "water": (70, 130, 220, 255),
    "roads": (90, 90, 90, 255),
}
SIZE_TOLERANCE_MM = 0.01


@dataclass
class ExportPaths:
    stl: Path
    threemf: Path
    glb: Path


def verify_single(tm: trimesh.Trimesh, spec: FrameSpec) -> None:
    if not tm.is_watertight or not tm.is_volume:
        raise ExportError("Resulting mesh is not watertight; please try a slightly different area.")
    size = spec.plate_size_mm
    if abs(tm.extents[0] - size) > SIZE_TOLERANCE_MM or abs(tm.extents[1] - size) > SIZE_TOLERANCE_MM:
        raise ExportError(f"Model footprint {tm.extents[0]:.2f}x{tm.extents[1]:.2f} mm does not match plate size {size} mm.")
    if tm.volume <= size * size * spec.plate_thickness_mm * 0.5:
        raise ExportError("Model has no volume above the plate.")


def export_all(meshset: MeshSet, spec: FrameSpec, out_dir: Path) -> ExportPaths:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = ExportPaths(stl=out_dir / "model.stl", threemf=out_dir / "model.3mf", glb=out_dir / "preview.glb")

    single = to_trimesh(meshset.single)
    verify_single(single, spec)
    single.export(str(paths.stl), file_type="stl")

    parts = {name: to_trimesh(man) for name, man in meshset.parts().items()}
    trimesh.Scene(parts).export(str(paths.threemf), file_type="3mf")

    for name, tm in parts.items():
        tm.visual.face_colors = PART_COLORS[name]
    trimesh.Scene(parts).export(str(paths.glb), file_type="glb")
    return paths
```

- [ ] **Step 5: Tests grün**

Run: `cd backend && uv run pytest tests/test_export.py -q`
Expected: `6 passed`

Hinweis: Das Volumen-Kriterium ist bewusst „mehr als die halbe Platte“, weil Vertiefungen das Plattenvolumen leicht reduzieren; ein Modell ohne Gebäude wird schon in `build_meshes` abgefangen.

- [ ] **Step 6: Commit**

```bash
git add backend/skylineframe/export.py backend/skylineframe/errors.py backend/tests/test_export.py
git commit -m "feat(backend): export STL/3MF/GLB with mesh verification

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Pipeline, CLI und Offline-End-to-End-Test

**Files:**
- Create: `backend/skylineframe/pipeline.py`
- Create: `backend/skylineframe/cli.py`
- Test: `backend/tests/test_pipeline.py`
- Test: `backend/tests/test_cli.py`

**Interfaces:**
- Consumes: alles aus Task 1–7; conftest-Fixtures `frankfurt_spec`, `frankfurt_data` (Task 3).
- Produces: `pipeline.py`: `ProgressCallback = Callable[[str, str], None]` (stage, message), `Stage`-Namen `fetch`, `prepare`, `mesh`, `export`; `RunResult(paths: ExportPaths, stats: dict[str, int])`; `run(spec, out_dir: Path, cache_dir: Path, progress: ProgressCallback | None = None, fetch: Callable[[FrameSpec, Path], Features] = fetch_features) -> RunResult`; `PipelineError(SkylineError)`. `cli.py`: typer-App `app` mit Kommando `generate`.

- [ ] **Step 1: Failing Tests schreiben**

`backend/tests/test_pipeline.py`:

```python
import pytest

from skylineframe.errors import PipelineError
from skylineframe.features import Features
from skylineframe.fetch import parse_overpass
from skylineframe.pipeline import run
from skylineframe.spec import FrameSpec, Mode


def test_run_frankfurt_offline(tmp_path, frankfurt_spec, frankfurt_data):
    stages: list[str] = []
    result = run(
        frankfurt_spec,
        out_dir=tmp_path / "out",
        cache_dir=tmp_path / "cache",
        progress=lambda stage, msg: stages.append(stage),
        fetch=lambda spec, cache_dir: parse_overpass(frankfurt_data, spec),
    )
    assert stages == ["fetch", "prepare", "mesh", "export"]
    assert result.paths.stl.exists() and result.paths.threemf.exists() and result.paths.glb.exists()
    assert result.stats["buildings"] > 20
    assert result.stats["roads"] >= 1
    assert result.stats["stl_bytes"] > 10_000


def test_run_simple_mode_has_no_roads(tmp_path, frankfurt_spec, frankfurt_data):
    spec = frankfurt_spec.model_copy(update={"mode": Mode.simple})
    result = run(spec, tmp_path / "out", tmp_path / "cache", fetch=lambda s, c: parse_overpass(frankfurt_data, s))
    assert result.stats["roads"] == 0 and result.stats["water"] == 0


def test_run_without_buildings_raises_pipeline_error(tmp_path):
    spec = FrameSpec(center_lat=0, center_lon=0)
    with pytest.raises(PipelineError, match="No buildings"):
        run(spec, tmp_path / "out", tmp_path / "cache", fetch=lambda s, c: Features())
```

`backend/tests/test_cli.py`:

```python
from pathlib import Path

from typer.testing import CliRunner

from skylineframe import cli
from skylineframe.errors import FetchError
from skylineframe.export import ExportPaths
from skylineframe.pipeline import RunResult

runner = CliRunner()


def test_generate_prints_paths(monkeypatch, tmp_path):
    captured = {}

    def fake_run(spec, out_dir, cache_dir, progress=None):
        captured["spec"] = spec
        if progress:
            progress("fetch", "loading")
        return RunResult(ExportPaths(out_dir / "model.stl", out_dir / "model.3mf", out_dir / "preview.glb"), {"buildings": 3})

    monkeypatch.setattr(cli, "run", fake_run)
    result = runner.invoke(cli.app, ["--lat", "50.1", "--lon", "8.6", "--side", "800", "--mode", "full", "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert captured["spec"].side_m == 800 and captured["spec"].mode == "full"
    assert "[fetch] loading" in result.output
    assert "model.stl" in result.output


def test_generate_reports_errors(monkeypatch, tmp_path):
    def failing_run(spec, out_dir, cache_dir, progress=None):
        raise FetchError("Overpass down")

    monkeypatch.setattr(cli, "run", failing_run)
    result = runner.invoke(cli.app, ["--lat", "50.1", "--lon", "8.6", "--out", str(tmp_path)])
    assert result.exit_code == 1
    assert "Overpass down" in result.output
```

- [ ] **Step 2: Fehlschlag bestätigen**

Run: `cd backend && uv run pytest tests/test_pipeline.py tests/test_cli.py -q`
Expected: FAIL mit `ModuleNotFoundError` (pipeline / cli).

- [ ] **Step 3: `PipelineError` ergänzen, `pipeline.py` und `cli.py` implementieren**

An `backend/skylineframe/errors.py` anhängen:

```python


class PipelineError(SkylineError):
    """The selected area cannot produce a model (e.g. no buildings)."""
```

`backend/skylineframe/pipeline.py`:

```python
"""Wire the stages together: fetch -> project -> prepare -> scale -> mesh -> export."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .errors import PipelineError
from .export import ExportPaths, export_all
from .features import Features
from .fetch import fetch_features
from .mesh import build_meshes
from .prepare import prepare
from .project import project_features
from .scale import scale_features
from .spec import FrameSpec

ProgressCallback = Callable[[str, str], None]
FetchFn = Callable[[FrameSpec, Path], Features]


@dataclass
class RunResult:
    paths: ExportPaths
    stats: dict[str, int]


def run(
    spec: FrameSpec,
    out_dir: Path,
    cache_dir: Path,
    progress: ProgressCallback | None = None,
    fetch: FetchFn = fetch_features,
) -> RunResult:
    def report(stage: str, message: str) -> None:
        if progress:
            progress(stage, message)

    report("fetch", "Loading OpenStreetMap data")
    raw = fetch(spec, cache_dir)

    report("prepare", "Clipping and cleaning geometry")
    prepared = prepare(project_features(raw, spec), spec)
    if not prepared.buildings:
        raise PipelineError("No buildings found in the selected area. Try a denser part of the city.")
    scaled = scale_features(prepared, spec)

    report("mesh", f"Building solids for {len(prepared.buildings)} buildings")
    meshes = build_meshes(scaled, spec)

    report("export", "Writing STL, 3MF and preview")
    paths = export_all(meshes, spec, out_dir)

    stats = {
        "buildings": len(prepared.buildings),
        "roads": len(prepared.roads),
        "water": len(prepared.water),
        "stl_bytes": paths.stl.stat().st_size,
        "threemf_bytes": paths.threemf.stat().st_size,
    }
    return RunResult(paths=paths, stats=stats)
```

`backend/skylineframe/cli.py`:

```python
"""Command line entry point: skylineframe --lat 50.11 --lon 8.68 --mode full --out ./out"""

from pathlib import Path
from typing import Annotated

import typer

from .errors import SkylineError
from .pipeline import run
from .spec import FrameSpec, Mode

app = typer.Typer(add_completion=False)


@app.command()
def generate(
    lat: Annotated[float, typer.Option(help="Centre latitude (WGS84)")],
    lon: Annotated[float, typer.Option(help="Centre longitude (WGS84)")],
    side: Annotated[float, typer.Option(help="Edge length of the city square in metres")] = 1500,
    plate: Annotated[float, typer.Option(help="Plate edge length in mm")] = 100,
    thickness: Annotated[float, typer.Option(help="Plate thickness in mm")] = 3.0,
    mode: Annotated[Mode, typer.Option(help="simple = buildings only, full = roads and water too")] = Mode.simple,
    rotation: Annotated[float, typer.Option(help="Clockwise rotation of the square in degrees")] = 0,
    z: Annotated[float, typer.Option(help="Height exaggeration factor")] = 1.5,
    out: Annotated[Path, typer.Option(help="Output directory")] = Path("out"),
    cache: Annotated[Path, typer.Option(help="Overpass cache directory")] = Path(".cache/overpass"),
) -> None:
    spec = FrameSpec(
        center_lat=lat,
        center_lon=lon,
        side_m=side,
        plate_size_mm=plate,
        plate_thickness_mm=thickness,
        mode=mode,
        rotation_deg=rotation,
        z_exaggeration=z,
    )
    try:
        result = run(spec, out, cache, progress=lambda stage, msg: typer.echo(f"[{stage}] {msg}"))
    except SkylineError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Buildings: {result.stats.get('buildings', 0)}")
    typer.echo(f"STL: {result.paths.stl}")
    typer.echo(f"3MF: {result.paths.threemf}")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Tests grün**

Run: `cd backend && uv run pytest -q`
Expected: alle Tests bestehen (Pipeline-Test mit Fixture dauert einige Sekunden).

Hinweis: Wenn `test_run_frankfurt_offline` mit `MeshError`/`ExportError` fehlschlägt, liegt ein echtes Geometrieproblem vor. Dann Ursache finden (z. B. `polygons_of` nach `simplify`, degenerierte Ringe mit < 3 Punkten vor `cross_section` filtern), nicht den Test lockern.

- [ ] **Step 5: CLI manuell prüfen (offline, nur Hilfe)**

Run: `cd backend && uv run skylineframe --help`
Expected: Hilfetext mit den Optionen `--lat`, `--lon`, `--side`, `--mode`.

- [ ] **Step 6: Commit**

```bash
git add backend/skylineframe backend/tests
git commit -m "feat(backend): pipeline runner, CLI and offline end-to-end test

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: FastAPI mit Hintergrund-Jobs

**Files:**
- Create: `backend/app/jobs.py`
- Create: `backend/app/main.py`
- Test: `backend/tests/test_jobs.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `FrameSpec` (Task 1), `SkylineError` (Task 3), `run`, `RunResult`, `ProgressCallback` (Task 8), `ExportPaths` (Task 7).
- Produces: `jobs.py`: `JOB_FILES = ("model.stl", "model.3mf", "preview.glb")`, `Job` (id, spec, dir, status, stage, message, stats, created_at, `to_dict()`), `Runner`-Typ `Callable[[FrameSpec, Path, Path, ProgressCallback], RunResult]`, `JobStore(root: Path, cache_dir: Path, runner=..., workers=2)` mit `create(spec) -> Job`, `get(id) -> Job | None`, `file(id, name) -> Path | None`, `cleanup(max_age_s=86400) -> int`. `main.py`: `create_app(store: JobStore | None = None, geocoder=None, frontend_dist: Path | None = None) -> FastAPI`, Modulvariable `app`. Endpoints: `POST /api/jobs` (202, `{"id"}`), `GET /api/jobs/{id}`, `GET /api/jobs/{id}/{filename}`.

- [ ] **Step 1: Failing Tests für JobStore**

`backend/tests/test_jobs.py`:

```python
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
```

- [ ] **Step 2: Failing Tests für die API**

`backend/tests/test_api.py`:

```python
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
```

- [ ] **Step 3: Fehlschlag bestätigen**

Run: `cd backend && uv run pytest tests/test_jobs.py tests/test_api.py -q`
Expected: FAIL mit `ModuleNotFoundError: No module named 'app.jobs'`

- [ ] **Step 4: `jobs.py` implementieren**

```python
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
        except SkylineError as exc:
            job.status, job.message = "error", str(exc)
        except Exception:
            log.exception("job %s crashed", job.id)
            job.status, job.message = "error", "Unexpected error during generation. See server log."
        else:
            job.stats = result.stats
            job.status, job.message = "done", "Ready"
```

- [ ] **Step 5: `main.py` implementieren**

```python
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


app = create_app()
```

Hinweis: `geocoder` wird in Task 10 mit einem echten `Geocoder` belegt; bis dahin ist der Endpoint nur vorhanden, wenn einer übergeben wird.

- [ ] **Step 6: Tests grün**

Run: `cd backend && uv run pytest tests/test_jobs.py tests/test_api.py -q`
Expected: `11 passed`

- [ ] **Step 7: Server manuell starten**

Run: `cd backend && uv run uvicorn app.main:app --port 8000` (in zweitem Terminal: `curl -s localhost:8000/api/jobs/x` → `{"detail":"job not found"}`), dann mit Ctrl+C beenden.

- [ ] **Step 8: Commit**

```bash
git add backend/app backend/tests/test_jobs.py backend/tests/test_api.py
git commit -m "feat(api): FastAPI job endpoints with background execution

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Geocoding über Nominatim

**Files:**
- Create: `backend/app/geocode.py`
- Modify: `backend/app/main.py` (Default-Geocoder einsetzen)
- Test: `backend/tests/test_geocode.py`

**Interfaces:**
- Consumes: `create_app` (Task 9).
- Produces: `GeocodeResult(name: str, lat: float, lon: float)`; `Geocoder(client: httpx.Client | None = None, ttl_s=600, min_interval_s=1.0, now=time.monotonic, sleep=time.sleep)` mit `search(q: str, limit: int = 5) -> list[GeocodeResult]`; Konstanten `NOMINATIM_URL`, `USER_AGENT`.

- [ ] **Step 1: Failing Tests schreiben**

`backend/tests/test_geocode.py`:

```python
import httpx
from fastapi.testclient import TestClient

from app.geocode import USER_AGENT, Geocoder
from app.jobs import JobStore
from app.main import create_app
from tests.test_jobs import fake_runner

NOMINATIM_BODY = [
    {"display_name": "Frankfurt am Main, Hessen, Deutschland", "lat": "50.1106444", "lon": "8.6820917"},
    {"display_name": "Frankfurt (Oder), Brandenburg, Deutschland", "lat": "52.3412", "lon": "14.5498"},
]


def make_geocoder(calls: list[httpx.Request], **kw) -> Geocoder:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=NOMINATIM_BODY)

    return Geocoder(client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def test_search_parses_results():
    calls: list[httpx.Request] = []
    results = make_geocoder(calls).search("Frankfurt")
    assert [r.name for r in results][0].startswith("Frankfurt am Main")
    assert results[0].lat == 50.1106444 and results[0].lon == 8.6820917
    assert calls[0].headers["user-agent"] == USER_AGENT
    assert calls[0].url.params["q"] == "Frankfurt" and calls[0].url.params["format"] == "jsonv2"


def test_search_is_cached():
    calls: list[httpx.Request] = []
    g = make_geocoder(calls)
    g.search("Frankfurt")
    g.search("frankfurt ")
    assert len(calls) == 1


def test_search_rate_limits():
    calls: list[httpx.Request] = []
    clock = {"t": 100.0}
    sleeps: list[float] = []
    g = make_geocoder(calls, now=lambda: clock["t"], sleep=sleeps.append)
    g.search("Berlin")
    clock["t"] += 0.3
    g.search("Hamburg")
    assert len(sleeps) == 1 and sleeps[0] == 0.7


def test_geocode_endpoint(tmp_path):
    store = JobStore(tmp_path / "jobs", tmp_path / "cache", runner=fake_runner)
    client = TestClient(create_app(store=store, geocoder=make_geocoder([])))
    r = client.get("/api/geocode", params={"q": "Frankfurt"})
    assert r.status_code == 200
    assert r.json()[0]["lat"] == 50.1106444
    assert client.get("/api/geocode", params={"q": "F"}).status_code == 422
```

- [ ] **Step 2: Fehlschlag bestätigen**

Run: `cd backend && uv run pytest tests/test_geocode.py -q`
Expected: FAIL mit `ModuleNotFoundError: No module named 'app.geocode'`

- [ ] **Step 3: `geocode.py` implementieren**

```python
"""Nominatim place search with in-memory cache and polite rate limiting."""

import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "SkylineFrameGenerator/0.1 (local dev tool)"


@dataclass
class GeocodeResult:
    name: str
    lat: float
    lon: float


class Geocoder:
    def __init__(
        self,
        client: httpx.Client | None = None,
        ttl_s: float = 600,
        min_interval_s: float = 1.0,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client or httpx.Client(timeout=10, headers={"User-Agent": USER_AGENT})
        self._ttl_s = ttl_s
        self._min_interval_s = min_interval_s
        self._now = now
        self._sleep = sleep
        self._cache: dict[str, tuple[float, list[GeocodeResult]]] = {}
        self._last_request: float | None = None

    def search(self, q: str, limit: int = 5) -> list[GeocodeResult]:
        key = q.strip().lower()
        cached = self._cache.get(key)
        if cached and self._now() - cached[0] < self._ttl_s:
            return cached[1]

        if self._last_request is not None:
            wait = self._min_interval_s - (self._now() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._now()

        response = self._client.get(
            NOMINATIM_URL,
            params={"q": q.strip(), "format": "jsonv2", "limit": limit},
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        results = [GeocodeResult(name=item["display_name"], lat=float(item["lat"]), lon=float(item["lon"])) for item in response.json()]
        self._cache[key] = (self._now(), results)
        return results
```

- [ ] **Step 4: Default-Geocoder in `main.py` einsetzen**

In `backend/app/main.py` den Import ergänzen und den Default setzen:

```python
from .geocode import Geocoder
```

und in `create_app` direkt nach `store = store or JobStore(...)`:

```python
    geocoder = geocoder or Geocoder()
```

Danach die Bedingung `if geocoder is not None:` entfernen, sodass der Endpoint immer registriert ist (Einrückung des Handlers eine Stufe zurück).

- [ ] **Step 5: Tests grün**

Run: `cd backend && uv run pytest -q`
Expected: alle Tests bestehen.

- [ ] **Step 6: Commit**

```bash
git add backend/app/geocode.py backend/app/main.py backend/tests/test_geocode.py
git commit -m "feat(api): Nominatim geocoding with cache and rate limit

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Frontend-Grundgerüst, Quadrat-Geometrie und API-Client

**Files:**
- Create: `frontend/` (Vite vanilla-ts), `frontend/vite.config.ts`, `frontend/src/square.ts`, `frontend/src/api.ts`
- Test: `frontend/src/square.test.ts`, `frontend/src/api.test.ts`

**Interfaces:**
- Consumes: API-Endpoints aus Task 9/10.
- Produces: `square.ts`: `SquareParams {lat, lon, sideM, rotationDeg}`, `localToLngLat(x, y, lat, lon): [number, number]`, `squareCorners(p): [number, number][]` (4 Ecken, Reihenfolge SW, SE, NE, NW bei Rotation 0), `squareGeoJSON(p): GeoJSON.Feature<GeoJSON.Polygon>`. `api.ts`: `FrameSpecInput`, `JobState`, `GeocodeHit`, `createJob(spec): Promise<{id: string}>`, `getJob(id): Promise<JobState>`, `waitForJob(id, onUpdate?, intervalMs=1000): Promise<JobState>`, `jobFileUrl(id, name): string`, `geocode(q): Promise<GeocodeHit[]>`.

- [ ] **Step 1: Projekt anlegen**

```bash
cd /Users/robinspeichermann/Documents/Projekte/SkylineFrameGenerator
npm create vite@latest frontend -- --template vanilla-ts
cd frontend
npm install
npm install maplibre-gl three
npm install -D vitest @types/three @types/geojson concurrently
rm -f src/counter.ts src/typescript.svg public/vite.svg
```

`frontend/vite.config.ts`:

```ts
import { defineConfig } from "vite";

export default defineConfig({
  server: {
    proxy: { "/api": "http://localhost:8000" },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
```

In `frontend/package.json` unter `scripts` ergänzen: `"test": "vitest"`. In `tsconfig.json` sicherstellen, dass `"types": ["vite/client"]` enthalten ist (Vite-Template setzt das bereits).

Run: `cd frontend && npx vitest --run`
Expected: `No test files found` (Exit-Code kann 1 sein, das ist hier in Ordnung).

- [ ] **Step 2: Failing Tests für `square.ts`**

`frontend/src/square.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { localToLngLat, squareCorners, squareGeoJSON } from "./square";

const base = { lat: 50, lon: 8, sideM: 1000, rotationDeg: 0 };

describe("localToLngLat", () => {
  it("moves north by side/2 for y", () => {
    const [lon, lat] = localToLngLat(0, 500, 50, 8);
    expect(lon).toBeCloseTo(8, 9);
    expect(lat).toBeCloseTo(50 + 500 / 110574, 6);
  });
  it("shrinks longitude step at higher latitude", () => {
    const [lon50] = localToLngLat(500, 0, 50, 8);
    const [lon0] = localToLngLat(500, 0, 0, 8);
    expect(lon50 - 8).toBeGreaterThan(lon0 - 8);
  });
});

describe("squareCorners", () => {
  it("returns SW, SE, NE, NW without rotation", () => {
    const [sw, se, ne, nw] = squareCorners(base);
    expect(sw[0]).toBeLessThan(8); expect(sw[1]).toBeLessThan(50);
    expect(se[0]).toBeGreaterThan(8); expect(se[1]).toBeLessThan(50);
    expect(ne[0]).toBeGreaterThan(8); expect(ne[1]).toBeGreaterThan(50);
    expect(nw[0]).toBeLessThan(8); expect(nw[1]).toBeGreaterThan(50);
  });
  it("rotates clockwise: the NE corner moves to due east at 45°", () => {
    const [, , ne] = squareCorners({ ...base, rotationDeg: 45 });
    expect(ne[1]).toBeCloseTo(50, 6);
    expect(ne[0]).toBeGreaterThan(8);
  });
});

describe("squareGeoJSON", () => {
  it("is a closed polygon feature", () => {
    const f = squareGeoJSON(base);
    expect(f.geometry.type).toBe("Polygon");
    const ring = f.geometry.coordinates[0];
    expect(ring).toHaveLength(5);
    expect(ring[0]).toEqual(ring[4]);
  });
});
```

- [ ] **Step 3: Fehlschlag bestätigen**

Run: `cd frontend && npx vitest --run src/square.test.ts`
Expected: FAIL, Modul `./square` nicht gefunden.

- [ ] **Step 4: `square.ts` implementieren**

```ts
// Square selection geometry. Uses an equirectangular approximation that is accurate to a few
// metres for squares up to 5 km; the backend uses a proper azimuthal projection for the model.
import type { Feature, Polygon } from "geojson";

export interface SquareParams {
  lat: number;
  lon: number;
  sideM: number;
  rotationDeg: number; // clockwise from north
}

const M_PER_DEG_LAT = 110574;
const M_PER_DEG_LON_EQUATOR = 111320;

export function localToLngLat(x: number, y: number, lat: number, lon: number): [number, number] {
  const mPerDegLon = M_PER_DEG_LON_EQUATOR * Math.cos((lat * Math.PI) / 180);
  return [lon + x / mPerDegLon, lat + y / M_PER_DEG_LAT];
}

export function squareCorners(p: SquareParams): [number, number][] {
  const h = p.sideM / 2;
  const t = (p.rotationDeg * Math.PI) / 180;
  const c = Math.cos(t);
  const s = Math.sin(t);
  const local: [number, number][] = [
    [-h, -h],
    [h, -h],
    [h, h],
    [-h, h],
  ];
  // clockwise rotation by t
  return local.map(([x, y]) => localToLngLat(x * c + y * s, -x * s + y * c, p.lat, p.lon));
}

export function squareGeoJSON(p: SquareParams): Feature<Polygon> {
  const corners = squareCorners(p);
  return {
    type: "Feature",
    properties: {},
    geometry: { type: "Polygon", coordinates: [[...corners, corners[0]]] },
  };
}
```

- [ ] **Step 5: Failing Tests für `api.ts`**

`frontend/src/api.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { createJob, geocode, jobFileUrl, waitForJob } from "./api";

function mockFetch(responses: Array<{ status?: number; body: unknown }>) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  globalThis.fetch = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    const next = responses.shift()!;
    return new Response(JSON.stringify(next.body), {
      status: next.status ?? 200,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
  return calls;
}

afterEach(() => vi.restoreAllMocks());

describe("createJob", () => {
  it("posts the spec and returns the id", async () => {
    const calls = mockFetch([{ status: 202, body: { id: "abc" } }]);
    const res = await createJob({ center_lat: 50, center_lon: 8, side_m: 1000, rotation_deg: 0, plate_size_mm: 100, plate_thickness_mm: 3, mode: "simple", z_exaggeration: 1.5 });
    expect(res.id).toBe("abc");
    expect(calls[0].url).toBe("/api/jobs");
    expect(calls[0].init?.method).toBe("POST");
  });
  it("throws with the server detail on error", async () => {
    mockFetch([{ status: 422, body: { detail: "bad spec" } }]);
    await expect(createJob({} as never)).rejects.toThrow(/bad spec|422/);
  });
});

describe("waitForJob", () => {
  it("polls until done and reports updates", async () => {
    mockFetch([
      { body: { id: "abc", status: "running", stage: "fetch", message: "", stats: {} } },
      { body: { id: "abc", status: "done", stage: "export", message: "Ready", stats: { buildings: 3 } } },
    ]);
    const seen: string[] = [];
    const final = await waitForJob("abc", (j) => seen.push(j.status), 1);
    expect(final.status).toBe("done");
    expect(seen).toEqual(["running", "done"]);
  });
});

describe("helpers", () => {
  it("builds file urls", () => {
    expect(jobFileUrl("abc", "model.stl")).toBe("/api/jobs/abc/model.stl");
  });
  it("geocode encodes the query", async () => {
    const calls = mockFetch([{ body: [{ name: "Frankfurt", lat: 50.1, lon: 8.6 }] }]);
    const hits = await geocode("Frankfurt am Main");
    expect(hits[0].lat).toBe(50.1);
    expect(calls[0].url).toBe("/api/geocode?q=Frankfurt+am+Main");
  });
});
```

- [ ] **Step 6: Fehlschlag bestätigen**

Run: `cd frontend && npx vitest --run src/api.test.ts`
Expected: FAIL, Modul `./api` nicht gefunden.

- [ ] **Step 7: `api.ts` implementieren**

```ts
// Typed client for the backend API.

export interface FrameSpecInput {
  center_lat: number;
  center_lon: number;
  side_m: number;
  rotation_deg: number;
  plate_size_mm: number;
  plate_thickness_mm: number;
  mode: "simple" | "full";
  z_exaggeration: number;
}

export type JobStatus = "queued" | "running" | "done" | "error";

export interface JobState {
  id: string;
  status: JobStatus;
  stage: string;
  message: string;
  stats: Record<string, number>;
}

export interface GeocodeHit {
  name: string;
  lat: number;
  lon: number;
}

export type JobFile = "model.stl" | "model.3mf" | "preview.glb";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export function createJob(spec: FrameSpecInput): Promise<{ id: string }> {
  return request("/api/jobs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(spec),
  });
}

export function getJob(id: string): Promise<JobState> {
  return request(`/api/jobs/${encodeURIComponent(id)}`);
}

export async function waitForJob(id: string, onUpdate?: (job: JobState) => void, intervalMs = 1000): Promise<JobState> {
  for (;;) {
    const job = await getJob(id);
    onUpdate?.(job);
    if (job.status === "done" || job.status === "error") return job;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

export function jobFileUrl(id: string, name: JobFile): string {
  return `/api/jobs/${encodeURIComponent(id)}/${name}`;
}

export function geocode(q: string): Promise<GeocodeHit[]> {
  const params = new URLSearchParams({ q });
  return request(`/api/geocode?${params.toString()}`);
}
```

- [ ] **Step 8: Tests grün**

Run: `cd frontend && npx vitest --run`
Expected: alle Tests bestehen (`9 passed`).

- [ ] **Step 9: Commit**

```bash
git add frontend
git commit -m "feat(frontend): Vite scaffold, square geometry and API client

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: Karte mit verschiebbarem Quadrat und Ortssuche

**Files:**
- Create: `frontend/src/map.ts`
- Create: `frontend/src/search.ts`

**Interfaces:**
- Consumes: `SquareParams`, `squareGeoJSON` (Task 11); `geocode`, `GeocodeHit` (Task 11).
- Produces: `map.ts`: `MapController { setParams(p: SquareParams): void; getParams(): SquareParams; onChange(cb: (p: SquareParams) => void): void; flyTo(lat: number, lon: number): void }`, `createMap(container: HTMLElement, initial: SquareParams): MapController`. `search.ts`: `setupSearch(input: HTMLInputElement, list: HTMLElement, onPick: (hit: GeocodeHit) => void): void`.

Diese Module brauchen WebGL und DOM; sie werden nicht per Unit-Test, sondern im Playwright-Smoke-Test (Task 14) geprüft.

- [ ] **Step 1: `map.ts` implementieren**

```ts
import maplibregl, { type GeoJSONSource, type MapMouseEvent } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { squareGeoJSON, type SquareParams } from "./square";

export interface MapController {
  setParams(p: SquareParams): void;
  getParams(): SquareParams;
  onChange(cb: (p: SquareParams) => void): void;
  flyTo(lat: number, lon: number): void;
}

const STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

export function createMap(container: HTMLElement, initial: SquareParams): MapController {
  let params = { ...initial };
  const listeners: Array<(p: SquareParams) => void> = [];

  const map = new maplibregl.Map({
    container,
    style: STYLE,
    center: [params.lon, params.lat],
    zoom: 13,
    attributionControl: {},
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");

  const redraw = () => {
    const src = map.getSource("square") as GeoJSONSource | undefined;
    src?.setData(squareGeoJSON(params));
  };

  const emit = () => listeners.forEach((cb) => cb(params));

  map.on("load", () => {
    map.addSource("square", { type: "geojson", data: squareGeoJSON(params) });
    map.addLayer({ id: "square-fill", type: "fill", source: "square", paint: { "fill-color": "#ff6a00", "fill-opacity": 0.15 } });
    map.addLayer({ id: "square-line", type: "line", source: "square", paint: { "line-color": "#ff6a00", "line-width": 2 } });

    map.on("mouseenter", "square-fill", () => (map.getCanvas().style.cursor = "move"));
    map.on("mouseleave", "square-fill", () => (map.getCanvas().style.cursor = ""));

    map.on("mousedown", "square-fill", (e: MapMouseEvent) => {
      e.preventDefault();
      map.dragPan.disable();
      const start = e.lngLat;
      const startCenter = { lat: params.lat, lon: params.lon };

      const onMove = (ev: MapMouseEvent) => {
        params = { ...params, lat: startCenter.lat + (ev.lngLat.lat - start.lat), lon: startCenter.lon + (ev.lngLat.lng - start.lng) };
        redraw();
      };
      const onUp = () => {
        map.off("mousemove", onMove);
        map.dragPan.enable();
        emit();
      };
      map.on("mousemove", onMove);
      map.once("mouseup", onUp);
    });
  });

  return {
    setParams(p) {
      params = { ...p };
      redraw();
    },
    getParams: () => ({ ...params }),
    onChange(cb) {
      listeners.push(cb);
    },
    flyTo(lat, lon) {
      params = { ...params, lat, lon };
      redraw();
      map.flyTo({ center: [lon, lat], zoom: 13 });
      emit();
    },
  };
}
```

- [ ] **Step 2: `search.ts` implementieren**

```ts
import { geocode, type GeocodeHit } from "./api";

export function setupSearch(input: HTMLInputElement, list: HTMLElement, onPick: (hit: GeocodeHit) => void): void {
  let timer: ReturnType<typeof setTimeout> | undefined;

  const render = (hits: GeocodeHit[]) => {
    list.replaceChildren();
    for (const hit of hits) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "search-hit";
      button.textContent = hit.name;
      button.addEventListener("click", () => {
        list.replaceChildren();
        input.value = hit.name;
        onPick(hit);
      });
      list.append(button);
    }
  };

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) {
      list.replaceChildren();
      return;
    }
    timer = setTimeout(async () => {
      try {
        render(await geocode(q));
      } catch (err) {
        list.replaceChildren();
        console.error("geocode failed", err);
      }
    }, 300);
  });
}
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: keine Fehler. (Falls `StyleSpecification` nicht exportiert wird: `import type { StyleSpecification } from "maplibre-gl"` verwenden.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/map.ts frontend/src/search.ts
git commit -m "feat(frontend): map with draggable square and place search

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 13: Seitenleiste, 3D-Vorschau und Verdrahtung

**Files:**
- Modify: `frontend/index.html`
- Create: `frontend/src/controls.ts`, `frontend/src/viewer.ts`, `frontend/src/style.css`
- Modify: `frontend/src/main.ts`

**Interfaces:**
- Consumes: `createMap`, `MapController` (Task 12); `setupSearch` (Task 12); `createJob`, `waitForJob`, `jobFileUrl`, `FrameSpecInput`, `JobState` (Task 11); `SquareParams` (Task 11).
- Produces: `controls.ts`: `Controls { read(): FrameSpecInput; writeSquare(p: SquareParams): void; setBusy(busy: boolean): void; setStatus(text: string, isError?: boolean): void; showDownloads(id: string | null): void; onGenerate(cb: () => void): void; onSquareInput(cb: (p: Partial<SquareParams>) => void): void; elements: { search: HTMLInputElement; searchResults: HTMLElement } }`, `setupControls(root: HTMLElement): Controls`. `viewer.ts`: `Viewer { load(url: string): Promise<void>; clear(): void }`, `createViewer(container: HTMLElement): Viewer`.

- [ ] **Step 1: `index.html` ersetzen**

```html
<!doctype html>
<html lang="de">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Skyline Frame Generator</title>
  </head>
  <body>
    <div id="app">
      <div id="map"></div>
      <aside id="sidebar">
        <h1>Skyline Frame</h1>
        <label>Ort suchen
          <input id="search" type="search" placeholder="z. B. Frankfurt am Main" autocomplete="off" />
        </label>
        <div id="search-results"></div>

        <label>Ausschnitt <output id="side-out">1500 m</output>
          <input id="side" type="range" min="200" max="5000" step="50" value="1500" />
        </label>
        <label>Drehung <output id="rotation-out">0°</output>
          <input id="rotation" type="range" min="-180" max="180" step="1" value="0" />
        </label>
        <label>Platte (mm)
          <input id="plate" type="number" min="40" max="250" step="5" value="100" />
        </label>
        <label>Plattendicke (mm)
          <input id="thickness" type="number" min="1" max="10" step="0.5" value="3" />
        </label>
        <label>Höhenfaktor
          <input id="zfactor" type="number" min="0.5" max="10" step="0.1" value="1.5" />
        </label>
        <label>Modus
          <select id="mode">
            <option value="simple">Simple (Gebäude + Platte)</option>
            <option value="full">Full (+ Straßen + Wasser)</option>
          </select>
        </label>

        <button id="generate" type="button">Generieren</button>
        <p id="status" role="status"></p>
        <div id="downloads" hidden>
          <a id="dl-stl" download>STL (einfarbig)</a>
          <a id="dl-3mf" download>3MF (mehrfarbig)</a>
        </div>
      </aside>
      <div id="viewer"></div>
    </div>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
```

- [ ] **Step 2: `style.css` anlegen**

```css
:root {
  font-family: system-ui, sans-serif;
  color: #1a1a1a;
  background: #f4f4f2;
}
* { box-sizing: border-box; }
body { margin: 0; }
#app {
  display: grid;
  grid-template-columns: 3fr 2fr;
  grid-template-rows: auto 1fr;
  height: 100vh;
}
#map { grid-row: 1 / span 2; }
#sidebar {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow-y: auto;
  border-left: 1px solid #ddd;
  background: #fff;
}
#sidebar h1 { font-size: 1.1rem; margin: 0 0 4px; }
#sidebar label { display: flex; flex-direction: column; gap: 4px; font-size: 0.85rem; }
#sidebar label output { float: right; color: #666; }
#sidebar input, #sidebar select { font: inherit; padding: 6px; }
#search-results { display: flex; flex-direction: column; gap: 2px; }
.search-hit { text-align: left; font: inherit; padding: 6px; border: 1px solid #ddd; background: #fafafa; cursor: pointer; }
.search-hit:hover { background: #eee; }
#generate { font: inherit; padding: 10px; background: #ff6a00; color: #fff; border: 0; cursor: pointer; }
#generate:disabled { opacity: 0.5; cursor: wait; }
#status { min-height: 1.2em; font-size: 0.85rem; margin: 0; }
#status.error { color: #b00020; }
#downloads { display: flex; gap: 8px; }
#downloads a { flex: 1; text-align: center; padding: 8px; border: 1px solid #ff6a00; color: #ff6a00; text-decoration: none; }
#viewer { border-left: 1px solid #ddd; background: #e9e9e6; min-height: 280px; }
#viewer canvas { display: block; }
```

- [ ] **Step 3: `controls.ts` implementieren**

```ts
import type { FrameSpecInput } from "./api";
import { jobFileUrl } from "./api";
import type { SquareParams } from "./square";

export interface Controls {
  read(): FrameSpecInput;
  writeSquare(p: SquareParams): void;
  setBusy(busy: boolean): void;
  setStatus(text: string, isError?: boolean): void;
  showDownloads(id: string | null): void;
  onGenerate(cb: () => void): void;
  onSquareInput(cb: (p: Partial<SquareParams>) => void): void;
  elements: { search: HTMLInputElement; searchResults: HTMLElement };
}

function el<T extends HTMLElement>(root: HTMLElement, id: string): T {
  const node = root.querySelector<T>(`#${id}`);
  if (!node) throw new Error(`missing element #${id}`);
  return node;
}

export function setupControls(root: HTMLElement): Controls {
  const side = el<HTMLInputElement>(root, "side");
  const sideOut = el<HTMLOutputElement>(root, "side-out");
  const rotation = el<HTMLInputElement>(root, "rotation");
  const rotationOut = el<HTMLOutputElement>(root, "rotation-out");
  const plate = el<HTMLInputElement>(root, "plate");
  const thickness = el<HTMLInputElement>(root, "thickness");
  const zfactor = el<HTMLInputElement>(root, "zfactor");
  const mode = el<HTMLSelectElement>(root, "mode");
  const generate = el<HTMLButtonElement>(root, "generate");
  const status = el<HTMLParagraphElement>(root, "status");
  const downloads = el<HTMLDivElement>(root, "downloads");
  const dlStl = el<HTMLAnchorElement>(root, "dl-stl");
  const dl3mf = el<HTMLAnchorElement>(root, "dl-3mf");

  let square: SquareParams = { lat: 50.1106, lon: 8.6821, sideM: Number(side.value), rotationDeg: Number(rotation.value) };

  const syncOutputs = () => {
    sideOut.value = `${side.value} m`;
    rotationOut.value = `${rotation.value}°`;
  };
  syncOutputs();

  return {
    read: () => ({
      center_lat: square.lat,
      center_lon: square.lon,
      side_m: Number(side.value),
      rotation_deg: Number(rotation.value),
      plate_size_mm: Number(plate.value),
      plate_thickness_mm: Number(thickness.value),
      mode: mode.value as "simple" | "full",
      z_exaggeration: Number(zfactor.value),
    }),
    writeSquare(p) {
      square = { ...p };
      side.value = String(p.sideM);
      rotation.value = String(p.rotationDeg);
      syncOutputs();
    },
    setBusy(busy) {
      generate.disabled = busy;
    },
    setStatus(text, isError = false) {
      status.textContent = text;
      status.classList.toggle("error", isError);
    },
    showDownloads(id) {
      downloads.hidden = id === null;
      if (id) {
        dlStl.href = jobFileUrl(id, "model.stl");
        dl3mf.href = jobFileUrl(id, "model.3mf");
      }
    },
    onGenerate(cb) {
      generate.addEventListener("click", cb);
    },
    onSquareInput(cb) {
      side.addEventListener("input", () => {
        syncOutputs();
        cb({ sideM: Number(side.value) });
      });
      rotation.addEventListener("input", () => {
        syncOutputs();
        cb({ rotationDeg: Number(rotation.value) });
      });
    },
    elements: { search: el<HTMLInputElement>(root, "search"), searchResults: el<HTMLElement>(root, "search-results") },
  };
}
```

- [ ] **Step 4: `viewer.ts` implementieren**

```ts
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

export interface Viewer {
  load(url: string): Promise<void>;
  clear(): void;
}

export function createViewer(container: HTMLElement): Viewer {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xe9e9e6);
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 2000);
  camera.position.set(120, 100, 120);

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  container.append(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;

  scene.add(new THREE.HemisphereLight(0xffffff, 0x888877, 1.1));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(80, 150, 60);
  scene.add(sun);
  scene.add(new THREE.GridHelper(300, 30, 0xbbbbbb, 0xdddddd));

  const resize = () => {
    const { clientWidth: w, clientHeight: h } = container;
    if (w === 0 || h === 0) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  new ResizeObserver(resize).observe(container);
  resize();

  renderer.setAnimationLoop(() => {
    controls.update();
    renderer.render(scene, camera);
  });

  let model: THREE.Object3D | null = null;
  const loader = new GLTFLoader();

  const clear = () => {
    if (model) scene.remove(model);
    model = null;
  };

  return {
    clear,
    async load(url) {
      clear();
      const gltf = await loader.loadAsync(url);
      model = gltf.scene;
      model.rotation.x = -Math.PI / 2; // model is z-up, three.js is y-up
      model.traverse((obj) => {
        if (obj instanceof THREE.Mesh) {
          obj.material = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.85 });
        }
      });
      scene.add(model);
      const box = new THREE.Box3().setFromObject(model);
      const size = box.getSize(new THREE.Vector3()).length();
      const center = box.getCenter(new THREE.Vector3());
      controls.target.copy(center);
      camera.position.copy(center).add(new THREE.Vector3(size * 0.7, size * 0.6, size * 0.7));
      camera.near = size / 100;
      camera.far = size * 10;
      camera.updateProjectionMatrix();
    },
  };
}
```

- [ ] **Step 5: `main.ts` ersetzen**

```ts
import "./style.css";
import { createJob, waitForJob, jobFileUrl } from "./api";
import { setupControls } from "./controls";
import { createMap } from "./map";
import { setupSearch } from "./search";
import { createViewer } from "./viewer";

const root = document.getElementById("app")!;
const controls = setupControls(root);
const map = createMap(document.getElementById("map")!, { lat: 50.1106, lon: 8.6821, sideM: 1500, rotationDeg: 0 });
const viewer = createViewer(document.getElementById("viewer")!);

controls.writeSquare(map.getParams());
map.onChange((p) => controls.writeSquare(p));
controls.onSquareInput((partial) => map.setParams({ ...map.getParams(), ...partial }));
setupSearch(controls.elements.search, controls.elements.searchResults, (hit) => map.flyTo(hit.lat, hit.lon));

controls.onGenerate(async () => {
  controls.setBusy(true);
  controls.showDownloads(null);
  controls.setStatus("Job wird gestartet …");
  try {
    const { id } = await createJob(controls.read());
    const job = await waitForJob(id, (j) => controls.setStatus(`${j.stage || j.status}: ${j.message}`));
    if (job.status === "error") {
      controls.setStatus(job.message || "Generierung fehlgeschlagen", true);
      return;
    }
    controls.setStatus(`Fertig: ${job.stats.buildings ?? 0} Gebäude`);
    controls.showDownloads(id);
    try {
      await viewer.load(jobFileUrl(id, "preview.glb"));
    } catch (err) {
      console.error(err);
      controls.setStatus("Fertig, aber Vorschau konnte nicht geladen werden.");
    }
  } catch (err) {
    controls.setStatus(err instanceof Error ? err.message : String(err), true);
  } finally {
    controls.setBusy(false);
  }
});
```

- [ ] **Step 6: Typecheck und Build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: keine Typfehler, `dist/` wird erzeugt. (Vite warnt ggf. über Chunk-Größe wegen three/maplibre; das ist in Ordnung.)

- [ ] **Step 7: Manuell im Browser prüfen**

Terminal 1: `cd backend && uv run uvicorn app.main:app --port 8000`
Terminal 2: `cd frontend && npm run dev`
Browser: `http://localhost:5173` → Karte mit orangem Quadrat sichtbar, Quadrat lässt sich ziehen, Slider ändern Größe/Drehung, Suche „Frankfurt" liefert Treffer. Klick auf „Generieren" (Netz nötig) zeigt Fortschritt, danach Vorschau und Download-Links.

- [ ] **Step 8: Commit**

```bash
git add frontend
git commit -m "feat(frontend): controls, 3D preview and generation flow

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 14: Makefile, README und Playwright-Smoke-Test

**Files:**
- Create: `Makefile`, `README.md`
- Create: `frontend/playwright.config.ts`, `frontend/e2e/smoke.spec.ts`
- Modify: `frontend/package.json` (Scripts), `.gitignore`

**Interfaces:**
- Consumes: alles Vorherige. Produces: Entwickler-Kommandos `make setup`, `make dev`, `make test`, `make build`, `make e2e`.

- [ ] **Step 1: Makefile anlegen**

```makefile
.PHONY: setup dev backend frontend test e2e build

setup:
	cd backend && uv sync
	cd frontend && npm install && npx playwright install chromium

backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

dev:
	cd frontend && npx concurrently -k -n api,web "cd ../backend && uv run uvicorn app.main:app --reload --port 8000" "npm run dev"

test:
	cd backend && uv run pytest -q
	cd frontend && npx vitest --run

e2e:
	cd frontend && npx playwright test

build:
	cd frontend && npm run build
```

- [ ] **Step 2: Playwright einrichten**

```bash
cd frontend && npm install -D @playwright/test && npx playwright install chromium
```

`frontend/playwright.config.ts`:

```ts
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: { baseURL: "http://localhost:5173", headless: true },
  webServer: { command: "npm run dev", url: "http://localhost:5173", reuseExistingServer: true },
});
```

In `frontend/package.json` Scripts ergänzen: `"e2e": "playwright test"`. In `vite.config.ts` `test.include` bleibt auf `src/**/*.test.ts`, damit vitest die Playwright-Datei ignoriert.

- [ ] **Step 3: Smoke-Test schreiben (Backend gemockt)**

`frontend/e2e/smoke.spec.ts`:

```ts
import { expect, test } from "@playwright/test";

test("page loads, square is drawn, generation flow shows downloads", async ({ page }) => {
  await page.route("**/api/jobs", (route) =>
    route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ id: "job1" }) }),
  );
  let polls = 0;
  await page.route("**/api/jobs/job1", (route) => {
    polls += 1;
    const body =
      polls < 2
        ? { id: "job1", status: "running", stage: "fetch", message: "Loading", stats: {} }
        : { id: "job1", status: "done", stage: "export", message: "Ready", stats: { buildings: 42 } };
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.route("**/api/jobs/job1/preview.glb", (route) => route.fulfill({ status: 404, body: "" }));

  await page.goto("/");
  await expect(page.locator("#map canvas")).toBeVisible();
  await expect(page.locator("#sidebar h1")).toHaveText("Skyline Frame");

  await page.selectOption("#mode", "full");
  await page.click("#generate");

  await expect(page.locator("#status")).toContainText("42 Gebäude", { timeout: 10_000 });
  await expect(page.locator("#downloads")).toBeVisible();
  await expect(page.locator("#dl-stl")).toHaveAttribute("href", "/api/jobs/job1/model.stl");
  await expect(page.locator("#dl-3mf")).toHaveAttribute("href", "/api/jobs/job1/model.3mf");
});

test("backend error is shown to the user", async ({ page }) => {
  await page.route("**/api/jobs", (route) =>
    route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ id: "job2" }) }),
  );
  await page.route("**/api/jobs/job2", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ id: "job2", status: "error", stage: "fetch", message: "Overpass down", stats: {} }),
    }),
  );
  await page.goto("/");
  await page.click("#generate");
  await expect(page.locator("#status")).toHaveText("Overpass down");
  await expect(page.locator("#status")).toHaveClass(/error/);
  await expect(page.locator("#downloads")).toBeHidden();
});
```

- [ ] **Step 4: Smoke-Test ausführen**

Run: `cd frontend && npx playwright test`
Expected: `2 passed`. Falls die Karten-Canvas in Headless-Chromium nicht sichtbar wird, `use.launchOptions = { args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] }` in der Playwright-Config ergänzen.

- [ ] **Step 5: README schreiben**

`README.md`:

```markdown
# Skyline Frame Generator

Erzeugt 3D-druckbare Stadtausschnitte (Gebäude, optional Straßen und Wasser auf einer quadratischen Platte)
aus OpenStreetMap-Daten. Ausgabe: STL (einfarbig) und 3MF (mehrfarbig, ein Objekt pro Farbe).

## Voraussetzungen

- Python ≥ 3.13 und [uv](https://docs.astral.sh/uv/)
- Node ≥ 20

## Start

    make setup   # einmalig
    make dev     # Backend auf :8000, Frontend auf http://localhost:5173

Ort suchen, Quadrat auf der Karte verschieben, Größe/Drehung einstellen, „Generieren“ klicken, STL oder 3MF laden.

## CLI

    cd backend && uv run skylineframe --lat 50.1106 --lon 8.6821 --side 1500 --mode full --out ../out

## Drucken (Bambu Studio)

- STL: direkt importieren, weiß drucken, 0,2 mm Layer, keine Stützen nötig.
- 3MF: importieren, die Objekte `base`, `buildings`, `water`, `roads` erhalten je ein Filament.
  Wer Straßen als Rille statt als Farbe will, löscht das Objekt `roads`.

## Tests

    make test    # pytest + vitest (offline)
    make e2e     # Playwright-Smoke-Test (Backend gemockt)

## Datenquellen

OpenStreetMap über die Overpass-API (Antworten werden unter `backend/.cache/overpass` gecacht),
Ortssuche über Nominatim. Bitte die Nutzungsbedingungen beider Dienste beachten.
```

- [ ] **Step 6: `.gitignore` ergänzen**

An `.gitignore` anhängen:

```
frontend/test-results/
frontend/playwright-report/
out/
```

- [ ] **Step 7: Vollständige Test-Suite**

Run: `make test && make e2e`
Expected: alle Backend-Tests, Vitest-Tests und die zwei Playwright-Tests bestehen.

- [ ] **Step 8: Commit**

```bash
git add Makefile README.md .gitignore frontend
git commit -m "chore: Makefile, README and Playwright smoke test

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 15: Manuelle Abnahme mit echtem Druck-Workflow

**Files:**
- Modify: `tasks/todo.md` (Review-Abschnitt)

Dieser Task braucht Netz und Bambu Studio. Er liefert kein Code-Artefakt, sondern die Bestätigung, dass das Produkt im Slicer funktioniert.

- [ ] **Step 1: Echten Lauf ausführen**

Run: `cd backend && uv run skylineframe --lat 50.1106 --lon 8.6821 --side 1500 --mode full --out ../out/frankfurt`
Expected: Fortschritt `[fetch] … [export] …`, dann `Buildings: <n>` mit n > 500, `STL:` und `3MF:` Pfade. Laufzeit unter 60 s.

- [ ] **Step 2: Zweiter Lauf mit Rotation und Simple-Modus**

Run: `cd backend && uv run skylineframe --lat 52.5200 --lon 13.4050 --side 2000 --rotation 30 --mode simple --plate 120 --out ../out/berlin`
Expected: erfolgreich, ohne `roads`/`water` im 3MF (in Bambu Studio nur `base` und `buildings`).

- [ ] **Step 3: In Bambu Studio prüfen**

1. `out/frankfurt/model.stl` importieren. Erwartung: ein Objekt, 100 × 100 mm Grundfläche, Slicing ohne Warnungen zu nicht-mannigfaltigen Kanten.
2. `out/frankfurt/model.3mf` importieren. Erwartung: vier Objekte `base`, `buildings`, `water`, `roads`, bündig übereinander; je ein Filament zuweisen (weiß, weiß, blau, grau); Slicing ohne Fehler.
3. Sichtprüfung im Slicer: Straßen als Rillen erkennbar, Main als Vertiefung, Gebäude am Plattenrand sauber abgeschnitten.

- [ ] **Step 4: Ergebnis dokumentieren**

In `tasks/todo.md` einen Abschnitt „Review“ anlegen mit: Laufzeiten beider Läufe, Gebäudeanzahl, Dateigrößen, Ergebnis der Bambu-Studio-Prüfung, offene Beobachtungen (z. B. Gebäude ohne Höhen-Tags, die zu flach wirken; Kandidaten für Phase 2).

- [ ] **Step 5: Commit**

```bash
git add tasks/todo.md
git commit -m "docs: acceptance results for first real runs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec-Abdeckung:** Modi (Task 4/6/8), Farbausgabe single/multi (Task 6/7), Geodaten inkl. Straßenklassen, Wasser-Selektoren, Höhenlogik, Cache und Retry (Task 3), Geocoding mit User-Agent/Rate-Limit/Cache (Task 10), FrameSpec-Parameter und Validierung (Task 1), Projektion mit Rotation (Task 2), Clip/Repair/Vorrangregel (Task 4), Skalierung mit Mindesthöhe (Task 5), Mesh mit Vertiefungen, Einlegern und Versenkung (Task 6), Export mit Verifikation von Wasserdichtigkeit und Plattenmaß (Task 7), Pipeline/CLI (Task 8), API-Endpoints, Job-Store, Cleanup, Fehlerbild ohne Stacktrace, statisches Frontend (Task 9), Frontend-Module (Task 11–13), Tests inkl. Fixture und Playwright (Task 3/14), manuelle Abnahme (Task 15). Nicht im Scope laut Spec: Küstenlinien, Gravur, Rahmen, Parts, Terrain.

**Platzhalter:** keine „TBD“/„TODO“; jeder Code-Schritt enthält vollständigen Code.

**Typkonsistenz:** `run(spec, out_dir, cache_dir, progress=None, fetch=...)` wird in Task 8, 9 (über `_default_runner`) und im CLI identisch benutzt. `RunResult(paths, stats)` und `ExportPaths(stl, threemf, glb)` sind in Task 7/8/9 gleich. `JOB_FILES` deckt genau `MEDIA_TYPES` ab. Frontend `FrameSpecInput` entspricht den Pflichtfeldern von `FrameSpec`; `road_width_mm` und die Mindest-Parameter bleiben Backend-Defaults. `MapController.setParams/getParams/onChange/flyTo` werden in `main.ts` genau so verwendet.
