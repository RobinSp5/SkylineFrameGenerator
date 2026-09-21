# Skyline Frame Generator – Design Spec

Datum: 2026-09-21
Status: freigegeben (Brainstorming abgeschlossen)

## 1. Ziel

Ein lokal laufender Generator, der aus einem frei wählbaren quadratischen Stadtausschnitt ein 3D-druckbares Modell im Stil von cityframes.de erzeugt: Gebäude extrudiert auf einer quadratischen Grundplatte, optional mit Straßen und Wasserflächen. Bedienung über eine Web-Oberfläche mit Karte, Ausgabe als STL (einfarbig) oder 3MF (mehrfarbig, ein Objekt pro Farbe).

Zieldrucker: Bambu Lab X2D mit AMS 2 Pro, 0,4 mm Düse. Primär einfarbig weiß, optional Multi-Color (z. B. Wasser blau).

### Nicht im Scope (MVP)

- Rahmen um die Platte
- Stadtname / Koordinaten als Gravur
- Küstenlinien / Meer (`natural=coastline` ist in OSM eine Linie, kein Polygon)
- `building:part`-Hierarchien (nur `building=*` wird verwendet)
- Terrain / Höhenrelief
- Nutzerkonten, Persistenz über Neustart hinaus, Hosting im Internet

## 2. Modi

| Modus  | Inhalt                                              |
|--------|-----------------------------------------------------|
| simple | Grundplatte + Gebäude                               |
| full   | Grundplatte + Gebäude + Straßenrillen + Wasserflächen |

Farbausgabe unabhängig vom Modus:

| Ausgabe     | Datei | Inhalt                                                        |
|-------------|-------|---------------------------------------------------------------|
| single      | .stl  | Ein vereinigtes, wasserdichtes Mesh                           |
| multi       | .3mf  | Getrennte Objekte `base`, `buildings`, `water`, `roads`; Wasser und Straßen sind Einleger, die exakt die Vertiefungen in `base` füllen. Wer Straßen lieber als Rille statt als Farbe will, löscht das Objekt `roads` im Slicer |

Beide Dateien werden pro Job immer erzeugt; die Vorschau nutzt ein zusätzliches GLB.

## 3. Geodaten

**Quelle:** OpenStreetMap über die Overpass-API (Endpoint konfigurierbar, Default `https://overpass-api.de/api/interpreter`). Abfrage per Bounding-Box, die das gedrehte Quadrat vollständig umschließt.

Abgefragte Features:

- Gebäude: `way[building]`, `relation[building][type=multipolygon]`
- Straßen (nur full): `way[highway]` mit Klassen motorway, trunk, primary, secondary, tertiary, residential, unclassified, living_street, pedestrian, service. Fußwege (`footway`, `path`, `cycleway`, `steps`) werden ignoriert.
- Wasser (nur full): `way/relation[natural=water]`, `way/relation[waterway=riverbank]`, `way/relation[landuse=reservoir]`, `way/relation[natural=bay]`

**Höhenableitung pro Gebäude (in Metern):**

1. `height` (Zahl, optional mit Einheit m) wenn vorhanden
2. sonst `building:levels` × 3,2 m
3. sonst Default 8 m (konfigurierbar)

`min_height` wird ignoriert (keine Parts). Nicht parsbare Werte fallen auf die nächste Stufe zurück.

**Caching:** Jede Overpass-Antwort wird als JSON unter `backend/.cache/overpass/<sha256(query)>.json` gespeichert. Cache-Hits gehen nicht ins Netz. Bei HTTP 429 oder 504 wird mit exponentiellem Backoff bis zu 3× wiederholt; danach Fehler mit Klartextmeldung.

**Geocoding:** Nominatim (`https://nominatim.openstreetmap.org/search`) mit eindeutigem User-Agent, maximal 1 Request/Sekunde, Ergebnisse 10 Minuten im Speicher gecacht.

## 4. Parameter: `FrameSpec`

Pydantic-Modell, gemeinsam für CLI und API.

