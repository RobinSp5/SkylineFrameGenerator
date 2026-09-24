# Gelände – Design Spec (Phase 4b)

Datum: 2026-09-24
Status: umgesetzt auf `phase4b-terrain` (Design im Chat bestätigt)
Baut auf: `2026-09-24-houses-not-slabs-design.md` (Phase 4a), in `main`

## 1. Ziel und Messung

Orte in Hügellage sollen ihr Relief zeigen. Heute ist die Platte immer eben.

| Ausschnitt 1500 m | Relief in Copernicus GLO-30 (roh, Oberflächenmodell) |
|---|---|
| Eppstein (Taunustal) | 177–342 m, **165 m** |
| Frankfurt Innenstadt | 90–128 m, 38 m (großteils Hochhaus- und Baum-Beulen) |

Bei 1:15 000 und Überhöhung 1,0 sind 165 m = 11 mm.

## 2. Entscheidungen (mit Robin)

- Gelände ist **Standard aus**, zuschaltbar: `FrameSpec.terrain: bool = False`.
- Eigene Überhöhung: `FrameSpec.terrain_exaggeration: float = 1.0`, Bereich `(0, 5]`, getrennt von `z_exaggeration` der Gebäude.
- CLI: `--terrain/--no-terrain`, `--terrain-z FLOAT`. API bekommt die Felder über `FrameSpec` automatisch.
- UI-Schalter und Regler kommen **später**, nicht in dieser Phase (offene Frontend-Änderungen von Robin im Working Tree, nicht anfassen).
- `terrain=False` ist **bit-identisch** zum heutigen Ergebnis (gleicher Code-Pfad, kein Heightfield).

## 3. Schnittstelle

`skylineframe/terrain/heightfield.py`, `Heightfield(z_mm, cell_mm, origin_mm)` — bereits im Repo, einzige Kopplung zwischen Daten- und Geometrieteil:

- `z_mm[j, i]` = Höhe über Plattenoberkante bei `x = origin_x + i·cell`, `y = origin_y + j·cell` im zentrierten Druck-Rahmen (derselbe wie jedes `Prism`).
- Raster deckt die Platte Kante bis Kante, Minimum ist exakt `0.0`.
- `sample(x, y)` bilinear, `Heightfield.flat(size)` für Tests.

## 4. Datenteil (`terrain/dem.py`, `terrain/ground.py`)

Einstieg: `terrain_heightfield(spec, cache_dir, client=None) -> Heightfield | None`.

1. **Kacheln:** Copernicus GLO-30 auf AWS, ohne Auth:
   `https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{N|S}{lat:02d}_00_{E|W}{lon:03d}_00_DEM/<gleicher Name>.tif`
   (untere linke Ecke der 1°-Kachel; z. B. `N50_00_E008_00`). Float32, Deflate, Georeferenz in den TIFF-Tags 33550/33922 (Pixelgröße lon hängt von der Breite ab, bei 50° N 1/2400°). Lesen mit `tifffile` + `imagecodecs` (neue Abhängigkeiten in `pyproject.toml`). Gemessen: 30 MB, 1,5 s.
2. **Cache:** `<cache_dir>/dem/<kachelname>.tif`, atomar schreiben (temp + rename), wie der LoD2-Cache.
3. **Fehlende Kachel (HTTP 404, Ozean):** Höhe 0 m; fehlen *alle* Kacheln, gilt das als Fehler (flach + Hinweis, keine Copernicus-Nennung). Lücken im DEM bekommen die Höhe des nächsten gültigen Pixels. Jeder andere Fehler (Netz, Timeout, kaputte Datei) → `None`; die Pipeline baut dann flach und meldet es (§6).
4. **Mosaik** der Kacheln über `query_bbox(spec, margin_m=200)`; Ausschnitt rastert nur, was gebraucht wird.
5. **Boden statt Oberfläche** (`ground.py`): Grauwert-Öffnung (`scipy.ndimage.grey_opening`) im DEM-Pixelraum mit Fenster ≈ 100 m (Pixelzahl aus der Pixelgröße in Metern, ungerade, mindestens 3), dann eine Glättung mit gleichem Fenster (`uniform_filter`), damit die Treppen der Öffnung verschwinden. Entfernt Bäume und Hochhäuser, lässt Hügel stehen.
6. **Umrechnung auf das Druckraster:** Zellgröße `TERRAIN_CELL_MM = 0.5`. Kubischer B-Spline ohne Vorfilter (`map_coordinates(order=3, prefilter=False)`): linear gab ein sichtbares 2-mm-Karomuster, ein interpolierender Spline schwingt an Klippen und Küsten über. Jeder Rasterknoten (x_mm, y_mm) → lokale Meter (/ scale) → Rotation zurück (`-rotation_deg`, wie `project._rotated_square_metric`) → WGS84 über `local_transformer(spec)` invers → bilinear im DEM.
7. **Normierung:** `z_mm = (h − min h) · spec.scale · terrain_exaggeration`.
8. **Quelle:** `Attribution` in `lod2/sources.py` (oder eigene Datei, gleiche Struktur) mit dem Pflichttext für bearbeitete Daten *und* dem Haftungssatz (Lizenz Art. 6 b/c):
   „Gelände: produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved. The organisations in charge of the Copernicus programme by law or by delegation do not incur any liability for any use of the Copernicus WorldDEM-30."
   `sources_text` bekommt einen optionalen Terrain-Block.

