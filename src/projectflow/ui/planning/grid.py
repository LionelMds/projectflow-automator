"""Planning grid widget: time header, frozen row column and the painted body.

``PlanningGrid`` shows one ``GridModel`` and turns the mouse, the keyboard and drops into
intent signals: it never edits data. The controller runs the matching command, then
gives the grid the new model with ``set_model``; the grid keeps the scroll position and
the selection. Moves inside the body follow the mouse events (a small state machine,
no ``QDrag``); only a plain move dragged out past the right edge of the body, towards
« À planifier », starts a ``QDrag`` carrying the task mime type, and cards dragged from
« À planifier » drop on the grid through ``drag_over`` / ``drop_at``.

Previews use the same calendars as the commands: worked minutes measured on the task's
first assignee, the new span laid out on the first assignee after the move. A drag moves
the pressed bar (and its siblings in time); moving a whole selection with the mouse is
left for a later lot, the arrow keys already shift every selected bar at once.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Literal

from PySide6.QtCore import (
    QByteArray,
    QEvent,
    QMimeData,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QContextMenuEvent,
    QDrag,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QFocusEvent,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPixmap,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QFrame, QGraphicsView, QGridLayout, QWidget

from projectflow.core.planning.calendar import SNAP_MINUTES, WorkCalendar, snap
from projectflow.core.planning.commands import DEFAULT_TASK_MINUTES, MIN_TASK_MINUTES
from projectflow.core.planning.layout import (
    UNASSIGNED_LABEL,
    UNASSIGNED_ROW_ID,
    RowLayout,
    row_kind,
)
from projectflow.core.planning.models import PlanningTask
from projectflow.core.planning.night import (
    fitted_night,
    night_day,
    night_error,
    night_label,
    night_start_at,
)
from projectflow.core.planning.timescale import (
    ViewKind,
    move_preserving_work,
    snap_end,
    snap_start,
)
from projectflow.core.planning.walltime import MINUTES_PER_DAY, WallTime, wall_date
from projectflow.ui.planning.grid_items import (
    BarItem,
    BarState,
    BarZone,
    PlanningScene,
    paint_bar,
)
from projectflow.ui.planning.grid_model import BarData, BarKey, GridModel, span_label
from projectflow.ui.planning.header import HEADER_HEIGHT, TimeHeader
from projectflow.ui.planning.rows import RowColumn, RowCorner
from projectflow.ui.theme import theme_notifier

TASK_MIME = "application/x-projectflow-task"
"""Mime type of a task dragged from « À planifier » or out of the grid (JSON payload)."""
GRID_SOURCE = "grid"

DRAG_THRESHOLD = 4
"""Pixels the pointer travels in empty cells before a press becomes a range or a lasso."""
BAR_DRAG_THRESHOLD = 8
"""Pixels a pressed bar travels before it moves (more if the platform asks): a click
that wobbles stays a click, and a Ctrl+click never makes a copy."""
EXPORT_MARGIN = 12
"""Pixels past the right edge of the body at which a moved bar leaves as a ``QDrag``."""
AUTOSCROLL_ZONE = 24
AUTOSCROLL_STEP = 16
AUTOSCROLL_MS = 30
WHEEL_NOTCH = 120
FIT_TOLERANCE = 0.5
SCROLL_LEAD = 3
"""``scroll_to`` puts the instant at a third of the view width."""
HALF_DAY = 0.5
EXPORT_OPACITY = 0.75

DETAIL_KEYS = frozenset({Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F2})
DELETE_KEYS = frozenset({Qt.Key.Key_Delete, Qt.Key.Key_Backspace})
HORIZONTAL_KEYS = frozenset({Qt.Key.Key_Left, Qt.Key.Key_Right})
VERTICAL_KEYS = frozenset({Qt.Key.Key_Up, Qt.Key.Key_Down})
ARROW_KEYS = HORIZONTAL_KEYS | VERTICAL_KEYS

ACCESSIBLE_NAME = "Planning de l'atelier"
ACCESSIBLE_DESCRIPTION = (
    "Une ligne par personne. Flèches : choisir puis décaler la barre ; Maj+flèches : "
    "allonger ou ajouter ; Entrée : détail ; Suppr : retirer la personne."
)
HEADER_ACCESSIBLE_NAME = "Jours et heures du planning"
ROWS_ACCESSIBLE_NAME = "Personnes du planning"
READ_ONLY_TOOLTIP = "Planning en lecture seule : les modifications sont désactivées."
ALREADY_ASSIGNED = "déjà affecté"
REST_WARNING = "Repos de 8 h"
COPY_SUFFIX = "copie"

MoveMode = Literal["move", "copy", "add"]
MOVE_MODES: tuple[MoveMode, ...] = ("move", "copy", "add")


class Gesture(Enum):
    IDLE = "idle"
    PRESS_BAR = "press-bar"
    PRESS_EMPTY = "press-empty"
    MOVE = "move"
    RESIZE_START = "resize-start"
    RESIZE_END = "resize-end"
    RANGE = "range"
    LASSO = "lasso"


ACTIVE_GESTURES = frozenset(
    {Gesture.MOVE, Gesture.RESIZE_START, Gesture.RESIZE_END, Gesture.RANGE, Gesture.LASSO}
)
_ZONE_GESTURE: dict[BarZone, Gesture] = {
    "body": Gesture.MOVE,
    "start": Gesture.RESIZE_START,
    "end": Gesture.RESIZE_END,
}


# ------------------------------------------------------------------ mime ----


@dataclass(frozen=True, slots=True)
class TaskDrop:
    """Task carried by a drag: its id, where it comes from and the row it left."""

    task_id: str
    source: str = ""
    resource_id: str = ""


def task_mime_data(task_id: str, *, source: str = GRID_SOURCE, resource_id: str = "") -> QMimeData:
    """Mime data of a task drag, the JSON ``{"task_id", "source", "resource_id"}``."""
    payload = {"task_id": task_id, "source": source, "resource_id": resource_id}
    mime = QMimeData()
    mime.setData(TASK_MIME, QByteArray(json.dumps(payload).encode("utf-8")))
    return mime


def parse_task_mime(mime: QMimeData | None) -> TaskDrop | None:
    """Task of a drag: the JSON object above, a JSON string, or a plain task id."""
    if mime is None or not mime.hasFormat(TASK_MIME):
        return None
    raw = bytes(mime.data(TASK_MIME).data()).decode("utf-8", "replace").strip()
    try:
        data: object = json.loads(raw)
    except ValueError:
        data = raw
    if isinstance(data, dict):
        task_id = str(data.get("task_id") or data.get("id") or "").strip()
        source = str(data.get("source") or "")
        resource_id = str(data.get("resource_id") or "")
        return TaskDrop(task_id, source, resource_id) if task_id else None
    text = str(data).strip() if isinstance(data, str | int) else ""
    first = text.split()[0] if text else ""
    return TaskDrop(first) if first else None


def drag_mode(modifiers: Qt.KeyboardModifier) -> MoveMode:
    """Shift adds the person of the target row, Ctrl/Cmd or Alt copies, else move."""
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        return "add"
    if modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
        return "copy"
    return "move"


def bars_by_task(bars: Iterable[BarKey]) -> dict[str, tuple[str, ...]]:
    """Rows of ``bars`` grouped by task, in order: one command (or chain) per task.

    Two bars of one task share its document: commands built on the same snapshot for
    each of them would overwrite each other.
    """
    grouped: dict[str, list[str]] = {}
    for task_id, row_id in bars:
        rows = grouped.setdefault(task_id, [])
        if row_id not in rows:
            rows.append(row_id)
    return {task_id: tuple(rows) for task_id, rows in grouped.items()}


# -------------------------------------------------------------- calendars ----


def _people_after(task: PlanningTask, from_row: str, to_row: str, mode: MoveMode) -> list[str]:
    """Assignees once the move is saved, in order (``commands.move_task`` and the copy)."""
    target = None if to_row == UNASSIGNED_ROW_ID else to_row
    if mode == "copy":
        return [target] if target is not None else []
    people = list(task.assignee_ids)
    if mode != "move" or to_row == from_row:
        return people
    if from_row == UNASSIGNED_ROW_ID:
        return [*people, target] if target is not None else people
    replaced = [target if rid == from_row else rid for rid in people]
    return [rid for rid in replaced if rid is not None]


def move_calendars(
    model: GridModel, task: PlanningTask, from_row: str, to_row: str, mode: MoveMode
) -> tuple[WorkCalendar, WorkCalendar]:
    """Calendars of a saved move: ``(measure, layout)``.

    As in the commands, the worked minutes are measured on the task's first assignee and
    the new span is laid out on the first assignee after the move (the target row for a
    copy), or on the shared default calendar when nobody is left.
    """
    people = _people_after(task, from_row, to_row, mode)
    return model.task_calendar(task.id), model.calendar_for(people[0] if people else None)


def _next_edge(calendar: WorkCalendar, value: WallTime, *, ends: bool) -> WallTime:
    """First period start (or end) of ``calendar`` strictly after ``value``."""
    day = wall_date(value)
    while True:
        for low, high in calendar.day_periods(day):
            edge = high if ends else low
            if edge > value:
                return edge
        day = calendar.next_workday(day)


def _previous_edge(calendar: WorkCalendar, value: WallTime, *, ends: bool) -> WallTime:
    """Last period start (or end) of ``calendar`` strictly before ``value``."""
    for low, high in reversed(calendar.day_periods(wall_date(value))):
        edge = high if ends else low
        if edge < value:
            return edge
    low, high = calendar.day_periods(calendar.previous_workday(wall_date(value)))[-1]
    return high if ends else low


def half_day_shift(
    calendar: WorkCalendar, start: WallTime, end: WallTime, *, direction: int, resize_end: bool
) -> int:
    """Signed worked minutes to the next (``direction`` 1) or previous half-day edge.

    The start steps to a period start (06:30 or 13:00), or the end to a period end (12:00
    or 16:30) with ``resize_end``; an end never steps onto or before the start (0).
    """
    if resize_end:
        if direction > 0:
            return calendar.work_between(end, _next_edge(calendar, end, ends=True))
        target = _previous_edge(calendar, end, ends=True)
        return -calendar.work_between(target, end) if target > start else 0
    if direction > 0:
        return calendar.work_between(start, _next_edge(calendar, start, ends=False))
    return -calendar.work_between(_previous_edge(calendar, start, ends=False), start)


def shortest_end(calendar: WorkCalendar, start: WallTime, view: ViewKind) -> WallTime:
    """End of the shortest span from ``start``: 30 worked minutes, half a day in the month."""
    if view == "month":
        return calendar.add_days(start, HALF_DAY)
    return calendar.add_work(start, MIN_TASK_MINUTES)


def shortest_start(calendar: WorkCalendar, end: WallTime, view: ViewKind) -> WallTime:
    """Start of the shortest span ending at ``end``: 30 worked minutes, or the half day."""
    if view == "month":
        return _previous_edge(calendar, end, ends=False)
    return calendar.subtract_work(end, MIN_TASK_MINUTES)


# --------------------------------------------------------------- previews ----


@dataclass(frozen=True, slots=True)
class Preview:
    """Where a bar would land: span, row, mode; ``valid`` False when nothing would change."""

    start: WallTime
    end: WallTime
    row_id: str
    mode: MoveMode = "move"
    valid: bool = True
    warn: bool = False
    calendar: WorkCalendar | None = field(default=None, compare=False)
    """Calendar the span was laid out with (the row's when None), for its label."""
    night: bool = False
    """Night work: drawn over its evening's column, labelled with its exact hours."""
    reason: str = ""
    """Why the saved command will refuse this place (a night on a Friday evening)."""
    task_id: str = ""
    """Task previewed: its own rest never warns."""


@dataclass(slots=True)
class BarDrag:
    item: BarItem
    bar: BarData
    zone: BarZone
    press: QPointF
    press_view: QPoint
    grab: int
    """Worked minutes between the bar's start and the pressed instant."""
    preview: Preview
    toggle: bool = False
    """Ctrl was held at the press: a click toggles the bar in the selection."""


@dataclass(slots=True)
class CellDrag:
    press: QPointF
    press_view: QPoint
    row_id: str
    lasso: bool = False
    toggle: bool = False
    rows: list[str] = field(default_factory=list)
    start: WallTime = 0
    end: WallTime = 0


# ---------------------------------------------------------------- gestures ----


class GridGestures:
    """Mouse and keyboard state machine of the body; works in scene coordinates.

    idle -> press-bar -> move | resize-start | resize-end -> idle, and
    idle -> press-empty -> range | lasso -> idle. ``Escape`` cancels any of them.
    """

    def __init__(self, grid: PlanningGrid) -> None:
        self.grid = grid
        self.state = Gesture.IDLE
        self.bar_drag: BarDrag | None = None
        self.cell_drag: CellDrag | None = None
        self.hovered: BarItem | None = None
        self.hovered_absence = ""
        self.swallow = False
        self.last_view = QPoint()
        self.last_modifiers = Qt.KeyboardModifier.NoModifier

    @property
    def scene(self) -> PlanningScene:
        return self.grid.scene

    @property
    def model(self) -> GridModel | None:
        return self.grid.model

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_GESTURES

    # Hover ------------------------------------------------------------

    def hover(self, point: QPointF, global_pos: QPoint) -> None:
        item = self.scene.bar_at(point)
        block = self.scene.absence_at(point) if item is None else None
        absence_id = block.absence_id if block is not None else ""
        bar_changed = item is not self.hovered
        absence_changed = absence_id != self.hovered_absence
        self.hovered = item
        self.hovered_absence = absence_id
        if bar_changed:
            self.grid.refresh_selection()
        # What the pointer left is said first, so that the card waits for what it reached.
        if bar_changed and item is None:
            self.grid.hover_changed.emit("", global_pos)
        if absence_changed:
            self.grid.absence_hovered.emit(absence_id, global_pos)
        if bar_changed and item is not None:
            self.grid.hover_changed.emit(item.bar.task_id, global_pos)
        row = self.scene.row_at(point.y())
        self.grid.rows.set_hovered(row.resource_id if row is not None else None)
        self.grid.set_cursor(self._hover_cursor(item, point))

    def leave(self) -> None:
        if self.hovered is not None:
            self.hovered = None
            self.grid.refresh_selection()
            self.grid.hover_changed.emit("", QPoint())
        if self.hovered_absence:
            self.hovered_absence = ""
            self.grid.absence_hovered.emit("", QPoint())
        self.grid.rows.set_hovered(None)

    def _hover_cursor(self, item: BarItem | None, point: QPointF) -> Qt.CursorShape:
        if item is None:
            return Qt.CursorShape.ArrowCursor
        if self.grid.read_only:
            return Qt.CursorShape.PointingHandCursor
        if item.zone_at(point.x() - item.x()) != "body":
            return Qt.CursorShape.SizeHorCursor
        return Qt.CursorShape.OpenHandCursor

    # Press, move, release ---------------------------------------------

    def press(self, point: QPointF, view_pos: QPoint, modifiers: Qt.KeyboardModifier) -> None:
        model = self.model
        self.swallow = False
        self.last_view, self.last_modifiers = view_pos, modifiers
        self.scene.range_item.setVisible(False)
        if model is None:
            return
        item = self.scene.bar_at(point)
        if item is not None:
            self._press_bar(item, point, view_pos, modifiers)
            return
        row = self.scene.row_at(point.y())
        self.cell_drag = CellDrag(
            press=QPointF(point),
            press_view=QPoint(view_pos),
            row_id=row.resource_id if row is not None else "",
            lasso=bool(modifiers & Qt.KeyboardModifier.ShiftModifier),
            toggle=bool(modifiers & Qt.KeyboardModifier.ControlModifier),
        )
        self.state = Gesture.PRESS_EMPTY

    def _press_bar(
        self, item: BarItem, point: QPointF, view_pos: QPoint, modifiers: Qt.KeyboardModifier
    ) -> None:
        model = self.model
        if model is None:
            return
        bar = item.bar
        toggle = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        if not toggle and item.key not in self.grid.selection:
            self.grid.set_selection([item.key], current=item.key)
        else:
            self.grid.current = item.key
        calendar = model.task_calendar(bar.task_id)
        pressed = model.timescale.time_at(point.x())
        grab = calendar.work_between(bar.start, pressed) if pressed > bar.start else 0
        zone = "body" if self.grid.read_only else item.zone_at(point.x() - item.x())
        self.bar_drag = BarDrag(
            item=item,
            bar=bar,
            zone=zone,
            press=QPointF(point),
            press_view=QPoint(view_pos),
            grab=grab,
            preview=Preview(bar.start, bar.end, bar.row_id),
            toggle=toggle,
        )
        self.state = Gesture.PRESS_BAR

    def move(self, point: QPointF, view_pos: QPoint, modifiers: Qt.KeyboardModifier) -> None:
        self.last_view, self.last_modifiers = view_pos, modifiers
        if self.swallow:
            return
        if self.state is Gesture.PRESS_BAR:
            self._start_bar_drag(view_pos)
        elif self.state is Gesture.PRESS_EMPTY:
            self._start_cell_drag(view_pos)
        if self.state is Gesture.MOVE and self._leaves_view(view_pos, modifiers):
            self._export()
            return
        if self.state in ACTIVE_GESTURES:
            self.update(point, modifiers)
            self.grid.autoscroll(view_pos)

    def _moved_enough(self, press_view: QPoint, view_pos: QPoint, threshold: int) -> bool:
        return (view_pos - press_view).manhattanLength() >= threshold

    def _start_bar_drag(self, view_pos: QPoint) -> None:
        drag = self.bar_drag
        threshold = max(BAR_DRAG_THRESHOLD, QGuiApplication.styleHints().startDragDistance())
        if (
            drag is None
            or self.grid.read_only
            or not self._moved_enough(drag.press_view, view_pos, threshold)
        ):
            return
        self.state = _ZONE_GESTURE[drag.zone]
        self.grid.drag_started.emit(drag.bar.task_id)

    def _start_cell_drag(self, view_pos: QPoint) -> None:
        drag = self.cell_drag
        if drag is None or not self._moved_enough(drag.press_view, view_pos, DRAG_THRESHOLD):
            return
        if drag.lasso:
            self.state = Gesture.LASSO
        elif not self.grid.read_only and drag.row_id and _editable(drag.row_id):
            self.state = Gesture.RANGE

    def update(self, point: QPointF, modifiers: Qt.KeyboardModifier) -> None:
        """Refresh the preview of the running gesture for ``point`` and ``modifiers``."""
        if self.state is Gesture.MOVE and self.bar_drag is not None:
            self._show_bar_preview(
                self.bar_drag, self._move_preview(self.bar_drag, point, modifiers)
            )
        elif self.state in {Gesture.RESIZE_START, Gesture.RESIZE_END} and self.bar_drag:
            self._show_bar_preview(self.bar_drag, self._resize_preview(self.bar_drag, point))
        elif self.state is Gesture.RANGE and self.cell_drag is not None:
            self._show_range(self.cell_drag, point)
        elif self.state is Gesture.LASSO and self.cell_drag is not None:
            self._show_lasso(self.cell_drag, point)

    def release(self, point: QPointF, modifiers: Qt.KeyboardModifier) -> None:
        state = self.state
        self.grid.stop_autoscroll()
        if self.swallow:
            self._reset()
            return
        if state in ACTIVE_GESTURES:
            self.update(point, modifiers)
        if state is Gesture.PRESS_BAR and self.bar_drag is not None:
            self._click_bar(self.bar_drag, modifiers)
        elif state is Gesture.PRESS_EMPTY and self.cell_drag is not None:
            self._click_empty(self.cell_drag)
        elif state in {Gesture.MOVE, Gesture.RESIZE_START, Gesture.RESIZE_END}:
            self._finish_bar(commit=True)
        elif state is Gesture.RANGE:
            self._finish_range()
        elif state is Gesture.LASSO:
            self._finish_lasso()
        self._reset()

    def double_click(self, point: QPointF) -> None:
        model = self.model
        self._reset()
        if model is None:
            return
        item = self.scene.bar_at(point)
        if item is not None:
            self.grid.bar_double_clicked.emit(item.bar.task_id, item.bar.row_id)
            return
        block = self.scene.absence_at(point)
        if block is not None and block.absence_id:
            self.grid.absence_activated.emit(block.absence_id)
            return
        row = self.scene.row_at(point.y())
        if row is None or not _editable(row.resource_id) or self.grid.read_only:
            return
        start = self.grid.snapped_start(row.resource_id, point.x())
        self.grid.cell_activated.emit(row.resource_id, start)

    def cancel(self) -> bool:
        """Escape: drop the running gesture without any change; True when one ran."""
        was_active = self.active
        self.grid.stop_autoscroll()
        if self.state is Gesture.IDLE:
            return False
        if self.bar_drag is not None and was_active:
            self.scene.place_task(self.bar_drag.bar.task_id)
            self.grid.drag_finished.emit(self.bar_drag.bar.task_id)
        if was_active:
            self.scene.hide_previews()
        self.swallow = True
        self.state = Gesture.IDLE
        self.bar_drag = None
        self.cell_drag = None
        self.grid.apply_deferred()
        return was_active

    def _reset(self) -> None:
        self.state = Gesture.IDLE
        self.bar_drag = None
        self.cell_drag = None
        self.swallow = False
        self.grid.apply_deferred()

    # Clicks -----------------------------------------------------------

    def _click_bar(self, drag: BarDrag, modifiers: Qt.KeyboardModifier) -> None:
        key = drag.item.key
        if drag.toggle:
            keys = [k for k in self.grid.selection if k != key]
            if key not in self.grid.selection:
                keys.append(key)
            self.grid.set_selection(keys, current=key)
        else:
            self.grid.set_selection([key], current=key)
        self.grid.bar_clicked.emit(drag.bar.task_id, drag.bar.row_id, modifiers)

    def _click_empty(self, drag: CellDrag) -> None:
        if not drag.toggle:
            self.grid.set_selection([])

    # Bar previews -----------------------------------------------------

    def _target_row(self, drag: BarDrag, y: float) -> str:
        row = self.scene.row_at(y, clamp=True)
        if row is None or row.kind == "team":
            return drag.preview.row_id
        return row.resource_id

    def _move_preview(
        self, drag: BarDrag, point: QPointF, modifiers: Qt.KeyboardModifier
    ) -> Preview:
        model = self.model
        bar = drag.bar
        if model is None:
            return drag.preview
        mode = drag_mode(modifiers)
        target = self._target_row(drag, point.y())
        task = model.task(bar.task_id)
        others = set(task.assignee_ids) - {bar.row_id} if task is not None else set()
        if mode == "add":
            valid = target not in others and target not in {bar.row_id, UNASSIGNED_ROW_ID}
            warn = valid and warned(model, target, bar.start, bar.end, bar.task_id)
            calendar = model.task_calendar(bar.task_id)
            return Preview(
                bar.start,
                bar.end,
                target,
                "add",
                valid=valid,
                warn=warn,
                calendar=calendar,
                night=bar.night,
                task_id=bar.task_id,
            )
        if mode == "move" and target in others:
            target = bar.row_id
        if task is None:
            return drag.preview
        if bar.night:
            start, end = night_move(model, bar.start, bar.end, drag.press.x(), point.x())
            return night_preview(model, start, end, target, mode=mode, task_id=bar.task_id)
        measure, layout = move_calendars(model, task, bar.row_id, target, mode)
        scale = model.timescale
        if bar.cut_left:
            pointer = scale.time_at(point.x())
            candidate = layout.subtract_work(pointer, drag.grab) if drag.grab else pointer
        else:
            candidate = scale.time_at(scale.x_of(bar.start) + point.x() - drag.press.x())
        candidate = snap_start(layout, candidate, model.view)
        start, end = move_preserving_work(bar.start, bar.end, candidate, measure, target=layout)
        warn = warned(model, target, start, end, bar.task_id)
        return Preview(start, end, target, mode, warn=warn, calendar=layout, task_id=bar.task_id)

    def _resize_preview(self, drag: BarDrag, point: QPointF) -> Preview:
        """Give the resized span with the calendar and minimum of ``commands.resize_task``."""
        model = self.model
        bar = drag.bar
        if model is None:
            return drag.preview
        scale = model.timescale
        calendar = model.task_calendar(bar.task_id)
        delta = point.x() - drag.press.x()
        start, end = bar.start, bar.end
        if bar.night:
            if self.state is Gesture.RESIZE_START:
                start = min(snap(scale.time_at(scale.x_of(bar.start) + delta)), end - SNAP_MINUTES)
            else:
                moved = snap(scale.time_at(scale.x_of(bar.end) + delta, end=True))
                end = max(moved, start + SNAP_MINUTES)
            return night_preview(model, start, end, bar.row_id, mode="move", task_id=bar.task_id)
        if self.state is Gesture.RESIZE_START:
            value = snap_start(calendar, scale.time_at(scale.x_of(bar.start) + delta), model.view)
            if calendar.work_between(value, end) < MIN_TASK_MINUTES:
                value = shortest_start(calendar, end, model.view)
            start = value
        else:
            moved = scale.time_at(scale.x_of(bar.end) + delta, end=True)
            value = snap_end(calendar, moved, model.view)
            if calendar.work_between(start, value) < MIN_TASK_MINUTES:
                value = shortest_end(calendar, start, model.view)
            end = value
        warn = warned(model, bar.row_id, start, end, bar.task_id)
        return Preview(
            start, end, bar.row_id, "move", warn=warn, calendar=calendar, task_id=bar.task_id
        )

    def _show_bar_preview(self, drag: BarDrag, preview: Preview) -> None:
        model = self.model
        if model is None:
            return
        drag.preview = preview
        scene = self.scene
        bar = drag.bar
        same_row = preview.row_id == bar.row_id
        lane = bar.lane if same_row else 0
        rect = scene.span_rect(preview.start, preview.end, preview.row_id, lane, night=bar.night)
        if preview.mode == "move":
            scene.ghost.setVisible(False)
            drag.item.set_flags(lifted=True, warn=preview.warn)
            drag.item.set_geometry(rect)
            for sibling in scene.items_by_task.get(bar.task_id, ()):
                if sibling is not drag.item:
                    sibling.set_geometry(
                        scene.span_rect(
                            preview.start,
                            preview.end,
                            sibling.bar.row_id,
                            sibling.bar.lane,
                            night=bar.night,
                        )
                    )
        else:
            scene.place_task(bar.task_id)
            ghost = model.preview_bar(bar.task_id, preview.row_id, preview.start, preview.end)
            if preview.valid and ghost is not None:
                scene.ghost.show_bar(ghost, rect, warn=preview.warn)
            else:
                scene.ghost.setVisible(False)
        scene.set_drop_row(None if same_row else preview.row_id)
        scene.label.show_text(
            self.grid.preview_text(preview),
            rect,
            self.grid.visible_scene_rect(),
            warn=preview.warn or not preview.valid,
        )
        self.grid.set_cursor(_drag_cursor(self.state, preview.mode))

    def _finish_bar(self, *, commit: bool) -> None:
        drag = self.bar_drag
        if drag is None:
            return
        state = self.state
        self.state = Gesture.IDLE
        self.scene.hide_previews()
        self.grid.apply_deferred()
        serial = self.grid.serial
        if commit:
            self._commit_bar(drag, state)
        if self.grid.serial == serial:
            self.scene.place_task(drag.bar.task_id)
        self.grid.drag_finished.emit(drag.bar.task_id)

    def _commit_bar(self, drag: BarDrag, state: Gesture) -> None:
        bar, preview = drag.bar, drag.preview
        if state is Gesture.MOVE:
            # A move or a copy dropped back where it started changes nothing.
            changed = preview.row_id != bar.row_id or preview.start != bar.start
            if not preview.valid or not changed:
                return
            if preview.mode == "move" and preview.row_id != bar.row_id:
                self.grid.follow(bar.key, (bar.task_id, preview.row_id))
            self.grid.move_requested.emit(
                bar.task_id, bar.row_id, preview.row_id, preview.start, preview.mode
            )
            return
        start = preview.start if preview.start != bar.start else None
        end = preview.end if preview.end != bar.end else None
        if start is not None or end is not None:
            self.grid.resize_requested.emit(bar.task_id, bar.row_id, start, end)

    # Range and lasso --------------------------------------------------

    def _show_range(self, drag: CellDrag, point: QPointF) -> None:
        model = self.model
        if model is None:
            return
        editable = list(model.editable_row_ids())
        first = drag.row_id
        if first not in editable:
            return
        row = self.scene.row_at(point.y(), clamp=True)
        last = _nearest_editable(model, row, editable) or first
        low, high = sorted((editable.index(first), editable.index(last)))
        drag.rows = editable[low : high + 1]
        scale = model.timescale
        calendar = model.calendar_of(drag.rows[0])
        left, right = sorted((drag.press.x(), point.x()))
        drag.start = snap_start(calendar, scale.time_at(left), model.view)
        drag.end = snap_end(calendar, scale.time_at(right, end=True), model.view)
        if calendar.work_between(drag.start, drag.end) < MIN_TASK_MINUTES:
            drag.end = shortest_end(calendar, drag.start, model.view)
        top_row = self.scene.row(drag.rows[0])
        bottom_row = self.scene.row(drag.rows[-1])
        if top_row is None or bottom_row is None:
            return
        top = self.scene.row_top(top_row)
        bottom = self.scene.row_top(bottom_row) + bottom_row.height
        x0, x1 = scale.span_x(drag.start, drag.end)
        area = QRectF(x0, min(top, bottom - 1), max(x1 - x0, 1.0), max(bottom - top, 1.0))
        self.scene.range_item.show_area(area)
        names = " · ".join(self.grid.row_short(row_id) for row_id in drag.rows)
        text = f"{span_label(calendar, drag.start, drag.end)} · {names}"
        self.scene.label.show_text(text, area, self.grid.visible_scene_rect())

    def _show_lasso(self, drag: CellDrag, point: QPointF) -> None:
        area = QRectF(drag.press, point).normalized()
        self.scene.range_item.show_area(area, lasso=True)

    def _finish_range(self) -> None:
        drag = self.cell_drag
        self.state = Gesture.IDLE
        self.scene.label.setVisible(False)
        if drag is None or not drag.rows:
            self.scene.range_item.setVisible(False)
            return
        self.grid.range_selected.emit(list(drag.rows), drag.start, drag.end)

    def _finish_lasso(self) -> None:
        area = QRectF(self.scene.range_item.area)
        drag = self.cell_drag
        self.scene.range_item.setVisible(False)
        keys = [item.key for item in self.scene.bars_in(area) if item.isVisible()]
        if drag is not None and drag.toggle:
            keys = [*self.grid.selection, *(key for key in keys if key not in self.grid.selection)]
        self.grid.set_selection(keys, current=keys[-1] if keys else None)

    # Export -----------------------------------------------------------

    def _leaves_view(self, view_pos: QPoint, modifiers: Qt.KeyboardModifier) -> bool:
        """Tell whether a plain move went past the right edge, where « À planifier » is.

        Copies, added people and the other edges stay inside: they autoscroll.
        """
        if not self.grid.drop_enabled or drag_mode(modifiers) != "move":
            return False
        return view_pos.x() > self.grid.view.viewport().rect().right() + EXPORT_MARGIN

    def _export(self) -> None:
        drag = self.bar_drag
        if drag is None:
            return
        self.grid.stop_autoscroll()
        self.scene.place_task(drag.bar.task_id)
        self.scene.hide_previews()
        self.state = Gesture.IDLE
        self.bar_drag = None
        self.swallow = True
        self.grid.apply_deferred()
        self.grid.export_bar(drag.bar, grab=drag.grab)

    # Keyboard ---------------------------------------------------------

    def modifiers_changed(self, modifiers: Qt.KeyboardModifier) -> None:
        """Shift, Ctrl or Alt pressed or released while dragging: refresh the mode."""
        self.last_modifiers = modifiers
        if self.state is Gesture.MOVE:
            view = self.grid.view
            self.update(view.mapToScene(self.last_view), modifiers)


def _editable(row_id: str) -> bool:
    """Rows that hold bars: people and « Non affecté », not the team headers."""
    return row_kind(row_id) != "team"


def _is_bar_drop(payload: TaskDrop, task: PlanningTask) -> bool:
    """Tell a bar of the grid coming back from a card of « À planifier »."""
    return payload.source == GRID_SOURCE and task.start is not None and task.end is not None


def _nearest_editable(model: GridModel, row: RowLayout | None, editable: Sequence[str]) -> str:
    """Row id of ``row``, or of the first editable row below a team header."""
    if row is None:
        return ""
    if row.resource_id in editable:
        return row.resource_id
    index = model.rows.index_of(row.resource_id)
    if index is None:
        return ""
    for following in model.rows.rows[index:]:
        if following.resource_id in editable:
            return following.resource_id
    return editable[-1] if editable else ""


def _drag_cursor(state: Gesture, mode: MoveMode) -> Qt.CursorShape:
    if state is not Gesture.MOVE:
        return Qt.CursorShape.SizeHorCursor
    if mode == "move":
        return Qt.CursorShape.ClosedHandCursor
    return Qt.CursorShape.DragCopyCursor


# ------------------------------------------------------------------- view ----


class PlanningView(QGraphicsView):
    """Body of the grid: forwards its events to the grid's gestures."""

    def __init__(self, grid: PlanningGrid, scene: PlanningScene) -> None:
        super().__init__(scene, grid)
        self.grid = grid
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
        self.setCacheMode(QGraphicsView.CacheModeFlag.CacheBackground)
        self.setOptimizationFlags(
            QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing
            | QGraphicsView.OptimizationFlag.DontSavePainterState
        )
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(ACCESSIBLE_NAME)
        self.setAccessibleDescription(ACCESSIBLE_DESCRIPTION)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.viewport().setMouseTracking(True)

    @property
    def gestures(self) -> GridGestures:
        return self.grid.gestures

    def scene_point(self, position: QPointF) -> QPointF:
        return self.mapToScene(position.toPoint())

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        position = event.position()
        self.gestures.press(self.scene_point(position), position.toPoint(), event.modifiers())
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        self.gestures.double_click(self.scene_point(event.position()))
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        position = event.position()
        point = self.scene_point(position)
        if self.gestures.state is Gesture.IDLE:
            self.gestures.hover(point, event.globalPosition().toPoint())
        else:
            self.gestures.move(point, position.toPoint(), event.modifiers())
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        position = event.position()
        point = self.scene_point(position)
        self.gestures.release(point, event.modifiers())
        self.gestures.hover(point, event.globalPosition().toPoint())
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self.grid.handle_key(event):
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if self.gestures.active:
            self.gestures.modifiers_changed(event.modifiers())
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.grid.show_focus(focused=True)

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        self.gestures.cancel()
        super().focusOutEvent(event)
        self.grid.show_focus(focused=False)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        if self.gestures.state is Gesture.IDLE:
            self.gestures.leave()
        super().leaveEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802
        self.grid.context_menu_at(self.mapToScene(event.pos()), event.globalPos())
        event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            bar = self.horizontalScrollBar()
            delta = event.angleDelta().y() or event.angleDelta().x()
            bar.setValue(bar.value() - round(delta / WHEEL_NOTCH * bar.singleStep() * 3))
            event.accept()
            return
        super().wheelEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.grid.viewport_resized()

    # Drops from « À planifier » ---------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        self._drag_over(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        self._drag_over(event)

    def _drag_over(self, event: QDragMoveEvent) -> None:
        point = self.scene_point(event.position())
        if self.grid.drag_over(event.mimeData(), point, event.modifiers()):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # noqa: N802
        self.grid.drag_left()
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        point = self.scene_point(event.position())
        if self.grid.drop_at(event.mimeData(), point, event.modifiers()):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()


# ------------------------------------------------------------------- grid ----


class PlanningGrid(QWidget):
    """The planning grid: header, row column and body, all painted from a ``GridModel``.

    Every signal is an intent; the grid never edits data. Instants are ``WallTime``
    integers, bars are ``(task_id, row_id)`` tuples (« Non affecté » is
    ``UNASSIGNED_ROW_ID``).
    """

    bar_clicked = Signal(str, str, object)
    """``(task_id, row_id, Qt.KeyboardModifier)`` after a click without drag."""
    bar_double_clicked = Signal(str, str)
    selection_changed = Signal(object)
    """List of ``(task_id, row_id)``."""
    move_requested = Signal(str, str, str, int, str)
    """``(task_id, from_row, to_row, new_start, mode)``, mode ``move``, ``copy`` or ``add``."""
    resize_requested = Signal(str, str, object, object)
    """``(task_id, row_id, start or None, end or None)``."""
    range_selected = Signal(object, int, int)
    """``(row_ids, start, end)`` of cells selected by a drag in the empty grid."""
    cell_activated = Signal(str, int)
    """``(row_id, start)`` of a double-clicked empty cell."""
    card_dropped = Signal(str, str, int)
    """``(task_id, row_id, start)`` of a card dropped from « À planifier »."""
    shift_requested = Signal(object, int, bool)
    """``(bars, signed worked minutes, resize_end)`` from the arrow keys (Shift: the end).

    30 minutes in the day and week views. In the month view each bar steps to its next
    half-day edge on its task's calendar, so one key press emits one signal per group of
    bars needing the same minutes (one only when they are aligned alike).
    """
    reassign_requested = Signal(str, str, str, bool)
    """``(task_id, from_row, to_row, add)`` from Up / Down (Shift adds)."""
    delete_requested = Signal(object, bool)
    """``(bars, permanent)``. Shift+Delete (``permanent``) deletes the tasks. Delete removes
    the person of each bar (``unassign``); a bar of « Non affecté » (``UNASSIGNED_ROW_ID``)
    has nobody to remove and goes back to « À planifier » (``unschedule_task``). Bars of one
    task share its document: group them with ``bars_by_task``."""
    detail_requested = Signal(str, str)
    context_menu_requested = Signal(str, str, int, QPoint)
    """``(task_id or "", row_id or "", start or -1, global position)``."""
    hover_changed = Signal(str, QPoint)
    absence_activated = Signal(str)
    """``absence_id`` of a double-clicked absence block (``recurring_block_id`` for a
    recurring day off, see ``grid_model.recurring_resource_id``)."""
    absence_menu_requested = Signal(str, QPoint)
    """``(absence_id, global position)`` of a right click on an absence block."""
    absence_hovered = Signal(str, QPoint)
    """``(absence_id or "", global position)``: the pointer reached or left an absence."""
    team_toggled = Signal(str)
    row_menu_requested = Signal(str, QPoint)
    row_clicked = Signal(str, object)
    drag_started = Signal(str)
    drag_finished = Signal(str)
    bar_drag_exported = Signal(str)
    """A bar dragged out of the grid was accepted elsewhere (« À planifier »)."""
    visible_range_changed = Signal(object, object)
    day_clicked = Signal(object)
    week_clicked = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model: GridModel | None = None
        self.serial = 0
        self.selection: list[BarKey] = []
        self.current: BarKey | None = None
        self.read_only = False
        self.drop_enabled = True
        self._read_only_reason = ""
        self._deferred: GridModel | None = None
        self._follow: dict[BarKey, BarKey] = {}
        self._visible_range: tuple[date, date] | None = None
        self._export_grab = 0
        self._dropped_inside = False
        self.scene = PlanningScene(self)
        self.gestures = GridGestures(self)
        self.view = PlanningView(self, self.scene)
        self.header = TimeHeader(self)
        self.rows = RowColumn(self)
        self.corner = RowCorner(self)
        self.corner.setFixedHeight(HEADER_HEIGHT)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.corner, 0, 0)
        layout.addWidget(self.header, 0, 1)
        layout.addWidget(self.rows, 1, 0)
        layout.addWidget(self.view, 1, 1)
        layout.setColumnStretch(1, 1)
        layout.setRowStretch(1, 1)
        self.setFocusProxy(self.view)
        self.header.setAccessibleName(HEADER_ACCESSIBLE_NAME)
        self.rows.setAccessibleName(ROWS_ACCESSIBLE_NAME)
        for widget in (self.header, self.rows, self.corner):
            # A click on the header or the row column gives the keyboard to the body.
            widget.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            widget.setFocusProxy(self.view)
        self._autoscroll = QTimer(self)
        self._autoscroll.setInterval(AUTOSCROLL_MS)
        self._autoscroll.timeout.connect(self._autoscroll_tick)
        self._scroll_delta = QPoint()
        self._wire()

    def _wire(self) -> None:
        self.view.horizontalScrollBar().valueChanged.connect(self._sync_scroll)
        self.view.verticalScrollBar().valueChanged.connect(self._sync_scroll)
        self.view.horizontalScrollBar().rangeChanged.connect(self._sync_scroll)
        self.view.verticalScrollBar().rangeChanged.connect(self._sync_scroll)
        self.rows.team_toggled.connect(self.team_toggled)
        self.rows.row_menu_requested.connect(self.row_menu_requested)
        self.rows.row_clicked.connect(self.row_clicked)
        self.rows.scroll_requested.connect(self._scroll_rows)
        self.header.day_clicked.connect(self.day_clicked)
        self.header.week_clicked.connect(self.week_clicked)
        theme_notifier().changed.connect(self._on_theme_changed)

    # Model ------------------------------------------------------------

    def set_model(self, model: GridModel) -> None:
        """Show ``model``, refitted to the viewport; scroll and selection stay when possible.

        From the press of a gesture to its end the model waits, so the bars never jump
        under the pointer and the pressed row stays known.
        """
        if self.gestures.state is not Gesture.IDLE:
            self._deferred = model
            return
        self._deferred = None
        width = self.viewport_width()
        if self.view.isVisible() and abs(model.viewport_width - width) > FIT_TOLERANCE:
            model = model.refit(width)
        self.model = model
        self.serial += 1
        self.scene.set_model(model)
        self.header.set_model(model)
        self.rows.set_model(model)
        self.corner.set_count(sum(1 for row in model.rows if row.kind == "resource"))
        self._keep_selection(model)
        self._sync_scroll()

    def apply_deferred(self) -> None:
        """Show the model that arrived during a gesture (called when the gesture ends)."""
        model = self._deferred
        if model is not None and self.gestures.state is Gesture.IDLE:
            self.set_model(model)

    def follow(self, old: BarKey, new: BarKey) -> None:
        """Keep ``old`` selected as ``new`` once the next model moved it to another row."""
        self._follow[old] = new

    def _keep_selection(self, model: GridModel) -> None:
        keys: list[BarKey] = []
        for key in self.selection:
            kept = key if key in model.bars_by_key else self._follow.get(key)
            if kept is not None and kept in model.bars_by_key and kept not in keys:
                keys.append(kept)
        current = self.current
        if current is not None and current not in model.bars_by_key:
            current = self._follow.get(current)
        self._follow.clear()
        self.set_selection(keys, current=current if current in keys else None)

    def viewport_width(self) -> float:
        return float(self.view.viewport().width())

    # Selection --------------------------------------------------------

    def select(self, bars: Iterable[BarKey]) -> None:
        """Select ``bars`` (those shown by the model); the last one gets the keyboard."""
        model = self.model
        keys = [
            key for key in dict.fromkeys(bars) if model is not None and key in model.bars_by_key
        ]
        self.set_selection(keys, current=keys[-1] if keys else None)

    def selected_bars(self) -> list[BarKey]:
        return list(self.selection)

    def set_selection(self, keys: Sequence[BarKey], *, current: BarKey | None = None) -> None:
        changed = list(keys) != self.selection
        self.selection = list(keys)
        self.current = current if current in self.selection else (keys[-1] if keys else None)
        self.refresh_selection()
        if changed:
            self.selection_changed.emit(list(self.selection))

    def refresh_selection(self) -> None:
        self.scene.set_selection(
            self.selection, hovered=self.gestures.hovered, current=self.current
        )

    def show_focus(self, *, focused: bool) -> None:
        """Body got or lost the keyboard: accent rule left of it, ring on the current bar."""
        self.scene.set_focused(focused)
        self.rows.set_focused(focused)
        self.corner.set_focused(focused)

    # Public API -------------------------------------------------------

    def scroll_to(self, value: WallTime, row_id: str | None = None) -> None:
        """Bring the instant ``value`` (and the row ``row_id``) into view."""
        model = self.model
        if model is None:
            return
        x = model.timescale.x_of(value)
        bar = self.view.horizontalScrollBar()
        bar.setValue(round(x - self.view.viewport().width() / SCROLL_LEAD))
        row = model.rows.get(row_id) if row_id else None
        if row is not None:
            vertical = self.view.verticalScrollBar()
            top, bottom = row.top, row.bottom
            visible_top = vertical.value() + self._pinned_height()
            visible_bottom = vertical.value() + self.view.viewport().height()
            if top < visible_top:
                vertical.setValue(round(top - self._pinned_height()))
            elif bottom > visible_bottom:
                vertical.setValue(round(bottom - self.view.viewport().height()))

    def _pinned_height(self) -> float:
        model = self.model
        pinned = model.rows.get(UNASSIGNED_ROW_ID) if model is not None else None
        return float(pinned.height) if pinned is not None else 0.0

    def set_read_only(self, read_only: bool, *, reason: str = "") -> None:  # noqa: FBT001
        """No drags, resizes, drops or editing keys; the body's tooltip says why."""
        self.read_only = read_only
        self._read_only_reason = reason or READ_ONLY_TOOLTIP
        if read_only:
            self.gestures.cancel()
        self.view.viewport().setToolTip(self._read_only_reason if read_only else "")

    def flash(self, task_ids: Iterable[str]) -> None:
        """Flash the bars of tasks changed by another poste (600 ms)."""
        self.scene.flash(task_ids)

    def set_pending(self, task_ids: Iterable[str]) -> None:
        """Mark the bars whose writes are not yet confirmed with a small dot."""
        self.scene.set_pending(task_ids)

    def set_drop_enabled(self, enabled: bool) -> None:  # noqa: FBT001
        """Accept cards from « À planifier », and let bars leave towards it."""
        self.drop_enabled = enabled
        self.view.viewport().setAcceptDrops(enabled)

    def clear_range(self) -> None:
        """Hide the cells selected by the last range drag (the editor closed)."""
        self.scene.range_item.setVisible(False)

    def visible_range(self) -> tuple[date, date] | None:
        return self._visible_range

    def visible_scene_rect(self) -> QRectF:
        return self.view.mapToScene(self.view.viewport().rect()).boundingRect()

    def set_cursor(self, shape: Qt.CursorShape) -> None:
        viewport = self.view.viewport()
        if viewport.cursor().shape() != shape:
            viewport.setCursor(shape)

    # Texts ------------------------------------------------------------

    def row_short(self, row_id: str) -> str:
        """Give the initials of a row, « Non affecté » for the unassigned one."""
        if row_id == UNASSIGNED_ROW_ID:
            return UNASSIGNED_LABEL
        label = self.model.label(row_id) if self.model is not None else None
        return label.initials or label.title if label is not None else row_id

    def preview_text(self, preview: Preview) -> str:
        """Floating label: ``lun 26.10 06:30 → mar 27.10 12:00 · 1 j ½ · KDU``."""
        model = self.model
        if model is None:
            return ""
        calendar = preview.calendar or model.calendar_of(preview.row_id)
        who = self.row_short(preview.row_id)
        if preview.night:
            parts = [night_label(preview.start, preview.end)]
        else:
            parts = [span_label(calendar, preview.start, preview.end)]
        if preview.mode == "add":
            parts.append(f"+ {who}")
        else:
            parts.append(who)
        if preview.mode == "copy":
            parts.append(COPY_SUFFIX)
        if not preview.valid:
            parts.append(ALREADY_ASSIGNED)
        if preview.reason:
            parts.append(preview.reason)
        absence = model.absent_during(preview.row_id, preview.start, preview.end)
        if absence is not None:
            parts.append(absence.label)
        rest = model.resting_during(
            preview.row_id, preview.start, preview.end, task_id=preview.task_id
        )
        if rest is not None:
            parts.append(REST_WARNING)
        return " · ".join(parts)

    def snapped_start(self, row_id: str, x: float) -> WallTime:
        model = self.model
        if model is None:
            return 0
        calendar = model.calendar_of(row_id)
        return snap_start(calendar, model.timescale.time_at(x), model.view)

    # Keyboard ---------------------------------------------------------

    def handle_key(self, event: QKeyEvent) -> bool:
        """Grid shortcuts; True when ``event`` was used."""
        key = event.key()
        modifiers = event.modifiers()
        if key == Qt.Key.Key_Escape:
            return self._escape()
        if self.gestures.active:
            self.gestures.modifiers_changed(modifiers)
            return True
        if key == Qt.Key.Key_A and modifiers & Qt.KeyboardModifier.ControlModifier:
            self.select_visible()
            return True
        if key in DETAIL_KEYS:
            return self._detail()
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if not self.selection:
            # Without a selection an arrow reaches the bars from the keyboard.
            return key in ARROW_KEYS and self.select_first_visible()
        return not self.read_only and self._edit_key(key, shift=shift)

    def _edit_key(self, key: int, *, shift: bool) -> bool:
        if key in DELETE_KEYS:
            self.delete_requested.emit(list(self.selection), shift)
            return True
        if key in HORIZONTAL_KEYS:
            direction = -1 if key == Qt.Key.Key_Left else 1
            for bars, minutes in self.arrow_shifts(direction, resize_end=shift):
                self.shift_requested.emit(bars, minutes, shift)
            return True
        if key in VERTICAL_KEYS:
            return self._reassign(up=key == Qt.Key.Key_Up, add=shift)
        return False

    def _escape(self) -> bool:
        if self.gestures.cancel():
            return True
        if self.scene.range_item.isVisible():
            self.scene.range_item.setVisible(False)
            return True
        if self.selection:
            self.set_selection([])
            return True
        return False

    def _detail(self) -> bool:
        if self.current is None:
            return False
        self.detail_requested.emit(*self.current)
        return True

    def arrow_shifts(self, direction: int, *, resize_end: bool) -> list[tuple[list[BarKey], int]]:
        """Group the selected bars by the signed worked minutes an arrow key moves them.

        One group of 30 minutes in the day and week views. In the month view each bar
        steps to the next (or previous) half-day edge of its task's calendar, its start or
        with ``resize_end`` its end; an end already one half day from the start stays.
        """
        model = self.model
        if model is None or not self.selection:
            return []
        if model.view != "month":
            return [(list(self.selection), SNAP_MINUTES * direction)]
        groups: dict[int, list[BarKey]] = {}
        for key in self.selection:
            bar = model.bars_by_key.get(key)
            if bar is None:
                continue
            calendar = model.task_calendar(bar.task_id)
            # A night has no half days: it moves by half hours in every view.
            minutes = (
                SNAP_MINUTES * direction
                if bar.night
                else half_day_shift(
                    calendar, bar.start, bar.end, direction=direction, resize_end=resize_end
                )
            )
            if minutes:
                groups.setdefault(minutes, []).append(key)
        return [(keys, minutes) for minutes, keys in groups.items()]

    def select_visible(self) -> None:
        area = self.visible_scene_rect()
        keys = [item.key for item in self.scene.bars_in(area) if item.isVisible()]
        ordered = sorted(keys, key=self._key_order)
        self.set_selection(ordered, current=self.current)

    def select_first_visible(self) -> bool:
        """Arrow key without a selection: select the first visible bar (top row, earliest)."""
        area = self.visible_scene_rect()
        keys = [item.key for item in self.scene.bars_in(area) if item.isVisible()]
        if not keys:
            return False
        first = min(keys, key=self._key_order)
        self.set_selection([first], current=first)
        return True

    def _key_order(self, key: BarKey) -> tuple[int, int]:
        model = self.model
        bar = model.bars_by_key.get(key) if model is not None else None
        index = model.rows.index_of(key[1]) if model is not None else None
        return (index or 0, bar.start if bar is not None else 0)

    def _reassign(self, *, up: bool, add: bool) -> bool:
        model = self.model
        if model is None or self.current is None:
            return False
        task_id, row_id = self.current
        task = model.task(task_id)
        assigned = set(task.assignee_ids) if task is not None else set()
        index = {row.resource_id: number for number, row in enumerate(model.rows)}
        position = index.get(row_id, -1)
        people = [
            row.resource_id
            for row in model.rows
            if row.kind == "resource" and row.resource_id not in assigned
        ]
        if up:
            candidates = [rid for rid in reversed(people) if index[rid] < position]
        else:
            candidates = [rid for rid in people if index[rid] > position]
        if not candidates:
            return True
        target = candidates[0]
        if not add:
            self.follow((task_id, row_id), (task_id, target))
        self.reassign_requested.emit(task_id, row_id, target, add)
        return True

    # Context menu -----------------------------------------------------

    def context_menu_at(self, point: QPointF, global_pos: QPoint) -> None:
        model = self.model
        if model is None:
            return
        item = self.scene.bar_at(point)
        row = self.scene.row_at(point.y())
        if item is not None:
            if item.key not in self.selection:
                self.set_selection([item.key], current=item.key)
            start = self.snapped_start(item.bar.row_id, point.x())
            self.context_menu_requested.emit(item.bar.task_id, item.bar.row_id, start, global_pos)
            return
        block = self.scene.absence_at(point)
        if block is not None and block.absence_id:
            self.absence_menu_requested.emit(block.absence_id, global_pos)
            return
        if row is None or not _editable(row.resource_id):
            self.context_menu_requested.emit("", "", -1, global_pos)
            return
        start = self.snapped_start(row.resource_id, point.x())
        self.context_menu_requested.emit("", row.resource_id, start, global_pos)

    # Drops ------------------------------------------------------------

    def _drop_target(
        self, mime: QMimeData | None, point: QPointF, modifiers: Qt.KeyboardModifier | None
    ) -> tuple[TaskDrop, PlanningTask, Preview] | None:
        """Where a dragged card or bar lands at the scene ``point``; None when refused."""
        model = self.model
        payload = parse_task_mime(mime)
        if model is None or payload is None or self.read_only or not self.drop_enabled:
            return None
        task = model.task(payload.task_id)
        row = self.scene.row_at(point.y())
        if task is None or row is None or not _editable(row.resource_id):
            return None
        if _is_bar_drop(payload, task):
            held = QGuiApplication.keyboardModifiers() if modifiers is None else modifiers
            preview = self._bar_drop(task, payload, row.resource_id, point.x(), drag_mode(held))
        else:
            preview = self._card_drop(task, row.resource_id, point.x())
        return (payload, task, preview) if preview.valid else None

    def _card_drop(self, task: PlanningTask, row_id: str, x: float) -> Preview:
        """Place a card of « À planifier »: its estimate on the row's calendar, as scheduled."""
        model = self.model
        if model is None:
            return Preview(0, 0, row_id, valid=False)
        calendar = model.calendar_of(row_id)
        pointer = model.timescale.time_at(x)
        if task.off_hours:
            elapsed = (
                task.end - task.start if task.start is not None and task.end is not None else 0
            )
            if not model.night_axis:
                pointer = model.timescale.column_at(x).day.toordinal() * MINUTES_PER_DAY
            start, end = fitted_night(night_start_at(pointer), elapsed or task.estimate_minutes)
            return night_preview(model, start, end, row_id, mode="move", task_id=task.id)
        start = calendar.working_start(snap_start(calendar, pointer, model.view))
        end = card_end(task, start, calendar, model)
        warn = warned(model, row_id, start, end, task.id)
        return Preview(start, end, row_id, warn=warn, calendar=calendar, task_id=task.id)

    def _bar_drop(
        self, task: PlanningTask, payload: TaskDrop, target: str, x: float, mode: MoveMode
    ) -> Preview:
        """Place a bar dragged out and back: same rules as a move inside the body."""
        model = self.model
        start, end = task.start, task.end
        if model is None or start is None or end is None:
            return Preview(0, 0, target, valid=False)
        source = payload.resource_id or UNASSIGNED_ROW_ID
        others = set(task.assignee_ids) - {source}
        if mode == "add":
            valid = row_kind(target) == "resource" and target != source and target not in others
            warn = valid and warned(model, target, start, end, task.id)
            calendar = model.task_calendar(task.id)
            return Preview(
                start,
                end,
                target,
                "add",
                valid=valid,
                warn=warn,
                calendar=calendar,
                night=task.off_hours,
                task_id=task.id,
            )
        if mode == "move" and target in others:
            target = source
        if task.off_hours:
            press = model.timescale.x_of(model.display_span(start, end, night=True)[0])
            begin, finish = night_move(model, start, end, press, x)
            return night_preview(model, begin, finish, target, mode=mode, task_id=task.id)
        measure, layout = move_calendars(model, task, source, target, mode)
        pointer = model.timescale.time_at(x)
        if self._export_grab:
            pointer = layout.subtract_work(pointer, self._export_grab)
        candidate = snap_start(layout, pointer, model.view)
        begin, finish = move_preserving_work(start, end, candidate, measure, target=layout)
        warn = warned(model, target, begin, finish, task.id)
        return Preview(begin, finish, target, mode, warn=warn, calendar=layout, task_id=task.id)

    def drag_over(
        self,
        mime: QMimeData | None,
        point: QPointF,
        modifiers: Qt.KeyboardModifier | None = None,
    ) -> bool:
        """Show where the dragged card or bar would land at the scene ``point``.

        True when the drop is allowed. A bar coming back follows ``modifiers`` (the held
        keys when None) like a move inside the body: Shift adds, Ctrl or Alt copies.
        """
        target = self._drop_target(mime, point, modifiers)
        model = self.model
        if target is None or model is None:
            self.drag_left()
            return False
        payload, _task, preview = target
        start, end, row_id = preview.start, preview.end, preview.row_id
        bar = model.preview_bar(payload.task_id, row_id, start, end)
        rect = self.scene.span_rect(start, end, row_id, night=preview.night)
        if bar is not None:
            self.scene.ghost.show_bar(bar, rect, warn=preview.warn)
        self.scene.set_drop_row(row_id)
        text = self.preview_text(preview)
        self.scene.label.show_text(text, rect, self.visible_scene_rect(), warn=preview.warn)
        return True

    def drag_left(self) -> None:
        self.scene.hide_previews()

    def drop_at(
        self,
        mime: QMimeData | None,
        point: QPointF,
        modifiers: Qt.KeyboardModifier | None = None,
    ) -> bool:
        """Drop a card (or a bar coming back) at the scene ``point``; True when used.

        A card gives ``card_dropped``. A bar coming back gives ``move_requested`` in the
        mode of ``modifiers`` (the held keys when None): « add » keeps the times and
        refuses someone already on the task, a move onto a sibling's row keeps the row,
        and a bar dropped where it was changes nothing.
        """
        target = self._drop_target(mime, point, modifiers)
        self.drag_left()
        if target is None:
            return False
        payload, task, preview = target
        if not _is_bar_drop(payload, task):
            self.card_dropped.emit(payload.task_id, preview.row_id, preview.start)
            return True
        self._dropped_inside = True
        source = payload.resource_id or UNASSIGNED_ROW_ID
        if preview.mode != "add" and preview.row_id == source and preview.start == task.start:
            return True
        if preview.mode == "move" and preview.row_id != source:
            self.follow((task.id, source), (task.id, preview.row_id))
        self.move_requested.emit(task.id, source, preview.row_id, preview.start, preview.mode)
        return True

    def export_bar(self, bar: BarData, *, grab: int) -> None:
        """Drag ``bar`` out of the grid as a ``QDrag`` (towards « À planifier »).

        ``bar_drag_exported`` follows when another widget accepted the drop; a drop back
        on the grid is a move, a copy or an added person (``move_requested``).
        """
        resource_id = "" if bar.row_id == UNASSIGNED_ROW_ID else bar.row_id
        mime = task_mime_data(bar.task_id, source=GRID_SOURCE, resource_id=resource_id)
        self._export_grab = grab
        self._dropped_inside = False
        try:
            result = self.exec_drag(mime, self._drag_pixmap(bar))
        finally:
            self._export_grab = 0
            self.drag_left()
        if result != Qt.DropAction.IgnoreAction and not self._dropped_inside:
            self.bar_drag_exported.emit(bar.task_id)
        self.drag_finished.emit(bar.task_id)

    def exec_drag(self, mime: QMimeData, pixmap: QPixmap) -> Qt.DropAction:
        """Run the ``QDrag`` of an exported bar (replaced in tests)."""
        drag = QDrag(self.view.viewport())
        drag.setMimeData(mime)
        if not pixmap.isNull():
            drag.setPixmap(pixmap)
            drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        return drag.exec(
            Qt.DropAction.MoveAction | Qt.DropAction.CopyAction, Qt.DropAction.MoveAction
        )

    def _drag_pixmap(self, bar: BarData) -> QPixmap:
        rect = self.scene.rect_of(bar)
        width = max(round(min(rect.width(), self.viewport_width() / 2)), 1)
        height = max(round(rect.height()), 1)
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap(round(width * ratio), round(height * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setOpacity(EXPORT_OPACITY)
        paint_bar(
            painter,
            bar,
            QRectF(0, 0, width, height),
            self.scene.bar_style,
            BarState(lifted=True, day_view=self.scene.day_view),
        )
        painter.end()
        return pixmap

    # Scrolling --------------------------------------------------------

    def _sync_scroll(self) -> None:
        origin = self.view.mapToScene(0, 0)
        x0, y0 = max(origin.x(), 0.0), max(origin.y(), 0.0)
        self.header.set_viewport(x0, self.viewport_width())
        self.rows.set_offset(y0)
        self.scene.set_pin_offset(y0)
        self.scene.set_sticky_left(x0)
        self._emit_visible_range()

    def _emit_visible_range(self) -> None:
        model = self.model
        if model is None:
            return
        area = self.visible_scene_rect()
        columns = model.timescale.visible_columns(area.left(), area.right())
        if not columns:
            return
        visible = (columns[0].day, columns[-1].day)
        if visible != self._visible_range:
            self._visible_range = visible
            self.visible_range_changed.emit(*visible)

    def _scroll_rows(self, delta: int) -> None:
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.value() + delta)

    def viewport_resized(self) -> None:
        # A model waiting for the end of a gesture is newer than the one shown.
        model = self._deferred or self.model
        if model is not None and abs(model.viewport_width - self.viewport_width()) > FIT_TOLERANCE:
            self.set_model(model)
        self._sync_scroll()

    def autoscroll(self, view_pos: QPoint) -> None:
        """Scroll while a drag runs near an edge of the body."""
        area = self.view.viewport().rect()
        dx = dy = 0
        if view_pos.x() < area.left() + AUTOSCROLL_ZONE:
            dx = -AUTOSCROLL_STEP
        elif view_pos.x() > area.right() - AUTOSCROLL_ZONE:
            dx = AUTOSCROLL_STEP
        if view_pos.y() < area.top() + AUTOSCROLL_ZONE:
            dy = -AUTOSCROLL_STEP
        elif view_pos.y() > area.bottom() - AUTOSCROLL_ZONE:
            dy = AUTOSCROLL_STEP
        self._scroll_delta = QPoint(dx, dy)
        if dx or dy:
            if not self._autoscroll.isActive():
                self._autoscroll.start()
        else:
            self._autoscroll.stop()

    def stop_autoscroll(self) -> None:
        self._autoscroll.stop()

    def _autoscroll_tick(self) -> None:
        if not self.gestures.active:
            self._autoscroll.stop()
            return
        horizontal = self.view.horizontalScrollBar()
        vertical = self.view.verticalScrollBar()
        before = (horizontal.value(), vertical.value())
        horizontal.setValue(horizontal.value() + self._scroll_delta.x())
        vertical.setValue(vertical.value() + self._scroll_delta.y())
        if (horizontal.value(), vertical.value()) == before:
            return
        view_pos = self.gestures.last_view
        self.gestures.update(self.view.mapToScene(view_pos), self.gestures.last_modifiers)

    def _on_theme_changed(self) -> None:
        self.scene.restyle()
        self.view.resetCachedContent()
        for widget in (self.header, self.rows, self.corner):
            widget.update()


def warned(model: GridModel, row_id: str, start: WallTime, end: WallTime, task_id: str) -> bool:
    """Whether a span on ``row_id`` lies on an absence or on the rest around another night."""
    return (
        model.absent_during(row_id, start, end) is not None
        or model.resting_during(row_id, start, end, task_id=task_id) is not None
    )


def night_move(
    model: GridModel, start: WallTime, end: WallTime, press_x: float, x: float
) -> tuple[WallTime, WallTime]:
    """Night moved by the pointer from ``press_x`` to ``x``, same clock times and length.

    On the 24-hour axis it follows the pointer by half hours; over day columns it changes
    evening only, column by column.
    """
    scale = model.timescale
    if model.night_axis:
        begin = snap(scale.time_at(scale.x_of(start) + x - press_x))
        return begin, begin + (end - start)
    evening = night_day(start)
    column = scale.column_of(evening)
    here = scale.column_at(x)
    if column is None:
        target = here.day
    else:
        index = column.index + here.index - scale.column_at(press_x).index
        target = scale.columns[min(max(index, 0), len(scale.columns) - 1)].day
    begin = start + (target - evening).days * MINUTES_PER_DAY
    return begin, begin + (end - start)


def night_preview(
    model: GridModel,
    start: WallTime,
    end: WallTime,
    row_id: str,
    *,
    mode: MoveMode,
    task_id: str,
) -> Preview:
    """Preview of a night; a place the command refuses says why, in red, until the drop."""
    reason = night_error(start, end) or ""
    warn = bool(reason) or warned(model, row_id, start, end, task_id)
    return Preview(start, end, row_id, mode, warn=warn, night=True, reason=reason, task_id=task_id)


def card_end(
    task: PlanningTask, start: WallTime, calendar: WorkCalendar, model: GridModel
) -> WallTime:
    """End of a card placed at ``start``: same rule as ``commands.schedule_task``.

    A task already on the grid keeps its worked minutes; a card lasts its working days
    (``estimate_days``), else its worked minutes (one worked day without estimate).
    """
    if task.start is not None and task.end is not None:
        source = model.calendar_for(next(iter(task.assignments), None))
        minutes = max(source.work_between(task.start, task.end), MIN_TASK_MINUTES)
        return calendar.add_work(start, _rounded(minutes))
    if task.estimate_days > 0:
        end = calendar.add_days(start, task.estimate_days)
        return max(end, calendar.add_work(start, MIN_TASK_MINUTES))
    minutes = task.estimate_minutes or DEFAULT_TASK_MINUTES
    return calendar.add_work(start, _rounded(minutes))


def _rounded(minutes: int) -> int:
    return max(MIN_TASK_MINUTES, -(-minutes // SNAP_MINUTES) * SNAP_MINUTES)


__all__ = [
    "MOVE_MODES",
    "TASK_MIME",
    "Gesture",
    "GridGestures",
    "MoveMode",
    "PlanningGrid",
    "PlanningView",
    "Preview",
    "TaskDrop",
    "bars_by_task",
    "card_end",
    "drag_mode",
    "half_day_shift",
    "move_calendars",
    "parse_task_mime",
    "shortest_end",
    "shortest_start",
    "task_mime_data",
]
