"""Clip features to the target square, repair geometry, form blocks and derive road/water areas.

Everything in this module is in local metres. The stage order follows spec §6:
clip -> fill heights -> assign parts -> resolve roofs -> blocks -> printability -> roads/water.
"""

import math
from dataclasses import dataclass, field, replace

import numpy as np
import shapely
from shapely import STRtree
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import make_valid

from .features import Block, Building, Features, Road, Water
from .heights import estimate_height_m
from .lod2.solidify import faces_of, footprint_of_faces, height_of_faces, to_solid
from .project import square_local
from .spec import MIN_FEATURE_MM, FrameSpec, Mode

SIMPLIFY_TOLERANCE_MM = 0.05
# Fraction of the weld radius used to collapse the chords its round joins leave behind.
ARC_SIMPLIFY_FRACTION = 0.1
BLOCK_PERCENTILE = 0.25  # spec §6.4
# Blocks stay a hair wider than the pure close so individual building walls are strictly inside
# the block volume below block height; avoids coincident faces in the 3D union.
BLOCK_HAIR_MM = 0.02
ROOF_RECT_RATIO = 0.85  # spec §6.6
ROOF_SLOPE_FACTOR = 0.29  # 30° over the half width (spec §7)
ROOF_HEIGHT_MIN_M = 2.0
ROOF_HEIGHT_MAX_M = 6.0
DOME_HEIGHT_FACTOR = 0.5
ROUND_ROOF_SHAPES = ("dome", "round")
LOD2_DISPLACE_FRACTION = 0.5  # spec §6.3: an OSM footprint covered by more than this is replaced
# Floor under the remainder an LoD2 model leaves of an OSM footprint. Two outlines that describe
# the same wall never agree to the micrometre, so their difference leaves corner triangles of a
# few square micrometres; those are round-off, not buildings, and must not become footprints.
# Anything a map or an LoD2 model really describes is orders of magnitude above 100 cm², and
# anything real but small still reaches the model through its block (spec §6.5).
LOD2_REMAINDER_MIN_AREA_M2 = 0.01


@dataclass
class Prepared:
    buildings: list[Building]  # individually printable footprints; valid Polygons in local metres
    blocks: list[Block] = field(default_factory=list)
    roads: list[Polygon] = field(default_factory=list)
    water: list[Polygon] = field(default_factory=list)
    footprint_coverage: float = 0.0  # building area in the model / building area in the square
    lod2_rejected: int = 0  # LoD2 models whose body would not close (spec §5.5)
    # LoD2 footprints that actually reached the model, blocks included. The provider answering at
    # all is not the same thing: the query box carries a 100 m margin, so a square over a park can
    # come back full of models that all clip away, and a model without a usable footprint (a 2D
    # response, say) never becomes a Building either. Naming the source then would be a false
    # attribution in SOURCES.txt, so the pipeline gates the source name on this count (spec §7/§8).
    lod2_footprints: int = 0


def polygons_of(geom: BaseGeometry | None) -> list[Polygon]:
    """Repair and flatten any geometry into a list of non-empty, valid Polygons."""
    if geom is None or geom.is_empty:
        return []
    geom = make_valid(geom)
    if isinstance(geom, Polygon):
        parts = [geom]
    elif isinstance(geom, (MultiPolygon, GeometryCollection)):
        parts = []
        for g in geom.geoms:
            if isinstance(g, Polygon):
                parts.append(g)
            elif isinstance(g, MultiPolygon):
                parts.extend(g.geoms)
    else:
        return []
    return [p for p in parts if not p.is_empty and p.area > 0]


def _is_printable(poly: Polygon, min_area_m2: float, half_feature_m: float) -> bool:
    """Large enough, and thick enough somewhere: eroding by half the minimum feature must leave something."""
    return poly.area >= min_area_m2 and not poly.buffer(-half_feature_m).is_empty


