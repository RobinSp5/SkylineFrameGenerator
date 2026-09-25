import pytest
from shapely.geometry import box

from skylineframe.errors import PipelineError
from skylineframe.features import Features, OvertureBuilding
from skylineframe.fetch import parse_overpass
from skylineframe.pipeline import run
from skylineframe.spec import FrameSpec, Mode


def test_run_frankfurt_offline(tmp_path, frankfurt_spec, frankfurt_data):
    stages: list[str] = []
    result = run(
        frankfurt_spec,
        out_dir=tmp_path / "out",
        cache_dir=tmp_path / "cache",
        progress=lambda stage, msg: stages.append(stage),
        fetch=lambda spec, cache_dir: parse_overpass(frankfurt_data, spec),
    )
    assert stages == ["fetch", "prepare", "mesh", "export"]
    assert result.paths.stl.exists() and result.paths.threemf.exists() and result.paths.glb.exists()
    assert result.stats["buildings"] > 20
    assert result.stats["buildings_individual"] == result.stats["buildings"]
    assert result.stats["blocks"] >= 1
    assert result.stats["roofs"] > 0  # the fixture has 60 roof:shape buildings
    assert result.stats["roads"] >= 1
    # The blocks are built around the road corridors, so the grooves survive (spec §6.4).
    assert result.stats["road_area_mm2"] > 100
    assert result.stats["stl_bytes"] > 10_000
    # Spec §6: the model must carry at least 95 % of the building area in the square.
    assert result.stats["footprint_coverage"] >= 0.95


def test_run_passes_the_name_to_the_export(tmp_path, frankfurt_spec, frankfurt_data):
    spec = frankfurt_spec.model_copy(update={"mode": Mode.simple})
    result = run(
        spec,
        tmp_path / "out",
        tmp_path / "cache",
        fetch=lambda s, c: parse_overpass(frankfurt_data, s),
        name="Frankfurt am Main – Altstadt",
    )
    assert result.paths.stl.read_bytes().startswith(b"Skyline Frame Frankfurt-am-Main_Altstadt")
    # Files on disk keep their stable names; the place name is applied at download time.
    assert result.paths.stl.name == "model.stl"


def test_run_bankenviertel_has_building_parts(tmp_path, bankenviertel_spec, bankenviertel_data):
    result = run(
        bankenviertel_spec,
        tmp_path / "out",
        tmp_path / "cache",
        fetch=lambda s, c: parse_overpass(bankenviertel_data, s),
    )
    assert result.stats["parts"] > 0
    assert result.stats["footprint_coverage"] >= 0.95


def test_run_without_roofs_and_parts(tmp_path, bankenviertel_spec, bankenviertel_data):
    spec = bankenviertel_spec.model_copy(update={"roofs": False, "parts": False})
    result = run(spec, tmp_path / "out", tmp_path / "cache", fetch=lambda s, c: parse_overpass(bankenviertel_data, s))
    assert result.stats["roofs"] == 0
    assert result.stats["parts"] == 0
    assert result.stats["buildings"] > 0


def test_run_simple_mode_has_no_roads(tmp_path, frankfurt_spec, frankfurt_data):
    spec = frankfurt_spec.model_copy(update={"mode": Mode.simple})
    result = run(spec, tmp_path / "out", tmp_path / "cache", fetch=lambda s, c: parse_overpass(frankfurt_data, s))
    assert result.stats["roads"] == 0 and result.stats["water"] == 0


def test_run_without_buildings_raises_pipeline_error(tmp_path):
    # terrain=False: this only tests the empty-fetch path, and terrain on would reach for a real
    # DEM before ever getting there since no `terrain` callable is injected.
    spec = FrameSpec(center_lat=0, center_lon=0, terrain=False)
    with pytest.raises(PipelineError, match="No buildings"):
        run(spec, tmp_path / "out", tmp_path / "cache", fetch=lambda s, c: Features())


