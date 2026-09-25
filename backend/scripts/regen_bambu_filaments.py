"""One-off regen of bambu_x2d_project_settings.json's filament arrays for the current filament count.

Every "filament_*" array in the template is one identical block repeated once per filament. Run
this after FILAMENT_COLORS changes length, so every array grows (or shrinks) with it instead of
being hand-edited 90-odd times. Not imported at runtime.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skylineframe.export import FILAMENT_COLORS, PROJECT_SETTINGS

COLOUR_KEYS = {"filament_colour", "filament_multi_colour"}
# Not a repeated block but each filament's own 1-based slot number, block_size times over.
SELF_INDEX_KEY = "filament_self_index"


def regen(settings: dict) -> dict:
    # The file's own filament_colour length, not a constant: this script has already been run
    # once before (3 -> 5), and a constant would only ever describe the count that was old then,
    # silently skipping every array on the next run instead of growing it again (or worse,
    # matching a wrong old count by coincidence and misreading the block boundaries).
    old_count = len(settings["filament_colour"])
    new_count = len(FILAMENT_COLORS)
    out = dict(settings)
    for key, value in settings.items():
        if not key.startswith("filament_") or not isinstance(value, list) or len(value) == 0:
            continue
        if len(value) % old_count != 0:
            continue
        block_size = len(value) // old_count
        blocks = [value[i * block_size : (i + 1) * block_size] for i in range(old_count)]
        if key in COLOUR_KEYS:
            out[key] = list(FILAMENT_COLORS)
            continue
        if key == SELF_INDEX_KEY:
            out[key] = [str(i) for i in range(1, new_count + 1) for _ in range(block_size)]
            continue
        if not all(block == blocks[0] for block in blocks):
            raise ValueError(f"{key!r}: its {old_count} per-filament blocks are not identical: {blocks}")
        out[key] = blocks[0] * new_count
    return out


def main() -> None:
    settings = json.loads(PROJECT_SETTINGS.read_text(encoding="utf-8"))
    PROJECT_SETTINGS.write_text(json.dumps(regen(settings), indent=4, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
