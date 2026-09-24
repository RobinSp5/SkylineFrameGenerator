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
    neighbours = {
        k: [
            int(o)
            for o in tree.query(p.geom, predicate="intersects")
            if o != k and prisms[o].geom.intersection(p.geom).area > 0
        ]
        for k, p in enumerate(prisms)
        if p.z0_mm > 0 and p.geom.area > 0
    }
    # A part can stand on another raised part, which was itself lowered onto the body below it.
    # The lowering has to travel up the stack, so the minimum runs over the already lowered bases
    # until nothing changes. Bases only ever decrease, so this ends; a stack is never deeper than
    # the number of parts.
    bases = list(own)
    for _ in range(len(prisms)):
        changed = False
        for k, others in neighbours.items():
            lowest = min((bases[o] for o in others), default=bases[k])
            if lowest < bases[k]:
                bases[k] = lowest
                changed = True
        if not changed:
            break
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


# --- export precision ---------------------------------------------------------------------------

# An STL has no vertex indices: a slicer welds its corners by position, in float32. manifold3d
# works in double precision and keeps two solids that touch in an edge or a point apart with
# coincident but distinct vertices. Both break once welded: an edge shorter than float32 resolves
# collapses its faces, and a pinch becomes an edge with four faces. Measured on Eppstein, 98 % of
# the problem came from the LoD2 bodies themselves (sub-micron edges where the footprint cut
# passes a roof vertex), the rest from buildings touching each other.
PRINT_GRID_MM = 1e-3  # 1 µm; three orders of magnitude below a nozzle, far above float32 steps
# A quarter of the grid: a separated copy can never come near a vertex on another grid point.
PINCH_SEPARATION_MM = PRINT_GRID_MM / 4
MAX_SEPARATION_PASSES = 8
_LINK = ((0, 1), (1, 2), (2, 0), (1, 0), (2, 1), (0, 2))


def _from_arrays(verts: np.ndarray, tris: np.ndarray) -> m3d.Manifold:
    # Copies on purpose: manifold3d only takes writable, owned arrays.
    return m3d.Manifold(
        m3d.Mesh64(vert_properties=np.array(verts, dtype=np.float64), tri_verts=np.array(tris, dtype=np.uint64))
    )


def _arrays(man: m3d.Manifold) -> tuple[np.ndarray, np.ndarray]:
    mesh = man.to_mesh64()
    return np.array(mesh.vert_properties)[:, :3], np.asarray(mesh.tri_verts).astype(np.int64)


def _separated(verts: np.ndarray, tris: np.ndarray, group: np.ndarray, size: np.ndarray) -> np.ndarray:
    """Move every copy of a shared position a hair into its own solid, away from the others.

    Each copy moves towards the centroid of its own ring of neighbours, which lies on its own side
    of the pinch. Taking out the mean direction of all copies at that position makes them move
    apart, not in parallel: two boxes of equal height that touch along an edge would otherwise
    both have their corners pulled down the same way by the roof.
    """
    ring = np.zeros_like(verts)
    degree = np.zeros(len(verts))
    for a, b in _LINK:
        np.add.at(ring, tris[:, a], verts[tris[:, b]])
        np.add.at(degree, tris[:, a], 1.0)
    own = ring / np.maximum(degree, 1.0)[:, None] - verts
    own /= np.maximum(np.linalg.norm(own, axis=1), 1e-300)[:, None]
    mean = np.zeros((int(group.max()) + 1, 3))
    np.add.at(mean, group, own)
    apart = own - mean[group] / size[group][:, None]
    length = np.linalg.norm(apart, axis=1)
    # When the copies' own directions nearly agree, the difference is noise; the own direction
    # still separates them, and the loop in printable() checks that it did.
    direction = np.where((length > 0.1)[:, None], apart / np.maximum(length, 1e-300)[:, None], own)
    shared = size[group] > 1
    out = verts.copy()
    out[shared] += PINCH_SEPARATION_MM * direction[shared]
    return out


# A separate shell below this is an artefact, not a feature. The smallest real one, a building
# widened to one 0.4 mm line and 0.8 mm tall, holds about 0.13 mm³; the slivers the booleans leave
# measure up to 0.002 mm³ (a 0.12 mm needle in Frankfurt), so the cut sits well between the two.
MIN_SHELL_VOLUME_MM3 = 0.05


def printable(man: m3d.Manifold) -> m3d.Manifold:
    """The solid as a slicer will see it: 2-manifold once its vertices are welded by position.

    1. Every vertex is snapped to a PRINT_GRID_MM grid, and manifold3d rebuilds the solid: the
       rebuild collapses the edges that became zero length. This is what removes the sub-micron
       edges, which the feature-preserving simplify() refuses to touch.
    2. Every position still held by more than one vertex is a pinch — solids touching in an edge
       or a point. Its copies are moved PINCH_SEPARATION_MM apart (_separated).

    The rebuild can itself split a vertex whose fan became two after a collapse, which creates a
    new pinch, so both steps repeat until a rebuild leaves no shared position. Positions are
    compared in float32 because that is what the STL stores. The shape moves by at most a micron:
    bounding box and volume stay the same to well below what a nozzle resolves.
    """
    verts, tris = _arrays(man)
    verts = np.round(verts / PRINT_GRID_MM) * PRINT_GRID_MM
    result = man
    for _ in range(MAX_SEPARATION_PASSES):
        # Slivers inside the loop: the snap and every rebuild can leave new ones, and taking them
        # out can put two remaining shells back onto one position, which the check below catches.
        result = _without_slivers(_from_arrays(verts, tris))
        verts, tris = _arrays(result)
        _, group, size = np.unique(verts.astype(np.float32), axis=0, return_inverse=True, return_counts=True)
        group = group.ravel()
        if (size == 1).all():
            break
        verts = _separated(verts, tris, group, size)
    return _check(result, "printable")


def _without_slivers(man: m3d.Manifold) -> m3d.Manifold:
    """Drop the shells that hold no printable material.

    The booleans leave a few dozen closed shells of practically zero volume behind, flat pockets
    where two coplanar faces nearly met (Eppstein with terrain: 51, none above 0.0007 mm³). No
    nozzle can lay them down, but a slicer counts each one as a separate part of the model.
    """
    shells = man.decompose()
    if len(shells) <= 1:
        return man
    kept = [shell for shell in shells if shell.volume() >= MIN_SHELL_VOLUME_MM3]
    if not kept or len(kept) == len(shells):
        return man
    return m3d.Manifold.compose(kept)


def to_trimesh(man: m3d.Manifold) -> trimesh.Trimesh:
    mesh = man.to_mesh()
    vertices = np.asarray(mesh.vert_properties)[:, :3]
    faces = np.asarray(mesh.tri_verts)
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
