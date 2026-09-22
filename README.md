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

Der Server läuft bewusst mit einem einzigen uvicorn-Worker: der Rate-Limiter für die Ortssuche (Nominatim,
max. 1 Anfrage/Sekunde) gilt pro Prozess und würde mit mehreren Workern vervielfacht.

## Produktionsmodus

    make build     # baut das Frontend nach frontend/dist
    make backend   # uvicorn auf :8000, liefert frontend/dist unter / aus

Danach genügt http://localhost:8000 — kein Vite-Server nötig. Für die Entwicklung getrennt:
`make frontend` (Vite auf :5173) und `make backend`.

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
Default an). Die Ausgabe nennt Gebäude, Blöcke, Teile, Dächer, Straßen (Anzahl und Rillenfläche
in mm²) und die Flächenabdeckung (Gebäudefläche im Modell / Gebäudefläche im Quadrat).

## Drucken (Bambu Studio)

- STL: direkt importieren, weiß drucken, 0,2 mm Layer, keine Stützen nötig.
- 3MF: importieren, dann **alle vier Objekte markieren → Rechtsklick → „Assemble“** (Zusammenbauen), damit sie ein
  Objekt mit mehreren Teilen bilden und die Einleger in den Vertiefungen bleiben statt einzeln auf die Druckplatte
  zu fallen. Danach `base`, `buildings`, `water`, `roads` je ein Filament zuweisen.
  Wer Straßen als Rille statt als Farbe will, löscht das Teil `roads`.
- Bambu Studio meldet beim Import u. U. gemeinsame Kanten dort, wo sich Gebäude an einer Ecke berühren.
  Die automatische Reparatur beim 3MF bitte **ablehnen** — sie füllt die Vertiefungen für Straßen und
  Wasser auf.
- Sichtprüfung nach dem Import: Dächer sitzen auf den Häusern statt als Nadeln darüber, `building:part`-
  Rücksprünge hängen nicht frei über dem Modell (freischwebende Teile werden bis zur Platte verlängert),
  und die Straßenrillen laufen durch — die verschmolzenen Blöcke enden an den Straßen, statt sie zu
  überbrücken.

## Tests

    make test    # pytest + vitest (offline)
    make e2e     # Playwright-Smoke-Test (Backend gemockt)

## Datenquellen

OpenStreetMap über die Overpass-API (Antworten werden unter `backend/.cache/overpass` gecacht),
Ortssuche über Nominatim. Bitte die Nutzungsbedingungen beider Dienste beachten.

`SKYLINE_OVERPASS_URL` setzt einen anderen Overpass-Endpunkt (z. B. eine eigene Instanz).

Weiche Obergrenze: Antworten mit mehr als 250 000 OSM-Elementen werden abgelehnt — dann ein kleineres
Quadrat oder den einfachen Modus wählen.

## Lizenz der Daten

Die Geometrie stammt aus OpenStreetMap und steht unter der [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/).
Ein gedrucktes Modell ist ein „Produced Work“ im Sinne der ODbL: Es darf verkauft werden, und die
Datenbank selbst muss dafür nicht offengelegt werden. Pflicht ist die Namensnennung —

> Enthält Daten von © OpenStreetMap-Mitwirkende (ODbL)

— sichtbar auf der Produktseite, in einer Beilage oder auf der Bodenplatte. Der Generator schreibt
diesen Hinweis **nicht** selbst in das Modell; wer Drucke verkauft, muss ihn selbst anbringen.
