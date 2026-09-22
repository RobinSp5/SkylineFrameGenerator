"""Roof solids over the minimum rotated rectangle of a footprint (spec §7).

Everything here is in print millimetres and independent of shapely: the caller hands in the
four rectangle corners and — for the clip — a ready-made footprint prism, so this module never
imports mesh.py (which imports this one).
"""

import math
from collections.abc import Sequence

import manifold3d as m3d

MIN_ROOF_MM = 0.3  # below this a roof is not visible in print and is skipped (spec §7)
DOME_SEGMENTS = 24  # circular segments of the sphere a dome is scaled from
ROUND_SEGMENTS = 12  # segments per half-circle arc of a round roof
HIP_INSET = 0.5  # ridge inset as a fraction of the short side
HALF_HIP_INSET = 0.25
MANSARD_INSET = 0.2  # mansard only; the gambrel ridge runs the full length (spec §7)
GABLE_HEIGHT_FRACTION = 0.6  # half-hipped gable point, above the eaves
KNEE_HEIGHT_FRACTION = 0.7  # mansard / gambrel second ring, above the eaves
KNEE_WIDTH_FRACTION = 0.8  # mansard / gambrel second ring, at this fraction of the half width

SHAPES: frozenset[str] = frozenset(
    {"gabled", "hipped", "half_hipped", "pyramidal", "skillion", "mansard", "gambrel", "dome", "round"}
)

Corner = tuple[float, float]
Point3 = tuple[float, float, float]
Frame = tuple[Corner, Corner, float, Corner, float]  # centre, ridge axis, half length, cross axis, half width


def _frame(rect: Sequence[Corner]) -> Frame | None:
    """Centre and unit axes of the rectangle; the ridge axis is the long one by default."""
    if len(rect) != 4:
        return None
    (x0, y0), (x1, y1), (x2, y2) = rect[0], rect[1], rect[2]
    e0 = (x1 - x0, y1 - y0)
    e1 = (x2 - x1, y2 - y1)
    len0 = math.hypot(*e0)
    len1 = math.hypot(*e1)
    if len0 <= 0 or len1 <= 0:
        return None
    centre = (sum(p[0] for p in rect) / 4, sum(p[1] for p in rect) / 4)
    if len0 >= len1:
        long_v, long_len, short_v, short_len = e0, len0, e1, len1
    else:
        long_v, long_len, short_v, short_len = e1, len1, e0, len0
    u = (long_v[0] / long_len, long_v[1] / long_len)
    v = (short_v[0] / short_len, short_v[1] / short_len)
    return centre, u, long_len / 2, v, short_len / 2


def _direction_vector(direction_deg: float) -> Corner:
    """Compass bearing in the local frame (0 = +y, clockwise) as a unit vector."""
    rad = math.radians(direction_deg)
    return math.sin(rad), math.cos(rad)


def _oriented(frame: Frame, direction_deg: float | None) -> Frame:
    """Swap the axes when roof:direction asks for a ridge across the long axis.

    roof:direction names the direction the roof faces, i.e. where it slopes down to, so the
    ridge runs perpendicular to it: the axis with the smaller |dot| against it wins.
    """
    centre, u, a, v, b = frame
    if direction_deg is None:
        return frame
    d = _direction_vector(direction_deg)
    if abs(u[0] * d[0] + u[1] * d[1]) > abs(v[0] * d[0] + v[1] * d[1]):
        return centre, v, b, u, a
    return frame


def _skillion_sign(v: Corner, direction_deg: float | None) -> float:
    """Which of the two long sides is the high one: the one away from the roof direction."""
    if direction_deg is None:
        return 1.0
    d = _direction_vector(direction_deg)
    return -1.0 if (v[0] * d[0] + v[1] * d[1]) > 0 else 1.0


