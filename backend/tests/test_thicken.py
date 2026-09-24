import time

import manifold3d as m3d
import pytest
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from skylineframe.thicken import thickened

LINE = 0.8  # spec.min_line_mm with print_optimized on
# The float32 mesh and the slice: a disk of the line width less this fits into every section.
CELL_SLACK = 0.02


def cube(x0, y0, z0, x1, y1, z1) -> m3d.Manifold:
    return m3d.Manifold.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0))


def spiked(x0=2.35, y0=2.35, w=0.3, top=6.0) -> m3d.Manifold:
    """A 5 x 5 x 2 mm box with a w x w spike rising to `top` — a church spire in print mm."""
    return cube(0, 0, 0, 5, 5, 2) + cube(x0, y0, 2, x0 + w, y0 + w, top)


def gabled(slope: float) -> m3d.Manifold:
    """A 4 x 6 mm house, eaves at 2 mm, ridge along y at x = 2 rising with `slope` (z per xy)."""
    ridge = 2.0 + 2.0 * slope
    profile = m3d.CrossSection([[(0, 0), (4, 0), (4, 2), (2, ridge), (0, 2)]])
    # The profile is drawn in x-z; extrude along +z and turn that axis into +y.
    body = profile.extrude(6.0).rotate((90, 0, 0)).translate((0, 6.0, 0))
    x0, y0, z0, x1, y1, z1 = body.bounding_box()
    assert (x0, y0, z0, x1, y1, z1) == pytest.approx((0, 0, 0, 4, 6, ridge), abs=1e-6)
    return body


def section(solid: m3d.Manifold, z: float) -> Polygon | MultiPolygon:
    rings = solid.slice(z).to_polygons()
    return unary_union([Polygon(r) for r in rings if len(r) >= 3])


def test_a_thin_spike_is_widened_to_the_line_at_every_height_and_keeps_its_height():
    body = spiked()
    out = thickened(body, LINE)
    assert out.bounding_box()[5] == pytest.approx(6.0, abs=1e-4)
    for z in (2.2, 3.0, 4.0, 5.0, 5.9):
        sec = section(out, z)
        # A disk of the line width (less one grid cell) fits into the cross-section.
        assert not sec.buffer(-(LINE - CELL_SLACK) / 2).is_empty, z
    # The spike was thickened, not the whole roof raised: the box around it is untouched.
    assert section(out, 3.0).area < 2.0


def test_a_tapering_spire_is_a_line_wide_up_to_its_tip():
    # The Dreikönigskirche at 1:15 000: 0.47 mm at its foot, a point at the top, 3.9 mm tall.
    spire = m3d.Manifold.cylinder(3.9, 0.235, 0.0, 4).translate((2.5, 2.5, 2.7))
    body = cube(0, 0, 0, 5, 5, 2.7) + spire
    out = thickened(body, LINE)
    assert out.bounding_box()[5] == pytest.approx(6.6, abs=1e-4)
    for z in (2.8, 4.0, 5.5, 6.5):
        assert not section(out, z).buffer(-(LINE - CELL_SLACK) / 2).is_empty, z


def test_the_thickening_stays_inside_the_footprint():
    # The body's own outline is its footprint: scale.py has trimmed it to the prepared one.
    body = spiked(x0=4.7)  # the spike stands against the wall
    out = thickened(body, LINE)
    x0, y0, _z0, x1, y1, _z1 = out.bounding_box()
    assert (x0, y0) >= (-1e-6, -1e-6) and (x1, y1) <= (5 + 1e-6, 5 + 1e-6)
    outside = out - cube(0, 0, -1, 5, 5, 10)
    assert outside.is_empty() or outside.volume() < 1e-9
    assert out.volume() > body.volume()  # widened inwards instead
    assert out.bounding_box()[5] == pytest.approx(6.0, abs=1e-4)


def test_the_thickening_stays_inside_the_trimmed_body():
    # scale.py trims the body to the footprint first; the thickening must not grow past it again.
    body = spiked(x0=2.5) ^ cube(0, 0, 0, 3, 5, 10)
    out = thickened(body, LINE)
    assert out.bounding_box()[3] <= 3 + 1e-6


def test_a_plain_box_comes_back_unchanged():
    body = cube(0, 0, 0, 5, 3, 2)
    out = thickened(body, LINE)
    assert out is body


@pytest.mark.parametrize("slope", [0.5, 1.5, 2.6])  # up to a 60 degree roof at z_exaggeration 1.5
def test_a_gabled_roof_comes_back_unchanged(slope):
    body = gabled(slope)
    out = thickened(body, LINE)
    assert out is body
    assert out.volume() == pytest.approx(body.volume(), abs=1e-6)


def test_a_setback_tower_comes_back_unchanged():
    # A step inside one body is a wall, not a thin part.
    body = cube(0, 0, 0, 6, 6, 2) + cube(1, 1, 2, 3, 3, 8)
    assert thickened(body, LINE) is body


def test_a_spike_already_as_wide_as_the_line_comes_back_unchanged():
    body = spiked(x0=2.0, y0=2.0, w=1.0)
    assert thickened(body, LINE) is body


def test_the_result_is_one_valid_body():
    out = thickened(spiked(), LINE)
    assert out.status() == m3d.Error.NoError
    assert out.genus() == 0
    assert len(out.decompose()) == 1


def test_the_check_on_a_printable_body_is_cheap():
    # 665 LoD2 bodies in the Frankfurt square: the common case has to cost next to nothing.
    body = gabled(1.5).scale((2.0, 2.0, 1.0))  # 8 x 12 mm, a large building at 1:15 000
    thickened(body, LINE)  # warm-up (imports, first-call overhead)
    start = time.perf_counter()
    for _ in range(5):
        thickened(body, LINE)
    assert (time.perf_counter() - start) / 5 < 0.05
