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
LEVEL_HEIGHT_M = 3.2  # metres per building level when only building:levels is tagged


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
    min_footprint_area_mm2: float = Field(default=1.0, ge=0)
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
