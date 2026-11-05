"""Planning body: styles, row layout, hand-painted bars, scene, gestures and view.

The scene paints the grid (rows, days, Friday/Monday separator, lunch, holidays,
today, "now") and holds one ``BarItem`` per task and row. ``GestureController`` is
the only place where mouse input changes the plan: the view feeds it scene
coordinates, and so does the benchmark.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum

from model import GROUPS, Kind, Plan, Resource, Task
from PySide6.QtCore import (
    QEvent,
    QLineF,
    QPointF,
    QRect,
    QRectF,
    QSizeF,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFocusEvent,
    QFont,
    QFontMetricsF,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsObject,
    QGraphicsScene,
    QGraphicsView,
    QStyleOptionGraphicsItem,
    QWidget,
)
from timescale import (
    AFTERNOON,
    DAY_SPAN,
    DAY_START,
    MORNING,
    SNAP_MINUTES,
    WORK_PER_DAY,
    Snap,
    TimeScale,
    WorkCalendar,
    assign_lanes,
    nearest_half_day_edge,
    shift_half_days,
    snap_minutes,
)

from projectflow.ui.theme import Theme, body_font, current_theme, heading_font
from projectflow.ui.widgets.industry import paint_corner_marks

# ------------------------------------------------------------ constants ----


class ViewKind(Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


@dataclass(frozen=True, slots=True)
class RowMetrics:
    lane: int
    bar: int
    padding: int
    group: int
    compact: bool


SNAPS = {ViewKind.DAY: Snap.HALF_HOUR, ViewKind.WEEK: Snap.HALF_HOUR, ViewKind.MONTH: Snap.HALF_DAY}
ROW_METRICS = {
    ViewKind.DAY: RowMetrics(lane=26, bar=20, padding=3, group=24, compact=False),
    ViewKind.WEEK: RowMetrics(lane=26, bar=20, padding=3, group=24, compact=False),
    ViewKind.MONTH: RowMetrics(lane=16, bar=13, padding=1, group=20, compact=True),
}
ZOOM_STEP = 1.15
STRIPE = 3
EDGE_ZONE = 6.0
DRAG_THRESHOLD = 4.0
MARK_MARGIN = 6.0

KIND_COLORS: dict[Kind, str] = {
    Kind.ETUDE: "accent",
    Kind.FABRICATION: "#b8862b",
    Kind.MONTAGE: "#4c8a5f",
    Kind.SOUS_TRAITANCE: "#7f6aa3",
    Kind.ABSENCE: "neutral-600",
}
"""Stripe colour per task type: a theme token where one fits, else a local placeholder."""

WEEKDAYS = ("lun.", "mar.", "mer.", "jeu.", "ven.")


def _moment_label(moment: datetime) -> str:
    return f"{WEEKDAYS[moment.weekday()]} {moment:%d.%m %H:%M}"


def _duration_label(minutes: int) -> str:
    if minutes >= WORK_PER_DAY:
        return f"{minutes / WORK_PER_DAY:.1f} j".replace(".", ",")
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest:02d}" if rest else f"{hours} h"


# --------------------------------------------------------------- styles ----


def blend(base: QColor, ink: QColor, amount: float) -> QColor:
    return QColor.fromRgbF(
        base.redF() + (ink.redF() - base.redF()) * amount,
        base.greenF() + (ink.greenF() - base.greenF()) * amount,
        base.blueF() + (ink.blueF() - base.blueF()) * amount,
    )


def make_pen(color: QColor, width: float = 1.0, style: Qt.PenStyle = Qt.PenStyle.SolidLine) -> QPen:
    pen = QPen(color)
    pen.setWidthF(width)
    pen.setStyle(style)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    return pen


def kind_color(theme: Theme, kind: Kind) -> QColor:
    value = KIND_COLORS[kind]
    if not value.startswith("#"):
        return theme.color(value)
    color = QColor(value)
    return color.lighter(125) if theme.is_dark else color


@dataclass(frozen=True, slots=True)
class BarStyle:
    serial: int
    compact: bool
    number_font: QFont
    title_font: QFont
    badge_font: QFont
    number_metrics: QFontMetricsF
    title_metrics: QFontMetricsF
    badge_metrics: QFontMetricsF
    stripe: dict[Kind, QColor]
    fill: dict[Kind, QColor]
    fill_hot: dict[Kind, QColor]
    edge: dict[Kind, QPen]
    edge_hot: dict[Kind, QPen]
    hatch: QBrush
    text: QColor
    muted: QColor
    select: QColor
    linked_pen: QPen


@dataclass(frozen=True, slots=True)
class GridStyle:
    bg: QColor
    group_fill: QColor
    group_pen: QPen
    row_pen: QPen
    day_pen: QPen
    hour_pen: QPen
    half_pen: QPen
    week_fill: QColor
    off_fill: QColor
    hatch: QBrush
    lunch_fill: QColor
    today_fill: QColor
    drop_fill: QColor
    now_fill: QColor


def build_bar_style(theme: Theme, serial: int, *, compact: bool) -> BarStyle:
    bg = theme.color("bg")
    stripe = {kind: kind_color(theme, kind) for kind in Kind}
    amount = 0.22 if theme.is_dark else 0.15
    number_font = heading_font(11 if compact else 13)
    title_font = body_font(12)
    badge_font = heading_font(11, QFont.Weight.Medium)
    return BarStyle(
        serial=serial,
        compact=compact,
        number_font=number_font,
        title_font=title_font,
        badge_font=badge_font,
        number_metrics=QFontMetricsF(number_font),
        title_metrics=QFontMetricsF(title_font),
        badge_metrics=QFontMetricsF(badge_font),
        stripe=stripe,
        fill={kind: blend(bg, color, amount) for kind, color in stripe.items()},
        fill_hot={kind: blend(bg, color, amount + 0.13) for kind, color in stripe.items()},
        edge={kind: make_pen(blend(bg, color, 0.55)) for kind, color in stripe.items()},
        edge_hot={kind: make_pen(color) for kind, color in stripe.items()},
        hatch=QBrush(theme.color("text", 0.18), Qt.BrushStyle.BDiagPattern),
        text=theme.color("text"),
        muted=theme.color("neutral-800"),
        select=theme.color("accent"),
        linked_pen=make_pen(theme.color("accent"), 1.0, Qt.PenStyle.DashLine),
    )


def build_grid_style(theme: Theme) -> GridStyle:
    return GridStyle(
        bg=theme.color("bg"),
        group_fill=theme.color("surface"),
        group_pen=make_pen(theme.color("text", 0.22)),
        row_pen=make_pen(theme.color("text", 0.08)),
        day_pen=make_pen(theme.color("text", 0.16)),
        hour_pen=make_pen(theme.color("text", 0.08)),
        half_pen=make_pen(theme.color("text", 0.04)),
        week_fill=theme.color("text", 0.42),
        off_fill=theme.color("text", 0.035),
        hatch=QBrush(theme.color("text", 0.11), Qt.BrushStyle.BDiagPattern),
        lunch_fill=theme.color("text", 0.03),
        today_fill=theme.color("accent", 0.08),
        drop_fill=theme.color("accent", 0.12),
        now_fill=theme.color("accent"),
    )


# --------------------------------------------------------------- layout ----


@dataclass(frozen=True, slots=True)
class Row:
    top: int
    height: int
    lanes: int
    group: str
    resource: Resource | None = None

    @property
    def bottom(self) -> int:
        return self.top + self.height


@dataclass(frozen=True, slots=True)
class RowLayout:
    rows: tuple[Row, ...] = ()
    tops: tuple[int, ...] = ()
    height: int = 0
    index_of: dict[str, int] | None = None

    def row_at(self, y: float) -> int:
        return min(max(bisect.bisect_right(self.tops, y) - 1, 0), len(self.rows) - 1)

    def visible(self, top: float, bottom: float) -> range:
        if not self.rows:
            return range(0)
        return range(self.row_at(top), self.row_at(bottom) + 1)

    def row_of(self, resource_id: str) -> int:
        return (self.index_of or {})[resource_id]


# ----------------------------------------------------------------- bars ----


class Zone(Enum):
    BODY = "body"
    LEFT = "left"
    RIGHT = "right"


@dataclass(frozen=True, slots=True)
class _BarText:
    key: tuple[float, int, int]
    number: str
    title: str
    badge: str
    left: float
    title_x: float
    badge_x: float
    number_y: float
    title_y: float
    badge_y: float


class BarItem(QGraphicsObject):
    """One task on one row, painted by hand: light fill, type stripe, dossier and title."""

    def __init__(self, board: PlanningScene, task: Task, resource_id: str) -> None:
        super().__init__()
        self.board = board
        self.task = task
        self.resource_id = resource_id
        self.span = board.span_of(task)
        self.lane = 0
        self.bar_width = 0.0
        self.bar_height = 0.0
        self.hovered = False
        self.selected = False
        self.linked = False
        self.lifted = False
        self._shape = QPainterPath()
        self._text: _BarText | None = None

    # State ------------------------------------------------------------

    def set_task(self, task: Task) -> None:
        self.task = task
        self.span = self.board.span_of(task)
        self._text = None
        self.update()

    def set_rect(self, rect: QRectF) -> None:
        if rect.width() != self.bar_width or rect.height() != self.bar_height:
            self.prepareGeometryChange()
            self.bar_width = rect.width()
            self.bar_height = rect.height()
            self._shape = QPainterPath()
            self._shape.addRect(QRectF(0, 0, self.bar_width, self.bar_height))
        if rect.topLeft() != self.pos():
            self.setPos(rect.topLeft())

    def rect(self) -> QRectF:
        return QRectF(self.pos(), QSizeF(self.bar_width, self.bar_height))

    def set_hovered(self, *, value: bool) -> None:
        if value != self.hovered:
            self.hovered = value
            self.update()

    def set_selected(self, *, value: bool) -> None:
        if value != self.selected:
            self.prepareGeometryChange()
            self.selected = value
            self.setZValue(2 if value else 0)
            self.update()

    def set_linked(self, *, value: bool) -> None:
        if value != self.linked:
            self.linked = value
            self.update()

    def set_lifted(self, *, value: bool) -> None:
        if value != self.lifted:
            self.lifted = value
            self.update()

    def zone_at(self, x: float) -> Zone:
        edge = min(EDGE_ZONE, self.bar_width / 3)
        if x <= edge:
            return Zone.LEFT
        if x >= self.bar_width - edge:
            return Zone.RIGHT
        return Zone.BODY

    # QGraphicsItem ----------------------------------------------------

    def boundingRect(self) -> QRectF:  # noqa: N802
        margin = MARK_MARGIN if self.selected else 0.0
        return QRectF(-margin, -margin, self.bar_width + 2 * margin, self.bar_height + 2 * margin)

    def shape(self) -> QPainterPath:
        return self._shape

    def paint(
        self,
        painter: QPainter,
        _option: QStyleOptionGraphicsItem,
        _widget: QWidget | None = None,
    ) -> None:
        style = self.board.bar_style
        kind = self.task.kind
        width, height = self.bar_width, self.bar_height
        hot = (self.hovered or self.lifted) and not self.board.printing
        painter.fillRect(QRectF(0, 0, width, height), (style.fill_hot if hot else style.fill)[kind])
        if kind is Kind.ABSENCE:
            painter.fillRect(QRectF(0, 0, width, height), style.hatch)
        painter.fillRect(QRectF(0, 0, min(STRIPE, width), height), style.stripe[kind])
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen((style.edge_hot if hot else style.edge)[kind])
        painter.drawRect(QRectF(0, 0, width - 1, height - 1))
        self._paint_text(painter, style)
        if self.board.printing:
            return
        if self.selected:
            painter.setPen(make_pen(style.select))
            painter.drawRect(QRectF(0, 0, width - 1, height - 1))
            painter.drawRect(QRectF(1, 1, width - 3, height - 3))
            paint_corner_marks(painter, QRectF(0, 0, width, height))
        elif self.linked:
            painter.setPen(style.linked_pen)
            painter.drawRect(QRectF(0, 0, width - 1, height - 1))

    def _paint_text(self, painter: QPainter, style: BarStyle) -> None:
        text = self._text_layout(style)
        if text.number:
            painter.setFont(style.number_font)
            painter.setPen(style.text)
            painter.drawText(QPointF(text.left, text.number_y), text.number)
        if text.title:
            painter.setFont(style.title_font)
            painter.setPen(style.muted if self.task.dossier else style.text)
            painter.drawText(QPointF(text.title_x, text.title_y), text.title)
        if text.badge:
            painter.setFont(style.badge_font)
            painter.setPen(style.muted)
            painter.drawText(QPointF(text.badge_x, text.badge_y), text.badge)

    def _text_layout(self, style: BarStyle) -> _BarText:
        shift = round(min(max(self.board.sticky_left - self.x(), 0.0), self.bar_width))
        key = (self.bar_width, style.serial, shift)
        if self._text is not None and self._text.key == key:
            return self._text
        left = float(STRIPE + (4 if style.compact else 6) + shift)
        room = self.bar_width - left - (3 if style.compact else 5)
        owners = len(self.task.resource_ids)
        badge = f"×{owners}" if owners > 1 and not style.compact else ""  # noqa: RUF001
        badge_width = style.badge_metrics.horizontalAdvance(badge) if badge else 0.0
        if badge and room - badge_width - 6 < 40:
            badge, badge_width = "", 0.0
        room -= badge_width + 6 if badge else 0
        number = self._fit(style.number_metrics, self.task.dossier, room, elide=False)
        if not number and self.task.dossier:
            suffix = self.task.dossier.rsplit("-", 1)[-1]
            number = self._fit(style.number_metrics, suffix, room, minimum=8, elide=False)
        title_x = left
        if number:
            title_x = left + style.number_metrics.horizontalAdvance(number) + 6
            room -= title_x - left
        title_metrics = style.title_metrics if self.task.dossier else style.number_metrics
        title = ""
        if not style.compact or not self.task.dossier:
            title = self._fit(title_metrics, self.task.title, room, minimum=24)
        self._text = _BarText(
            key=key,
            number=number,
            title=title,
            badge=badge,
            left=left,
            title_x=title_x,
            badge_x=self.bar_width - (3 if style.compact else 5) - badge_width,
            number_y=self._baseline(style.number_metrics),
            title_y=self._baseline(title_metrics),
            badge_y=self._baseline(style.badge_metrics),
        )
        return self._text

    def _baseline(self, metrics: QFontMetricsF) -> float:
        return round((self.bar_height + metrics.ascent() - metrics.descent()) / 2)

    @staticmethod
    def _fit(
        metrics: QFontMetricsF, text: str, room: float, minimum: float = 14, *, elide: bool = True
    ) -> str:
        if not text or room < minimum:
            return ""
        if metrics.horizontalAdvance(text) <= room:
            return text
        if not elide:
            return ""
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, room)
        return "" if len(elided) < 4 else elided


class DragHint(QGraphicsObject):
    """Small floating label with the snapped dates while a bar is dragged."""

    def __init__(self) -> None:
        super().__init__()
        self.setZValue(50)
        self.setVisible(False)
        self._font = heading_font(13)
        self._metrics = QFontMetricsF(self._font)
        self._text = ""
        self._size = QSizeF(0, 0)

    def show_text(self, text: str, anchor: QRectF, visible: QRectF) -> None:
        if text != self._text:
            self.prepareGeometryChange()
            self._text = text
            self._size = QSizeF(
                math.ceil(self._metrics.horizontalAdvance(text)) + 16,
                math.ceil(self._metrics.height()) + 8,
            )
        y = anchor.top() - self._size.height() - 6
        if y < visible.top() + 2:
            y = anchor.bottom() + 6
        x = min(max(anchor.left(), visible.left() + 4), visible.right() - self._size.width() - 4)
        self.setPos(round(x), round(y))
        self.setVisible(True)

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
        painter.setPen(make_pen(theme.color("accent")))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.setFont(self._font)
        painter.setPen(theme.color("text"))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), self._text)


# ---------------------------------------------------------------- scene ----


class PlanningScene(QGraphicsScene):
    """Bars, row layout and the painted grid of the Planning body."""

    grid_changed = Signal()

    def __init__(self, plan: Plan, calendar: WorkCalendar, now: datetime) -> None:
        super().__init__()
        self.plan = plan
        self.calendar = calendar
        self.now = now
        self.today = calendar.index_of(now.date())
        self.kind = ViewKind.WEEK
        self.metrics = ROW_METRICS[self.kind]
        self.scale = TimeScale(calendar, 240.0)
        self._serial = 0
        self.bar_style = build_bar_style(current_theme(), self._serial, compact=False)
        self.grid_style = build_grid_style(current_theme())
        self.layout = RowLayout()
        self.bars: dict[tuple[str, str], BarItem] = {}
        self.task_bars: dict[str, list[BarItem]] = {}
        self.selected: BarItem | None = None
        self.hovered: BarItem | None = None
        self.drop_row: int | None = None
        self.lane_window: tuple[int, int] | None = None
        self.printing = False
        self.sticky_left = 0.0
        self.hint = DragHint()
        self.addItem(self.hint)
        self._build_items()
        self.relayout()

    @property
    def snap(self) -> Snap:
        return SNAPS[self.kind]

    def span_of(self, task: Task) -> tuple[int, int]:
        return self.calendar.position_of(task.start), self.calendar.position_of(task.end)

    def _build_items(self) -> None:
        for task in self.plan.tasks.values():
            for resource_id in task.resource_ids:
                bar = BarItem(self, task, resource_id)
                self.addItem(bar)
                self.bars[task.id, resource_id] = bar
                self.task_bars.setdefault(task.id, []).append(bar)

    # Geometry ---------------------------------------------------------

    def apply_view(
        self, kind: ViewKind, day_width: float, lanes: tuple[int, int] | None = None
    ) -> None:
        """Switch view and scale; ``lanes`` is the span whose overlaps set the row heights."""
        self.kind = kind
        self.lane_window = lanes
        self.metrics = ROW_METRICS[kind]
        self.scale = TimeScale(self.calendar, day_width)
        self._serial += 1
        theme = current_theme()
        self.bar_style = build_bar_style(theme, self._serial, compact=self.metrics.compact)
        self.grid_style = build_grid_style(theme)
        self.relayout()

    def set_lane_window(self, lanes: tuple[int, int] | None) -> None:
        if lanes != self.lane_window:
            self.lane_window = lanes
            self.relayout()

    def _assign_lanes(self, bars: list[BarItem]) -> int:
        """Give every bar a lane; only bars inside the lane window add lanes to the row.

        Bars outside it share the row's lanes and may overlap, but they are off-screen:
        the window moves the lane window before they come into view.
        """
        window = self.lane_window
        inside, outside = bars, []
        if window is not None:
            low, high = window
            inside = [bar for bar in bars if bar.span[0] < high and bar.span[1] > low]
            outside = [bar for bar in bars if not (bar.span[0] < high and bar.span[1] > low)]
        lanes, count = assign_lanes([bar.span for bar in inside])
        for bar, lane in zip(inside, lanes, strict=True):
            bar.lane = lane
        others, _count = assign_lanes([bar.span for bar in outside])
        for bar, lane in zip(outside, others, strict=True):
            bar.lane = min(lane, count - 1)
        return count

    def relayout(self) -> None:
        """Recompute lanes and row heights, then place every bar."""
        metrics = self.metrics
        per_resource: dict[str, list[BarItem]] = {item.id: [] for item in self.plan.resources}
        for bar in self.bars.values():
            per_resource[bar.resource_id].append(bar)
        rows: list[Row] = []
        top = 0
        for group in GROUPS:
            members = [item for item in self.plan.resources if item.group == group]
            if not members:
                continue
            rows.append(Row(top=top, height=metrics.group, lanes=0, group=group))
            top += metrics.group
            for resource in members:
                count = self._assign_lanes(per_resource[resource.id])
                height = 2 * metrics.padding + count * metrics.lane
                rows.append(Row(top, height, count, group, resource))
                top += height
        self.layout = RowLayout(
            rows=tuple(rows),
            tops=tuple(row.top for row in rows),
            height=top,
            index_of={row.resource.id: i for i, row in enumerate(rows) if row.resource},
        )
        self.setSceneRect(0, 0, self.scale.width, top)
        for bar in self.bars.values():
            self.place(bar)
        self.grid_changed.emit()

    def bar_rect(self, span: tuple[int, int], row_index: int, lane: int) -> QRectF:
        row = self.layout.rows[row_index]
        metrics = self.metrics
        left = round(self.scale.x_of(span[0]))
        right = round(self.scale.x_of(span[1]))
        width = right - left - 1 if right - left > 4 else max(right - left, 2)
        top = row.top + metrics.padding + lane * metrics.lane + (metrics.lane - metrics.bar) // 2
        return QRectF(left, top, width, metrics.bar)

    def place(self, bar: BarItem) -> None:
        bar.set_rect(self.bar_rect(bar.span, self.layout.row_of(bar.resource_id), bar.lane))

    def commit(self, task: Task, moved: BarItem, resource_id: str) -> None:
        """Store an edited task (new times and/or new row for ``moved``) and reflow."""
        self.plan.tasks[task.id] = task
        if resource_id != moved.resource_id:
            del self.bars[task.id, moved.resource_id]
            moved.resource_id = resource_id
            self.bars[task.id, resource_id] = moved
        for bar in self.task_bars[task.id]:
            bar.set_task(task)
        self.relayout()

    # Interaction state ------------------------------------------------

    def bar_at(self, point: QPointF) -> BarItem | None:
        for item in self.items(point):
            if isinstance(item, BarItem):
                return item
        return None

    def set_hovered(self, bar: BarItem | None) -> None:
        if bar is self.hovered:
            return
        if self.hovered is not None:
            self.hovered.set_hovered(value=False)
        self.hovered = bar
        if bar is not None:
            bar.set_hovered(value=True)

    def select(self, bar: BarItem | None) -> None:
        if bar is self.selected:
            return
        for old in self._family(self.selected):
            old.set_selected(value=False)
            old.set_linked(value=False)
        self.selected = bar
        for item in self._family(bar):
            if item is bar:
                item.set_selected(value=True)
            else:
                item.set_linked(value=True)
        self.grid_changed.emit()

    def _family(self, bar: BarItem | None) -> list[BarItem]:
        return [] if bar is None else self.task_bars[bar.task.id]

    def set_sticky_left(self, x: float) -> None:
        """Keep the text of bars cut by the left edge of the view readable."""
        if x == self.sticky_left:
            return
        height = max(self.layout.height, 1)
        crossing = [*self.items(QRectF(self.sticky_left, 0, 1, height))]
        crossing += self.items(QRectF(x, 0, 1, height))
        self.sticky_left = x
        for item in crossing:
            if isinstance(item, BarItem):
                item.update()

    def set_drop_row(self, index: int | None) -> None:
        if index == self.drop_row:
            return
        for row_index in (self.drop_row, index):
            if row_index is not None:
                row = self.layout.rows[row_index]
                self.invalidate(
                    QRectF(0, row.top, self.scale.width, row.height),
                    QGraphicsScene.SceneLayer.BackgroundLayer,
                )
        self.drop_row = index
        self.grid_changed.emit()

    # Painting ---------------------------------------------------------

    def drawBackground(self, painter: QPainter, exposed: QRectF | QRect) -> None:  # noqa: N802
        rect = QRectF(exposed)
        style = self.grid_style
        painter.fillRect(rect, style.bg)
        if not self.layout.rows:
            return
        days = self.scale.visible_days(rect.left(), rect.right())
        self._paint_columns(painter, rect, days)
        self._paint_rows(painter, rect)
        if self.kind is not ViewKind.MONTH:
            self._paint_hours(painter, rect, days)
        self._paint_day_lines(painter, rect, days)

    def _paint_columns(self, painter: QPainter, rect: QRectF, days: range) -> None:
        style = self.grid_style
        for index in days:
            column = QRectF(
                self.scale.day_x(index), rect.top(), self.scale.day_width, rect.height()
            )
            if not self.calendar.is_working(index):
                painter.fillRect(column, style.off_fill)
                painter.fillRect(column, style.hatch)
            elif index == self.today and not self.printing:
                painter.fillRect(column, style.today_fill)

    def _paint_rows(self, painter: QPainter, rect: QRectF) -> None:
        style = self.grid_style
        lines: list[QLineF] = []
        group_lines: list[QLineF] = []
        for index in self.layout.visible(rect.top(), rect.bottom()):
            row = self.layout.rows[index]
            band = QRectF(rect.left(), row.top, rect.width(), row.height)
            bottom = row.bottom - 1
            if row.resource is None:
                painter.fillRect(band, style.group_fill)
                group_lines.append(QLineF(rect.left(), bottom, rect.right(), bottom))
                continue
            if index == self.drop_row:
                painter.fillRect(band, style.drop_fill)
            lines.append(QLineF(rect.left(), bottom, rect.right(), bottom))
        painter.setPen(style.row_pen)
        painter.drawLines(lines)
        painter.setPen(style.group_pen)
        painter.drawLines(group_lines)

    def _paint_hours(self, painter: QPainter, rect: QRectF, days: range) -> None:
        style = self.grid_style
        scale = self.scale
        hours: list[QLineF] = []
        halves: list[QLineF] = []
        top, bottom = rect.top(), rect.bottom()
        for index in days:
            if not self.calendar.is_working(index):
                continue
            base = scale.day_x(index)
            lunch = QRectF(
                base + scale.x_of(MORNING[1]),
                top,
                scale.x_of(AFTERNOON[0] - MORNING[1]),
                rect.height(),
            )
            painter.fillRect(lunch, style.lunch_fill)
            if self.kind is not ViewKind.DAY:
                continue
            painter.fillRect(lunch, style.hatch)
            for offset in range(SNAP_MINUTES, DAY_SPAN, SNAP_MINUTES):
                x = round(base + scale.x_of(offset))
                is_hour = (offset + DAY_START.minute) % 60 == 0
                (hours if is_hour else halves).append(QLineF(x, top, x, bottom))
        painter.setPen(style.half_pen)
        painter.drawLines(halves)
        painter.setPen(style.hour_pen)
        painter.drawLines(hours)

    def _paint_day_lines(self, painter: QPainter, rect: QRectF, days: range) -> None:
        style = self.grid_style
        lines: list[QLineF] = []
        top, bottom = rect.top(), rect.bottom()
        for index in range(days.start, min(days.stop + 1, len(self.calendar) + 1)):
            x = round(self.scale.day_x(index))
            if index < len(self.calendar) and self.calendar.days[index].weekday() == 0:
                painter.fillRect(QRectF(x - 1, top, 2, rect.height()), style.week_fill)
            else:
                lines.append(QLineF(x, top, x, bottom))
        painter.setPen(style.day_pen)
        painter.drawLines(lines)

    def now_x(self) -> float | None:
        first, last = self.calendar.days[0], self.calendar.days[-1]
        if not first <= self.now.date() <= last:
            return None
        return self.scale.x_of(self.calendar.position_of(self.now))

    def drawForeground(self, painter: QPainter, exposed: QRectF | QRect) -> None:  # noqa: N802
        rect = QRectF(exposed)
        x = self.now_x()
        if self.printing or x is None or not rect.left() - 2 <= x <= rect.right() + 2:
            return
        painter.fillRect(
            QRectF(round(x) - 1, rect.top(), 2, rect.height()), self.grid_style.now_fill
        )


# ------------------------------------------------------------- gestures ----


class Gesture(Enum):
    IDLE = "idle"
    PRESS = "press"
    MOVE = "move"
    RESIZE_LEFT = "resize-left"
    RESIZE_RIGHT = "resize-right"


_ZONE_GESTURE = {
    Zone.BODY: Gesture.MOVE,
    Zone.LEFT: Gesture.RESIZE_LEFT,
    Zone.RIGHT: Gesture.RESIZE_RIGHT,
}
_ZONE_CURSOR = {
    Zone.BODY: Qt.CursorShape.OpenHandCursor,
    Zone.LEFT: Qt.CursorShape.SizeHorCursor,
    Zone.RIGHT: Qt.CursorShape.SizeHorCursor,
}


@dataclass(slots=True)
class Drag:
    bar: BarItem
    zone: Zone
    press: QPointF
    span: tuple[int, int]
    work: int
    row: int
    preview: tuple[int, int, int]


class GestureController:
    """Mouse state machine: idle -> press -> move | resize-left | resize-right -> idle.

    Every method takes scene coordinates, so the view's mouse events and the
    benchmark drive exactly the same code.
    """

    def __init__(self, scene: PlanningScene, visible_rect: Callable[[], QRectF]) -> None:
        self.scene = scene
        self.visible_rect = visible_rect
        self.state = Gesture.IDLE
        self.drag: Drag | None = None
        self.cursor = Qt.CursorShape.ArrowCursor
        self._swallow = False
        self._fine = False

    @property
    def active(self) -> bool:
        return self.state in {Gesture.MOVE, Gesture.RESIZE_LEFT, Gesture.RESIZE_RIGHT}

    def hover(self, point: QPointF) -> None:
        bar = self.scene.bar_at(point)
        self.scene.set_hovered(bar)
        self.cursor = (
            Qt.CursorShape.ArrowCursor
            if bar is None
            else _ZONE_CURSOR[bar.zone_at(point.x() - bar.x())]
        )

    def press(self, point: QPointF) -> None:
        self._swallow = False
        bar = self.scene.bar_at(point)
        self.scene.select(bar)
        self.drag = None
        if bar is None or self._cut_by_range(bar.task):
            self.state = Gesture.IDLE
            return
        row = self.scene.layout.row_of(bar.resource_id)
        self.drag = Drag(
            bar=bar,
            zone=bar.zone_at(point.x() - bar.x()),
            press=QPointF(point),
            span=bar.span,
            work=self.scene.calendar.work_between(*bar.span),
            row=row,
            preview=(*bar.span, row),
        )
        self.state = Gesture.PRESS

    def move(self, point: QPointF, *, fine: bool = False) -> None:
        """Follow the pointer; ``fine`` (Shift) snaps to 30 min even in month view."""
        self._fine = fine
        drag = self.drag
        if drag is None or self._swallow:
            return
        if self.state is Gesture.PRESS:
            if (point - drag.press).manhattanLength() < DRAG_THRESHOLD:
                return
            self._begin(drag)
        if self.state is Gesture.MOVE:
            self._show(drag, self._moved(drag, point))
        else:
            self._show(drag, self._resized(drag, point, left=self.state is Gesture.RESIZE_LEFT))

    def release(self, _point: QPointF) -> None:
        drag, state = self.drag, self.state
        self.drag = None
        self.state = Gesture.IDLE
        swallowed, self._swallow = self._swallow, False
        if drag is not None and not swallowed and state is not Gesture.PRESS:
            self._finish(drag, commit=True)

    def cancel(self) -> bool:
        """Escape: put the bar back where it was; True when a drag was running."""
        drag = self.drag
        if drag is None:
            return False
        was_active = self.active
        if was_active:
            self._finish(drag, commit=False)
        self.drag = None
        self.state = Gesture.IDLE
        self._swallow = True
        return was_active

    # Internals --------------------------------------------------------

    def _begin(self, drag: Drag) -> None:
        self.state = _ZONE_GESTURE[drag.zone]
        self.cursor = (
            Qt.CursorShape.ClosedHandCursor
            if self.state is Gesture.MOVE
            else Qt.CursorShape.SizeHorCursor
        )
        drag.bar.setZValue(10)
        self._show(drag, drag.preview, force=True)

    def _cut_by_range(self, task: Task) -> bool:
        """Tell whether the task runs past the loaded days: editing it would drop the rest."""
        calendar = self.scene.calendar
        first = datetime.combine(calendar.days[0], DAY_START)
        return task.start < first or task.end > calendar.moment_at(calendar.end, end=True)

    def _moved(self, drag: Drag, point: QPointF) -> tuple[int, int, int]:
        calendar = self.scene.calendar
        delta = self.scene.scale.position_at(point.x() - drag.press.x())
        if self._snap() is Snap.HALF_HOUR:
            start = snap_minutes(drag.span[0] + delta)
        else:
            start = snap_minutes(shift_half_days(drag.span[0], round(delta / (DAY_SPAN / 2))))
        latest = calendar.latest_start(drag.work)
        latest -= latest % SNAP_MINUTES
        start = calendar.working_start(min(max(start, 0), latest))
        end = calendar.add_work(start, drag.work)
        return start, end, self._target_row(drag, point.y())

    def _resized(self, drag: Drag, point: QPointF, *, left: bool) -> tuple[int, int, int]:
        calendar = self.scene.calendar
        position = self.scene.scale.position_at(point.x())
        half_hour = self._snap() is Snap.HALF_HOUR
        start, end = drag.span
        if left:
            edge = (
                snap_minutes(position) if half_hour else nearest_half_day_edge(position, start=True)
            )
            start = calendar.working_start(min(max(edge, 0), end - SNAP_MINUTES))
            if start >= end:
                start = drag.span[0]
        else:
            edge = (
                snap_minutes(position)
                if half_hour
                else nearest_half_day_edge(position, start=False)
            )
            end = calendar.working_end(min(max(edge, start + SNAP_MINUTES), calendar.end))
            if end <= start:
                end = calendar.add_work(start, SNAP_MINUTES)
        return start, end, drag.row

    def _snap(self) -> Snap:
        return Snap.HALF_HOUR if self._fine else self.scene.snap

    def _target_row(self, drag: Drag, y: float) -> int:
        layout = self.scene.layout
        index = layout.row_at(y)
        resource = layout.rows[index].resource
        if resource is None:
            return drag.preview[2]
        if resource.id != drag.bar.resource_id and resource.id in drag.bar.task.resource_ids:
            return drag.preview[2]
        return index

    def _show(self, drag: Drag, preview: tuple[int, int, int], *, force: bool = False) -> None:
        if preview == drag.preview and not force:
            return
        drag.preview = preview
        start, end, row = preview
        scene = self.scene
        same_row = row == drag.row
        rect = scene.bar_rect((start, end), row, drag.bar.lane if same_row else 0)
        drag.bar.set_rect(rect)
        drag.bar.set_lifted(value=not same_row)
        for sibling in scene.task_bars[drag.bar.task.id]:
            if sibling is not drag.bar:
                row_index = scene.layout.row_of(sibling.resource_id)
                sibling.set_rect(scene.bar_rect((start, end), row_index, sibling.lane))
        scene.set_drop_row(None if same_row else row)
        scene.hint.show_text(self._hint_text(drag), rect, self.visible_rect())

    def _hint_text(self, drag: Drag) -> str:
        start, end, row = drag.preview
        calendar = self.scene.calendar
        text = (
            f"{_moment_label(calendar.moment_at(start))} – "  # noqa: RUF001
            f"{_moment_label(calendar.moment_at(end, end=True))} · "
            f"{_duration_label(calendar.work_between(start, end))}"
        )
        resource = self.scene.layout.rows[row].resource
        if row != drag.row and resource is not None:
            text += f" · {resource.name}"
        return text

    def _finish(self, drag: Drag, *, commit: bool) -> None:
        scene = self.scene
        bar = drag.bar
        bar.setZValue(2 if bar.selected else 0)
        bar.set_lifted(value=False)
        scene.hint.setVisible(False)
        scene.set_drop_row(None)
        start, end, row = drag.preview
        resource = scene.layout.rows[row].resource
        moved = resource is not None and resource.id != bar.resource_id
        if commit and resource is not None and (moved or (start, end) != drag.span):
            calendar = scene.calendar
            owners = tuple(
                resource.id if owner == bar.resource_id else owner
                for owner in bar.task.resource_ids
            )
            task = replace(
                bar.task,
                start=calendar.moment_at(start),
                end=calendar.moment_at(end, end=True),
                resource_ids=owners,
            )
            scene.commit(task, bar, resource.id)
            return
        for sibling in scene.task_bars[bar.task.id]:
            scene.place(sibling)


# ----------------------------------------------------------------- view ----


class PlanningView(QGraphicsView):
    zoom_requested = Signal(float, float)
    resized = Signal()
    gesture_ended = Signal()

    def __init__(self, scene: PlanningScene) -> None:
        super().__init__(scene)
        self.planning = scene
        self.controller = GestureController(scene, self.visible_scene_rect)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setOptimizationFlags(
            QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing
            | QGraphicsView.OptimizationFlag.DontSavePainterState
        )
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setMouseTracking(True)
        self._cursor = Qt.CursorShape.ArrowCursor
        self.horizontalScrollBar().valueChanged.connect(self._sync_sticky)

    def _sync_sticky(self) -> None:
        self.planning.set_sticky_left(self.mapToScene(0, 0).x())

    def visible_scene_rect(self) -> QRectF:
        return self.mapToScene(self.viewport().rect()).boundingRect()

    def _scene_point(self, event: QMouseEvent) -> QPointF:
        return self.mapToScene(event.position().toPoint())

    def _sync_cursor(self) -> None:
        if self.controller.cursor != self._cursor:
            self._cursor = self.controller.cursor
            self.viewport().setCursor(self._cursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.controller.press(self._scene_point(event))
        self._sync_cursor()
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        # Qt turns a quick second press into a double-click: it must still start a drag.
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        self.mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        point = self._scene_point(event)
        if event.buttons() & Qt.MouseButton.LeftButton:
            fine = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.controller.move(point, fine=fine)
        else:
            self.controller.hover(point)
        self._sync_cursor()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        point = self._scene_point(event)
        self.controller.release(point)
        self.controller.hover(point)
        self._sync_cursor()
        event.accept()
        self.gesture_ended.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape and self.controller.cancel():
            self.controller.cursor = Qt.CursorShape.ArrowCursor
            self._sync_cursor()
            event.accept()
            self.gesture_ended.emit()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        if self.controller.cancel():
            self.gesture_ended.emit()
        super().focusOutEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        if self.controller.state is Gesture.IDLE:
            self.planning.set_hovered(None)
        super().leaveEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        modifiers = event.modifiers()
        steps = event.angleDelta().y() / 120
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            if steps:
                self.zoom_requested.emit(ZOOM_STEP**steps, event.position().x())
            event.accept()
            return
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            scroll = self.horizontalScrollBar()
            scroll.setValue(scroll.value() - event.angleDelta().y())
            event.accept()
            return
        super().wheelEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.resized.emit()
