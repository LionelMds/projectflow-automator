"""Painted body of the Planning grid: styles, background, bars, previews and the scene.

The scene draws the background by hand (rows, days, lunch and Friday afternoon, holidays
of each row's canton, absences, today) and holds one ``BarItem`` per ``BarData``: a
hand-painted ``QGraphicsObject`` without any nested widget. The paint functions take
the theme and the model explicitly, so the PDF export draws exactly the same picture.

The « Non affecté » row stays pinned under the time header: once the rows scroll, an
opaque band item repaints that row at the top of the view and its bars follow it.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from PySide6.QtCore import QElapsedTimer, QLineF, QPointF, QRect, QRectF, QSizeF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsScene,
    QStyleOptionGraphicsItem,
    QWidget,
)

from projectflow.core.planning.layout import UNASSIGNED_ROW_ID, RowLayout
from projectflow.core.planning.walltime import MINUTES_PER_HOUR, WallTime, day_start
from projectflow.ui.planning.colors import (
    EDGE_AMOUNT,
    HOT_AMOUNT,
    PROVISIONAL_AMOUNT,
    BarColors,
    mix,
    ok_color,
)
from projectflow.ui.planning.grid_model import (
    AbsenceBlock,
    Badge,
    BarData,
    BarKey,
    GridModel,
    RestBlock,
    column_tinted,
)
from projectflow.ui.theme import Theme, body_font, current_theme, heading_font
from projectflow.ui.widgets.industry import paint_corner_marks

if TYPE_CHECKING:
    from projectflow.core.planning.calendar import WorkCalendar

STRIPE = 3
EDGE_ZONE = 6.0
"""Width of the grab zone of each end of a bar, where the cursor becomes ↔."""
BAR_GAP = 1
"""Pixels left free at each end of a bar, so that two touching bars stay apart."""
MIN_BAR_WIDTH = 3.0
MIN_BAR_HEIGHT = 8.0
MARK_MARGIN = 6.0
TEXT_LEFT = 5.0
TEXT_RIGHT = 4.0
TEXT_GAP = 5.0
TWO_LINE_HEIGHT = 30.0
TEXT_GROWTH_FROM = 38
"""Lane height (« Normale ») up to which the text of the bars keeps its designed size."""
TEXT_GROWTH_STEP = 8
"""One pixel more of text for each step of lane height past ``TEXT_GROWTH_FROM``."""
MAX_TEXT_GROWTH = 2
"""Reached at « Large »: taller rows then show more lines (``STACK_PAD``), not larger text."""
STACK_PAD = 2.0
"""Above the first line of a bar tall enough to stack its texts, and under the last."""
MAX_TITLE_LINES = 3
"""A title wraps onto this many lines at most, the last one elided."""
MAX_NOTE_LINES = 2
WIDE_BAR = 150.0
MEDIUM_BAR = 70.0
NARROW_BAR = 30.0
MIN_TEXT_ROOM = 14.0
MIN_ELIDED_CHARS = 4
BADGE_HEIGHT = 14.0
BADGE_PADDING = 4.0
BADGE_GAP = 2.0
BADGE_MIN_BAR_WIDTH = 64.0
TRIANGLE_HALF = 4.5
TRIANGLE_INSET = 3.0
CHECK_SIZE = 10.0
PENDING_DOT = 4.0
CUT_FADE = 14.0
"""Width of the fade at the end of a bar that runs past the shown range; no text under it."""
FOCUS_OFFSET = 3.5
"""Distance of the dotted focus ring around the keyboard's current bar."""
DASH_ON = 3.0
DASH_OFF = 2.0
HATCH_SPACING = 7.0
ABSENCE_HATCH_SPACING = 6.0
ALTERNATE_ALPHA = 0.075
"""Ink of the alternate (« bi-ton ») day or week columns over the ground."""
ALTERNATE_ALPHA_DARK = 0.095
"""Light ink reads weaker on a dark ground: a little more of it in the dark themes."""
ABSENCE_INSET = 2.0
REST_HATCH_SPACING = 9.0
NIGHT_MUTED_ALPHA = 0.78
NIGHT_HATCH_ALPHA = 0.2
"""Wider than an absence's hatching: the rest is lighter, it never blocks a drop."""
MOON_SIZE = 11.0
ABSENCE_LABEL_MIN_WIDTH = 60.0
ABSENCE_LABEL_MIN_HEIGHT = 16.0
ABSENCE_CHIP_HEIGHT = 16.0
ABSENCE_CHIP_PADDING = 10.0
DONE_OPACITY = 0.55
GHOST_OPACITY = 0.6
FLASH_MS = 600
FLASH_TICK_MS = 30
FLASH_ALPHA = 0.85
LABEL_PADDING = 8.0
LABEL_OFFSET = 6.0
LABEL_EDGE = 4.0
NOW_WIDTH = 2.0
BAND_SHADOW = 3.0
LUNCH_HATCH_MIN_DAY_WIDTH = 120.0
"""Below this day width (month view) only Friday afternoon and days off are hatched."""
HALF_HOUR = 30

Z_BAR = 1.0
Z_SELECTED_BUMP = 2.0
Z_BAND = 6.0
"""The pinned « Non affecté » band covers the rows scrolled under it."""
Z_PINNED_BAR = 7.0
Z_DRAGGED = 10.0
Z_GHOST = 12.0
Z_RANGE = 14.0
Z_LABEL = 50.0

BarZone = Literal["body", "start", "end"]


def make_pen(color: QColor, width: float = 1.0, style: Qt.PenStyle = Qt.PenStyle.SolidLine) -> QPen:
    pen = QPen(color)
    pen.setWidthF(width)
    pen.setStyle(style)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    return pen


def hatch_lines(rect: QRectF, spacing: float = HATCH_SPACING) -> list[QLineF]:
    """Diagonal lines ``/`` filling ``rect``, on a lattice fixed to the scene origin.

    The lattice never moves with the rectangle, so neighbouring hatched areas join up and
    scrolling never makes the hatching swim.
    """
    if rect.width() <= 0 or rect.height() <= 0:
        return []
    left, right, top, bottom = rect.left(), rect.right(), rect.top(), rect.bottom()
    first = math.ceil((left + top) / spacing)
    last = math.floor((right + bottom) / spacing)
    lines: list[QLineF] = []
    for index in range(first, last + 1):
        total = index * spacing
        x_start = max(left, total - bottom)
        x_end = min(right, total - top)
        if x_end > x_start:
            lines.append(QLineF(x_start, total - x_start, x_end, total - x_end))
    return lines


# ---------------------------------------------------------------- styles ----


@dataclass(frozen=True, slots=True)
class GridStyle:
    """Pens and fills of the grid background for one theme."""

    theme: Theme
    bg: QColor
    alternate_fill: QColor
    team_fill: QColor
    unassigned_fill: QColor
    row_pen: QPen
    team_pen: QPen
    rule_pen: QPen
    day_pen: QPen
    week_pen: QPen
    hour_pen: QPen
    half_pen: QPen
    off_fill: QColor
    hatch_pen: QPen
    lunch_fill: QColor
    today_fill: QColor
    drop_fill: QColor
    now_color: QColor
    absence_fill: QColor
    absence_hatch_pen: QPen
    absence_pen: QPen
    absence_text: QColor
    absence_font: QFont
    holiday_fill: QColor
    holiday_hatch_pen: QPen
    shadow: QColor
    rest_fill: QColor
    rest_hatch_pen: QPen
    rest_pen: QPen
    rest_text: QColor


def alternate_fill(theme: Theme) -> QColor:
    """Tint of the alternate columns, the same in the body, the header and on paper."""
    return theme.color("text", ALTERNATE_ALPHA_DARK if theme.is_dark else ALTERNATE_ALPHA)