def lod2_features(spec, data, boxes):
    """The parsed OSM fixture plus hand-made LoD2 models, as fetch would deliver them."""
    feats = parse_overpass(data, spec)
    feats.lod2 = list(boxes)
    feats.lod2_source = "hessen"
    return feats


def wgs84_box(lat, lon, size_deg, z0, z1, osm_id, closed=True, size_deg_lat=None):
    """A box as separate faces in WGS84; closed=False leaves the roof off and cannot be solidified.

    size_deg is the extent in longitude and, unless size_deg_lat says otherwise, in latitude too.
    """
    from skylineframe.features import Lod2Building

    x0, y0, x1, y1 = lon, lat, lon + size_deg, lat + (size_deg if size_deg_lat is None else size_deg_lat)
    bottom = ((x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0))
    top = ((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))
    walls = (
        ((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)),
        ((x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)),
        ((x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1)),
        ((x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1)),
    )
    return Lod2Building(osm_id=osm_id, surfaces=(bottom, top, *walls) if closed else (bottom, *walls))


def test_run_with_lod2_reports_the_source_and_stays_watertight(tmp_path, frankfurt_spec, frankfurt_data):
    # Two 0.0005 deg (~35 m) towers next to the Römer, 40 m and 60 m above their ground.
    boxes = [
        wgs84_box(50.1090, 8.6820, 0.0005, 100.0, 140.0, "lod2/hessen/A"),
        wgs84_box(50.1094, 8.6826, 0.0005, 100.0, 160.0, "lod2/hessen/B"),
    ]
    result = run(
        frankfurt_spec,
        tmp_path / "out",
        tmp_path / "cache",
        fetch=lambda s, c: lod2_features(s, frankfurt_data, boxes),
    )
    assert result.stats["lod2_source"] == "hessen"
    assert result.stats["lod2_buildings"] == 2
    assert result.stats["lod2_rejected"] == 0
    assert result.stats["lod2_triangles"] > 0
    assert result.stats["footprint_coverage"] >= 0.95
    assert result.paths.sources.read_text(encoding="utf-8").count("LoD2 Hessen") == 1
    # export_all verifies the union and raises ExportError when it is not watertight, so a run
    # that gets this far is the watertight guarantee. nonmanifold_edges is not asserted to be
    # zero: it is a report, and a real city is above zero either way because neighbouring
    # buildings legitimately share an edge once a slicer merges their vertices. What matters is
    # that a body does not make the mesh worse than the prisms it replaced.
    osm_only = run(
        frankfurt_spec, tmp_path / "osm", tmp_path / "cache", fetch=lambda s, c: parse_overpass(frankfurt_data, s)
    )
    assert result.stats["nonmanifold_edges"] <= osm_only.stats["nonmanifold_edges"]
    assert result.stats["degenerate_faces"] <= osm_only.stats["degenerate_faces"]


def test_lod2_buildings_counts_only_the_ones_with_a_real_body(tmp_path, frankfurt_spec, frankfurt_data):
    # An open box has a footprint and a height but no closable body: it prints as a prism at the
    # right height and must not be reported as an LoD2 building, because the CLI and the status
    # line promise roof geometry for that number (spec §8).
    boxes = [
        wgs84_box(50.1090, 8.6820, 0.0005, 100.0, 140.0, "lod2/hessen/A"),
        wgs84_box(50.1094, 8.6826, 0.0005, 100.0, 160.0, "lod2/hessen/B", closed=False),
    ]
    result = run(
        frankfurt_spec,
        tmp_path / "out",
        tmp_path / "cache",
        fetch=lambda s, c: lod2_features(s, frankfurt_data, boxes),
    )
    assert result.stats["lod2_buildings"] == 1
    assert result.stats["lod2_rejected"] == 1
    assert result.stats["footprint_coverage"] >= 0.95


