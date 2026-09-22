"""Solid modelling with manifold3d. Plate top is z = 0; buildings rise above, recesses go below."""

from dataclasses import dataclass

import manifold3d as m3d
import numpy as np
import trimesh
from shapely.geometry import Polygon

from .errors import MeshError
from .scale import Scaled
from .spec import FrameSpec

EPS = 0.01  # overshoot of cutters above the plate top for a clean boolean cut
BUILDING_SINK_MM = 0.2  # buildings are sunk into the plate so the union never relies on face contact only


@dataclass
class MeshSet:
    base: m3d.Manifold
    buildings: m3d.Manifold
    single: m3d.Manifold
    water: m3d.Manifold | None = None
    roads: m3d.Manifold | None = None

    def parts(self) -> dict[str, m3d.Manifold]:
        parts = {"base": self.base, "buildings": self.buildings}
        if self.water is not None:
            parts["water"] = self.water
        if self.roads is not None:
            parts["roads"] = self.roads
        return parts


def cross_section(poly: Polygon) -> m3d.CrossSection:
    rings = [list(poly.exterior.coords)[:-1]] + [list(ring.coords)[:-1] for ring in poly.interiors]
    rings = [ring for ring in rings if len(set(ring)) >= 3]  # drop degenerate rings
    return m3d.CrossSection(rings, m3d.FillRule.EvenOdd)


def prism(poly: Polygon, height: float, z0: float = 0.0) -> m3d.Manifold:
    return cross_section(poly).extrude(height).translate((0, 0, z0))


def union(parts: list[m3d.Manifold]) -> m3d.Manifold:
    if len(parts) == 1:
        return parts[0]
    return m3d.Manifold.batch_boolean(parts, m3d.OpType.Add)


def _check(man: m3d.Manifold, name: str) -> m3d.Manifold:
    if man.status() != m3d.Error.NoError:
        raise MeshError(f"{name}: manifold error {man.status()}")
    if man.is_empty() or man.volume() <= 0:
        raise MeshError(f"{name}: resulting solid is empty")
    return man


def plate(spec: FrameSpec) -> m3d.Manifold:
    size, t = spec.plate_size_mm, spec.plate_thickness_mm
    return m3d.Manifold.cube((size, size, t), center=True).translate((0, 0, -t / 2))


def recess(polys: list[Polygon], depth: float) -> tuple[m3d.Manifold, m3d.Manifold] | None:
    """Return (cutter, inlay), or None when nothing printable is left.

    The cutter overshoots above z=0; the inlay fills the recess exactly. Zero-area polygons
    cannot be extruded at all, so they are dropped; a part made only of those is simply absent
    rather than a hard failure.
    """
    polys = [p for p in polys if p.area > 0]
    if not polys:
        return None
    cutter = union([prism(p, depth + EPS, z0=-depth) for p in polys])
    inlay = union([prism(p, depth, z0=-depth) for p in polys])
    return cutter, inlay


def build_meshes(scaled: Scaled, spec: FrameSpec) -> MeshSet:
    if not scaled.buildings:
        raise MeshError("No buildings in the selected area.")

    footprints = [b for b in scaled.buildings if b.geom.area > 0]
    if not footprints:
        raise MeshError("No printable building footprints in the selected area.")

    base = plate(spec)
    # Exported part: flush on the plate top, so 3MF parts never overlap.
    buildings = _check(union([prism(b.geom, b.height_mm) for b in footprints]), "buildings")
    # For the single-colour union we sink the buildings slightly so the boolean never relies on
    # a pure face contact at z = 0.
    buildings_sunk = union([prism(b.geom, b.height_mm + BUILDING_SINK_MM, z0=-BUILDING_SINK_MM) for b in footprints])

    water = roads = None
    cut = recess(scaled.water, spec.water_depth_mm) if scaled.water else None
    if cut is not None:
        cutter, water = cut
        base = base - cutter
        _check(water, "water")
    cut = recess(scaled.roads, spec.road_depth_mm) if scaled.roads else None
    if cut is not None:
        cutter, roads = cut
        base = base - cutter
        _check(roads, "roads")
    _check(base, "base")

    single = _check(base + buildings_sunk, "single")
    return MeshSet(base=base, buildings=buildings, single=single, water=water, roads=roads)


def to_trimesh(man: m3d.Manifold) -> trimesh.Trimesh:
    mesh = man.to_mesh()
    vertices = np.asarray(mesh.vert_properties)[:, :3]
    faces = np.asarray(mesh.tri_verts)
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
