import manifold3d as m3d
import pytest
from shapely.geometry import Polygon

from skylineframe.mesh import prism
from skylineframe.roofs import MIN_ROOF_MM, SHAPES, roof_hull, roof_points, roof_solid

# 10 x 6 mm rectangle, long axis along x, eaves at 0, ridge at 3 mm.
RECT = ((0.0, 0.0), (10.0, 0.0), (10.0, 6.0), (0.0, 6.0))
ZE, ZR = 0.0, 3.0


@pytest.mark.parametrize(
    "shape,expected",
    [
        # 4 corners at z=0 plus the two ridge points at the short-side midpoints. Prismatoid:
        # V = h/6 * (A_bottom + 4*A_mid + A_top) = 3/6 * (60 + 4*30 + 0) = 90.
        ("gabled", 90.0),
        # ridge inset 0.5 * short side = 3 mm, so the ridge is 4 mm long:
        # A_mid = ((10+4)/2) * (6/2) = 21  =>  3/6 * (60 + 84 + 0) = 72.
        ("hipped", 72.0),
        # ridge inset 0.25 * 6 = 1.5 mm (ridge 7 mm) gives 3/6 * (60 + 4*25.5) = 81, plus the
        # two gable points at 0.6 * 3 = 1.8 mm, each a pyramid of 1/3 * 10.0623 * 0.80498 = 2.7.
        ("half_hipped", 86.4),
        # pyramid over the full rectangle: 1/3 * 60 * 3 = 60.
        ("pyramidal", 60.0),
        # wedge: the whole rectangle raised on one long side => 10 * 6 * 3 / 2 = 90.
        ("skillion", 90.0),
        # mansard = hipped (ridge inset 0.2*6 = 1.2) plus a ring at 0.7*3 = 2.1 mm on 0.8 of
        # both half axes. Frustum 0 -> 2.1: 2.1/6 * (60 + 4*48.6 + 38.4) = 102.48; cap 2.1 -> 3:
        # 0.9/6 * (38.4 + 4*18.72) = 16.992; the convex hull also fills the dent their corners
        # leave between them, which is why the hull is 121.2 and not 119.472.
        ("mansard", 121.2),
        # gambrel = gabled (ridge over the full length) plus the same knee ring, pulled in
        # across only (10 x 4.8 at 2.1 mm). Frustum 0 -> 2.1: 2.1/6 * (60 + 4*54 + 48) = 113.4;
        # cap 2.1 -> 3: 0.9/6 * (48 + 4*24 + 0) = 21.6; the gable ends stay vertical, so there
        # is no dent to fill and the hull is exactly 113.4 + 21.6 = 135.0.
        ("gambrel", 135.0),
        # sphere(1, 24) scaled to (5, 3, 3) and trimmed at the eaves. The analytic half
        # ellipsoid is 2/3 * pi * 5 * 3 * 3 = 94.2478; the 24-segment polyhedron reaches
        # 90.547 (measured with manifold3d 3.5.3 — a different tessellation would shift it).
        ("dome", 90.547),
        # half cylinder over 12 segments per arc: 0.5 * 3 * 3 * sin(pi/12) * 12 * 10 = 139.7623.
        ("round", 139.762),
    ],
)
def test_roof_volumes_over_a_10x6_rectangle(shape, expected):
    solid = roof_hull(RECT, ZE, ZR, shape)
    assert solid is not None
    # The dome is the one shape whose volume depends on the library's tessellation, so it gets
    # a looser tolerance than the exact hull arithmetic of the others.
    tolerance = 1e-3 if shape == "dome" else 1e-4
    assert solid.volume() == pytest.approx(expected, rel=tolerance)


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_roof_stays_inside_the_rectangle_and_between_eaves_and_ridge(shape):
    solid = roof_hull(RECT, ZE, ZR, shape)
    xmin, ymin, zmin, xmax, ymax, zmax = solid.bounding_box()
    # abs=1e-3: the dome and the round roof touch the rectangle only with the vertices of
    # their tessellation (measured with manifold3d 3.5.3).
    assert (xmin, ymin) == pytest.approx((0.0, 0.0), abs=1e-3)
    assert (xmax, ymax) == pytest.approx((10.0, 6.0), abs=1e-3)
    assert (zmin, zmax) == pytest.approx((ZE, ZR), abs=1e-3)


