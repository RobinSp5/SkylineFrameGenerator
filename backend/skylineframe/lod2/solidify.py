"""LoD2 surface model -> watertight solid (spec §5). Local metres in, local metres out.

Official LoD2 data is a surface model, not a body, and assembling the faces into a Manifold does
not work: the Schirn Kunsthalle (137 faces) comes out as Error.NotManifold with 8 open edges and
65 edges shared by three or more faces, because wall and roof faces are subdivided differently
and meet in T-joints. So nothing is repaired — the body is cut:

    footprint prism  ∩  (roof faces + a skirt down below the ground)

Both operands are closed by construction, the cut is what produces the walls, and the T-joints
never matter because no wall face is ever used.
"""

import math
from collections.abc import Sequence

import manifold3d as m3d
import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid

Point3 = tuple[float, float, float]
Surfaces = Sequence[Sequence[Point3]]

GROUND_DROP_M = 1.0  # how far the skirt and the prism reach below the lowest vertex
UP_NORMAL_MIN = 0.05  # a face counts as "roof" above this z component of its unit normal
CLEAN_BUFFER_M = 0.05  # dilate/erode that closes the seams between the projected faces
MIN_RING_POINTS = 3
SOLID_SIMPLIFY_MM = 0.2  # spec §5; applied after scaling, by scale.py
SIMPLIFY_MIN_VOLUME = 0.8  # a simplified body is only kept if it holds this share of the volume


def faces_of(surfaces: Surfaces) -> list[list[Point3]]:
    """The usable faces: closing vertex dropped, rings below three distinct points removed."""
    faces: list[list[Point3]] = []
    for ring in surfaces:
        # A point that is not a triple is skipped rather than raised on: a bulk run over a whole
        # square must be able to count a malformed model as discarded, not stop on it (spec §5.5).
        points = [(float(p[0]), float(p[1]), float(p[2])) for p in ring if len(p) == 3]
        if len(points) > 1 and points[0] == points[-1]:
            points = points[:-1]
        # Rounded to the millimetre: in local metres a ring whose points differ by microns is a
        # numerical artefact, and a fan over it would only add degenerate triangles.
        if len({(round(x, 3), round(y, 3), round(z, 3)) for x, y, z in points}) < MIN_RING_POINTS:
            continue
        faces.append(points)
    return faces


def _normal(face: Sequence[Point3]) -> tuple[float, float, float] | None:
    """Unit normal by Newell's method; None for a face with no area.

    Newell rather than a cross product of the first three points: LoD2 faces are not always
    convex and the first three points are regularly collinear.
    """
    nx = ny = nz = 0.0
    count = len(face)
    for i in range(count):
        x0, y0, z0 = face[i]
        x1, y1, z1 = face[(i + 1) % count]
        nx += (y0 - y1) * (z0 + z1)
        ny += (z0 - z1) * (x0 + x1)
        nz += (x0 - x1) * (y0 + y1)
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length == 0.0:
        return None
    return nx / length, ny / length, nz / length


def footprint_of_faces(faces: list[list[Point3]]) -> Polygon | None:
    """The ground plan: every face projected to 2D, unioned, cleaned, largest part (spec §5.2).

    The buffer pair closes the hairline seams the projection leaves between faces that meet at an
    edge; without it the union falls apart into slivers and the largest part is a single wall.

    Takes faces rather than raw rings so that a caller who also needs the height range walks the
    rings once. At 114 672 polygons per square that is the difference between one pass and three.
    """
    polygons = []
    for face in faces:
        ring = [(x, y) for x, y, _ in face]
        if len(set(ring)) < MIN_RING_POINTS:
            continue  # a vertical wall projects to a line
        repaired = make_valid(Polygon(ring))
        # make_valid returns a MultiLineString for three collinear points; only areal parts are
        # a footprint, and anything else would just be carried through the union as a lie.
        if repaired.geom_type in ("Polygon", "MultiPolygon") and repaired.area > 0:
            polygons.append(repaired)
    if not polygons:
        return None
    merged = unary_union(polygons).buffer(CLEAN_BUFFER_M).buffer(-CLEAN_BUFFER_M)
    parts = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]
    parts = [p for p in parts if p.geom_type == "Polygon" and p.area > 0]
    return max(parts, key=lambda p: p.area) if parts else None


