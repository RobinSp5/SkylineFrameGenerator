"""Solid modelling with manifold3d. Plate top is z = 0; buildings rise above, recesses go below.

With terrain (spec 4b §5) the plate top is the relief instead, lowest point at z = 0, and every
building, sockel and groove is placed relative to it.
"""

from collections.abc import Callable
from dataclasses import dataclass

import manifold3d as m3d
import numpy as np
import shapely
import trimesh
from shapely.geometry import Polygon

from .errors import MeshError
from .roofs import roof_solid
from .scale import Prism, Scaled
from .spec import FrameSpec
from .terrain.heightfield import Heightfield

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
    a roof sits above the plate, so sinking the bodies never moves it. A LoD2 building has no
    ScaledRoof at all — its roof shape is already inside its body."""
    roofs = []
    for p in prisms:
        if p.solid_mm is not None or not _is_solid(p):
            continue
        roof = _roof_body(p)
        if roof is not None:
            roofs.append(roof)
    return roofs


def _bodies(prisms: list[Prism], sink: float) -> list[m3d.Manifold]:
    """Vertical bodies. `sink` pulls a footprint that stands on the plate below it.

    A LoD2 body is finished geometry and is only moved: sinking it costs 0.2 mm of ridge height
    in the single-colour union, two orders of magnitude below what a nozzle resolves, and it
    saves a boolean per building compared with welding a skirt underneath.

    A body that carries a roof is raised by EPS into it: the roof starts exactly at the eaves,
    so without the overlap the two solids would only touch face to face at that plane and the
    union would have to resolve coincident faces. EPS is 0.01 mm, well below what a nozzle can
    resolve, and it never shows: the roof covers the footprint it stands on.
    """
    out: list[m3d.Manifold] = []
    for p in prisms:
        if p.solid_mm is not None:
            out.append(p.solid_mm.translate((0.0, 0.0, -sink)) if sink else p.solid_mm)
            continue
        if not _is_solid(p):
            continue
        bottom = p.z0_mm - sink if p.z0_mm <= 0 else p.z0_mm
        top = p.height_mm + EPS if p.roof is not None else p.height_mm
        out.append(prism(p.geom, top - bottom, z0=bottom))
    return out


def build_meshes(scaled: Scaled, spec: FrameSpec, terrain: Heightfield | None = None) -> MeshSet:
    if not scaled.buildings and not scaled.blocks:
        raise MeshError("No buildings in the selected area.")
    # Without terrain nothing below this line changes: the flat plate stays bit-identical to the
    # model before phase 4b (spec 4b §2), so the relief lives in a path of its own.
    if terrain is not None:
        return _build_on_terrain(scaled, spec, terrain)

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


# --- terrain (spec 4b §5) ---------------------------------------------------------------------

Surface = np.ndarray | float  # a height per grid node, or one constant height


def _surface(hf: Heightfield, z: Surface, facing_up: bool, offset: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One horizontal side of a grid solid: (vertices, triangles, perimeter ring).

    A grid surface gets two triangles per cell. A constant one only needs its perimeter, and a
    fan from its centre closes it: the flat bottom of a relief plate would otherwise carry as
    many triangles as its top for nothing. The ring runs counter-clockwise seen from above and
    has one vertex per grid node on the plate edge either way, so the walls between two sides
    always pair up vertex for vertex.
    """
    ny, nx = hf.z_mm.shape
    xs = hf.origin_mm[0] + np.arange(nx) * hf.cell_mm
    ys = hf.origin_mm[1] + np.arange(ny) * hf.cell_mm
    # Perimeter grid nodes (j, i), counter-clockwise from the south-west corner, no repeats.
    ring_j = np.concatenate([np.zeros(nx - 1, int), np.arange(ny - 1), np.full(nx - 1, ny - 1), np.arange(ny - 1, 0, -1)])
    ring_i = np.concatenate([np.arange(nx - 1), np.full(ny - 1, nx - 1), np.arange(nx - 1, 0, -1), np.zeros(ny - 1, int)])
    if isinstance(z, np.ndarray):
        gx, gy = np.meshgrid(xs, ys)
        verts = np.column_stack([gx.ravel(), gy.ravel(), z.ravel()])
        node = np.arange(ny * nx).reshape(ny, nx)
        a, b = node[:-1, :-1].ravel(), node[:-1, 1:].ravel()
        c, d = node[1:, 1:].ravel(), node[1:, :-1].ravel()
        tris = np.concatenate([np.column_stack([a, b, c]), np.column_stack([a, c, d])])
        ring = node[ring_j, ring_i]
    else:
        n = len(ring_j)
        verts = np.column_stack([xs[ring_i], ys[ring_j], np.full(n, float(z))])
        verts = np.vstack([verts, [xs.mean(), ys.mean(), float(z)]])
        k = np.arange(n)
        tris = np.column_stack([np.full(n, n), k, (k + 1) % n])
        ring = k
    if not facing_up:
        tris = tris[:, ::-1]
    return verts, tris + offset, ring + offset


