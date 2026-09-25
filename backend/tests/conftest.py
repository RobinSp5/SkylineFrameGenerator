import json
from pathlib import Path

import pytest

from skylineframe.spec import FrameSpec, Mode

FIXTURES = Path(__file__).parent / "fixtures"

# Same parameters as fixtures/record_frankfurt.py — keep in sync. Trees and terrain off: both
# layers load tiles over the network, and the tests that want them inject a layer or heightfield of
# their own. Multicolor off: the tests that want the Bambu project turn it on themselves.
FRANKFURT = FrameSpec(
    center_lat=50.1090, center_lon=8.6820, side_m=400, mode=Mode.full, trees=False, terrain=False, multicolor=False
)
BANKENVIERTEL = FrameSpec(
    center_lat=50.1105, center_lon=8.6747, side_m=300, mode=Mode.full, trees=False, terrain=False, multicolor=False
)


@pytest.fixture
def frankfurt_spec() -> FrameSpec:
    return FRANKFURT.model_copy()


@pytest.fixture
def frankfurt_data() -> dict:
    return json.loads((FIXTURES / "frankfurt_roemer.json").read_text())


@pytest.fixture
def bankenviertel_spec() -> FrameSpec:
    return BANKENVIERTEL.model_copy()


@pytest.fixture
def bankenviertel_data() -> dict:
    return json.loads((FIXTURES / "frankfurt_bankenviertel.json").read_text())


@pytest.fixture
def lod2_xml_path() -> Path:
    """The recorded Hessen WFS response: three buildings around the Frankfurt Römer."""
    return FIXTURES / "lod2_frankfurt.xml"


@pytest.fixture
def lod2_frankfurt(lod2_xml_path) -> list:
    from skylineframe.lod2.gml import parse_buildings

    return parse_buildings(str(lod2_xml_path), "hessen")


@pytest.fixture
def lod2_local(lod2_frankfurt) -> list:
    """The recorded buildings projected into the local metric frame of the Römer square."""
    from skylineframe.project import local_transformer, project_lod2

    spec = FrameSpec(center_lat=50.1106, center_lon=8.6821, side_m=400, mode=Mode.full)
    tr = local_transformer(spec)
    return [project_lod2(b, tr, spec.rotation_deg) for b in lod2_frankfurt]