## 5. Geometrieteil (`mesh.py`, `scale.py`, `pipeline.py`, `spec.py`, `cli.py`)

`build_meshes(scaled, spec, terrain: Heightfield | None = None)`; `None` = heutiger Pfad unverändert.

1. **Geländekörper statt Platte:** Oberseite = Höhenraster (2 Dreiecke pro Zelle), vier senkrechte Seitenwände, Boden bei `z = −plate_thickness_mm`. Als `m3d.Manifold` aus Vertex-/Dreieck-Arrays (`m3d.Mesh`), Status NoError.
2. **Gebäude:** jedes Prisma/jeder LoD2-Körper wird um `z_base = min(terrain.sample)` über den Grundriss angehoben (Stützpunkte: alle Umriss-Vertices plus die Rasterknoten innerhalb des Grundrisses). Bergseitig steckt das Gebäude dann im Hang, talseitig steht es auf. Die Gebäudehöhe zählt ab `z_base`. `BUILDING_SINK_MM` wirkt relativ zu `z_base`. Teile, die in der Luft beginnen (`z0_mm > 0`), und Dächer verschieben sich um dasselbe `z_base` ihres eigenen Grundrisses.
3. **Sockel:** Block-Grundriss ∩ Schicht zwischen Oberfläche und Oberfläche + `SOCKEL_MM` (dasselbe Raster um `SOCKEL_MM` angehoben, ∩ Prisma des Blocks).
4. **Straßen/Wasser:** Schnittkörper = Prisma des Polygons ∩ Bereich über `Oberfläche − Tiefe`; Einlage = Prisma ∩ Schicht zwischen `Oberfläche − Tiefe` und `Oberfläche`. Tiefe wie heute (`road_depth_mm`, `water_depth_mm`).
5. **Export-Checks** (`export.verify_single` u. a.) auf nicht-ebene Platten anpassen, falls sie eine feste z-Höhe annehmen.
6. **Pipeline:** holt `terrain_heightfield` nur wenn `spec.terrain`; `None` trotz `terrain=True` → flaches Modell, `stats["terrain_source"] = ""` und `stats["terrain_note"] = "Gelände nicht verfügbar"`; sonst `"copernicus"` und `stats["terrain_relief_mm"]`. CLI druckt beides.

## 6. Abnahme

| Kriterium | Grenze |
|---|---|
| Eppstein 1500 m full `--terrain`: `single` NoError, wasserdicht | ja |
| Eppstein Relief (`terrain_relief_mm`) | 7–12 mm |
| Kein Gebäude schwebt: für jedes Gebäude Unterkante ≤ Gelände unter jedem Umriss-Vertex | ja |
| Mehrkosten mit warmem DEM-Cache | ≤ 5 s |
| `terrain=False`: STL bit-identisch zu `main` | ja |
| Offline mit `--terrain`: flaches Modell + Hinweis, kein Abbruch | ja |
| Test-Suite grün | ja |

## 7. Tests (Mindestumfang)

Kachelnamen N/S/E/W inkl. Vorzeichen-Kanten (−0,5° → S01/W001), 404 → 0 m, Netzfehler → `None`, Mosaik über Kachelgrenze, Öffnung entfernt 60-m-Beule und lässt 500-m-Hügel, Rotation (90° gedrehtes Quadrat sampelt gedrehtes Gelände), Normierung min = 0, Geländekörper-Volumen bei `Heightfield.flat` = heutige Platte, Gebäude auf Hang auf Minimum, Straßentiefe relativ zur Oberfläche konstant, SOURCES.txt mit Copernicus-Block nur bei Gelände. Netzwerk in Tests immer gemockt (httpx.MockTransport wie bei LoD2).

## 8. Ergebnis (gemessen 2026-09-24, Eppstein 1500 m full, warmer Cache)

| | ohne Gelände | mit Gelände |
|---|---|---|
| Laufzeit | 12,9 s | 15,5 s (+2,6 s; Kachel kalt +1,8 s einmalig) |
| Relief | – | 10,41 mm |
| STL | 9,3 MB | 19,2 MB (Sockel und Rillenböden folgen dem 0,5-mm-Raster) |
| Frankfurt Relief | – | 2,0 mm (Hochhaus-Beulen weggefiltert) |

`terrain=False`: STL SHA-256 identisch zu `main` vor Phase 4b. DEM-Ausfall: flach, „Gelände nicht verfügbar“, keine Copernicus-Nennung. Tests: 502 grün.

Review-Befunde behoben: gestapelte Teile am Hang schwebten 0,4 mm (Absenkung wird jetzt den Stapel hinauf weitergereicht), DEM-Lücken wurden 0-m-Gruben, reine 404-Ausschnitte nannten Copernicus, nicht beschreibbarer Cache brach den Lauf ab.

Offen: UI-Schalter und Regler (wartet auf Robins uncommittete Frontend-Änderungen), Slicer-Check in Bambu Studio.
