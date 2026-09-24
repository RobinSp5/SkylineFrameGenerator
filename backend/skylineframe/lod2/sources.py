"""Attribution and licence text per data source (spec §7).

One place for every wording. The pipeline writes SOURCES.txt from here, so a licence change is a
one-line edit rather than a hunt through the code.
"""

from dataclasses import dataclass

SOURCES_FILENAME = "SOURCES.txt"
HEADER = "Geometrie erzeugt mit Skyline Frame Generator am {date}."


@dataclass(frozen=True)
class Attribution:
    name: str  # provider name as it appears in stats["lod2_source"]
    text: str  # the block of lines this source contributes to SOURCES.txt, without a trailing newline


HESSEN = Attribution(
    name="hessen",
    text=(
        "Gebäude: 3D-Gebäudemodell LoD2 Hessen, Hessische Verwaltung für Bodenmanagement und Geoinformation,\n"
        "         Datenlizenz Deutschland – Zero – Version 2.0 (https://www.govdata.de/dl-de/zero-2-0)."
    ),
)

# OpenStreetMap is always in the model: roads, water and every footprint outside the LoD2 coverage.
OSM = Attribution(
    name="osm",
    text=(
        "Straßen, Wasser, Grundrisse: © OpenStreetMap-Mitwirkende, ODbL (https://www.openstreetmap.org/copyright).\n"
        "Bei Verkauf, Weitergabe oder Veröffentlichung von Drucken und Dateien ist die OpenStreetMap-Namensnennung anzubringen."
    ),
)

# provider name -> attribution; pipeline looks the used source up here.
ATTRIBUTIONS: dict[str, Attribution] = {HESSEN.name: HESSEN}

# The terrain relief (spec 4b §4.8). The Copernicus DEM licence asks for this exact notice on
# processed data (Art. 6 b) plus the liability sentence (Art. 6 c), so it stays one verbatim line
# instead of being wrapped like the blocks above. Deliberately not in ATTRIBUTIONS: that registry
# is looked up by stats["lod2_source"], and Copernicus is never a building source.
COPERNICUS = Attribution(
    name="copernicus",
    text=(
        "Gelände: produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH "
        "2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved. The organisations "
        "in charge of the Copernicus programme by law or by delegation do not incur any liability for any use of "
        "the Copernicus WorldDEM-30."
    ),
)


# The tree cover (spec 6 §2). CC BY 4.0 asks for the credit line, verbatim like the Copernicus one.
# OpenStreetMap trees are covered by the OSM block below.
WORLDCOVER = Attribution(
    name="worldcover",
    text=(
        "Bäume: © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA "
        "WorldCover consortium, CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)."
    ),
)


def sources_text(
    date_iso: str,
    lod2: Attribution | None = None,
    terrain: Attribution | None = None,
    trees: Attribution | None = None,
) -> str:
    """The content of SOURCES.txt for one run, one block per source used (spec §7, 4b §4.8, 6 §2)."""
    blocks = [HEADER.format(date=date_iso)]
    if lod2 is not None:
        blocks.append(lod2.text)
    if terrain is not None:
        blocks.append(terrain.text)
    if trees is not None:
        blocks.append(trees.text)
    blocks.append(OSM.text)
    return "\n".join(blocks) + "\n"
