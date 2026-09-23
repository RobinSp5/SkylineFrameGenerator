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
        "         Datenlizenz Deutschland Zero 2.0 (https://www.govdata.de/dl-de/zero-2-0)."
    ),
)

# OpenStreetMap is always in the model: roads, water and every footprint outside the LoD2 coverage.
OSM = Attribution(
    name="osm",
    text=(
        "Straßen, Wasser, Grundrisse: © OpenStreetMap-Mitwirkende, ODbL (https://www.openstreetmap.org/copyright).\n"
        "Beim Verkauf von Drucken ist die OpenStreetMap-Namensnennung anzubringen."
    ),
)

# provider name -> attribution; pipeline looks the used source up here.
ATTRIBUTIONS: dict[str, Attribution] = {HESSEN.name: HESSEN}


def sources_text(date_iso: str, lod2: Attribution | None = None) -> str:
    """The content of SOURCES.txt for one run, one block per source used (spec §7)."""
    blocks = [HEADER.format(date=date_iso)]
    if lod2 is not None:
        blocks.append(lod2.text)
    blocks.append(OSM.text)
    return "\n".join(blocks) + "\n"
