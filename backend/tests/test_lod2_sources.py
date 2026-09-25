from datetime import date

from skylineframe.lod2.sources import (
    ATTRIBUTIONS,
    COPERNICUS,
    HESSEN,
    OSM,
    SOURCES_FILENAME,
    WORLDCOVER,
    sources_text,
)


def test_filename_and_registry():
    assert SOURCES_FILENAME == "SOURCES.txt"
    assert ATTRIBUTIONS["hessen"] is HESSEN


def test_osm_only_text():
    text = sources_text("2026-09-23")
    assert text.startswith("Geometrie erzeugt mit Skyline Frame Generator am 2026-09-23.\n")
    assert "© OpenStreetMap-Mitwirkende, ODbL (https://www.openstreetmap.org/copyright)." in text
    assert "Bei Verkauf, Weitergabe oder Veröffentlichung von Drucken und Dateien ist die OpenStreetMap-Namensnennung anzubringen." in text
    assert "LoD2" not in text
    assert text.endswith("\n")


def test_hessen_text_is_the_block_from_the_spec():
    text = sources_text("2026-09-23", HESSEN)
    assert text == (
        "Geometrie erzeugt mit Skyline Frame Generator am 2026-09-23.\n"
        "Gebäude: 3D-Gebäudemodell LoD2 Hessen, Hessische Verwaltung für Bodenmanagement und Geoinformation,\n"
        "         Datenlizenz Deutschland – Zero – Version 2.0 (https://www.govdata.de/dl-de/zero-2-0).\n"
        "Straßen, Wasser, Grundrisse: © OpenStreetMap-Mitwirkende, ODbL (https://www.openstreetmap.org/copyright).\n"
        "Bei Verkauf, Weitergabe oder Veröffentlichung von Drucken und Dateien ist die OpenStreetMap-Namensnennung anzubringen.\n"
    )


def test_todays_date_is_accepted_verbatim():
    today = date.today().isoformat()
    assert today in sources_text(today, HESSEN)


def test_every_attribution_is_reachable_by_its_provider_name():
    # sources_text is fed with ATTRIBUTIONS[stats["lod2_source"]]; a provider without an entry
    # would silently print an OSM-only file for data that is not OSM.
    for name, attribution in ATTRIBUTIONS.items():
        assert attribution.name == name
        assert attribution.text.strip()


COPERNICUS_TEXT = (
    "Gelände: produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH "
    "2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved. The organisations in "
    "charge of the Copernicus programme by law or by delegation do not incur any liability for any use of the "
    "Copernicus WorldDEM-30."
)


def test_copernicus_text_is_the_wording_of_the_licence():
    # Spec 4b §4.8: the processed-data notice and the liability sentence, verbatim.
    assert COPERNICUS.name == "copernicus"
    assert COPERNICUS.text == COPERNICUS_TEXT
    # Not an LoD2 provider: the pipeline looks stats["lod2_source"] up in ATTRIBUTIONS.
    assert "copernicus" not in ATTRIBUTIONS


def test_terrain_block_sits_between_lod2_and_osm():
    text = sources_text("2026-09-24", HESSEN, terrain=COPERNICUS)
    assert text.index("LoD2 Hessen") < text.index("Copernicus WorldDEM-30") < text.index("OpenStreetMap")
    assert COPERNICUS_TEXT + "\n" + OSM.text + "\n" in text
    assert "Copernicus" not in sources_text("2026-09-24", HESSEN)
    assert sources_text("2026-09-24", terrain=COPERNICUS) == (
        "Geometrie erzeugt mit Skyline Frame Generator am 2026-09-24.\n" + COPERNICUS_TEXT + "\n" + OSM.text + "\n"
    )


WORLDCOVER_TEXT = (
    "Bäume: © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA "
    "WorldCover consortium, CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)."
)


def test_worldcover_text_is_the_line_from_the_spec():
    # Spec 6 §2: the mandatory CC BY 4.0 line, verbatim, and never a building source.
    assert WORLDCOVER.name == "worldcover"
    assert WORLDCOVER.text == WORLDCOVER_TEXT
    assert "worldcover" not in ATTRIBUTIONS


def test_overture_block_sits_between_lod2_and_terrain():
    from skylineframe.overture.sources import OVERTURE

    text = sources_text("2026-09-24", HESSEN, overture=OVERTURE, terrain=COPERNICUS)
    assert text.index("LoD2 Hessen") < text.index("Overture Maps Foundation") < text.index("WorldDEM-30")
    assert sources_text("2026-09-24", overture=OVERTURE) == (
        "Geometrie erzeugt mit Skyline Frame Generator am 2026-09-24.\n" + OVERTURE.text + "\n" + OSM.text + "\n"
    )
    assert "Overture" not in sources_text("2026-09-24", HESSEN)


def test_trees_block_sits_after_the_terrain_and_before_osm():
    text = sources_text("2026-09-24", HESSEN, terrain=COPERNICUS, trees=WORLDCOVER)
    assert text.index("LoD2 Hessen") < text.index("WorldDEM-30") < text.index("WorldCover") < text.index("OpenStreetMap")
    assert sources_text("2026-09-24", trees=WORLDCOVER) == (
        "Geometrie erzeugt mit Skyline Frame Generator am 2026-09-24.\n" + WORLDCOVER_TEXT + "\n" + OSM.text + "\n"
    )
    assert "WorldCover" not in sources_text("2026-09-24", HESSEN, terrain=COPERNICUS)
