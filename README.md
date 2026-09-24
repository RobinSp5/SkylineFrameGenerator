# Skyline Frame Generator

Generates 3D-printable city cut-outs (buildings, optionally streets and water, on a square base plate)
from OpenStreetMap data. Output: STL (single colour) and 3MF (multi-colour, one object per colour).

https://github.com/user-attachments/assets/614b05d7-3558-42c3-a7cf-110bc110856a

<p align="center"><em>Pick a place, frame the square, generate, and print.</em></p>

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
default). `--no-lod2` switches off the official LoD2 building models and falls back to OpenStreetMap
heights everywhere; `--no-roofs` does not touch the LoD2 buildings that carry a body, because their
roof shape is part of the body rather than a separate solid — the ones that fell back to a prism are
flattened like any other building. The output reports buildings, blocks, parts, roofs, roads (count
and groove area in mm²), the footprint coverage (building area in the model divided by building area
in the square) and, where official data was used, the LoD2 source, how many buildings carry a real
body, how many fell back to a prism, and the triangle count of those bodies. The fall-back number
merges two causes without telling them apart: a model whose body would not close, and a closed body
the printability check dropped again because it stays below the minimum height or leaves the plate.

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
- In an area with LoD2 coverage (currently Hessen) the roofs are the real ones from the official model
  rather than a shape guessed from `roof:shape`; the ridges are where they are in the city, and towers
  hit their real height instead of an estimate.

## Tests

    make test    # pytest + vitest (offline)
    make e2e     # Playwright smoke test (backend mocked)

## Data sources

OpenStreetMap via the Overpass API (responses are cached under `backend/.cache/overpass`),
place search via Nominatim. Please respect the usage policies of both services.

Where official LoD2 building models are openly available, they replace the OpenStreetMap buildings:
real heights and real roof shapes instead of estimates. One source is wired up so far:

| Source | Coverage | Service |
|---|---|---|
| LoD2 Hessen | 7.777–10.224 E, 49.396–51.655 N | INSPIRE WFS 2.0.0, one request per square |

A square has to lie entirely inside a coverage; on a state border the run stays on OpenStreetMap
rather than mixing real and estimated heights inside one model. More states plug into the same
provider layer without touching the pipeline; Bavaria is verified but deferred until its required
attribution wording is available in machine-readable form. Everything else — roads, water and
the footprints outside the LoD2 stock — always comes from OpenStreetMap.

LoD2 responses are cached under `backend/.cache/overpass/lod2`, keyed per bounding box. They are
large: a 1500 m square in Frankfurt is about 152 MB (6 119 buildings, 114 672 polygons) and takes on
the order of a minute to fetch — 52 s measured once on one link, so that time tracks your connection
rather than the data. Deleting the directory only costs the next run its download.

If no source covers the square, the service is unreachable, or `--no-lod2` is given, the result is
exactly the OpenStreetMap-only model — a LoD2 outage never fails a run.

`SKYLINE_OVERPASS_URL` points the generator at a different Overpass endpoint (for example your own instance).

Soft limit: Overpass responses with more than 250,000 OSM elements are rejected, and a LoD2 response
above 256 MB is abandoned; choose a smaller square or the simple mode in that case.

## Data licence

Every run writes `SOURCES.txt` next to the model, naming each source it actually used. That file is
what has to travel with a print or a download.

The OpenStreetMap geometry is licensed under the [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/).
A printed model is a "Produced Work" in the sense of the ODbL: it may be sold, and the database itself
does not have to be published for that. Attribution is mandatory:

> Contains data from © OpenStreetMap contributors (ODbL)

visibly on the product page, on an insert, or on the base plate. ODbL section 4.3 ties that notice to
any public use of the Produced Work, not to sale: giving a print away, exhibiting it or uploading the
STL to a model-sharing site all require it. The generator does **not** write the notice into the
model itself; whoever sells, passes on or publishes a print has to add it.

The LoD2 models carry their own terms:

- **Hessen:** [Datenlizenz Deutschland – Zero – Version 2.0](https://www.govdata.de/dl-de/zero-2-0) — no conditions,
  attribution not required. `SOURCES.txt` names the source anyway.
