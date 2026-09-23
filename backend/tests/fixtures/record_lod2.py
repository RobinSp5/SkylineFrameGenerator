"""Record the Hessen LoD2 response used by the offline tests. Run once, with network:

    cd backend && uv run python tests/fixtures/record_lod2.py

The committed tests/fixtures/lod2_frankfurt.xml is the authority; this script only exists so the
fixture can be rebuilt. count=3 is what keeps the answer small: the Römer bbox alone holds far
more than three buildings, and the production request in lod2/hessen.py deliberately sends no
count at all.
"""

import os
from pathlib import Path

import httpx

from skylineframe.lod2.gml import parse_buildings
from skylineframe.lod2.hessen import OUTPUT_CRS, QUERY_CRS, TYPE_NAMES, WFS_URL

# Frankfurt Römer; keep in sync with the expectations in tests/test_lod2_gml.py.
BBOX = (50.1099, 8.6797, 50.1116, 8.6839)  # (south, west, north, east)
COUNT = 3
TARGET = "lod2_frankfurt.xml"


def main() -> None:
    here = Path(__file__).parent
    south, west, north, east = BBOX
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": TYPE_NAMES,
        "bbox": f"{south},{west},{north},{east},{QUERY_CRS}",
        "srsName": OUTPUT_CRS,
        "count": str(COUNT),
    }
    response = httpx.get(WFS_URL, params=params, timeout=120.0, follow_redirects=True)
    response.raise_for_status()
    # Write beside the target and move it into place only once the download succeeded: a failed
    # request must never leave the committed fixture truncated.
    staged = here / f"{TARGET}.{os.getpid()}.tmp"
    staged.write_bytes(response.content)
    buildings = parse_buildings(str(staged), "hessen")
    print(f"recorded {TARGET}: {len(buildings)} buildings, {sum(len(b.surfaces) for b in buildings)} rings")
    for building in buildings:
        print(f"  {building.osm_id} {building.name}")
    os.replace(staged, here / TARGET)


if __name__ == "__main__":
    main()