def _clip(buildings: list[Building], square: Polygon, tol_m: float) -> list[Building]:
    """Clip to the square, simplify and repair; one Building per resulting polygon.

    Nothing is dropped for being small here — the printability split happens after the blocks
    are formed, so a small footprint still contributes its area to its block (spec §6.5).

    A LoD2 footprint that the square cuts in two keeps its faces on the larger piece only: the
    body is built once and clipped to the plate later, so a second copy would solidify the same
    building twice and put two identical bodies into the union.
    """
    out: list[Building] = []
    for b in buildings:
        pieces: list[Polygon] = []
        for poly in polygons_of(b.geom.intersection(square)):
            # Simplification is not guaranteed to preserve validity (notably for rings with holes),
            # so its result goes back through the same repair/flatten choke point.
            pieces.extend(polygons_of(poly.simplify(tol_m, preserve_topology=True)))
        keep = max(range(len(pieces)), key=lambda i: pieces[i].area) if pieces else 0
        for i, piece in enumerate(pieces):
            out.append(replace(b, geom=piece, surfaces=b.surfaces if i == keep else ()))
    return out


def estimate_missing_heights(buildings: list[Building]) -> None:
    """Fill height_m of outlines without any height tag from type and area (spec §5). In place.

    Parts are skipped: they take the height of their outline, or the spec default (spec §4).
    """
    for b in buildings:
        if b.height_m <= 0 and not b.is_part:
            b.height_m = estimate_height_m(b.kind, b.geom.area)


def lod2_buildings(features: Features) -> list[Building]:
    """One Building per LoD2 model: footprint, ground-to-ridge height, faces kept for later.

    Only the cheap half of spec §5 runs here. The body costs two orders of magnitude more and is
    built in solidify_buildings, for the footprints that survive the printability check — a
    1500 m Frankfurt square delivers 6 119 models and most of them end up inside a block.
    """
    out: list[Building] = []
    for raw in features.lod2:
        # Walk the rings once: this loop runs for every model the provider delivered (6 119 in a
        # 1500 m Frankfurt square), not only for the ones that end up with a body.
        faces = faces_of(raw.surfaces)
        footprint = footprint_of_faces(faces)
        span = height_of_faces(faces)
        if footprint is None or span is None or span[1] - span[0] <= 0:
            continue
        out.append(
            Building(
                geom=footprint,
                # The model is levelled onto the plate, so the height is ridge minus ground and
                # never metres above sea level (spec §5.6). height_is_top: the roof is in the body.
                height_m=span[1] - span[0],
                height_is_top=True,
                roof=None,
                osm_id=raw.osm_id,
                lod2=True,
                surfaces=raw.surfaces,
            )
        )
    return out


def drop_covered(
    osm: list[Building], lod2: list[Building]
) -> tuple[list[Building], list[BaseGeometry]]:
    """Subtract the LoD2 footprints from the OSM ones they take over (spec §6.3/§6.4).

    A footprint LOD2_DISPLACE_FRACTION covered or less survives untouched: LoD2 stock ends at
    state borders and misses new buildings, so what is only grazed must not move. Above that
    share the LoD2 model is the building, and the OSM footprint is replaced — but only where the
    LoD2 model actually covers it. Whatever sticks out is a wing, an extension or a garage the
    LoD2 stock does not know about, and it stays as a footprint of its own. Slivers among those
    remainders are not printable and reach the model through their block, which is how the rest
    of the pipeline already recovers small geometry (spec §6.5). Discarding the whole footprint
    instead threw 21 023 m² away in a 1500 m Frankfurt square — 2.2 % of its building area.

    One rule for outlines and for building:part alike: a part over a LoD2 building is a setback
    that the body already has, and keeping it would put a second tower inside the first.

    Returns (kept, displaced): the displaced geometry is the area the LoD2 models genuinely took
    over. It belongs in the denominator of footprint_coverage, which is the acceptance gate for
    this feature, so that the metric describes the building area that stood in the square rather
    than the area that survived this stage.
    """
    if not lod2:
        return osm, []
    tree = STRtree([b.geom for b in lod2])
    kept: list[Building] = []
    displaced: list[BaseGeometry] = []
    for b in osm:
        area = b.geom.area
        if area <= 0:
            kept.append(b)
            continue
        hits = tree.query(b.geom, predicate="intersects")
        if len(hits) == 0:
            kept.append(b)
            continue
        cover = unary_union([lod2[int(i)].geom for i in hits])
        taken = cover.intersection(b.geom)
        if taken.area / area <= LOD2_DISPLACE_FRACTION:
            kept.append(b)
            continue
        displaced.extend(polygons_of(taken))
        # polygons_of rather than a bare difference: the remainder of a building the LoD2 model
        # cuts in two is a MultiPolygon, and a footprint is one polygon everywhere else in this
        # module.
        for piece in polygons_of(b.geom.difference(cover)):
            if piece.area < LOD2_REMAINDER_MIN_AREA_M2:
                continue
            # No roof on the remainder, exactly as in assign_parts: roof:shape described the whole
            # building, and over a leftover strip the roof body would be a spike.
            kept.append(replace(b, geom=piece, roof=None, rect=()))
    return kept, displaced


