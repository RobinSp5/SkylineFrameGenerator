"""Convert prepared features from local metres to print millimetres."""

from collections.abc import Sequence
from dataclasses import dataclass, field

import manifold3d as m3d
import shapely.affinity
from shapely.geometry import Polygon
from shapely.ops import unary_union

from .features import Building
from .lod2.solidify import SOLID_SIMPLIFY_MM, simplified
from .prepare import Prepared
from .roofs import MIN_ROOF_MM
from .spec import SOCKEL_MM, FrameSpec
from .thicken import thickened
from .trees.geometry import fitted_trees
from .trees.model import Tree

# Share of a part's footprint that has to rest on lower bodies of the same outline before the
# part may start in the air (spec §8). Two towers of one complex often share a single corner
# node, so any positive overlap would accept a contact of a few hundredths of a square
# millimetre as a foundation and print a tower balancing on a point.
SUPPORT_FRACTION = 0.25


@dataclass
class ScaledRoof:
    rect_mm: tuple[tuple[float, float], ...]  # four corners of the minimum rotated rectangle
    shape: str
    z_eaves_mm: float
    z_ridge_mm: float
    direction_deg: float | None = None


@dataclass
class Prism:
    geom: Polygon  # footprint in mm, centred on the plate
    height_mm: float  # top of the vertical body (the eaves, when there is a roof)
    z0_mm: float = 0.0  # bottom of the body; > 0 only for a part that stands on something
    roof: ScaledRoof | None = None
    # A ready-made LoD2 body in print millimetres, standing on z = 0. When this is set, mesh.py
    # ignores geom/height_mm/roof and uses the body (spec §6). Last field on purpose: every
    # existing positional Prism(...) call stays valid.
    solid_mm: m3d.Manifold | None = None


@dataclass
class Scaled:
    buildings: list[Prism]
    blocks: list[Prism] = field(default_factory=list)
    roads: list[Polygon] = field(default_factory=list)
    water: list[Polygon] = field(default_factory=list)
    # Trees that print, already fitted between everything above (spec 6 §5.2).
    trees: list[Tree] = field(default_factory=list)


def building_height_mm(height_m: float, spec: FrameSpec) -> float:
    """Print height of one building, never below the printable minimum and never above the plate.

    The upper cap is the last line of defence against a mistagged height: a tower taller than
    the plate is wide turns the model into an unprintable spike.
    """
    raw = round(height_m * spec.scale * spec.z_exaggeration, 2)
    return min(max(raw, spec.min_building_height_mm), spec.plate_size_mm)


def raw_height_mm(height_m: float, spec: FrameSpec) -> float:
    """Same scaling without the printable minimum: for bottoms and ridges.

    A min_height run through building_height_mm would lift every ground-level footprint by
    min_building_height_mm and tear the model off the plate.
    """
    return min(round(height_m * spec.scale * spec.z_exaggeration, 2), spec.plate_size_mm)


def _scale_geom(geom: Polygon, factor: float) -> Polygon:
    return shapely.affinity.scale(geom, xfact=factor, yfact=factor, origin=(0, 0))


def _by_outline(buildings: list[Building]) -> dict[str, list[Building]]:
    groups: dict[str, list[Building]] = {}
    for b in buildings:
        if b.outline_id is not None:
            groups.setdefault(b.outline_id, []).append(b)
    return groups


def _is_supported(b: Building, siblings: list[Building]) -> bool:
    """True when the footprints of the same outline under `b` carry enough of it (spec §8).

    Parts start in the air by design (a setback tower stands on its base). A part with nothing
    below it is a tagging artefact, and printing it floating is impossible, so it is extended
    down to the plate instead.

    "Under `b`" means the sibling's body spans the bottom plane of `b`. All of them are unioned
    before the overlap is measured, so a part that rests on two neighbours each carrying a third
    of it is supported even though neither would qualify alone. The union has to cover at least
    SUPPORT_FRACTION of the part: a shared corner or a hairline seam is a point contact, not a
    foundation, and a tower standing on one would snap off the plate.
    """
    area = b.geom.area
    if area <= 0:  # a degenerate footprint has nothing to rest on and nothing to carry
        return False
    below = [
        other
        for other in siblings
        if other is not b and other.min_height_m < b.min_height_m and other.eaves_m >= b.min_height_m
    ]
    if not below:
        return False
    carried = unary_union([o.geom for o in below]).intersection(b.geom).area
    return carried / area >= SUPPORT_FRACTION


def _roof_of(b: Building, eaves_mm: float, spec: FrameSpec) -> ScaledRoof | None:
    if b.roof is None or len(b.rect) != 4:
        return None
    ridge_mm = max(raw_height_mm(b.ridge_m, spec), eaves_mm)
    if ridge_mm - eaves_mm < MIN_ROOF_MM:
        return None
    s = spec.scale
    return ScaledRoof(
        rect_mm=tuple((x * s, y * s) for x, y in b.rect),
        shape=b.roof.shape,
        z_eaves_mm=eaves_mm,
        z_ridge_mm=ridge_mm,
        direction_deg=b.roof.direction_deg,
    )


