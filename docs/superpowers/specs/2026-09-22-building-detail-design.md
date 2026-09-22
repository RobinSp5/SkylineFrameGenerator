# Building Detail Upgrade – Design Spec

Datum: 2026-09-22
Status: freigegeben (Brainstorming abgeschlossen)
Baut auf: `docs/superpowers/specs/2026-09-21-skyline-frame-generator-design.md` (MVP, gemerged in `main`)

## 1. Ziel

Die gedruckten Modelle zeigen deutlich mehr Stadtstruktur und wiedererkennbare Silhouetten, ausschließlich aus OpenStreetMap-Daten (weltweit, kostenlos, ODbL: kommerzieller Verkauf von Drucken erlaubt, Namensnennung Pflicht).

Diagnose am Frankfurter Default-Ausschnitt (1500 m / 100 mm):

| Befund | Zahl |
|---|---|
| Gebäude im Quadrat | 2.534 |
| davon im Modell (MVP) | 609 (24 %) |
| davon verworfen durch Mindestfläche 1 mm² (= 225 m²) | 1.795 |
| davon verworfen durch Sliver-Filter (Erosion 6 m) | 130 |
| `building:part` in Frankfurt / Manhattan (1500 m) | 404 / 2.390 – im MVP nie abgefragt |
| Gebäude mit `roof:shape` (Frankfurt) | 27 % – im MVP ignoriert |
| Gebäude ohne `height` und ohne `building:levels` | ca. 70 % – pauschal 8 m |

## 2. Scope

Fünf Änderungen, alle in der bestehenden Pipeline (`backend/skylineframe/`) plus ein UI-Element:

1. Blockverschmelzung statt Löschen kleiner Grundrisse
2. `building:part` (Rücksprünge, Aufsätze) statt eines Kastens pro Umriss
3. Dachkörper aus `roof:shape`
4. Höhenschätzung nach Gebäudetyp und Grundfläche
5. Maßstabs-Presets in der Seitenleiste
6. README-Abschnitt zur ODbL-Namensnennung

Nicht im Scope: Fassaden, Fenster, Bäume, Terrain, Dächer über nicht-rechteckigen Grundrissen (bleiben flach), externe Höhendaten (LoD2, Overture), Küstenlinien.

## 3. Datenmodell

`features.py` wird erweitert:

```python
@dataclass
class Building:
    geom: BaseGeometry
    height_m: float          # Oberkante (inkl. Dach, falls height-Tag), sonst Traufhöhe
    min_height_m: float = 0.0
    roof: RoofSpec | None = None   # None = flach
    osm_id: int = 0
    is_part: bool = False
    outline_id: int | None = None  # bei Teilen: OSM-ID des Umrisses, falls zuordenbar

@dataclass
class RoofSpec:
    shape: str        # gabled | hipped | half_hipped | pyramidal | skillion | mansard | gambrel | dome | round
    height_m: float   # Dachhöhe (Firsthöhe über Traufe)
    direction_deg: float | None = None   # roof:direction, falls vorhanden
```

`Features` behält `buildings`; Teile stehen in derselben Liste mit `is_part=True`.

## 4. Abfrage (`fetch.py`)

Zusätzlich zu `way/relation["building"]`:

```
way["building:part"]{bb};
relation["building:part"]["type"="multipolygon"]{bb};
```