def footprint_of(surfaces: Surfaces) -> Polygon | None:
    """footprint_of_faces for raw rings."""
    return footprint_of_faces(faces_of(surfaces))


def height_of_faces(faces: list[list[Point3]]) -> tuple[float, float] | None:
    """(ground, ridge) in metres above sea level (spec §5.6)."""
    zs = [z for face in faces for _, _, z in face]
    return (min(zs), max(zs)) if zs else None


def height_range(surfaces: Surfaces) -> tuple[float, float] | None:
    """height_of_faces for raw rings."""
    return height_of_faces(faces_of(surfaces))


def _tent(face: list[Point3], z_base: float) -> m3d.Manifold | None:
    """One closed tent: everything above z_base, inside the face's outline and below its plane.

    Built as the prism of the face's plan cut by the face's plane, never by triangulating the
    face. A fan from the first vertex is only right for a convex face, and half of all LoD2 roof
    faces are not (Darmstadt: 6 197 of 13 361): an L-shaped roof fanned from next to its inner
    corner covers the notch of the L with a triangle turned inside out, and the tents came out
    mangled (a 7 m³ test building measured 3 m³, and real bodies grew wider upwards, which Bambu
    Studio reports as floating regions). The cut is exact for any simple polygon, holes included.

    The plane goes through the mean of the vertices along the Newell normal, so a face that is
    not quite planar, as surveyed faces rarely are, is cut by its best-fitting plane.
    """
    normal = _normal(face)
    if normal is None or normal[2] <= 0.0:
        return None
    plan = _plan(face)
    if plan is None:
        return None
    nx, ny, nz = normal
    offset = sum(nx * x + ny * y + nz * z for x, y, z in face) / len(face)
    top = max(z for _, _, z in face)
    prism = _cross_section(plan).extrude(top - z_base + 1.0).translate((0.0, 0.0, z_base))
    # trim_by_plane keeps the side the normal points to: with the normal flipped, what stays is
    # n · p <= offset, the space below the roof face.
    tent = prism.trim_by_plane((-nx, -ny, -nz), -offset)
    return tent if tent.status() == m3d.Error.NoError and not tent.is_empty() else None


def _plan(face: list[Point3]) -> Polygon | None:
    """The face seen from above, repaired; None when nothing with area is left."""
    ring = [(x, y) for x, y, _ in face]
    if len(set(ring)) < MIN_RING_POINTS:
        return None
    plan = Polygon(ring)
    if not plan.is_valid:
        repaired = make_valid(plan)
        parts = [g for g in getattr(repaired, "geoms", [repaired]) if isinstance(g, Polygon) and g.area > 0]
        if not parts:
            return None
        plan = max(parts, key=lambda g: g.area)
    return plan if plan.area > 0 else None


def _roof_body(faces: list[list[Point3]], z_base: float) -> m3d.Manifold | None:
    """The union of one closed tent per upward face.

    The tents must be *unioned*, not concatenated: roof faces of a real building overlap in plan
    (a dormer sits on top of the main roof), and a concatenated mesh counts the overlap twice —
    measured, a 2x2 m building with a 0.5x0.5 m dormer came out as 8.75 m³ instead of 8.25 m³.
    Even without overlap a concatenation is not one body but a heap of shells glued at coincident
    interior walls (the Paulskirche: genus -81, 82 shells), and volume() is load-bearing for the
    validity check, for the simplify guard and for the statistics. The union also pays for itself
    in triangles, because the interior walls disappear.
    """
    tents = []
    for face in faces:
        normal = _normal(face)
        if normal is None or normal[2] <= UP_NORMAL_MIN:
            continue
        tent = _tent(face, z_base)
        if tent is not None:
            tents.append(tent)
    if not tents:
        return None
    # batch_boolean rather than a fold of `+`: one pass over all tents instead of n-1 pairwise
    # booleans, each of which would re-walk the whole accumulated body.
    return m3d.Manifold.batch_boolean(tents, m3d.OpType.Add)


