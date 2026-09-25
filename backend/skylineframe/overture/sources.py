"""Attribution for Overture Maps Buildings.

The theme is ODbL throughout: it fuses OpenStreetMap with other sources, and ODbL's share-alike
pulls every record along with it regardless of which one a given building actually came from.
Gating this into SOURCES.txt only when Overture data actually reaches a model is the pipeline's
job (see lod2/sources.py, whose Attribution this reuses, for the equivalent gate on lod2_source).
"""

from ..lod2.sources import Attribution

OVERTURE = Attribution(
    name="overture",
    text=(
        "Gebäude (weltweit, wo kein LoD2 vorliegt): © OpenStreetMap-Mitwirkende, Overture Maps Foundation "
        "(overturemaps.org), ODbL (https://www.openstreetmap.org/copyright)."
    ),
)
