"""Record the Overpass responses used by offline tests. Run once, with network:

    cd backend && uv run python tests/fixtures/record_frankfurt.py
"""

import json
import os
import shutil
from pathlib import Path

from skylineframe.fetch import build_query, fetch_overpass
from skylineframe.project import query_bbox
from skylineframe.spec import FrameSpec, Mode

# Keep in sync with tests/conftest.py.
SPECS: dict[str, FrameSpec] = {
    # Römer, north bank of the Main: dense old town, 60 roof:shape buildings.
    "frankfurt_roemer.json": FrameSpec(center_lat=50.1090, center_lon=8.6820, side_m=400, mode=Mode.full),
    # Bankenviertel: towers modelled with building:part (Commerzbank Tower and neighbours).
    "frankfurt_bankenviertel.json": FrameSpec(center_lat=50.1105, center_lon=8.6747, side_m=300, mode=Mode.full),
}


def main() -> None:
    here = Path(__file__).parent
    tmp = here / "_tmp_cache"
    for name, spec in SPECS.items():
        data = fetch_overpass(build_query(query_bbox(spec), spec.mode), cache_dir=tmp)
        # Write beside the target and move it into place only once the download succeeded:
        # a failed second query must never leave the first fixture truncated.
        staged = here / f"{name}.{os.getpid()}.tmp"
        staged.write_text(json.dumps(data))
        os.replace(staged, here / name)
        print(f"recorded {name}: {len(data['elements'])} elements")
    shutil.rmtree(tmp)


if __name__ == "__main__":
    main()