def test_run_without_lod2_data_reports_an_empty_source(tmp_path, frankfurt_spec, frankfurt_data):
    result = run(
        frankfurt_spec,
        tmp_path / "out",
        tmp_path / "cache",
        fetch=lambda s, c: parse_overpass(frankfurt_data, s),
    )
    assert result.stats["lod2_source"] == ""
    assert result.stats["lod2_buildings"] == 0
    assert result.stats["lod2_triangles"] == 0
    assert "LoD2" not in result.paths.sources.read_text(encoding="utf-8")


def test_lod2_data_that_never_reaches_the_model_does_not_name_the_source(
    tmp_path, frankfurt_spec, frankfurt_data
):
    # The provider answered, but nothing of it is in the model, so SOURCES.txt must not name it
    # (spec §7). The three ways a model falls out, one box each:
    #   OUTSIDE  the query box carries a 100 m margin, so a model can sit entirely outside the
    #            square and be clipped away (the 400 m fixture square reaches to about 50.1108,
    #            this box starts at 50.1115);
    #   FLAT     a footprint is fine but the height span is zero, so lod2_buildings skips it;
    #   FLAT_2D  a 2D response: the parser drops every ring on srsDimension="2", so the model
    #            arrives with no surfaces and has no footprint to derive at all.
    from skylineframe.features import Lod2Building

    boxes = [
        wgs84_box(50.1115, 8.6820, 0.0005, 100.0, 140.0, "lod2/hessen/OUTSIDE"),
        wgs84_box(50.1090, 8.6826, 0.0005, 100.0, 100.0, "lod2/hessen/FLAT"),
        Lod2Building(osm_id="lod2/hessen/FLAT_2D", surfaces=()),
    ]
    result = run(
        frankfurt_spec,
        tmp_path / "out",
        tmp_path / "cache",
        fetch=lambda s, c: lod2_features(s, frankfurt_data, boxes),
    )
    assert result.stats["lod2_source"] == ""
    assert result.stats["lod2_buildings"] == 0
    assert "Hessen" not in result.paths.sources.read_text(encoding="utf-8")
    # And it really is the OpenStreetMap-only model, not just a quieter label on a different one.
    plain = run(
        frankfurt_spec, tmp_path / "plain", tmp_path / "cache", fetch=lambda s, c: parse_overpass(frankfurt_data, s)
    )
    assert result.paths.stl.read_bytes() == plain.paths.stl.read_bytes()


def test_lod2_geometry_that_only_feeds_a_block_still_names_the_source(
    tmp_path, frankfurt_spec, frankfurt_data
):
    # The source is named for LoD2 geometry that reaches the model at all, not only for the
    # buildings that get their own body (spec §7). A kiosk of about 1 x 1.2 m is 0.075 mm² at this
    # scale, below TINY_FOOTPRINT_MM2, so it carries no body and only feeds its block
    # (spec 4a §2.1). Its geometry is still in the print, so naming Hessen is truthful, and
    # leaving it unnamed would be the false negative.
    sliver = wgs84_box(50.1090, 8.6822, 0.000014, 100.0, 118.0, "lod2/hessen/SLIVER", size_deg_lat=0.0000108)
    result = run(
        frankfurt_spec,
        tmp_path / "out",
        tmp_path / "cache",
        fetch=lambda s, c: lod2_features(s, frankfurt_data, [sliver]),
    )
    assert result.stats["lod2_buildings"] == 0
    assert result.stats["lod2_source"] == "hessen"
    assert "LoD2 Hessen" in result.paths.sources.read_text(encoding="utf-8")