def solidify_buildings(buildings: list[Building]) -> int:
    """Build the LoD2 body of every building that is printed on its own (spec §5). In place.

    Returns the number of models whose body would not close. Those keep their footprint and their
    height and are extruded like any other building: the OSM footprint under them is already gone,
    so dropping them as well would leave a hole where a house stands.
    """
    rejected = 0
    for b in buildings:
        if not b.surfaces:
            continue
        b.solid_m = to_solid(b.surfaces)
        if b.solid_m is None:
            rejected += 1
    return rejected


def assign_parts(buildings: list[Building], default_height_m: float) -> list[Building]:
    """Turn outlines with parts into (parts + remainder) and return the footprint list (spec §6.3).

    A part belongs to the outline that contains an interior point of it. Outlines with at least
    one part are not extruded as a whole any more: the parts are rendered, and what they leave
    of the outline becomes one remainder footprint per polygon at the outline's own height.
    A part with no outline is treated as a building of its own, height estimate included.
    """
    # A LoD2 outline is never a part owner: its setbacks are already inside the body, and turning
    # it into remainder polygons would hand a copy of its faces to every remainder and solidify
    # the same building once per piece (spec §6.4). Parts over a LoD2 building are usually gone by
    # now; one that survived drop_covered simply stands beside it, like a part without an outline.
    outlines = [b for b in buildings if not b.is_part and not b.lod2]
    lod2_outlines = [b for b in buildings if not b.is_part and b.lod2]
    parts = [b for b in buildings if b.is_part]
    if not parts:
        return list(buildings)

    tree = STRtree([o.geom for o in outlines]) if outlines else None
    covered: dict[int, list[BaseGeometry]] = {}
    for p in parts:
        hit: int | None = None
        if tree is not None:
            # STRtree applies the predicate as input.predicate(tree_geom), so "within" returns
            # the outlines that contain the point. representative_point() is inside the part by
            # construction; the centroid of an L or a U is not (spec §6.3). Nested outlines:
            # the first hit wins.
            found = tree.query(p.geom.representative_point(), predicate="within")
            if len(found):
                hit = int(found[0])
        if hit is None:
            if p.height_m <= 0:
                # A part whose outline is missing from the data is an ordinary building
                # (spec §6.3), so it gets the same type/area estimate as any untagged outline
                # and falls back to the spec default only if that estimate has nothing to say.
                p.height_m = estimate_height_m(p.kind, p.geom.area) or default_height_m
            continue
        owner = outlines[hit]
        p.outline_id = owner.osm_id
        if p.height_m <= 0:
            p.height_m = owner.height_m
            p.height_is_top = owner.height_is_top
        covered.setdefault(hit, []).append(p.geom)

    footprints: list[Building] = list(parts) + lod2_outlines
    for i, owner in enumerate(outlines):
        if i not in covered:
            footprints.append(owner)
            continue
        remainder = owner.geom.difference(unary_union(covered[i]))
        for poly in polygons_of(remainder):
            # The remainder inherits the height but never the roof: the roof shape belongs to
            # the main body, and a roof on a leftover strip would be a spike. outline_id lets
            # scale.py see that a part standing on this remainder is supported.
            footprints.append(replace(owner, geom=poly, roof=None, rect=(), outline_id=owner.osm_id))
    return footprints


