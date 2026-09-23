# Skyline Frame Generator – Todo

Plan: docs/superpowers/plans/2026-09-21-skyline-frame-generator.md
Spec: docs/superpowers/specs/2026-09-21-skyline-frame-generator-design.md

## Backend
- [x] Task 1: Backend-Grundgerüst und FrameSpec
- [x] Task 2: Feature-Container und lokale Projektion
- [x] Task 3: Overpass-Fetch, Parsing, Cache und Fixture (einmalig Netz)
- [x] Task 4: Clipping, Reparatur, Straßen-/Wasserflächen
- [x] Task 5: Skalierung Meter → Millimeter
- [x] Task 6: Mesh-Erzeugung mit manifold3d
- [x] Task 7: Export STL/3MF/GLB mit Verifikation
- [x] Task 8: Pipeline, CLI, Offline-E2E-Test
- [x] Task 9: FastAPI mit Hintergrund-Jobs
- [x] Task 10: Geocoding über Nominatim

## Frontend
- [x] Task 11: Vite-Grundgerüst, Quadrat-Geometrie, API-Client
- [x] Task 12: Karte mit verschiebbarem Quadrat und Ortssuche
- [x] Task 13: Seitenleiste, 3D-Vorschau, Verdrahtung

## Abschluss
- [x] Task 14: Makefile, README, Playwright-Smoke-Test
- [~] Task 15: (CLI-Läufe erledigt, Bambu-Studio-Prüfung offen)

## Review

**Datum:** 2026-09-22 — Task 15, Schritte 1, 2 und 4 (CLI-Läufe und Verifikation). Schritt 3 (Bambu Studio) steht noch aus.

### Echte Läufe

```
cd backend && uv run skylineframe --lat 50.1106 --lon 8.6821 --side 1500 --mode full --out ../out/frankfurt
cd backend && uv run skylineframe --lat 52.5200 --lon 13.4050 --side 2000 --rotation 30 --mode simple --plate 120 --out ../out/berlin
```

| Stadt | Modus | side | plate | Gebäude | Straßen | Wasser | STL | 3MF | GLB | Laufzeit (kalt / warm) |
|---|---|---|---|---|---|---|---|---|---|---|
| Frankfurt | full | 1500 m | 100 mm | 602 | 12 | 6 | 3 094 484 B (3,0 MiB) | 1 350 222 B (1,3 MiB) | 1 933 452 B (1,8 MiB) | 5,93 s / 2,66 s |
| Berlin | simple (30° rotiert) | 2000 m | 120 mm | 704 | 0 | 0 | 1 629 484 B (1,6 MiB) | 396 121 B (388 KiB) | 642 852 B (628 KiB) | 4,58 s / 2,58 s |

„Kalt“ = erster Lauf inklusive Overpass-Abruf, „warm“ = zweiter Lauf aus `backend/.cache/overpass`.
Beide Läufe liegen deutlich unter der 60-s-Grenze; Frankfurt erfüllt mit 602 Gebäuden die Erwartung > 500.
Die Spalten Straßen/Wasser sind die aufbereiteten (nach Klasse zusammengefassten) Geometrien, nicht die rohen OSM-Ways.

### Verifikation der Dateien (trimesh, außerhalb der CLI-Ausgabe)

**3MF-Teile**

| Datei | Teile | Extents (mm) | wasserdicht | Volumen (mm³) |
|---|---|---|---|---|
| frankfurt | `base` | 100 × 100 × 3,0 | ja | 28 233,07 |
| | `buildings` | 100 × 100 × 25,9 | ja | 4 262,08 |
| | `water` | 100 × 97,757 × 0,6 | ja | 629,50 |
| | `roads` | 100 × 100 × 0,4 | ja | 1 137,42 |
| berlin | `base` | 120 × 120 × 3,0 | ja | 43 200,00 |
| | `buildings` | 120 × 120 × 33,12 | ja | 5 815,53 |

Frankfurt enthält wie erwartet genau `base`, `buildings`, `water`, `roads`; Berlin im Simple-Modus genau `base` und `buildings` — keine `roads`/`water`.
Die Volumina der neu geladenen 3MF-Teile stimmen exakt mit den Werten im Speicher überein: der 3MF-Roundtrip ist verlustfrei (Vertices werden in voller float32-Genauigkeit geschrieben).

**STL**

| Datei | Extents (mm) | Abweichung x / y vom Plattenmaß | Volumen (mm³) |
|---|---|---|---|
| frankfurt | 100,0 × 100,0 × 28,9 | 0,00000 / 0,00000 | 32 495,150 |
| berlin | 120,0 × 120,0 × 36,12 | 0,00000 / 0,00000 | 49 015,528 |

