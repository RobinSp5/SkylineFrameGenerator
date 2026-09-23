from datetime import date

from skylineframe.lod2.sources import ATTRIBUTIONS, HESSEN, OSM, SOURCES_FILENAME, sources_text


def test_filename_and_registry():
    assert SOURCES_FILENAME == "SOURCES.txt"
    assert ATTRIBUTIONS["hessen"] is HESSEN


def test_osm_only_text():
    text = sources_text("2026-09-23")
    assert text.startswith("Geometrie erzeugt mit Skyline Frame Generator am 2026-09-23.\n")
    assert "© OpenStreetMap-Mitwirkende, ODbL (https://www.openstreetmap.org/copyright)." in text
    assert "Beim Verkauf von Drucken ist die OpenStreetMap-Namensnennung anzubringen." in text
    assert "LoD2" not in text
    assert text.endswith("\n")


def test_hessen_text_is_the_block_from_the_spec():
    text = sources_text("2026-09-23", HESSEN)
    assert text == (
        "Geometrie erzeugt mit Skyline Frame Generator am 2026-09-23.\n"
        "Gebäude: 3D-Gebäudemodell LoD2 Hessen, Hessische Verwaltung für Bodenmanagement und Geoinformation,\n"
        "         Datenlizenz Deutschland – Zero – Version 2.0 (https://www.govdata.de/dl-de/zero-2-0).\n"
        "Straßen, Wasser, Grundrisse: © OpenStreetMap-Mitwirkende, ODbL (https://www.openstreetmap.org/copyright).\n"
        "Beim Verkauf von Drucken ist die OpenStreetMap-Namensnennung anzubringen.\n"
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
