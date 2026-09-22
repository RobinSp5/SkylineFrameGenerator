# Building Detail Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die gedruckten Modelle zeigen deutlich mehr Stadtstruktur: kleine Grundrisse verschmelzen zu Blöcken statt zu verschwinden, `building:part` liefert Rücksprünge und Aufsätze, `roof:shape` echte Dachkörper, fehlende Höhen kommen aus einer Typ-/Flächenschätzung, und die UI bietet Maßstabs-Presets.

**Architecture:** Alle Änderungen bleiben in der bestehenden Pipeline (`fetch → project → prepare → scale → mesh → export`). `fetch` parst zusätzlich Teile, Dächer und Mindesthöhen; `prepare` bekommt drei neue Stufen (Höhen auffüllen, Teil-Zuordnung, Blockbildung) und liefert neben `buildings` jetzt `blocks`; ein neues Modul `roofs.py` baut Dachkörper mit `manifold3d.Manifold.hull_points`; `scale`/`mesh` extrudieren Blöcke, Teile ab `min_height` und setzen die Dächer auf. Frontend und CLI bekommen Presets, die Statistik neue Kennzahlen.

**Tech Stack:** Python 3.13, uv, pydantic 2, shapely 2.1.2, pyproj 3.8, osm2geojson 0.3, manifold3d 3.5.3, trimesh 5.1, numpy 2, typer, pytest. Frontend: Vite, TypeScript, maplibre-gl, three, vitest (happy-dom), Playwright.

**Spec:** `docs/superpowers/specs/2026-09-22-building-detail-design.md`

## Global Constraints

Die folgenden Werte stammen wörtlich aus der Spec; sie gelten für **jeden** Task.

- Nicht im Scope: Fassaden, Fenster, Bäume, Terrain, Dächer über nicht-rechteckigen Grundrissen (bleiben flach), externe Höhendaten (LoD2, Overture), Küstenlinien.
- Bestehende API bleibt; `FrameSpec` erhält `roofs: bool = True`, `parts: bool = True`, `min_footprint_area_mm2` Default 0,25. 3MF-Teile und Dateinamen unverändert (`base`, `buildings`, `water`, `roads`, `model.stl`, `model.3mf`, `preview.glb`).
- `FrameSpec` hat `model_config = ConfigDict(extra="forbid")`: das Frontend darf ausschließlich bekannte Felder senden.
- Einheiten: `prepare` rechnet in Metern, `scale`/`mesh`/`roofs` in Millimetern. `scale = plate_size_mm / side_m`. `MIN_FEATURE_MM = 0.8`, `LEVEL_HEIGHT_M = 3.2`, `BUILDING_SINK_MM = 0.2` (nur für `single`), Plattenoberseite bei z = 0.
- Höhen laufen durch die `building_height_mm`-Logik (`scale × z_exaggeration`, gerundet auf 1/100 mm, Mindesthöhe `min_building_height_mm`, Deckel `plate_size_mm`). Dachhöhen werden mit demselben Faktor skaliert.
- Höhensemantik (Spec §4): Mit `height`-Tag gilt `height_m = height` und die Traufhöhe ist `height − roof.height_m`, nie unter `0.5 × height`. Ohne `height`-Tag ist `height_m` die Traufhöhe und das Dach kommt obendrauf.
- Höhenschätzung (Spec §5), Reihenfolge `height` → `building:levels × 3.2` → Typtabelle → Flächenregel. Typtabelle: cathedral 35; church, chapel, mosque, synagogue, temple 18; office, hotel, hospital, university 20; apartments, dormitory, civic, public, government 15; commercial, retail, school, industrial, warehouse, supermarket 10; house, detached, semidetached_house, terrace, residential, bungalow 7; garage, garages, shed, hut, carport, kiosk, service 3. Flächenregel: < 100 m² → 5 m; < 400 m² → 8 m; < 1500 m² → 12 m; sonst 15 m.
- Verworfen werden `building=roof`, `building=no`, `building:part=no`.
- Blockbildung (Spec §6.4): Close um `MIN_FEATURE_MM / 2 / scale` (dilate → erode, runde Verbindungen); Blockhöhe = 25. Perzentil der Traufhöhen der enthaltenen Grundrisse (flächengewichtet), mindestens `min_building_height_mm / scale`.
- Druckbarkeit (Spec §6.5): `area ≥ min_footprint_area_mm2 / scale²` **und** `buffer(−MIN_FEATURE_MM/2/scale)` nicht leer. Nicht einzeln druckbare Grundrisse gehen nur in den Block ein.
- Dach-Eignung (Spec §6.6): nur wenn `area / minimum_rotated_rectangle.area ≥ 0.85`, sonst flach.
- Dachhöhe ohne Tag (Spec §7): `0.29 × kurze Seite`, begrenzt auf 2 … 6 m; für `dome`/`round` `0.5 × kurze Seite`. Unter `0.3 mm` Druckhöhe wird kein Dach erzeugt.
- Dachformen (Spec §7): gabled, hipped, half_hipped, pyramidal, skillion, mansard, gambrel, dome, round; unbekannte `roof:shape`-Werte → flach. Der Dachkörper wird auf `prism(footprint, z_e … z_r + ε)` zugeschnitten.
- Presets (Spec §9/§10): Skyline 1500 m / 100 mm, Detail 800 m / 100 mm, Groß 1500 m / 200 mm, Eigene = Feldwerte. CLI: `--preset {skyline,detail,gross}`, explizite Flags gewinnen; `--roofs/--no-roofs`, `--parts/--no-parts` (beide Default an).
- Statistik (Spec §11): zusätzlich `buildings_individual`, `blocks`, `parts`, `roofs`, `footprint_coverage` (0..1). UI zeigt „N Gebäude, M Blöcke, K Dächer".
- Lizenz (Spec §12): README-Abschnitt „Lizenz der Daten": ODbL, Drucke sind „Produced Works", Verkauf erlaubt, Hinweis „Enthält Daten von © OpenStreetMap-Mitwirkende (ODbL)" nötig; der Generator schreibt nichts in das Modell.
- Abnahmeziel (Spec §6): Frankfurt 1500 m / 100 mm → `footprint_coverage ≥ 0.95`.
- Tests laufen ohne Netz. Die einzigen Netz-Aktionen sind die beiden Fixture-Aufzeichnungen in Task 2 und die manuelle Abnahme in Task 8.
- `pytest` läuft mit `filterwarnings = ["error", …]`: numpy-RuntimeWarnings aus `shapely.minimum_rotated_rectangle` müssen mit `np.errstate(divide="ignore", invalid="ignore")` umschlossen werden.
- Jeder Task endet mit grüner Suite (`cd backend && uv run pytest -q`, für Frontend-Tasks zusätzlich `cd frontend && npx vitest --run`) und einem Commit. Commit-Messages enden mit `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Verifizierte Bibliotheks-Fakten (nicht erneut prüfen)

- `manifold3d 3.5.3`: `Manifold.hull_points(list[tuple[float,float,float]]) -> Manifold`; `Manifold.sphere(radius, circular_segments)`; `.scale((sx,sy,sz))`, `.rotate((rx,ry,rz))` (Grad), `.translate(...)`, `.trim_by_plane((nx,ny,nz), offset)` behält `n·p >= offset`; `^` = Schnitt, `-` = Differenz, `+` = Vereinigung; `Manifold.batch_boolean(list, OpType.Add|Intersect|Subtract)`; `.volume()`, `.bounding_box()` (flaches 6-Tupel), `.status()`, `.is_empty()`; `CrossSection(rings, FillRule.EvenOdd).extrude(h)` extrudiert ab z = 0.
- `shapely 2.1.2`: `shapely.minimum_rotated_rectangle(poly)` existiert, gibt bei achsparallelen Eingaben numpy-RuntimeWarnings aus. **`STRtree(geoms).query(geom, predicate=…)` wendet das Prädikat als `eingabe.predicate(baum_geom)` an** — um die Polygone zu finden, die einen Punkt enthalten, lautet das Prädikat daher `"within"` (geprüft: `"contains"` liefert dafür ein leeres Array). `buffer(r, join_style="round")`, `unary_union`, `make_valid`, `representative_point()`.
- `osm2geojson`: `feature["properties"]` enthält `{"type": "way"|"relation", "id": int, "tags": {...}}`.

## Dateistruktur

```
backend/skylineframe/
  features.py    + RoofSpec, Block; Building um height_is_top, min_height_m, roof, osm_id,
                   is_part, outline_id, kind, eaves_m, ridge_m, rect erweitert
  spec.py        + roofs, parts, min_footprint_area_mm2 = 0.25, Preset, PRESETS
  project.py     project_features über dataclasses.replace (alle Felder überleben)
  fetch.py       + building:part-Query, parse_min_height, parse_roof, parse_direction,
                   estimate_height_m, ROOF_SHAPE_MAP
  prepare.py     + estimate_missing_heights, assign_parts, resolve_roof, minimum_rect,
                   default_roof_height_m, weighted_percentile, build_blocks, Prepared.blocks,
                   Prepared.footprint_coverage
  roofs.py       NEU: roof_hull, roof_solid, SHAPES, MIN_ROOF_MM
  scale.py       + ScaledRoof, Prism.z0_mm, Prism.roof, Scaled.blocks, raw_height_mm
  mesh.py        + Blöcke, Teile ab z0, Dachkörper
  pipeline.py    + neue stats-Schlüssel
  cli.py         + --preset, --roofs/--no-roofs, --parts/--no-parts, neue Ausgaben
backend/app/jobs.py    stats-Typ dict[str, float]
backend/tests/         conftest (+ bankenviertel), test_fetch, test_prepare, test_roofs (neu),
                       test_scale, test_mesh, test_pipeline, test_cli, test_spec, test_project
backend/tests/fixtures/record_frankfurt.py, frankfurt_roemer.json (neu aufgezeichnet),
                       frankfurt_bankenviertel.json (neu)
frontend/src/presets.ts (neu), controls.ts, main.ts, controls.test.ts (neu)
frontend/index.html, frontend/e2e/smoke.spec.ts
README.md
```

---

### Task 1: Datenmodell, Spec-Felder und Projektion

**Files:**
- Modify: `backend/skylineframe/features.py` (komplett ersetzen)
- Modify: `backend/skylineframe/spec.py:39-62` (Konstanten und `FrameSpec`-Felder)
- Modify: `backend/skylineframe/project.py:59-65` (`project_features`)
- Test: `backend/tests/test_spec.py` (anhängen)
- Test: `backend/tests/test_project.py` (anhängen)

**Interfaces:**
- Produces:
  - `features.RoofSpec(shape: str, height_m: float = 0.0, direction_deg: float | None = None)` — `height_m == 0.0` heißt „nicht getaggt, `prepare` füllt".
  - `features.Building(geom: BaseGeometry, height_m: float, height_is_top: bool = False, min_height_m: float = 0.0, roof: RoofSpec | None = None, osm_id: int = 0, is_part: bool = False, outline_id: int | None = None, kind: str = "", eaves_m: float = 0.0, ridge_m: float = 0.0, rect: tuple[tuple[float, float], ...] = ())`
  - `features.Block(geom: BaseGeometry, height_m: float)`
  - `spec.Preset` (StrEnum: `skyline`, `detail`, `gross`), `spec.PRESETS: dict[str, tuple[float, float]]` (Name → `(side_m, plate_size_mm)`)
  - `FrameSpec.roofs: bool = True`, `FrameSpec.parts: bool = True`, `FrameSpec.min_footprint_area_mm2: float = 0.25`
- Consumes: nichts (erster Task).

- [ ] **Step 1: Failing Tests schreiben**

An `backend/tests/test_spec.py` anhängen:

```python
def test_detail_defaults():
    s = FrameSpec(center_lat=50, center_lon=8)
    assert s.roofs is True
    assert s.parts is True
    assert s.min_footprint_area_mm2 == 0.25


def test_detail_flags_can_be_switched_off():
    s = FrameSpec(center_lat=50, center_lon=8, roofs=False, parts=False)
    assert s.roofs is False and s.parts is False


def test_presets_match_the_spec_table():
    from skylineframe.spec import PRESETS, Preset

    assert PRESETS[Preset.skyline] == (1500.0, 100.0)
    assert PRESETS[Preset.detail] == (800.0, 100.0)
    assert PRESETS[Preset.gross] == (1500.0, 200.0)
    assert set(PRESETS) == {"skyline", "detail", "gross"}
```

An `backend/tests/test_project.py` anhängen:

```python
def test_project_features_keeps_building_detail_fields():
    from shapely.geometry import box

    from skylineframe.features import Building, Features, RoofSpec

    spec = FrameSpec(center_lat=50.0, center_lon=8.0, side_m=1000)
    building = Building(
        geom=box(8.0, 50.0, 8.001, 50.001),
        height_m=42.0,
        height_is_top=True,
        min_height_m=12.0,
        roof=RoofSpec(shape="gabled", height_m=3.0, direction_deg=45.0),
        osm_id=123,
        is_part=True,
        outline_id=456,
        kind="office",
    )
    out = project_features(Features(buildings=[building]), spec)
    got = out.buildings[0]
    assert got.height_m == 42.0
    assert got.height_is_top is True
    assert got.min_height_m == 12.0
    assert got.roof == RoofSpec(shape="gabled", height_m=3.0, direction_deg=45.0)
    assert (got.osm_id, got.is_part, got.outline_id, got.kind) == (123, True, 456, "office")
    assert got.geom.geom_type == "Polygon"
    assert got.geom is not building.geom  # projected, not the original
```

(`project_features`, `Building`, `Features` und `FrameSpec` stehen bereits im Import-Block der Datei; `box` und `RoofSpec` importiert der Test lokal, damit der bestehende Block unverändert bleibt.)

- [ ] **Step 2: Fehlschlag bestätigen**

Run: `cd backend && uv run pytest tests/test_spec.py tests/test_project.py -q`
Expected: FAIL — `ImportError: cannot import name 'PRESETS'` bzw. `TypeError: Building.__init__() got an unexpected keyword argument 'height_is_top'`.

- [ ] **Step 3: `features.py` ersetzen**

`backend/skylineframe/features.py`:

```python
"""Plain containers for OSM features; geometry CRS depends on the pipeline stage."""

from dataclasses import dataclass, field

from shapely.geometry.base import BaseGeometry


@dataclass
class RoofSpec:
    shape: str  # gabled | hipped | half_hipped | pyramidal | skillion | mansard | gambrel | dome | round
    height_m: float = 0.0  # 0.0 = untagged; prepare derives it from the footprint (spec §7)
    direction_deg: float | None = None  # roof:direction as a compass bearing, None = use the long axis


@dataclass
class Building:
    geom: BaseGeometry  # Polygon or MultiPolygon

    # height_m carries two different meanings, and height_is_top says which one (spec §4):
    # with a `height` tag it is the top of the roof, otherwise it is the eaves height and the
    # roof sits on top of it. prepare resolves both into eaves_m / ridge_m.
    height_m: float
    height_is_top: bool = False

    min_height_m: float = 0.0
    roof: RoofSpec | None = None  # None = flat
    osm_id: int = 0
    is_part: bool = False
    outline_id: int | None = None  # OSM id of the outline this footprint belongs to
    kind: str = ""  # value of the building / building:part tag, input of estimate_height_m

    # Filled by prepare, in local metres.
    eaves_m: float = 0.0
    ridge_m: float = 0.0
    rect: tuple[tuple[float, float], ...] = ()  # 4 corners of the minimum rotated rectangle, or ()


@dataclass
class Block:
    """A welded group of footprints, extruded as one solid (spec §6.4)."""

    geom: BaseGeometry  # Polygon
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
    buildings: list[Building] = field(default_factory=list)  # parts included, is_part=True
    roads: list[Road] = field(default_factory=list)
    water: list[Water] = field(default_factory=list)
```

- [ ] **Step 4: `spec.py` erweitern**

In `backend/skylineframe/spec.py` nach `LEVEL_HEIGHT_M = 3.2` einfügen:

```python


class Preset(StrEnum):
    skyline = "skyline"
    detail = "detail"
    gross = "gross"


# Preset name -> (side_m, plate_size_mm); mirrored by frontend/src/presets.ts (spec §9).
PRESETS: dict[str, tuple[float, float]] = {
    Preset.skyline: (1500.0, 100.0),
    Preset.detail: (800.0, 100.0),
    Preset.gross: (1500.0, 200.0),
}
```

In `FrameSpec` die Zeile `min_footprint_area_mm2: float = Field(default=1.0, ge=0)` ersetzen durch:

```python
    # 0.25 mm² instead of the MVP's 1.0: everything below still reaches the model through its
    # block, so the threshold only decides "own solid" vs "part of the block" (spec §6.5).
    min_footprint_area_mm2: float = Field(default=0.25, ge=0)
    roofs: bool = True  # build roof solids from roof:shape (spec §7)
    parts: bool = True  # render building:part instead of one box per outline (spec §6.3)
```

- [ ] **Step 5: `project_features` feldsicher machen**

In `backend/skylineframe/project.py` den Import

```python
from .features import Building, Features, Road, Water
```

ersetzen durch

```python
from dataclasses import replace

from .features import Features, Road, Water
```

(der `dataclasses`-Import gehört an den Anfang des Import-Blocks) und `project_features` ersetzen durch:

```python
def project_features(features: Features, spec: FrameSpec) -> Features:
    tr = local_transformer(spec)
    # replace() instead of a positional rebuild: Building carries a dozen fields now, and a
    # forgotten one would silently drop roofs or part heights on the way to prepare.
    return Features(
        buildings=[replace(b, geom=to_local(b.geom, spec, tr)) for b in features.buildings],
        roads=[Road(to_local(r.geom, spec, tr), r.cls) for r in features.roads],
        water=[Water(to_local(w.geom, spec, tr)) for w in features.water],
    )
```

- [ ] **Step 6: Tests grün**

Run: `cd backend && uv run pytest -q`
Expected: alle Tests bestehen. (`tests/test_prepare.py::test_tiny_footprint_is_dropped` bleibt grün: 25 m² erreicht zwar die neue Flächengrenze, die Erosion um 4 m macht das Rechteck aber leer.)

- [ ] **Step 7: Commit**

```bash
git add backend/skylineframe/features.py backend/skylineframe/spec.py backend/skylineframe/project.py backend/tests/test_spec.py backend/tests/test_project.py
git commit -m "feat(model): roof spec, building parts and presets in the data model

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Fetch — `building:part`, Dach-Tags, Höhenschätzung, neue Fixtures

