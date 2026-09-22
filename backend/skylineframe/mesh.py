"""Solid modelling with manifold3d. Plate top is z = 0; buildings rise above, recesses go below."""

from dataclasses import dataclass

import manifold3d as m3d
import numpy as np
import trimesh
from shapely.geometry import Polygon

from .errors import MeshError
from .roofs import roof_solid
from .scale import Prism, Scaled
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


def _is_solid(p: Prism) -> bool:
    return p.geom.area > 0 and p.height_mm > p.z0_mm


def _roof_body(p: Prism) -> m3d.Manifold | None:
    """The roof of one footprint, cut down to the footprint itself (spec §7).

    The clip prism reaches EPS below the eaves and EPS above the ridge: its bottom face must
    not be coplanar with the bottom face of the roof body, or the intersection has to resolve
    two coincident faces. The roof itself keeps the exact eaves plane roofs.py gives it; what
    removes the face contact with the wall below is the body, whose top _bodies raises by EPS
    into the roof.
    """
    if p.roof is None:
        return None
    height = p.roof.z_ridge_mm - p.roof.z_eaves_mm + 2 * EPS
    clip = prism(p.geom, height, z0=p.roof.z_eaves_mm - EPS)
    return roof_solid(
        p.roof.rect_mm,
        p.roof.z_eaves_mm,
        p.roof.z_ridge_mm,
        p.roof.shape,
        p.roof.direction_deg,
        clip=clip,
    )


def _roof_bodies(prisms: list[Prism]) -> list[m3d.Manifold]:
    """All roofs of a list of prisms. Built once and reused by the flush and the sunk union:
    a roof sits above the plate, so sinking the bodies never moves it."""
    roofs = []
    for p in prisms:
        if not _is_solid(p):
            continue
        roof = _roof_body(p)
        if roof is not None:
            roofs.append(roof)
    return roofs


def _bodies(prisms: list[Prism], sink: float) -> list[m3d.Manifold]:
    """Vertical bodies. `sink` pulls a footprint that stands on the plate below it.

    A body that carries a roof is raised by EPS into it: the roof starts exactly at the eaves,
    so without the overlap the two solids would only touch face to face at that plane and the
    union would have to resolve coincident faces. EPS is 0.01 mm, well below what a nozzle can
    resolve, and it never shows: the roof covers the footprint it stands on.
    """
    out: list[m3d.Manifold] = []
    for p in prisms:
        if not _is_solid(p):
            continue
        bottom = p.z0_mm - sink if p.z0_mm <= 0 else p.z0_mm
        top = p.height_mm + EPS if p.roof is not None else p.height_mm
        out.append(prism(p.geom, top - bottom, z0=bottom))
    return out


def build_meshes(scaled: Scaled, spec: FrameSpec) -> MeshSet:
    if not scaled.buildings and not scaled.blocks:
        raise MeshError("No buildings in the selected area.")

    bodies = _bodies(scaled.buildings, 0.0) + _bodies(scaled.blocks, 0.0)
    if not bodies:
        raise MeshError("No printable building footprints in the selected area.")
    # Roofs are hulls and boolean intersections — the two unions below share them instead of
    # building every roof twice. Blocks never carry a roof.
    roofs = _roof_bodies(scaled.buildings)

    base = plate(spec)
    # Exported part: flush on the plate top, so 3MF parts never overlap.
    buildings = _check(union(bodies + roofs), "buildings")
    # For the single-colour union we sink the buildings slightly so the boolean never relies on
    # a pure face contact at z = 0. Parts that start in the air keep their bottom.
    sunk_bodies = _bodies(scaled.buildings, BUILDING_SINK_MM) + _bodies(scaled.blocks, BUILDING_SINK_MM)
    buildings_sunk = union(sunk_bodies + roofs)

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
