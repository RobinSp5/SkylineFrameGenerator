import json
from pathlib import Path

import pytest

from skylineframe.spec import FrameSpec, Mode

FIXTURES = Path(__file__).parent / "fixtures"

# Same parameters as fixtures/record_frankfurt.py — keep in sync.
FRANKFURT = FrameSpec(center_lat=50.1090, center_lon=8.6820, side_m=400, mode=Mode.full)
BANKENVIERTEL = FrameSpec(center_lat=50.1105, center_lon=8.6747, side_m=300, mode=Mode.full)


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