def test_roof_sits_on_a_raised_eaves_plane():
    solid = roof_hull(RECT, 12.0, 15.0, "gabled")
    _, _, zmin, _, _, zmax = solid.bounding_box()
    assert (zmin, zmax) == pytest.approx((12.0, 15.0))
    assert solid.volume() == pytest.approx(90.0)


def test_roof_is_clipped_to_the_footprint():
    # L-shaped footprint inside the same rectangle. The gabled roof is 3 - |y - 3| mm high, so
    # the part over x in [0,10], y in [0,3] is 10 * 4.5 = 45 and the part over x in [0,4],
    # y in [3,6] is 4 * 4.5 = 18 => 63 mm³ instead of the unclipped 90.
    footprint = Polygon([(0, 0), (10, 0), (10, 3), (4, 3), (4, 6), (0, 6)])
    clip = prism(footprint, ZR - ZE + 0.01, z0=ZE)
    solid = roof_solid(RECT, ZE, ZR, "gabled", clip=clip)
    assert solid is not None
    assert solid.volume() == pytest.approx(63.0, rel=1e-6)


def test_roof_below_the_minimum_height_is_skipped():
    assert roof_hull(RECT, 0.0, MIN_ROOF_MM - 0.01, "gabled") is None
    assert roof_solid(RECT, 0.0, 0.2, "gabled") is None


def test_unknown_shape_and_degenerate_rectangle_are_skipped():
    assert roof_hull(RECT, ZE, ZR, "brezel") is None
    assert roof_hull(((0.0, 0.0), (0.0, 0.0), (0.0, 0.0), (0.0, 0.0)), ZE, ZR, "gabled") is None
    assert roof_hull(((0.0, 0.0), (10.0, 0.0)), ZE, ZR, "gabled") is None


def test_ridge_runs_along_the_long_axis_by_default():
    tops = [p for p in roof_points(RECT, ZE, ZR, "gabled") if p[2] == ZR]
    assert sorted(tops) == [(0.0, 3.0, 3.0), (10.0, 3.0, 3.0)]


def test_roof_direction_turns_the_ridge():
    # roof:direction is the downhill direction, so a roof facing east (90°) has its ridge
    # running north-south — here along the short axis of the rectangle.
    tops = [p for p in roof_points(RECT, ZE, ZR, "gabled", direction_deg=90.0) if p[2] == ZR]
    assert sorted(tops) == [(5.0, 0.0, 3.0), (5.0, 6.0, 3.0)]


def test_skillion_rises_away_from_its_direction():
    default_top = sorted(p for p in roof_points(RECT, ZE, ZR, "skillion") if p[2] == ZR)
    assert default_top == [(0.0, 6.0, 3.0), (10.0, 6.0, 3.0)]
    # facing north (+y) means the roof slopes down towards +y, so the south edge is the high one
    north = sorted(p for p in roof_points(RECT, ZE, ZR, "skillion", direction_deg=0.0) if p[2] == ZR)
    assert north == [(0.0, 0.0, 3.0), (10.0, 0.0, 3.0)]


def test_clip_that_misses_the_roof_yields_none():
    far_away = prism(Polygon([(100, 100), (110, 100), (110, 106), (100, 106)]), 3.0, z0=ZE)
    assert roof_solid(RECT, ZE, ZR, "gabled", clip=far_away) is None


def test_every_shape_produces_a_valid_solid():
    for shape in sorted(SHAPES):
        solid = roof_hull(RECT, ZE, ZR, shape)
        assert solid.status() == m3d.Error.NoError
        assert not solid.is_empty()
        assert solid.volume() > 0
