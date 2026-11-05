"""Planning frame around the body: painted time header, row column, window, PDF export.

The header and the row column are plain QWidgets painted by hand from the scene's
``TimeScale`` and ``RowLayout``; they follow the body's scroll bars. The same paint
functions draw the A3 PDF.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable
from pathlib import Path

from grid import (
    STRIPE,
    WEEKDAYS,
    BarItem,
    PlanningScene,
    PlanningView,
    Row,
    RowLayout,
    ViewKind,
    blend,
    kind_color,
    make_pen,
)
from model import KIND_LABELS
from PySide6.QtCore import (
    QLineF,
    QMarginsF,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import (
    QBrush,
    QFont,
    QFontMetricsF,
    QKeySequence,
    QPageLayout,
    QPageSize,
    QPainter,
    QPaintEvent,
    QPdfWriter,
    QShortcut,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from timescale import (
    AFTERNOON,
    DAY_SPAN,
    DAY_START,
    MORNING,
    SNAP_MINUTES,
    Snap,
    TimeScale,
)

from projectflow.ui.theme import Theme, body_font, current_theme, heading_font
from projectflow.ui.widgets.industry import button, label, rule

VIEW_LABELS = {ViewKind.DAY: "Jour", ViewKind.WEEK: "Semaine", ViewKind.MONTH: "Mois"}
DAY_VIEW_HOUR_WIDTH = 80.0
WEEK_DAYS = 5
ZOOM_LIMITS = (0.5, 4.0)
HEADER_HEIGHT = 52
HEADER_BAND = 22
COLUMN_WIDTH = 220
PDF_TITLE_HEIGHT = 40.0
PRINT_MIN_PT = 7.0
SMALLEST_TEXT_PX = 11
"""Smallest font of the grid (group labels, badges): it must print at PRINT_MIN_PT or more."""

WEEKDAYS_LONG = ("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi")
WEEKDAY_LETTERS = ("lu", "ma", "me", "je", "ve")
MONTHS = (
    "janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
    "octobre", "novembre", "décembre",
)  # fmt: skip

# ------------------------------------------------- header and row column ----


def _sticky_label(painter: QPainter, text: str, left: float, right: float) -> None:
    """Draw a period label that stays readable while its period scrolls out on the left."""
    text_width = painter.fontMetrics().horizontalAdvance(text)
    x = min(max(left, 0) + 8, right - text_width - 8)
    painter.drawText(
        QRectF(x, 0, text_width + 2, HEADER_BAND), int(Qt.AlignmentFlag.AlignVCenter), text
    )


def _period_runs(scene: PlanningScene, days: range) -> list[range]:
    runs: list[range] = []
    index = days.start
    while index < days.stop:
        if scene.kind is ViewKind.DAY:
            run = range(index, index + 1)
        elif scene.kind is ViewKind.WEEK:
            run = scene.calendar.week_range(index)
        else:
            run = scene.calendar.month_range(index)
        runs.append(run)
        index = run.stop
    return runs


def period_label(scene: PlanningScene, run: range) -> str:
    calendar = scene.calendar
    first, last = calendar.days[run.start], calendar.days[run.stop - 1]
    if scene.kind is ViewKind.DAY:
        text = (
            f"{WEEKDAYS_LONG[first.weekday()]} {first.day} {MONTHS[first.month - 1]} {first.year}"
        )
        holiday = calendar.holidays.get(first)
        if holiday is not None:
            text += f" · {holiday.name} ({', '.join(holiday.cantons)})"
        return text
    if scene.kind is ViewKind.WEEK:
        return f"Semaine {first.isocalendar().week} · {first:%d.%m} – {last:%d.%m.%Y}"  # noqa: RUF001
    return f"{MONTHS[first.month - 1].capitalize()} {first.year}"


def paint_time_header(painter: QPainter, scene: PlanningScene, width: float, x0: float) -> None:
    """Paint the two-band time header for scene x in [x0, x0 + width]."""
    theme = current_theme()
    painter.fillRect(QRectF(0, 0, width, HEADER_HEIGHT), theme.color("bg"))
    days = scene.scale.visible_days(x0, x0 + width)
    _paint_header_cells(painter, scene, theme, days, x0)
    if scene.kind is ViewKind.DAY:
        _paint_header_hours(painter, scene, theme, days, x0)
    divider = make_pen(theme.color("text", 0.30))
    painter.setFont(heading_font(14))
    for run in _period_runs(scene, days):
        left = scene.scale.day_x(run.start) - x0
        right = scene.scale.day_x(run.stop) - x0
        painter.setPen(theme.color("text"))
        _sticky_label(painter, period_label(scene, run), left, right)
        painter.setPen(divider)
        painter.drawLine(QLineF(round(left), 0, round(left), HEADER_BAND))
    painter.setPen(divider)
    painter.drawLine(QLineF(0, HEADER_BAND - 1, width, HEADER_BAND - 1))
    painter.drawLine(QLineF(0, HEADER_HEIGHT - 1, width, HEADER_HEIGHT - 1))
    now = scene.now_x()
    if now is not None and not scene.printing:
        painter.fillRect(
            QRectF(round(now - x0) - 1, HEADER_BAND, 2, HEADER_HEIGHT - HEADER_BAND),
            theme.color("accent"),
        )


def _paint_header_cells(
    painter: QPainter, scene: PlanningScene, theme: Theme, days: range, x0: float
) -> None:
    calendar = scene.calendar
    scale = scene.scale
    band = HEADER_HEIGHT - HEADER_BAND
    hatch = QBrush(theme.color("text", 0.11), Qt.BrushStyle.BDiagPattern)
    day_font = heading_font(14 if scene.kind is ViewKind.WEEK else 12)
    note_font = body_font(11)
    line_pen = make_pen(theme.color("text", 0.16))
    for index in days:
        day = calendar.days[index]
        left = scale.day_x(index) - x0
        cell = QRectF(left, HEADER_BAND, scale.day_width, band)
        holiday = calendar.holidays.get(day)
        today = index == scene.today and not scene.printing
        if holiday is not None:
            painter.fillRect(cell, theme.color("text", 0.035))
            painter.fillRect(cell, hatch)
        elif today:
            painter.fillRect(cell, theme.color("accent-100"))
        if day.weekday() == 0:
            painter.fillRect(
                QRectF(round(left) - 1, HEADER_BAND, 2, band), theme.color("text", 0.42)
            )
        else:
            painter.setPen(line_pen)
            painter.drawLine(QLineF(round(left), HEADER_BAND, round(left), HEADER_HEIGHT))
        if scene.kind is ViewKind.DAY:
            continue
        painter.setFont(day_font)
        painter.setPen(theme.color("accent-800" if today else "text"))
        if scene.kind is ViewKind.WEEK:
            text = f"{WEEKDAYS[day.weekday()]} {day:%d.%m}"
            painter.drawText(cell.adjusted(8, 0, -6, 0), int(Qt.AlignmentFlag.AlignVCenter), text)
            if holiday is not None:
                offset = painter.fontMetrics().horizontalAdvance(text) + 16
                painter.setFont(note_font)
                painter.setPen(theme.color("neutral-800"))
                note = f"{holiday.name} ({', '.join(holiday.cantons)})"
                room = int(cell.width() - offset - 6)
                elided = painter.fontMetrics().elidedText(note, Qt.TextElideMode.ElideRight, room)
                painter.drawText(
                    cell.adjusted(offset, 0, -6, 0), int(Qt.AlignmentFlag.AlignVCenter), elided
                )
            _paint_hour_ticks(painter, scene, theme, left)
        else:
            text = f"{WEEKDAY_LETTERS[day.weekday()]} {day.day}"
            painter.drawText(cell, int(Qt.AlignmentFlag.AlignCenter), text)


def _paint_hour_ticks(painter: QPainter, scene: PlanningScene, theme: Theme, left: float) -> None:
    painter.setPen(make_pen(theme.color("text", 0.25)))
    ticks = [
        QLineF(x, HEADER_HEIGHT - 5, x, HEADER_HEIGHT - 1)
        for offset in range(60 - DAY_START.minute, DAY_SPAN, 60)
        for x in (round(left + scene.scale.x_of(offset)),)
    ]
    painter.drawLines(ticks)


def _paint_header_hours(
    painter: QPainter, scene: PlanningScene, theme: Theme, days: range, x0: float
) -> None:
    scale = scene.scale
    painter.setFont(heading_font(12, QFont.Weight.Medium))
    hour_pen = make_pen(theme.color("text", 0.16))
    lunch_brush = QBrush(theme.color("text", 0.11), Qt.BrushStyle.BDiagPattern)
    for index in days:
        if not scene.calendar.is_working(index):
            continue
        base = scale.day_x(index) - x0
        lunch_left = base + scale.x_of(MORNING[1])
        lunch = QRectF(lunch_left, HEADER_BAND, scale.x_of(AFTERNOON[0] - MORNING[1]), 30)
        painter.fillRect(lunch, lunch_brush)
        for offset in range(0, DAY_SPAN, SNAP_MINUTES):
            x = round(base + scale.x_of(offset))
            minute = DAY_START.hour * 60 + DAY_START.minute + offset
            if minute % 60:
                painter.setPen(hour_pen)
                painter.drawLine(QLineF(x, HEADER_HEIGHT - 5, x, HEADER_HEIGHT - 1))
                continue
            if offset:
                painter.setPen(hour_pen)
                painter.drawLine(QLineF(x, HEADER_BAND, x, HEADER_HEIGHT))
            painter.setPen(theme.color("neutral-800"))
            painter.drawText(
                QRectF(x + 5, HEADER_BAND, 60, HEADER_HEIGHT - HEADER_BAND - 2),
                int(Qt.AlignmentFlag.AlignVCenter),
                f"{minute // 60:02d}:00",
            )


def paint_resource_column(
    painter: QPainter, scene: PlanningScene, height: float, y0: float
) -> None:
    """Paint the frozen row column for scene y in [y0, y0 + height]."""
    theme = current_theme()
    width = COLUMN_WIDTH
    painter.fillRect(QRectF(0, 0, width, height), theme.color("bg"))
    layout = scene.layout
    selected = scene.selected
    selected_row = layout.row_of(selected.resource_id) if selected is not None else None
    for index in layout.visible(y0, y0 + height):
        row = layout.rows[index]
        band = QRectF(0, row.top - y0, width, row.height)
        if row.resource is None:
            _paint_group_cell(painter, scene, theme, row, band)
        else:
            highlight = not scene.printing and index in {scene.drop_row, selected_row}
            _paint_resource_cell(painter, scene, theme, row, band, highlight=highlight)
    painter.setPen(make_pen(theme.color("text", 0.30)))
    painter.drawLine(QLineF(width - 1, 0, width - 1, height))


def _paint_group_cell(
    painter: QPainter, scene: PlanningScene, theme: Theme, row: Row, band: QRectF
) -> None:
    painter.fillRect(band, theme.color("surface"))
    font = body_font(11, QFont.Weight.Medium)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.1)
    painter.setFont(font)
    painter.setPen(theme.color("accent-800"))
    painter.drawText(
        band.adjusted(14, 0, -40, 0), int(Qt.AlignmentFlag.AlignVCenter), row.group.upper()
    )
    count = sum(1 for item in scene.plan.resources if item.group == row.group)
    painter.setFont(heading_font(12, QFont.Weight.Medium))
    painter.setPen(theme.color("neutral-700"))
    painter.drawText(
        band.adjusted(0, 0, -12, 0),
        int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
        str(count),
    )
    painter.setPen(make_pen(theme.color("text", 0.22)))
    painter.drawLine(QLineF(band.left(), band.bottom() - 1, band.right(), band.bottom() - 1))


def _paint_resource_cell(
    painter: QPainter,
    scene: PlanningScene,
    theme: Theme,
    row: Row,
    band: QRectF,
    *,
    highlight: bool,
) -> None:
    resource = row.resource
    if resource is None:
        return
    metrics = scene.metrics
    if highlight:
        painter.fillRect(band, theme.color("accent", 0.12))
        painter.fillRect(QRectF(band.left(), band.top(), 3, band.height()), theme.color("accent"))
    first_lane = QRectF(band.left(), band.top() + metrics.padding, band.width(), metrics.lane)
    font = body_font(12 if metrics.compact else 13)
    painter.setFont(font)
    right = band.right() - 12
    if resource.subcontractor:
        tag_font = body_font(10 if metrics.compact else 11)
        painter.setFont(tag_font)
        painter.setPen(theme.color("neutral-700"))
        painter.drawText(
            first_lane.adjusted(0, 0, -12, 0),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
            "externe",
        )
        right -= painter.fontMetrics().horizontalAdvance("externe") + 8
        painter.setFont(font)
    painter.setPen(theme.color("text"))
    room = int(right - band.left() - 14)
    name = painter.fontMetrics().elidedText(resource.name, Qt.TextElideMode.ElideRight, room)
    painter.drawText(first_lane.adjusted(14, 0, 0, 0), int(Qt.AlignmentFlag.AlignVCenter), name)
    painter.setPen(make_pen(theme.color("text", 0.08)))
    painter.drawLine(QLineF(band.left(), band.bottom() - 1, band.right(), band.bottom() - 1))


def paint_corner(painter: QPainter, scene: PlanningScene) -> None:
    theme = current_theme()
    painter.fillRect(QRectF(0, 0, COLUMN_WIDTH, HEADER_HEIGHT), theme.color("bg"))
    font = body_font(11, QFont.Weight.Medium)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.1)
    painter.setFont(font)
    painter.setPen(theme.color("neutral-800"))
    snap = "pas 30 min" if scene.snap is Snap.HALF_HOUR else "pas ½ journée"
    painter.drawText(
        QRectF(14, 0, COLUMN_WIDTH - 26, HEADER_BAND),
        int(Qt.AlignmentFlag.AlignVCenter),
        f"{VIEW_LABELS[scene.kind].upper()} · {snap.upper()}",
    )
    painter.drawText(
        QRectF(14, HEADER_BAND, COLUMN_WIDTH - 26, HEADER_HEIGHT - HEADER_BAND),
        int(Qt.AlignmentFlag.AlignVCenter),
        "RESSOURCES",
    )
    painter.setFont(heading_font(12, QFont.Weight.Medium))
    painter.drawText(
        QRectF(14, HEADER_BAND, COLUMN_WIDTH - 26, HEADER_HEIGHT - HEADER_BAND),
        int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
        str(len(scene.plan.resources)),
    )
    painter.setPen(make_pen(theme.color("text", 0.30)))
    painter.drawLine(QLineF(0, HEADER_BAND - 1, COLUMN_WIDTH, HEADER_BAND - 1))
    painter.drawLine(QLineF(0, HEADER_HEIGHT - 1, COLUMN_WIDTH, HEADER_HEIGHT - 1))
    painter.drawLine(QLineF(COLUMN_WIDTH - 1, 0, COLUMN_WIDTH - 1, HEADER_HEIGHT))


class TimeHeader(QWidget):
    def __init__(self, scene: PlanningScene, view: PlanningView) -> None:
        super().__init__()
        self.scene = scene
        self.view = view
        self.setFixedHeight(HEADER_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        view.horizontalScrollBar().valueChanged.connect(self.update)
        scene.grid_changed.connect(self.update)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), current_theme().color("bg"))
        width = self.view.viewport().width()
        painter.setClipRect(QRectF(0, 0, width, HEADER_HEIGHT))
        paint_time_header(painter, self.scene, width, self.view.mapToScene(0, 0).x())
        painter.setClipping(False)
        painter.setPen(make_pen(current_theme().color("text", 0.30)))
        painter.drawLine(QLineF(width, HEADER_HEIGHT - 1, self.width(), HEADER_HEIGHT - 1))
        painter.end()


class ResourceColumn(QWidget):
    def __init__(self, scene: PlanningScene, view: PlanningView) -> None:
        super().__init__()
        self.scene = scene
        self.view = view
        self.setFixedWidth(COLUMN_WIDTH)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        view.verticalScrollBar().valueChanged.connect(self.update)
        scene.grid_changed.connect(self.update)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        # Stop at the viewport: no half row beside the horizontal scroll bar.
        painter = QPainter(self)
        theme = current_theme()
        painter.fillRect(self.rect(), theme.color("bg"))
        height = self.view.viewport().height()
        painter.setClipRect(QRectF(0, 0, COLUMN_WIDTH, height))
        paint_resource_column(painter, self.scene, height, self.view.mapToScene(0, 0).y())
        painter.setClipping(False)
        painter.setPen(make_pen(theme.color("text", 0.30)))
        painter.drawLine(QLineF(COLUMN_WIDTH - 1, height, COLUMN_WIDTH - 1, self.height()))
        painter.end()


class CornerCell(QWidget):
    def __init__(self, scene: PlanningScene) -> None:
        super().__init__()
        self.scene = scene
        self.setFixedSize(COLUMN_WIDTH, HEADER_HEIGHT)
        scene.grid_changed.connect(self.update)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        paint_corner(painter, self.scene)
        painter.end()


class Legend(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._font = body_font(12)
        metrics = QFontMetricsF(self._font)
        self._width = sum(metrics.horizontalAdvance(text) + 32 for text in KIND_LABELS.values())
        self.setFixedHeight(22)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(math.ceil(self._width), 22)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        theme = current_theme()
        bg = theme.color("bg")
        painter.setFont(self._font)
        x = 0.0
        for kind, text in KIND_LABELS.items():
            color = kind_color(theme, kind)
            swatch = QRectF(x, 5, 16, 12)
            painter.fillRect(swatch, blend(bg, color, 0.22 if theme.is_dark else 0.15))
            painter.fillRect(QRectF(x, 5, STRIPE, 12), color)
            painter.setPen(theme.color("neutral-800"))
            painter.drawText(QRectF(x + 22, 0, 200, 22), int(Qt.AlignmentFlag.AlignVCenter), text)
            x += painter.fontMetrics().horizontalAdvance(text) + 32
        painter.end()


# --------------------------------------------------------------- window ----


class PlanningWindow(QMainWindow):
    def __init__(self, scene: PlanningScene) -> None:
        super().__init__()
        self.setWindowTitle("Planning · prototype Lot 0")
        self.scene = scene
        self.calendar = scene.calendar
        self.view = PlanningView(scene)
        self.header = TimeHeader(scene, self.view)
        self.column = ResourceColumn(scene, self.view)
        self.corner_cell = CornerCell(scene)
        self.kind = ViewKind.WEEK
        self._anchor = self.calendar.index_at_or_after(scene.now.date())
        self._zoom = dict.fromkeys(ViewKind, 1.0)
        self._fitted_width = 0
        self._applying = False
        self._buttons: dict[ViewKind, QPushButton] = {}
        self.period = label("", "h5")
        self.stats = label("", "small")
        central = QWidget()
        central.setProperty("pfRoot", "true")
        central.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._toolbar())
        layout.addWidget(rule())
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        grid.addWidget(self.corner_cell, 0, 0)
        grid.addWidget(self.header, 0, 1)
        grid.addWidget(self.column, 1, 0)
        grid.addWidget(self.view, 1, 1)
        layout.addLayout(grid, 1)
        self.setCentralWidget(central)
        # Owned by the window and parented last, so it dies after the view and its slots.
        scene.setParent(self)
        self._shortcuts()
        self.view.horizontalScrollBar().valueChanged.connect(self._on_scrolled)
        self.view.zoom_requested.connect(self.zoom)
        self.view.resized.connect(self._on_resized)
        self.view.gesture_ended.connect(self._sync_lanes)
        self._refresh_labels()

    def _toolbar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(8)
        row.addWidget(label("Planning", "h4"))
        row.addSpacing(16)
        group = QButtonGroup(bar)
        group.setExclusive(True)
        for kind, text in VIEW_LABELS.items():
            toggle = button(text, "seg")
            toggle.setCheckable(True)
            toggle.clicked.connect(lambda _checked=False, k=kind: self.set_view(k))
            group.addButton(toggle)
            self._buttons[kind] = toggle
            row.addWidget(toggle)
        row.addSpacing(12)
        for text, slot in (
            ("‹", lambda: self.step(-1)),  # noqa: RUF001
            ("Aujourd'hui", self.go_today),
            ("›", lambda: self.step(1)),  # noqa: RUF001
        ):
            nav = button(text, size="sm")
            nav.clicked.connect(slot)
            row.addWidget(nav)
        row.addSpacing(12)
        row.addWidget(self.period)
        row.addStretch(1)
        row.addWidget(Legend())
        row.addSpacing(16)
        row.addWidget(self.stats)
        return bar

    def _shortcuts(self) -> None:
        bindings: dict[str, Callable[[], None]] = {
            "1": lambda: self.set_view(ViewKind.DAY),
            "J": lambda: self.set_view(ViewKind.DAY),
            "2": lambda: self.set_view(ViewKind.WEEK),
            "S": lambda: self.set_view(ViewKind.WEEK),
            "3": lambda: self.set_view(ViewKind.MONTH),
            "M": lambda: self.set_view(ViewKind.MONTH),
            "T": self.go_today,
            "Left": lambda: self.step(-1),
            "Right": lambda: self.step(1),
        }
        for key, slot in bindings.items():
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(slot)

    # Navigation -------------------------------------------------------

    @property
    def anchor(self) -> int:
        """First displayed day of the current period (or left day after a free scroll)."""
        return self._anchor

    def left_day(self) -> int:
        return self.scene.scale.day_at(self.view.mapToScene(0, 0).x() + 1)

    def _period_start(self, kind: ViewKind, index: int) -> int:
        index = min(max(index, 0), len(self.calendar) - 1)
        if kind is ViewKind.WEEK:
            return self.calendar.week_start(index)
        if kind is ViewKind.MONTH:
            return self.calendar.month_range(index).start
        return index

    def _day_width(self, kind: ViewKind, anchor: int) -> float:
        available = max(self.view.viewport().width(), 200)
        self._fitted_width = available
        if kind is ViewKind.DAY:
            base = DAY_VIEW_HOUR_WIDTH * DAY_SPAN / 60
        elif kind is ViewKind.WEEK:
            base = available / WEEK_DAYS
        else:
            base = available / max(len(self.calendar.month_range(anchor)), 1)
        return base * self._zoom[kind]

    def set_view(self, kind: ViewKind, anchor: int | None = None) -> None:
        anchor = self._period_start(kind, self._anchor if anchor is None else anchor)
        self.kind = kind
        self._anchor = anchor
        self._apply(self._day_width(kind, anchor), lambda scale: scale.day_x(anchor))
        self._buttons[kind].setChecked(True)
        self._refresh_labels()

    def _apply(self, day_width: float, scroll_to: Callable[[TimeScale], float]) -> None:
        # The view follows the scene rect: never pin it, lanes change the scene height.
        scale = TimeScale(self.calendar, day_width)
        left = scroll_to(scale)
        self._applying = True
        try:
            self.scene.apply_view(self.kind, day_width, self._lane_window(scale, left))
            self.view.horizontalScrollBar().setValue(round(left))
        finally:
            self._applying = False
        self._sync_lanes()

    def _lane_window(self, scale: TimeScale, left: float) -> tuple[int, int]:
        """Display positions whose overlaps set the row heights: the whole periods in sight."""
        first = scale.day_at(left + 1)
        last = scale.day_at(left + self.view.viewport().width() - 1)
        if self.kind is ViewKind.WEEK:
            first, stop = self.calendar.week_start(first), self.calendar.week_range(last).stop
        elif self.kind is ViewKind.MONTH:
            first = self.calendar.month_range(first).start
            stop = self.calendar.month_range(last).stop
        else:
            stop = last + 1
        return first * DAY_SPAN, stop * DAY_SPAN

    def _sync_lanes(self) -> None:
        """Re-size rows on the periods in sight, keeping the top row where it was."""
        layout = self.scene.layout
        lanes = self._lane_window(self.scene.scale, self.view.mapToScene(0, 0).x())
        if self.view.controller.active or lanes == self.scene.lane_window or not layout.rows:
            return
        y = self.view.mapToScene(0, 0).y()
        row = layout.row_at(y)
        offset = y - layout.rows[row].top
        self.scene.set_lane_window(lanes)
        kept = self.scene.layout.rows[row]
        self.view.verticalScrollBar().setValue(round(kept.top + min(offset, kept.height - 1)))

    def step(self, direction: int) -> None:
        anchor = self._period_start(self.kind, self._anchor)
        if self.kind is ViewKind.DAY:
            target = anchor + direction
        elif self.kind is ViewKind.WEEK:
            target = anchor + direction * WEEK_DAYS
        else:
            month = self.calendar.month_range(anchor)
            target = month.stop if direction > 0 else month.start - 1
        self.set_view(self.kind, target)

    def go_today(self) -> None:
        self.set_view(self.kind, self.calendar.index_at_or_after(self.scene.now.date()))

    def zoom(self, factor: float, viewport_x: float) -> None:
        old = self._zoom[self.kind]
        new = min(max(old * factor, ZOOM_LIMITS[0]), ZOOM_LIMITS[1])
        if math.isclose(new, old):
            return
        position = self.scene.scale.position_at(self.view.mapToScene(round(viewport_x), 0).x())
        self._zoom[self.kind] = new
        width = self._day_width(self.kind, self._anchor)
        self._apply(width, lambda scale: scale.x_of(position) - viewport_x)
        self._anchor = self.left_day()
        self._refresh_labels()

    def _on_scrolled(self, _value: int) -> None:
        if not self._applying:
            self._anchor = self.left_day()
            self._refresh_labels()
            self._sync_lanes()

    def _on_resized(self) -> None:
        width = self.view.viewport().width()
        if self.kind is not ViewKind.DAY and width != self._fitted_width:
            self.set_view(self.kind, self._anchor)
        else:
            self._sync_lanes()

    def _refresh_labels(self) -> None:
        index = min(max(self._anchor, 0), len(self.calendar) - 1)
        if self.kind is ViewKind.WEEK:
            run = self.calendar.week_range(index)
        elif self.kind is ViewKind.MONTH:
            run = self.calendar.month_range(index)
        else:
            run = range(index, index + 1)
        self.period.setText(period_label(self.scene, run))
        plan = self.scene.plan
        self.stats.setText(
            f"{len(plan.resources)} lignes · {plan.bar_count} barres · "
            f"{len(self.calendar.holidays)} fériés GE/VD"
        )

    # Helpers for the CLI ----------------------------------------------

    def visible_bars(self) -> list[BarItem]:
        rect = self.view.visible_scene_rect()
        return [item for item in self.scene.items(rect) if isinstance(item, BarItem)]

    def select_sample_bar(self) -> BarItem | None:
        """Select a shared task bar near the middle of the viewport (for screenshots)."""
        rect = self.view.visible_scene_rect().adjusted(0, 0, -40, -40)
        inside = [bar for bar in self.visible_bars() if rect.contains(bar.rect())]
        wide = [bar for bar in inside if bar.bar_width >= min(120, rect.width() / 12)]
        shared = [bar for bar in wide if len(bar.task.resource_ids) > 1]
        candidates = shared or wide or inside
        if not candidates:
            return None
        center = rect.center()
        bar = min(candidates, key=lambda item: (item.rect().center() - center).manhattanLength())
        self.scene.select(bar)
        return bar


# ----------------------------------------------------------- pdf export ----


def _page_bands(layout: RowLayout, room: float) -> list[tuple[int, int]]:
    """Cut the rows into bands of at most ``room`` px; a group that fits starts a new page."""
    room = max(room, 1.0)
    groups = [row.top for row in layout.rows if row.resource is None] + [layout.height]
    bands: list[tuple[int, int]] = []
    top = 0
    for first, stop in itertools.pairwise(groups):
        if stop - top <= room:
            continue
        if first > top:
            bands.append((top, first))
            top = first
        while stop - top > room:
            cuts = [row.top for row in layout.rows if top < row.top <= top + room]
            cut = cuts[-1] if cuts else math.floor(top + room)
            bands.append((top, cut))
            top = cut
    bands.append((top, layout.height))
    return bands


def _paint_pdf_page(
    painter: QPainter, scene: PlanningScene, factor: float, title: str, source: QRectF
) -> None:
    """One page: title, corner, time header, then the row column and body of ``source``."""
    painter.save()
    painter.scale(factor, factor)
    painter.setFont(heading_font(20))
    painter.setPen(current_theme().color("text"))
    painter.drawText(
        QRectF(0, 0, COLUMN_WIDTH + source.width(), PDF_TITLE_HEIGHT - 8),
        int(Qt.AlignmentFlag.AlignVCenter),
        title,
    )
    painter.translate(0, PDF_TITLE_HEIGHT)
    paint_corner(painter, scene)
    painter.save()
    painter.translate(COLUMN_WIDTH, 0)
    painter.setClipRect(QRectF(0, 0, source.width(), HEADER_HEIGHT))
    paint_time_header(painter, scene, source.width(), source.left())
    painter.restore()
    painter.translate(0, HEADER_HEIGHT)
    painter.save()
    painter.setClipRect(QRectF(0, 0, COLUMN_WIDTH, source.height()))
    paint_resource_column(painter, scene, source.height(), source.top())
    painter.restore()
    target = QRectF(COLUMN_WIDTH, 0, source.width(), source.height())
    scene.render(painter, target, source, Qt.AspectRatioMode.IgnoreAspectRatio)
    painter.restore()


def export_week_pdf(window: PlanningWindow, path: Path, day_index: int | None = None) -> int:
    """A3 landscape PDF of one week; returns the page count.

    The week fills the page width. Rows run on as many pages as needed (header
    repeated, a group kept on one page when it fits) so that no text prints
    smaller than PRINT_MIN_PT.
    """
    scene = window.scene
    week = scene.calendar.week_range(window.left_day() if day_index is None else day_index)
    first, last = scene.calendar.days[week.start], scene.calendar.days[week.stop - 1]
    week_number = first.isocalendar().week
    title = f"Planning · semaine {week_number} · {first:%d.%m} – {last:%d.%m.%Y}"  # noqa: RUF001
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A3))
    writer.setPageOrientation(QPageLayout.Orientation.Landscape)
    writer.setPageMargins(QMarginsF(10, 10, 10, 10), QPageLayout.Unit.Millimeter)
    writer.setResolution(150)
    writer.setTitle(title)
    writer.setCreator("ProjectFlow · prototype planning")
    page = writer.pageLayout().paintRectPixels(writer.resolution())
    previous = (window.kind, window.anchor)
    lanes = (week.start * DAY_SPAN, week.stop * DAY_SPAN)
    scene.apply_view(ViewKind.WEEK, scene.scale.day_width, lanes)
    chrome = PDF_TITLE_HEIGHT + HEADER_HEIGHT
    smallest = PRINT_MIN_PT * writer.resolution() / (72 * SMALLEST_TEXT_PX)
    factor = max(page.height() / (chrome + scene.layout.height), smallest)
    day_width = max((page.width() / factor - COLUMN_WIDTH) / len(week), 160.0)
    factor = min(factor, page.width() / (COLUMN_WIDTH + day_width * len(week)))
    scene.apply_view(ViewKind.WEEK, day_width, lanes)
    left, right = scene.scale.day_x(week.start), scene.scale.day_x(week.stop)
    bands = _page_bands(scene.layout, page.height() / factor - chrome + 0.5)
    painter = QPainter(writer)
    sticky, scene.sticky_left, scene.printing = scene.sticky_left, left, True
    try:
        for number, (top, bottom) in enumerate(bands, 1):
            if number > 1:
                writer.newPage()
            heading = f"{title} · page {number}/{len(bands)}" if len(bands) > 1 else title
            source = QRectF(left, top, right - left, bottom - top)
            _paint_pdf_page(painter, scene, factor, heading, source)
    finally:
        painter.end()
        scene.sticky_left, scene.printing = sticky, False
        window.set_view(*previous)
    return len(bands)
