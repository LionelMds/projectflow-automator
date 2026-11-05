"""Shared list of task types: keys, keywords, colours and the « Atelier / Montage / Autre » preset.

The list lives in ``PlanningMeta.task_types`` and is edited from the « Types de tâche »
page of the team dialog. A type's key never changes once created (tasks store it); its
label, colour, keywords and place in the list do. Keywords are kept folded (lower case,
no accents) as quick entry matches them. Nothing here imports Qt.
"""

from __future__ import annotations

import colorsys
import re
from collections.abc import Iterable, Mapping
from typing import Final

from projectflow.core.planning.models import TaskType
from projectflow.core.planning.quick_entry import fold_text

_KEY_RE = re.compile(r"[^a-z0-9]+")
_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")
_CHANNEL_MAX: Final = 255
DARK_LIGHTNESS_STEP: Final = 0.12
"""Lightness added to a colour for dark mode, as the default types do."""
DARK_MAX_LIGHTNESS: Final = 0.78
DEFAULT_TYPE_KEY: Final = "type"

TYPE_PALETTE: tuple[tuple[str, str], ...] = (
    ("#4f7ea8", "#78a6d1"),
    ("#4086bd", "#66abe5"),
    ("#009494", "#36baba"),
    ("#4a925c", "#6fb880"),
    ("#908126", "#b5a64f"),
    ("#aa732b", "#d19853"),
    ("#b7684c", "#df8c6f"),
    ("#a34b4b", "#d27878"),
    ("#b16389", "#d887ae"),
    ("#8572bb", "#a996e2"),
    ("#787a7e", "#a3a5a9"),
)
"""Stripe colours (light, dark) offered for a type; the default types use them too."""

SIMPLE_TASK_TYPES: tuple[TaskType, ...] = (
    TaskType(
        key="atelier",
        label="Atelier",
        color="#4f7ea8",
        color_dark="#78a6d1",
        keywords=(
            "atelier",
            "debit",
            "decoupe",
            "sciage",
            "laser",
            "soudure",
            "souder",
            "assemblage",
            "fab",
            "fabrication",
            "pliage",
            "finition",
            "meulage",
            "ebavurage",
            "controle",
        ),
        team="atelier",
    ),
    TaskType(
        key="montage",
        label="Montage",
        color="#4a925c",
        color_dark="#6fb880",
        keywords=(
            "montage",
            "pose",
            "poser",
            "chantier",
            "livraison",
            "livrer",
            "transport",
            "camion",
        ),
        team="montage",
    ),
    TaskType(
        key="autre",
        label="Autre",
        color="#787a7e",
        color_dark="#a3a5a9",
        keywords=(
            "autre",
            "releve",
            "cotes",
            "mesure",
            "dessin",
            "plan",
            "plans",
            "sav",
            "reparation",
            "depannage",
            "interne",
            "rangement",
            "entretien",
            "nettoyage",
        ),
    ),
)
"""« Simplifier : Atelier / Montage / Autre »: three types instead of the detailed ten."""

SIMPLE_TYPE_MAPPING: Mapping[str, str] = {
    "atelier": "atelier",
    "debit": "atelier",
    "soudure": "atelier",
    "finition": "atelier",
    "montage": "montage",
    "livraison": "montage",
}
"""Old key -> preset key; every other key becomes « autre »."""
SIMPLE_FALLBACK = "autre"


def simple_mapping(keys: Iterable[str]) -> dict[str, str]:
    """Preset key of each of ``keys`` (the types of the tasks to convert)."""
    return {key: SIMPLE_TYPE_MAPPING.get(key, SIMPLE_FALLBACK) for key in keys}


def new_type_key(label: str, taken: Iterable[str]) -> str:
    """Key of a new type: its folded label without spaces, made unique (``atelier2``)."""
    base = _KEY_RE.sub("", fold_text(label)) or DEFAULT_TYPE_KEY
    used = set(taken)
    key = base
    number = 2
    while key in used:
        key = f"{base}{number}"
        number += 1
    return key


def parse_keywords(text: str) -> tuple[str, ...]:
    """Keywords typed comma separated, folded, without blanks nor repeats."""
    words = (" ".join(fold_text(part).split()) for part in text.split(","))
    return tuple(dict.fromkeys(word for word in words if word))


def keywords_text(keywords: Iterable[str]) -> str:
    return ", ".join(keywords)


def dark_variant(color: str) -> str:
    """Stripe of ``color`` for dark mode: a palette pair, else the colour lightened."""
    match = _HEX_RE.match(color.strip())
    if match is None:
        return TYPE_PALETTE[-1][1]
    light = f"#{match.group(1).lower()}"
    paired = next((dark for hue, dark in TYPE_PALETTE if hue == light), None)
    if paired is not None:
        return paired
    value = int(match.group(1), 16)
    red, green, blue = ((value >> shift) & 0xFF for shift in (16, 8, 0))
    hue, lightness, saturation = colorsys.rgb_to_hls(
        red / _CHANNEL_MAX, green / _CHANNEL_MAX, blue / _CHANNEL_MAX
    )
    lightness = min(max(lightness + DARK_LIGHTNESS_STEP, 0.55), DARK_MAX_LIGHTNESS)
    channels = colorsys.hls_to_rgb(hue, lightness, saturation)
    return "#{:02x}{:02x}{:02x}".format(*(round(channel * _CHANNEL_MAX) for channel in channels))


def next_palette_color(task_types: Iterable[TaskType]) -> tuple[str, str]:
    """First palette colour no type uses yet (the first one when all are taken)."""
    used = {kind.color.lower() for kind in task_types}
    return next((pair for pair in TYPE_PALETTE if pair[0] not in used), TYPE_PALETTE[0])
