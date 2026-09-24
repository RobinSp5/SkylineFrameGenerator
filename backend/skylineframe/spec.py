"""Parameters of one frame generation run."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Mode(StrEnum):
    simple = "simple"  # plate + buildings
    full = "full"  # plate + buildings + road grooves + water recesses


ROAD_CLASSES: tuple[str, ...] = (
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "residential",
    "unclassified",
    "living_street",
    "pedestrian",
    "service",
)

DEFAULT_ROAD_WIDTH_MM: dict[str, float] = {
    "motorway": 2.0,
    "trunk": 2.0,
    "primary": 1.6,
    "secondary": 1.4,
    "tertiary": 1.2,
    "residential": 1.0,
    "unclassified": 1.0,
    "living_street": 1.0,
    "pedestrian": 0.8,
    "service": 0.8,
}

MIN_FEATURE_MM = 0.8  # two nozzle widths on a 0.4 mm nozzle
# One nozzle line: the narrowest wall a 0.4 mm nozzle still lays down. A footprint thinner than
# this is widened to it rather than buried in a block (spec 4a §2.2).
MIN_LINE_MM = 0.4
# Fixed print height of a block. The block is only a ground plate under the houses now, and
# every house is at least min_building_height_mm tall, so it rises 0.4 mm above it (spec 4a §2.3).
SOCKEL_MM = 0.4
# Below this a footprint (a ~4.5 m² shed at 1:15 000) only feeds its block and gets no body of
# its own (spec 4a §2.1). It also keeps out the corner slivers an LoD2 model leaves of the OSM
# outline it replaces: 4 443 of them sit under 5 m² in the Eppstein square.
TINY_FOOTPRINT_MM2 = 0.02
LEVEL_HEIGHT_M = 3.2  # metres per building level when only building:levels is tagged


class Preset(StrEnum):
    skyline = "skyline"
    detail = "detail"
    gross = "gross"


# Preset name -> (side_m, plate_size_mm); mirrored by frontend/src/presets.ts (spec §9).
PRESETS: dict[str, tuple[float, float]] = {
    Preset.skyline: (1500.0, 100.0),
    Preset.detail: (800.0, 100.0),
    Preset.gross: (1500.0, 200.0),
}


class FrameSpec(BaseModel):
    # The spec is parsed straight from the request body: an unknown key is a client-side typo
    # or a stale field name, and silently dropping it would generate the wrong model.
    model_config = ConfigDict(extra="forbid")

    center_lat: float = Field(ge=-85, le=85)
    center_lon: float = Field(ge=-180, le=180)
    side_m: float = Field(default=1500, ge=200, le=5000)
    rotation_deg: float = Field(default=0, ge=-180, le=180)
    plate_size_mm: float = Field(default=100, ge=40, le=250)
    plate_thickness_mm: float = Field(default=3.0, ge=1.0, le=10.0)
    mode: Mode = Mode.simple
    z_exaggeration: float = Field(default=1.5, gt=0, le=10)
    default_building_height_m: float = Field(default=8.0, gt=0)
    min_building_height_mm: float = Field(default=0.8, ge=0)
    # Smallest printable sockel polygon. It no longer decides which footprint gets a body of its
    # own — every footprint of at least TINY_FOOTPRINT_MM2 does — and stays a field so existing
    # API clients keep working (spec 4a §2.1).
    min_footprint_area_mm2: float = Field(default=0.25, ge=0)
    roofs: bool = True  # build roof solids from roof:shape (spec §7)
    parts: bool = True  # render building:part instead of one box per outline (spec §6.3)
    # Use an official LoD2 model where one is available; without a provider, without network or
    # with lod2=False the result is bit-identical to the OSM-only run (spec §8/§11).
    lod2: bool = True
    # Terrain relief from the Copernicus DEM (spec 4b §2). Off by default, and off is the exact
    # flat-plate code path of before. Its own exaggeration: a hill wants a different factor than a
    # tower, and z_exaggeration already means "buildings" to every existing client.
    terrain: bool = False
    terrain_exaggeration: float = Field(default=1.0, gt=0, le=5)
    road_depth_mm: float = Field(default=0.4, gt=0)
    water_depth_mm: float = Field(default=0.6, gt=0)
    road_width_mm: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_ROAD_WIDTH_MM))

    @field_validator("road_width_mm")
    @classmethod
    def _validate_road_widths(cls, value: dict[str, float]) -> dict[str, float]:
        unknown = set(value) - set(ROAD_CLASSES)
        if unknown:
            raise ValueError(f"unknown road classes: {sorted(unknown)}")
        too_thin = {k: w for k, w in value.items() if w < MIN_FEATURE_MM}
        if too_thin:
            raise ValueError(f"road widths below {MIN_FEATURE_MM} mm are not printable: {too_thin}")
        return {**DEFAULT_ROAD_WIDTH_MM, **value}

    @model_validator(mode="after")
    def _recesses_fit_plate(self) -> "FrameSpec":
        for name in ("road_depth_mm", "water_depth_mm"):
            if getattr(self, name) >= self.plate_thickness_mm:
                raise ValueError(f"{name} must be smaller than plate_thickness_mm")
        return self

    @property
    def scale(self) -> float:
        """Millimetres of print per metre of city."""
        return self.plate_size_mm / self.side_m