def build_grid_style(theme: Theme, *, spaced: bool = True) -> GridStyle:
    """Pens and fills for ``theme``; ``spaced`` False drops the letter spacing (PDF)."""
    absence_font = body_font(10, QFont.Weight.Medium)
    if spaced:
        absence_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
    return GridStyle(
        theme=theme,
        bg=theme.color("bg"),
        alternate_fill=alternate_fill(theme),
        team_fill=theme.color("surface"),
        unassigned_fill=theme.color("text", 0.025),
        row_pen=make_pen(theme.color("text", 0.08)),
        team_pen=make_pen(theme.color("text", 0.22)),
        rule_pen=make_pen(theme.color("text", 0.30)),
        day_pen=make_pen(theme.color("text", 0.14)),
        week_pen=make_pen(theme.color("text", 0.34)),
        hour_pen=make_pen(theme.color("text", 0.07)),
        half_pen=make_pen(theme.color("text", 0.05), 1.0, Qt.PenStyle.DashLine),
        off_fill=theme.color("text", 0.035),
        hatch_pen=make_pen(theme.color("text", 0.10)),
        lunch_fill=theme.color("text", 0.025),
        today_fill=theme.color("accent", 0.08),
        drop_fill=theme.color("accent", 0.10),
        now_color=theme.color("accent"),
        absence_fill=theme.color("absence", 0.18 if theme.is_dark else 0.13),
        absence_hatch_pen=make_pen(theme.color("absence", 0.55 if theme.is_dark else 0.45)),
        absence_pen=make_pen(theme.color("absence", 0.85)),
        absence_text=theme.color("absence-ink"),
        absence_font=absence_font,
        holiday_fill=theme.color("absence", 0.09 if theme.is_dark else 0.06),
        holiday_hatch_pen=make_pen(theme.color("absence", 0.30 if theme.is_dark else 0.22)),
        shadow=theme.color("text", 0.10),
        rest_fill=theme.color("night-rest", 0.07),
        rest_hatch_pen=make_pen(theme.color("night-rest", 0.24)),
        rest_pen=make_pen(theme.color("night-rest", 0.40), 1.0, Qt.PenStyle.DashLine),
        rest_text=theme.color("night-rest"),
    )


@dataclass(frozen=True, slots=True)
class ResolvedColors:
    stripe: QColor
    fill: QColor
    fill_hot: QColor
    provisional_fill: QColor
    edge: QPen
    edge_hot: QPen
    dashed: QPen
    hatch: QPen


@dataclass(slots=True)
class BarStyle:
    """Fonts, metrics and colours of the bars for one theme and density."""

    theme: Theme
    compact: bool
    growth: int
    number_font: QFont
    head_font: QFont
    sub_font: QFont
    badge_font: QFont
    number_metrics: QFontMetricsF
    head_metrics: QFontMetricsF
    sub_metrics: QFontMetricsF
    badge_metrics: QFontMetricsF
    text: QColor
    muted: QColor
    select_pen: QPen
    focus_pen: QPen
    sibling_pen: QPen
    ghost_pen: QPen
    ghost_fill: QColor
    err: QColor
    err_bg: QColor
    ok: QColor
    badge_bg: QColor
    badge_text: QColor
    pending: QColor
    flash: QColor
    fade: QColor
    night_fill: QColor
    night_fill_hot: QColor
    night_edge: QPen
    night_ink: QColor
    night_muted: QColor
    night_hatch: QColor
    moon: QColor
    resolved: dict[BarColors, ResolvedColors] = field(default_factory=dict)

    def colors(self, colors: BarColors) -> ResolvedColors:
        found = self.resolved.get(colors)
        if found is None:
            found = _resolve(self.theme, colors)
            self.resolved[colors] = found
        return found


HATCH_ALPHA = 0.45


def _resolve(theme: Theme, colors: BarColors) -> ResolvedColors:
    dark = theme.is_dark
    background = theme.tokens["bg"]
    stripe = colors.stripe_for(dark=dark)
    fill = colors.fill_for(background, dark=dark)
    hatch = QColor(stripe)
    hatch.setAlphaF(HATCH_ALPHA)
    return ResolvedColors(
        stripe=QColor(stripe),
        fill=QColor(fill),
        fill_hot=QColor(mix(fill, stripe, HOT_AMOUNT)),
        provisional_fill=QColor(mix(background, stripe, PROVISIONAL_AMOUNT)),
        edge=make_pen(QColor(mix(background, stripe, EDGE_AMOUNT))),
        edge_hot=make_pen(QColor(stripe)),
        dashed=make_pen(QColor(stripe), 1.0, Qt.PenStyle.DashLine),
        hatch=make_pen(hatch),
    )


def text_growth(lane_height: float) -> int:
    """Pixels added to the text of the bars in lanes of ``lane_height``.

    Taller rows, larger text: +2 at « Large », at most ``MAX_TEXT_GROWTH``.
    """
    steps = (int(lane_height) - TEXT_GROWTH_FROM) // TEXT_GROWTH_STEP
    return min(max(steps, 0), MAX_TEXT_GROWTH)


def build_bar_style(theme: Theme, *, compact: bool, growth: int = 0) -> BarStyle:
    """``growth`` pixels are added to the number, the title and the second line."""
    number_font = heading_font((12 if compact else 13) + growth)
    head_font = body_font(12 + growth, QFont.Weight.Medium)
    sub_font = body_font(11 + growth)
    badge_font = body_font(10, QFont.Weight.DemiBold)
    dark = theme.is_dark
    return BarStyle(
        theme=theme,
        compact=compact,
        growth=growth,
        number_font=number_font,
        head_font=head_font,
        sub_font=sub_font,
        badge_font=badge_font,
        number_metrics=QFontMetricsF(number_font),
        head_metrics=QFontMetricsF(head_font),
        sub_metrics=QFontMetricsF(sub_font),
        badge_metrics=QFontMetricsF(badge_font),
        text=theme.color("text"),
        muted=theme.color("neutral-800"),
        select_pen=make_pen(theme.color("accent"), 2.0),
        focus_pen=make_pen(theme.color("accent-900"), 1.0, Qt.PenStyle.DotLine),
        sibling_pen=make_pen(theme.color("accent-700"), 1.0, Qt.PenStyle.DashLine),
        ghost_pen=make_pen(theme.color("accent"), 1.5, Qt.PenStyle.DashLine),
        ghost_fill=theme.color("accent", 0.16),
        err=theme.color("err"),
        err_bg=QColor(mix(theme.tokens["bg"], theme.tokens["err"], 0.14)),
        ok=QColor(ok_color(dark=dark)),
        badge_bg=theme.color("bg"),
        badge_text=theme.color("neutral-800"),
        pending=theme.color("accent"),
        flash=theme.color("accent-200"),
        fade=theme.color("bg", 0.7),
        night_fill=theme.color("night"),
        night_fill_hot=QColor(mix(theme.tokens["night"], theme.tokens["night-edge"], HOT_AMOUNT)),
        night_edge=make_pen(theme.color("night-edge")),
        night_ink=theme.color("night-ink"),
        night_muted=theme.color("night-ink", NIGHT_MUTED_ALPHA),
        night_hatch=theme.color("night-ink", NIGHT_HATCH_ALPHA),
        moon=theme.color("moon"),
    )


# ------------------------------------------------------------ background ----


def _row_runs(rows: Sequence[RowLayout], model: GridModel) -> list[tuple[str, float, float]]:
    """Consecutive non-team rows sharing a calendar, as ``(first row id, top, bottom)``."""
    runs: list[tuple[str, float, float]] = []
    current: WorkCalendar | None = None
    for row in rows:
        if row.kind == "team":
            current = None
            continue
        calendar = model.calendar_of(row.resource_id)
        if runs and calendar is current and runs[-1][2] == row.top:
            first, top, _bottom = runs[-1]
            runs[-1] = (first, top, float(row.bottom))
        else:
            runs.append((row.resource_id, float(row.top), float(row.bottom)))
        current = calendar
    return runs


def _off_rects(
    model: GridModel, row_id: str, top: float, bottom: float, rect: QRectF
) -> list[tuple[QRectF, bool]]:
    """Areas not worked, each with whether it is a holiday or a closure (whole day)."""
    scale = model.timescale
    lunch = scale.day_width >= LUNCH_HATCH_MIN_DAY_WIDTH
    day_end = scale.first_minute + scale.display_minutes
    days = model.days_of(row_id)
    rects: list[tuple[QRectF, bool]] = []
    for column in scale.visible_columns(rect.left(), rect.right()):
        info = days[column.index]
        base = day_start(column.day)
        day_off = info.holiday is not None or info.closed
        for low, high in info.off:
            if info.workday and not lunch and high < day_end:
                continue
            left = scale.x_of(base + low)
            right = scale.x_of(base + high)
            if right > left:
                rects.append((QRectF(left, top, right - left, bottom - top), day_off))
    return rects


def paint_background(
    painter: QPainter,
    model: GridModel,
    rect: QRectF,
    style: GridStyle,
    *,
    drop_row: str | None = None,
    printing: bool = False,
) -> None:
    """Draw the grid behind the bars for the scene area ``rect``."""
    painter.fillRect(rect, style.bg)
    rows = model.rows.visible(rect.top(), rect.bottom())
    if not rows:
        return
    _paint_alternate(painter, model, rect, style)
    _paint_today(painter, model, rect, style, printing=printing)
    _paint_days_off(painter, model, rows, rect, style)
    _paint_row_bands(painter, rows, rect, style, drop_row=None if printing else drop_row)
    if model.view != "month":
        _paint_hours(painter, model, rect, style)
    _paint_day_lines(painter, model, rect, style)
    _paint_absences(painter, model, rows, rect, style)
    _paint_rests(painter, model, rows, rect, style)
    _paint_row_lines(painter, rows, rect, style)