**Files:**
- Modify: `backend/skylineframe/fetch.py:37-51` (`build_query`), `:82-109` (Parsing), Ende (neue Helfer)
- Modify: `backend/tests/fixtures/record_frankfurt.py` (komplett ersetzen)
- Modify: `backend/tests/conftest.py` (komplett ersetzen)
- Test: `backend/tests/test_fetch.py` (anhängen)
- Create: `backend/tests/fixtures/frankfurt_bankenviertel.json` (aufgezeichnet)
- Modify: `backend/tests/fixtures/frankfurt_roemer.json` (neu aufgezeichnet)

**Interfaces:**
- Consumes: `features.Building`, `features.RoofSpec` (Task 1).
- Produces:
  - `fetch.parse_min_height(tags: dict) -> float`
  - `fetch.parse_roof(tags: dict) -> RoofSpec | None`
  - `fetch.parse_direction(raw: str | None) -> float | None`
  - `fetch.estimate_height_m(kind: str, area_m2: float) -> float`
  - `fetch.ROOF_SHAPE_MAP: dict[str, str]`, `fetch.TYPE_HEIGHT_M: dict[str, float]`, `fetch.MAX_ROOF_HEIGHT_M = 100.0`
  - `build_query` fragt zusätzlich `way["building:part"]` und `relation["building:part"]["type"="multipolygon"]` ab.
  - `parse_overpass` setzt `height_m = 0.0`, wenn weder `height` noch `building:levels` getaggt sind (Marker für „schätzen").
  - conftest-Fixtures `frankfurt_spec`, `frankfurt_data`, `bankenviertel_spec`, `bankenviertel_data`.

- [ ] **Step 1: Failing Tests schreiben**

An `backend/tests/test_fetch.py` anhängen (und den Import-Block oben um `estimate_height_m, parse_direction, parse_min_height, parse_roof` erweitern sowie `from skylineframe.features import RoofSpec` ergänzen):

```python
# --- building parts, roofs, estimates ----------------------------------

PARTS_SAMPLE = {
    "version": 0.6,
    "elements": [
        # outline 200: 
        {"type": "node", "id": 101, "lat": 50.0000, "lon": 8.0000},
        {"type": "node", "id": 102, "lat": 50.0000, "lon": 8.0020},
        {"type": "node", "id": 103, "lat": 50.0020, "lon": 8.0020},
        {"type": "node", "id": 104, "lat": 50.0020, "lon": 8.0000},
        {"type": "way", "id": 200, "nodes": [101, 102, 103, 104, 101], "tags": {"building": "yes", "height": "20"}},
        # part 201 inside the outline, starting at 10 m, with a gabled roof
        {"type": "node", "id": 105, "lat": 50.0005, "lon": 8.0005},
        {"type": "node", "id": 106, "lat": 50.0005, "lon": 8.0015},
        {"type": "node", "id": 107, "lat": 50.0015, "lon": 8.0015},
        {"type": "node", "id": 108, "lat": 50.0015, "lon": 8.0005},
        {
            "type": "way",
            "id": 201,
            "nodes": [105, 106, 107, 108, 105],
            "tags": {
                "building:part": "yes",
                "min_height": "10 m",
                "height": "40",
                "roof:shape": "gabled",
                "roof:height": "3",
                "roof:direction": "NE",
            },
        },
        # 202: a canopy -> dropped
        {"type": "node", "id": 109, "lat": 50.0030, "lon": 8.0000},
        {"type": "node", "id": 110, "lat": 50.0030, "lon": 8.0010},
        {"type": "node", "id": 111, "lat": 50.0040, "lon": 8.0010},
        {"type": "node", "id": 112, "lat": 50.0040, "lon": 8.0000},
        {"type": "way", "id": 202, "nodes": [109, 110, 111, 112, 109], "tags": {"building": "roof"}},
        # 203: building:part=no -> dropped
        {"type": "node", "id": 113, "lat": 50.0050, "lon": 8.0000},
        {"type": "node", "id": 114, "lat": 50.0050, "lon": 8.0010},
        {"type": "node", "id": 115, "lat": 50.0060, "lon": 8.0010},
        {"type": "node", "id": 116, "lat": 50.0060, "lon": 8.0000},
        {"type": "way", "id": 203, "nodes": [113, 114, 115, 116, 113], "tags": {"building:part": "no"}},
        # 204: part without any height, with building:min_level
        {"type": "node", "id": 117, "lat": 50.0070, "lon": 8.0000},
        {"type": "node", "id": 118, "lat": 50.0070, "lon": 8.0010},
        {"type": "node", "id": 119, "lat": 50.0080, "lon": 8.0010},
        {"type": "node", "id": 120, "lat": 50.0080, "lon": 8.0000},
        {
            "type": "way",
            "id": 204,
            "nodes": [117, 118, 119, 120, 117],
            "tags": {"building:part": "yes", "building:min_level": "5", "roof:shape": "onion"},
        },
        # 205: house without any height tag -> height_m 0.0, kind "house"
        {"type": "node", "id": 121, "lat": 50.0090, "lon": 8.0000},
        {"type": "node", "id": 122, "lat": 50.0090, "lon": 8.0010},
        {"type": "node", "id": 123, "lat": 50.0100, "lon": 8.0010},
        {"type": "node", "id": 124, "lat": 50.0100, "lon": 8.0000},
        {"type": "way", "id": 205, "nodes": [121, 122, 123, 124, 121], "tags": {"building": "house", "roof:shape": "brezel"}},
    ],
}


def by_id(feats) -> dict[int, object]:
    return {b.osm_id: b for b in feats.buildings}


def test_build_query_asks_for_building_parts():
    q = build_query((49.9, 7.9, 50.1, 8.1), Mode.simple)
    assert 'way["building:part"](49.900000,7.900000,50.100000,8.100000);' in q
    assert 'relation["building:part"]["type"="multipolygon"]' in q


def test_parse_overpass_reads_outline_and_part():
    feats = parse_overpass(PARTS_SAMPLE, spec())
    buildings = by_id(feats)
    assert set(buildings) == {200, 201, 204, 205}  # building=roof and building:part=no dropped

    outline = buildings[200]
    assert outline.is_part is False
    assert outline.height_m == 20.0 and outline.height_is_top is True
    assert outline.kind == "yes" and outline.min_height_m == 0.0 and outline.roof is None

    part = buildings[201]
    assert part.is_part is True and part.kind == "yes"
    assert part.height_m == 40.0 and part.height_is_top is True
    assert part.min_height_m == 10.0
    assert part.roof == RoofSpec(shape="gabled", height_m=3.0, direction_deg=45.0)


def test_parse_overpass_reads_min_level_and_maps_roof_aliases():
    buildings = by_id(parse_overpass(PARTS_SAMPLE, spec()))
    part = buildings[204]
    assert part.min_height_m == pytest.approx(16.0)  # 5 levels x 3.2 m
    assert part.height_m == 0.0  # unknown; prepare fills it from the outline or the default
    assert part.roof is not None and part.roof.shape == "dome"  # onion -> dome
    assert part.roof.height_m == 0.0  # untagged; prepare derives it from the footprint


def test_parse_overpass_leaves_unknown_roof_and_height_flat():
    house = by_id(parse_overpass(PARTS_SAMPLE, spec()))[205]
    assert house.roof is None  # "brezel" is not a supported shape -> flat
    assert house.height_m == 0.0 and house.height_is_top is False
    assert house.kind == "house"


@pytest.mark.parametrize(
    "tags,expected",
    [
        ({"min_height": "12"}, 12.0),
        ({"min_height": "12.5 m"}, 12.5),
        ({"building:min_level": "4"}, 12.8),
        ({"min_height": "junk", "building:min_level": "2"}, 6.4),
        ({}, 0.0),
        ({"min_height": "-3"}, 0.0),
        ({"min_height": "5000"}, 0.0),
    ],
)
def test_parse_min_height(tags, expected):
    assert parse_min_height(tags) == pytest.approx(expected)


@pytest.mark.parametrize(
    "raw,expected",
    [("45", 45.0), ("45.5", 45.5), ("N", 0.0), ("NE", 45.0), ("SSW", 202.5), ("e", 90.0), ("400", 40.0), ("", None), (None, None), ("uphill", None)],
)
def test_parse_direction(raw, expected):
    assert parse_direction(raw) == expected


@pytest.mark.parametrize(
    "tags,shape,height",
    [
        ({"roof:shape": "gabled"}, "gabled", 0.0),
        ({"roof:shape": "half-hipped"}, "half_hipped", 0.0),
        ({"roof:shape": "HIPPED"}, "hipped", 0.0),
        ({"roof:shape": "gabled", "roof:height": "4 m"}, "gabled", 4.0),
        ({"roof:shape": "gabled", "roof:levels": "2"}, "gabled", 6.4),
        ({"roof:shape": "gabled", "roof:height": "500"}, "gabled", 0.0),
    ],
)
def test_parse_roof(tags, shape, height):
    roof = parse_roof(tags)
    assert roof is not None
    assert roof.shape == shape
    assert roof.height_m == pytest.approx(height)


@pytest.mark.parametrize("tags", [{}, {"roof:shape": "flat"}, {"roof:shape": "something"}])
def test_parse_roof_returns_none_for_flat_and_unknown(tags):
    assert parse_roof(tags) is None


@pytest.mark.parametrize(
    "kind,area,expected",
    [
        ("cathedral", 5000.0, 35.0),
        ("church", 800.0, 18.0),
        ("office", 5000.0, 20.0),
        ("apartments", 300.0, 15.0),
        ("retail", 2000.0, 10.0),
        ("house", 90.0, 7.0),
        ("garage", 30.0, 3.0),
        ("yes", 50.0, 5.0),
        ("yes", 100.0, 8.0),
        ("yes", 399.0, 8.0),
        ("yes", 400.0, 12.0),
        ("yes", 1499.0, 12.0),
        ("yes", 1500.0, 15.0),
        ("", 20000.0, 15.0),
    ],
)
def test_estimate_height_m(kind, area, expected):
    assert estimate_height_m(kind, area) == pytest.approx(expected)
```

Außerdem den bestehenden Fixture-Test in `test_fetch.py` ersetzen (`test_frankfurt_fixture_parses`):

```python
def test_frankfurt_fixture_parses(frankfurt_spec, frankfurt_data):
    feats = parse_overpass(frankfurt_data, frankfurt_spec)
    assert len(feats.buildings) > 20
    assert len(feats.roads) > 5
    assert len(feats.water) >= 1
    assert sum(1 for b in feats.buildings if b.roof is not None) > 20


def test_bankenviertel_fixture_has_building_parts(bankenviertel_spec, bankenviertel_data):
    feats = parse_overpass(bankenviertel_data, bankenviertel_spec)
    assert sum(1 for b in feats.buildings if b.is_part) > 0
    assert all(b.osm_id != 0 for b in feats.buildings)
```

- [ ] **Step 2: Fehlschlag bestätigen**

Run: `cd backend && uv run pytest tests/test_fetch.py -q`
Expected: FAIL — `ImportError: cannot import name 'estimate_height_m' from 'skylineframe.fetch'`.

- [ ] **Step 3: `fetch.py` erweitern**

In `backend/skylineframe/fetch.py` den Import

```python
from .features import Building, Features, Road, Water
```

ersetzen durch

```python
from .features import Building, Features, Road, RoofSpec, Water
```

Nach `WATER_SELECTORS = (...)` einfügen:

```python
MAX_ROOF_HEIGHT_M = 100.0

# roof:shape values we can build (spec §7) plus the common synonyms; everything else is flat.
ROOF_SHAPE_MAP: dict[str, str] = {
    "gabled": "gabled",
    "pitched": "gabled",
    "hipped": "hipped",
    "half-hipped": "half_hipped",
    "half_hipped": "half_hipped",
    "pyramidal": "pyramidal",
    "cone": "pyramidal",
    "skillion": "skillion",
    "mansard": "mansard",
    "gambrel": "gambrel",
    "dome": "dome",
    "onion": "dome",
    "round": "round",
}

COMPASS_DEG: dict[str, float] = {
    "N": 0.0, "NNE": 22.5, "NE": 45.0, "ENE": 67.5,
    "E": 90.0, "ESE": 112.5, "SE": 135.0, "SSE": 157.5,
    "S": 180.0, "SSW": 202.5, "SW": 225.0, "WSW": 247.5,
    "W": 270.0, "WNW": 292.5, "NW": 315.0, "NNW": 337.5,
}

# Height by building type, then by footprint area (spec §5). Buildings without any height tag
# are ~70 % of the data, and a flat 8 m for all of them is what flattened the MVP models.
TYPE_HEIGHT_M: dict[str, float] = {
    "cathedral": 35.0,
    "church": 18.0, "chapel": 18.0, "mosque": 18.0, "synagogue": 18.0, "temple": 18.0,
    "office": 20.0, "hotel": 20.0, "hospital": 20.0, "university": 20.0,
    "apartments": 15.0, "dormitory": 15.0, "civic": 15.0, "public": 15.0, "government": 15.0,
    "commercial": 10.0, "retail": 10.0, "school": 10.0, "industrial": 10.0,
    "warehouse": 10.0, "supermarket": 10.0,
    "house": 7.0, "detached": 7.0, "semidetached_house": 7.0, "terrace": 7.0,
    "residential": 7.0, "bungalow": 7.0,
    "garage": 3.0, "garages": 3.0, "shed": 3.0, "hut": 3.0, "carport": 3.0,
    "kiosk": 3.0, "service": 3.0,
}
AREA_HEIGHT_M: tuple[tuple[float, float], ...] = ((100.0, 5.0), (400.0, 8.0), (1500.0, 12.0))
AREA_HEIGHT_FALLBACK_M = 15.0
```

`build_query` ersetzen:

```python
def build_query(bbox: tuple[float, float, float, float], mode: Mode) -> str:
    south, west, north, east = bbox
    bb = f"({south:.6f},{west:.6f},{north:.6f},{east:.6f})"
    parts = [
        f'way["building"]{bb};',
        f'relation["building"]["type"="multipolygon"]{bb};',
        # Parts are the setbacks and rooftop boxes of a tagged outline (spec §4). They are their
        # own elements, so without this pair the MVP query never saw a single one of them.
        f'way["building:part"]{bb};',
        f'relation["building:part"]["type"="multipolygon"]{bb};',
    ]
    if mode == Mode.full:
        classes = "|".join(ROAD_CLASSES)
        parts.append(f'way["highway"~"^({classes})$"]{bb};')
        for selector in WATER_SELECTORS:
            parts.append(f"way{selector}{bb};")
            parts.append(f"relation{selector}{bb};")
    body = "\n  ".join(parts)
    # (._;>;) unions the matched elements with everything they reference, so every way and node
    # is emitted exactly once *with* tags. The classic "out body; >; out skel qt;" would emit member
    # ways twice (once tagged, once as tagless skeleton) and osm2geojson could shadow the tagged copy.
    return f"[out:json][timeout:90];\n(\n  {body}\n);\n(._;>;);\nout body qt;\n"
```

Nach `parse_height` einfügen:

```python
def _metres(raw: str | None, limit: float) -> float | None:
    """A non-negative length in metres from an OSM value, or None when it is unusable."""
    if not raw:
        return None
    try:
        value = float(raw.strip().removesuffix("m").strip())
    except ValueError:
        return None
    return value if 0 <= value <= limit else None


def _levels(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        count = float(raw)
    except ValueError:
        return None
    return count if 0 <= count <= MAX_LEVELS else None


def parse_min_height(tags: dict) -> float:
    """Bottom of a building part: min_height, else building:min_level x 3.2, else 0 (spec §4)."""
    value = _metres(tags.get("min_height"), MAX_HEIGHT_M)
    if value is not None:
        return value
    levels = _levels(tags.get("building:min_level"))
    return levels * LEVEL_HEIGHT_M if levels is not None else 0.0


def parse_direction(raw: str | None) -> float | None:
    """roof:direction as a compass bearing in degrees (0 = north, clockwise), or None."""
    if not raw:
        return None
    text = raw.strip().upper()
    if text in COMPASS_DEG:
        return COMPASS_DEG[text]
    try:
        value = float(text)
    except ValueError:
        return None
    return value % 360


def parse_roof(tags: dict) -> RoofSpec | None:
    """The roof of one element, or None for flat / unsupported / untagged shapes (spec §7).

    height_m stays 0.0 when neither roof:height nor roof:levels is tagged; prepare then derives
    it from the footprint, because the rule needs the short side in local metres.
    """
    shape = ROOF_SHAPE_MAP.get((tags.get("roof:shape") or "").strip().lower())
    if shape is None:
        return None
    height = _metres(tags.get("roof:height"), MAX_ROOF_HEIGHT_M)
    if height is None:
        levels = _levels(tags.get("roof:levels"))
        height = levels * LEVEL_HEIGHT_M if levels else None
    return RoofSpec(shape=shape, height_m=height or 0.0, direction_deg=parse_direction(tags.get("roof:direction")))


def estimate_height_m(kind: str, area_m2: float) -> float:
    """Height of a building without height tags, by type and then by footprint area (spec §5)."""
    by_type = TYPE_HEIGHT_M.get(kind)
    if by_type is not None:
        return by_type
    for limit, height in AREA_HEIGHT_M:
        if area_m2 < limit:
            return height
    return AREA_HEIGHT_FALLBACK_M


def _building_tags(tags: dict) -> tuple[str, bool] | None:
    """(kind, is_part) for a building or a building part, or None when the element is neither.

    building=roof is a carport or a canopy with no walls, and every =no is an explicit
    "there is nothing here"; both are dropped (spec §4).
    """
    value = tags.get("building")
    if value and value not in ("no", "roof"):
        return value, False
    part = tags.get("building:part")
    if part and part != "no":
        return part, True
    return None
```

In `parse_overpass` den Gebäude-Zweig ersetzen:

```python
        classified = _building_tags(tags)
        if classified is not None and kind in ("Polygon", "MultiPolygon"):
            building_kind, is_part = classified
            tagged_height = _metres(tags.get("height"), MAX_HEIGHT_M)
            feats.buildings.append(
                Building(
                    geom=geom,
                    # 0.0 means "no height information at all"; prepare fills it with the
                    # estimate (buildings) or the outline height (parts), spec §5/§6.
                    height_m=parse_height(tags, 0.0),
                    height_is_top=bool(tagged_height),
                    min_height_m=parse_min_height(tags) if is_part else 0.0,
                    roof=parse_roof(tags),
                    osm_id=int(feature["properties"].get("id", 0)),
                    is_part=is_part,
                    kind=building_kind,
                )
            )
        elif tags.get("highway") in ROAD_CLASSES and kind in ("LineString", "MultiLineString"):
```

(Die Wasser-Zeile darunter bleibt unverändert; die Variable `kind` aus `geom.geom_type` behält ihren Namen.)

- [ ] **Step 4: Unit-Tests grün (ohne Fixtures)**

Run: `cd backend && uv run pytest tests/test_fetch.py -q -k "not fixture and not bankenviertel"`
Expected: PASS.

- [ ] **Step 5: conftest und Aufzeichnungsskript ersetzen**

`backend/tests/fixtures/record_frankfurt.py`:

```python
"""Record the Overpass responses used by offline tests. Run once, with network:

    cd backend && uv run python tests/fixtures/record_frankfurt.py
"""

import json
import shutil
from pathlib import Path

from skylineframe.fetch import build_query, fetch_overpass
from skylineframe.project import query_bbox
from skylineframe.spec import FrameSpec, Mode

# Keep in sync with tests/conftest.py.
SPECS: dict[str, FrameSpec] = {
    # Römer, north bank of the Main: dense old town, 60 roof:shape buildings.
    "frankfurt_roemer.json": FrameSpec(center_lat=50.1090, center_lon=8.6820, side_m=400, mode=Mode.full),
    # Bankenviertel: towers modelled with building:part (Commerzbank Tower and neighbours).
    "frankfurt_bankenviertel.json": FrameSpec(center_lat=50.1105, center_lon=8.6747, side_m=300, mode=Mode.full),
}


def main() -> None:
    here = Path(__file__).parent
    tmp = here / "_tmp_cache"
    for name, spec in SPECS.items():
        data = fetch_overpass(build_query(query_bbox(spec), spec.mode), cache_dir=tmp)
        (here / name).write_text(json.dumps(data))
        print(f"recorded {name}: {len(data['elements'])} elements")
    shutil.rmtree(tmp)


if __name__ == "__main__":
    main()
```

`backend/tests/conftest.py`:

```python
import json
from pathlib import Path

import pytest

from skylineframe.spec import FrameSpec, Mode

FIXTURES = Path(__file__).parent / "fixtures"

# Same parameters as fixtures/record_frankfurt.py — keep in sync.
FRANKFURT = FrameSpec(center_lat=50.1090, center_lon=8.6820, side_m=400, mode=Mode.full)
BANKENVIERTEL = FrameSpec(center_lat=50.1105, center_lon=8.6747, side_m=300, mode=Mode.full)


@pytest.fixture
def frankfurt_spec() -> FrameSpec:
    return FRANKFURT.model_copy()


@pytest.fixture
def frankfurt_data() -> dict:
    return json.loads((FIXTURES / "frankfurt_roemer.json").read_text())


@pytest.fixture
def bankenviertel_spec() -> FrameSpec:
    return BANKENVIERTEL.model_copy()


@pytest.fixture
def bankenviertel_data() -> dict:
    return json.loads((FIXTURES / "frankfurt_bankenviertel.json").read_text())
```

- [ ] **Step 6: Fixtures aufzeichnen (einzige Netz-Aktion)**

Run: `cd backend && uv run python tests/fixtures/record_frankfurt.py`
Expected: zwei Zeilen, z. B. `recorded frankfurt_roemer.json: 12000 elements` und `recorded frankfurt_bankenviertel.json: 6000 elements`; beide Dateien liegen unter `backend/tests/fixtures/`, `_tmp_cache` ist gelöscht.

Falls Overpass gerade überlastet ist (`FetchError`), den Befehl nach ein bis zwei Minuten wiederholen — das Skript ist idempotent.

- [ ] **Step 7: Fixture-Inhalt prüfen**

Run:

```bash
cd backend && uv run python -c "
import json
from skylineframe.fetch import parse_overpass
from tests.conftest import BANKENVIERTEL, FIXTURES, FRANKFURT
for name, spec in (('frankfurt_roemer.json', FRANKFURT), ('frankfurt_bankenviertel.json', BANKENVIERTEL)):
    feats = parse_overpass(json.loads((FIXTURES / name).read_text()), spec)
    parts = sum(1 for b in feats.buildings if b.is_part)
    roofs = sum(1 for b in feats.buildings if b.roof is not None)
    print(name, 'buildings', len(feats.buildings), 'parts', parts, 'roofs', roofs)
"
```

Expected: `frankfurt_roemer.json` mit > 400 Gebäuden und > 20 Dächern, `frankfurt_bankenviertel.json` mit `parts` > 0. Ist `parts` = 0, ist die Query nicht die neue — Step 3 prüfen und neu aufzeichnen (vorher `backend/.cache/overpass` nicht anfassen, das Skript nutzt einen eigenen Cache).

- [ ] **Step 8: Volle Suite grün**

Run: `cd backend && uv run pytest -q`
Expected: alle Tests bestehen (`test_pipeline.py` nutzt die neu aufgezeichnete Fixture weiter).

- [ ] **Step 9: Commit**

```bash
git add backend/skylineframe/fetch.py backend/tests/test_fetch.py backend/tests/conftest.py backend/tests/fixtures
git commit -m "feat(fetch): query building parts, parse roof tags and estimate missing heights

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Prepare — Höhen auffüllen, Teile zuordnen, Blöcke bilden, Dächer vorbereiten

**Files:**
- Modify: `backend/skylineframe/prepare.py` (komplett ersetzen)
- Test: `backend/tests/test_prepare.py` (bestehende Datei erweitern, zwei Tests ersetzen)

**Interfaces:**
- Consumes: `features.Building/Block/RoofSpec` (Task 1), `fetch.estimate_height_m(kind, area_m2)` (Task 2), `project.square_local(spec)`, `spec.MIN_FEATURE_MM`, `FrameSpec.roofs/.parts/.min_footprint_area_mm2`.
- Produces:
  - `prepare.Prepared(buildings: list[Building], blocks: list[Block] = [], roads: list[Polygon] = [], water: list[Polygon] = [], footprint_coverage: float = 0.0)`
  - `prepare.prepare(features: Features, spec: FrameSpec) -> Prepared` — `buildings` enthält nur einzeln druckbare Grundrisse, jeder mit gefülltem `eaves_m`, `ridge_m`, und (falls Dach) `roof` + `rect` (vier Ecken in Metern).
  - `prepare.polygons_of(geom) -> list[Polygon]` (unverändert)
  - `prepare.weighted_percentile(values: list[float], weights: list[float], q: float) -> float`
  - `prepare.minimum_rect(poly: Polygon) -> tuple[tuple[float, float], ...]`
  - `prepare.default_roof_height_m(shape: str, short_side_m: float) -> float`
  - `prepare.assign_parts(buildings: list[Building], default_height_m: float) -> list[Building]`
  - `prepare.resolve_roof(b: Building, rotation_deg: float) -> None`
  - `prepare.build_blocks(footprints, spec, close_m, min_area_m2, half_feature_m) -> list[Block]`
  - Konstanten `SIMPLIFY_TOLERANCE_MM = 0.05`, `BLOCK_PERCENTILE = 0.25`, `ROOF_RECT_RATIO = 0.85`
- Downstream-Vertrag: `Building.rect` ist entweder leer oder genau vier Ecken; `ridge_m ≥ eaves_m`; `roof is None` ⇒ `eaves_m == ridge_m == height_m`.

- [ ] **Step 1: Failing Tests schreiben**

In `backend/tests/test_prepare.py` den Import-Block oben ersetzen:

```python
import pytest
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from skylineframe.features import Building, Features, Road, RoofSpec, Water
from skylineframe.prepare import (
    SIMPLIFY_TOLERANCE_MM,
    assign_parts,
    default_roof_height_m,
    minimum_rect,
    polygons_of,
    prepare,
    weighted_percentile,
)
from skylineframe.spec import FrameSpec, Mode
```

Die beiden Tests `test_tiny_footprint_is_dropped` und `test_sliver_footprint_is_dropped` ersetzen durch:

```python
def test_tiny_footprint_reaches_the_model_through_its_block():
    # 0.25 mm² at scale 0.1 => 25 m², and the 0.8 mm feature width => 8 m. The 5x5 shed is
    # neither, so it is no longer its own solid — but it is 1 m away from the house, so the
    # close (radius 4 m) welds both into one block and none of its area is lost (spec §6.5).
    feats = Features(buildings=[bld(box(0, 0, 10, 10)), bld(box(11, 0, 16, 5))])
    out = prepare(feats, spec())
    assert len(out.buildings) == 1
    assert out.buildings[0].geom.area == pytest.approx(100)
    assert len(out.blocks) == 1
    assert out.blocks[0].geom.area > 125
    assert out.footprint_coverage == pytest.approx(1.0)


def test_sliver_footprint_is_dropped():
    # 2 m x 300 m wall: area 600 m² passes the area filter but is 0.2 mm wide at scale 0.1 =>
    # unprintable, and its block (the wall alone) is unprintable too, so it disappears.
    feats = Features(buildings=[bld(box(0, 0, 2, 300)), bld(box(20, 20, 40, 40))])
    out = prepare(feats, spec())
    assert len(out.buildings) == 1
    assert out.buildings[0].geom.bounds == pytest.approx((20, 20, 40, 40))
    assert [b.geom.bounds for b in out.blocks] == [pytest.approx((20, 20, 40, 40))]
```

Am Ende von `backend/tests/test_prepare.py` anhängen:

```python
# --- heights ------------------------------------------------------------


def test_missing_height_is_estimated_by_type():
    feats = Features(buildings=[Building(box(0, 0, 20, 20), height_m=0.0, kind="church")])
    out = prepare(feats, spec())
    assert out.buildings[0].height_m == 18.0
    assert out.buildings[0].eaves_m == 18.0


def test_missing_height_falls_back_to_the_area_rule():
    # kind "yes" is not in the type table; 20 x 20 = 400 m² lands in the "< 1500" row => 12 m.
    feats = Features(buildings=[Building(box(0, 0, 20, 20), height_m=0.0, kind="yes")])
    out = prepare(feats, spec())
    assert out.buildings[0].height_m == 12.0


def test_tagged_height_is_kept():
    feats = Features(buildings=[Building(box(0, 0, 20, 20), height_m=9.0, kind="yes")])
    out = prepare(feats, spec())
    assert out.buildings[0].height_m == 9.0


# --- building parts -----------------------------------------------------


def outline(geom, height=20.0, osm_id=1) -> Building:
    return Building(geom, height_m=height, height_is_top=True, osm_id=osm_id, kind="yes")


def part(geom, height=0.0, osm_id=2, min_height=0.0) -> Building:
    return Building(geom, height_m=height, height_is_top=height > 0, osm_id=osm_id, is_part=True, min_height_m=min_height, kind="yes")


def test_part_inside_outline_replaces_it_together_with_the_remainder():
    feats = Features(buildings=[outline(box(0, 0, 40, 40)), part(box(10, 10, 30, 30), height=30.0, min_height=10.0)])
    out = prepare(feats, spec())
    by_area = sorted(out.buildings, key=lambda b: b.geom.area)
    assert [round(b.geom.area) for b in by_area] == [400, 1200]  # the part and the frame around it
    p, remainder = by_area
    assert p.is_part is True and p.outline_id == 1 and p.min_height_m == 10.0 and p.height_m == 30.0
    assert remainder.is_part is False and remainder.outline_id == 1 and remainder.height_m == 20.0
    assert remainder.geom.interiors  # the frame keeps the hole where the part stands


def test_part_without_height_inherits_the_outline_height():
    feats = Features(buildings=[outline(box(0, 0, 40, 40), height=25.0), part(box(10, 10, 30, 30))])
    out = prepare(feats, spec())
    p = next(b for b in out.buildings if b.is_part)
    assert p.height_m == 25.0
    assert p.eaves_m == 25.0


def test_part_without_outline_is_treated_like_a_building():
    feats = Features(buildings=[part(box(0, 0, 20, 20))])
    out = prepare(feats, spec())
    assert len(out.buildings) == 1
    p = out.buildings[0]
    assert p.outline_id is None
    assert p.height_m == 8.0  # spec default_building_height_m


def test_parts_can_be_switched_off():
    feats = Features(buildings=[outline(box(0, 0, 40, 40)), part(box(10, 10, 30, 30), height=30.0)])
    out = prepare(feats, spec(parts=False))
    assert len(out.buildings) == 1
    assert out.buildings[0].geom.area == pytest.approx(1600)
    assert not out.buildings[0].geom.interiors


def test_assign_parts_keeps_everything_when_there_is_no_part():
    buildings = [outline(box(0, 0, 10, 10)), outline(box(20, 20, 30, 30), osm_id=3)]
    out = assign_parts(buildings, 8.0)
    assert [id(b) for b in out] == [id(b) for b in buildings]  # same objects, new list


# --- blocks -------------------------------------------------------------


def test_three_row_houses_form_one_block():
    # 0.5 m gaps are far below the close radius of MIN_FEATURE_MM / 2 / scale = 4 m.
    feats = Features(
        buildings=[
            bld(box(0, 0, 10, 10), 12.0),
            bld(box(10.5, 0, 20.5, 10), 20.0),
            bld(box(21, 0, 31, 10), 30.0),
        ]
    )
    out = prepare(feats, spec())
    assert len(out.blocks) == 1
    assert out.blocks[0].geom.area == pytest.approx(310, rel=1e-3)  # 31 x 10 including both gaps
    # equal areas => the 25th percentile is the lowest of the three eaves heights
    assert out.blocks[0].height_m == pytest.approx(12.0)
    assert len(out.buildings) == 3  # each house is printable on its own as well


def test_block_height_has_a_floor():
    # min_building_height_mm / scale = 0.8 / 0.1 = 8 m
    out = prepare(Features(buildings=[bld(box(0, 0, 20, 20), 3.0)]), spec())
    assert out.blocks[0].height_m == pytest.approx(8.0)


def test_weighted_percentile_uses_the_areas():
    # Unweighted the 25th percentile would be 12; weighted, the 12 m footprint carries 20 of
    # 400 m², so the first value whose cumulative area reaches 100 m² is 40.
    assert weighted_percentile([40.0, 12.0], [380.0, 20.0], 0.25) == 40.0
    assert weighted_percentile([40.0, 12.0], [100.0, 300.0], 0.25) == 12.0
    assert weighted_percentile([7.0], [1.0], 0.25) == 7.0


def test_unprintable_block_is_dropped_and_lowers_the_coverage():
    # The 5x5 shed stands alone, so its block is the shed itself: 25 m² is exactly the area
    # threshold, but eroding by 4 m leaves nothing, so the block goes and 25 of 125 m² of
    # building area are missing from the model.
    feats = Features(buildings=[bld(box(0, 0, 10, 10)), bld(box(200, 200, 205, 205))])
    out = prepare(feats, spec())
    assert len(out.blocks) == 1
    assert len(out.buildings) == 1
    assert out.footprint_coverage == pytest.approx(0.8)


def test_roads_are_blocked_by_the_block_not_only_by_the_footprints():
    # Two houses 3 m apart: the close welds them, so the road pocket stops at the block outline
    # and no groove runs through the gap between them (spec §6.7).
    feats = Features(
        buildings=[bld(box(-13, -5, -3, 5)), bld(box(0, -5, 10, 5))],
        roads=[Road(LineString([(-100, 0), (100, 0)]), "residential")],
    )
    out = prepare(feats, spec(mode=Mode.full))
    gap = box(-3, -5, 0, 5)
    assert unary_union(out.roads).intersection(gap).area == pytest.approx(0, abs=1e-6)


# --- roofs --------------------------------------------------------------


def roofed(geom, shape="gabled", height=12.0, height_is_top=False, roof_height=0.0) -> Building:
    return Building(
        geom,
        height_m=height,
        height_is_top=height_is_top,
        roof=RoofSpec(shape=shape, height_m=roof_height),
        kind="yes",
    )


def test_minimum_rect_returns_four_corners_without_numpy_warnings():
    # shapely's oriented_envelope divides by zero on axis-aligned input and the suite turns
    # warnings into errors, so this call would fail the whole run if it were not guarded.
    rect = minimum_rect(box(0, 0, 10, 6))
    assert len(rect) == 4
    assert Polygon(rect).area == pytest.approx(60)


def test_rectangular_footprint_keeps_its_roof():
    out = prepare(Features(buildings=[roofed(box(0, 0, 20, 10))]), spec())
    b = out.buildings[0]
    assert b.roof is not None and b.roof.shape == "gabled"
    assert len(b.rect) == 4
    assert b.eaves_m == pytest.approx(12.0)
    # untagged roof height over a 10 m short side: 0.29 * 10 = 2.9, inside the 2..6 m clamp
    assert b.roof.height_m == pytest.approx(2.9)
    assert b.ridge_m == pytest.approx(14.9)


def test_l_shaped_footprint_stays_flat():
    l_shape = Polygon([(0, 0), (20, 0), (20, 10), (10, 10), (10, 20), (0, 20)])  # 300 of 400 m² => 0.75
    out = prepare(Features(buildings=[roofed(l_shape)]), spec())
    b = out.buildings[0]
    assert b.roof is None
    assert b.rect == ()
    assert b.eaves_m == b.ridge_m == 12.0


def test_height_tag_puts_the_roof_below_the_top():
    out = prepare(Features(buildings=[roofed(box(0, 0, 20, 10), height=20.0, height_is_top=True, roof_height=6.0)]), spec())
    b = out.buildings[0]
    assert b.ridge_m == pytest.approx(20.0)
    assert b.eaves_m == pytest.approx(14.0)


def test_roof_never_eats_more_than_half_the_tagged_height():
    out = prepare(Features(buildings=[roofed(box(0, 0, 20, 10), height=20.0, height_is_top=True, roof_height=15.0)]), spec())
    b = out.buildings[0]
    assert b.eaves_m == pytest.approx(10.0)  # 0.5 x height, not 5
    assert b.roof.height_m == pytest.approx(10.0)
    assert b.ridge_m == pytest.approx(20.0)


@pytest.mark.parametrize(
    "shape,short,expected",
    [
        ("gabled", 6.0, 2.0),  # 0.29 * 6 = 1.74 -> clamped to 2
        ("gabled", 20.0, 5.8),
        ("gabled", 30.0, 6.0),  # 8.7 -> clamped to 6
        ("dome", 10.0, 5.0),
        ("round", 8.0, 4.0),
    ],
)
def test_default_roof_height_m(shape, short, expected):
    assert default_roof_height_m(shape, short) == pytest.approx(expected)


def test_roofs_can_be_switched_off():
    out = prepare(Features(buildings=[roofed(box(0, 0, 20, 10))]), spec(roofs=False))
    b = out.buildings[0]
    assert b.roof is None and b.ridge_m == b.eaves_m == 12.0


def test_roof_direction_is_rotated_into_the_local_frame():
    # The world is rotated by -rotation_deg to align the square, so a compass bearing in the
    # local frame is the tagged bearing minus rotation_deg.
    feats = Features(buildings=[Building(box(0, 0, 20, 10), height_m=12.0, roof=RoofSpec("gabled", 3.0, 90.0), kind="yes")])
    out = prepare(feats, spec(rotation_deg=30))
    assert out.buildings[0].roof.direction_deg == pytest.approx(60.0)
```

- [ ] **Step 2: Fehlschlag bestätigen**

Run: `cd backend && uv run pytest tests/test_prepare.py -q`
Expected: FAIL — `ImportError: cannot import name 'weighted_percentile' from 'skylineframe.prepare'`.

- [ ] **Step 3: `prepare.py` ersetzen**

`backend/skylineframe/prepare.py`:

```python
"""Clip features to the target square, repair geometry, form blocks and derive road/water areas.

Everything in this module is in local metres. The stage order follows spec §6:
clip -> fill heights -> assign parts -> resolve roofs -> blocks -> printability -> roads/water.
"""

import math
from dataclasses import dataclass, field, replace

import numpy as np
import shapely
from shapely import STRtree
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import make_valid

from .features import Block, Building, Features, Road, Water
from .fetch import estimate_height_m
from .project import square_local
from .spec import MIN_FEATURE_MM, FrameSpec, Mode

SIMPLIFY_TOLERANCE_MM = 0.05
# Fraction of the weld radius used to collapse the chords its round joins leave behind.
ARC_SIMPLIFY_FRACTION = 0.1
BLOCK_PERCENTILE = 0.25  # spec §6.4
ROOF_RECT_RATIO = 0.85  # spec §6.6
ROOF_SLOPE_FACTOR = 0.29  # 30° over the half width (spec §7)
ROOF_HEIGHT_MIN_M = 2.0
ROOF_HEIGHT_MAX_M = 6.0
DOME_HEIGHT_FACTOR = 0.5
ROUND_ROOF_SHAPES = ("dome", "round")


@dataclass
class Prepared:
    buildings: list[Building]  # individually printable footprints; valid Polygons in local metres
    blocks: list[Block] = field(default_factory=list)
    roads: list[Polygon] = field(default_factory=list)
    water: list[Polygon] = field(default_factory=list)
    footprint_coverage: float = 0.0  # building area in the model / building area in the square


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


def _is_printable(poly: Polygon, min_area_m2: float, half_feature_m: float) -> bool:
    """Large enough, and thick enough somewhere: eroding by half the minimum feature must leave something."""
    return poly.area >= min_area_m2 and not poly.buffer(-half_feature_m).is_empty


def _clip(buildings: list[Building], square: Polygon, tol_m: float) -> list[Building]:
    """Clip to the square, simplify and repair; one Building per resulting polygon.

    Nothing is dropped for being small here — the printability split happens after the blocks
    are formed, so a small footprint still contributes its area to its block (spec §6.5).
    """
    out: list[Building] = []
    for b in buildings:
        for poly in polygons_of(b.geom.intersection(square)):
            # Simplification is not guaranteed to preserve validity (notably for rings with holes),
            # so its result goes back through the same repair/flatten choke point.
            for piece in polygons_of(poly.simplify(tol_m, preserve_topology=True)):
                out.append(replace(b, geom=piece))
    return out


def estimate_missing_heights(buildings: list[Building]) -> None:
    """Fill height_m of outlines without any height tag from type and area (spec §5). In place.

    Parts are skipped: they take the height of their outline, or the spec default (spec §4).
    """
    for b in buildings:
        if b.height_m <= 0 and not b.is_part:
            b.height_m = estimate_height_m(b.kind, b.geom.area)


def assign_parts(buildings: list[Building], default_height_m: float) -> list[Building]:
    """Turn outlines with parts into (parts + remainder) and return the footprint list (spec §6.3).

    A part belongs to the outline that contains its centroid. Outlines with at least one part
    are not extruded as a whole any more: the parts are rendered, and what they leave of the
    outline becomes one remainder footprint per polygon at the outline's own height.
    """
    outlines = [b for b in buildings if not b.is_part]
    parts = [b for b in buildings if b.is_part]
    if not parts:
        return list(buildings)

    tree = STRtree([o.geom for o in outlines]) if outlines else None
    covered: dict[int, list[BaseGeometry]] = {}
    for p in parts:
        hit: int | None = None
        if tree is not None:
            # STRtree applies the predicate as input.predicate(tree_geom), so "within" returns
            # the outlines that contain the centroid. Nested outlines: the first hit wins.
            found = tree.query(p.geom.centroid, predicate="within")
            if len(found):
                hit = int(found[0])
        if hit is None:
            if p.height_m <= 0:
                p.height_m = default_height_m
            continue
        owner = outlines[hit]
        p.outline_id = owner.osm_id
        if p.height_m <= 0:
            p.height_m = owner.height_m
            p.height_is_top = owner.height_is_top
        covered.setdefault(hit, []).append(p.geom)

    footprints: list[Building] = list(parts)
    for i, owner in enumerate(outlines):
        if i not in covered:
            footprints.append(owner)
            continue
        remainder = owner.geom.difference(unary_union(covered[i]))
        for poly in polygons_of(remainder):
            # The remainder inherits the height but never the roof: the roof shape belongs to
            # the main body, and a roof on a leftover strip would be a spike. outline_id lets
            # scale.py see that a part standing on this remainder is supported.
            footprints.append(replace(owner, geom=poly, roof=None, rect=(), outline_id=owner.osm_id))
    return footprints


def minimum_rect(poly: Polygon) -> tuple[tuple[float, float], ...]:
    """The four corners of the minimum rotated rectangle, or () when there is none.

    shapely's oriented_envelope divides by zero for axis-aligned input and the suite turns
    warnings into errors, so the numpy error state is silenced around this one call.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        rect = shapely.minimum_rotated_rectangle(poly)
    if not isinstance(rect, Polygon) or rect.is_empty:
        return ()
    coords = list(rect.exterior.coords)[:4]
    if len(coords) != 4:
        return ()
    return tuple((float(x), float(y)) for x, y in coords)


def rect_sides(rect: tuple[tuple[float, float], ...]) -> tuple[float, float]:
    """(long side, short side) of a rectangle given by four corners."""
    (x0, y0), (x1, y1), (x2, y2) = rect[0], rect[1], rect[2]
    a = math.hypot(x1 - x0, y1 - y0)
    b = math.hypot(x2 - x1, y2 - y1)
    return max(a, b), min(a, b)


def default_roof_height_m(shape: str, short_side_m: float) -> float:
    """Roof height when neither roof:height nor roof:levels is tagged (spec §7)."""
    if shape in ROUND_ROOF_SHAPES:
        return DOME_HEIGHT_FACTOR * short_side_m
    return min(max(ROOF_SLOPE_FACTOR * short_side_m, ROOF_HEIGHT_MIN_M), ROOF_HEIGHT_MAX_M)


def resolve_roof(b: Building, rotation_deg: float) -> None:
    """Fill eaves_m, ridge_m, rect and the roof height of one footprint (spec §4/§6.6/§7). In place.

    A footprint that fills less than ROOF_RECT_RATIO of its minimum rotated rectangle loses its
    roof: the roof body is built over that rectangle, and over an L or a comb it would stand in
    the courtyard rather than on the building.
    """
    b.eaves_m = b.ridge_m = b.height_m
    if b.roof is None:
        return
    rect = minimum_rect(b.geom)
    if len(rect) != 4:
        b.roof = None
        return
    long_m, short_m = rect_sides(rect)
    rect_area = long_m * short_m
    if rect_area <= 0 or b.geom.area / rect_area < ROOF_RECT_RATIO:
        b.roof = None
        return

    roof = b.roof
    if roof.height_m <= 0:
        roof = replace(roof, height_m=default_roof_height_m(roof.shape, short_m))
    if roof.direction_deg is not None:
        # project.py rotates the world by +rotation_deg, so a compass bearing in the local
        # frame is the tagged bearing minus rotation_deg.
        roof = replace(roof, direction_deg=(roof.direction_deg - rotation_deg) % 360)
    b.rect = rect
    if b.height_is_top:
        b.ridge_m = b.height_m
        b.eaves_m = max(b.height_m - roof.height_m, 0.5 * b.height_m)
        roof = replace(roof, height_m=b.ridge_m - b.eaves_m)
    else:
        b.eaves_m = b.height_m
        b.ridge_m = b.height_m + roof.height_m
    b.roof = roof


def weighted_percentile(values: list[float], weights: list[float], q: float) -> float:
    """The q-quantile of `values` weighted by `weights`, 'lower' convention.

    Sort by value, accumulate the weights and return the first value whose cumulative weight
    reaches q of the total. With equal weights this is the plain q-quantile.
    """
    pairs = sorted(zip(values, weights))
    target = q * sum(weights)
    cumulative = 0.0
    for value, weight in pairs:
        cumulative += weight
        if cumulative >= target:
            return value
    return pairs[-1][0]


def _close(area: BaseGeometry, radius_m: float) -> BaseGeometry:
    """Morphological close with round joins: dilate, then erode (spec §6.4)."""
    if area.is_empty:
        return area
    return area.buffer(radius_m, join_style="round").buffer(-radius_m, join_style="round")


def build_blocks(
    footprints: list[Building],
    spec: FrameSpec,
    close_m: float,
    min_area_m2: float,
    half_feature_m: float,
) -> list[Block]:
    """One Block per connected group of footprints, at the weighted 25th percentile eaves height.

    A block that is itself unprintable (a single shed in the middle of a field) is dropped —
    it would be a sliver in the mesh, and footprint_coverage reports what that costs.
    """
    if not footprints:
        return []
    closed = _close(unary_union([b.geom for b in footprints]), close_m)
    polys = [p for p in polygons_of(closed) if _is_printable(p, min_area_m2, half_feature_m)]
    if not polys:
        return []

    tree = STRtree(polys)
    members: dict[int, list[Building]] = {}
    for b in footprints:
        found = tree.query(b.geom.representative_point(), predicate="within")
        if len(found):
            members.setdefault(int(found[0]), []).append(b)

    floor_m = spec.min_building_height_mm / spec.scale
    blocks: list[Block] = []
    for i, poly in enumerate(polys):
        inside = members.get(i, [])
        if not inside:
            continue
        height = weighted_percentile(
            [b.eaves_m for b in inside], [b.geom.area for b in inside], BLOCK_PERCENTILE
        )
        blocks.append(Block(geom=poly, height_m=max(height, floor_m)))
    return blocks


def _coverage(footprints: list[Building], blocks: list[Block]) -> float:
    """Share of the building area in the square that the kept blocks still carry (spec §6)."""
    if not footprints:
        return 0.0
    total = unary_union([b.geom for b in footprints])
    if total.is_empty or total.area <= 0:
        return 0.0
    if not blocks:
        return 0.0
    kept = unary_union([b.geom for b in blocks])
    return float(kept.intersection(total).area / total.area)


def _weld(area: BaseGeometry, weld_m: float) -> BaseGeometry:
    """Morphological close: merge pockets that only touch at a point and drop hairline gaps.

    Two pockets meeting in a single point are extruded as two prisms and leave a zero-thickness
    plate wall between them, which no slicer can print. Closing fuses them into one pocket and
    costs at most weld_m of outline accuracy.

    The close approximates its round joins with chords, which multiplies the vertex count of
    every pocket outline (and with it the triangle count of the whole model) without adding any
    shape the printer could resolve. Collapsing them again with a tolerance an order of magnitude
    below the outline tolerance removes them without reopening what the close merged.
    """
    if area.is_empty:
        return area
    closed = area.buffer(weld_m).buffer(-weld_m)
    return closed.simplify(weld_m * ARC_SIMPLIFY_FRACTION, preserve_topology=True)


def _clearance(blocked: BaseGeometry, weld_m: float) -> BaseGeometry:
    """Grow what blocks a pocket, so the pocket wall never coincides with the wall that bounds it.

    Buildings are sunk into the plate, so a pocket cut exactly at a building outline puts the
    building wall and the pocket wall in the same plane: the union of the two solids then has to
    resolve a zero-thickness wall and leaves degenerate faces and non-manifold edges behind.
    A hairline gap of SIMPLIFY_TOLERANCE_MM in print space removes that coincidence entirely.
    """
    # Mitre joins, so growing a footprint keeps its corner count instead of replacing every
    # corner with a fan of arc vertices.
    return blocked if blocked.is_empty else blocked.buffer(weld_m, join_style="mitre")


def _road_areas(
    roads: list[Road],
    spec: FrameSpec,
    square: Polygon,
    blocked: BaseGeometry,
    min_area_m2: float,
    weld_m: float,
) -> list[Polygon]:
    if not roads:
        return []
    buffered = [
        r.geom.buffer(spec.road_width_mm[r.cls] / spec.scale / 2, cap_style="flat", join_style="round")
        for r in roads
    ]
    area = unary_union(buffered).intersection(square).difference(_clearance(blocked, weld_m))
    return [p for p in polygons_of(_weld(area, weld_m)) if p.area >= min_area_m2]


def _water_areas(
    water: list[Water], square: Polygon, blocked: BaseGeometry, min_area_m2: float, weld_m: float
) -> list[Polygon]:
    if not water:
        return []
    area = unary_union([w.geom for w in water]).intersection(square).difference(_clearance(blocked, weld_m))
    return [p for p in polygons_of(_weld(area, weld_m)) if p.area >= min_area_m2]


def prepare(features: Features, spec: FrameSpec) -> Prepared:
    square = square_local(spec)
    scale = spec.scale
    tol_m = SIMPLIFY_TOLERANCE_MM / scale
    half_feature_m = MIN_FEATURE_MM / 2 / scale
    min_area_m2 = spec.min_footprint_area_mm2 / scale**2
    close_m = MIN_FEATURE_MM / 2 / scale

    clipped = _clip(features.buildings, square, tol_m)
    if not spec.parts:
        clipped = [b for b in clipped if not b.is_part]
    if not spec.roofs:
        for b in clipped:
            b.roof = None
    estimate_missing_heights(clipped)
    footprints = assign_parts(clipped, spec.default_building_height_m)
    for b in footprints:
        resolve_roof(b, spec.rotation_deg)

    blocks = build_blocks(footprints, spec, close_m, min_area_m2, half_feature_m)
    buildings = [b for b in footprints if _is_printable(b.geom, min_area_m2, half_feature_m)]
    coverage = _coverage(footprints, blocks)
    if spec.mode != Mode.full:
        return Prepared(buildings=buildings, blocks=blocks, footprint_coverage=coverage)

    recess_min_area_m2 = MIN_FEATURE_MM**2 / scale**2
    weld_m = SIMPLIFY_TOLERANCE_MM / scale
    # Precedence stays buildings > roads > water, and "buildings" is now the union of the
    # blocks: a groove through a welded courtyard would cut the block wall open (spec §6.7).
    blocked = unary_union([b.geom for b in blocks]) if blocks else Polygon()
    roads = _road_areas(features.roads, spec, square, blocked, recess_min_area_m2, weld_m)
    blocked_for_water = unary_union([blocked, *roads])
    water = _water_areas(features.water, square, blocked_for_water, recess_min_area_m2, weld_m)
    return Prepared(
        buildings=buildings, blocks=blocks, roads=roads, water=water, footprint_coverage=coverage
    )
```

- [ ] **Step 4: Tests grün**

Run: `cd backend && uv run pytest tests/test_prepare.py -q`
Expected: PASS (alle, inklusive der unveränderten Straßen-/Wasser-Tests).

Falls `test_water_is_cut_out_under_buildings_and_roads` scheitert: die Blockbildung ersetzt das Gebäude durch seinen Close; für ein einzelnes konvexes Rechteck ist der Close flächengleich, die Abweichung liegt bei < 1e-4 relativ. Ein größerer Fehler bedeutet, dass `close_m` falsch berechnet ist (`MIN_FEATURE_MM / 2 / scale`, nicht `MIN_FEATURE_MM / scale`).

- [ ] **Step 5: Volle Suite grün**

Run: `cd backend && uv run pytest -q`
Expected: PASS. `tests/test_scale.py` und `tests/test_mesh.py` laufen noch gegen das alte Prism-Modell — das ist Task 5; sie dürfen hier nicht rot werden, weil `Prepared` nur neue Felder mit Defaults bekommen hat.

- [ ] **Step 6: Commit**

```bash
git add backend/skylineframe/prepare.py backend/tests/test_prepare.py
git commit -m "feat(prepare): block welding, building parts, height estimates and roof eligibility

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Dachkörper (`roofs.py`)

**Files:**
- Create: `backend/skylineframe/roofs.py`
- Test: `backend/tests/test_roofs.py`

**Interfaces:**
- Consumes: nur `manifold3d` und `math` — bewusst keine Imports aus `mesh.py`, sonst entsteht ein Zyklus (`mesh` importiert `roofs`). Der Zuschnitt-Körper wird als Parameter `clip` hereingereicht.
- Produces:
  - `roofs.SHAPES: frozenset[str]` = {gabled, hipped, half_hipped, pyramidal, skillion, mansard, gambrel, dome, round}
  - `roofs.MIN_ROOF_MM = 0.3`, `roofs.DOME_SEGMENTS = 24`, `roofs.ROUND_SEGMENTS = 12`
  - `roofs.roof_points(rect_mm: Sequence[tuple[float, float]], z_eaves_mm: float, z_ridge_mm: float, shape: str, direction_deg: float | None = None) -> list[tuple[float, float, float]]`
  - `roofs.roof_hull(rect_mm, z_eaves_mm, z_ridge_mm, shape, direction_deg=None) -> m3d.Manifold | None`
  - `roofs.roof_solid(rect_mm, z_eaves_mm, z_ridge_mm, shape, direction_deg=None, clip: m3d.Manifold | None = None) -> m3d.Manifold | None`
- `rect_mm` sind die vier Ecken in Reihenfolge (aus `prepare.minimum_rect`, mit `scale` multipliziert). `direction_deg` ist bereits in den lokalen Rahmen gedreht (0 = +y, im Uhrzeigersinn).

- [ ] **Step 1: Failing Tests schreiben**

`backend/tests/test_roofs.py`:

```python
import manifold3d as m3d
import pytest
from shapely.geometry import Polygon

from skylineframe.mesh import prism
from skylineframe.roofs import MIN_ROOF_MM, SHAPES, roof_hull, roof_points, roof_solid

# 10 x 6 mm rectangle, long axis along x, eaves at 0, ridge at 3 mm.
RECT = ((0.0, 0.0), (10.0, 0.0), (10.0, 6.0), (0.0, 6.0))
ZE, ZR = 0.0, 3.0


@pytest.mark.parametrize(
    "shape,expected",
    [
        # 4 corners at z=0 plus the two ridge points at the short-side midpoints. Prismatoid:
        # V = h/6 * (A_bottom + 4*A_mid + A_top) = 3/6 * (60 + 4*30 + 0) = 90.
        ("gabled", 90.0),
        # ridge inset 0.5 * short side = 3 mm, so the ridge is 4 mm long:
        # A_mid = ((10+4)/2) * (6/2) = 21  =>  3/6 * (60 + 84 + 0) = 72.
        ("hipped", 72.0),
        # ridge inset 0.25 * 6 = 1.5 mm (ridge 7 mm) gives 3/6 * (60 + 4*25.5) = 81, plus the
        # two gable points at 0.6 * 3 = 1.8 mm, each a pyramid of 1/3 * 10.0623 * 0.80498 = 2.7.
        ("half_hipped", 86.4),
        # pyramid over the full rectangle: 1/3 * 60 * 3 = 60.
        ("pyramidal", 60.0),
        # wedge: the whole rectangle raised on one long side => 10 * 6 * 3 / 2 = 90.
        ("skillion", 90.0),
        # hipped with inset 0.2*6 = 1.2 plus a ring at 0.7*3 = 2.1 mm on 0.8 of both half axes.
        # Frustum 0 -> 2.1: 2.1/6 * (60 + 4*48.6 + 38.4) = 102.48; cap 2.1 -> 3:
        # 0.9/6 * (38.4 + 4*18.72) = 16.992; the convex hull also fills the dent their corners
        # leave between them, which is why the hull is 121.2 and not 119.472.
        ("mansard", 121.2),
        # like mansard, but the ring keeps the full length (0.8 only across): 133.272.
        ("gambrel", 133.272),
        # sphere(1, 24) scaled to (5, 3, 3) and trimmed at the eaves. The analytic half
        # ellipsoid is 2/3 * pi * 5 * 3 * 3 = 94.2478; the 24-segment polyhedron reaches 90.547.
        ("dome", 90.547),
        # half cylinder over 12 segments per arc: 0.5 * 3 * 3 * sin(pi/12) * 12 * 10 = 139.7623.
        ("round", 139.762),
    ],
)
def test_roof_volumes_over_a_10x6_rectangle(shape, expected):
    solid = roof_hull(RECT, ZE, ZR, shape)
    assert solid is not None
    assert solid.volume() == pytest.approx(expected, rel=1e-4)


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_roof_stays_inside_the_rectangle_and_between_eaves_and_ridge(shape):
    solid = roof_hull(RECT, ZE, ZR, shape)
    xmin, ymin, zmin, xmax, ymax, zmax = solid.bounding_box()
    assert (xmin, ymin) == pytest.approx((0.0, 0.0), abs=1e-6)
    assert (xmax, ymax) == pytest.approx((10.0, 6.0), abs=1e-6)
    assert (zmin, zmax) == pytest.approx((ZE, ZR), abs=1e-6)


def test_roof_sits_on_a_raised_eaves_plane():
    solid = roof_hull(RECT, 12.0, 15.0, "gabled")
    _, _, zmin, _, _, zmax = solid.bounding_box()
    assert (zmin, zmax) == pytest.approx((12.0, 15.0))
    assert solid.volume() == pytest.approx(90.0)


def test_roof_is_clipped_to_the_footprint():
    # L-shaped footprint inside the same rectangle. The gabled roof is 3 - |y - 3| mm high, so
    # the part over x in [0,10], y in [0,3] is 10 * 4.5 = 45 and the part over x in [0,4],
    # y in [3,6] is 4 * 4.5 = 18 => 63 mm³ instead of the unclipped 90.
    footprint = Polygon([(0, 0), (10, 0), (10, 3), (4, 3), (4, 6), (0, 6)])
    clip = prism(footprint, ZR - ZE + 0.01, z0=ZE)
    solid = roof_solid(RECT, ZE, ZR, "gabled", clip=clip)
    assert solid is not None
    assert solid.volume() == pytest.approx(63.0, rel=1e-6)


def test_roof_below_the_minimum_height_is_skipped():
    assert roof_hull(RECT, 0.0, MIN_ROOF_MM - 0.01, "gabled") is None
    assert roof_solid(RECT, 0.0, 0.2, "gabled") is None


def test_unknown_shape_and_degenerate_rectangle_are_skipped():
    assert roof_hull(RECT, ZE, ZR, "brezel") is None
    assert roof_hull(((0.0, 0.0), (0.0, 0.0), (0.0, 0.0), (0.0, 0.0)), ZE, ZR, "gabled") is None
    assert roof_hull(((0.0, 0.0), (10.0, 0.0)), ZE, ZR, "gabled") is None


def test_ridge_runs_along_the_long_axis_by_default():
    tops = [p for p in roof_points(RECT, ZE, ZR, "gabled") if p[2] == ZR]
    assert sorted(tops) == [(0.0, 3.0, 3.0), (10.0, 3.0, 3.0)]


def test_roof_direction_turns_the_ridge():
    # roof:direction is the downhill direction, so a roof facing east (90°) has its ridge
    # running north-south — here along the short axis of the rectangle.
    tops = [p for p in roof_points(RECT, ZE, ZR, "gabled", direction_deg=90.0) if p[2] == ZR]
    assert sorted(tops) == [(5.0, 0.0, 3.0), (5.0, 6.0, 3.0)]


def test_skillion_rises_away_from_its_direction():
    default_top = sorted(p for p in roof_points(RECT, ZE, ZR, "skillion") if p[2] == ZR)
    assert default_top == [(0.0, 6.0, 3.0), (10.0, 6.0, 3.0)]
    # facing north (+y) means the roof slopes down towards +y, so the south edge is the high one
    north = sorted(p for p in roof_points(RECT, ZE, ZR, "skillion", direction_deg=0.0) if p[2] == ZR)
    assert north == [(0.0, 0.0, 3.0), (10.0, 0.0, 3.0)]


def test_clip_that_misses_the_roof_yields_none():
    far_away = prism(Polygon([(100, 100), (110, 100), (110, 106), (100, 106)]), 3.0, z0=ZE)
    assert roof_solid(RECT, ZE, ZR, "gabled", clip=far_away) is None


def test_every_shape_produces_a_valid_solid():
    for shape in sorted(SHAPES):
        solid = roof_hull(RECT, ZE, ZR, shape)
        assert solid.status() == m3d.Error.NoError
        assert not solid.is_empty()
        assert solid.volume() > 0
```

- [ ] **Step 2: Fehlschlag bestätigen**

Run: `cd backend && uv run pytest tests/test_roofs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'skylineframe.roofs'`.

- [ ] **Step 3: `roofs.py` schreiben**

`backend/skylineframe/roofs.py`:

```python
"""Roof solids over the minimum rotated rectangle of a footprint (spec §7).

Everything here is in print millimetres and independent of shapely: the caller hands in the
four rectangle corners and — for the clip — a ready-made footprint prism, so this module never
imports mesh.py (which imports this one).
"""

import math
from collections.abc import Sequence

import manifold3d as m3d

MIN_ROOF_MM = 0.3  # below this a roof is not visible in print and is skipped (spec §7)
DOME_SEGMENTS = 24  # circular segments of the sphere a dome is scaled from
ROUND_SEGMENTS = 12  # segments per half-circle arc of a round roof
HIP_INSET = 0.5  # ridge inset as a fraction of the short side
HALF_HIP_INSET = 0.25
MANSARD_INSET = 0.2
GABLE_HEIGHT_FRACTION = 0.6  # half-hipped gable point, above the eaves
KNEE_HEIGHT_FRACTION = 0.7  # mansard / gambrel second ring, above the eaves
KNEE_WIDTH_FRACTION = 0.8  # mansard / gambrel second ring, at this fraction of the half width

SHAPES: frozenset[str] = frozenset(
    {"gabled", "hipped", "half_hipped", "pyramidal", "skillion", "mansard", "gambrel", "dome", "round"}
)

Corner = tuple[float, float]
Point3 = tuple[float, float, float]
Frame = tuple[Corner, Corner, float, Corner, float]  # centre, ridge axis, half length, cross axis, half width


def _frame(rect: Sequence[Corner]) -> Frame | None:
    """Centre and unit axes of the rectangle; the ridge axis is the long one by default."""
    if len(rect) != 4:
        return None
    (x0, y0), (x1, y1), (x2, y2) = rect[0], rect[1], rect[2]
    e0 = (x1 - x0, y1 - y0)
    e1 = (x2 - x1, y2 - y1)
    len0 = math.hypot(*e0)
    len1 = math.hypot(*e1)
    if len0 <= 0 or len1 <= 0:
        return None
    centre = (sum(p[0] for p in rect) / 4, sum(p[1] for p in rect) / 4)
    if len0 >= len1:
        long_v, long_len, short_v, short_len = e0, len0, e1, len1
    else:
        long_v, long_len, short_v, short_len = e1, len1, e0, len0
    u = (long_v[0] / long_len, long_v[1] / long_len)
    v = (short_v[0] / short_len, short_v[1] / short_len)
    return centre, u, long_len / 2, v, short_len / 2


def _direction_vector(direction_deg: float) -> Corner:
    """Compass bearing in the local frame (0 = +y, clockwise) as a unit vector."""
    rad = math.radians(direction_deg)
    return math.sin(rad), math.cos(rad)


def _oriented(frame: Frame, direction_deg: float | None) -> Frame:
    """Swap the axes when roof:direction asks for a ridge across the long axis.

    roof:direction names the direction the roof faces, i.e. where it slopes down to, so the
    ridge runs perpendicular to it: the axis with the smaller |dot| against it wins.
    """
    centre, u, a, v, b = frame
    if direction_deg is None:
        return frame
    d = _direction_vector(direction_deg)
    if abs(u[0] * d[0] + u[1] * d[1]) > abs(v[0] * d[0] + v[1] * d[1]):
        return centre, v, b, u, a
    return frame


def _skillion_sign(v: Corner, direction_deg: float | None) -> float:
    """Which of the two long sides is the high one: the one away from the roof direction."""
    if direction_deg is None:
        return 1.0
    d = _direction_vector(direction_deg)
    return -1.0 if (v[0] * d[0] + v[1] * d[1]) > 0 else 1.0


def roof_points(
    rect_mm: Sequence[Corner],
    z_eaves_mm: float,
    z_ridge_mm: float,
    shape: str,
    direction_deg: float | None = None,
) -> list[Point3]:
    """The convex-hull points of one roof body. Empty for dome (a sphere) and unknown shapes."""
    frame = _frame(rect_mm)
    if frame is None or shape not in SHAPES or shape == "dome":
        return []
    centre, u, a, v, b = _oriented(frame, direction_deg)
    height = z_ridge_mm - z_eaves_mm
    short = 2 * b

    def at(du: float, dv: float, z: float) -> Point3:
        return (centre[0] + u[0] * du + v[0] * dv, centre[1] + u[1] * du + v[1] * dv, z)

    # Every shape stands on the full rectangle at the eaves: the raised points alone would be a
    # flat sheet (skillion) or a body hanging in the air.
    base = [at(-a, -b, z_eaves_mm), at(a, -b, z_eaves_mm), at(a, b, z_eaves_mm), at(-a, b, z_eaves_mm)]

    if shape == "gabled":
        return base + [at(-a, 0.0, z_ridge_mm), at(a, 0.0, z_ridge_mm)]
    if shape == "hipped":
        inset = min(HIP_INSET * short, a)
        return base + [at(-(a - inset), 0.0, z_ridge_mm), at(a - inset, 0.0, z_ridge_mm)]
    if shape == "half_hipped":
        inset = min(HALF_HIP_INSET * short, a)
        gable_z = z_eaves_mm + GABLE_HEIGHT_FRACTION * height
        return base + [
            at(-(a - inset), 0.0, z_ridge_mm),
            at(a - inset, 0.0, z_ridge_mm),
            at(-a, 0.0, gable_z),
            at(a, 0.0, gable_z),
        ]
    if shape == "pyramidal":
        return base + [at(0.0, 0.0, z_ridge_mm)]
    if shape == "skillion":
        sign = _skillion_sign(v, direction_deg)
        return base + [at(-a, sign * b, z_ridge_mm), at(a, sign * b, z_ridge_mm)]
    if shape in ("mansard", "gambrel"):
        inset = min(MANSARD_INSET * short, a)
        knee_z = z_eaves_mm + KNEE_HEIGHT_FRACTION * height
        # The gambrel keeps its full length at the knee (gable ends), the mansard pulls in on
        # both axes — that is the only difference between the two.
        knee_a = KNEE_WIDTH_FRACTION * a if shape == "mansard" else a
        knee_b = KNEE_WIDTH_FRACTION * b
        ring = [at(sx * knee_a, sy * knee_b, knee_z) for sx in (-1.0, 1.0) for sy in (-1.0, 1.0)]
        return base + [at(-(a - inset), 0.0, z_ridge_mm), at(a - inset, 0.0, z_ridge_mm)] + ring
    # round: two half-circle arcs, one at each end of the ridge
    arcs = [math.pi * k / ROUND_SEGMENTS for k in range(ROUND_SEGMENTS + 1)]
    return [
        at(sx * a, b * math.cos(t), z_eaves_mm + height * math.sin(t))
        for sx in (-1.0, 1.0)
        for t in arcs
    ]


def _dome(frame: Frame, z_eaves_mm: float, z_ridge_mm: float) -> m3d.Manifold:
    centre, u, a, _v, b = frame
    angle = math.degrees(math.atan2(u[1], u[0]))
    return (
        m3d.Manifold.sphere(1.0, DOME_SEGMENTS)
        .scale((a, b, z_ridge_mm - z_eaves_mm))
        .rotate((0.0, 0.0, angle))
        .translate((centre[0], centre[1], z_eaves_mm))
        # trim_by_plane keeps the half space with n.p >= offset, i.e. everything above the eaves.
        .trim_by_plane((0.0, 0.0, 1.0), z_eaves_mm)
    )


def roof_hull(
    rect_mm: Sequence[Corner],
    z_eaves_mm: float,
    z_ridge_mm: float,
    shape: str,
    direction_deg: float | None = None,
) -> m3d.Manifold | None:
    """The bare roof body between the eaves and the ridge plane, or None when there is none."""
    if shape not in SHAPES or z_ridge_mm - z_eaves_mm < MIN_ROOF_MM:
        return None
    frame = _frame(rect_mm)
    if frame is None:
        return None
    if shape == "dome":
        return _dome(frame, z_eaves_mm, z_ridge_mm)
    points = roof_points(rect_mm, z_eaves_mm, z_ridge_mm, shape, direction_deg)
    if len(points) < 4:
        return None
    return m3d.Manifold.hull_points(points)


def roof_solid(
    rect_mm: Sequence[Corner],
    z_eaves_mm: float,
    z_ridge_mm: float,
    shape: str,
    direction_deg: float | None = None,
    clip: m3d.Manifold | None = None,
) -> m3d.Manifold | None:
    """roof_hull, cut down to `clip` (the footprint prism) when one is given.

    The rectangle may stick out over a footprint that fills only 85 % of it, and an overhanging
    roof edge would float next to the wall instead of sitting on it.
    """
    solid = roof_hull(rect_mm, z_eaves_mm, z_ridge_mm, shape, direction_deg)
    if solid is None:
        return None
    if clip is not None:
        solid = solid ^ clip
    if solid.status() != m3d.Error.NoError or solid.is_empty() or solid.volume() <= 0:
        return None
    return solid
```

- [ ] **Step 4: Tests grün**

Run: `cd backend && uv run pytest tests/test_roofs.py -q`
Expected: PASS (17 Tests inkl. Parametrisierungen).

Weicht ein Volumen ab, stimmt der Punktsatz nicht mit der Tabelle im Test überein — nicht die Erwartung anpassen, sondern die Punkte.

- [ ] **Step 5: Commit**

```bash
git add backend/skylineframe/roofs.py backend/tests/test_roofs.py
git commit -m "feat(roofs): hull-based roof bodies for the nine supported roof shapes

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Skalierung und Mesh — Blöcke, Teile, Dächer

**Files:**
- Modify: `backend/skylineframe/scale.py` (komplett ersetzen)
- Modify: `backend/skylineframe/mesh.py` (komplett ersetzen)
- Test: `backend/tests/test_scale.py` (anhängen)
- Test: `backend/tests/test_mesh.py` (anhängen)

**Interfaces:**
- Consumes: `prepare.Prepared` (Task 3), `roofs.roof_solid`, `roofs.MIN_ROOF_MM` (Task 4), `features.Block`.
- Produces:
  - `scale.ScaledRoof(rect_mm: tuple[tuple[float, float], ...], shape: str, z_eaves_mm: float, z_ridge_mm: float, direction_deg: float | None = None)`
  - `scale.Prism(geom: Polygon, height_mm: float, z0_mm: float = 0.0, roof: ScaledRoof | None = None)` — `height_mm` ist die Oberkante des senkrechten Körpers (Traufe), `z0_mm` die Unterkante.
  - `scale.Scaled(buildings: list[Prism], blocks: list[Prism] = [], roads: list[Polygon] = [], water: list[Polygon] = [])`
  - `scale.building_height_mm(height_m: float, spec: FrameSpec) -> float` (unverändert, mit Mindesthöhe)
  - `scale.raw_height_mm(height_m: float, spec: FrameSpec) -> float` (ohne Mindesthöhe, für Unterkanten und Firste)
  - `scale.scale_features(prepared: Prepared, spec: FrameSpec) -> Scaled`
  - `mesh.build_meshes(scaled: Scaled, spec: FrameSpec) -> MeshSet` (Signatur unverändert), `mesh.prism(poly, height, z0=0.0)`, `mesh.cross_section`, `mesh.union`, `mesh.to_trimesh` unverändert.

- [ ] **Step 1: Failing Tests für `scale.py` schreiben**

An `backend/tests/test_scale.py` anhängen. Der Import-Block oben lautet danach:

```python
import pytest
from shapely.geometry import box

from skylineframe.features import Block, Building, RoofSpec
from skylineframe.prepare import Prepared
from skylineframe.scale import building_height_mm, raw_height_mm, scale_features
from skylineframe.spec import FrameSpec
```


```python
def building(geom, **kw) -> Building:
    values = {"height_m": 20.0, "eaves_m": 20.0, "ridge_m": 20.0}
    values.update(kw)
    return Building(geom, **values)


def test_eaves_ridge_and_rect_are_scaled():
    b = building(
        box(-100, -100, 100, 100),
        height_m=20.0,
        eaves_m=20.0,
        ridge_m=26.0,
        roof=RoofSpec(shape="gabled", height_m=6.0, direction_deg=45.0),
        rect=((-100.0, -100.0), (100.0, -100.0), (100.0, 100.0), (-100.0, 100.0)),
    )
    out = scale_features(Prepared(buildings=[b]), spec())
    p = out.buildings[0]
    assert p.height_mm == pytest.approx(3.0)  # 20 m x 0.1 mm/m x 1.5
    assert p.z0_mm == 0.0
    assert p.roof is not None
    assert p.roof.shape == "gabled" and p.roof.direction_deg == 45.0
    assert p.roof.z_eaves_mm == pytest.approx(3.0)
    assert p.roof.z_ridge_mm == pytest.approx(3.9)  # 26 m x 0.15
    # pytest.approx rejects nested tuples, so the corners are compared flat.
    flat = [value for corner in p.roof.rect_mm for value in corner]
    assert flat == pytest.approx([-10.0, -10.0, 10.0, -10.0, 10.0, 10.0, -10.0, 10.0])


def test_roof_below_the_print_minimum_is_dropped():
    # 20 -> 20.1 m is 0.015 mm of ridge, far below the 0.3 mm a roof needs to be visible.
    b = building(
        box(-100, -100, 100, 100),
        ridge_m=20.1,
        roof=RoofSpec(shape="gabled", height_m=0.1),
        rect=((-100.0, -100.0), (100.0, -100.0), (100.0, 100.0), (-100.0, 100.0)),
    )
    assert scale_features(Prepared(buildings=[b]), spec()).buildings[0].roof is None


def test_supported_part_starts_at_its_min_height():
    lower = building(box(-100, -100, 100, 100), height_m=40.0, eaves_m=40.0, ridge_m=40.0, outline_id=7)
    upper = building(
        box(-50, -50, 50, 50), height_m=80.0, eaves_m=80.0, ridge_m=80.0, min_height_m=40.0, is_part=True, outline_id=7
    )
    out = scale_features(Prepared(buildings=[lower, upper]), spec())
    assert out.buildings[1].z0_mm == pytest.approx(6.0)  # 40 m x 0.15
    assert out.buildings[1].height_mm == pytest.approx(12.0)


def test_floating_part_is_extended_down_to_the_plate():
    # Nothing of the same outline stands under it, so min_height is ignored (spec §8).
    lonely = building(box(-50, -50, 50, 50), height_m=80.0, eaves_m=80.0, ridge_m=80.0, min_height_m=40.0, is_part=True, outline_id=7)
    out = scale_features(Prepared(buildings=[lonely]), spec())
    assert out.buildings[0].z0_mm == 0.0


def test_part_next_to_its_sibling_is_not_supported_by_it():
    # Same outline, but the two footprints do not overlap, so the upper one would float.
    lower = building(box(-100, -100, -10, 100), height_m=40.0, eaves_m=40.0, ridge_m=40.0, outline_id=7)
    beside = building(box(10, -50, 100, 50), height_m=80.0, eaves_m=80.0, ridge_m=80.0, min_height_m=40.0, is_part=True, outline_id=7)
    out = scale_features(Prepared(buildings=[lower, beside]), spec())
    assert out.buildings[1].z0_mm == 0.0


def test_part_whose_base_is_above_its_own_top_falls_back_to_the_plate():
    lower = building(box(-100, -100, 100, 100), height_m=90.0, eaves_m=90.0, ridge_m=90.0, outline_id=7)
    broken = building(box(-50, -50, 50, 50), height_m=20.0, eaves_m=20.0, ridge_m=20.0, min_height_m=60.0, is_part=True, outline_id=7)
    out = scale_features(Prepared(buildings=[lower, broken]), spec())
    assert out.buildings[1].z0_mm == 0.0


def test_blocks_are_scaled_like_buildings():
    prepared = Prepared(buildings=[], blocks=[Block(box(-500, -500, 500, 500), 10.0)])
    out = scale_features(prepared, spec())
    assert out.blocks[0].geom.bounds == pytest.approx((-50, -50, 50, 50))
    assert out.blocks[0].height_mm == pytest.approx(1.5)
    assert out.blocks[0].z0_mm == 0.0 and out.blocks[0].roof is None


def test_raw_height_has_no_minimum_but_keeps_the_cap():
    assert raw_height_mm(0.0, spec()) == 0.0
    assert raw_height_mm(2.0, spec()) == pytest.approx(0.3)  # building_height_mm would return 0.8
    assert raw_height_mm(5000.0, spec()) == pytest.approx(100.0)
```

- [ ] **Step 2: Fehlschlag bestätigen**

Run: `cd backend && uv run pytest tests/test_scale.py -q`
Expected: FAIL — `ImportError: cannot import name 'raw_height_mm' from 'skylineframe.scale'`.

- [ ] **Step 3: `scale.py` ersetzen**

`backend/skylineframe/scale.py`:

```python
"""Convert prepared features from local metres to print millimetres."""

from dataclasses import dataclass, field

import shapely.affinity
from shapely.geometry import Polygon

from .features import Building
from .prepare import Prepared
from .roofs import MIN_ROOF_MM
from .spec import FrameSpec


@dataclass
class ScaledRoof:
    rect_mm: tuple[tuple[float, float], ...]  # four corners of the minimum rotated rectangle
    shape: str
    z_eaves_mm: float
    z_ridge_mm: float
    direction_deg: float | None = None


@dataclass
class Prism:
    geom: Polygon  # footprint in mm, centred on the plate
    height_mm: float  # top of the vertical body (the eaves, when there is a roof)
    z0_mm: float = 0.0  # bottom of the body; > 0 only for a part that stands on something
    roof: ScaledRoof | None = None


@dataclass
class Scaled:
    buildings: list[Prism]
    blocks: list[Prism] = field(default_factory=list)
    roads: list[Polygon] = field(default_factory=list)
    water: list[Polygon] = field(default_factory=list)


def building_height_mm(height_m: float, spec: FrameSpec) -> float:
    """Print height of one building, never below the printable minimum and never above the plate.

    The upper cap is the last line of defence against a mistagged height: a tower taller than
    the plate is wide turns the model into an unprintable spike.
    """
    raw = round(height_m * spec.scale * spec.z_exaggeration, 2)
    return min(max(raw, spec.min_building_height_mm), spec.plate_size_mm)


def raw_height_mm(height_m: float, spec: FrameSpec) -> float:
    """Same scaling without the printable minimum: for bottoms and ridges.

    A min_height run through building_height_mm would lift every ground-level footprint by
    min_building_height_mm and tear the model off the plate.
    """
    return min(round(height_m * spec.scale * spec.z_exaggeration, 2), spec.plate_size_mm)


def _scale_geom(geom: Polygon, factor: float) -> Polygon:
    return shapely.affinity.scale(geom, xfact=factor, yfact=factor, origin=(0, 0))


def _by_outline(buildings: list[Building]) -> dict[int, list[Building]]:
    groups: dict[int, list[Building]] = {}
    for b in buildings:
        if b.outline_id is not None:
            groups.setdefault(b.outline_id, []).append(b)
    return groups


def _is_supported(b: Building, siblings: list[Building]) -> bool:
    """True when another footprint of the same outline reaches up to the bottom of `b`.

    Parts start in the air by design (a setback tower stands on its base). A part with nothing
    below it is a tagging artefact, and printing it floating is impossible, so it is extended
    down to the plate instead (spec §8).
    """
    for other in siblings:
        if other is b:
            continue
        if other.min_height_m < b.min_height_m and other.eaves_m >= b.min_height_m:
            if other.geom.intersection(b.geom).area > 0:
                return True
    return False


def _roof_of(b: Building, eaves_mm: float, spec: FrameSpec) -> ScaledRoof | None:
    if b.roof is None or len(b.rect) != 4:
        return None
    ridge_mm = max(raw_height_mm(b.ridge_m, spec), eaves_mm)
    if ridge_mm - eaves_mm < MIN_ROOF_MM:
        return None
    s = spec.scale
    return ScaledRoof(
        rect_mm=tuple((x * s, y * s) for x, y in b.rect),
        shape=b.roof.shape,
        z_eaves_mm=eaves_mm,
        z_ridge_mm=ridge_mm,
        direction_deg=b.roof.direction_deg,
    )


def _building_prism(b: Building, groups: dict[int, list[Building]], spec: FrameSpec) -> Prism:
    eaves_mm = building_height_mm(b.eaves_m, spec)
    z0_mm = 0.0
    if b.min_height_m > 0 and b.outline_id is not None and _is_supported(b, groups.get(b.outline_id, [])):
        z0_mm = raw_height_mm(b.min_height_m, spec)
        if z0_mm >= eaves_mm:  # mistagged: the part would have no body at all
            z0_mm = 0.0
    return Prism(
        geom=_scale_geom(b.geom, spec.scale),
        height_mm=eaves_mm,
        z0_mm=z0_mm,
        roof=_roof_of(b, eaves_mm, spec),
    )


def scale_features(prepared: Prepared, spec: FrameSpec) -> Scaled:
    s = spec.scale
    groups = _by_outline(prepared.buildings)
    return Scaled(
        buildings=[_building_prism(b, groups, spec) for b in prepared.buildings],
        blocks=[Prism(_scale_geom(bl.geom, s), building_height_mm(bl.height_m, spec)) for bl in prepared.blocks],
        roads=[_scale_geom(p, s) for p in prepared.roads],
        water=[_scale_geom(p, s) for p in prepared.water],
    )
```

- [ ] **Step 4: Failing Tests für `mesh.py` schreiben**

An `backend/tests/test_mesh.py` anhängen (Import-Block oben um `ScaledRoof` erweitern):

```python
RECT_MM = ((-5.0, -3.0), (5.0, -3.0), (5.0, 3.0), (-5.0, 3.0))


def test_block_and_building_are_unioned():
    scaled = Scaled(
        buildings=[Prism(box(-5, -5, 5, 5), 10.0)],
        blocks=[Prism(box(-10, -10, 10, 10), 4.0)],
    )
    ms = build_meshes(scaled, spec())
    # block 20x20x4 = 1600, building 10x10x10 = 1000, overlap 10x10x4 = 400 counted once
    assert ms.buildings.volume() == pytest.approx(1600 + 1000 - 400, rel=1e-6)
    assert ms.single.volume() == pytest.approx(PLATE_VOLUME + 2200, rel=1e-6)
    tm = to_trimesh(ms.single)
    assert tm.is_watertight and tm.is_volume


def test_block_alone_is_enough_for_a_model():
    ms = build_meshes(Scaled(buildings=[], blocks=[Prism(box(-10, -10, 10, 10), 2.0)]), spec())
    assert ms.buildings.volume() == pytest.approx(400 * 2, rel=1e-6)


def test_part_standing_on_a_lower_part_is_one_body():
    scaled = Scaled(
        buildings=[Prism(box(-10, -10, 10, 10), 5.0), Prism(box(-5, -5, 5, 5), 12.0, z0_mm=5.0)]
    )
    ms = build_meshes(scaled, spec())
    assert ms.buildings.volume() == pytest.approx(400 * 5 + 100 * 7, rel=1e-6)
    tm = to_trimesh(ms.single)
    assert tm.is_watertight and tm.is_volume


def test_roof_volume_is_added_on_top_of_the_body():
    roof = ScaledRoof(rect_mm=RECT_MM, shape="gabled", z_eaves_mm=2.0, z_ridge_mm=5.0)
    ms = build_meshes(Scaled(buildings=[Prism(box(-5, -3, 5, 3), 2.0, roof=roof)]), spec())
    # body 10 x 6 x 2 = 120, gabled roof over the same rectangle at 3 mm = 90
    assert ms.buildings.volume() == pytest.approx(120 + 90, rel=1e-6)
    xmin, ymin, zmin, xmax, ymax, zmax = ms.buildings.bounding_box()
    assert zmax == pytest.approx(5.0)
    tm = to_trimesh(ms.single)
    assert tm.is_watertight and tm.is_volume


def test_roof_is_clipped_to_a_footprint_that_is_smaller_than_its_rectangle():
    # The footprint is the left half of the rectangle, so only half the roof survives.
    roof = ScaledRoof(rect_mm=RECT_MM, shape="gabled", z_eaves_mm=2.0, z_ridge_mm=5.0)
    ms = build_meshes(Scaled(buildings=[Prism(box(-5, -3, 0, 3), 2.0, roof=roof)]), spec())
    assert ms.buildings.volume() == pytest.approx(5 * 6 * 2 + 45, rel=1e-6)


def test_everything_together_stays_watertight():
    roof = ScaledRoof(rect_mm=RECT_MM, shape="hipped", z_eaves_mm=3.0, z_ridge_mm=6.0)
    scaled = Scaled(
        buildings=[
            Prism(box(-5, -3, 5, 3), 3.0, roof=roof),
            Prism(box(-3, -2, 3, 2), 9.0, z0_mm=3.0),
        ],
        blocks=[Prism(box(-12, -12, 12, 12), 1.5)],
        roads=[box(-50, 20, 50, 21)],
        water=[box(-50, -50, -30, -30)],
    )
    ms = build_meshes(scaled, spec())
    tm = to_trimesh(ms.single)
    assert tm.is_watertight and tm.is_volume
    assert tm.volume == pytest.approx(ms.single.volume(), rel=1e-6)
    assert list(ms.parts()) == ["base", "buildings", "water", "roads"]
```

- [ ] **Step 5: Fehlschlag bestätigen**

Run: `cd backend && uv run pytest tests/test_mesh.py -q`
Expected: FAIL — `TypeError: Prism.__init__() got an unexpected keyword argument 'z0_mm'` bzw. `ImportError` für `ScaledRoof` (Schritt 3 liefert beides, sobald `mesh.py` sie benutzt).

- [ ] **Step 6: `mesh.py` ersetzen**

`backend/skylineframe/mesh.py`:

```python
"""Solid modelling with manifold3d. Plate top is z = 0; buildings rise above, recesses go below."""

from dataclasses import dataclass

import manifold3d as m3d
import numpy as np
import trimesh
from shapely.geometry import Polygon

from .errors import MeshError
from .roofs import roof_solid
from .scale import Prism, Scaled
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
    rings = [ring for ring in rings if len(set(ring)) >= 3]  # drop degenerate rings
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


def recess(polys: list[Polygon], depth: float) -> tuple[m3d.Manifold, m3d.Manifold] | None:
    """Return (cutter, inlay), or None when nothing printable is left.

    The cutter overshoots above z=0; the inlay fills the recess exactly. Zero-area polygons
    cannot be extruded at all, so they are dropped; a part made only of those is simply absent
    rather than a hard failure.
    """
    polys = [p for p in polys if p.area > 0]
    if not polys:
        return None
    cutter = union([prism(p, depth + EPS, z0=-depth) for p in polys])
    inlay = union([prism(p, depth, z0=-depth) for p in polys])
    return cutter, inlay


def _roof_body(p: Prism) -> m3d.Manifold | None:
    """The roof of one footprint, cut down to the footprint itself (spec §7)."""
    if p.roof is None:
        return None
    clip = prism(p.geom, p.roof.z_ridge_mm - p.roof.z_eaves_mm + EPS, z0=p.roof.z_eaves_mm)
    return roof_solid(
        p.roof.rect_mm,
        p.roof.z_eaves_mm,
        p.roof.z_ridge_mm,
        p.roof.shape,
        p.roof.direction_deg,
        clip=clip,
    )


def _solids(prisms: list[Prism], sink: float) -> list[m3d.Manifold]:
    """Vertical bodies plus roofs. `sink` pulls a footprint that stands on the plate below it."""
    out: list[m3d.Manifold] = []
    for p in prisms:
        if p.geom.area <= 0 or p.height_mm <= p.z0_mm:
            continue
        bottom = p.z0_mm - sink if p.z0_mm <= 0 else p.z0_mm
        out.append(prism(p.geom, p.height_mm - bottom, z0=bottom))
        roof = _roof_body(p)
        if roof is not None:
            out.append(roof)
    return out


def build_meshes(scaled: Scaled, spec: FrameSpec) -> MeshSet:
    if not scaled.buildings and not scaled.blocks:
        raise MeshError("No buildings in the selected area.")

    solids = _solids(scaled.buildings, 0.0) + _solids(scaled.blocks, 0.0)
    if not solids:
        raise MeshError("No printable building footprints in the selected area.")

    base = plate(spec)
    # Exported part: flush on the plate top, so 3MF parts never overlap.
    buildings = _check(union(solids), "buildings")
    # For the single-colour union we sink the buildings slightly so the boolean never relies on
    # a pure face contact at z = 0. Parts that start in the air keep their bottom.
    buildings_sunk = union(_solids(scaled.buildings, BUILDING_SINK_MM) + _solids(scaled.blocks, BUILDING_SINK_MM))

    water = roads = None
    cut = recess(scaled.water, spec.water_depth_mm) if scaled.water else None
    if cut is not None:
        cutter, water = cut
        base = base - cutter
        _check(water, "water")
    cut = recess(scaled.roads, spec.road_depth_mm) if scaled.roads else None
    if cut is not None:
        cutter, roads = cut
        base = base - cutter
        _check(roads, "roads")
    _check(base, "base")

    single = _check(base + buildings_sunk, "single")
    return MeshSet(base=base, buildings=buildings, single=single, water=water, roads=roads)


def to_trimesh(man: m3d.Manifold) -> trimesh.Trimesh:
    mesh = man.to_mesh()
    vertices = np.asarray(mesh.vert_properties)[:, :3]
    faces = np.asarray(mesh.tri_verts)
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
```

- [ ] **Step 7: Tests grün**

Run: `cd backend && uv run pytest tests/test_scale.py tests/test_mesh.py -q`
Expected: PASS.

- [ ] **Step 8: Volle Suite grün**

Run: `cd backend && uv run pytest -q`
Expected: PASS. `test_pipeline.py::test_run_frankfurt_offline` läuft jetzt mit Blöcken, Teilen und Dächern durch; scheitert es mit `MeshError`/`ExportError`, ist das ein echtes Geometrieproblem (z. B. ein Dach, dessen Zuschnitt einen leeren Körper liefert) — Ursache beheben, nicht den Test lockern.

- [ ] **Step 9: Commit**

```bash
git add backend/skylineframe/scale.py backend/skylineframe/mesh.py backend/tests/test_scale.py backend/tests/test_mesh.py
git commit -m "feat(mesh): extrude blocks, floating parts and roof bodies

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Pipeline, Statistik, CLI-Flags und API-Durchreichung

**Files:**
- Modify: `backend/skylineframe/pipeline.py:21-61`
- Modify: `backend/skylineframe/cli.py` (komplett ersetzen)
- Modify: `backend/app/jobs.py:39` (`stats`-Typ)
- Test: `backend/tests/test_pipeline.py` (anhängen und einen Test erweitern)
- Test: `backend/tests/test_cli.py` (anhängen)
- Test: `backend/tests/test_api.py` (anhängen)

**Interfaces:**
- Consumes: `prepare.Prepared.blocks/.footprint_coverage` (Task 3), `scale.Scaled.blocks` (Task 5), `spec.PRESETS`, `spec.Preset` (Task 1).
- Produces:
  - `pipeline.RunResult(paths: ExportPaths, stats: dict[str, float])`
  - `stats`-Schlüssel: `buildings`, `buildings_individual`, `blocks`, `parts`, `roofs`, `footprint_coverage`, `roads`, `water`, `stl_bytes`, `threemf_bytes`, `nonmanifold_edges`, `degenerate_faces`. `buildings` bleibt erhalten (API, Frontend, Playwright) und ist identisch mit `buildings_individual`.
  - `cli.generate` mit `--preset`, `--side`/`--plate` als `float | None` (explizit schlägt Preset), `--roofs/--no-roofs`, `--parts/--no-parts`.

- [ ] **Step 1: Failing Tests schreiben**

In `backend/tests/test_pipeline.py` `test_run_frankfurt_offline` ersetzen und die neuen Tests anhängen:

```python
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
    assert result.stats["buildings_individual"] == result.stats["buildings"]
    assert result.stats["blocks"] >= 1
    assert result.stats["roofs"] > 0  # the fixture has 60 roof:shape buildings
    assert result.stats["roads"] >= 1
    assert result.stats["stl_bytes"] > 10_000
    # Spec §6: the model must carry at least 95 % of the building area in the square.
    assert result.stats["footprint_coverage"] >= 0.95


def test_run_bankenviertel_has_building_parts(tmp_path, bankenviertel_spec, bankenviertel_data):
    result = run(
        bankenviertel_spec,
        tmp_path / "out",
        tmp_path / "cache",
        fetch=lambda s, c: parse_overpass(bankenviertel_data, s),
    )
    assert result.stats["parts"] > 0
    assert result.stats["footprint_coverage"] >= 0.95


def test_run_without_roofs_and_parts(tmp_path, bankenviertel_spec, bankenviertel_data):
    spec = bankenviertel_spec.model_copy(update={"roofs": False, "parts": False})
    result = run(spec, tmp_path / "out", tmp_path / "cache", fetch=lambda s, c: parse_overpass(bankenviertel_data, s))
    assert result.stats["roofs"] == 0
    assert result.stats["parts"] == 0
    assert result.stats["buildings"] > 0
```

An `backend/tests/test_cli.py` anhängen:

```python
def run_cli(monkeypatch, tmp_path, args: list[str]) -> tuple[object, object]:
    """Invoke the CLI with a stubbed pipeline and return (captured spec, result)."""
    captured = {}

    def fake_run(spec, out_dir, cache_dir, progress=None):
        captured["spec"] = spec
        return RunResult(
            ExportPaths(out_dir / "model.stl", out_dir / "model.3mf", out_dir / "preview.glb"),
            {"buildings": 3, "blocks": 2, "parts": 1, "roofs": 4, "footprint_coverage": 0.97},
        )

    monkeypatch.setattr(cli, "run", fake_run)
    result = runner.invoke(cli.app, ["--lat", "50.1", "--lon", "8.6", "--out", str(tmp_path), *args])
    assert result.exit_code == 0, result.output
    return captured["spec"], result


def test_preset_sets_side_and_plate(monkeypatch, tmp_path):
    spec, _ = run_cli(monkeypatch, tmp_path, ["--preset", "detail"])
    assert (spec.side_m, spec.plate_size_mm) == (800, 100)
    spec, _ = run_cli(monkeypatch, tmp_path, ["--preset", "gross"])
    assert (spec.side_m, spec.plate_size_mm) == (1500, 200)


def test_explicit_side_and_plate_beat_the_preset(monkeypatch, tmp_path):
    spec, _ = run_cli(monkeypatch, tmp_path, ["--preset", "detail", "--side", "1200", "--plate", "150"])
    assert (spec.side_m, spec.plate_size_mm) == (1200, 150)


def test_defaults_without_preset(monkeypatch, tmp_path):
    spec, _ = run_cli(monkeypatch, tmp_path, [])
    assert (spec.side_m, spec.plate_size_mm) == (1500, 100)
    assert spec.roofs is True and spec.parts is True


def test_roofs_and_parts_can_be_switched_off(monkeypatch, tmp_path):
    spec, _ = run_cli(monkeypatch, tmp_path, ["--no-roofs", "--no-parts"])
    assert spec.roofs is False and spec.parts is False


def test_generate_prints_the_detail_stats(monkeypatch, tmp_path):
    _, result = run_cli(monkeypatch, tmp_path, [])
    assert "Buildings: 3" in result.output
    assert "Blocks: 2" in result.output
    assert "Parts: 1" in result.output
    assert "Roofs: 4" in result.output
    assert "Footprint coverage: 97.0%" in result.output
```

An `backend/tests/test_api.py` anhängen:

```python
def test_create_job_accepts_the_detail_flags(client):
    r = client.post("/api/jobs", json={**SPEC, "roofs": False, "parts": False})
    assert r.status_code == 202


def test_create_job_still_rejects_unknown_fields(client):
    # FrameSpec forbids extras, so the frontend may only send fields the backend knows.
    r = client.post("/api/jobs", json={**SPEC, "preset": "detail"})
    assert r.status_code == 422
```

- [ ] **Step 2: Fehlschlag bestätigen**

Run: `cd backend && uv run pytest tests/test_pipeline.py tests/test_cli.py tests/test_api.py -q`
Expected: FAIL — `KeyError: 'blocks'` im Pipeline-Test und `Error: No such option: --preset` im CLI-Test.

- [ ] **Step 3: `pipeline.py` anpassen**

In `backend/skylineframe/pipeline.py` `RunResult` und `run` ersetzen:

```python
@dataclass
class RunResult:
    paths: ExportPaths
    stats: dict[str, float]  # counts are ints, footprint_coverage is a ratio


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
    # A square of nothing but small sheds has no individual buildings but still has blocks,
    # and a block alone is a perfectly good model.
    if not prepared.buildings and not prepared.blocks:
        raise PipelineError("No buildings found in the selected area. Try a denser part of the city.")
    scaled = scale_features(prepared, spec)

    report("mesh", f"Building solids for {len(prepared.buildings)} buildings in {len(prepared.blocks)} blocks")
    meshes = build_meshes(scaled, spec)

    report("export", "Writing STL, 3MF and preview")
    paths = export_all(meshes, spec, out_dir)

    individual = len(prepared.buildings)
    stats: dict[str, float] = {
        # `buildings` stays the headline number the API, the frontend and the Playwright test
        # already read; buildings_individual is the same count under the spec's name.
        "buildings": individual,
        "buildings_individual": individual,
        "blocks": len(prepared.blocks),
        "parts": sum(1 for b in prepared.buildings if b.is_part),
        "roofs": sum(1 for b in prepared.buildings if b.roof is not None),
        "footprint_coverage": round(prepared.footprint_coverage, 4),
        "roads": len(prepared.roads),
        "water": len(prepared.water),
        "stl_bytes": paths.stl.stat().st_size,
        "threemf_bytes": paths.threemf.stat().st_size,
        **paths.diagnostics,
    }
    return RunResult(paths=paths, stats=stats)
```

- [ ] **Step 4: `cli.py` ersetzen**

`backend/skylineframe/cli.py`:

```python
"""Command line entry point: skylineframe --lat 50.11 --lon 8.68 --mode full --out ./out"""

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from .errors import SkylineError
from .pipeline import run
from .spec import PRESETS, FrameSpec, Mode, Preset

app = typer.Typer(add_completion=False)
DEFAULT_SIDE_M, DEFAULT_PLATE_MM = PRESETS[Preset.skyline]


def _message(exc: Exception) -> str:
    """One readable line per problem; pydantic's full repr is too noisy for a terminal."""
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
    return str(exc)


@app.command()
def generate(
    lat: Annotated[float, typer.Option(help="Centre latitude (WGS84)")],
    lon: Annotated[float, typer.Option(help="Centre longitude (WGS84)")],
    preset: Annotated[
        Preset | None,
        typer.Option(help="skyline = 1500 m on 100 mm, detail = 800/100, gross = 1500/200"),
    ] = None,
    side: Annotated[float | None, typer.Option(help="Edge length of the city square in metres (wins over --preset)")] = None,
    plate: Annotated[float | None, typer.Option(help="Plate edge length in mm (wins over --preset)")] = None,
    thickness: Annotated[float, typer.Option(help="Plate thickness in mm")] = 3.0,
    mode: Annotated[Mode, typer.Option(help="simple = buildings only, full = roads and water too")] = Mode.simple,
    rotation: Annotated[float, typer.Option(help="Clockwise rotation of the square in degrees")] = 0,
    z: Annotated[float, typer.Option(help="Height exaggeration factor")] = 1.5,
    roofs: Annotated[bool, typer.Option("--roofs/--no-roofs", help="Build roof bodies from roof:shape")] = True,
    parts: Annotated[bool, typer.Option("--parts/--no-parts", help="Render building:part instead of one box per outline")] = True,
    out: Annotated[Path, typer.Option(help="Output directory")] = Path("out"),
    cache: Annotated[Path, typer.Option(help="Overpass cache directory")] = Path(".cache/overpass"),
) -> None:
    preset_side, preset_plate = PRESETS.get(preset, (DEFAULT_SIDE_M, DEFAULT_PLATE_MM))
    # Spec construction is inside the try: out-of-range options must read as an error line,
    # not as a pydantic traceback. Pydantic's ValidationError is named next to SkylineError
    # because it is the one expected failure that is not part of our own hierarchy.
    try:
        spec = FrameSpec(
            center_lat=lat,
            center_lon=lon,
            side_m=side if side is not None else preset_side,
            plate_size_mm=plate if plate is not None else preset_plate,
            plate_thickness_mm=thickness,
            mode=mode,
            rotation_deg=rotation,
            z_exaggeration=z,
            roofs=roofs,
            parts=parts,
        )
        result = run(spec, out, cache, progress=lambda stage, msg: typer.echo(f"[{stage}] {msg}"))
    except (SkylineError, ValidationError) as exc:
        typer.echo(f"Error: {_message(exc)}", err=True)
        raise typer.Exit(code=1)
    stats = result.stats
    typer.echo(f"Buildings: {int(stats.get('buildings', 0))}")
    typer.echo(f"Blocks: {int(stats.get('blocks', 0))}")
    typer.echo(f"Parts: {int(stats.get('parts', 0))}")
    typer.echo(f"Roofs: {int(stats.get('roofs', 0))}")
    typer.echo(f"Footprint coverage: {stats.get('footprint_coverage', 0.0):.1%}")
    typer.echo(f"Non-manifold edges after vertex merge: {int(stats.get('nonmanifold_edges', 0))}")
    typer.echo(f"Degenerate faces after vertex merge: {int(stats.get('degenerate_faces', 0))}")
    typer.echo(f"STL: {result.paths.stl}")
    typer.echo(f"3MF: {result.paths.threemf}")


if __name__ == "__main__":
    app()
```

- [ ] **Step 5: `app/jobs.py` Typ nachziehen**

In `backend/app/jobs.py` in der `Job`-Dataclass

```python
    stats: dict[str, int] = field(default_factory=dict)
```

ersetzen durch

```python
    stats: dict[str, float] = field(default_factory=dict)  # counts plus footprint_coverage
```

- [ ] **Step 6: Tests grün**

Run: `cd backend && uv run pytest -q`
Expected: PASS.

Scheitert `footprint_coverage >= 0.95` auf der Römer-Fixture, ist das ein echtes Ergebnis — den Schwellwert nicht senken, sondern nachsehen, was verloren geht:

```bash
cd backend && uv run python -c "
import json
from shapely.ops import unary_union
from skylineframe.fetch import parse_overpass
from skylineframe.prepare import prepare
from skylineframe.project import project_features
from tests.conftest import FIXTURES, FRANKFURT
feats = parse_overpass(json.loads((FIXTURES / 'frankfurt_roemer.json').read_text()), FRANKFURT)
out = prepare(project_features(feats, FRANKFURT), FRANKFURT)
print('coverage', out.footprint_coverage, 'blocks', len(out.blocks), 'buildings', len(out.buildings))
missing = unary_union([b.geom for b in out.buildings]).difference(unary_union([b.geom for b in out.blocks]))
print('area outside the blocks', missing.area)
"
```

Erwartung: `coverage` ≥ 0.95 und `area outside the blocks` ≈ 0. Ist `coverage` niedrig, prüfen, ob `close_m` wirklich `MIN_FEATURE_MM / 2 / scale` ist und ob die Druckbarkeitsschwelle der Blöcke (`min_area_m2`) mit `spec.min_footprint_area_mm2 / scale**2` gerechnet wird.

- [ ] **Step 7: CLI-Hilfe prüfen**

Run: `cd backend && uv run skylineframe --help`
Expected: Optionen `--preset`, `--side`, `--plate`, `--roofs / --no-roofs`, `--parts / --no-parts` sind gelistet.

- [ ] **Step 8: Commit**

```bash
git add backend/skylineframe/pipeline.py backend/skylineframe/cli.py backend/app/jobs.py backend/tests/test_pipeline.py backend/tests/test_cli.py backend/tests/test_api.py
git commit -m "feat(cli): presets, roof/part switches and detail statistics

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Frontend — Presets in der Seitenleiste und neue Statuszeile

**Files:**
- Create: `frontend/src/presets.ts`
- Create: `frontend/src/controls.test.ts`
- Modify: `frontend/index.html:19-21` (neues `<label>` vor „Ausschnitt")
- Modify: `frontend/src/controls.ts` (komplett ersetzen)
- Modify: `frontend/src/main.ts:45-46` (Statuszeile)
- Modify: `frontend/e2e/smoke.spec.ts` (Stats und Preset)

**Interfaces:**
- Consumes: `stats.buildings`, `stats.blocks`, `stats.roofs` aus `JobState` (Task 6). `FrameSpecInput` bleibt unverändert — Presets setzen nur `side_m` und `plate_size_mm`, es geht kein neues Feld an die API (`FrameSpec` verbietet Extras).
- Produces:
  - `presets.PresetName = "skyline" | "detail" | "gross"`, `presets.Preset { sideM: number; plateMm: number }`, `presets.PRESETS: Record<PresetName, Preset>`, `presets.presetFor(sideM: number, plateMm: number): PresetName | "custom"`
  - `controls.setupControls(root: HTMLElement): Controls` (Interface unverändert; alle Listener werden jetzt in `setupControls` registriert, `onSquareInput(cb)` hinterlegt nur den Callback).

- [ ] **Step 1: Failing Tests schreiben**

`frontend/src/controls.test.ts`:

```ts
// @vitest-environment happy-dom
// Set per-file so vite.config.ts can stay on the fast `environment: "node"` default.
import { beforeEach, describe, expect, it } from "vitest";
import { setupControls } from "./controls";
import { PRESETS, presetFor } from "./presets";

const SIDEBAR = `
  <input id="search" />
  <div id="search-results"></div>
  <select id="preset">
    <option value="skyline">Skyline</option>
    <option value="detail">Detail</option>
    <option value="gross">Groß</option>
    <option value="custom">Eigene</option>
  </select>
  <output id="side-out"></output>
  <input id="side" type="range" min="200" max="5000" step="50" value="1500" />
  <output id="rotation-out"></output>
  <input id="rotation" type="range" min="-180" max="180" step="1" value="0" />
  <input id="plate" type="number" value="100" />
  <input id="thickness" type="number" value="3" />
  <input id="zfactor" type="number" value="1.5" />
  <select id="mode"><option value="simple">simple</option><option value="full">full</option></select>
  <button id="generate"></button>
  <p id="status"></p>
  <div id="downloads" hidden><a id="dl-stl"></a><a id="dl-3mf"></a></div>
`;

function field<T extends HTMLElement>(id: string): T {
  return document.getElementById(id) as unknown as T;
}

describe("presetFor", () => {
  it("recognises every preset and falls back to custom", () => {
    expect(presetFor(1500, 100)).toBe("skyline");
    expect(presetFor(800, 100)).toBe("detail");
    expect(presetFor(1500, 200)).toBe("gross");
    expect(presetFor(1234, 100)).toBe("custom");
    expect(presetFor(1500, 123)).toBe("custom");
  });

  it("matches the backend table", () => {
    expect(PRESETS).toEqual({
      skyline: { sideM: 1500, plateMm: 100 },
      detail: { sideM: 800, plateMm: 100 },
      gross: { sideM: 1500, plateMm: 200 },
    });
  });
});

describe("setupControls", () => {
  let root: HTMLElement;
  let updates: Array<Record<string, number>>;

  beforeEach(() => {
    document.body.innerHTML = `<div id="app">${SIDEBAR}</div>`;
    root = document.getElementById("app")!;
    updates = [];
  });

  const fire = (el: HTMLElement, type: string) => el.dispatchEvent(new Event(type));

  it("starts on the preset that matches the initial fields", () => {
    setupControls(root);
    expect(field<HTMLSelectElement>("preset").value).toBe("skyline");
  });

  it("writes side and plate when a preset is picked and tells the map", () => {
    const controls = setupControls(root);
    controls.onSquareInput((p) => updates.push(p as Record<string, number>));

    const preset = field<HTMLSelectElement>("preset");
    preset.value = "gross";
    fire(preset, "change");

    expect(field<HTMLInputElement>("side").value).toBe("1500");
    expect(field<HTMLInputElement>("plate").value).toBe("200");
    expect(field<HTMLOutputElement>("side-out").value).toBe("1500 m");
    expect(controls.read().plate_size_mm).toBe(200);
    expect(updates).toEqual([{ sideM: 1500 }]);
  });

  it("switches to Detail including the side length", () => {
    const controls = setupControls(root);
    const preset = field<HTMLSelectElement>("preset");
    preset.value = "detail";
    fire(preset, "change");
    expect(controls.read().side_m).toBe(800);
    expect(controls.read().plate_size_mm).toBe(100);
  });

  it("falls back to Eigene when the side slider is moved by hand", () => {
    const controls = setupControls(root);
    controls.onSquareInput((p) => updates.push(p as Record<string, number>));

    const side = field<HTMLInputElement>("side");
    side.value = "1250";
    fire(side, "input");

    expect(field<HTMLSelectElement>("preset").value).toBe("custom");
    expect(updates).toEqual([{ sideM: 1250 }]);
  });

  it("falls back to Eigene when the plate size is edited by hand", () => {
    setupControls(root);
    const plate = field<HTMLInputElement>("plate");
    plate.value = "140";
    fire(plate, "input");
    expect(field<HTMLSelectElement>("preset").value).toBe("custom");
  });

  it("keeps the fields untouched when Eigene is selected", () => {
    const controls = setupControls(root);
    controls.onSquareInput((p) => updates.push(p as Record<string, number>));
    const preset = field<HTMLSelectElement>("preset");
    preset.value = "custom";
    fire(preset, "change");
    expect(controls.read().side_m).toBe(1500);
    expect(updates).toEqual([]);
  });

  it("does not leave the preset when only the rotation changes", () => {
    setupControls(root);
    const rotation = field<HTMLInputElement>("rotation");
    rotation.value = "30";
    fire(rotation, "input");
    expect(field<HTMLSelectElement>("preset").value).toBe("skyline");
  });
});
```

- [ ] **Step 2: Fehlschlag bestätigen**

Run: `cd frontend && npx vitest --run src/controls.test.ts`
Expected: FAIL — `Failed to resolve import "./presets"`.

- [ ] **Step 3: `presets.ts` anlegen**

`frontend/src/presets.ts`:

```ts
// Scale presets for the sidebar (spec §9). Keep in sync with backend/skylineframe/spec.py::PRESETS
// and with the CLI's --preset. Only side_m and plate_size_mm are preset values; everything else
// stays what the user set, and no new field is ever sent to the API (FrameSpec forbids extras).
export type PresetName = "skyline" | "detail" | "gross";

export interface Preset {
  sideM: number;
  plateMm: number;
}

export const PRESETS: Record<PresetName, Preset> = {
  skyline: { sideM: 1500, plateMm: 100 },
  detail: { sideM: 800, plateMm: 100 },
  gross: { sideM: 1500, plateMm: 200 },
};

/** The preset matching these values, or "custom" ("Eigene" in the UI). */
export function presetFor(sideM: number, plateMm: number): PresetName | "custom" {
  const names = Object.keys(PRESETS) as PresetName[];
  return names.find((name) => PRESETS[name].sideM === sideM && PRESETS[name].plateMm === plateMm) ?? "custom";
}
```

- [ ] **Step 4: `controls.ts` ersetzen**

`frontend/src/controls.ts`:

```ts
// Sidebar wiring: reads the form into a FrameSpecInput and reflects map state back into the inputs.
import type { FrameSpecInput } from "./api";
import { jobFileUrl } from "./api";
import { PRESETS, presetFor, type PresetName } from "./presets";
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
  const preset = el<HTMLSelectElement>(root, "preset");
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

  // The centre lives on the map, not in a form field; writeSquare keeps this copy in sync.
  let square: SquareParams = { lat: 50.1106, lon: 8.6821, sideM: Number(side.value), rotationDeg: Number(rotation.value) };
  // Set by onSquareInput; the preset needs to reach the map too, so every listener is
  // registered here and goes through this one callback.
  let squareListener: ((p: Partial<SquareParams>) => void) | null = null;

  const syncOutputs = () => {
    sideOut.value = `${side.value} m`;
    rotationOut.value = `${rotation.value}°`;
  };
  syncOutputs();
  preset.value = presetFor(Number(side.value), Number(plate.value));

  side.addEventListener("input", () => {
    syncOutputs();
    preset.value = "custom";
    squareListener?.({ sideM: Number(side.value) });
  });
  rotation.addEventListener("input", () => {
    syncOutputs();
    squareListener?.({ rotationDeg: Number(rotation.value) });
  });
  // Editing a preset field by hand means the scale is no longer one of the presets (spec §9).
  plate.addEventListener("input", () => {
    preset.value = "custom";
  });
  preset.addEventListener("change", () => {
    const chosen = PRESETS[preset.value as PresetName];
    if (!chosen) return; // "Eigene" keeps whatever the fields say
    side.value = String(chosen.sideM);
    plate.value = String(chosen.plateMm);
    syncOutputs();
    squareListener?.({ sideM: chosen.sideM });
  });

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
      preset.value = presetFor(Number(side.value), Number(plate.value));
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
      squareListener = cb;
    },
    elements: { search: el<HTMLInputElement>(root, "search"), searchResults: el<HTMLElement>(root, "search-results") },
  };
}
```

- [ ] **Step 5: `index.html` erweitern**

In `frontend/index.html` direkt vor `<label>Ausschnitt …` einfügen:

```html
        <label>Maßstab
          <select id="preset">
            <option value="skyline">Skyline — 1500 m auf 100 mm (1:15.000)</option>
            <option value="detail">Detail — 800 m auf 100 mm (1:8.000)</option>
            <option value="gross">Groß — 1500 m auf 200 mm (1:7.500)</option>
            <option value="custom">Eigene</option>
          </select>
        </label>
```

- [ ] **Step 6: Statuszeile in `main.ts`**

In `frontend/src/main.ts` die Zeile

```ts
    const summary = `Fertig: ${job.stats.buildings ?? 0} Gebäude`;
```

ersetzen durch

```ts
    const stats = job.stats ?? {};
    const summary = `Fertig: ${stats.buildings ?? 0} Gebäude, ${stats.blocks ?? 0} Blöcke, ${stats.roofs ?? 0} Dächer`;
```

- [ ] **Step 7: Vitest grün**

Run: `cd frontend && npx vitest --run`
Expected: PASS für `controls.test.ts`, `api.test.ts`, `search.test.ts`, `square.test.ts`.

Run: `cd frontend && npx tsc --noEmit`
Expected: keine Ausgabe (strikter Typcheck sauber).

- [ ] **Step 8: Playwright-Smoke-Test erweitern**

In `frontend/e2e/smoke.spec.ts` im ersten Test den `done`-Body und die Assertions anpassen:

```ts
        : { id: "job1", status: "done", stage: "export", message: "Ready", stats: { buildings: 42, blocks: 3, roofs: 5 } };
```

und nach `await expect(page.locator("#sidebar h1")).toHaveText("Skyline Frame");` einfügen:

```ts
  // Presets write both fields; picking one must not need a page reload (spec §9).
  await page.selectOption("#preset", "detail");
  await expect(page.locator("#side")).toHaveValue("800");
  await expect(page.locator("#plate")).toHaveValue("100");
  await page.selectOption("#preset", "skyline");
  await expect(page.locator("#side")).toHaveValue("1500");
```

und die Statuszeile:

```ts
  await expect(page.locator("#status")).toContainText("42 Gebäude, 3 Blöcke, 5 Dächer", { timeout: 10_000 });
```

- [ ] **Step 9: E2E grün**

Run: `cd frontend && npm run build && npx playwright test`
Expected: beide Smoke-Tests bestehen.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/presets.ts frontend/src/controls.ts frontend/src/controls.test.ts frontend/src/main.ts frontend/index.html frontend/e2e/smoke.spec.ts
git commit -m "feat(ui): scale presets in the sidebar and block/roof counts in the status line

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: README und Abnahme

**Files:**
- Modify: `README.md` (Abschnitte „CLI", „Drucken", neuer Abschnitt „Lizenz der Daten")
- Modify: `tasks/todo.md` (Review-Abschnitt; Datei anlegen, falls nicht vorhanden)

**Interfaces:**
- Consumes: die CLI aus Task 6 (`--preset`, `--roofs/--no-roofs`, `--parts/--no-parts`, neue Statistikzeilen).
- Produces: kein Code — die dokumentierte Bestätigung, dass die Kennzahlen stimmen und das Modell im Slicer funktioniert.

- [ ] **Step 1: README ergänzen**

Den Abschnitt „## CLI" in `README.md` ersetzen durch:

```markdown
## CLI

    cd backend && uv run skylineframe --lat 50.1106 --lon 8.6821 --preset skyline --mode full --out ../out

(Ein Unterkommando gibt es nicht — die Optionen stehen direkt hinter `skylineframe`.)

| Preset | Ausschnitt | Platte | Maßstab |
|---|---|---|---|
| `skyline` (Default) | 1500 m | 100 mm | 1:15.000 |
| `detail` | 800 m | 100 mm | 1:8.000 |
| `gross` | 1500 m | 200 mm | 1:7.500 |

`--side` und `--plate` schlagen das Preset. Zum Vergleichen: `--no-roofs` lässt alle Dächer flach,
`--no-parts` rendert je Umriss einen Kasten statt der `building:part`-Rücksprünge (beides ist per
Default an). Die Ausgabe nennt Gebäude, Blöcke, Teile, Dächer und die Flächenabdeckung
(Gebäudefläche im Modell / Gebäudefläche im Quadrat).
```

Nach dem Abschnitt „## Datenquellen" anhängen:

```markdown
## Lizenz der Daten

Die Geometrie stammt aus OpenStreetMap und steht unter der [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/).
Ein gedrucktes Modell ist ein „Produced Work" im Sinne der ODbL: Es darf verkauft werden, und die
Datenbank selbst muss dafür nicht offengelegt werden. Pflicht ist die Namensnennung —

> Enthält Daten von © OpenStreetMap-Mitwirkende (ODbL)

— sichtbar auf der Produktseite, in einer Beilage oder auf der Bodenplatte. Der Generator schreibt
diesen Hinweis **nicht** selbst in das Modell; wer Drucke verkauft, muss ihn selbst anbringen.
```

- [ ] **Step 2: Abnahmelauf Frankfurt Skyline**

Run: `cd backend && uv run skylineframe --lat 50.1106 --lon 8.6821 --preset skyline --mode full --out ../out/frankfurt-skyline`
Expected: `[fetch] … [export] …`, dann `Buildings:` > 1000, `Blocks:` > 100, `Roofs:` > 100, `Footprint coverage:` ≥ 95,0 %, `Non-manifold edges after vertex merge:` wird ausgegeben, Laufzeit unter drei Minuten.

- [ ] **Step 3: Abnahmelauf Frankfurt Detail**

Run: `cd backend && uv run skylineframe --lat 50.1106 --lon 8.6821 --preset detail --mode full --out ../out/frankfurt-detail`
Expected: erfolgreich; gegenüber Step 2 weniger Gebäude, aber mehr Dächer pro Fläche (größerer Maßstab: 0,125 mm/m statt 0,067 mm/m).

- [ ] **Step 4: Abnahmelauf Manhattan Midtown**

Run: `cd backend && uv run skylineframe --lat 40.7580 --lon -73.9855 --side 1500 --plate 100 --mode full --out ../out/manhattan`
Expected: erfolgreich; `Parts:` > 0 (Midtown ist stark mit `building:part` modelliert), `Footprint coverage:` ≥ 95 %.

Bei `Overpass request failed`: eine Minute warten und wiederholen. Bei „too much map data": das ist die 250-000-Elemente-Grenze — dann mit `--side 1200` dokumentieren.

- [ ] **Step 5: Vergleichslauf ohne die neuen Features**

Run: `cd backend && uv run skylineframe --lat 50.1106 --lon 8.6821 --preset skyline --mode full --no-roofs --no-parts --out ../out/frankfurt-flat`
Expected: erfolgreich, `Roofs: 0`, `Parts: 0`; STL merklich kleiner als in Step 2.

- [ ] **Step 6: Kennzahlen dokumentieren**

In `tasks/todo.md` einen Abschnitt „Review Building Detail Upgrade" anlegen mit dieser Tabelle, gefüllt aus den vier Läufen:

```markdown
## Review Building Detail Upgrade

| Lauf | Gebäude | Blöcke | Teile | Dächer | Coverage | STL (MB) | Laufzeit |
|---|---|---|---|---|---|---|---|
| Frankfurt Skyline (1500 m / 100 mm) | | | | | | | |
| Frankfurt Detail (800 m / 100 mm) | | | | | | | |
| Manhattan Midtown (1500 m / 100 mm) | | | | | | | |
| Frankfurt ohne Dächer/Teile | | | | | | | |

Beobachtungen: …
```

Zum Vergleich mit dem MVP: dieser hatte am Frankfurter Default 609 von 2.534 Gebäuden im Modell (24 %).

- [ ] **Step 7: In Bambu Studio prüfen (manuell, durch den Menschen)**

1. `out/frankfurt-skyline/model.stl` importieren. Erwartung: ein Objekt, 100 × 100 mm, Slicing ohne Warnungen zu nicht-mannigfaltigen Kanten; Dächer als Satteldächer/Walmdächer erkennbar, Blöcke als zusammenhängende Sockel statt einzelner Kästen.
2. `out/frankfurt-skyline/model.3mf` importieren, alle Objekte markieren → Rechtsklick → „Assemble", je Teil ein Filament. Erwartung: `base`, `buildings`, `water`, `roads` wie bisher.
3. Sichtprüfung: keine schwebenden Teile über dem Modell (freischwebende `building:part` wurden bis zur Platte verlängert), keine Nadeln auf Dächern, Türme des Bankenviertels mit Rücksprüngen.
4. Die automatische Reparatur beim 3MF weiter **ablehnen** — sie füllt die Vertiefungen auf.

- [ ] **Step 8: Ergebnis der Slicer-Prüfung ergänzen und committen**

```bash
git add README.md tasks/todo.md
git commit -m "docs: ODbL section, preset table and acceptance results for the detail upgrade

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec-Abdeckung**

| Spec | Task |
|---|---|
| §2.1 Blockverschmelzung | Task 3 (`build_blocks`, `_close`), Task 5 (Extrusion) |
| §2.2 `building:part` | Task 2 (Query/Parsing), Task 3 (`assign_parts`), Task 5 (`z0_mm`, Stützregel) |
| §2.3 Dachkörper | Task 2 (`parse_roof`), Task 3 (`resolve_roof`), Task 4 (`roofs.py`), Task 5 (Mesh) |
| §2.4 Höhenschätzung | Task 2 (`estimate_height_m`), Task 3 (`estimate_missing_heights`) |
| §2.5 Presets | Task 6 (CLI), Task 7 (UI) |
| §2.6 README/ODbL | Task 8 |
| §3 Datenmodell (`RoofSpec`, `Building`, `Block`) | Task 1 |
| §4 Abfrage und Parsing, `height`-Semantik | Task 2 (Parsing), Task 3 (`resolve_roof` löst `height_is_top` auf) |
| §5 Höhentabelle und Flächenregel | Task 2 |
| §6.1–6.7 Vorbereitungsreihenfolge | Task 3 |
| §7 Dachformen, Zuschnitt, Default-Dachhöhe, 0,3-mm-Grenze | Task 3 (Default-Höhe, Rechteck), Task 4 (Körper, Zuschnitt), Task 5 (0,3-mm-Grenze in `_roof_of`) |
| §8 Mesh (Blöcke, Teile, Versenkung, Skalierung) | Task 5 |
| §9 Presets im Frontend | Task 7 |
| §10 CLI-Flags | Task 6 |
| §11 Statistik | Task 6 (Backend), Task 7 (UI-Zeile) |
| §12 Lizenz | Task 8 |
| §13 Tests | Task 2 (fetch), Task 3 (prepare), Task 4 (roofs), Task 5 (mesh), Task 6 (pipeline), Task 7 (Frontend), Task 8 (Abnahme) |
| §14 Kompatibilität | Task 1 (`FrameSpec`), Task 6 (`stats["buildings"]` bleibt, 3MF-Teile unverändert) |

Keine Spec-Anforderung ohne Task.

**2. Platzhalter-Scan:** kein „TBD", kein „siehe Task N", kein „Fehlerbehandlung ergänzen". Jeder Code-Schritt enthält den vollständigen Datei- oder Funktionsinhalt, jeder Test-Schritt den vollständigen Testcode, jeder Run-Schritt den exakten Befehl und die erwartete Ausgabe. Die einzige bewusst offene Stelle ist die Kennzahlentabelle in Task 8 Step 6 — sie wird erst durch die Abnahmeläufe gefüllt und ist das Artefakt dieses Tasks, kein Platzhalter im Plan.

**3. Typkonsistenz**

- `Building` (Task 1) wird in Task 2 (`fetch`), Task 3 (`prepare`), Task 5 (`scale`) mit exakt denselben Feldnamen benutzt: `height_m`, `height_is_top`, `min_height_m`, `roof`, `osm_id`, `is_part`, `outline_id`, `kind`, `eaves_m`, `ridge_m`, `rect`.
- `RoofSpec(shape, height_m, direction_deg)` — `height_m == 0.0` heißt in Task 2 „nicht getaggt" und wird in Task 3 (`default_roof_height_m`) gefüllt; Task 5 liest danach nur noch `shape` und `direction_deg`, die Höhe steckt in `eaves_m`/`ridge_m`.
- `Block(geom, height_m)` aus Task 1 wird in Task 3 erzeugt und in Task 5 zu `Prism(geom, height_mm)` skaliert.
- `Prism(geom, height_mm, z0_mm, roof)` und `ScaledRoof(rect_mm, shape, z_eaves_mm, z_ridge_mm, direction_deg)` heißen in Task 5 (`scale.py`), Task 5 (`mesh.py`) und in beiden Testdateien gleich.
- `roof_solid(rect_mm, z_eaves_mm, z_ridge_mm, shape, direction_deg=None, clip=None)` wird in Task 4 definiert und in Task 5 (`mesh._roof_body`) mit genau dieser Reihenfolge aufgerufen; `MIN_ROOF_MM` wird von `scale.py` und `roofs.py` aus derselben Quelle (`roofs`) gelesen.
- `prepare.Prepared(buildings, blocks, roads, water, footprint_coverage)` — Feldreihenfolge ist rückwärtskompatibel zu den bestehenden Keyword-Aufrufen in `test_scale.py`.
- `stats`-Schlüssel sind in Task 6 (`pipeline`), Task 6 (`cli`), Task 7 (`main.ts`, Playwright) identisch: `buildings`, `blocks`, `roofs`, `parts`, `footprint_coverage`.
- `PRESETS` existiert zweimal (Python in `spec.py`, TypeScript in `presets.ts`) mit denselben Werten; beide Kopien tragen einen Kommentar, der auf die andere verweist, und `controls.test.ts` prüft die Tabelle gegen die Spec-Werte.
- `STRtree.query(..., predicate="within")` wird in Task 3 zweimal benutzt (Teil → Umriss, Grundriss → Block) — beide Male mit der geprüften Richtung `eingabe.predicate(baum_geom)`.
