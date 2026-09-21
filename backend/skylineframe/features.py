"""Plain containers for OSM features; geometry CRS depends on the pipeline stage."""

from dataclasses import dataclass, field

from shapely.geometry.base import BaseGeometry


@dataclass
class Building:
    geom: BaseGeometry  # Polygon or MultiPolygon
    height_m: float


@dataclass
class Road:
    geom: BaseGeometry  # LineString or MultiLineString
    cls: str  # one of spec.ROAD_CLASSES


@dataclass
class Water:
    geom: BaseGeometry  # Polygon or MultiPolygon


@dataclass
class Features:
    buildings: list[Building] = field(default_factory=list)
    roads: list[Road] = field(default_factory=list)
    water: list[Water] = field(default_factory=list)
