"""Time header of the Planning grid: week or month band, days, hours, holidays, today.

``paint_time_header`` draws the bands for the scene columns between ``x0`` and
``x0 + width``; the widget follows the body's horizontal scroll bar and the PDF export
calls the same function for every page. The week view has three: the week, the days and
their half days (« Matin », « Après-midi », the workshop's real unit).
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QFont, QMouseEvent, QPainter, QPainterPath, QPaintEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

from projectflow.core.planning.timescale import (
    WEEKDAY_ABBREVIATIONS,
    ColumnGroup,
    DayColumn,
    day_label,
    month_label,
)
from projectflow.core.planning.walltime import MINUTES_PER_HOUR, day_start, format_hhmm
from projectflow.ui.planning.grid_items import alternate_fill, hatch_lines, make_pen, now_x
from projectflow.ui.planning.grid_model import GridModel, column_tinted
from projectflow.ui.theme import Theme, body_font, current_theme, heading_font

HEADER_HEIGHT = 52
BAND_HEIGHT = 18
"""Upper band: week numbers (month view), the week (week view) or the day (day view)."""
HALF_DAY_HEIGHT = 14
"""Lower band of the week view: « Matin » and « Après-midi » under each day."""
DAY_ROW_HEIGHT = HEADER_HEIGHT - BAND_HEIGHT - HALF_DAY_HEIGHT
HALF_DAY_NAMES: tuple[tuple[str, ...], ...] = (("Matin", "Mat."), ("Après-midi", "Aprem", "Apm"))
"""Names of the worked periods of a day, longest first: the first that fits is shown."""
HALF_DAY_INSET = 6.0
TICK_HEIGHT = 4
LABEL_INSET = 8.0
TODAY_LINE = 2.0
NOW_MARKER = 5.0
MONTH_LONG_LABEL_WIDTH = 44.0
"""Narrower month columns show the day number alone."""
HOUR_LABEL_MIN_WIDTH = 34.0
"""Hours are labelled only when an hour is at least this wide."""
TODAY_SUFFIX = "aujourd'hui"


def _sticky(painter: QPainter, text: str, left: float, right: float, x0: float) -> None:
    """Draw a band label that stays readable while its period scrolls out on the left."""
    metrics = painter.fontMetrics()
    room = right - max(left, x0) - 2 * LABEL_INSET
    if room <= 0:
        return
    shown = metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(room))
    x = max(left, x0) + LABEL_INSET
    painter.drawText(QRectF(x, 0, room + 2, BAND_HEIGHT), int(Qt.AlignmentFlag.AlignVCenter), shown)


def _groups(model: GridModel) -> tuple[ColumnGroup, ...]:
    scale = model.timescale
    return scale.week_groups(short=model.view == "month")


def _group_label(model: GridModel, group: ColumnGroup) -> str:
    if model.view == "month":
        return group.label
    first = model.timescale.columns[group.columns.start].day
    return f"{group.label} · {month_label(first)}"


def _day_title(model: GridModel) -> str:
    day = model.timescale.first_day
    parts = [f"{day_label(day)}.{day.year}"]
    holiday = model.holidays.get(day)
    if holiday is not None:
        parts.append(holiday.label)
    elif day in model.closures:
        parts.append(model.closures[day])
    if day == model.today:
        parts.append(TODAY_SUFFIX)
    return " · ".join(parts)


def _paint_top_band(
    painter: QPainter, model: GridModel, theme: Theme, x0: float, width: float
) -> None:
    painter.setFont(heading_font(14))
    divider = make_pen(theme.color("text", 0.30))
    if model.view == "day":
        painter.setPen(theme.color("accent-800" if model.today_index is not None else "text"))
        _sticky(painter, _day_title(model), 0.0, model.timescale.width, x0)
        return
    for group in _groups(model):
        if group.x + group.width < x0 or group.x > x0 + width:
            continue
        painter.setPen(theme.color("text"))
        _sticky(painter, _group_label(model, group), group.x, group.x + group.width, x0)
        if group.columns.start > 0:
            painter.setPen(divider)
            x = round(group.x) + 0.5
            painter.drawLine(QLineF(x, 0, x, HEADER_HEIGHT))


def _day_text(model: GridModel, column: DayColumn) -> str:
    if model.view == "month" and column.width < MONTH_LONG_LABEL_WIDTH:
        return str(column.day.day)
    return f"{WEEKDAY_ABBREVIATIONS[column.day.weekday()].capitalize()} {column.day.day}"


def _note(model: GridModel, day: date) -> str:
    holiday = model.holidays.get(day)
    if holiday is not None:
        return holiday.label
    return model.closures.get(day, "")


def _paint_cell_ground(
    painter: QPainter, model: GridModel, theme: Theme, column: DayColumn, *, printing: bool
) -> None:
    cell = QRectF(column.x, BAND_HEIGHT, column.width, HEADER_HEIGHT - BAND_HEIGHT)
    _paint_alternate(painter, model, theme, column)
    holiday = model.holidays.get(column.day)
    closed = column.day in model.closures
    if holiday is not None or closed:
        painter.fillRect(cell, theme.color("absence", 0.12 if theme.is_dark else 0.09))
        strong = closed or (holiday is not None and holiday.everywhere)
        painter.setPen(make_pen(theme.color("absence", 0.40 if strong else 0.22)))
        painter.drawLines(hatch_lines(cell))
    elif column.day == model.today and not printing:
        painter.fillRect(cell, theme.color("accent-100"))
    if column.day == model.today and not printing:
        painter.fillRect(
            QRectF(column.x, HEADER_HEIGHT - TODAY_LINE, column.width, TODAY_LINE),
            theme.color("accent"),
        )


def _paint_alternate(painter: QPainter, model: GridModel, theme: Theme, column: DayColumn) -> None:
    """Tint the header cells of an alternate column as the body under it.

    The month view alternates whole weeks: their band of week numbers is tinted too.
    """
    if not column_tinted(model.view, column.day):
        return
    top = 0.0 if model.view == "month" else BAND_HEIGHT
    painter.fillRect(
        QRectF(column.x, top, column.width, HEADER_HEIGHT - top), alternate_fill(theme)
    )


def half_day_labels(model: GridModel, column: DayColumn) -> tuple[tuple[str, float, float], ...]:
    """Week view: the worked periods of a day as ``(name, left, right)`` scene x.

    « Matin » then « Après-midi » (« Matin » alone on Friday); none on a holiday or a
    closure, whose name the day row already shows.
    """
    info = model.days[column.index]
    if not info.workday:
        return ()
    scale = model.timescale
    base = day_start(column.day)
    periods = scale.calendar.periods_of_weekday(column.day.weekday())
    return tuple(
        (names[0], scale.x_of(base + low), scale.x_of(base + high))
        for names, (low, high) in zip(HALF_DAY_NAMES, periods, strict=False)
    )


def _paint_half_days(
    painter: QPainter, model: GridModel, theme: Theme, column: DayColumn, *, today: bool
) -> None:
    """Lower band of a week day: its half days named, the lunch break hatched between."""
    top = HEADER_HEIGHT - HALF_DAY_HEIGHT
    info = model.days[column.index]
    if info.workday:
        scale = model.timescale
        base = day_start(column.day)
        painter.setPen(make_pen(theme.color("text", 0.08)))
        for low, high in info.off:
            left, right = scale.x_of(base + low), scale.x_of(base + high)
            if right > left:
                area = QRectF(left, top, right - left, HALF_DAY_HEIGHT)
                painter.fillRect(area, theme.color("text", 0.03))
                painter.drawLines(hatch_lines(area))
    painter.setPen(make_pen(theme.color("text", 0.08)))
    painter.drawLine(QLineF(column.x, top + 0.5, column.x + column.width, top + 0.5))
    painter.setFont(body_font(10, QFont.Weight.DemiBold))
    metrics = painter.fontMetrics()
    painter.setPen(theme.color("accent-800" if today else "neutral-700"))
    for index, (_name, left, right) in enumerate(half_day_labels(model, column)):
        room = right - left - 2 * HALF_DAY_INSET
        shown = next(
            (name for name in HALF_DAY_NAMES[index] if metrics.horizontalAdvance(name) <= room),
            "",
        )
        if shown:
            painter.drawText(
                QRectF(left + HALF_DAY_INSET, top, room, HALF_DAY_HEIGHT - TODAY_LINE),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                shown,
            )


def _paint_day_cells(
    painter: QPainter,
    model: GridModel,
    theme: Theme,
    columns: tuple[DayColumn, ...],
    *,
    printing: bool,
) -> None:
    week = model.view == "week"
    day_font = heading_font(15 if week else 12)
    note_font = body_font(11)
    line_pen = make_pen(theme.color("text", 0.14))
    for column in columns:
        _paint_cell_ground(painter, model, theme, column, printing=printing)
        if column.index > 0:
            painter.setPen(line_pen)
            x = round(column.x) + 0.5
            painter.drawLine(QLineF(x, BAND_HEIGHT, x, HEADER_HEIGHT))
        today = column.day == model.today and not printing
        painter.setFont(day_font)
        painter.setPen(theme.color("accent-800" if today else "text"))
        text = _day_text(model, column)
        if not week:
            cell = QRectF(column.x, BAND_HEIGHT, column.width, HEADER_HEIGHT - BAND_HEIGHT)
            painter.drawText(cell, int(Qt.AlignmentFlag.AlignCenter), text)
            continue
        cell = QRectF(column.x, BAND_HEIGHT, column.width, DAY_ROW_HEIGHT)
        painter.drawText(
            cell.adjusted(LABEL_INSET, 0, -4, 0), int(Qt.AlignmentFlag.AlignVCenter), text
        )
        note = _note(model, column.day) or (TODAY_SUFFIX if today else "")
        if note:
            offset = LABEL_INSET + painter.fontMetrics().horizontalAdvance(text) + 8
            painter.setFont(note_font)
            painter.setPen(
                theme.color("accent-800" if today and note == TODAY_SUFFIX else "neutral-800")
            )
            room = int(cell.width() - offset - 6)
            elided = painter.fontMetrics().elidedText(note, Qt.TextElideMode.ElideRight, room)
            painter.drawText(
                cell.adjusted(offset, 0, -6, 0), int(Qt.AlignmentFlag.AlignVCenter), elided
            )
        _paint_half_days(painter, model, theme, column, today=today)


def _paint_hours(
    painter: QPainter, model: GridModel, theme: Theme, column: DayColumn, *, printing: bool
) -> None:
    """Day view: the hours of the single day, breaks hatched, the current hour in accent."""
    scale = model.timescale
    base = day_start(column.day)
    info = model.days[column.index]
    band = HEADER_HEIGHT - BAND_HEIGHT
    _paint_alternate(painter, model, theme, column)
    painter.setPen(make_pen(theme.color("text", 0.10)))
    for low, high in info.off:
        left, right = scale.x_of(base + low), scale.x_of(base + high)
        area = QRectF(left, BAND_HEIGHT, right - left, band)
        painter.fillRect(area, theme.color("text", 0.03))
        painter.drawLines(hatch_lines(area))
    first = scale.first_minute
    last = first + scale.display_minutes
    hour_width = scale.day_width * MINUTES_PER_HOUR / scale.display_minutes
    painter.setFont(heading_font(12, QFont.Weight.Medium))
    minute = first
    while minute < last:
        x = round(scale.x_of(base + minute)) + 0.5
        full = minute % MINUTES_PER_HOUR == 0
        painter.setPen(make_pen(theme.color("text", 0.16 if full else 0.10)))
        top = BAND_HEIGHT if full else HEADER_HEIGHT - TICK_HEIGHT
        if minute > first:
            painter.drawLine(QLineF(x, top, x, HEADER_HEIGHT))
        if full and hour_width >= HOUR_LABEL_MIN_WIDTH:
            painter.setPen(theme.color("neutral-800"))
            painter.drawText(
                QRectF(x + 5, BAND_HEIGHT, hour_width - 6, band - 2),
                int(Qt.AlignmentFlag.AlignVCenter),
                format_hhmm(minute),
            )
        minute += MINUTES_PER_HOUR // 2
    if column.day == model.today and not printing:
        painter.fillRect(
            QRectF(column.x, HEADER_HEIGHT - TODAY_LINE, column.width, TODAY_LINE),
            theme.color("accent"),
        )


def _paint_now(painter: QPainter, model: GridModel, theme: Theme) -> None:
    x = now_x(model)
    if x is None:
        return
    color = theme.color("accent")
    painter.fillRect(QRectF(round(x) - 1, BAND_HEIGHT, 2, HEADER_HEIGHT - BAND_HEIGHT), color)
    marker = QPainterPath()
    marker.moveTo(round(x) - NOW_MARKER, BAND_HEIGHT)
    marker.lineTo(round(x) + NOW_MARKER, BAND_HEIGHT)
    marker.lineTo(round(x), BAND_HEIGHT + NOW_MARKER + 1)
    marker.closeSubpath()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, on=True)
    painter.fillPath(marker, color)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, on=False)


def paint_time_header(
    painter: QPainter,
    model: GridModel,
    theme: Theme,
    *,
    x0: float,
    width: float,
    printing: bool = False,
) -> None:
    """Paint both header bands for the scene columns in ``[x0, x0 + width]`` at painter x 0."""
    painter.save()
    painter.setClipRect(QRectF(0, 0, width, HEADER_HEIGHT))
    painter.fillRect(QRectF(0, 0, width, HEADER_HEIGHT), theme.color("bg"))
    painter.translate(-x0, 0)
    columns = model.timescale.visible_columns(x0, x0 + width)
    if model.view == "day":
        for column in columns:
            _paint_hours(painter, model, theme, column, printing=printing)
    else:
        _paint_day_cells(painter, model, theme, columns, printing=printing)
    _paint_top_band(painter, model, theme, x0, width)
    if not printing:
        _paint_now(painter, model, theme)
    painter.restore()
    painter.setPen(make_pen(theme.color("text", 0.30)))
    painter.drawLine(QLineF(0, BAND_HEIGHT - 0.5, width, BAND_HEIGHT - 0.5))
    painter.drawLine(QLineF(0, HEADER_HEIGHT - 0.5, width, HEADER_HEIGHT - 0.5))


def header_tooltip(model: GridModel, day: date) -> str:
    """Full name of a header day, with its holiday or closure and « aujourd'hui »."""
    parts = [f"{day_label(day)}.{day.year}"]
    note = _note(model, day)
    if note:
        parts.append(note)
    if day == model.today:
        parts.append(TODAY_SUFFIX)
    return " · ".join(parts)