def roof_points(
    rect_mm: Sequence[Corner],
    z_eaves_mm: float,
    z_ridge_mm: float,
    shape: str,
    direction_deg: float | None = None,
) -> list[Point3]:
    """The convex-hull points of one roof body. Empty for dome (a sphere) and unknown shapes."""
    frame = _frame(rect_mm)
    if frame is None or shape not in SHAPES or shape == "dome":
        return []
    centre, u, a, v, b = _oriented(frame, direction_deg)
    height = z_ridge_mm - z_eaves_mm
    short = 2 * b

    def at(du: float, dv: float, z: float) -> Point3:
        return (centre[0] + u[0] * du + v[0] * dv, centre[1] + u[1] * du + v[1] * dv, z)

    # Every shape stands on the full rectangle at the eaves: the raised points alone would be a
    # flat sheet (skillion) or a body hanging in the air.
    base = [at(-a, -b, z_eaves_mm), at(a, -b, z_eaves_mm), at(a, b, z_eaves_mm), at(-a, b, z_eaves_mm)]

    if shape == "gabled":
        return base + [at(-a, 0.0, z_ridge_mm), at(a, 0.0, z_ridge_mm)]
    if shape == "hipped":
        inset = min(HIP_INSET * short, a)
        return base + [at(-(a - inset), 0.0, z_ridge_mm), at(a - inset, 0.0, z_ridge_mm)]
    if shape == "half_hipped":
        inset = min(HALF_HIP_INSET * short, a)
        gable_z = z_eaves_mm + GABLE_HEIGHT_FRACTION * height
        return base + [
            at(-(a - inset), 0.0, z_ridge_mm),
            at(a - inset, 0.0, z_ridge_mm),
            at(-a, 0.0, gable_z),
            at(a, 0.0, gable_z),
        ]
    if shape == "pyramidal":
        return base + [at(0.0, 0.0, z_ridge_mm)]
    if shape == "skillion":
        sign = _skillion_sign(v, direction_deg)
        return base + [at(-a, sign * b, z_ridge_mm), at(a, sign * b, z_ridge_mm)]
    if shape in ("mansard", "gambrel"):
        # mansard is the hipped variant (ridge pulled in on both ends), gambrel the gabled one
        # (ridge over the full length, vertical gable ends) — spec §7. Both get the same knee
        # ring, and the mansard pulls that in on both axes while the gambrel only narrows it.
        inset = min(MANSARD_INSET * short, a) if shape == "mansard" else 0.0
        knee_z = z_eaves_mm + KNEE_HEIGHT_FRACTION * height
        knee_a = KNEE_WIDTH_FRACTION * a if shape == "mansard" else a
        knee_b = KNEE_WIDTH_FRACTION * b
        ring = [at(sx * knee_a, sy * knee_b, knee_z) for sx in (-1.0, 1.0) for sy in (-1.0, 1.0)]
        return base + [at(-(a - inset), 0.0, z_ridge_mm), at(a - inset, 0.0, z_ridge_mm)] + ring
    # round: two half-circle arcs, one at each end of the ridge
    arcs = [math.pi * k / ROUND_SEGMENTS for k in range(ROUND_SEGMENTS + 1)]
    return [
        at(sx * a, b * math.cos(t), z_eaves_mm + height * math.sin(t))
        for sx in (-1.0, 1.0)
        for t in arcs
    ]


def _dome(frame: Frame, z_eaves_mm: float, z_ridge_mm: float) -> m3d.Manifold:
    centre, u, a, _v, b = frame
    angle = math.degrees(math.atan2(u[1], u[0]))
    return (
        m3d.Manifold.sphere(1.0, DOME_SEGMENTS)
        .scale((a, b, z_ridge_mm - z_eaves_mm))
        .rotate((0.0, 0.0, angle))
        .translate((centre[0], centre[1], z_eaves_mm))
        # trim_by_plane keeps the half space with n.p >= offset, i.e. everything above the eaves.
        .trim_by_plane((0.0, 0.0, 1.0), z_eaves_mm)
    )


def roof_hull(
    rect_mm: Sequence[Corner],
    z_eaves_mm: float,
    z_ridge_mm: float,
    shape: str,
    direction_deg: float | None = None,
) -> m3d.Manifold | None:
    """The bare roof body between the eaves and the ridge plane, or None when there is none."""
    if shape not in SHAPES or z_ridge_mm - z_eaves_mm < MIN_ROOF_MM:
        return None
    frame = _frame(rect_mm)
    if frame is None:
        return None
    if shape == "dome":
        return _dome(frame, z_eaves_mm, z_ridge_mm)
    points = roof_points(rect_mm, z_eaves_mm, z_ridge_mm, shape, direction_deg)
    if len(points) < 4:
        return None
    return m3d.Manifold.hull_points(points)


def roof_solid(
    rect_mm: Sequence[Corner],
    z_eaves_mm: float,
    z_ridge_mm: float,
    shape: str,
    direction_deg: float | None = None,
    clip: m3d.Manifold | None = None,
) -> m3d.Manifold | None:
    """roof_hull, cut down to `clip` (the footprint prism) when one is given.

    The rectangle may stick out over a footprint that fills only 85 % of it, and an overhanging
    roof edge would float next to the wall instead of sitting on it.
    """
    solid = roof_hull(rect_mm, z_eaves_mm, z_ridge_mm, shape, direction_deg)
    if solid is None:
        return None
    if clip is not None:
        solid = solid ^ clip
    if solid.status() != m3d.Error.NoError or solid.is_empty() or solid.volume() <= 0:
        return None
    return solid