| Feld                       | Typ    | Default | Bedeutung |
|----------------------------|--------|---------|-----------|
| center_lat, center_lon     | float  | –       | Mittelpunkt (WGS84) |
| side_m                     | float  | 1500    | Kantenlänge des Ausschnitts in Metern (200–5000) |
| rotation_deg               | float  | 0       | Drehung des Quadrats gegen Nord, im Uhrzeigersinn |
| plate_size_mm              | float  | 100     | Kantenlänge der Grundplatte (40–250) |
| plate_thickness_mm         | float  | 3.0     | Plattendicke |
| mode                       | enum   | simple  | simple / full |
| z_exaggeration             | float  | 1.5     | Multiplikator auf Gebäudehöhen |
| default_building_height_m  | float  | 8.0     | Höhe ohne Tags |
| min_building_height_mm     | float  | 0.8     | Untergrenze nach Skalierung |
| min_footprint_area_mm2     | float  | 1.0     | Grundrisse darunter werden verworfen |
| road_depth_mm              | float  | 0.4     | Tiefe der Straßenrillen |
| water_depth_mm             | float  | 0.6     | Tiefe der Wasserflächen |
| road_width_mm              | dict   | s. u.   | Rillenbreite pro Straßenklasse |

Straßenbreiten Default (mm, nach Skalierung konstant, nicht maßstabsabhängig): motorway/trunk 2.0, primary 1.6, secondary 1.4, tertiary 1.2, residential/unclassified/living_street 1.0, pedestrian/service 0.8. Alle ≥ 0,8 mm (Düsenbreite × 2).

Abgeleitet: `scale = plate_size_mm / side_m` (mm pro Meter).

## 5. Geometrie-Pipeline (`backend/skylineframe/`)

Jede Stufe ist eine reine Funktion mit klar definierten Ein- und Ausgabetypen und wird einzeln getestet.

### 5.1 `fetch.py`
`fetch_features(spec) -> RawFeatures`
Baut die Overpass-Query aus der Bounding-Box, nutzt den Cache, parst die Antwort zu Shapely-Geometrien in WGS84. Multipolygon-Relationen werden über `osm2geojson` zusammengesetzt (`filter_used_refs=False`, sonst verschwinden getaggte Gebäude, die zugleich Relationsmitglied sind). Ergebnis: Listen von `Building(geom, height_m)`, `Road(geom, cls)`, `Water(geom)`.

### 5.2 `project.py`
`project(features, spec) -> LocalFeatures`
Transformiert WGS84 in ein lokales metrisches System (Azimuthal Equidistant um den Mittelpunkt via pyproj), dann Rotation um `-rotation_deg`, sodass das Zielquadrat achsenparallel liegt mit Mittelpunkt (0, 0) und Kanten bei ±side_m/2.

### 5.3 `prepare.py`
`prepare(local, spec) -> PreparedFeatures`
- Clip aller Geometrien auf das Quadrat (`shapely.intersection`).
- `make_valid` auf allen Polygonen; leere oder nicht-polygonale Reste verwerfen.
- Gebäude: Polygone, Fläche ≥ `min_footprint_area_mm2 / scale²`, Vereinfachung mit Toleranz `0.05 mm / scale`. Überlappende Grundrisse werden nicht in 2D zusammengeführt; die 3D-Vereinigung in der Mesh-Stufe löst Überlappungen (der höhere Körper gewinnt).
- Straßen (full): Linien werden mit `road_width_mm / scale / 2` gepuffert (flache Enden, runde Verbindungen), alle Klassen vereinigt, dann erneut auf das Quadrat geclippt.
- Wasser (full): Polygone vereinigt.
- Vorrangregel: Gebäude > Straßen > Wasser. Straßenflächen werden um Gebäudegrundrisse reduziert, Wasserflächen um Gebäude und Straßen (Brücken erscheinen so als Straße). Damit liegt keine Vertiefung unter einem Gebäude und Straßen- und Wasserflächen überlappen nie.

