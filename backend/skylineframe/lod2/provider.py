"""Provider protocol and registry: which source answers for a bounding box (spec §4)."""

from pathlib import Path
from typing import Protocol

import httpx

from ..features import Lod2Building
from .hessen import HessenProvider
from .sources import Attribution

Bbox = tuple[float, float, float, float]  # (south, west, north, east), as project.query_bbox gives it


class Lod2Provider(Protocol):
    name: str
    attribution: Attribution

    def covers(self, bbox: Bbox) -> bool: ...

    def fetch(self, bbox: Bbox, cache_dir: Path, client: httpx.Client | None = None) -> list[Lod2Building]: ...


# The order is the resolution order, and it is fixed: the same square must reproducibly use the
# same source, otherwise two runs of the same spec could disagree about the heights (spec §4).
REGISTRY: tuple[Lod2Provider, ...] = (HessenProvider(),)


def select_provider(bbox: Bbox) -> Lod2Provider | None:
    """The first provider that covers the box, or None — then the run stays on OpenStreetMap."""
    for provider in REGISTRY:
        if provider.covers(bbox):
            return provider
    return None