def test_lod2_off_gives_the_same_model_as_no_lod2_data(tmp_path, frankfurt_spec, frankfurt_data):
    # Spec §11: with the flag off the result is bit-identical to the OSM-only run, even when the
    # data is right there.
    boxes = [wgs84_box(50.1090, 8.6820, 0.0005, 100.0, 140.0, "lod2/hessen/A")]
    off = frankfurt_spec.model_copy(update={"lod2": False})
    with_flag_off = run(
        off, tmp_path / "a", tmp_path / "cache", fetch=lambda s, c: lod2_features(s, frankfurt_data, boxes)
    )
    plain = run(
        frankfurt_spec, tmp_path / "b", tmp_path / "cache", fetch=lambda s, c: parse_overpass(frankfurt_data, s)
    )
    assert with_flag_off.paths.stl.read_bytes() == plain.paths.stl.read_bytes()
    assert with_flag_off.stats["lod2_buildings"] == 0
    # The reported source and SOURCES.txt have to go quiet as well: the data was in the fetch
    # result, and naming Hessen next to a model that carries none of it is a false attribution.
    assert with_flag_off.stats["lod2_source"] == ""
    assert "Hessen" not in with_flag_off.paths.sources.read_text(encoding="utf-8")


# --- Overture -------------------------------------------------------------------------------


def overture_features(spec, data, buildings):
    """The parsed OSM fixture plus hand-made Overture records, as fetch would deliver them."""
    feats = parse_overpass(data, spec)
    feats.overture = list(buildings)
    feats.overture_source = "overture"
    return feats


def test_run_with_overture_reports_the_source_and_stays_watertight(tmp_path, frankfurt_spec, frankfurt_data):
    # Well clear of every Römer fixture building, so this is purely additional geometry.
    building = OvertureBuilding(osm_id="overture/x/A", geom=box(8.6810, 50.1102, 8.6814, 50.1106), height_m=15.0)
    spec = frankfurt_spec.model_copy(update={"overture": True})
    result = run(
        spec,
        tmp_path / "out",
        tmp_path / "cache",
        fetch=lambda s, c: overture_features(s, frankfurt_data, [building]),
    )
    assert result.stats["overture_source"] == "overture"
    assert result.stats["overture_buildings"] == 1
    assert result.paths.sources.read_text(encoding="utf-8").count("Overture Maps Foundation") == 1


def test_run_without_overture_data_reports_an_empty_source(tmp_path, frankfurt_spec, frankfurt_data):
    spec = frankfurt_spec.model_copy(update={"overture": True})
    result = run(spec, tmp_path / "out", tmp_path / "cache", fetch=lambda s, c: parse_overpass(frankfurt_data, s))
    assert result.stats["overture_source"] == ""
    assert result.stats["overture_buildings"] == 0
    assert "Overture" not in result.paths.sources.read_text(encoding="utf-8")


def test_overture_data_that_never_reaches_the_model_does_not_name_the_source(tmp_path, frankfurt_spec, frankfurt_data):
    # Outside the query box's own 100 m margin, so it clips away entirely (mirrors LoD2's OUTSIDE case).
    outside = OvertureBuilding(osm_id="overture/x/OUTSIDE", geom=box(8.6820, 50.1200, 8.6825, 50.1205), height_m=15.0)
    spec = frankfurt_spec.model_copy(update={"overture": True})
    result = run(
        spec,
        tmp_path / "out",
        tmp_path / "cache",
        fetch=lambda s, c: overture_features(s, frankfurt_data, [outside]),
    )
    assert result.stats["overture_source"] == ""
    assert "Overture" not in result.paths.sources.read_text(encoding="utf-8")
    plain = run(
        frankfurt_spec, tmp_path / "plain", tmp_path / "cache", fetch=lambda s, c: parse_overpass(frankfurt_data, s)
    )
    assert result.paths.stl.read_bytes() == plain.paths.stl.read_bytes()


