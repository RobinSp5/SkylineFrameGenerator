# Skyline Frame Generator

Generates 3D-printable city cut-outs (buildings, optionally streets and water, on a square base plate)
from OpenStreetMap data. Output: STL (single colour) and 3MF (multi-colour, one object per colour).

## Requirements

- Python ≥ 3.13 and [uv](https://docs.astral.sh/uv/)
- Node ≥ 20

## Getting started

    make setup   # once
    make dev     # backend on :8000, frontend on http://localhost:5173

Search for a place, drag the square on the map, set size and rotation, click "Generieren", download the STL or 3MF.

The server deliberately runs with a single uvicorn worker: the rate limiter for the place search (Nominatim,
max. 1 request per second) is per process and would be multiplied by additional workers.

## Production mode

    make build     # builds the frontend into frontend/dist
    make backend   # uvicorn on :8000, serves frontend/dist at /

After that http://localhost:8000 is all you need; no Vite server required. For development run them separately:
`make frontend` (Vite on :5173) and `make backend`.

## CLI

    cd backend && uv run skylineframe --lat 50.1106 --lon 8.6821 --preset skyline --mode full --out ../out

(There is no subcommand; the options follow `skylineframe` directly.)

| Preset | Square | Plate | Scale |
|---|---|---|---|
| `skyline` (default) | 1500 m | 100 mm | 1:15,000 |
| `detail` | 800 m | 100 mm | 1:8,000 |
| `gross` | 1500 m | 200 mm | 1:7,500 |

`--side` and `--plate` override the preset. For comparisons, `--no-roofs` keeps every roof flat and
`--no-parts` renders one box per outline instead of the `building:part` setbacks (both are on by
default). The output reports buildings, blocks, parts, roofs, roads (count and groove area in mm²)
and the footprint coverage (building area in the model divided by building area in the square).

## Printing (Bambu Studio)

- STL: import directly, print in white, 0.2 mm layers, no supports needed.
- 3MF: import, then **select all four objects → right-click → "Assemble"**, so they form one object with
  several parts and the inlays stay in their recesses instead of dropping onto the build plate individually.
  Then assign one filament each to `base`, `buildings`, `water` and `roads`.
  If you prefer streets as grooves instead of a colour, delete the `roads` part.
- Bambu Studio may report shared edges on import where buildings touch at a corner.
  **Decline** the automatic repair for the 3MF; it fills in the recesses for streets and water.
- Visual check after import: roofs sit on the houses rather than as needles above them, `building:part`
  setbacks do not hang freely above the model (floating parts are extended down to the plate), and the
  street grooves run through: the merged blocks end at the streets instead of bridging them.

## Tests

    make test    # pytest + vitest (offline)
    make e2e     # Playwright smoke test (backend mocked)

## Data sources

OpenStreetMap via the Overpass API (responses are cached under `backend/.cache/overpass`),
place search via Nominatim. Please respect the usage policies of both services.

`SKYLINE_OVERPASS_URL` points the generator at a different Overpass endpoint (for example your own instance).

Soft limit: responses with more than 250,000 OSM elements are rejected; choose a smaller square or the
simple mode in that case.

## Data licence

The geometry comes from OpenStreetMap and is licensed under the [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/).
A printed model is a "Produced Work" in the sense of the ODbL: it may be sold, and the database itself
does not have to be published for that. Attribution is mandatory:

> Contains data from © OpenStreetMap contributors (ODbL)

visibly on the product page, on an insert, or on the base plate. The generator does **not** write this
notice into the model itself; anyone selling prints has to add it.