Parsing:
- `building=roof` wird verworfen (Carports, Vordächer).
- `building:part=no` und `building=no` werden verworfen.
- Teile: `min_height` (Zahl, optional „m") oder `building:min_level × 3.2`; `height` / `building:levels` wie bisher. Teile ohne jede Höhenangabe erhalten die Umrisshöhe (siehe §6) oder, ohne Umriss, den Default.
- `roof:shape` wird auf die unterstützten Werte gemappt (Tabelle §7); unbekannte Werte → flach. `roof:height` (m) oder `roof:levels × 3.2` als Dachhöhe; fehlt beides → Formel §7.
- `roof:direction` (Grad oder Himmelsrichtung N/E/S/W/NE …) optional.
- Wenn ein `height`-Tag vorhanden ist, gilt `height_m = height` und die Traufhöhe ist `height − roof.height_m` (nie unter 0,5 × height). Ohne `height`-Tag ist `height_m` die Traufhöhe (levels × 3,2 oder Schätzung) und das Dach kommt obendrauf.

## 5. Höhenschätzung (`fetch.py`, `estimate_height_m(tags, area_m2)`)

Reihenfolge: `height` → `building:levels × 3.2` → Typtabelle → Flächenregel.

| `building=` | Höhe (m) |
|---|---|
| cathedral | 35 |
| church, chapel, mosque, synagogue, temple | 18 |
| office, hotel, hospital, university | 20 |
| apartments, dormitory, civic, public, government | 15 |
| commercial, retail, school, industrial, warehouse, supermarket | 10 |
| house, detached, semidetached_house, terrace, residential, bungalow | 7 |
| garage, garages, shed, hut, carport, kiosk, service | 3 |
| sonst (`yes` u. a.) | Flächenregel |

Flächenregel: < 100 m² → 5 m; < 400 m² → 8 m; < 1500 m² → 12 m; sonst 15 m.

Die Fläche wird im lokalen metrischen System berechnet, deshalb läuft die Schätzung nach `project` (in `prepare`), nicht beim Parsen. `fetch` speichert die Tags, die dafür nötig sind (`building`-Wert) im `Building` (Feld `kind: str`).

## 6. Vorbereitung (`prepare.py`)

Reihenfolge:

1. **Projektion, Clip, Reparatur** wie bisher (`polygons_of`, `simplify`).
2. **Höhen auffüllen**: Gebäude ohne Höhenangabe erhalten die Schätzung aus §5.
3. **Teile zuordnen**: Für jedes Teil wird der Umriss gesucht, der den Teil-Schwerpunkt enthält (STRtree). Umrisse mit mindestens einem Teil gelten als „mit Teilen". Für solche Umrisse gilt: Teile werden gerendert; der Restbereich `Umriss − Vereinigung der Teile` wird mit der Umrisshöhe extrudiert, sofern er die Druckbarkeitsgrenze erreicht. Teile ohne Umriss werden wie normale Gebäude behandelt. Teile ohne Höhe bekommen die Umrisshöhe.
4. **Blöcke bilden**: Alle Grundrisse (Umrisse ohne Teile, Teile, Restbereiche) werden 2D vereinigt mit einem Close um `MIN_FEATURE_MM / 2 / scale` (dilate → erode, runde Verbindungen). Jede resultierende Fläche ist ein **Block**. Blockhöhe = 25. Perzentil der Traufhöhen der enthaltenen Grundrisse (flächengewichtet), mindestens `min_building_height_mm / scale`.
5. **Druckbarkeit**: Ein Grundriss ist „einzeln druckbar", wenn `area ≥ min_footprint_area_mm2 / scale²` und `buffer(−MIN_FEATURE_MM/2/scale)` nicht leer. Der Default für `min_footprint_area_mm2` sinkt von 1,0 auf 0,25. Nicht einzeln druckbare Grundrisse gehen **nur** in den Block ein (kein Verlust der Fläche mehr).
6. **Dächer**: Für einzeln druckbare Grundrisse mit `roof` wird geprüft, ob `area / minimum_rotated_rectangle.area ≥ 0.85`; nur dann wird das Dach erzeugt (sonst flach). Die Dachgeometrie wird in `mesh.py` gebaut; `prepare` liefert das Rechteck (vier Ecken) und die Dachparameter mit.
7. Straßen und Wasser: unverändert, aber „blocked" ist jetzt die Vereinigung der Blöcke (nicht der Einzelgrundrisse).

Ergebnis `Prepared.buildings: list[Building]` (einzeln druckbare, mit `roof`/`rect`), neu `Prepared.blocks: list[Block(geom, height_m)]`.

Erwartung: Frankfurt 1500 m / 100 mm → Gebäudefläche im Modell ≥ 95 % der Gebäudefläche im Quadrat (Messgröße im Abnahmetest).

## 7. Dächer (`roofs.py`, neu)

Alle Formen werden über dem minimalen umschließenden Rechteck des Grundrisses gebaut (Ecken `c0..c3`, lange Achse = Firstrichtung, sofern `roof:direction` nichts anderes sagt). Traufhöhe `z_e`, Firsthöhe `z_r = z_e + roof.height_m`. Körper via `manifold3d.Manifold.hull_points`:

| shape | Punkte der Hülle |
|---|---|
| gabled | 4 Ecken bei z_e + 2 Firstpunkte (Mitten der kurzen Seiten) bei z_r |
| hipped | 4 Ecken bei z_e + 2 Firstpunkte, um `0.5 × kurze Seite` von den kurzen Seiten eingerückt, bei z_r |
| half_hipped | wie hipped, Einrückung `0.25 × kurze Seite`, Firstpunkte bei z_r; zusätzlich Giebelpunkte bei `z_e + 0.6 × roof.height_m` an den kurzen Seiten |
| pyramidal | 4 Ecken bei z_e + Mittelpunkt bei z_r |
| skillion | 2 Ecken einer langen Seite bei z_e + gegenüberliegende 2 Ecken bei z_r |
| mansard, gambrel | wie hipped bzw. gabled, mit Einrückung `0.2 × kurze Seite` und einem zweiten Punktkranz bei `z_e + 0.7 × roof.height_m` auf `0.8` der Halbbreite (steiler Unterteil) |
| dome | `Manifold.sphere(1, 24)` skaliert auf (halbe Länge, halbe Breite, roof.height_m), Mittelpunkt bei z_e, `trim_by_plane((0,0,1), z_e)` |
| round | Halbzylinder: Hülle aus 2 × 13 Punkten auf Halbkreisbögen an beiden kurzen Seiten (Radius = halbe Breite, Höhe skaliert auf roof.height_m) |

Der Dachkörper wird auf die Grundrissfläche zugeschnitten: `roof ∩ prism(footprint, z_e … z_r + ε)`, damit bei Rechteck-Abweichungen (≥ 85 %) nichts übersteht. Dachhöhe ohne Tag: `0.29 × kurze Seite` (30° Neigung), begrenzt auf 2 … 6 m; für dome/round `0.5 × kurze Seite`. Unter `0.3 mm` Druckhöhe wird kein Dach erzeugt (nicht sichtbar).

Der Gebäudekörper besteht dann aus `prism(footprint, z_min … z_e)` + Dachkörper. Bei `height`-Tag bleibt die Gesamthöhe exakt `height`.

## 8. Mesh (`mesh.py`)

- `blocks` werden wie Gebäude extrudiert (Sockel, versenkt um `BUILDING_SINK_MM` für `single`).
- Einzeln druckbare Gebäude: Prisma von `max(min_height, 0)` bis Traufe, plus Dach. Teile mit `min_height > 0` beginnen in der Luft; da sie in der Praxis auf niedrigeren Teilen desselben Gebäudes stehen, entsteht nach der 3D-Vereinigung ein zusammenhängender Körper. Freischwebende Teile (ohne Körper darunter) werden bis 0 verlängert (Sicherheitsregel: `min_height` wird ignoriert, wenn kein anderer Grundriss desselben Umrisses darunter liegt).
- Alles wird per `batch_boolean(Add)` zum Teil `buildings` vereinigt; `single` und die 3MF-Teile bleiben wie im MVP.
- Skalierung: alle Höhen (Traufe, First, min_height, Blockhöhe) laufen durch `building_height_mm`-Logik (scale × z_exaggeration, Mindesthöhe, Deckel Plattengröße). Dachhöhen werden mit demselben Faktor skaliert, damit Proportionen stimmen.

## 9. Presets (`frontend`)

`<select id="preset">` in der Seitenleiste vor „Ausschnitt":

| Preset | side_m | plate_size_mm | Maßstab |
|---|---|---|---|
| Skyline (Default) | 1500 | 100 | 1:15.000 |
| Detail | 800 | 100 | 1:8.000 |
| Groß | 1500 | 200 | 1:7.500 |
| Eigene | – | – | Wert der Felder |

Auswahl setzt Slider und Plattenfeld; manuelle Änderung springt auf „Eigene".

## 10. CLI

`--preset {skyline,detail,gross}` setzt `side`/`plate`, explizite Flags gewinnen. `--roofs/--no-roofs` (Default an), `--parts/--no-parts` (Default an) für Vergleiche.

## 11. Statistik

`stats` erhält zusätzlich: `buildings_individual`, `blocks`, `parts`, `roofs`, `footprint_coverage` (Gebäudefläche im Modell / Gebäudefläche im Quadrat, 0..1). Die CLI gibt sie aus, die UI zeigt „N Gebäude, M Blöcke, K Dächer".

## 12. Lizenz (README)

Abschnitt „Lizenz der Daten": OSM-Daten stehen unter ODbL. Drucke sind „Produced Works": Verkauf erlaubt, Hinweis „Enthält Daten von © OpenStreetMap-Mitwirkende (ODbL)" auf Produktseite, Beilage oder Bodenplatte nötig. Der Generator selbst schreibt nichts in das Modell.

## 13. Tests

- `fetch`: Parsing von `building:part`, `min_height`, `building:min_level`, `roof:*`, Verwerfen von `building=roof`; Höhenschätzung nach Typ und Fläche.
- `prepare`: Teil-Zuordnung (Teil im Umriss, Teil ohne Umriss, Umriss mit Restbereich); Blockbildung (drei 8-m-Reihenhäuser → ein Block, Höhe = Perzentil); kleine Grundrisse landen im Block statt im Nichts; Straßen blockiert durch Blöcke.
- `roofs`: Volumen jeder Form über einem 10 × 6-Rechteck (gabled 90 m³ bei 3 m, pyramidal 60, hipped 72, skillion 90, dome ≈ 90); Zuschnitt auf L-Grundriss (Ratio < 0,85 → flach); Dach unter 0,3 mm entfällt.
- `mesh`: Teil mit `min_height` auf niedrigerem Teil → ein Körper; freischwebendes Teil wird bis 0 verlängert; Block + einzelnes Gebäude vereinigt; wasserdicht.
- `pipeline`: Frankfurt-Fixture (Römer, 400 m) → `footprint_coverage ≥ 0.95`, `roofs > 0` (Fixture hat 60 `roof:shape`-Gebäude). Die bestehende Fixture wurde ohne `building:part`-Abfrage aufgezeichnet (0 Teile, nur zufällig enthalten); sie wird mit der erweiterten Query neu aufgezeichnet (`record_frankfurt.py`, einmalig Netz), und zusätzlich wird eine zweite kleine Fixture `frankfurt_bankenviertel.json` (Mitte 50.1105, 8.6747, 300 m, full) aufgezeichnet, die den Commerzbank Tower mit Teilen enthält → `parts > 0`.
- Frontend: Preset setzt Felder, manuelle Änderung → „Eigene" (vitest, happy-dom).
- Abnahme: Frankfurt 1500 m Skyline und Detail, Manhattan Midtown 1500 m; Kennzahlen dokumentieren; Slicen in Bambu Studio.

## 14. Kompatibilität

Bestehende API bleibt; `FrameSpec` erhält `roofs: bool = True`, `parts: bool = True`, `min_footprint_area_mm2` Default 0,25. 3MF-Teile und Dateinamen unverändert.
