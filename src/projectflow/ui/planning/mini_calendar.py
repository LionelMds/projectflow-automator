"""Small month calendar under « À planifier »: a click on a day brings the planning there.

It shows one month with its week numbers, rings today and tints the period the grid
shows; while the grid scrolls on in time, the month shown follows it. The arrows (or the
wheel) leaf through the months without moving the planning, and the title comes back to
the month of the period.
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QFont,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from projectflow.core.planning.timescale import MONTH_NAMES, WEEKDAY_ABBREVIATIONS, week_start
from projectflow.ui.theme import Theme, body_font, current_theme, theme_notifier

DAYS_PER_WEEK = 7
WEEK_ROWS = 6
MONTHS_PER_YEAR = 12
WEEKEND_START = 5
PADDING_X = 12
PADDING_TOP = 8
PADDING_BOTTOM = 10
TITLE_HEIGHT = 28
WEEKDAY_HEIGHT = 20
ROW_HEIGHT = 24
WEEK_COLUMN = 26
ARROW_WIDTH = 26
ARROW_SIZE = 4.0
DAY_DISC = 22.0
WHEEL_NOTCH = 120
ACCESSIBLE_NAME = "Calendrier du planning"
TITLE_TOOLTIP = "Revenir au mois affiché par le planning"
STEP_KEYS = {
    Qt.Key.Key_Left: -1,
    Qt.Key.Key_Right: 1,
    Qt.Key.Key_Up: -DAYS_PER_WEEK,
    Qt.Key.Key_Down: DAYS_PER_WEEK,
}
CHOOSE_KEYS = frozenset({Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space})


def month_start(day: date) -> date:
    return day.replace(day=1)


def add_months(first: date, months: int) -> date:
    """First day of the month ``months`` after (before when negative) that of ``first``."""
    year, month = divmod(first.year * MONTHS_PER_YEAR + first.month - 1 + months, MONTHS_PER_YEAR)
    return date(year, month + 1, 1)


def month_title(month: date) -> str:
    return f"{MONTH_NAMES[month.month - 1]} {month.year}"


def month_weeks(month: date) -> tuple[tuple[date, ...], ...]:
    """Give the six weeks, Monday first, drawn for ``month``: its days and those around."""
    first = week_start(month_start(month))
    return tuple(
        tuple(first + timedelta(days=DAYS_PER_WEEK * week + day) for day in range(DAYS_PER_WEEK))
        for week in range(WEEK_ROWS)
    )


class MiniCalendar(QWidget):
    """One month to click in: ``day_chosen`` carries the day the planning should show."""

    day_chosen = Signal(object)
    month_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._today = date.today()
        self._month = month_start(self._today)
        self._period: tuple[date, date] = (self._today, self._today)
        self._hovered: date | None = None
        self._hovered_week = False
        """The pointer is on the week number of ``_hovered`` (a Monday), not on a day."""
        self._hovered_arrow = 0
        self._cursor: date | None = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(ACCESSIBLE_NAME)
        self.setAccessibleDescription(
            "Flèches : choisir un jour ; Entrée : l'afficher ; Page préc. / suiv. : autre mois."
        )
        theme_notifier().changed.connect(self.update)

    # -- public API -----------------------------------------------------------

    def today(self) -> date:
        return self._today

    def month(self) -> date:
        """First day of the month shown."""
        return self._month

    def period(self) -> tuple[date, date]:
        return self._period

    def title(self) -> str:
        return month_title(self._month)

    def set_today(self, today: date) -> None:
        if today != self._today:
            self._today = today
            self.update()

    def set_period(self, first: date, last: date) -> None:
        """Tint ``first``..``last`` (what the grid shows) and show the month it starts in.

        A week across two months stays on the month already shown when it holds a day of it.
        """
        period = (first, last)
        if period == self._period:
            return
        self._period = period
        shown = month_weeks(self._month)
        visible = shown[0][0] <= first and last <= shown[-1][-1]
        inside = any(month_start(day) == self._month for day in (first, last))
        if not (visible and inside):
            self.set_month(first)
        self.update()

    def set_month(self, day: date) -> None:
        month = month_start(day)
        if month != self._month:
            self._month = month
            self._cursor = None
            self.month_changed.emit(month)
            self.update()

    def step_month(self, steps: int) -> None:
        self.set_month(add_months(self._month, steps))

    def show_period_month(self) -> None:
        """Come back to the month of the period shown by the grid."""
        self.set_month(self._period[0])

    def day_rect(self, day: date) -> QRectF | None:
        """Cell of ``day`` in the month shown, None when it is not drawn."""
        for row, week in enumerate(month_weeks(self._month)):
            if week[0] <= day <= week[-1]:
                return self._cell(row, (day - week[0]).days)
        return None

    def day_at(self, point: QPointF) -> date | None:
        left, top, column = self._grid_origin()
        if point.x() < left or point.y() < top:
            return None
        row = int((point.y() - top) // ROW_HEIGHT)
        index = int((point.x() - left) // column)
        if not (0 <= row < WEEK_ROWS and 0 <= index < DAYS_PER_WEEK):
            return None
        return month_weeks(self._month)[row][index]

    def week_at(self, point: QPointF) -> date | None:
        """Monday of the week whose number is under ``point``."""
        _left, top, _column = self._grid_origin()
        if not (PADDING_X <= point.x() < PADDING_X + WEEK_COLUMN) or point.y() < top:
            return None
        row = int((point.y() - top) // ROW_HEIGHT)
        return month_weeks(self._month)[row][0] if 0 <= row < WEEK_ROWS else None

    # -- geometry -------------------------------------------------------------

    def sizeHint(self) -> QSize:  # noqa: N802
        height = PADDING_TOP + TITLE_HEIGHT + WEEKDAY_HEIGHT + WEEK_ROWS * ROW_HEIGHT
        return QSize(260, height + PADDING_BOTTOM)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(220, self.sizeHint().height())

    def _grid_origin(self) -> tuple[float, float, float]:
        left = PADDING_X + WEEK_COLUMN
        column = max((self.width() - left - PADDING_X) / DAYS_PER_WEEK, 1.0)
        return float(left), float(PADDING_TOP + TITLE_HEIGHT + WEEKDAY_HEIGHT), column

    def _cell(self, row: int, index: int) -> QRectF:
        left, top, column = self._grid_origin()
        return QRectF(left + index * column, top + row * ROW_HEIGHT, column, ROW_HEIGHT)

    def _arrow_rect(self, direction: int) -> QRectF:
        right = self.width() - PADDING_X
        x = right - ARROW_WIDTH if direction > 0 else right - 2 * ARROW_WIDTH
        return QRectF(x, PADDING_TOP, ARROW_WIDTH, TITLE_HEIGHT)

    def _title_rect(self) -> QRectF:
        width = self.width() - 2 * PADDING_X - 2 * ARROW_WIDTH
        return QRectF(PADDING_X, PADDING_TOP, max(width, 0), TITLE_HEIGHT)

    def _arrow_at(self, point: QPointF) -> int:
        for direction in (-1, 1):
            if self._arrow_rect(direction).contains(point):
                return direction
        return 0

    # -- events ---------------------------------------------------------------

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        point = event.position()
        week = self.week_at(point)
        hovered = self.day_at(point) or week
        arrow = self._arrow_at(point)
        clickable = hovered is not None or arrow or self._title_rect().contains(point)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor
        )
        self.setToolTip(TITLE_TOOLTIP if self._title_rect().contains(point) and not arrow else "")
        state = (hovered, week is not None, arrow)
        if state != (self._hovered, self._hovered_week, self._hovered_arrow):
            self._hovered, self._hovered_week, self._hovered_arrow = state
            self.update()

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self._hovered, self._hovered_week, self._hovered_arrow = None, False, 0
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        point = event.position()
        arrow = self._arrow_at(point)
        day = self.day_at(point) or self.week_at(point)
        if arrow:
            self.step_month(arrow)
        elif self._title_rect().contains(point):
            self.show_period_month()
        elif day is not None:
            self.day_chosen.emit(day)
        event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        delta = event.angleDelta().y() or event.angleDelta().x()
        if abs(delta) >= WHEEL_NOTCH:
            self.step_month(-1 if delta > 0 else 1)
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        cursor = self._cursor or self._shown_start()
        if key in STEP_KEYS:
            self._move_cursor(cursor + timedelta(days=STEP_KEYS[Qt.Key(key)]))
        elif key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            self.step_month(-1 if key == Qt.Key.Key_PageUp else 1)
        elif key in CHOOSE_KEYS:
            self.day_chosen.emit(cursor)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def _shown_start(self) -> date:
        first = self._period[0]
        return first if month_start(first) == self._month else self._month

    def _move_cursor(self, day: date) -> None:
        self.set_month(day)
        self._cursor = day
        self.update()

    # -- painting -------------------------------------------------------------

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        theme = current_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), theme.color("bg"))
        painter.fillRect(QRectF(0, 0, self.width(), 1), theme.color("text", 0.18))
        self._paint_title(painter, theme)
        self._paint_weekdays(painter, theme)
        for row, week in enumerate(month_weeks(self._month)):
            self._paint_week(painter, theme, row, week)
        painter.end()

    def _paint_title(self, painter: QPainter, theme: Theme) -> None:
        painter.setFont(body_font(13, QFont.Weight.DemiBold))
        painter.setPen(theme.color("text"))
        title = self._title_rect().adjusted(4, 0, 0, 0)
        painter.drawText(title, int(Qt.AlignmentFlag.AlignVCenter), self.title().capitalize())
        for direction in (-1, 1):
            rect = self._arrow_rect(direction)
            if self._hovered_arrow == direction:
                painter.fillRect(rect.adjusted(1, 3, -1, -3), theme.color("text", 0.07))
            pen = QPen(theme.color("neutral-800"))
            pen.setWidthF(1.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            center = rect.center()
            tip = QPointF(center.x() + direction * ARROW_SIZE / 2, center.y())
            back = center.x() - direction * ARROW_SIZE / 2
            painter.drawPolyline(
                [
                    QPointF(back, center.y() - ARROW_SIZE),
                    tip,
                    QPointF(back, center.y() + ARROW_SIZE),
                ]
            )

    def _paint_weekdays(self, painter: QPainter, theme: Theme) -> None:
        painter.setFont(body_font(10, QFont.Weight.Medium))
        top = PADDING_TOP + TITLE_HEIGHT
        left, _top, column = self._grid_origin()
        for index, name in enumerate(WEEKDAY_ABBREVIATIONS):
            painter.setPen(theme.color("text", 0.38 if index >= WEEKEND_START else 0.6))
            cell = QRectF(left + index * column, top, column, WEEKDAY_HEIGHT)
            painter.drawText(cell, int(Qt.AlignmentFlag.AlignCenter), name)

    def _paint_week(
        self, painter: QPainter, theme: Theme, row: int, week: tuple[date, ...]
    ) -> None:
        first, last = self._period
        shown = [index for index, day in enumerate(week) if first <= day <= last]
        if shown:
            band = self._cell(row, shown[0]).united(self._cell(row, shown[-1]))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.color("accent-100"))
            painter.drawRoundedRect(band.adjusted(1, 2, -1, -2), 6, 6)
        number = QRectF(PADDING_X, self._cell(row, 0).top(), WEEK_COLUMN - 4, ROW_HEIGHT)
        if self._hovered_week and self._hovered == week[0]:
            painter.fillRect(number.adjusted(0, 3, 0, -3), theme.color("text", 0.07))
        painter.setFont(body_font(10))
        painter.setPen(theme.color("text", 0.42))
        week_number = str(week[0].isocalendar().week)
        painter.drawText(number, int(Qt.AlignmentFlag.AlignCenter), week_number)
        for index, day in enumerate(week):
            self._paint_day(painter, theme, self._cell(row, index), day, in_period=index in shown)

    def _paint_day(
        self, painter: QPainter, theme: Theme, cell: QRectF, day: date, *, in_period: bool
    ) -> None:
        disc = QRectF(0, 0, DAY_DISC, DAY_DISC)
        disc.moveCenter(cell.center())
        today = day == self._today
        painter.setPen(Qt.PenStyle.NoPen)
        if today:
            painter.setBrush(theme.color("accent"))
            painter.drawEllipse(disc)
        elif day == self._hovered and not self._hovered_week:
            painter.setBrush(theme.color("text", 0.09))
            painter.drawEllipse(disc)
        if day == self._cursor and self.hasFocus():
            pen = QPen(theme.color("accent"))
            pen.setWidthF(1.5)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(disc.adjusted(-1.5, -1.5, 1.5, 1.5))
        other_month = month_start(day) != self._month
        weekend = day.weekday() >= WEEKEND_START
        if today:
            color = theme.color("bg")
        elif in_period and not other_month:
            color = theme.color("accent-800")
        else:
            color = theme.color("text", 0.3 if other_month else (0.5 if weekend else 0.92))
        weight = QFont.Weight.DemiBold if today or in_period else QFont.Weight.Normal
        painter.setFont(body_font(12, weight))
        painter.setPen(color)
        painter.drawText(cell, int(Qt.AlignmentFlag.AlignCenter), str(day.day))