Das Plattenmaß wird in beiden Fällen exakt getroffen (Toleranz 0,01 mm eingehalten).

### Offene Beobachtungen

- **Sich berührende Körper (zu beobachten im Slicer).** Der Export prüft jedes Teil vor dem Schreiben auf Wasserdichtigkeit — beide Läufe haben diese Prüfung bestanden. Lädt man die Dateien mit `trimesh.load(...)` in der Standardeinstellung neu, meldet trimesh trotzdem `is_watertight = False`. Ursache ist nicht ein Loch, sondern das automatische Verschmelzen deckungsgleicher Vertices beim Laden: Gebäude, die sich exakt berühren, teilen sich danach eine Kante. Das Kanten-Histogramm zeigt ausschließlich gerade Zahlen (Frankfurt 90 476 × 2, 1 152 × 4, 16 × 6, 1 × 8; Berlin 48 810 × 2, 36 × 4) — keine einzige Kante mit 1 oder 3 Flächen, also keine offene Hülle. Mit `process=False` geladen sind alle 3MF-Teile `watertight = True` und `is_volume = True`. Beim Slicen darauf achten, ob Bambu Studio hier eine Reparatur- oder Nicht-mannigfaltig-Warnung zeigt (Step 3, Punkt 1).
- **Entartete Dreiecke.** Die Frankfurt-Vereinigung enthält 1 131 Dreiecke mit Fläche 0 (Nebenprodukt der manifold3d-Booleans), Berlin nur 4. Sie stören das Volumen nicht, blähen die STL aber auf. Kandidat für Phase 2: entartete Flächen vor dem Export entfernen.
- **Wasserfläche randnah.** Der `water`-Teil in Frankfurt ist mit 97,757 mm in y knapp kleiner als die Platte — der Main läuft nicht bis an beide y-Ränder. Erwartet, kein Fehler.
- **Gebäudehöhen.** Gebäude ohne `height`/`building:levels` fallen auf `default_building_height_m = 8,0` zurück und wirken dadurch flach. Für die Sichtprüfung im Slicer relevant; Phase-2-Kandidat wäre eine Höhenschätzung aus der Grundfläche.
- **Dateigröße nicht bit-identisch.** Wiederholte Läufe erzeugen 3MF-Dateien mit minimal abweichender Byte-Größe (ZIP-Zeitstempel), die Geometrie ist identisch.

### Bambu Studio (manuell, offen)

- [ ] `out/frankfurt/model.stl` importieren: ein Objekt, 100 × 100 mm Grundfläche, Slicing ohne Warnungen zu nicht-mannigfaltigen Kanten.
- [ ] `out/frankfurt/model.3mf` importieren: vier Objekte `base`, `buildings`, `water`, `roads`; alle markieren → Rechtsklick → „Assemble“ → ein Objekt mit vier Teilen, Einleger bündig in den Vertiefungen (nicht auf der Druckplatte); je Teil ein Filament (weiß, weiß, blau, grau); Slicing ohne Fehler. Falls Bambu Studio die Teile beim Assemble verschiebt: Ergebnis dokumentieren und als Alternative pro Teil ein STL exportieren und gemeinsam importieren (Phase-2-Kandidat).
- [ ] Sichtprüfung im Slicer: Straßen als Rillen erkennbar, Main als Vertiefung, Gebäude am Plattenrand sauber abgeschnitten.

### Nach der Fix-Welle (2026-09-22, Commits 60aaad3..51a86f7)
| Datei | Nicht-mannigfaltige Kanten (nach Vertex-Merge) | davon z ≤ 0 (Platte) | Degenerierte Dreiecke |
|---|---|---|---|
| Frankfurt STL | 1169 → 51 | 1113 → 0 | 1129 → 2 |
| Berlin STL | 36 → 36 | 0 → 0 | 5 → 5 |
Restliche geteilte Kanten liegen ausschließlich an Gebäudeecken (z > 0), die sich berühren; Slicer verschmelzen diese. Beim 3MF-Import in Bambu Studio eine angebotene Auto-Reparatur ablehnen (Vertiefungen). Weitere Änderungen: Overpass-Elementlimit 250.000, Höhen-Plausibilität (> 1000 m → Default, Deckel = Plattengröße), Geocoder bricht im Backoff sofort ab, `AreaError` statt `ValueError`, `extra="forbid"` in FrameSpec. Tests: Backend 127, Frontend 18, Playwright 2.