def _paint_alternate(painter: QPainter, model: GridModel, rect: QRectF, style: GridStyle) -> None:
    """Tint every other day (week, day views) or ISO week (month view): the base layer."""
    for column in model.timescale.visible_columns(rect.left(), rect.right()):
        if column_tinted(model.view, column.day):
            area = QRectF(column.x, rect.top(), column.width, rect.height())
            painter.fillRect(area, style.alternate_fill)


def _paint_today(
    painter: QPainter, model: GridModel, rect: QRectF, style: GridStyle, *, printing: bool
) -> None:
    index = model.today_index
    if index is None or printing:
        return
    column = model.timescale.columns[index]
    painter.fillRect(QRectF(column.x, rect.top(), column.width, rect.height()), style.today_fill)


def _paint_days_off(
    painter: QPainter,
    model: GridModel,
    rows: Sequence[RowLayout],
    rect: QRectF,
    style: GridStyle,
) -> None:
    hatches: list[QLineF] = []
    holiday_hatches: list[QLineF] = []
    for row_id, top, bottom in _row_runs(rows, model):
        for area, day_off in _off_rects(model, row_id, top, bottom, rect):
            # A holiday or a closure stands out in red-orange, as an absence of everyone.
            painter.fillRect(area, style.holiday_fill if day_off else style.off_fill)
            (holiday_hatches if day_off else hatches).extend(hatch_lines(area.intersected(rect)))
    painter.setPen(style.hatch_pen)
    painter.drawLines(hatches)
    painter.setPen(style.holiday_hatch_pen)
    painter.drawLines(holiday_hatches)


def _paint_row_bands(
    painter: QPainter,
    rows: Sequence[RowLayout],
    rect: QRectF,
    style: GridStyle,
    *,
    drop_row: str | None,
) -> None:
    for row in rows:
        band = QRectF(rect.left(), row.top, rect.width(), row.height)
        if row.kind == "team":
            painter.fillRect(band, style.team_fill)
        elif row.kind == "unassigned":
            painter.fillRect(band, style.unassigned_fill)
        if row.resource_id == drop_row:
            painter.fillRect(band, style.drop_fill)


def _paint_hours(painter: QPainter, model: GridModel, rect: QRectF, style: GridStyle) -> None:
    scale = model.timescale
    first_minute = scale.first_minute
    step = HALF_HOUR if model.view == "day" else MINUTES_PER_HOUR
    hours: list[QLineF] = []
    halves: list[QLineF] = []
    top, bottom = rect.top(), rect.bottom()
    for column in scale.visible_columns(rect.left(), rect.right()):
        base = day_start(column.day)
        minute = first_minute + (step - first_minute % step) % step
        while minute < first_minute + scale.display_minutes:
            x = round(scale.x_of(base + minute)) + 0.5
            if minute > first_minute:
                target = hours if minute % MINUTES_PER_HOUR == 0 else halves
                target.append(QLineF(x, top, x, bottom))
            minute += step
    painter.setPen(style.half_pen)
    painter.drawLines(halves)
    painter.setPen(style.hour_pen)
    painter.drawLines(hours)


def _paint_day_lines(painter: QPainter, model: GridModel, rect: QRectF, style: GridStyle) -> None:
    scale = model.timescale
    days: list[QLineF] = []
    weeks: list[QLineF] = []
    top, bottom = rect.top(), rect.bottom()
    for column in scale.visible_columns(rect.left() - 1, rect.right() + 1):
        if column.index == 0:
            continue
        x = round(column.x) + 0.5
        previous = scale.columns[column.index - 1].day
        new_week = column.day.isocalendar()[:2] != previous.isocalendar()[:2]
        (weeks if new_week else days).append(QLineF(x, top, x, bottom))
    painter.setPen(style.day_pen)
    painter.drawLines(days)
    painter.setPen(style.week_pen)
    painter.drawLines(weeks)


def absence_rect(model: GridModel, block: AbsenceBlock, row: RowLayout) -> QRectF:
    left, right = model.timescale.span_x(block.start, block.end)
    return QRectF(
        left + BAR_GAP,
        row.top + ABSENCE_INSET,
        max(right - left - 2 * BAR_GAP, 1.0),
        max(row.height - 2 * ABSENCE_INSET - 1, 1.0),
    )


def _paint_absences(
    painter: QPainter,
    model: GridModel,
    rows: Sequence[RowLayout],
    rect: QRectF,
    style: GridStyle,
) -> None:
    hatches: list[QLineF] = []
    boxes: list[tuple[QRectF, str]] = []
    for row in rows:
        for block in model.absences_by_row.get(row.resource_id, ()):
            area = absence_rect(model, block, row)
            if not area.intersects(rect):
                continue
            painter.fillRect(area, style.absence_fill)
            hatches.extend(hatch_lines(area, ABSENCE_HATCH_SPACING))
            boxes.append((area, block.label))
    if not boxes:
        return
    painter.setPen(style.absence_hatch_pen)
    painter.drawLines(hatches)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(style.absence_pen)
    for area, _text in boxes:
        painter.drawRect(area.adjusted(0.5, 0.5, -0.5, -0.5))
    _paint_absence_labels(painter, boxes, style)


def rest_rect(model: GridModel, block: RestBlock, row: RowLayout) -> QRectF:
    left, right = model.timescale.span_x(block.start, block.end)
    return QRectF(
        left + BAR_GAP,
        row.top + ABSENCE_INSET,
        max(right - left - 2 * BAR_GAP, 0.0),
        max(row.height - 2 * ABSENCE_INSET - 1, 1.0),
    )


def _paint_rests(
    painter: QPainter,
    model: GridModel,
    rows: Sequence[RowLayout],
    rect: QRectF,
    style: GridStyle,
) -> None:
    """Light hatched « Repos » blocks: 8 h before and after each night of a person."""
    hatches: list[QLineF] = []
    boxes: list[tuple[QRectF, str]] = []
    for row in rows:
        for block in model.rests_by_row.get(row.resource_id, ()):
            area = rest_rect(model, block, row)
            if area.width() < 1 or not area.intersects(rect):
                continue
            painter.fillRect(area, style.rest_fill)
            hatches.extend(hatch_lines(area, REST_HATCH_SPACING))
            boxes.append((area, block.label))
    if not boxes:
        return
    painter.setPen(style.rest_hatch_pen)
    painter.drawLines(hatches)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(style.rest_pen)
    for area, _text in boxes:
        painter.drawRect(area.adjusted(0.5, 0.5, -0.5, -0.5))
    _paint_absence_labels(painter, boxes, style, ink=style.rest_text)


def _paint_absence_labels(
    painter: QPainter,
    boxes: Sequence[tuple[QRectF, str]],
    style: GridStyle,
    *,
    ink: QColor | None = None,
) -> None:
    painter.setFont(style.absence_font)
    metrics = QFontMetricsF(style.absence_font)
    for area, text in boxes:
        if area.width() < ABSENCE_LABEL_MIN_WIDTH or area.height() < ABSENCE_LABEL_MIN_HEIGHT:
            continue
        room = area.width() - ABSENCE_CHIP_PADDING - 2
        label = metrics.elidedText(text.upper(), Qt.TextElideMode.ElideRight, room)
        width = metrics.horizontalAdvance(label) + ABSENCE_CHIP_PADDING
        chip = QRectF(
            area.center().x() - width / 2,
            area.center().y() - ABSENCE_CHIP_HEIGHT / 2,
            width,
            ABSENCE_CHIP_HEIGHT,
        )
        painter.fillRect(chip, style.bg)
        painter.setPen(ink or style.absence_text)
        painter.drawText(chip, int(Qt.AlignmentFlag.AlignCenter), label)


def _paint_row_lines(
    painter: QPainter, rows: Sequence[RowLayout], rect: QRectF, style: GridStyle
) -> None:
    plain: list[QLineF] = []
    strong: list[QLineF] = []
    for row in rows:
        y = row.bottom - 0.5
        line = QLineF(rect.left(), y, rect.right(), y)
        (plain if row.kind == "resource" else strong).append(line)
    painter.setPen(style.row_pen)
    painter.drawLines(plain)
    painter.setPen(style.team_pen)
    painter.drawLines(strong)


def now_x(model: GridModel) -> float | None:
    if not model.now_visible:
        return None
    return model.timescale.x_of(model.now)


def paint_now_line(painter: QPainter, model: GridModel, rect: QRectF, color: QColor) -> None:
    x = now_x(model)
    if x is None or not rect.left() - NOW_WIDTH <= x <= rect.right() + NOW_WIDTH:
        return
    painter.fillRect(QRectF(round(x) - 1, rect.top(), NOW_WIDTH, rect.height()), color)


# ------------------------------------------------------------------ bars ----


