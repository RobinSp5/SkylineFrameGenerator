# Bäume – Design Spec (Phase 6)

Datum: 2026-09-24
Status: freigegeben (Robin: „Fixt das bitte weltweit!“ nach einem Manhattan-Modell ohne Central-Park-Bäume)
Baut auf: Phase 4a/4b/5, alles in `main`

## 1. Ziel

Bäume gehören ins Modell, weltweit, auch in Dörfern. OSM-Einzelbäume sind extrem ungleich erfasst (New York viele, die meisten Dörfer keine), deshalb ist die Hauptquelle eine weltweite Landbedeckungskarte.

## 2. Entscheidungen

- `FrameSpec.trees: bool = True`, CLI `--trees/--no-trees`, UI-Schalter „Trees“ (Standard an). `trees=False` → Ausgabe bit-identisch zum Stand vor Phase 6.
- Quellen: **ESA WorldCover 10 m v200 (2021)**, Klasse 10 „Tree cover“, plus **OSM** `natural=tree` (Knoten) und `natural=tree_row` (Wege, Punkte im Abstand der Krone). WorldCover nicht ladbar → nur OSM-Bäume, kein Abbruch, Hinweis in den Stats.
- Druck: Bäume sind Kuppeln (Kugelkappe, 2.5D, keine Überhänge) auf Boden bzw. Gelände; nie auf Gebäuden, Straßen, Wasser. Eigenes 3MF-Teil `trees` (Farbe Grün in der Vorschau).
- Lizenz WorldCover: CC BY 4.0, „provided free of charge, without restriction of use“. Pflichtzeile in SOURCES.txt: „Bäume: © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium, CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/).“ Nur wenn WorldCover-Daten wirklich eingeflossen sind.

## 3. Schnittstelle (`skylineframe/trees/model.py`, im Repo)

- `OsmTree(x, y, height_m, crown_m)` — Rohdaten, CRS der Pipeline-Stufe.
- `Tree(x_mm, y_mm, crown_mm, height_mm)` — ein zu druckender Baum im zentrierten Druck-Rahmen; Höhe bereits mit `spec.scale` und `spec.z_exaggeration` skaliert; nicht auf Druckbares geklemmt.
- Datenhälfte liefert `tree_layer(spec, osm_trees: list[OsmTree] (lokale Meter), cache_dir, client=None) -> TreeLayer` in `trees/layer.py` mit `TreeLayer(trees: list[Tree], source: str)`; `source` = `"worldcover"` wenn WorldCover-Bäume dabei sind, sonst `""`.

## 4. Datenhälfte (`trees/worldcover.py`, `trees/layer.py`, `fetch.py`, `features.py`, `project.py`)

1. **WorldCover-Kacheln:** `https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{N|S}{lat:02d}{E|W}{lon:03d}_Map.tif`, 3°×3°, Name = untere linke Ecke auf 3° abgerundet (z. B. N39W075, N48E006). uint8, Klasse 10 = Baum. 57–94 MB, Cache `<cache_dir>/worldcover/`, atomar, wie `terrain/dem.py` (dessen Muster wiederverwenden: Download, Cache, `_read_window` nur benötigter Innen-Kacheln, 404 = Ozean/keine Daten).
2. **Platzierung:** Baumflächen-Maske auf das Quadrat abbilden (Rotation wie beim DEM); darin Bäume auf einem gejitterten Raster (deterministisch, Hash der lokalen Koordinate, kein Zufallszustand) mit Abstand = Standard-Kronendurchmesser `TREE_CROWN_M = 8.0`; Höhe `TREE_HEIGHT_M = 12.0` ±20 % per Hash. Raster ausreichend fein, dass ein einzelnes 10-m-Pixel (Straßenbaum) mindestens einen Baum bekommt, wenn sein Zentrum im Quadrat liegt.
3. **OSM-Bäume:** Overpass-Abfrage um `node["natural"="tree"]` und `way["natural"="tree_row"]` erweitern (nur wenn `spec.trees`); `height`/`diameter_crown` parsen (Muster `_metres`). Projektion in `project_features`. Ein OSM-Baum ersetzt WorldCover-Bäume im Umkreis seiner Krone (keine Doppelbäume).
4. Umrechnung Meter → Druck: `x_mm = x_m * scale`, `crown_mm = crown_m * scale`, `height_mm = height_m * scale * z_exaggeration`.