## Phase 2 – Building Detail Upgrade
Plan: docs/superpowers/plans/2026-09-22-building-detail-upgrade.md
Spec: docs/superpowers/specs/2026-09-22-building-detail-design.md
- [x] Task 1: Datenmodell, Spec-Felder und Projektion
- [x] Task 2: Fetch — building:part, Dach-Tags, Höhenschätzung, neue Fixtures (einmalig Netz)
- [x] Task 3: Prepare — Höhen auffüllen, Teile zuordnen, Blöcke bilden, Dächer vorbereiten
- [x] Task 4: Dachkörper (roofs.py)
- [x] Task 5: Skalierung und Mesh — Blöcke, Teile, Dächer
- [x] Task 6: Pipeline, Statistik, CLI-Flags und API-Durchreichung
- [x] Task 7: Frontend — Presets und neue Statuszeile
- [~] Task 8: README und Abnahme (CLI-Läufe und Kennzahlen erledigt, Bambu-Studio-Prüfung offen)

## Review Building Detail Upgrade

**Datum:** 2026-09-22 — Task 8, Schritte 1–6 (README, vier Abnahmeläufe, Verifikation). Schritt 7 (Bambu Studio) steht noch aus.

### Echte Läufe

```
cd backend && uv run skylineframe --lat 50.1106 --lon 8.6821 --preset skyline --mode full --out ../out/frankfurt-skyline
cd backend && uv run skylineframe --lat 50.1106 --lon 8.6821 --preset detail  --mode full --out ../out/frankfurt-detail
cd backend && uv run skylineframe --lat 40.7580 --lon -73.9855 --side 1500 --plate 100 --mode full --out ../out/manhattan
cd backend && uv run skylineframe --lat 50.1106 --lon 8.6821 --preset skyline --mode full --no-roofs --no-parts --out ../out/frankfurt-flat
```

| Lauf | Gebäude | Blöcke | Teile | Dächer | Straßen (mm²) | Coverage | STL | 3MF | Laufzeit |
|---|---|---|---|---|---|---|---|---|---|
| Frankfurt Skyline (1500 m / 100 mm, full) | 851 | 164 | 152 | 23 | 23 Stück / 2 649 mm² | 97,0 % | 5 028 184 B (4,8 MiB) | 2 082 438 B (2,0 MiB) | 9,09 s (kalt) |
| Frankfurt Detail (800 m / 100 mm, full) | 678 | 73 | 76 | 51 | 17 Stück / 1 598 mm² | 99,4 % | 2 512 384 B (2,4 MiB) | 973 530 B (0,93 MiB) | 18,81 s (kalt) |
| Manhattan Midtown (1500 m / 100 mm, full) | 1 144 | 165 | 504 | 13 | 6 Stück / 2 745 mm² | 99,4 % | 2 998 384 B (2,9 MiB) | 1 144 143 B (1,1 MiB) | 26,82 s (kalt) |
| Frankfurt ohne Dächer/Teile (1500 m / 100 mm, full) | 760 | 166 | 0 | 0 | 19 Stück / 2 698 mm² | 97,1 % | 4 854 184 B (4,6 MiB) | 2 021 202 B (1,9 MiB) | 4,66 s (warm) |

„Kalt“ = mit Overpass-Abruf, „warm“ = aus `backend/.cache/overpass` (der Vergleichslauf trifft denselben Cache-Eintrag wie der Skyline-Lauf).
„Gebäude“ sind die einzeln stehenden Körper; die übrigen Grundrisse stecken in den Blöcken.

**Diagnose je Lauf (aus der CLI-Ausgabe)**

| Lauf | Nicht-mannigfaltige Kanten nach Vertex-Merge | Degenerierte Dreiecke |
|---|---|---|
| Frankfurt Skyline | 143 | 134 |
| Frankfurt Detail | 236 | 273 |
| Manhattan Midtown | 81 | 86 |
| Frankfurt ohne Dächer/Teile | 21 | 1 |

### Verifikation der Dateien (trimesh, `process=False`)

| Lauf | STL-Extents (mm) | Plattenmaß ±0,01 mm | STL-Volumen (mm³) | 3MF-Teile |
|---|---|---|---|---|
| Frankfurt Skyline | 100,0000 × 100,0000 × 28,900 | ja | 35 213,8 | `base`, `buildings`, `water`, `roads` |
| Frankfurt Detail | 100,0000 × 100,0000 × 16,500 | ja | 37 771,6 | `base`, `buildings`, `water`, `roads` |
| Manhattan Midtown | 100,0000 × 100,0000 × 42,700 | ja | 64 602,6 | `base`, `buildings`, `water`, `roads` |
| Frankfurt ohne Dächer/Teile | 100,0000 × 100,0000 × 28,900 | ja | 35 163,1 | `base`, `buildings`, `water`, `roads` |

Alle 3MF-Teile sind einzeln geladen `watertight = True`. Das vereinigte STL meldet nach `merge_vertices()`
wie in Phase 1 `is_watertight = False`, aber **keine** Kante mit ungerader Flächenzahl (Skyline
150 538 × 2, 127 × 4, 13 × 6, 3 × 8) — also kein Loch, sondern nur geteilte Kanten an sich berührenden
Körpern. Die Zahlen decken sich exakt mit der CLI-Diagnose (127 + 13 + 3 = 143).

