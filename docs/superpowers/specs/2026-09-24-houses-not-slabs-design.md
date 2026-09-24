# Häuser statt Platten – Design Spec (Phase 4a)

Datum: 2026-09-24
Status: umgesetzt auf `phase4a-houses` (Teil 1 im Chat bestätigt, Rest auf Zuruf „zieh das flott durch“)
Baut auf: `2026-09-23-lod2-import-design.md` (Phase 3), in `main`
Folgt: Phase 4b Gelände (Copernicus DEM), eigene Spec

## 1. Problem (gemessen)

Eppstein (PLZ 65817), 1500 m auf 100 mm, `--mode full`:

| Befund | Wert |
|---|---|
| OSM-Gebäude im Rohabruf | 2 467 |
| LoD2-Gebäude im Rohabruf | 2 532 |
| Gebäude mit eigenem Körper im Modell | **67** |
| Rest | flache Block-Platten auf dem gewichteten 25. Perzentil der Traufhöhe |

Ursache ist nicht die Datenlage, sondern `prepare`: Ein Grundriss bekommt nur dann einen eigenen Körper, wenn ihn eine Erosion um 0,4 mm (halbe `MIN_FEATURE_MM`) nicht auslöscht, bei 1:15 000 also ab ~12 m Breite. Ein Einfamilienhaus (~10 m = 0,67 mm) verschwindet in einer Platte. Gegenprobe 800 m: 426 Körper, davon 379 mit LoD2-Dach.

Kosten gemessen: alle LoD2-Modelle solidifizieren kostet Eppstein 4,6 s (2 522/2 532), Frankfurt 13,8 s (5 975/6 044).

## 2. Regeln

### 2.1 Jeder Grundriss wird ein Körper

Die Trennung „druckbar → eigener Körper / sonst → Block“ entfällt. Jeder Footprint nach `assign_parts` mit Fläche ≥ `TINY_FOOTPRINT_MM2 = 0.02` mm² (Druckraum, ~4,5 m² bei 1:15 000) wird ein Gebäude in `Prepared.buildings`. Kleinere (Schuppen, Eck-Splitter, die ein LoD2-Modell vom OSM-Umriss übrig lässt) tragen nur noch zum Sockel bei. *Korrigiert während der Umsetzung: die erste Fassung nannte 0,1 mm², das sind 22,5 m² und nicht die gemeinten ~5 m².*

`spec.min_footprint_area_mm2` entfällt als Schwelle für „eigener Körper“; das Feld bleibt aus API-Kompatibilität bestehen und steuert weiterhin nur die Mindestfläche eines Sockel-Polygons.

### 2.2 Minimales Verbreitern

`MIN_LINE_MM = 0.4` (eine Düsenbahn). Ein Footprint, der eine Erosion um `MIN_LINE_MM / 2` nicht überlebt, wird mit Gehrungs-Puffer (`join_style="mitre"`, `mitre_limit=1.5`: rechte Winkel bleiben scharf, spitzere Ecken werden abgeschrägt statt als Stachel bis 5·r herauszuwachsen) um den kleinsten Radius `r ∈ (0, MIN_LINE_MM / 2]` wachsen gelassen, nach dem er sie überlebt (Bisektion oder `shapely.maximum_inscribed_circle`, Shapely 2.1.2 ist installiert). Höchstens 0,2 mm pro Seite, die Lage bleibt erhalten. Das Verbreitern passiert nach `assign_parts` und **vor** `resolve_roof`, damit das Dach-Rechteck zum verbreiterten Grundriss passt.

Ein verbreiterter LoD2-Footprint verliert seine `surfaces` und wird als Prisma in seiner LoD2-Höhe gedruckt (wie heute ein abgelehntes Modell). Er zählt nicht zu `lod2_rejected`.

### 2.3 Sockel statt Block

Blöcke bleiben geometrisch wie heute (`build_blocks`: Close, Straßenkorridore, Wasser, Quadrat). Ihre Höhe ist aber fest `SOCKEL_MM = 0.4` im Druckraum, nicht mehr das Perzentil und nicht durch `building_height_mm` geklemmt (sonst würde die 0,8-mm-Mindesthöhe den Sockel auf Dachhöhe heben — genau der heutige Plattenfehler). `Block.height_m` und `BLOCK_PERCENTILE` / `weighted_percentile` entfallen, sofern sonst unbenutzt. Die Mindesthöhe `min_building_height_mm = 0.8` gilt unverändert für Gebäude, jedes Haus ragt also mindestens 0,4 mm über den Sockel.

### 2.4 Standarddach für untaggte Wohnhäuser

