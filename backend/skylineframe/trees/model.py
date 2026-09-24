"""The one interface between tree data and tree geometry (spec 6 §3).

Deliberately free of network, raster and mesh imports: the geometry half and its tests only ever
need these two classes.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class OsmTree:
    """A tree mapped in OpenStreetMap (natural=tree node, or one point of a tree_row), in the CRS of
    the pipeline stage: lon/lat after fetch, local metres after project_features. Tags it did not
    carry are None; the placement fills them with defaults."""

    x: float
    y: float
    height_m: float | None = None  # `height`
    crown_m: float | None = None  # `diameter_crown`


@dataclass(frozen=True)
class Tree:
    """One tree to print, in the centred print frame of every Prism (millimetres, x/y on the plate).

    height_mm is the top of the crown above the ground it stands on, already scaled and
    exaggerated with spec.z_exaggeration like a building; crown_mm is the crown diameter. Neither
    is clamped to anything printable here: that is the geometry's job (spec 6 §5).
    """

    x_mm: float
    y_mm: float
    crown_mm: float
    height_mm: float