### Beobachtungen

- **Flächenabdeckung statt Gebäudezahl.** Der MVP hatte am Frankfurter Default 609 von 2 534 Grundrissen
  im Modell (24 %); jetzt sind es 97,0 % der Gebäudefläche im Quadrat (851 Einzelkörper plus 164 Blöcke
  über den restlichen der 2 515 Grundrisse) — die Blockverschmelzung ersetzt das Aussortieren kleiner
  Häuser.
- **Straßenrillen bleiben offen.** 2 649 mm² Rillenfläche auf der 100 × 100 mm-Platte (Erwartung > 1 000 mm²);
  die Blöcke überbrücken die Straßen also nicht (Spec §6.4). Detail 1 598 mm², Manhattan 2 745 mm².
- **Dächer: 23 statt der im Task erwarteten > 100.** Nachgerechnet für Frankfurt Skyline: 211 Grundrisse
  mit nicht-flachem `roof:shape` im Abruf, 158 davon im Quadrat, 114 davon gehen in einen Block auf
  (ein Block hat per Spec §2.1 kein Dach), von den 44 verbleibenden Einzelgebäuden fallen 21 durch die
  Rechteck-Regel aus §7 (L- und Kammformen). Bleiben 23. Das ist die Folge der beiden Design-Regeln,
  kein Fehler — im `detail`-Preset (kleinerer Ausschnitt, weniger Verschmelzung) sind es 51 Dächer bei
  weniger Gebäuden, also deutlich mehr Dächer pro Fläche, wie erwartet.
- **Manhattan ist der Teile-Fall.** 504 `building:part`-Körper (Erwartung > 0), 99,4 % Abdeckung,
  Modellhöhe 42,7 mm — die Türme mit Rücksprüngen sind da.
- **`--no-roofs --no-parts` spart weniger als gedacht.** Das STL schrumpft nur von 5 028 184 B auf
  4 854 184 B (−3,5 %): bei 152 Teilen und 23 Dächern gegenüber 851 Gebäuden und 164 Blöcken fällt die
  Detailgeometrie kaum ins Gewicht. Auffällig ist eher die saubere Diagnose des flachen Laufs
  (21 geteilte Kanten, 1 degeneriertes Dreieck) — Teile und Dachkörper erzeugen die meisten Berührkanten.
- **Laufzeiten.** Alle vier Läufe unter 30 s, weit unter der Drei-Minuten-Grenze; der Löwenanteil ist der
  Overpass-Abruf (CPU-Anteil 20–25 % bei den kalten Läufen).

### Bambu Studio (manuell, offen)

- [ ] `out/frankfurt-skyline/model.stl` importieren: ein Objekt, 100 × 100 mm, Slicing ohne Warnungen zu nicht-mannigfaltigen Kanten; Dächer als Sattel-/Walmdächer erkennbar, Blöcke als zusammenhängende Sockel statt einzelner Kästen.
- [ ] `out/frankfurt-skyline/model.3mf` importieren, alle Objekte markieren → Rechtsklick → „Assemble“, je Teil ein Filament. Erwartung: `base`, `buildings`, `water`, `roads` wie bisher.
- [ ] Sichtprüfung: keine schwebenden Teile über dem Modell, keine Nadeln auf Dächern, Türme des Bankenviertels mit Rücksprüngen, Straßenrillen laufen durch (Blöcke enden an den Straßen).
- [ ] Die automatische Reparatur beim 3MF **ablehnen** — sie füllt die Vertiefungen auf.

## Phase 3 – LoD2 Import
Plan: docs/superpowers/plans/2026-09-23-lod2-import.md
Spec: docs/superpowers/specs/2026-09-23-lod2-import-design.md
- [x] Task 1: Spec-Flag, Container, Projektion und Quellentexte
- [x] Task 2: lod2/gml.py — streamender Parser, Fixture, Aufzeichnungsskript
- [x] Task 3: lod2/solidify.py — Flächenmodell zu wasserdichtem Körper
- [x] Task 4: lod2/provider.py und lod2/hessen.py — Protokoll, Registry, WFS
- [x] Task 5: Pipeline-Einbau — fetch holt, prepare verdrängt und verkörpert
- [ ] Task 6: scale und mesh — Körper statt Prisma plus Dach
- [ ] Task 7: Statistik, SOURCES.txt, CLI, API und UI
- [ ] Task 8: README und Abnahme (Frankfurt mit und ohne LoD2; Bambu Studio manuell)
(Bayern-Provider auf eine spätere Phase verschoben, siehe Spec 3.2)