class TimeHeader(QWidget):
    """Header widget above the body.

    A click on a day gives ``day_clicked(date)``, on the week band ``week_clicked(date)``
    with the Monday of that week.
    """

    day_clicked = Signal(object)
    week_clicked = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model: GridModel | None = None
        self.x0 = 0.0
        self.content_width = 0.0
        self.setFixedHeight(HEADER_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def day_at(self, x: float) -> date | None:
        if self.model is None or x > (self.content_width or self.width()):
            return None
        return self.model.timescale.column_at(x + self.x0).day

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        day = self.day_at(event.position().x())
        self.setToolTip(header_tooltip(self.model, day) if self.model and day else "")
        super().mouseMoveEvent(event)

    def set_model(self, model: GridModel | None) -> None:
        self.model = model
        self.update()

    def set_viewport(self, x0: float, width: float) -> None:
        if (x0, width) != (self.x0, self.content_width):
            self.x0, self.content_width = x0, width
            self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        theme = current_theme()
        painter.fillRect(self.rect(), theme.color("bg"))
        if self.model is not None:
            paint_time_header(
                painter, self.model, theme, x0=self.x0, width=self.content_width or self.width()
            )
        painter.setPen(make_pen(theme.color("text", 0.30)))
        painter.drawLine(QLineF(0, HEADER_HEIGHT - 0.5, self.width(), HEADER_HEIGHT - 0.5))
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self.model is None or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        position: QPointF = event.position()
        day = self.day_at(position.x())
        if day is None:
            return
        if position.y() < BAND_HEIGHT and self.model.view != "day":
            self.week_clicked.emit(day - timedelta(days=day.weekday()))
        else:
            self.day_clicked.emit(day)
        event.accept()