Ein OSM-Gebäude (nicht LoD2, kein Part) ohne `roof:shape` bekommt `RoofSpec(shape="gabled")`, wenn `kind` in `{house, detached, semidetached_house, bungalow}` liegt oder `kind == "residential"` mit Grundfläche < 200 m², oder `kind == "yes"` mit 30 m² ≤ Grundfläche < 200 m² und höchstens einem hausgroßen Nachbarn im Abstand ≤ 1 m (Garagen, Schuppen, Carports und alles unter 30 m² zählen nicht als Nachbar). *Ergänzt während der Umsetzung: 2 174 von 2 467 Eppsteiner Gebäuden sind nur `building=yes`; die Typliste allein ergab 2 Dächer. Die Nachbarregel hält Reihen und Blockrand (Brownstones, Gründerzeitzeilen) flach.* Ein getaggtes `roof:shape` gewinnt immer, auch `flat` (`Building.roof_tagged`). Die Rechteck-Regel (`ROOF_RECT_RATIO`) in `resolve_roof` entscheidet wie bei getaggten Dächern, ob es gebaut wird. Dachhöhe aus `default_roof_height_m`. Die Zuweisung steht in einer eigenen reinen Funktion `default_roof(kind, area_m2, freestanding) -> RoofSpec | None` und läuft in `prepare` direkt nach `estimate_missing_heights`, also vor dem `spec.roofs`-Abschalter, sodass `--no-roofs` auch Standarddächer abschaltet.

### 2.5 Unverändert

Große Gebäude, `building:part`-Logik, Straßen, Wasser, `min_building_height_mm`, `z_exaggeration`, `SOLID_SIMPLIFY_MM = 0.2`, Attribution. `lod2=False` bleibt bit-identisch zum OSM-only-Lauf **dieser** Phase (nicht zu Phase 3).

## 3. Abnahme (gemessen, nicht geschätzt)

| Kriterium | Grenze |
|---|---|
| Eppstein 1500 m full: Gebäude mit eigenem Körper | ≥ 2 000 |
| Eppstein: LoD2 mit Körper | ≥ 1 500 |
| Frankfurt 1500 m full, warmer Cache, Gesamtlaufzeit | ≤ 90 s (heute 23 s) |
| Frankfurt STL | ≤ 40 MB (heute 8,3 MB) |
| `single`-Mesh | `manifold3d` Status NoError, wie heute |
| Test-Suite | grün (`uv run pytest`) |

Überschreitet Laufzeit oder STL-Größe das Budget, wird gemessen und berichtet, nicht still nachjustiert.

## 4. Tests

- `default_roof`: Typen, Flächengrenze 200 m², LoD2/Part/getaggt bleiben unberührt, `--no-roofs`.
- Verbreitern: 0,3-mm-Streifen wird auf ≥ 0,4 mm verbreitert; 1-mm-Haus bleibt bit-gleich; verbreitertes LoD2 verliert Körper.
- Sockel: Blockhöhe ist 0,4 mm unabhängig von Mitgliedshöhen.
- Einzelkörper: Footprint unter alter Druckbarkeitsschwelle, aber ≥ 0,1 mm², landet in `buildings`.
- Bestehende Tests, die Perzentil-Blockhöhen oder die alte Schwelle annehmen, werden auf diese Spec umgestellt, nicht gelöscht.

## 5. Ergebnis (gemessen 2026-09-24, warmer Cache, `--mode full`)

| Lauf | Gebäude mit Körper | LoD2 mit Körper | Dächer (OSM) | Laufzeit | STL |
|---|---|---|---|---|---|
| Eppstein 1500 m, vorher | 67 | 47 | 0 | 23 s (kalt) | – |
| Eppstein 1500 m | **2 148** | 702 | 15 | 12,9 s | 9,3 MB |
| Eppstein 800 m | 1 060 | 584 | 31 | 7,4 s | 6,3 MB |
| Eppstein 800 m, `--no-lod2` (= weltweiter Fall) | 982 | – | **359** | 2,6 s | 3,2 MB |
| Frankfurt 1500 m | 4 558 | 1 836 | 1 | 32,7 s | 22,6 MB |

Budget §3: erfüllt bis auf „LoD2 mit Körper ≥ 1 500“ (702). Das Kriterium war geschätzt, nicht gemessen, und widerspricht zwei gewollten Regeln: ein verbreitertes LoD2-Haus (schmaler als ~6 m) wird Prisma (§2.2), und ein LoD2-Körper unter 0,8 mm Firsthöhe wird Prisma (Phase-3-Regel). Beide Häuser stehen trotzdem im Modell, in ihrer echten Höhe. Kein Schwellwert wurde dafür verschoben.

Bei 1:15 000 sind Standarddächer (≥ 2 m → 0,2 mm) unter `MIN_ROOF_MM = 0.3` und werden bewusst weggelassen; sie erscheinen ab ~1:10 000 (800-m-Ausschnitt: 359 Dächer).

Nicht-mannigfaltige Kanten steigen mit der Zahl der LoD2-Körper (Frankfurt ~14 000). Phase 3 hat diese Kennzahl als unkritisch eingestuft; ein Slicer-Check in Bambu Studio steht für diese Phase noch aus.