def bar_rect(
    model: GridModel,
    row: RowLayout,
    start: WallTime,
    end: WallTime,
    lane: int,
    *,
    night: bool = False,
) -> QRectF:
    """Scene rectangle of a bar from ``start`` to ``end`` on ``lane`` of ``row``.

    A row shorter than one lane (the empty « Non affecté » row) gets a thinner bar; a
    night fills its evening's column unless the axis shows the night.
    """
    left, right = model.timescale.span_x(*model.display_span(start, end, night=night))
    left = round(left) + BAR_GAP
    right = round(right) - BAR_GAP
    width = max(right - left, MIN_BAR_WIDTH)
    top = row.bar_top(lane)
    height = min(row.bar_height, row.bottom - top - 2)
    return QRectF(left, top, width, max(height, MIN_BAR_HEIGHT))


@dataclass(frozen=True, slots=True)
class BarState:
    hovered: bool = False
    selected: bool = False
    sibling: bool = False
    lifted: bool = False
    pending: bool = False
    flash: float = 0.0
    warn: bool = False
    """Previewed on someone absent: red hatching, the drop is still allowed."""
    ghost: bool = False
    shift: float = 0.0
    """Pixels the text moves right to stay readable when the bar's start is scrolled out."""
    day_view: bool = False
    focus: bool = False
    """The keyboard's current bar while the grid has the focus: a dotted ring around it."""
    printing: bool = False
    """Drawn for paper: no alpha fade (PDF viewers flatten it into a white block)."""


def has_cut_mark(bar: BarData, width: float) -> bool:
    """Whether the end of ``bar`` gets the « continues » mark (fade, dashes on paper)."""
    return bar.cut_right and width > CUT_FADE * 2


@dataclass(frozen=True, slots=True)
class BarText:
    number: str = ""
    head: str = ""
    sub: str = ""
    extra: str = ""
    number_x: float = 0.0
    head_x: float = 0.0
    sub_x: float = 0.0
    extra_x: float = 0.0
    line1_y: float = 0.0
    line2_y: float = 0.0
    badges: tuple[tuple[Badge, QRectF], ...] = ()
    check: QRectF | None = None
    title_lines: tuple[tuple[str, float], ...] = ()
    """The title past the first line, in the title's font: text and baseline, from ``sub_x``."""
    details: tuple[tuple[str, float], ...] = ()
    """Under the second line, in its font: designation, hours, team, notes."""


def _fit(metrics: QFontMetricsF, text: str, room: float, *, elide: bool = True) -> str:
    if not text or room < MIN_TEXT_ROOM:
        return ""
    if metrics.horizontalAdvance(text) <= room:
        return text
    if not elide:
        return ""
    elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, room)
    return "" if len(elided) < MIN_ELIDED_CHARS else elided


def _wrap(metrics: QFontMetricsF, text: str, rooms: Sequence[float]) -> list[str]:
    """``text`` word by word over lines as wide as ``rooms``, the last one elided.

    A word wider than its line is elided there, or waits for the next line when too little
    of it would show. Fewer lines when the text ends sooner.
    """
    words = text.split()
    lines: list[str] = []
    for index, room in enumerate(rooms):
        if not words:
            break
        if index == len(rooms) - 1:
            lines.append(_fit(metrics, " ".join(words), room))
            break
        if metrics.horizontalAdvance(words[0]) > room:
            # Elided here when that says something, else the word waits for the next line.
            cut = _fit(metrics, words[0], room)
            if cut:
                words.pop(0)
            lines.append(cut)
            continue
        line = words.pop(0)
        while words and metrics.horizontalAdvance(f"{line} {words[0]}") <= room:
            line = f"{line} {words.pop(0)}"
        lines.append(line)
    return lines


def _badge_width(style: BarStyle, badge: Badge) -> float:
    if badge.kind in {"conflict", "night"}:
        return BADGE_HEIGHT + 2
    return style.badge_metrics.horizontalAdvance(badge.text) + 2 * BADGE_PADDING


def _place_badges(
    bar: BarData, style: BarStyle, width: float, top: float, *, room_left: float, inset: float
) -> tuple[tuple[tuple[Badge, QRectF], ...], QRectF | None, float]:
    """Badges and done check from ``inset`` before the right end; gives them and the text end."""
    right = width - inset
    check: QRectF | None = None
    if bar.done and width >= MEDIUM_BAR:
        check_top = top + (BADGE_HEIGHT - CHECK_SIZE) / 2
        check = QRectF(right - CHECK_SIZE, check_top, CHECK_SIZE, CHECK_SIZE)
        right = check.left() - BADGE_GAP
    if width < BADGE_MIN_BAR_WIDTH or not bar.badges:
        return (), check, right
    kept = list(bar.badges)
    while kept:
        total = sum(_badge_width(style, badge) for badge in kept) + BADGE_GAP * (len(kept) - 1)
        if right - total - room_left >= NARROW_BAR:
            break
        kept.pop()
    placed: list[tuple[Badge, QRectF]] = []
    x = right
    for badge in reversed(kept):
        badge_width = _badge_width(style, badge)
        x -= badge_width
        placed.append((badge, QRectF(x, top, badge_width, BADGE_HEIGHT)))
        x -= BADGE_GAP
    placed.reverse()
    return tuple(placed), check, (x if placed else right)


def _baseline(metrics: QFontMetricsF, center: float) -> float:
    return round(center + (metrics.ascent() - metrics.descent()) / 2)


def _second_line(
    bar: BarData,
    width: float,
    left: float,
    style: BarStyle,
    state: BarState,
    *,
    number: bool,
    inset: float,
) -> tuple[str, str, float]:
    """Title (or type) and duration (or hours in the day view) of the second line."""
    extra_text = bar.time_range if state.day_view and bar.time_range else bar.duration
    line_right = width - inset
    extra = ""
    extra_x = left
    if width >= WIDE_BAR:
        extra = _fit(style.sub_metrics, extra_text, line_right - left, elide=False)
        extra_x = line_right - style.sub_metrics.horizontalAdvance(extra)
        line_right = extra_x - TEXT_GAP if extra else line_right
    sub_text = bar.client if number else bar.type_label
    sub = _fit(style.sub_metrics, sub_text or bar.type_label, line_right - left)
    return sub, extra, extra_x


@dataclass(frozen=True, slots=True)
class _FirstLine:
    """The first line of a bar: its number and title, and where its lines run."""

    number: str
    title: str
    left: float
    title_x: float
    right: float
    """Where the first line ends, before the badges."""
    width: float
    inset: float
    """Kept free at the right end of the other lines (the end mark of a cut bar)."""


def _moved(
    badges: tuple[tuple[Badge, QRectF], ...], check: QRectF | None, top: float
) -> tuple[tuple[tuple[Badge, QRectF], ...], QRectF | None]:
    """Badges and done check placed at 0, moved down to ``top``."""
    moved = tuple((badge, rect.translated(0, top)) for badge, rect in badges)
    return moved, (None if check is None else check.translated(0, top))


def _next_baseline(metrics: QFontMetricsF, above: float) -> float:
    """Baseline of a line of ``metrics`` set right under ``above``."""
    return _baseline(metrics, above + metrics.height() / 2)


def _stacks(style: BarStyle, height: float) -> bool:
    """Whether a bar ``height`` high has room for a third line: the title's second one."""
    lines = style.number_metrics.height() + style.sub_metrics.height() + style.head_metrics.height()
    return height - 2 * STACK_PAD >= lines


def _details(bar: BarData, extra: str, metrics: QFontMetricsF, room: float) -> list[str]:
    """Designation, hours (unless the second line says them) and team, each on one line."""
    hours = bar.time_range if bar.time_range != extra else ""
    team = ", ".join(bar.people) if len(bar.people) > 1 else ""
    fitted = (_fit(metrics, text, room) for text in (bar.designation, hours, team) if text)
    return [text for text in fitted if text]


def _allot(
    room: float, heights: dict[str, float], needs: dict[str, int], order: Sequence[str]
) -> dict[str, int]:
    """Lines given to each text, in ``order`` while ``room`` is left: one at each turn."""
    counts = dict.fromkeys(needs, 0)
    for key in order:
        if counts[key] < needs[key] and room >= heights[key]:
            counts[key] += 1
            room -= heights[key]
    return counts


def _title_lines(metrics: QFontMetricsF, first: _FirstLine, count: int) -> list[str]:
    """Wrap the title over ``count`` lines, the first one beside the number."""
    full = first.width - first.inset - first.left
    rooms = [first.right - first.title_x] + [full] * (count - 1)
    lines = _wrap(metrics, first.title, rooms)
    if len(rooms) > 1 and lines and lines[0] in {"", "-"}:
        # No room beside the number: the title starts on the next line, without its dash.
        lines = ["", *_wrap(metrics, first.title.removeprefix("- "), rooms[1:])]
    return lines