def test_overture_off_gives_the_same_model_as_no_overture_data(tmp_path, frankfurt_spec, frankfurt_data):
    building = OvertureBuilding(osm_id="overture/x/A", geom=box(8.6810, 50.1102, 8.6814, 50.1106), height_m=15.0)
    off = frankfurt_spec.model_copy(update={"overture": False})
    with_flag_off = run(
        off, tmp_path / "a", tmp_path / "cache", fetch=lambda s, c: overture_features(s, frankfurt_data, [building])
    )
    plain = run(
        frankfurt_spec, tmp_path / "b", tmp_path / "cache", fetch=lambda s, c: parse_overpass(frankfurt_data, s)
    )
    assert with_flag_off.paths.stl.read_bytes() == plain.paths.stl.read_bytes()
    assert with_flag_off.stats["overture_source"] == ""
    assert "Overture" not in with_flag_off.paths.sources.read_text(encoding="utf-8")


def test_multicolor_with_a_mix_of_lod2_and_other_buildings_yields_six_named_parts(
    tmp_path, frankfurt_spec, frankfurt_data
):
    import trimesh

    lod2_boxes = [wgs84_box(50.1090, 8.6820, 0.0005, 100.0, 140.0, "lod2/hessen/A")]
    # Clear of the LoD2 tower above, so both reach the model as separate footprints.
    overture_bldg = OvertureBuilding(osm_id="overture/x/1", geom=box(8.6810, 50.1102, 8.6814, 50.1106), height_m=15.0)

    def fetch(s, c):
        feats = parse_overpass(frankfurt_data, s)
        feats.lod2, feats.lod2_source = list(lod2_boxes), "hessen"
        feats.overture, feats.overture_source = [overture_bldg], "overture"
        return feats

    spec = frankfurt_spec.model_copy(update={"multicolor": True, "lod2": True, "overture": True, "trees": True})
    layer, _ = tree_layer_fake(tree_grid())
    result = run(spec, tmp_path / "out", tmp_path / "cache", fetch=fetch, trees=layer)
    assert result.stats["lod2_buildings"] == 1
    assert result.stats["overture_buildings"] == 1
    scene = trimesh.load(result.paths.threemf, file_type="3mf")
    assert set(scene.geometry) == {"base", "buildings", "buildings_verified", "roads", "water", "trees"}


def test_multicolor_stays_at_five_parts_when_overture_and_lod2_find_nothing(tmp_path, frankfurt_spec, frankfurt_data):
    # Same request (multicolor, lod2 and overture all on) but no official or Overture data
    # actually reaches the model: graceful degradation, no empty buildings_verified part.
    import trimesh

    spec = frankfurt_spec.model_copy(update={"multicolor": True, "lod2": True, "overture": True, "trees": True})
    layer, _ = tree_layer_fake(tree_grid())
    result = run(
        spec, tmp_path / "out", tmp_path / "cache", fetch=lambda s, c: parse_overpass(frankfurt_data, s), trees=layer
    )
    assert result.stats["lod2_buildings"] == 0 and result.stats["overture_buildings"] == 0
    scene = trimesh.load(result.paths.threemf, file_type="3mf")
    assert set(scene.geometry) == {"base", "buildings", "roads", "water", "trees"}


# --- terrain (spec 4b §5.6) --------------------------------------------------------------------


def tilted(spec: FrameSpec, relief_mm: float = 4.0):
    """A plane rising diagonally across the plate, lowest corner exactly 0 as the contract says."""
    import numpy as np

    from skylineframe.terrain.heightfield import Heightfield

    flat = Heightfield.flat(spec.plate_size_mm)
    n = flat.z_mm.shape[0]
    t = np.arange(n) / (n - 1)
    return Heightfield(relief_mm * (t[None, :] + t[:, None]) / 2, flat.cell_mm, flat.origin_mm)


