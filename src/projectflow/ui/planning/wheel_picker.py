"""Rolls to pick a day and a time, as the calendar of a phone does (« Détail » panel).

A ``WheelPicker`` shows side by side columns of values, five rows each: the middle one,
in bold on a light band, is the value picked; the others fade out above and below. The
wheel turns the column under the pointer, a vertical drag rolls it, a click on a value
brings it to the middle, and the arrow keys roll the column with the focus (left and
right change the column). Enter or Escape asks to close (``finished``); each change
made by the user sends ``changed``. Painted by hand, colours from the theme.

``DateTimeWheel`` rolls a day, an hour (00-23) and minutes (00 or 30, the half hour of
the grid); ``DateWheel`` rolls a day alone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QFocusEvent,
    QFont,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from projectflow.core.planning.calendar import SNAP_MINUTES
from projectflow.core.planning.night import WEEKDAY_SHORT
from projectflow.core.planning.walltime import (
    MINUTES_PER_HOUR,
    WallTime,
    day_start,
    format_hhmm,
    minute_of_day,
    wall_date,
)
from projectflow.ui.theme import body_font, current_theme

ROW_HEIGHT = 34
VISIBLE_ROWS = 5
CENTER_ROW = VISIBLE_ROWS // 2
SIDE_MARGIN = 10
BAND_INSET = 4
WHEEL_NOTCH = 120
"""Angle of one notch of a mouse wheel: one value."""
DRAG_THRESHOLD = 4
PAGE_STEP = 5
DAY_SPAN = 400
"""Days offered before and after the day shown."""
HOURS_PER_DAY = 24
MONTH_SHORT = (
    "janv",
    "févr",
    "mars",
    "avr",
    "mai",
    "juin",
    "juil",
    "août",
    "sept",
    "oct",
    "nov",
    "déc",
)
FADE = (1.0, 0.58, 0.30)
"""Opacity of the text of the middle row, then one and two rows away."""
FONT_SIZES = (17, 15, 14)

WheelItem = tuple[object, str]


def day_text(day: date, today: date | None = None) -> str:
    """« lun 16 nov », with the year when it is not the year of ``today``."""
    text = f"{WEEKDAY_SHORT[day.weekday()]} {day.day} {MONTH_SHORT[day.month - 1]}"
    if today is not None and day.year != today.year:
        text += f" {day.year}"
    return text


def moment_text(value: WallTime, today: date | None = None) -> str:
    """« lun 16 nov · 07:00 »."""
    return f"{day_text(wall_date(value), today)} · {format_hhmm(minute_of_day(value))}"


def day_items(center: date, today: date | None = None, span: int = DAY_SPAN) -> list[WheelItem]:
    """Every day from ``span`` days before ``center`` to ``span`` days after it."""
    first = center - timedelta(days=span)
    return [
        (first + timedelta(days=offset), day_text(first + timedelta(days=offset), today))
        for offset in range(2 * span + 1)
    ]


HOUR_ITEMS: tuple[WheelItem, ...] = tuple((hour, f"{hour:02d}") for hour in range(HOURS_PER_DAY))
MINUTE_ITEMS: tuple[WheelItem, ...] = tuple(
    (minute, f"{minute:02d}") for minute in range(0, MINUTES_PER_HOUR, SNAP_MINUTES)
)


@dataclass(slots=True)
class _Column:
    items: list[WheelItem]
    stretch: int = 1
    index: int = 0
    values: dict[object, int] = field(default_factory=dict)


class WheelPicker(QWidget):
    """Columns of values rolled with the wheel, a drag, a click or the arrow keys."""

    changed = Signal()
    """A value was rolled by the user (never sent by the ``set_…`` methods)."""
    finished = Signal(bool)
    """Enter (True) or Escape (False): the user is done."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._columns: list[_Column] = []
        self._current = 0
        self._wheel_rest = 0
        self._press: tuple[int, float, int] | None = None
        self._dragging = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(ROW_HEIGHT * VISIBLE_ROWS)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.SizeVerCursor)

    # -- columns -----------------------------------------------------------------

    def set_columns(self, columns: Sequence[tuple[Sequence[WheelItem], int]]) -> None:
        """Replace the columns: their items and their relative width."""
        self._columns = [
            _Column(
                items=list(items),
                stretch=max(1, stretch),
                values={value: index for index, (value, _text) in enumerate(items)},
            )
            for items, stretch in columns
        ]
        self._current = min(self._current, max(0, len(self._columns) - 1))
        self.update()

    def set_items(self, column: int, items: Sequence[WheelItem]) -> None:
        """Replace the items of one column; its index goes back to the first one."""
        col = self._columns[column]
        col.items = list(items)
        col.values = {value: index for index, (value, _text) in enumerate(items)}
        col.index = 0
        self.update()

    def column_count(self) -> int:
        return len(self._columns)

    def items(self, column: int) -> list[WheelItem]:
        return list(self._columns[column].items)

    def index(self, column: int) -> int:
        return self._columns[column].index

    def value(self, column: int) -> object:
        col = self._columns[column]
        return col.items[col.index][0] if col.items else None

    def text(self, column: int) -> str:
        col = self._columns[column]
        return col.items[col.index][1] if col.items else ""

    def visible_texts(self, column: int) -> list[str]:
        """Return the five rows of a column as shown, empty past either end."""
        col = self._columns[column]
        texts: list[str] = []
        for offset in range(-CENTER_ROW, CENTER_ROW + 1):
            index = col.index + offset
            texts.append(col.items[index][1] if 0 <= index < len(col.items) else "")
        return texts

    def set_index(self, column: int, index: int) -> None:
        """Show item ``index`` of a column in the middle (kept within the column)."""
        self._move(column, index, notify=False)

    def set_value(self, column: int, value: object) -> bool:
        """Show ``value`` in the middle of a column; False when the column has no such value."""
        index = self._columns[column].values.get(value)
        if index is None:
            return False
        self._move(column, index, notify=False)
        return True

    def step(self, column: int, steps: int) -> None:
        """Roll a column by ``steps`` values (positive: later), as the user does."""
        self._move(column, self._columns[column].index + steps, notify=True)

    def current_column(self) -> int:
        """Column the arrow keys roll."""
        return self._current

    def set_current_column(self, column: int) -> None:
        if 0 <= column < len(self._columns) and column != self._current:
            self._current = column
            self.update()

    def _move(self, column: int, index: int, *, notify: bool) -> None:
        col = self._columns[column]
        if not col.items:
            return
        index = min(max(index, 0), len(col.items) - 1)
        if index == col.index:
            return
        col.index = index
        self.update()
        if notify:
            self.changed.emit()

    # -- geometry ----------------------------------------------------------------

    def column_rect(self, column: int) -> QRectF:
        total = sum(col.stretch for col in self._columns) or 1
        width = max(0.0, self.width() - 2 * SIDE_MARGIN)
        before = sum(col.stretch for col in self._columns[:column])
        left = SIDE_MARGIN + width * before / total
        right = SIDE_MARGIN + width * (before + self._columns[column].stretch) / total
        return QRectF(left, 0, right - left, self.height())

    def column_at(self, x: float) -> int:
        for column in range(len(self._columns)):
            rect = self.column_rect(column)
            if x < rect.right() or column == len(self._columns) - 1:
                return column
        return 0

    @staticmethod
    def row_at(y: float) -> int:
        """Rows away from the middle one (negative: above)."""
        return min(max(int(y // ROW_HEIGHT), 0), VISIBLE_ROWS - 1) - CENTER_ROW

    # -- events ------------------------------------------------------------------

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if not self._columns:
            event.ignore()
            return
        event.accept()
        column = self.column_at(event.position().x())
        self.set_current_column(column)
        self._wheel_rest += event.angleDelta().y()
        while abs(self._wheel_rest) >= WHEEL_NOTCH:
            # Rolling the wheel towards oneself brings the later values up.
            direction = 1 if self._wheel_rest < 0 else -1
            self._wheel_rest += direction * WHEEL_NOTCH
            self.step(column, direction)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or not self._columns:
            super().mousePressEvent(event)
            return
        event.accept()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        column = self.column_at(event.position().x())
        self.set_current_column(column)
        self._press = (column, event.position().y(), self._columns[column].index)
        self._dragging = False

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._press is None:
            super().mouseMoveEvent(event)
            return
        event.accept()
        column, start_y, start_index = self._press
        moved = start_y - event.position().y()
        if not self._dragging and abs(moved) < DRAG_THRESHOLD:
            return
        self._dragging = True
        self._move(column, start_index + round(moved / ROW_HEIGHT), notify=True)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._press is None or event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        event.accept()
        column = self._press[0]
        if not self._dragging:
            # A click on a value brings it to the middle.
            offset = self.row_at(event.position().y())
            if offset:
                self.step(column, offset)
        self._press = None
        self._dragging = False

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        steps = {
            int(Qt.Key.Key_Up): -1,
            int(Qt.Key.Key_Down): 1,
            int(Qt.Key.Key_PageUp): -PAGE_STEP,
            int(Qt.Key.Key_PageDown): PAGE_STEP,
        }.get(int(key))
        if steps is not None and self._columns:
            self.step(self._current, steps)
        elif key == Qt.Key.Key_Left:
            self.set_current_column(max(0, self._current - 1))
        elif key == Qt.Key.Key_Right:
            self.set_current_column(min(len(self._columns) - 1, self._current + 1))
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finished.emit(True)  # noqa: FBT003
        elif key == Qt.Key.Key_Escape:
            self.finished.emit(False)  # noqa: FBT003
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self.update()

    # -- painting ----------------------------------------------------------------

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        theme = current_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        band = QRectF(
            BAND_INSET, CENTER_ROW * ROW_HEIGHT, self.width() - 2 * BAND_INSET, ROW_HEIGHT
        )
        # A light neutral band under the values picked: one step off the ground.
        painter.fillRect(band, theme.color("neutral-100" if theme.is_dark else "neutral-200"))
        text_color = theme.color("text")
        for number, col in enumerate(self._columns):
            rect = self.column_rect(number)
            for offset in range(-CENTER_ROW, CENTER_ROW + 1):
                index = col.index + offset
                if not 0 <= index < len(col.items):
                    continue
                distance = abs(offset)
                weight = QFont.Weight.DemiBold if distance == 0 else QFont.Weight.Normal
                painter.setFont(body_font(FONT_SIZES[distance], weight))
                color = text_color if distance == 0 else theme.color("text", FADE[distance])
                painter.setPen(color)
                row = QRectF(
                    rect.left(), (CENTER_ROW + offset) * ROW_HEIGHT, rect.width(), ROW_HEIGHT
                )
                painter.drawText(row, int(Qt.AlignmentFlag.AlignCenter), col.items[index][1])
            if self.hasFocus() and number == self._current:
                pen = QPen(theme.color("accent"))
                pen.setWidthF(2.0)
                painter.setPen(pen)
                bottom = band.bottom() - 1
                painter.drawLine(
                    QPointF(rect.left() + 6, bottom), QPointF(rect.right() - 6, bottom)
                )
        painter.end()


class DateTimeWheel(WheelPicker):
    """A day, an hour (00-23) and minutes (00 or 30)."""

    DAY, HOUR, MINUTE = 0, 1, 2

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._center: date | None = None
        self.set_columns([((), 2), (HOUR_ITEMS, 1), (MINUTE_ITEMS, 1)])

    def set_moment(self, value: WallTime, *, today: date | None = None) -> None:
        """Show ``value``; a time off the half hour shows its half hour."""
        day = wall_date(value)
        if self._center is None or abs((day - self._center).days) > DAY_SPAN // 2:
            self._center = day
            self.set_items(self.DAY, day_items(day, today))
        self.set_value(self.DAY, day)
        minute = minute_of_day(value)
        self.set_value(self.HOUR, minute // MINUTES_PER_HOUR)
        self.set_value(self.MINUTE, minute % MINUTES_PER_HOUR // SNAP_MINUTES * SNAP_MINUTES)

    def moment(self) -> WallTime:
        day = self.value(self.DAY)
        hour = self.value(self.HOUR)
        minute = self.value(self.MINUTE)
        if not isinstance(day, date) or not isinstance(hour, int) or not isinstance(minute, int):
            return 0
        return day_start(day) + hour * MINUTES_PER_HOUR + minute


class DateWheel(WheelPicker):
    """A day alone (the deadline)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._center: date | None = None
        self.set_columns([((), 1)])

    def set_day(self, day: date, *, today: date | None = None) -> None:
        if self._center is None or abs((day - self._center).days) > DAY_SPAN // 2:
            self._center = day
            self.set_items(0, day_items(day, today))
        self.set_value(0, day)

    def day(self) -> date | None:
        value = self.value(0)
        return value if isinstance(value, date) else None