def _stacked(
    bar: BarData,
    first: _FirstLine,
    height: float,
    style: BarStyle,
    state: BarState,
    *,
    badges: tuple[tuple[Badge, QRectF], ...],
    check: QRectF | None,
) -> BarText:
    """Texts of a bar tall enough for three lines, stacked from its top.

    As long as there is room, in turn: the second line (client, duration); the title's
    second line when it needs one; the designation; the title's third line; the hours;
    the team; the notes on two lines. A low bar thus says a little of everything before
    all of its title, as the events of a calendar do when they grow.
    """
    full = first.width - first.inset - first.left
    sub, extra, extra_x = _second_line(
        bar, first.width, first.left, style, state, number=bool(first.number), inset=first.inset
    )
    details = _details(bar, extra, style.sub_metrics, full)
    notes = _wrap(style.sub_metrics, bar.notes, [full] * MAX_NOTE_LINES) if bar.notes else []
    keys = [f"detail{index}" for index in range(len(details))]
    first_metrics = style.number_metrics if first.number else style.head_metrics
    head_step = style.head_metrics.height()
    sub_step = style.sub_metrics.height()
    counts = _allot(
        height - 2 * STACK_PAD - first_metrics.height(),
        {"title": head_step, "sub": sub_step, "notes": sub_step} | dict.fromkeys(keys, sub_step),
        {
            "title": len(_title_lines(style.head_metrics, first, MAX_TITLE_LINES)) - 1,
            "sub": int(bool(sub or extra)),
            "notes": len(notes),
        }
        | dict.fromkeys(keys, 1),
        ["sub", "title", *keys[:1], "title", *keys[1:], *["notes"] * MAX_NOTE_LINES],
    )
    title = _title_lines(style.head_metrics, first, 1 + counts["title"])
    line1_center = STACK_PAD + first_metrics.height() / 2
    # Each line right under the one above; ``y`` is where the last one ends.
    y = STACK_PAD + first_metrics.height()
    title_lines: list[tuple[str, float]] = []
    for text in title[1:]:
        title_lines.append((text, _next_baseline(style.head_metrics, y)))
        y += head_step
    line2_y = _next_baseline(style.sub_metrics, y)
    y += sub_step * counts["sub"]
    shown = [text for key, text in zip(keys, details, strict=True) if counts[key]]
    under: list[tuple[str, float]] = []
    for text in [*shown, *_wrap(style.sub_metrics, bar.notes, [full] * counts["notes"])]:
        under.append((text, _next_baseline(style.sub_metrics, y)))
        y += sub_step
    badges, check = _moved(badges, check, round(line1_center - BADGE_HEIGHT / 2))
    return BarText(
        number=first.number,
        head=title[0] if title else "",
        sub=sub if counts["sub"] else "",
        extra=extra if counts["sub"] else "",
        number_x=first.left,
        head_x=first.title_x,
        sub_x=first.left,
        extra_x=extra_x,
        line1_y=_baseline(first_metrics, line1_center),
        line2_y=line2_y,
        badges=badges,
        check=check,
        title_lines=tuple(title_lines),
        details=tuple(under),
    )


def layout_bar_text(
    bar: BarData, width: float, height: float, style: BarStyle, state: BarState
) -> BarText:
    """Choose what a bar of ``width`` x ``height`` shows, as the proposal's table says.

    150 px and more: number and client, then the title; 70 to 150 px: both elided; 30 to
    70 px: the number only; below 30 px nothing (the hover card takes over). Nothing is
    placed under the end mark of a bar that runs past the shown range. A bar tall enough
    for three lines stacks its texts from its top (``_stacked``).
    """
    if width < NARROW_BAR:
        return BarText()
    inset = CUT_FADE if has_cut_mark(bar, width) else TEXT_RIGHT
    two_lines = height >= TWO_LINE_HEIGHT and not style.compact
    left = STRIPE + TEXT_LEFT + min(max(state.shift, 0.0), max(width - MEDIUM_BAR, 0.0))
    # Badges are placed along the first line, moved to its height once that is known.
    badges, check, right = _place_badges(bar, style, width, 0.0, room_left=left, inset=inset)
    room = right - TEXT_GAP - left
    # « 2026-5271 - Désignation »: the whole number when it fits, else « 5271 ».
    number = _fit(style.number_metrics, bar.project_number, room, elide=False) or _fit(
        style.number_metrics, bar.number, room, elide=False
    )
    head_x = left + (style.number_metrics.horizontalAdvance(number) + TEXT_GAP if number else 0)
    if not number:
        head_text = bar.title or bar.type_label
    else:
        head_text = f"- {bar.title}" if bar.title else bar.client
    if two_lines and width >= MEDIUM_BAR and _stacks(style, height):
        first = _FirstLine(number, head_text, left, head_x, right - TEXT_GAP, width, inset)
        return _stacked(bar, first, height, style, state, badges=badges, check=check)
    line1_center = height / 2 - (style.sub_metrics.height() / 2 if two_lines else 0)
    badges, check = _moved(badges, check, round(line1_center - BADGE_HEIGHT / 2))
    head = ""
    if width >= MEDIUM_BAR or not number:
        head = _fit(style.head_metrics, head_text, right - TEXT_GAP - head_x)
    sub = extra = ""
    extra_x = left
    if two_lines and width >= MEDIUM_BAR:
        sub, extra, extra_x = _second_line(
            bar, width, left, style, state, number=bool(number), inset=inset
        )
    line1_y = _baseline(style.number_metrics if number else style.head_metrics, line1_center)
    line2_center = (
        line1_center + style.number_metrics.height() / 2 + style.sub_metrics.height() / 2 + 1
    )
    return BarText(
        number=number,
        head=head,
        sub=sub,
        extra=extra,
        number_x=left,
        head_x=head_x,
        sub_x=left,
        extra_x=extra_x,
        line1_y=line1_y,
        line2_y=_baseline(style.sub_metrics, line2_center),
        badges=badges,
        check=check,
    )


def _paint_dashes(painter: QPainter, rect: QRectF, color: QColor) -> None:
    y = rect.top()
    while y < rect.bottom():
        dash = QRectF(rect.left(), y, rect.width(), min(DASH_ON, rect.bottom() - y))
        painter.fillRect(dash, color)
        y += DASH_ON + DASH_OFF


def _paint_night_fill(
    painter: QPainter, bar: BarData, rect: QRectF, style: BarStyle, state: BarState
) -> None:
    """Night work: the night ink of the theme, the type's stripe kept on the left."""
    colors = style.colors(bar.colors)
    hot = state.hovered or state.lifted
    painter.fillRect(rect, style.night_fill_hot if hot else style.night_fill)
    if bar.provisional:
        painter.setPen(make_pen(style.night_hatch))
        painter.drawLines(hatch_lines(rect.adjusted(STRIPE, 0, 0, 0), HATCH_SPACING))
    stripe = QRectF(rect.left(), rect.top(), min(STRIPE, rect.width()), rect.height())
    if bar.provisional or bar.cut_left:
        _paint_dashes(painter, stripe, colors.stripe)
    else:
        painter.fillRect(stripe, colors.stripe)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(colors.dashed if bar.provisional else style.night_edge)
    painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))


def paint_moon(painter: QPainter, center: QPointF, size: float, color: QColor) -> None:
    """Paint a crescent moon of ``size`` pixels around ``center``."""
    radius = size / 2
    disc = QPainterPath()
    disc.addEllipse(center, radius, radius)
    shadow = QPainterPath()
    shadow.addEllipse(
        QPointF(center.x() + radius * 0.55, center.y() - radius * 0.25),
        radius * 0.85,
        radius * 0.85,
    )
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, on=True)
    painter.fillPath(disc.subtracted(shadow), color)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, on=False)


def _paint_fill(
    painter: QPainter, bar: BarData, rect: QRectF, colors: ResolvedColors, state: BarState
) -> None:
    hot = state.hovered or state.lifted
    if bar.provisional:
        painter.fillRect(rect, colors.fill_hot if hot else colors.provisional_fill)
        painter.setPen(colors.hatch)
        painter.drawLines(hatch_lines(rect.adjusted(STRIPE, 0, 0, 0), HATCH_SPACING))
    else:
        painter.fillRect(rect, colors.fill_hot if hot else colors.fill)
    stripe = QRectF(rect.left(), rect.top(), min(STRIPE, rect.width()), rect.height())
    if bar.provisional or bar.cut_left:
        _paint_dashes(painter, stripe, colors.stripe)
    else:
        painter.fillRect(stripe, colors.stripe)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if bar.provisional:
        painter.setPen(colors.dashed)
    else:
        painter.setPen(colors.edge_hot if hot else colors.edge)
    painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))