### 5.4 `scale.py`
`scale_features(prepared, spec) -> ScaledFeatures`
Meter → Millimeter über `scale`. Gebäudehöhe: `max(h_m × scale × z_exaggeration, min_building_height_mm)`. Rundung auf 0,01 mm.

### 5.5 `mesh.py`
`build_meshes(scaled, spec) -> MeshSet`
Koordinatensystem: Plattenoberseite bei z = 0, Platte von z = −thickness bis 0, Gebäude von 0 bis h.

- `base`: Quader. Im full-Modus werden Straßen- und Wasserflächen als Extrusionen (Tiefe road_depth/water_depth, von −depth bis +0,01 mm) per Boolean-Differenz abgezogen.
- `buildings`: Jede Grundrissfläche mit `trimesh.creation.extrude_polygon` extrudiert (Löcher werden unterstützt), alle vereinigt.
- `water`, `roads` (full): Einleger, identische Grundfläche wie die Vertiefungen, Höhe = Tiefe, sitzen bündig in `base`.
- `single`: Boolean-Vereinigung von base (mit Vertiefungen) + buildings. Die Einleger werden bewusst nicht vereinigt, damit Straßen und Wasser im einfarbigen Druck als Relief sichtbar bleiben. Gebäude werden 0,2 mm in die Platte versenkt, damit die Vereinigung keine reinen Flächenkontakte hat.

Boolean-Engine: manifold3d über trimesh. Nach jeder Boolean-Operation wird `is_watertight` und `is_volume` geprüft. Bei Verletzung bricht die Pipeline mit `MeshError` ab.

### 5.6 `export.py`
`export(meshset, out_dir) -> ExportPaths`
- `model.stl`: `single` als Binär-STL.
- `model.3mf`: alle Teil-Meshes als benannte Objekte im selben Koordinatensystem.
- `preview.glb`: Teil-Meshes mit festen Farben (Platte hellgrau, Gebäude weiß, Wasser blau, Straßen dunkelgrau) für die Vorschau.

Vor dem Schreiben: Bounding-Box von `single` muss in x/y exakt `plate_size_mm` sein (Toleranz 0,01 mm), Volumen > Plattenvolumen.

### 5.7 `pipeline.py` und `cli.py`
`run(spec, out_dir, progress_cb) -> ExportPaths` verkettet 5.1–5.6 und meldet Fortschritt (`fetch`, `prepare`, `mesh`, `export`).
CLI: `skylineframe generate --lat 50.11 --lon 8.68 --side 1500 --mode full --out ./out`.

## 6. Backend (`backend/app/`, FastAPI)

| Endpoint                            | Beschreibung |
|-------------------------------------|--------------|
| `POST /api/jobs`                    | Body: FrameSpec. Antwort: `{id}`. Startet Generierung in einem ThreadPoolExecutor (max. 2 parallel). |
| `GET /api/jobs/{id}`                | `{status: queued/running/done/error, stage, message, stats}`; stats: Anzahl Gebäude/Straßen/Wasserflächen, Dateigrößen. |
| `GET /api/jobs/{id}/preview.glb`    | Vorschau-Mesh |
| `GET /api/jobs/{id}/model.stl`      | Download |
| `GET /api/jobs/{id}/model.3mf`      | Download |
| `GET /api/geocode?q=`               | Bis zu 5 Treffer `{name, lat, lon}` |

Jobs liegen in einem Dict im Prozess; Dateien unter `backend/.jobs/<id>/`. Beim Start werden Job-Ordner älter als 24 h gelöscht. Das Backend liefert im Produktionsmodus zusätzlich das gebaute Frontend als statische Dateien aus.

Fehlerbild: Jeder Pipeline-Fehler landet als `status: error` mit lesbarer `message` (z. B. „Overpass antwortet nicht, bitte später erneut versuchen“ oder „Keine Gebäude im Ausschnitt gefunden“). Kein Stacktrace an den Client.

## 7. Frontend (`frontend/`, Vite + TypeScript, kein Framework)

Module:

- `map.ts`: MapLibre GL mit OSM-Raster-Tiles. Zeichnet das Zielquadrat als GeoJSON-Polygon. Interaktion: Ziehen verschiebt den Mittelpunkt, Slider für `side_m` und `rotation_deg`. Das Quadrat wird aus Mittelpunkt, Seite und Rotation über eine lokale Projektion berechnet (gleiche Formel wie im Backend).
- `search.ts`: Suchfeld → `/api/geocode`, Klick auf Treffer zentriert Karte und Quadrat.
- `controls.ts`: Seitenleiste mit Modus, Plattengröße, Höhenfaktor, Plattendicke, Button „Generieren“, Fortschritt und Fehlermeldung.
- `viewer.ts`: three.js mit OrbitControls, lädt `preview.glb`, Licht und Grundraster.
- `api.ts`: typisierter Client, Polling alle 1 s bis `done`/`error`.
- Download-Buttons für STL und 3MF erscheinen nach `done`.

Layout: Karte links (60 %), rechts Seitenleiste oben und 3D-Vorschau darunter. Desktop-fokussiert, keine Mobile-Optimierung im MVP.

## 8. Druck-Randbedingungen

- Alle Features ≥ 0,8 mm (zwei Düsenbreiten): Rillenbreiten, Gebäudemindesthöhe, Mindestgrundriss.
- Keine Überhänge: reine Extrusionen nach oben, Vertiefungen nach unten.
- Platte 100 mm × 100 mm × 3 mm passt auf jede Bambu-Druckplatte; Maximum 250 mm.
- 3MF-Objekte teilen ein Koordinatensystem; in Bambu Studio „Als ein Objekt mit mehreren Teilen importieren“ wählen und je Teil ein Filament zuweisen.

## 9. Tests

- **Unit** (`pytest`): Höhenableitung aus Tags; Projektion (Hin- und Rückweg, Rotation); Clipping und Reparatur an konstruierten Polygonen; Straßenpufferung; Skalierung mit Mindesthöhen.
- **Mesh:** Synthetische Grundrisse (Rechteck, L-Form, Polygon mit Loch, Gebäude über der Kante). Prüfungen: watertight, is_volume, Bounding-Box, Volumen = Summe der Erwartungen ± 1 %.
- **Fixture:** Aufgezeichnete Overpass-Antwort eines 300 m-Ausschnitts (Frankfurt Römer) unter `tests/fixtures/`. Pipeline-Test läuft damit komplett offline und schreibt STL/3MF/GLB in ein Temp-Verzeichnis.
- **API:** FastAPI TestClient mit gemocktem `fetch_features`.
- **Frontend:** Vitest für die Quadrat-Berechnung; Playwright-Smoke-Test (Seite lädt, Karte sichtbar, Generieren gegen Mock-Backend liefert Download-Links).
- **Manuelle Abnahme:** Echter Durchlauf Frankfurt full/multi, Import in Bambu Studio, Slicing ohne Fehler.

## 10. Projektstruktur

```
SkylineFrameGenerator/
  backend/
    skylineframe/   spec.py fetch.py project.py prepare.py scale.py mesh.py export.py pipeline.py cli.py
    app/            main.py jobs.py geocode.py
    tests/          fixtures/
    pyproject.toml  (uv, Python 3.13)
  frontend/         Vite + TS: map.ts search.ts controls.ts viewer.ts api.ts
  docs/superpowers/specs/
  tasks/todo.md tasks/lessons.md
  Makefile          make dev (Backend + Frontend), make test
```

Abhängigkeiten Backend: fastapi, uvicorn, pydantic, httpx, shapely ≥ 2, pyproj, osm2geojson, trimesh, manifold3d, numpy, typer.
Abhängigkeiten Frontend: maplibre-gl, three, vite, typescript, vitest, playwright.

## 11. Phase 2 (nach MVP, nicht Teil des Plans)

Küstenlinien, Stadtname-Gravur, Rahmen, `building:part`, Terrain, Presets für Städte, Docker-Image.
