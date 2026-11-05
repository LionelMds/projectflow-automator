"""Colours of the planning bars, coloured by task type (default) or by dossier.

A bar has one stripe colour, with a light and a dark variant; its fill is that stripe
mixed into the theme background (22 % in light mode, 30 % in dark mode), so the bars sit
on any of the four palettes. Dossier colours come from a stable hash of the main number:
a dossier and its sub-projects share one of twelve hues on every poste. Everything here
works on ``#rrggbb`` strings and imports no Qt, so the view model stays pure.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Literal

from projectflow.core.planning.models import PlanningMeta, PlanningTask

if TYPE_CHECKING:
    from projectflow.ui.theme import Theme

ColorBy = Literal["type", "dossier"]
COLOR_BY_LABELS: dict[str, str] = {"type": "Type", "dossier": "Dossier"}

FILL_AMOUNT_LIGHT = 0.22
FILL_AMOUNT_DARK = 0.30
HOT_AMOUNT = 0.12
"""Extra stripe mixed into the fill of a hovered or lifted bar."""
EDGE_AMOUNT = 0.55
PROVISIONAL_AMOUNT = 0.10
"""Fill of a provisional bar: a light wash under its hatching."""

WARN_LIGHT = "#9a5b00"
WARN_DARK = "#e3a447"
OK_LIGHT = "#2f7a45"
OK_DARK = "#6fb880"

DOSSIER_HUES: tuple[tuple[str, str], ...] = (
    ("#4086bd", "#66abe5"),
    ("#009494", "#36baba"),
    ("#4a925c", "#6fb880"),
    ("#908126", "#b5a64f"),
    ("#aa732b", "#d19853"),
    ("#b7684c", "#df8c6f"),
    ("#b16389", "#d887ae"),
    ("#8572bb", "#a996e2"),
    ("#5f7f2a", "#8aab52"),
    ("#a34b4b", "#d27878"),
    ("#4f62a8", "#7d8fd6"),
    ("#7a6a4f", "#a8977a"),
)
"""Twelve stripe hues (light, dark) for « Colorer par dossier »."""

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")
_YEAR_DIGITS = 4
_CHANNEL_MAX = 255


@dataclass(frozen=True, slots=True)
class BarColors:
    """Stripe colour of a bar in light and dark mode."""

    stripe: str
    stripe_dark: str

    def stripe_for(self, *, dark: bool) -> str:
        return self.stripe_dark if dark else self.stripe

    def fill_for(self, background: str, *, dark: bool) -> str:
        """Fill of a confirmed bar: the stripe mixed into ``background``."""
        amount = FILL_AMOUNT_DARK if dark else FILL_AMOUNT_LIGHT
        return mix(background, self.stripe_for(dark=dark), amount)


NO_DOSSIER_COLORS = BarColors(stripe="#96989c", stripe_dark="#b5b7bb")
"""Neutral stripe of a task without dossier when the bars are coloured by dossier."""


def _channels(color: str) -> tuple[int, int, int]:
    match = _HEX_RE.match(color.strip())
    if match is None:
        return (0x96, 0x98, 0x9C)
    value = int(match.group(1), 16)
    return (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF


@lru_cache(maxsize=1024)
def mix(base: str, ink: str, amount: float) -> str:
    """``ink`` laid over ``base`` at ``amount`` (0 gives ``base``, 1 gives ``ink``)."""
    weight = min(max(amount, 0.0), 1.0)
    mixed = (
        round(low + (high - low) * weight)
        for low, high in zip(_channels(base), _channels(ink), strict=True)
    )
    return "#{:02x}{:02x}{:02x}".format(*(min(max(c, 0), _CHANNEL_MAX) for c in mixed))


def normalized_hex(color: str, fallback: str) -> str:
    """``color`` as ``#rrggbb``, or ``fallback`` when it is not a hex colour."""
    match = _HEX_RE.match(color.strip())
    return f"#{match.group(1).lower()}" if match is not None else fallback


def type_colors(meta: PlanningMeta, key: str) -> BarColors:
    """Colours of task type ``key`` from the shared settings (the default type if unknown)."""
    kind = meta.task_type(key)
    stripe = normalized_hex(kind.color, NO_DOSSIER_COLORS.stripe)
    return BarColors(stripe=stripe, stripe_dark=normalized_hex(kind.color_dark, stripe))


def dossier_key(number: str) -> str:
    """Return the main number shared by a dossier and its sub-projects (``2026-118``)."""
    parts = [part for part in number.strip().split("-") if part]
    if not parts:
        return ""
    if len(parts[0]) == _YEAR_DIGITS and parts[0].isdigit() and len(parts) > 1:
        return f"{parts[0]}-{parts[1]}"
    return parts[0]


def dossier_colors(number: str) -> BarColors:
    """One of the twelve hues, the same for a dossier and its sub-projects on every poste."""
    key = dossier_key(number)
    if not key:
        return NO_DOSSIER_COLORS
    stripe, stripe_dark = DOSSIER_HUES[zlib.crc32(key.encode("utf-8")) % len(DOSSIER_HUES)]
    return BarColors(stripe=stripe, stripe_dark=stripe_dark)


def bar_colors(task: PlanningTask, meta: PlanningMeta, color_by: ColorBy) -> BarColors:
    if color_by == "dossier":
        return dossier_colors(task.project_number)
    return type_colors(meta, task.task_type)


def warn_color(*, dark: bool) -> str:
    return WARN_DARK if dark else WARN_LIGHT


def ok_color(*, dark: bool) -> str:
    return OK_DARK if dark else OK_LIGHT


def text_color(theme: Theme) -> str:
    """Ink of the bar text, the theme's ``text`` token."""
    return theme.tokens["text"]


def muted_text_color(theme: Theme) -> str:
    return theme.tokens["neutral-800"]
