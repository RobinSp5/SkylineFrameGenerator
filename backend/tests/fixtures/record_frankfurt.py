"""Record the Overpass response used by offline tests. Run once, with network:

    cd backend && uv run python tests/fixtures/record_frankfurt.py
"""

import json
import shutil
from pathlib import Path

from skylineframe.fetch import build_query, fetch_overpass
from skylineframe.project import query_bbox
from skylineframe.spec import FrameSpec, Mode

# Keep in sync with tests/conftest.py::FRANKFURT (Römer, north bank of the Main).
FRANKFURT = FrameSpec(center_lat=50.1090, center_lon=8.6820, side_m=400, mode=Mode.full)

here = Path(__file__).parent
tmp = here / "_tmp_cache"
data = fetch_overpass(build_query(query_bbox(FRANKFURT), FRANKFURT.mode), cache_dir=tmp)
(here / "frankfurt_roemer.json").write_text(json.dumps(data))
shutil.rmtree(tmp)
print(f"recorded {len(data['elements'])} elements")