def _paint_text(painter: QPainter, bar: BarData, text: BarText, style: BarStyle) -> None:
    ink = style.night_ink if bar.night else style.text
    if text.number:
        painter.setFont(style.number_font)
        painter.setPen(ink)
        painter.drawText(QPointF(text.number_x, text.line1_y), text.number)
    painter.setFont(style.head_font)
    painter.setPen(ink)
    if text.head:
        painter.drawText(QPointF(text.head_x, text.line1_y), text.head)
    for line, y in text.title_lines:
        painter.drawText(QPointF(text.sub_x, y), line)
    painter.setFont(style.sub_font)
    painter.setPen(style.night_muted if bar.night else style.muted)
    if text.sub:
        painter.drawText(QPointF(text.sub_x, text.line2_y), text.sub)
    if text.extra:
        painter.drawText(QPointF(text.extra_x, text.line2_y), text.extra)
    for line, y in text.details:
        painter.drawText(QPointF(text.sub_x, y), line)
    if bar.done and text.check is not None:
        _paint_check(painter, text.check, style.ok)


def _paint_check(painter: QPainter, rect: QRectF, color: QColor) -> None:
    path = QPainterPath()
    path.moveTo(rect.left() + 1, rect.center().y())
    path.lineTo(rect.left() + rect.width() * 0.4, rect.bottom() - 1.5)
    path.lineTo(rect.right() - 0.5, rect.top() + 1.5)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, on=True)
    painter.strokePath(path, make_pen(color, 1.6))
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, on=False)


def _paint_triangle(painter: QPainter, rect: QRectF, color: QColor) -> None:
    triangle = QPainterPath()
    middle = rect.center().x()
    triangle.moveTo(middle, rect.top() + TRIANGLE_INSET)
    triangle.lineTo(middle + TRIANGLE_HALF, rect.bottom() - TRIANGLE_INSET)
    triangle.lineTo(middle - TRIANGLE_HALF, rect.bottom() - TRIANGLE_INSET)
    triangle.closeSubpath()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, on=True)
    painter.fillPath(triangle, color)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, on=False)


def _paint_badges(painter: QPainter, text: BarText, style: BarStyle) -> None:
    painter.setFont(style.badge_font)
    for badge, rect in text.badges:
        if badge.kind == "night":
            paint_moon(painter, rect.center(), MOON_SIZE, style.moon)
            continue
        if badge.kind == "conflict":
            painter.fillRect(rect, style.err_bg)
            _paint_triangle(painter, rect, style.err)
            continue
        painter.fillRect(rect, style.badge_bg)
        painter.setPen(style.badge_text)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), badge.text)


def _paint_cut_fade(painter: QPainter, rect: QRectF, style: BarStyle) -> None:
    fade = QLinearGradient(rect.right() - CUT_FADE, 0, rect.right(), 0)
    clear = QColor(style.fade)
    clear.setAlphaF(0.0)
    fade.setColorAt(0.0, clear)
    fade.setColorAt(1.0, style.fade)
    area = QRectF(rect.right() - CUT_FADE, rect.top() + 1, CUT_FADE - 1, rect.height() - 2)
    painter.fillRect(area, fade)


def _paint_cut_edge(painter: QPainter, rect: QRectF, color: QColor) -> None:
    """Paper version of the fade: a dashed end, like the start of a bar cut on the left."""
    _paint_dashes(painter, QRectF(rect.right() - STRIPE, rect.top(), STRIPE, rect.height()), color)


def _paint_outline(painter: QPainter, rect: QRectF, style: BarStyle, state: BarState) -> None:
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if state.ghost:
        painter.setPen(style.ghost_pen)
        painter.drawRect(rect.adjusted(0.75, 0.75, -0.75, -0.75))
    elif state.selected:
        painter.setPen(style.select_pen)
        painter.drawRect(rect.adjusted(1, 1, -1, -1))
        paint_corner_marks(painter, rect, "accent-900")
        if state.focus:
            painter.setPen(style.focus_pen)
            ring = rect.adjusted(-FOCUS_OFFSET, -FOCUS_OFFSET, FOCUS_OFFSET, FOCUS_OFFSET)
            painter.drawRect(ring)
    elif state.sibling:
        painter.setPen(style.sibling_pen)
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))


def _paint_marks(
    painter: QPainter, bar: BarData, rect: QRectF, style: BarStyle, state: BarState
) -> None:
    if has_cut_mark(bar, rect.width()):
        if state.printing:
            _paint_cut_edge(painter, rect, style.colors(bar.colors).stripe)
        else:
            _paint_cut_fade(painter, rect, style)
    if state.pending:
        dot = QRectF(rect.left() + STRIPE + 2, rect.top() + 2, PENDING_DOT, PENDING_DOT)
        painter.fillRect(dot, style.pending)
    if state.flash > 0:
        flash = QColor(style.flash)
        flash.setAlphaF(FLASH_ALPHA * state.flash)
        painter.fillRect(rect, flash)
    if state.warn:
        painter.setPen(make_pen(style.err))
        painter.drawLines(hatch_lines(rect, ABSENCE_HATCH_SPACING))
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
    _paint_outline(painter, rect, style, state)


def _lone_moon(bar: BarData, layout: BarText, rect: QRectF) -> bool:
    """Whether a night bar too narrow for its badges and texts gets a moon in its middle."""
    if not bar.night or any(badge.kind == "night" for badge, _rect in layout.badges):
        return False
    return rect.width() >= MOON_SIZE + STRIPE + 4 and not layout.number and not layout.head


def paint_bar(
    painter: QPainter,
    bar: BarData,
    rect: QRectF,
    style: BarStyle,
    state: BarState,
    *,
    text: BarText | None = None,
) -> None:
    """Paint one bar in ``rect``: fill and stripe by status, texts, badges, marks."""
    painter.save()
    colors = style.colors(bar.colors)
    if state.ghost:
        painter.setOpacity(GHOST_OPACITY)
    elif bar.done:
        painter.setOpacity(DONE_OPACITY)
    if bar.night:
        _paint_night_fill(painter, bar, rect, style, state)
    else:
        _paint_fill(painter, bar, rect, colors, state)
    layout = text or layout_bar_text(bar, rect.width(), rect.height(), style, state)
    painter.translate(rect.topLeft())
    _paint_text(painter, bar, layout, style)
    _paint_badges(painter, layout, style)
    if _lone_moon(bar, layout, rect):
        # Too narrow for its badges (month view): the moon alone tells the night.
        center = QPointF(STRIPE + (rect.width() - STRIPE) / 2, rect.height() / 2)
        paint_moon(painter, center, min(MOON_SIZE, rect.height() - 4), style.moon)
    painter.translate(-rect.topLeft())
    painter.setOpacity(1.0)
    _paint_marks(painter, bar, rect, style, state)
    painter.restore()


class BarItem(QGraphicsObject):
    """One ``BarData`` on the scene, painted by ``paint_bar``."""

    def __init__(self, board: PlanningScene, bar: BarData) -> None:
        super().__init__()
        self.board = board
        self.bar = bar
        self.width = 0.0
        self.height = 0.0
        self.hovered = False
        self.selected = False
        self.current = False
        self.sibling = False
        self.lifted = False
        self.pending = False
        self.warn = False
        self.flash_level = 0.0
        self._text: BarText | None = None
        self._text_key: tuple[object, ...] = ()
        self.setZValue(self.rest_z())
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    @property
    def key(self) -> BarKey:
        return self.bar.key

    @property
    def pinned(self) -> bool:
        """On the « Non affecté » row, which stays under the time header."""
        return self.bar.row_id == UNASSIGNED_ROW_ID

    def rest_z(self) -> float:
        base = Z_PINNED_BAR if self.pinned else Z_BAR
        return base + (Z_SELECTED_BUMP if self.selected else 0.0)

    def set_bar(self, bar: BarData) -> None:
        if bar != self.bar:
            self.bar = bar
            self._text = None
            self.update()

    def set_geometry(self, rect: QRectF) -> None:
        if rect.width() != self.width or rect.height() != self.height:
            self.prepareGeometryChange()
            self.width = rect.width()
            self.height = rect.height()
        if rect.topLeft() != self.pos():
            self.setPos(rect.topLeft())

    def scene_rect(self) -> QRectF:
        return QRectF(self.pos(), QSizeF(self.width, self.height))

    def set_flags(self, **flags: bool) -> None:
        """Change hovered, selected, current, sibling, lifted, pending or warn; repaint if so."""
        changed = False
        for name, value in flags.items():
            if getattr(self, name) != value:
                if name == "selected":
                    self.prepareGeometryChange()
                setattr(self, name, value)
                changed = True
        if changed:
            self.setZValue(Z_DRAGGED if self.lifted else self.rest_z())
            self.update()

    def zone_at(self, x: float) -> BarZone:
        """Grab zone under the local ``x``: an end (6 px, a third of a short bar) or the body."""
        model = self.board.model
        if self.bar.night and (model is None or not model.night_axis):
            # A night over a whole column has no edge to drag: its hours are not there.
            return "body"
        edge = min(EDGE_ZONE, self.width / 3)
        if x <= edge and not self.bar.cut_left:
            return "start"
        if x >= self.width - edge and not self.bar.cut_right:
            return "end"
        return "body"

    def state(self) -> BarState:
        return BarState(
            hovered=self.hovered,
            selected=self.selected,
            sibling=self.sibling,
            lifted=self.lifted,
            pending=self.pending,
            flash=self.flash_level,
            warn=self.warn,
            shift=self.board.sticky_left - self.x(),
            day_view=self.board.day_view,
            focus=self.current and self.selected and self.board.focused,
        )

    # QGraphicsItem ----------------------------------------------------

    def boundingRect(self) -> QRectF:  # noqa: N802
        margin = MARK_MARGIN if self.selected else 0.0
        return QRectF(-margin, -margin, self.width + 2 * margin, self.height + 2 * margin)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(QRectF(0, 0, self.width, self.height))
        return path

    def paint(
        self,
        painter: QPainter,
        _option: QStyleOptionGraphicsItem,
        _widget: QWidget | None = None,
    ) -> None:
        style = self.board.bar_style
        state = self.state()
        key = (self.width, self.height, id(style), round(state.shift))
        if self._text is None or key != self._text_key:
            self._text = layout_bar_text(self.bar, self.width, self.height, style, state)
            self._text_key = key
        rect = QRectF(0, 0, self.width, self.height)
        paint_bar(painter, self.bar, rect, style, state, text=self._text)


