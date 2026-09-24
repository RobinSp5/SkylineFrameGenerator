import pytest

from skylineframe.errors import PipelineError
from skylineframe.features import Features
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
    spec = FrameSpec(center_lat=0, center_lon=0)
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