def _grid_solid(hf: Heightfield, top: Surface, bottom: Surface) -> m3d.Manifold:
    """The closed solid between two surfaces over the whole plate square, with vertical walls.

    Built from arrays rather than booleans: a relief plate is one mesh of a few hundred thousand
    triangles, and every layer below (sockel, groove, inlay) is the same grid shifted, so it
    costs no more than the plate itself. Callers keep top above bottom everywhere.
    """
    top_v, top_t, top_ring = _surface(hf, top, facing_up=True, offset=0)
    bot_v, bot_t, bot_ring = _surface(hf, bottom, facing_up=False, offset=len(top_v))
    # Walls: the rings run counter-clockwise, so (b_k, b_k+1, t_k+1) faces outwards.
    t0, t1 = top_ring, np.roll(top_ring, -1)
    b0, b1 = bot_ring, np.roll(bot_ring, -1)
    walls = np.concatenate([np.column_stack([b0, b1, t1]), np.column_stack([b0, t1, t0])])
    mesh = m3d.Mesh(
        vert_properties=np.ascontiguousarray(np.vstack([top_v, bot_v]), dtype=np.float32),
        tri_verts=np.ascontiguousarray(np.concatenate([top_t, bot_t, walls]), dtype=np.uint32),
    )
    return m3d.Manifold(mesh)


def terrain_solid(hf: Heightfield, thickness_mm: float) -> m3d.Manifold:
    """The relief plate: the terrain on top, a flat bottom at -thickness (spec 4b §5.1).

    Its lowest node is 0, so on a flat field this is exactly today's plate.
    """
    return _grid_solid(hf, hf.z_mm, -thickness_mm)


def footprint_base(hf: Heightfield, poly: Polygon) -> float:
    """The lowest terrain under a footprint: its z_base (spec 4b §5.2).

    Sampled at every outline vertex and at every grid node inside the outline. The vertices
    alone would miss a hollow in the middle of a large footprint, and the building would float
    over it.
    """
    rings = [poly.exterior, *poly.interiors]
    coords = np.concatenate([np.asarray(r.coords) for r in rings])
    lowest = float(np.min(hf.sample(coords[:, 0], coords[:, 1])))
    ny, nx = hf.z_mm.shape
    x0, y0, x1, y1 = poly.bounds
    i0 = max(int(np.ceil((x0 - hf.origin_mm[0]) / hf.cell_mm)), 0)
    i1 = min(int(np.floor((x1 - hf.origin_mm[0]) / hf.cell_mm)), nx - 1)
    j0 = max(int(np.ceil((y0 - hf.origin_mm[1]) / hf.cell_mm)), 0)
    j1 = min(int(np.floor((y1 - hf.origin_mm[1]) / hf.cell_mm)), ny - 1)
    if i0 <= i1 and j0 <= j1:
        gx, gy = np.meshgrid(
            hf.origin_mm[0] + np.arange(i0, i1 + 1) * hf.cell_mm,
            hf.origin_mm[1] + np.arange(j0, j1 + 1) * hf.cell_mm,
        )
        inside = shapely.contains_xy(poly, gx, gy)
        if inside.any():
            lowest = min(lowest, float(hf.z_mm[j0 : j1 + 1, i0 : i1 + 1][inside].min()))
    return lowest


def _footprint_bases(prisms: list[Prism], hf: Heightfield) -> list[float]:
    """z_base per prism. A part that starts in the air takes the lowest base of the footprints it
    overlaps, not only its own (spec 4b §5.2).

    Its z0 is measured from the ground of its building, and the body carrying it is lifted by
    that body's own z_base. A tower on the uphill half of a wide base has a higher minimum under
    its own footprint than the base has, so on its own base it would start above the roof it is
    meant to stand on and float. Taking the minimum of everything it overlaps can only lower it
    into the body below, never lift it off.
    """
    own = [footprint_base(hf, p.geom) if p.geom.area > 0 else 0.0 for p in prisms]
    if not any(p.z0_mm > 0 for p in prisms):
        return own
    tree = shapely.STRtree([p.geom for p in prisms])
    bases = list(own)
    for k, p in enumerate(prisms):
        if p.z0_mm <= 0 or p.geom.area <= 0:
            continue
        for other in tree.query(p.geom, predicate="intersects"):
            if other != k and prisms[other].geom.intersection(p.geom).area > 0:
                bases[k] = min(bases[k], own[other])
    return bases