class GhostItem(QGraphicsObject):
    """Dashed preview of a copy, an added person or a card dropped from « À planifier »."""

    def __init__(self, board: PlanningScene) -> None:
        super().__init__()
        self.board = board
        self.bar: BarData | None = None
        self.width = 0.0
        self.height = 0.0
        self.warn = False
        self.setZValue(Z_GHOST)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setVisible(False)

    def show_bar(self, bar: BarData, rect: QRectF, *, warn: bool) -> None:
        if rect.width() != self.width or rect.height() != self.height:
            self.prepareGeometryChange()
            self.width, self.height = rect.width(), rect.height()
        self.bar = bar
        self.warn = warn
        self.setPos(rect.topLeft())
        self.setVisible(True)
        self.update()

    def scene_rect(self) -> QRectF:
        return QRectF(self.pos(), QSizeF(self.width, self.height))

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(0, 0, self.width, self.height)

    def paint(
        self,
        painter: QPainter,
        _option: QStyleOptionGraphicsItem,
        _widget: QWidget | None = None,
    ) -> None:
        if self.bar is None:
            return
        state = BarState(ghost=True, warn=self.warn, day_view=self.board.day_view)
        rect = QRectF(0, 0, self.width, self.height)
        paint_bar(painter, self.bar, rect, self.board.bar_style, state)


class RangeItem(QGraphicsObject):
    """Cells selected by a drag in the empty grid, or the lasso of Shift+drag."""

    def __init__(self) -> None:
        super().__init__()
        self.area = QRectF()
        self.lasso = False
        self.setZValue(Z_RANGE)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setVisible(False)

    def show_area(self, area: QRectF, *, lasso: bool = False) -> None:
        if area != self.area:
            self.prepareGeometryChange()
            self.area = QRectF(area)
        self.lasso = lasso
        self.setVisible(True)
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self.area.adjusted(-1, -1, 1, 1)

    def paint(
        self,
        painter: QPainter,
        _option: QStyleOptionGraphicsItem,
        _widget: QWidget | None = None,
    ) -> None:
        theme = current_theme()
        if not self.lasso:
            painter.fillRect(self.area, theme.color("accent", 0.18))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        style = Qt.PenStyle.DashLine if self.lasso else Qt.PenStyle.SolidLine
        painter.setPen(make_pen(theme.color("accent"), 1.5, style))
        painter.drawRect(self.area.adjusted(0.75, 0.75, -0.75, -0.75))


class DragLabel(QGraphicsObject):
    """Floating label with the snapped dates while a bar or a card is dragged."""

    def __init__(self) -> None:
        super().__init__()
        self.setZValue(Z_LABEL)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setVisible(False)
        self._font = heading_font(13)
        self._metrics = QFontMetricsF(self._font)
        self.text = ""
        self.warn = False
        self._size = QSizeF(0, 0)

    def show_text(self, text: str, anchor: QRectF, visible: QRectF, *, warn: bool = False) -> None:
        if text != self.text:
            self.prepareGeometryChange()
            self.text = text
            self._size = QSizeF(
                math.ceil(self._metrics.horizontalAdvance(text) + 2 * LABEL_PADDING),
                math.ceil(self._metrics.height() + LABEL_PADDING),
            )
        self.warn = warn
        y = anchor.top() - self._size.height() - LABEL_OFFSET
        if y < visible.top() + 2:
            y = anchor.bottom() + LABEL_OFFSET
        right_limit = visible.right() - self._size.width() - LABEL_EDGE
        left_limit = visible.left() + LABEL_EDGE
        x = min(max(anchor.left(), left_limit), max(right_limit, left_limit))
        self.setPos(round(x), round(y))
        self.setVisible(True)
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(QPointF(0, 0), self._size)

    def paint(
        self,
        painter: QPainter,
        _option: QStyleOptionGraphicsItem,
        _widget: QWidget | None = None,
    ) -> None:
        theme = current_theme()
        rect = QRectF(QPointF(0, 0), self._size)
        painter.fillRect(rect, theme.color("surface"))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(make_pen(theme.color("err" if self.warn else "accent")))
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        painter.setFont(self._font)
        painter.setPen(theme.color("err-ink" if self.warn else "text"))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), self.text)


class PinnedBand(QGraphicsObject):
    """The « Non affecté » row repainted at the top of the view once the rows scroll."""

    def __init__(self, board: PlanningScene) -> None:
        super().__init__()
        self.board = board
        self.band_width = 0.0
        self.band_height = 0.0
        self.setZValue(Z_BAND)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption, enabled=True)
        self.setVisible(False)

    def set_band(self, width: float, height: float) -> None:
        if (width, height) != (self.band_width, self.band_height):
            self.prepareGeometryChange()
            self.band_width, self.band_height = width, height

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(0, 0, self.band_width, self.band_height + BAND_SHADOW)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        _widget: QWidget | None = None,
    ) -> None:
        model = self.board.model
        row = model.rows.get(UNASSIGNED_ROW_ID) if model is not None else None
        if model is None or row is None:
            return
        band = QRectF(0, 0, self.band_width, row.height)
        exposed = QRectF(option.exposedRect).intersected(band)
        style = self.board.grid_style
        painter.save()
        painter.translate(0, -row.top)
        area = QRectF(exposed.left(), row.top, exposed.width(), row.height)
        paint_background(painter, model, area, style, drop_row=self.board.drop_row)
        painter.restore()
        shadow = QLinearGradient(0, row.height, 0, row.height + BAND_SHADOW)
        clear = QColor(style.shadow)
        clear.setAlphaF(0.0)
        shadow.setColorAt(0.0, style.shadow)
        shadow.setColorAt(1.0, clear)
        painter.fillRect(QRectF(exposed.left(), row.height, exposed.width(), BAND_SHADOW), shadow)


# ----------------------------------------------------------------- scene ----


