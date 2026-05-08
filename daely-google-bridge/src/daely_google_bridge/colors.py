"""Google Calendar event-color palette + Daely-hex → Google-colorId mapper.

Google Calendar events have a `colorId` field that picks one of 11 fixed colors
(palette returned by `colors.get` under the `event` map). Daely profiles carry
a free-form `colorCode` hex (`#RRGGBB`). This module bridges the two by
nearest-RGB matching, plus exports a per-colorId emoji used for the
multi-participant title-prefix.

Hex values are taken from Google's modern (Material) event palette as observed
in the Calendar UI. They differ slightly between API versions; the API is the
ground truth via `colors().get()`, but for offline matching we hard-code a
representative snapshot — exact byte parity is not required for nearest-match.
"""
from __future__ import annotations

# Google's 11 event colors (id → representative hex). The labels are Google's
# documented friendly names; users will see them in the Calendar UI.
GOOGLE_EVENT_COLORS: dict[str, str] = {
    "1":  "#7986cb",  # Lavender
    "2":  "#33b679",  # Sage
    "3":  "#8e24aa",  # Grape
    "4":  "#e67c73",  # Flamingo
    "5":  "#f6bf26",  # Banana
    "6":  "#f4511e",  # Tangerine
    "7":  "#039be5",  # Peacock
    "8":  "#616161",  # Graphite
    "9":  "#3f51b5",  # Blueberry
    "10": "#0b8043",  # Basil
    "11": "#d50000",  # Tomato
}

# Per-colorId emoji used for title-prefix when multiple profiles attend.
# Some Google colors collapse onto the same color-emoji (e.g. Lavender + Grape
# → 🟣). For families where two profiles end up in collapsing buckets, the
# `profile_overrides` config lets the user pick a non-colliding colorId by hand.
EMOJI_FOR_COLOR_ID: dict[str, str] = {
    "1":  "🟣",  # Lavender
    "2":  "🟢",  # Sage
    "3":  "🟣",  # Grape
    "4":  "🔴",  # Flamingo
    "5":  "🟡",  # Banana
    "6":  "🟠",  # Tangerine
    "7":  "🔵",  # Peacock
    "8":  "⚫",  # Graphite
    "9":  "🔵",  # Blueberry
    "10": "🟢",  # Basil
    "11": "🔴",  # Tomato
}

VALID_COLOR_IDS = frozenset(GOOGLE_EVENT_COLORS.keys())


def _hex_to_rgb(hex_code: str) -> tuple[int, int, int] | None:
    """Parse `#RRGGBB` (or `RRGGBB`) into an (r, g, b) tuple. Returns None on garbage."""
    if not hex_code:
        return None
    s = hex_code.lstrip("#").strip()
    if len(s) != 6:
        return None
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return None


def nearest_color_id(hex_code: str | None) -> str | None:
    """Return the Google colorId whose palette hex is closest to `hex_code`.

    Distance metric: squared Euclidean in RGB space. Ties resolve to the
    lower colorId by iteration order of `GOOGLE_EVENT_COLORS`.

    Returns None if `hex_code` is missing or unparseable — caller should
    fall back to leaving Google's colorId unset.
    """
    rgb = _hex_to_rgb(hex_code) if hex_code else None
    if rgb is None:
        return None
    r, g, b = rgb
    best_id: str | None = None
    best_dist = float("inf")
    for cid, palette_hex in GOOGLE_EVENT_COLORS.items():
        pr, pg, pb = _hex_to_rgb(palette_hex)  # type: ignore[misc]  # palette is well-formed
        dist = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
        if dist < best_dist:
            best_dist = dist
            best_id = cid
    return best_id


def is_valid_color_id(value: str | None) -> bool:
    """True iff `value` is one of Google's 11 event colorIds (string '1'..'11')."""
    return value in VALID_COLOR_IDS


def emoji_for_color_id(color_id: str | None) -> str | None:
    """Return the title-prefix emoji for a colorId, or None if unmapped."""
    if color_id is None:
        return None
    return EMOJI_FOR_COLOR_ID.get(color_id)


__all__ = [
    "EMOJI_FOR_COLOR_ID",
    "GOOGLE_EVENT_COLORS",
    "VALID_COLOR_IDS",
    "emoji_for_color_id",
    "is_valid_color_id",
    "nearest_color_id",
]
