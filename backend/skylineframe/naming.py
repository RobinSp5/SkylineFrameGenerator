"""Human labels and ASCII-safe file stems for an exported model.

The label is what a person reads ("Frankfurt am Main – Altstadt", or the coordinates when no place
name is known); the stem is what goes into a file name ("Frankfurt-am-Main_Altstadt_1500m_10cm").
Nothing here touches the network: finding the place name is the web app's job.
"""

import re
import unicodedata

from .spec import FrameSpec

# Separates the place from its district in a label; each side becomes one "_"-joined slug part.
LABEL_SEPARATOR = " – "
MAX_STEM_LEN = 80
FALLBACK_PLACE = "Skyline"

# German first, so "Gießen" reads "Giessen" and not "Gieen"; the rest are letters NFKD cannot
# split into a base letter and an accent.
_TRANSLITERATION = str.maketrans(
    {
        "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss", "ẞ": "SS",
        "ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "æ": "ae", "Æ": "Ae", "œ": "oe", "Œ": "Oe",
        "đ": "d", "Đ": "D", "ð": "d", "þ": "th",
    }
)
_LONE_DOT = re.compile(r"(?<!\d)\.|\.(?!\d)")  # a dot is only kept inside a number
_UNSAFE_RUN = re.compile(r"[^A-Za-z0-9.]+")


def _slug_part(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text.translate(_TRANSLITERATION))
    ascii_text = ascii_text.encode("ascii", "ignore").decode("ascii")
    return _UNSAFE_RUN.sub("-", _LONE_DOT.sub(" ", ascii_text)).strip("-.")


def slugify(label: str, max_len: int = MAX_STEM_LEN) -> str:
    """ASCII-safe slug: words joined by "-", the label's place and district joined by "_"."""
    parts = [_slug_part(part) for part in label.split(LABEL_SEPARATOR.strip())]
    slug = "_".join(part for part in parts if part)
    return slug[:max_len].rstrip("-_.")


def scale_tag(spec: FrameSpec) -> str:
    """Square edge and plate edge, e.g. "1500m_10cm" (mm when the plate is not whole centimetres)."""
    plate_mm = round(spec.plate_size_mm)
    plate = f"{plate_mm // 10}cm" if plate_mm % 10 == 0 else f"{plate_mm}mm"
    return f"{round(spec.side_m)}m_{plate}"


def file_stem(label: str, spec: FrameSpec) -> str:
    """Download file name without extension: place slug plus scale, at most MAX_STEM_LEN chars."""
    tag = scale_tag(spec)
    place = slugify(label, max_len=MAX_STEM_LEN - len(tag) - 1) or FALLBACK_PLACE
    return f"{place}_{tag}"


def coordinate_label(lat: float, lon: float) -> str:
    """Label for a square whose place name is unknown, e.g. "50.1413N 8.3925E"."""
    return f"{abs(lat):.4f}{'N' if lat >= 0 else 'S'} {abs(lon):.4f}{'E' if lon >= 0 else 'W'}"