def _fit_to_plate(solid: m3d.Manifold, spec: FrameSpec) -> m3d.Manifold | None:
    """Cut the body down to the plate square and to plate_size_mm of height (spec §6).

    The bounding box is checked first: almost every building is well inside the plate, and a
    boolean per building would cost more than the whole LoD2 import. The height cap is the same
    last line of defence building_height_mm applies to a prism.
    """
    half = spec.plate_size_mm / 2
    top = spec.plate_size_mm
    x0, y0, _z0, x1, y1, z1 = solid.bounding_box()
    if -half <= x0 and -half <= y0 and x1 <= half and y1 <= half and z1 <= top:
        return solid
    box_mm = m3d.Manifold.cube((spec.plate_size_mm, spec.plate_size_mm, top), center=True)
    cut = solid ^ box_mm.translate((0.0, 0.0, top / 2))
    if cut.status() != m3d.Error.NoError or cut.is_empty() or cut.volume() <= 0:
        return None
    return cut


def _footprint_prism(poly_mm: Polygon, height_mm: float) -> m3d.Manifold:
    rings = [list(poly_mm.exterior.coords)[:-1]] + [list(r.coords)[:-1] for r in poly_mm.interiors]
    rings = [ring for ring in rings if len(set(ring)) >= 3]
    return m3d.CrossSection(rings, m3d.FillRule.EvenOdd).extrude(height_mm)


def _scaled_solid(b: Building, spec: FrameSpec) -> m3d.Manifold | None:
    """The LoD2 body in print millimetres, or None when the prism path has to take over (spec §6)."""
    if b.solid_m is None:
        return None
    s = spec.scale
    solid = b.solid_m.scale((s, s, s * spec.z_exaggeration))
    # The body was cut from the raw LoD2 outline, but prepare simplified b.geom by
    # SIMPLIFY_TOLERANCE_MM / scale (0.5 m at the skyline preset) and prepare._clearance grows the
    # road and water blockers by exactly that much. A wall left outside the simplified outline
    # would eat that hairline gap and put a pocket wall in the same plane as a building wall, so
    # the body is trimmed to the outline the rest of the pipeline reasons about.
    solid = solid ^ _footprint_prism(_scale_geom(b.geom, s), spec.plate_size_mm + 1.0)
    if solid.status() != m3d.Error.NoError or solid.is_empty() or solid.volume() <= 0:
        return None
    solid = _fit_to_plate(solid, spec)
    if solid is None:
        return None
    # A body carries its own height and never passes through building_height_mm, so nothing else
    # would lift a real 2 m building off the 0.3 mm it scales to. Below the printable minimum the
    # prism path takes over, which clamps — a bump that vanishes in the first layer is worse than
    # a box at the right minimum height.
    if solid.bounding_box()[5] < spec.min_building_height_mm:
        return None
    solid = simplified(solid, SOLID_SIMPLIFY_MM)
    # After the simplification, which moves vertices by up to SOLID_SIMPLIFY_MM and could thin a
    # widened spire again. The thickening stays inside the trimmed body's own outline.
    return thickened(solid, spec.min_line_mm) if spec.print_optimized else solid


def _building_prism(b: Building, groups: dict[str, list[Building]], spec: FrameSpec) -> Prism:
    eaves_mm = building_height_mm(b.eaves_m, spec)
    z0_mm = 0.0
    # Print-optimized, every part stands on the ground: a part may otherwise start in the air
    # with only SUPPORT_FRACTION of it resting on its base, and the rest is an overhang a slicer
    # flags as floating. Filled to the ground the whole model is carried from below by
    # construction, wherever it is, instead of by a threshold tuned on one city.
    if (
        not spec.print_optimized
        and b.min_height_m > 0
        and b.outline_id is not None
        and _is_supported(b, groups.get(b.outline_id, []))
    ):
        z0_mm = raw_height_mm(b.min_height_m, spec)
        if z0_mm >= eaves_mm:  # mistagged: the part would have no body at all
            z0_mm = 0.0
    return Prism(
        geom=_scale_geom(b.geom, spec.scale),
        height_mm=eaves_mm,
        z0_mm=z0_mm,
        roof=_roof_of(b, eaves_mm, spec),
        solid_mm=_scaled_solid(b, spec),
    )


def scale_features(prepared: Prepared, spec: FrameSpec, trees: Sequence[Tree] = ()) -> Scaled:
    """`trees` are already in print millimetres (spec 6 §3); they are fitted here, against the
    scaled footprints, blocks, roads and water, the same way a building is clamped to what prints."""
    s = spec.scale
    groups = _by_outline(prepared.buildings)
    scaled = Scaled(
        buildings=[_building_prism(b, groups, spec) for b in prepared.buildings],
        # SOCKEL_MM as it is, not through building_height_mm: its 0.8 mm minimum and
        # z_exaggeration would lift the sockel back up to the roofs of small houses — exactly the
        # slab it replaces (spec 4a §2.3).
        blocks=[Prism(_scale_geom(bl.geom, s), SOCKEL_MM) for bl in prepared.blocks],
        roads=[_scale_geom(p, s) for p in prepared.roads],
        water=[_scale_geom(p, s) for p in prepared.water],
    )
    if trees:
        obstacles = [p.geom for p in scaled.buildings] + [p.geom for p in scaled.blocks] + scaled.roads + scaled.water
        scaled.trees = fitted_trees(list(trees), obstacles, spec)
    return scaled