def test_run_on_terrain_reports_the_relief_and_credits_copernicus(tmp_path, frankfurt_spec, frankfurt_data):
    import trimesh

    spec = frankfurt_spec.model_copy(update={"terrain": True})
    calls = []

    def terrain(s, cache_dir):
        calls.append(cache_dir)
        return tilted(s)

    stages: list[str] = []
    result = run(
        spec,
        tmp_path / "out",
        tmp_path / "cache",
        progress=lambda stage, msg: stages.append(stage),
        fetch=lambda s, c: parse_overpass(frankfurt_data, s),
        terrain=terrain,
    )
    assert calls == [tmp_path / "cache"]
    assert stages == ["fetch", "terrain", "prepare", "mesh", "export"]
    assert result.stats["terrain_source"] == "copernicus"
    assert result.stats["terrain_relief_mm"] == pytest.approx(4.0)
    assert "terrain_note" not in result.stats
    sources = result.paths.sources.read_text(encoding="utf-8")
    assert sources.count("Copernicus WorldDEM-30") == 2
    assert sources.index("Copernicus") < sources.index("OpenStreetMap")
    # export_all verified the manifold topology, so getting here is the watertight guarantee; the
    # reloaded STL merges touching buildings exactly like the flat model does (see the LoD2 test).
    stl = trimesh.load(result.paths.stl)
    assert stl.extents[:2] == pytest.approx([spec.plate_size_mm, spec.plate_size_mm], abs=0.01)
    assert stl.bounds[0][2] == pytest.approx(-spec.plate_thickness_mm, abs=1e-4)  # flat bottom
    assert stl.bounds[1][2] > 4.0  # the relief is in the print


def test_terrain_that_cannot_be_loaded_gives_the_flat_model_and_a_note(tmp_path, frankfurt_spec, frankfurt_data):
    # Spec 4b §5.6/§6: offline with terrain on is a flat model and a note, never an abort.
    spec = frankfurt_spec.model_copy(update={"terrain": True})
    offline = run(
        spec,
        tmp_path / "a",
        tmp_path / "cache",
        fetch=lambda s, c: parse_overpass(frankfurt_data, s),
        terrain=lambda s, c: None,
    )
    plain = run(
        frankfurt_spec, tmp_path / "b", tmp_path / "cache", fetch=lambda s, c: parse_overpass(frankfurt_data, s)
    )
    assert offline.stats["terrain_source"] == ""
    assert offline.stats["terrain_note"] == "Gelände nicht verfügbar"
    assert "terrain_relief_mm" not in offline.stats
    assert offline.paths.stl.read_bytes() == plain.paths.stl.read_bytes()
    assert "Copernicus" not in offline.paths.sources.read_text(encoding="utf-8")


def test_terrain_off_never_asks_for_a_heightfield_and_is_byte_identical(tmp_path, frankfurt_spec, frankfurt_data):
    def must_not_run(s, c):
        raise AssertionError("terrain loaded although spec.terrain is False")

    with_param = run(
        frankfurt_spec,
        tmp_path / "a",
        tmp_path / "cache",
        fetch=lambda s, c: parse_overpass(frankfurt_data, s),
        terrain=must_not_run,
    )
    without = run(
        frankfurt_spec, tmp_path / "b", tmp_path / "cache", fetch=lambda s, c: parse_overpass(frankfurt_data, s)
    )
    assert with_param.paths.stl.read_bytes() == without.paths.stl.read_bytes()
    assert with_param.stats["terrain_source"] == ""
    assert "terrain_note" not in with_param.stats
    assert "Copernicus" not in with_param.paths.sources.read_text(encoding="utf-8")


# --- trees (spec 6 §5) -------------------------------------------------------------------------


def tree_grid(step: float = 3.0) -> list:
    """A grid of 2 mm trees over the whole 100 mm plate; the pipeline keeps the ones that are clear."""
    from skylineframe.trees.model import Tree

    coords = [-48.0 + step * k for k in range(int(96 / step) + 1)]
    return [Tree(x, y, 2.0, 1.5) for x in coords for y in coords]


def tree_layer_fake(trees, source="worldcover", **extra):
    """A stand-in for trees.layer.tree_layer, which the data half provides, and the calls it got."""
    from types import SimpleNamespace

    calls = []

    def layer(spec, osm_trees, cache_dir):
        calls.append((list(osm_trees), cache_dir))
        return SimpleNamespace(trees=list(trees), source=source, **extra)

    return layer, calls