def minimum_rect(poly: Polygon) -> tuple[tuple[float, float], ...]:
    """The four corners of the minimum rotated rectangle, or () when there is none.

    shapely's oriented_envelope divides by zero for axis-aligned input and the suite turns
    warnings into errors, so the numpy error state is silenced around this one call.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        rect = shapely.minimum_rotated_rectangle(poly)
    if not isinstance(rect, Polygon) or rect.is_empty:
        return ()
    coords = list(rect.exterior.coords)[:4]
    if len(coords) != 4:
        return ()
    return tuple((float(x), float(y)) for x, y in coords)


def rect_sides(rect: tuple[tuple[float, float], ...]) -> tuple[float, float]:
    """(long side, short side) of a rectangle given by four corners."""
    (x0, y0), (x1, y1), (x2, y2) = rect[0], rect[1], rect[2]
    a = math.hypot(x1 - x0, y1 - y0)
    b = math.hypot(x2 - x1, y2 - y1)
    return max(a, b), min(a, b)


def default_roof_height_m(shape: str, short_side_m: float) -> float:
    """Roof height when neither roof:height nor roof:levels is tagged (spec §7)."""
    if shape in ROUND_ROOF_SHAPES:
        return DOME_HEIGHT_FACTOR * short_side_m
    return min(max(ROOF_SLOPE_FACTOR * short_side_m, ROOF_HEIGHT_MIN_M), ROOF_HEIGHT_MAX_M)


def resolve_roof(b: Building, rotation_deg: float) -> None:
    """Fill eaves_m, ridge_m, rect and the roof height of one footprint (spec §4/§6.6/§7). In place.

    A footprint that fills less than ROOF_RECT_RATIO of its minimum rotated rectangle loses its
    roof: the roof body is built over that rectangle, and over an L or a comb it would stand in
    the courtyard rather than on the building.
    """
    b.eaves_m = b.ridge_m = b.height_m
    if b.roof is None:
        return
    rect = minimum_rect(b.geom)
    if len(rect) != 4:
        b.roof = None
        return
    long_m, short_m = rect_sides(rect)
    rect_area = long_m * short_m
    if rect_area <= 0 or b.geom.area / rect_area < ROOF_RECT_RATIO:
        b.roof = None
        return

    roof = b.roof
    if roof.height_m <= 0:
        roof = replace(roof, height_m=default_roof_height_m(roof.shape, short_m))
    if roof.direction_deg is not None:
        # project.py rotates the world by +rotation_deg, so a compass bearing in the local
        # frame is the tagged bearing minus rotation_deg.
        roof = replace(roof, direction_deg=(roof.direction_deg - rotation_deg) % 360)
    b.rect = rect
    if b.height_is_top:
        b.ridge_m = b.height_m
        b.eaves_m = max(b.height_m - roof.height_m, 0.5 * b.height_m)
        roof = replace(roof, height_m=b.ridge_m - b.eaves_m)
    else:
        b.eaves_m = b.height_m
        b.ridge_m = b.height_m + roof.height_m
    b.roof = roof


def weighted_percentile(values: list[float], weights: list[float], q: float) -> float:
    """The q-quantile of `values` weighted by `weights`, 'lower' convention.

    Sort by value, accumulate the weights and return the first value whose cumulative weight
    reaches q of the total. With equal weights this is the plain q-quantile.
    """
    pairs = sorted(zip(values, weights))
    target = q * sum(weights)
    cumulative = 0.0
    for value, weight in pairs:
        cumulative += weight
        if cumulative >= target:
            return value
    return pairs[-1][0]


def _close(area: BaseGeometry, radius_m: float, hair_m: float = 0.0) -> BaseGeometry:
    """Morphological close with round joins: dilate, then erode (spec §6.4).

    Eroding by `hair_m` less than the dilation leaves the block that much wider than the pure
    close. A block is the welded hull of the very footprints it contains, so without the hair
    its wall is exactly the wall of every bulky building in it: the union of the two solids
    then has to resolve coincident vertical faces and leaves degenerate faces and zero-volume
    shells behind. The hair is 0.02 mm in print space — two orders of magnitude below what a
    nozzle can resolve, and enough to put every building wall strictly inside the block.

    The round joins are approximated by chords, which multiplies the vertex count of every
    block outline without adding a shape the printer could resolve, so they are collapsed
    again afterwards — same treatment as the road/water weld.
    """
    if area.is_empty:
        return area
    closed = area.buffer(radius_m, join_style="round").buffer(-(radius_m - hair_m), join_style="round")
    return closed.simplify(radius_m * ARC_SIMPLIFY_FRACTION, preserve_topology=True)


def road_corridors(roads: list[Road], spec: FrameSpec, square: Polygon) -> BaseGeometry:
    """The road surfaces before anything is cut out of them, clipped to the square.

    Blocks are formed around these corridors and the pockets are cut from them, so both stages
    see exactly the same road geometry (spec §6.4).
    """
    if not roads:
        return Polygon()
    buffered = [
        r.geom.buffer(spec.road_width_mm[r.cls] / spec.scale / 2, cap_style="flat", join_style="round")
        for r in roads
    ]
    return unary_union(buffered).intersection(square)


def water_area(water: list[Water], square: Polygon) -> BaseGeometry:
    """The water surfaces before anything is cut out of them, clipped to the square.

    Blocks are formed around these surfaces and the recesses are cut from them, so both stages
    see exactly the same water geometry — the same contract road_corridors has (spec §6.4).
    """
    if not water:
        return Polygon()
    return unary_union([w.geom for w in water]).intersection(square)


def build_blocks(
    footprints: list[Building],
    spec: FrameSpec,
    close_m: float,
    min_area_m2: float,
    half_feature_m: float,
    corridors: BaseGeometry,
    waters: BaseGeometry,
    square: Polygon,
) -> list[Block]:
    """One Block per connected group of footprints, at the weighted 25th percentile eaves height.

    A block that is itself unprintable (a single shed in the middle of a field) is dropped —
    it would be a sliver in the mesh, and footprint_coverage reports what that costs.

    The height stays the plain percentile in metres. The printable minimum is a print-space
    number and is applied once, by scale.building_height_mm, exactly as for a building: a
    metre floor of min_building_height_mm / scale here would be scaled again afterwards and
    would therefore carry z_exaggeration twice (spec §6.4).

    Roads and water both stop the close from welding across them, but they do it at opposite
    ends of it (spec §6.4). The road corridors come off the *input*: a street buffer overlaps
    the footprints along it, and trimming them back to the kerb is what opens the gap the close
    then refuses to bridge. Water comes off the *output*, and only where no footprint stands:
    subtracting it from the input would delete a building standing in the water — a pier, a
    riverbank block, an island — from the block layer altogether, while cutting
    `water - footprints` out of the closed area still removes any bridge the close threw across
    a canal, because such a bridge is by construction water with no footprint on it.

    The result is clipped to the square: the block hair widens a block that reaches the edge of
    the model past the plate, and the plate is exactly plate_size_mm wide.
    """
    if not footprints:
        return []
    covered = unary_union([b.geom for b in footprints])
    area = covered.difference(corridors) if not corridors.is_empty else covered
    closed = _close(area, close_m, BLOCK_HAIR_MM / spec.scale).intersection(square)
    if not waters.is_empty:
        closed = closed.difference(waters.difference(covered))
    polys = [p for p in polygons_of(closed) if _is_printable(p, min_area_m2, half_feature_m)]
    if not polys:
        return []

    tree = STRtree(polys)
    members: dict[int, list[Building]] = {}
    for b in footprints:
        found = tree.query(b.geom.representative_point(), predicate="within")
        if len(found):
            members.setdefault(int(found[0]), []).append(b)

    blocks: list[Block] = []
    for i, poly in enumerate(polys):
        inside = members.get(i, [])
        if not inside:
            continue
        height = weighted_percentile(
            [b.eaves_m for b in inside], [b.geom.area for b in inside], BLOCK_PERCENTILE
        )
        blocks.append(Block(geom=poly, height_m=height))
    return blocks


def _coverage(
    footprints: list[Building],
    blocks: list[Block],
    buildings: list[Building],
    displaced: list[BaseGeometry],
) -> float:
    """Share of the building area in the square that the model still carries (spec §6).

    The model is the union of the blocks and the individually printable buildings: a footprint
    whose block was dropped, or trimmed away by a road corridor, still counts when it is a
    solid of its own.

    `displaced` are the OSM footprints that LoD2 replaced, already clipped to the square. They go
    into the denominator and never into the numerator: the area they held stood in the square and
    is what the model has to account for, so the part of it no LoD2 model covers shows up as the
    loss it is instead of vanishing with the footprint.
    """
    if not footprints and not displaced:
        return 0.0
    total = unary_union([*(b.geom for b in footprints), *displaced])
    if total.is_empty or total.area <= 0:
        return 0.0
    modelled = unary_union([*(b.geom for b in blocks), *(b.geom for b in buildings)])
    if modelled.is_empty:
        return 0.0
    return float(modelled.intersection(total).area / total.area)


def _weld(area: BaseGeometry, weld_m: float) -> BaseGeometry:
    """Morphological close: merge pockets that only touch at a point and drop hairline gaps.

    Two pockets meeting in a single point are extruded as two prisms and leave a zero-thickness
    plate wall between them, which no slicer can print. Closing fuses them into one pocket and
    costs at most weld_m of outline accuracy.

    The close approximates its round joins with chords, which multiplies the vertex count of
    every pocket outline (and with it the triangle count of the whole model) without adding any
    shape the printer could resolve. Collapsing them again with a tolerance an order of magnitude
    below the outline tolerance removes them without reopening what the close merged.
    """
    if area.is_empty:
        return area
    closed = area.buffer(weld_m).buffer(-weld_m)
    return closed.simplify(weld_m * ARC_SIMPLIFY_FRACTION, preserve_topology=True)


def _clearance(blocked: BaseGeometry, weld_m: float) -> BaseGeometry:
    """Grow what blocks a pocket, so the pocket wall never coincides with the wall that bounds it.

    Buildings are sunk into the plate, so a pocket cut exactly at a building outline puts the
    building wall and the pocket wall in the same plane: the union of the two solids then has to
    resolve a zero-thickness wall and leaves degenerate faces and non-manifold edges behind.
    A hairline gap of SIMPLIFY_TOLERANCE_MM in print space removes that coincidence entirely.
    """
    # Mitre joins, so growing a footprint keeps its corner count instead of replacing every
    # corner with a fan of arc vertices.
    return blocked if blocked.is_empty else blocked.buffer(weld_m, join_style="mitre")


def _road_areas(
    corridors: BaseGeometry, blocked: BaseGeometry, min_area_m2: float, weld_m: float
) -> list[Polygon]:
    if corridors.is_empty:
        return []
    area = corridors.difference(_clearance(blocked, weld_m))
    return [p for p in polygons_of(_weld(area, weld_m)) if p.area >= min_area_m2]


def _water_areas(
    waters: BaseGeometry, blocked: BaseGeometry, min_area_m2: float, weld_m: float
) -> list[Polygon]:
    if waters.is_empty:
        return []
    area = waters.difference(_clearance(blocked, weld_m))
    return [p for p in polygons_of(_weld(area, weld_m)) if p.area >= min_area_m2]


def prepare(features: Features, spec: FrameSpec) -> Prepared:
    square = square_local(spec)
    scale = spec.scale
    tol_m = SIMPLIFY_TOLERANCE_MM / scale
    half_feature_m = MIN_FEATURE_MM / 2 / scale
    min_area_m2 = spec.min_footprint_area_mm2 / scale**2
    close_m = MIN_FEATURE_MM / 2 / scale

    # The type/area estimate is a property of the building, not of the cut-out, so it runs on
    # the projected but still unclipped footprints: a building sliced by the edge of the square
    # must not shrink into a lower area class, and a MultiPolygon must be estimated once rather
    # than once per lobe (spec §6.2). The estimate is filled in place, so prepare works on
    # shallow copies and leaves the caller's Buildings alone.
    buildings = [replace(b) for b in features.buildings]
    estimate_missing_heights(buildings)
    # spec.lod2 is checked in fetch as well; checking it here too means a caller that hands in
    # LoD2 data with the flag off (the pipeline tests do exactly that) gets the OSM-only model.
    lod2 = lod2_buildings(features) if spec.lod2 else []
    displaced: list[BaseGeometry] = []
    if lod2:
        buildings, dropped = drop_covered(buildings, lod2)
        # Clipped to the square like every other footprint, so the coverage denominator only ever
        # holds building area that was inside the model in the first place.
        # area > 0, not "not empty": a displaced footprint that only touches the edge of the
        # square clips to a LineString, and that is not area — same guard polygons_of uses.
        displaced = [g for g in (d.intersection(square) for d in dropped) if g.area > 0]
    clipped = _clip(buildings + lod2, square, tol_m)
    if not spec.parts:
        clipped = [b for b in clipped if not b.is_part]
    if not spec.roofs:
        for b in clipped:
            b.roof = None
    footprints = assign_parts(clipped, spec.default_building_height_m)
    # Counted on footprints, not on `buildings` below: a LoD2 footprint that only feeds a block
    # still puts official geometry into the model, so the source is truthfully named for it.
    lod2_footprints = sum(1 for b in footprints if b.lod2)
    for b in footprints:
        resolve_roof(b, spec.rotation_deg)

    full = spec.mode == Mode.full
    # Only the full mode has roads and water at all; in simple mode nothing is subtracted and
    # the close is free to weld across both (spec §6.4).
    corridors = road_corridors(features.roads, spec, square) if full else Polygon()
    waters = water_area(features.water, square) if full else Polygon()
    blocks = build_blocks(footprints, spec, close_m, min_area_m2, half_feature_m, corridors, waters, square)
    buildings = [b for b in footprints if _is_printable(b.geom, min_area_m2, half_feature_m)]
    # Only now is it clear which footprints get their own solid; everything else goes into a
    # block and would never use a body (spec §5).
    lod2_rejected = solidify_buildings(buildings)
    coverage = _coverage(footprints, blocks, buildings, displaced)
    if not full:
        return Prepared(
            buildings=buildings,
            blocks=blocks,
            footprint_coverage=coverage,
            lod2_rejected=lod2_rejected,
            lod2_footprints=lod2_footprints,
        )

    recess_min_area_m2 = MIN_FEATURE_MM**2 / scale**2
    weld_m = SIMPLIFY_TOLERANCE_MM / scale
    # Precedence stays buildings > roads > water. What blocks a pocket is the union of the
    # blocks and of the individually printable buildings: the blocks stop at the corridors,
    # but a building that sticks into one still keeps its ground (spec §6.4/§6.7).
    blocked = unary_union([*(b.geom for b in blocks), *(b.geom for b in buildings)])
    roads = _road_areas(corridors, blocked, recess_min_area_m2, weld_m)
    blocked_for_water = unary_union([blocked, *roads])
    water = _water_areas(waters, blocked_for_water, recess_min_area_m2, weld_m)
    return Prepared(
        buildings=buildings,
        blocks=blocks,
        roads=roads,
        water=water,
        footprint_coverage=coverage,
        lod2_rejected=lod2_rejected,
        lod2_footprints=lod2_footprints,
    )