def _cross_section(poly: Polygon) -> m3d.CrossSection:
    rings = [list(poly.exterior.coords)[:-1]] + [list(r.coords)[:-1] for r in poly.interiors]
    rings = [ring for ring in rings if len(set(ring)) >= MIN_RING_POINTS]
    return m3d.CrossSection(rings, m3d.FillRule.EvenOdd)


def _without_empty_shells(solid: m3d.Manifold) -> m3d.Manifold | None:
    """The body without the zero-volume shells a boolean can leave inside it.

    Measured on the Paulskirche: the union of the tents leaves two four-triangle flaps of volume
    0 where nearly coincident skirts met, and they cost the finished body its single-shell
    property (genus -1, two shells) although they enclose nothing. Parts with volume are all
    kept — a genuinely detached wing of a building is geometry, not an artefact.
    """
    parts = [part for part in solid.decompose() if part.volume() > 0]
    if not parts:
        return None
    # compose rather than a boolean: decompose returns disjoint parts, so there is nothing to
    # resolve and composing merely puts them back into one Manifold.
    return parts[0] if len(parts) == 1 else m3d.Manifold.compose(parts)


def _valid(solid: m3d.Manifold) -> bool:
    return solid.status() == m3d.Error.NoError and not solid.is_empty() and solid.volume() > 0


def to_solid(surfaces: Surfaces) -> m3d.Manifold | None:
    """The watertight body of one LoD2 model, standing on z = 0, or None (spec §5).

    None means "discarded and to be counted": the run never stops for a building that cannot be
    closed (spec §5.5).
    """
    # One pass over the rings: faces_of is not cheap at 217 faces a building, and this used to
    # run three times (here, inside footprint_of and inside height_range).
    faces = faces_of(surfaces)
    if not faces:
        return None
    footprint = footprint_of_faces(faces)
    span = height_of_faces(faces)
    if footprint is None or span is None:
        return None
    z_min, z_max = span
    if z_max - z_min <= 0:
        return None
    roof = _roof_body(faces, z_min - GROUND_DROP_M)
    if roof is None or roof.status() != m3d.Error.NoError:
        return None
    prism = _cross_section(footprint).extrude((z_max - z_min) + 2 * GROUND_DROP_M)
    body = prism.translate((0.0, 0.0, z_min - GROUND_DROP_M)) ^ roof
    if body.status() != m3d.Error.NoError:
        return None
    # The cut deliberately reaches GROUND_DROP_M below the ground so the two bottom caps never
    # lie in the same plane. Cutting the result back to the ground keeps the exported "buildings"
    # part flush with the plate top, the way every prism is.
    body = body.trim_by_plane((0.0, 0.0, 1.0), z_min)
    body = _without_empty_shells(body)
    if body is None or not _valid(body):
        return None
    return body.translate((0.0, 0.0, -z_min))


def simplified(solid: m3d.Manifold, tolerance_mm: float = SOLID_SIMPLIFY_MM) -> m3d.Manifold:
    """Simplify, but only keep the result if it is still a body (spec §5).

    Measured: simplify(4.5) reduced a 12-triangle solid to 0 triangles. Without this guard a
    building would silently disappear from the model instead of merely keeping a few triangles.
    """
    if tolerance_mm <= 0:
        return solid
    candidate = solid.simplify(tolerance_mm)
    if not _valid(candidate) or candidate.volume() < SIMPLIFY_MIN_VOLUME * solid.volume():
        return solid
    return candidate