def test_run_with_trees_prints_them_as_their_own_part(tmp_path, frankfurt_spec, frankfurt_data):
    import trimesh

    spec = frankfurt_spec.model_copy(update={"trees": True})
    layer, calls = tree_layer_fake(tree_grid())
    stages: list[str] = []
    result = run(
        spec,
        tmp_path / "out",
        tmp_path / "cache",
        progress=lambda stage, msg: stages.append(stage),
        fetch=lambda s, c: parse_overpass(frankfurt_data, s),
        trees=layer,
    )
    assert stages == ["fetch", "prepare", "trees", "mesh", "export"]
    assert calls == [([], tmp_path / "cache")]  # the fixture has no OSM trees
    # The Römer square is dense: most of the grid stands on a house, a block or a road.
    assert 0 < result.stats["trees"] < len(tree_grid())
    assert result.stats["trees_source"] == "worldcover"
    assert "trees_note" not in result.stats
    assert "ESA WorldCover" in result.paths.sources.read_text(encoding="utf-8")
    scene = trimesh.load(result.paths.threemf, file_type="3mf")
    assert "trees" in scene.geometry
    plain = run(
        frankfurt_spec, tmp_path / "plain", tmp_path / "cache", fetch=lambda s, c: parse_overpass(frankfurt_data, s)
    )
    assert result.stats["stl_bytes"] > plain.stats["stl_bytes"]


def test_multicolor_reaches_the_3mf_and_the_stats(tmp_path, frankfurt_spec, frankfurt_data):
    import zipfile

    fetch = lambda s, c: parse_overpass(frankfurt_data, s)  # noqa: E731
    plain = run(frankfurt_spec, tmp_path / "plain", tmp_path / "cache", fetch=fetch)
    colour = run(frankfurt_spec.model_copy(update={"multicolor": True}), tmp_path / "colour", tmp_path / "cache", fetch=fetch)
    assert plain.stats["multicolor"] is False and colour.stats["multicolor"] is True
    with zipfile.ZipFile(plain.paths.threemf) as z:
        assert "Metadata/project_settings.config" not in z.namelist()
    with zipfile.ZipFile(colour.paths.threemf) as z:
        assert "Metadata/project_settings.config" in z.namelist()
    assert plain.paths.stl.read_bytes() == colour.paths.stl.read_bytes()


def test_osm_trees_reach_the_tree_layer_in_local_metres(tmp_path, frankfurt_spec, frankfurt_data, monkeypatch):
    # The data half adds Features.trees; until then the pipeline reads it with a default.
    from skylineframe import pipeline
    from skylineframe.trees.model import OsmTree

    projected = pipeline.project_features

    def with_trees(features, spec):
        out = projected(features, spec)
        out.trees = [OsmTree(1.0, 2.0, 12.0, 6.0)]
        return out

    monkeypatch.setattr(pipeline, "project_features", with_trees)
    layer, calls = tree_layer_fake([])
    run(
        frankfurt_spec.model_copy(update={"trees": True}),
        tmp_path / "out",
        tmp_path / "cache",
        fetch=lambda s, c: parse_overpass(frankfurt_data, s),
        trees=layer,
    )
    assert calls == [([OsmTree(1.0, 2.0, 12.0, 6.0)], tmp_path / "cache")]