class PlanningScene(QGraphicsScene):
    """Bars and previews of the Planning body; the background is painted from the model."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model: GridModel | None = None
        theme = current_theme()
        self.grid_style = build_grid_style(theme)
        self.bar_style = build_bar_style(theme, compact=False)
        self.items_by_key: dict[BarKey, BarItem] = {}
        self.items_by_task: dict[str, list[BarItem]] = {}
        self.drop_row: str | None = None
        self.sticky_left = 0.0
        self.pin_offset = 0.0
        self.day_view = False
        self.focused = False
        self.ghost = GhostItem(self)
        self.range_item = RangeItem()
        self.label = DragLabel()
        self.band = PinnedBand(self)
        for item in (self.ghost, self.range_item, self.label, self.band):
            self.addItem(item)
        self._pending: frozenset[str] = frozenset()
        self._flash_clock = QElapsedTimer()
        self._flash_until: dict[str, int] = {}
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(FLASH_TICK_MS)
        self._flash_timer.timeout.connect(self._flash_tick)
        self.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.BspTreeIndex)

    # Model ------------------------------------------------------------

    def set_model(self, model: GridModel) -> None:
        compact = int(model.density) < TWO_LINE_HEIGHT + 2
        growth = text_growth(model.density)
        if (compact, growth) != (self.bar_style.compact, self.bar_style.growth):
            self.bar_style = build_bar_style(self.grid_style.theme, compact=compact, growth=growth)
        self.model = model
        self.day_view = model.view == "day"
        stale = set(self.items_by_key)
        by_task: dict[str, list[BarItem]] = {}
        for data in model.bars:
            item = self.items_by_key.get(data.key)
            if item is None:
                item = BarItem(self, data)
                self.addItem(item)
                self.items_by_key[data.key] = item
            else:
                stale.discard(data.key)
                item.set_bar(data)
            item.set_flags(pending=data.task_id in self._pending, lifted=False, warn=False)
            item.setZValue(item.rest_z())
            by_task.setdefault(data.task_id, []).append(item)
        for key in stale:
            self.removeItem(self.items_by_key.pop(key))
        self.items_by_task = by_task
        self.setSceneRect(QRectF(0, 0, model.timescale.width, max(model.rows.height, 1)))
        self._place_band()
        self.place_all()
        self.invalidate(self.sceneRect(), QGraphicsScene.SceneLayer.BackgroundLayer)

    def restyle(self) -> None:
        """Rebuild the styles after a theme change and repaint everything."""
        theme = current_theme()
        self.grid_style = build_grid_style(theme)
        self.bar_style = build_bar_style(
            theme, compact=self.bar_style.compact, growth=self.bar_style.growth
        )
        for item in self.items_by_key.values():
            item.set_bar(item.bar)
            item.update()
        self.band.update()
        self.invalidate(self.sceneRect(), QGraphicsScene.SceneLayer.AllLayers)
        self.update()

    def row(self, row_id: str) -> RowLayout | None:
        return self.model.rows.get(row_id) if self.model is not None else None

    def row_offset(self, row_id: str) -> float:
        """Extra y of a row drawn pinned under the header (« Non affecté » once scrolled)."""
        return self.pin_offset if row_id == UNASSIGNED_ROW_ID else 0.0

    def row_top(self, row: RowLayout) -> float:
        return row.top + self.row_offset(row.resource_id)

    def row_at(self, y: float, *, clamp: bool = False) -> RowLayout | None:
        """Row drawn under the scene ``y``, the pinned « Non affecté » band included."""
        if self.model is None:
            return None
        pinned = self.model.rows.get(UNASSIGNED_ROW_ID)
        if self.pin_offset > 0 and pinned is not None:
            top = self.pin_offset + pinned.top
            if top <= y < top + pinned.height:
                return pinned
        return self.model.rows.row_at(y, clamp=clamp)

    def span_rect(
        self, start: WallTime, end: WallTime, row_id: str, lane: int = 0, *, night: bool = False
    ) -> QRectF:
        row = self.row(row_id)
        if self.model is None or row is None:
            return QRectF()
        lane = min(lane, max(row.lane_count - 1, 0))
        rect = bar_rect(self.model, row, start, end, lane, night=night)
        return rect.translated(0, self.row_offset(row_id))

    def rect_of(self, bar: BarData) -> QRectF:
        return self.span_rect(bar.start, bar.end, bar.row_id, bar.lane, night=bar.night)

    def place(self, item: BarItem) -> None:
        item.set_geometry(self.rect_of(item.bar))

    def place_all(self) -> None:
        for item in self.items_by_key.values():
            self.place(item)

    def place_task(self, task_id: str) -> None:
        """Put the bars of ``task_id`` back where the model has them."""
        for item in self.items_by_task.get(task_id, ()):
            item.set_flags(lifted=False, warn=False)
            item.setZValue(item.rest_z())
            self.place(item)

    def bar_at(self, point: QPointF) -> BarItem | None:
        """Topmost visible bar under ``point``; the pinned band hides the rows under it."""
        for item in self.items(point):
            if isinstance(item, BarItem) and item.isVisible():
                return item
            if item is self.band:
                return None
        return None

    def absence_at(self, point: QPointF) -> AbsenceBlock | None:
        """Hatched absence block under ``point`` when no bar is there (bars win)."""
        if self.model is None or self.bar_at(point) is not None:
            return None
        if any(item is self.band for item in self.items(point)):
            return None
        row = self.row_at(point.y())
        if row is None or row.kind != "resource":
            return None
        for block in self.model.absences_by_row.get(row.resource_id, ()):
            if absence_rect(self.model, block, row).contains(point):
                return block
        return None

    def bars_in(self, area: QRectF) -> list[BarItem]:
        return [item for item in self.items(area) if isinstance(item, BarItem)]

    # Pinned row -------------------------------------------------------

    def set_pin_offset(self, offset: float) -> None:
        """Keep « Non affecté » under the header while the rows scroll by ``offset``."""
        offset = max(offset, 0.0)
        if offset == self.pin_offset:
            return
        self.pin_offset = offset
        self._place_band()
        for item in self.items_by_key.values():
            if item.pinned and not item.lifted:
                self.place(item)

    def _place_band(self) -> None:
        row = self.row(UNASSIGNED_ROW_ID)
        if self.model is None or row is None or self.pin_offset <= 0:
            self.band.setVisible(False)
            return
        self.band.set_band(self.model.timescale.width, float(row.height))
        self.band.setPos(0, row.top + self.pin_offset)
        self.band.setVisible(True)
        self.band.update()

    # State ------------------------------------------------------------

    def set_selection(
        self,
        keys: Collection[BarKey],
        hovered: BarItem | None = None,
        current: BarKey | None = None,
    ) -> None:
        """Mark the selected bars, their siblings, the hovered bar and the keyboard's one."""
        selected = set(keys)
        tasks = {task_id for task_id, _row in selected}
        if hovered is not None:
            tasks.add(hovered.bar.task_id)
        for item in self.items_by_key.values():
            is_selected = item.key in selected
            item.set_flags(
                selected=is_selected,
                current=is_selected and item.key == current,
                sibling=not is_selected and item.bar.task_id in tasks and item is not hovered,
                hovered=item is hovered,
            )

    def set_focused(self, focused: bool) -> None:  # noqa: FBT001
        """Show or hide the ring of the current bar as the view gets or loses the keyboard."""
        if focused == self.focused:
            return
        self.focused = focused
        for item in self.items_by_key.values():
            if item.current:
                item.update()

    def set_drop_row(self, row_id: str | None) -> None:
        if row_id == self.drop_row:
            return
        for name in (self.drop_row, row_id):
            row = self.row(name) if name is not None else None
            if row is not None:
                self.invalidate(
                    QRectF(0, row.top, self.sceneRect().width(), row.height),
                    QGraphicsScene.SceneLayer.BackgroundLayer,
                )
        self.drop_row = row_id
        self.band.update()

    def set_sticky_left(self, x: float) -> None:
        """Keep the text of bars cut by the left edge of the view readable."""
        if x == self.sticky_left:
            return
        height = max(self.sceneRect().height(), 1.0)
        touched = [
            *self.items(QRectF(self.sticky_left, 0, 1, height)),
            *self.items(QRectF(x, 0, 1, height)),
        ]
        self.sticky_left = x
        for item in touched:
            if isinstance(item, BarItem):
                item.update()

    def set_pending(self, task_ids: Iterable[str]) -> None:
        self._pending = frozenset(task_ids)
        for item in self.items_by_key.values():
            item.set_flags(pending=item.bar.task_id in self._pending)

    def pending(self) -> frozenset[str]:
        return self._pending

    def flash(self, task_ids: Iterable[str]) -> None:
        if not self._flash_clock.isValid():
            self._flash_clock.start()
        until = self._flash_clock.elapsed() + FLASH_MS
        for task_id in task_ids:
            self._flash_until[task_id] = until
        if self._flash_until:
            self._flash_tick()
            self._flash_timer.start()

    def flashing(self) -> frozenset[str]:
        return frozenset(self._flash_until)

    def _flash_tick(self) -> None:
        now = self._flash_clock.elapsed()
        for task_id, until in list(self._flash_until.items()):
            level = max(0.0, (until - now) / FLASH_MS)
            for item in self.items_by_task.get(task_id, ()):
                item.flash_level = level
                item.update()
            if level <= 0:
                del self._flash_until[task_id]
        if not self._flash_until:
            self._flash_timer.stop()

    def hide_previews(self) -> None:
        self.ghost.setVisible(False)
        self.label.setVisible(False)
        self.range_item.setVisible(False)
        self.set_drop_row(None)

    # Painting ---------------------------------------------------------

    def drawBackground(self, painter: QPainter, rect: QRectF | QRect) -> None:  # noqa: N802
        area = QRectF(rect)
        if self.model is None:
            painter.fillRect(area, self.grid_style.bg)
            return
        paint_background(painter, self.model, area, self.grid_style, drop_row=self.drop_row)

    def drawForeground(self, painter: QPainter, rect: QRectF | QRect) -> None:  # noqa: N802
        if self.model is not None:
            paint_now_line(painter, self.model, QRectF(rect), self.grid_style.now_color)