def _lifted(
    prisms: list[Prism], bases: list[float], build: Callable[[list[Prism]], list[m3d.Manifold]]
) -> list[m3d.Manifold]:
    """Run a flat-path builder per prism and lift what it returns by that prism's z_base, so a
    building on terrain is exactly the flat one, moved up (spec 4b §5.2)."""
    return [
        solid.translate((0.0, 0.0, z_base))
        for p, z_base in zip(prisms, bases, strict=True)
        for solid in build([p])
    ]


def _columns(polys: list[Polygon], hf: Heightfield, spec: FrameSpec, extra_mm: float) -> m3d.Manifold:
    """Prisms over the polygons, tall enough to cut through the plate and every layer above it."""
    z0 = -spec.plate_thickness_mm - 1.0
    height = float(hf.z_mm.max()) + extra_mm + 1.0 - z0
    return union([prism(p, height, z0=z0) for p in polys])


def _sockel_on(blocks: list[Prism], hf: Heightfield, spec: FrameSpec, sink: float) -> m3d.Manifold | None:
    """The sockel follows the surface: block footprint ∩ the layer from surface - sink up to
    surface + its height (spec 4b §5.3). One layer per distinct height, one boolean each."""
    by_height: dict[float, list[Polygon]] = {}
    for b in blocks:
        if _is_solid(b):
            by_height.setdefault(b.height_mm, []).append(b.geom)
    layers = [
        _columns(polys, hf, spec, height) ^ _grid_solid(hf, hf.z_mm + height, hf.z_mm - sink)
        for height, polys in by_height.items()
    ]
    return union(layers) if layers else None


def _recess_on(
    polys: list[Polygon], depth: float, hf: Heightfield, spec: FrameSpec
) -> tuple[m3d.Manifold, m3d.Manifold] | None:
    """recess() on the relief: the groove keeps its depth below the surface (spec 4b §5.4).

    Cutter = columns ∩ everything above surface - depth; inlay = columns ∩ the layer between
    surface - depth and the surface, so base + inlay is the uncut relief plate again.
    """
    polys = [p for p in polys if p.area > 0]
    if not polys:
        return None
    columns = _columns(polys, hf, spec, EPS)
    below = hf.z_mm - depth
    cutter = columns ^ _grid_solid(hf, float(hf.z_mm.max()) + EPS, below)
    inlay = columns ^ _grid_solid(hf, hf.z_mm, below)
    return cutter, inlay


def _build_on_terrain(scaled: Scaled, spec: FrameSpec, hf: Heightfield) -> MeshSet:
    """build_meshes on a relief plate (spec 4b §5).

    On a slope a flush bottom does not exist — the uphill side of a house stands in the hill —
    so the exported buildings part is its solids minus the relief plate. That is what keeps the
    3MF parts from overlapping, as building them flush does on the flat plate. Prisms and the
    sockel are therefore built sunk only once: sinking moves nothing but their bottom, and the
    subtraction cuts that off again. A LoD2 body sinks as a whole, so the exported part gets it
    unsunk, with its full ridge height, exactly as on the flat plate.
    """
    ground = _check(terrain_solid(hf, spec.plate_thickness_mm), "base")
    prisms = scaled.buildings
    bases = _footprint_bases(prisms, hf)
    lod2 = [k for k, p in enumerate(prisms) if p.solid_mm is not None]
    plain = [k for k, p in enumerate(prisms) if p.solid_mm is None]

    def lifted(idx: list[int], build: Callable[[list[Prism]], list[m3d.Manifold]]) -> list[m3d.Manifold]:
        return _lifted([prisms[k] for k in idx], [bases[k] for k in idx], build)

    shared = lifted(plain, lambda ps: _bodies(ps, BUILDING_SINK_MM)) + lifted(plain, _roof_bodies)
    sockel = _sockel_on(scaled.blocks, hf, spec, BUILDING_SINK_MM)
    if sockel is not None:
        shared.append(sockel)
    lod2_sunk = lifted(lod2, lambda ps: _bodies(ps, BUILDING_SINK_MM))
    if not shared and not lod2_sunk:
        raise MeshError("No printable building footprints in the selected area.")
    lod2_flush = lifted(lod2, lambda ps: _bodies(ps, 0.0))
    buildings = _check(union(shared + lod2_flush) - ground, "buildings")
    buildings_sunk = union(shared + lod2_sunk)

    base = ground
    water = roads = None
    cut = _recess_on(scaled.water, spec.water_depth_mm, hf, spec) if scaled.water else None
    if cut is not None:
        cutter, water = cut
        base = base - cutter
        _check(water, "water")
    cut = _recess_on(scaled.roads, spec.road_depth_mm, hf, spec) if scaled.roads else None
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