## 5. Geometriehälfte (`mesh.py`, `scale.py`, `pipeline.py`, `export.py`, `spec.py`, `cli.py`, `lod2/sources.py`, Frontend)

1. Pipeline ruft `tree_layer` nur bei `spec.trees` (lazy import, injizierbar wie `terrain`).
2. **Freiraum:** Bäume, deren Krone Gebäude (inkl. Sockel), Straßen- oder Wasserflächen berührt, werden verkleinert, bis sie frei stehen, oder weggelassen, wenn sie dann unter die Mindestgröße fallen.
3. **Form:** Kugelkappe mit Basisdurchmesser `crown_mm`, Höhe `height_mm`; im Druckmodus Mindestdurchmesser `spec.min_line_mm`·1.25 (Kuppel oben breiter als eine Linie) und Mindesthöhe `spec.min_building_height_mm`·0.5 (Bäume sind niedriger als Häuser, aber sichtbar). Alle Bäume gemeinsam als **ein Höhenfeld** (Maximum der Kappen) auf einem Raster von höchstens 0,2 mm → ein Gitterkörper (`grid.grid_solid`), nur im Fenster mit Bäumen vernetzen; nicht tausende Einzelkörper.
4. Auf Gelände: Baumfuß = Gelände unter dem Baum (Höhenfeld addiert); Sink wie Gebäude.
5. Export: eigenes Teil `trees` in 3MF und GLB (Farbe `(84, 130, 53, 255)`), in STL vereinigt; `printable()` wie alle Teile.
6. Stats: `trees` (Anzahl), `trees_source`, ggf. `trees_note`; CLI druckt sie; Frontend-Schalter „Trees“ mit Hinweis „From ESA WorldCover and OpenStreetMap“, `summarize` nennt die Anzahl.

## 6. Abnahme

| Kriterium | Grenze |
|---|---|
| Central Park Süd (40.7644, -73.9730, 1200 m, 15 cm) | sichtbar Bäume im Park |
| Eppstein 1500 m `--terrain` | Wälder auf den Hängen |
| Bambu `--info` STL | manifold, 1 Teil; Slice ohne Warnung (Stichprobe je 1× OSM-lastig, 1× Wald) |
| `trees=False` | STL bit-identisch zu `main` |
| Laufzeit (Stichprobe, warm) | +≤30 %; STL +≤60 % |
| Tests | grün, kein Netz in Tests |

## 7. Ergebnis (gemessen 2026-09-24, warmer Cache)

| Lauf | Bäume | Laufzeit | STL | Bambu |
|---|---|---|---|---|
| Eppstein full `--terrain`, ohne Bäume | – | 16,4 s | 13,8 MB | – |
| Eppstein full `--terrain` | 15 984 | 25,4 s (+55 %) | 26,6 MB (+93 %) | manifold, 1 Teil, keine Warnung |
| Central Park Süd 1200 m / 15 cm full | 3 888 | 11,9 s | 19,7 MB | manifold, 1 Teil, keine Warnung |

Budget §6 verfehlt bei Laufzeit (+55 % statt +30 %) und STL (+93 % statt +60 %), weil bewaldete Flächen als Kuppelfeld auf 0,2-mm-Raster vernetzt werden und `simplify` gekrümmte Flächen kaum reduziert. Bewusst akzeptiert: 27 MB lädt Bambu Studio problemlos. Möglicher nächster Schritt, falls nötig: Dezimierung (z. B. `fast-simplification`) oder gröberes Raster nur für Waldflächen.

`trees=False`: STL bit-identisch zu 0c07703. WorldCover-Ausfall: nur OSM-Bäume, `trees_note` in Stats und Statuszeile.