def test_trees_off_never_asks_for_trees_and_is_byte_identical(tmp_path, frankfurt_spec, frankfurt_data):
    def must_not_run(s, osm, c):
        raise AssertionError("trees loaded although spec.trees is False")

    off = run(
        frankfurt_spec.model_copy(update={"trees": False}),
        tmp_path / "a",
        tmp_path / "cache",
        fetch=lambda s, c: parse_overpass(frankfurt_data, s),
        trees=must_not_run,
    )
    # Trees asked for but none left to print: the same model, byte for byte.
    layer, _ = tree_layer_fake([], source="")
    empty = run(
        frankfurt_spec.model_copy(update={"trees": True}),
        tmp_path / "b",
        tmp_path / "cache",
        fetch=lambda s, c: parse_overpass(frankfurt_data, s),
        trees=layer,
    )
    assert off.paths.stl.read_bytes() == empty.paths.stl.read_bytes()
    assert off.stats["trees"] == 0 and off.stats["trees_source"] == ""
    assert "trees_note" not in off.stats
    assert "WorldCover" not in off.paths.sources.read_text(encoding="utf-8")


def test_worldcover_is_only_credited_when_its_trees_reach_the_print(tmp_path, frankfurt_spec, frankfurt_data):
    from skylineframe.trees.model import Tree

    spec = frankfurt_spec.model_copy(update={"trees": True})
    # Off the plate: fitted away, so nothing of WorldCover is in the model.
    layer, _ = tree_layer_fake([Tree(500.0, 500.0, 2.0, 1.5)])
    result = run(spec, tmp_path / "a", tmp_path / "cache", fetch=lambda s, c: parse_overpass(frankfurt_data, s), trees=layer)
    assert result.stats["trees"] == 0
    assert result.stats["trees_source"] == ""
    assert "WorldCover" not in result.paths.sources.read_text(encoding="utf-8")
    # OSM trees only: printed, but not credited to WorldCover.
    layer, _ = tree_layer_fake(tree_grid(), source="")
    result = run(spec, tmp_path / "b", tmp_path / "cache", fetch=lambda s, c: parse_overpass(frankfurt_data, s), trees=layer)
    assert result.stats["trees"] > 0
    assert result.stats["trees_source"] == ""
    assert "WorldCover" not in result.paths.sources.read_text(encoding="utf-8")


def test_the_tree_layer_note_is_passed_on(tmp_path, frankfurt_spec, frankfurt_data):
    layer, _ = tree_layer_fake(tree_grid(), source="", note="WorldCover nicht verfügbar")
    result = run(
        frankfurt_spec.model_copy(update={"trees": True}),
        tmp_path / "out",
        tmp_path / "cache",
        fetch=lambda s, c: parse_overpass(frankfurt_data, s),
        trees=layer,
    )
    assert result.stats["trees_note"] == "WorldCover nicht verfügbar"


def test_without_an_injected_layer_the_data_half_is_imported_lazily(
    tmp_path, frankfurt_spec, frankfurt_data, monkeypatch
):
    import sys
    from types import ModuleType

    layer, calls = tree_layer_fake(tree_grid())
    module = ModuleType("skylineframe.trees.layer")
    module.tree_layer = layer
    monkeypatch.setitem(sys.modules, "skylineframe.trees.layer", module)
    result = run(
        frankfurt_spec.model_copy(update={"trees": True}),
        tmp_path / "out",
        tmp_path / "cache",
        fetch=lambda s, c: parse_overpass(frankfurt_data, s),
    )
    assert len(calls) == 1
    assert result.stats["trees"] > 0


def test_trees_on_terrain(tmp_path, frankfurt_spec, frankfurt_data):
    import trimesh

    spec = frankfurt_spec.model_copy(update={"trees": True, "terrain": True})
    layer, _ = tree_layer_fake(tree_grid())
    result = run(
        spec,
        tmp_path / "out",
        tmp_path / "cache",
        fetch=lambda s, c: parse_overpass(frankfurt_data, s),
        terrain=lambda s, c: tilted(s),
        trees=layer,
    )
    assert result.stats["trees"] > 0
    assert result.stats["nonmanifold_edges"] == 0
    stl = trimesh.load(result.paths.stl)
    assert stl.is_watertight
    assert stl.bounds[0][2] == pytest.approx(-spec.plate_thickness_mm, abs=1e-4)
