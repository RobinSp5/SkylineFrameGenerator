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

## CLI

    cd backend && uv run skylineframe --lat 50.1106 --lon 8.6821 --side 1500 --mode full --out ../out

(Ein Unterkommando gibt es nicht — die Optionen stehen direkt hinter `skylineframe`.)

## Drucken (Bambu Studio)

- STL: direkt importieren, weiß drucken, 0,2 mm Layer, keine Stützen nötig.
- 3MF: importieren, dann **alle vier Objekte markieren → Rechtsklick → „Assemble“** (Zusammenbauen), damit sie ein
  Objekt mit mehreren Teilen bilden und die Einleger in den Vertiefungen bleiben statt einzeln auf die Druckplatte
  zu fallen. Danach `base`, `buildings`, `water`, `roads` je ein Filament zuweisen.
  Wer Straßen als Rille statt als Farbe will, löscht das Teil `roads`.

## Tests

    make test    # pytest + vitest (offline)
    make e2e     # Playwright-Smoke-Test (Backend gemockt)

## Datenquellen

OpenStreetMap über die Overpass-API (Antworten werden unter `backend/.cache/overpass` gecacht),
Ortssuche über Nominatim. Bitte die Nutzungsbedingungen beider Dienste beachten.

`SKYLINE_OVERPASS_URL` setzt einen anderen Overpass-Endpunkt (z. B. eine eigene Instanz).

Weiche Obergrenze: Antworten mit mehr als 250 000 OSM-Elementen werden abgelehnt — dann ein kleineres
Quadrat oder den einfachen Modus wählen.
