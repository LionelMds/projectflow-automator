from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFocusEvent, QImage, QMouseEvent, QPainter, QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import QApplication

from projectflow.core.planning import commands
from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.commands import CommandContext, calendar_resolver
from projectflow.core.planning.layout import UNASSIGNED_ROW_ID, Density, team_row_id
from projectflow.core.planning.models import (
    Absence,
    Author,
    PlanningMeta,
    PlanningTask,
    ProjectCache,
    Resource,
)
from projectflow.core.planning.walltime import wall_time
from projectflow.ui.planning import printing
from projectflow.ui.planning.grid import (
    TASK_MIME,
    Gesture,
    PlanningGrid,
    bars_by_task,
    drag_mode,
    parse_task_mime,
    task_mime_data,
)
from projectflow.ui.planning.grid_items import (
    CUT_FADE,
    BarState,
    bar_rect,
    build_bar_style,
    layout_bar_text,
    paint_bar,
)
from projectflow.ui.planning.grid_model import GridModel, RowLabel, build_grid_model
from projectflow.ui.planning.header import HEADER_HEIGHT, half_day_labels
from projectflow.ui.planning.printing import export_pdf, page_bands, paper_theme, print_title
from projectflow.ui.planning.rows import ROW_COLUMN_WIDTH, RowFonts, label_room, resource_texts
from projectflow.ui.theme import load_fonts

STAMP = "2026-10-01T08:00:00Z"
TODAY = date(2026, 10, 22)
MONDAY = date(2026, 10, 26)
THURSDAY = MONDAY + timedelta(days=3)


def at(day: date, hour: int, minute: int = 0) -> int:
    return wall_time(day, hour, minute)


def resource(rid: str, name: str, team: str, order: int, **extra: object) -> Resource:
    return Resource.model_validate(
        {
            "id": rid,
            "name": name,
            "initials": rid.upper(),
            "team": team,
            "order": order,
            "created_at": STAMP,
            "updated_at": STAMP,
            **extra,
        }
    )


def task(
    tid: str, start: int | None, end: int | None, people: list[str], **extra: Any
) -> PlanningTask:
    return PlanningTask.model_validate(
        {
            "id": tid,
            "project_number": "2026-5231",
            "title": f"Tâche {tid}",
            "project_cache": ProjectCache(client="Müller SA").model_dump(),
            "start": start,
            "end": end,
            "assignments": {person: {} for person in people},
            "created_at": STAMP,
            "updated_at": STAMP,
            **extra,
        }
    )


def snapshot() -> PlanningSnapshot:
    resources = [
        resource("mr", "Marc Rey", "atelier", 1000),
        resource("jd", "Julien Dubois", "atelier", 2000),
        resource("lb", "Luca Bianchi", "montage", 1000),
        resource("nf", "Nicolas Favre", "montage", 2000),
    ]
    tasks = [
        task("t1", at(MONDAY, 6, 30), at(MONDAY, 12), ["mr"]),
        task("t2", at(MONDAY + timedelta(1), 6, 30), at(MONDAY + timedelta(1), 16, 30), ["jd"]),
        task("pair", at(MONDAY + timedelta(2), 6, 30), at(MONDAY + timedelta(2), 12), ["lb", "nf"]),
        task("un", at(MONDAY + timedelta(2), 13), at(MONDAY + timedelta(2), 16, 30), []),
        task("card", None, None, [], estimate_days=2),
    ]
    vacation = Absence.model_validate(
        {
            "id": "vac",
            "resource_id": "lb",
            "reason": "vacances",
            "first_day": MONDAY.isoformat(),
            "last_day": MONDAY.isoformat(),
            "created_at": STAMP,
            "updated_at": STAMP,
        }
    )
    return PlanningSnapshot(
        tasks={item.id: item for item in tasks},
        resources={item.id: item for item in resources},
        absences={vacation.id: vacation},
        meta=PlanningMeta(),
    )


def command_context(snap: PlanningSnapshot) -> CommandContext:
    author = Author.model_validate({"initials": "LM", "name": "Lionel", "poste": "BUREAU"})
    return CommandContext(
        snapshot=snap, author=author, now=STAMP, calendar_for=calendar_resolver(snap)
    )


def with_tasks(snap: PlanningSnapshot, *tasks: PlanningTask) -> PlanningSnapshot:
    return PlanningSnapshot(
        tasks={**snap.tasks, **{item.id: item for item in tasks}},
        resources=snap.resources,
        absences=snap.absences,
        meta=snap.meta,
    )


def make_model(grid: PlanningGrid, **options: Any) -> GridModel:
    params: dict[str, Any] = {
        "view": "week",
        "anchor": MONDAY,
        "viewport_width": grid.viewport_width(),
        "today": TODAY,
        "now": at(TODAY, 10),
        "density": Density.NORMAL,
    }
    params.update(options)
    snap = params.pop("snapshot", None) or snapshot()
    return build_grid_model(snap, **params)


@pytest.fixture
def grid(qtbot: Any) -> PlanningGrid:
    widget = PlanningGrid()
    qtbot.addWidget(widget)
    widget.resize(1200, 640)
    widget.show()
    qtbot.waitExposed(widget)
    widget.set_model(make_model(widget))
    return widget


class Recorder:
    def __init__(self, *signals: Any) -> None:
        self.calls: list[tuple[Any, ...]] = []
        for signal in signals:
            signal.connect(lambda *args: self.calls.append(args))


def bar_pos(grid: PlanningGrid, task_id: str, row_id: str, fraction: float = 0.5) -> QPoint:
    item = grid.scene.items_by_key[(task_id, row_id)]
    rect = item.scene_rect()
    point = QPointF(rect.left() + rect.width() * fraction, rect.center().y())
    return grid.view.mapFromScene(point)


def cell_pos(grid: PlanningGrid, row_id: str, value: int) -> QPoint:
    model = grid.model
    assert model is not None
    row = model.rows.get(row_id)
    assert row is not None
    point = QPointF(model.timescale.x_of(value), row.top + row.height / 2)
    return grid.view.mapFromScene(point)


def minutes_px(grid: PlanningGrid, minutes: int) -> int:
    assert grid.model is not None
    return round(minutes * grid.model.timescale.minute_width)


def drag(
    qtbot: Any,
    grid: PlanningGrid,
    start: QPoint,
    end: QPoint,
    modifier: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> None:
    viewport = grid.view.viewport()
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, modifier, start)
    middle = QPoint((start.x() + end.x()) // 2, (start.y() + end.y()) // 2)
    qtbot.mouseMove(viewport, middle)
    qtbot.mouseMove(viewport, end)
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, modifier, end)


# ------------------------------------------------------------- gestures ----


def test_move_snaps_to_the_half_hour(qtbot: Any, grid: PlanningGrid) -> None:
    moves = Recorder(grid.move_requested)
    started = Recorder(grid.drag_started, grid.drag_finished)
    start = bar_pos(grid, "t1", "mr")
    drag(qtbot, grid, start, start + QPoint(minutes_px(grid, 70), 0))
    assert moves.calls == [("t1", "mr", "mr", at(MONDAY, 7, 30), "move")]
    assert started.calls == [("t1",), ("t1",)]
    assert grid.gestures.state is Gesture.IDLE


def test_vertical_drag_replaces_the_person(qtbot: Any, grid: PlanningGrid) -> None:
    moves = Recorder(grid.move_requested)
    start = bar_pos(grid, "t1", "mr")
    target = cell_pos(grid, "jd", at(MONDAY, 9))
    drag(qtbot, grid, start, QPoint(start.x(), target.y()))
    assert moves.calls == [("t1", "mr", "jd", at(MONDAY, 6, 30), "move")]


def test_shift_drag_adds_the_person_without_moving(qtbot: Any, grid: PlanningGrid) -> None:
    moves = Recorder(grid.move_requested)
    start = bar_pos(grid, "t1", "mr")
    target = cell_pos(grid, "jd", at(MONDAY + timedelta(1), 9))
    drag(qtbot, grid, start, target, Qt.KeyboardModifier.ShiftModifier)
    assert moves.calls == [("t1", "mr", "jd", at(MONDAY, 6, 30), "add")]


def test_ctrl_drag_copies(qtbot: Any, grid: PlanningGrid) -> None:
    moves = Recorder(grid.move_requested)
    start = bar_pos(grid, "t1", "mr", 0.2)
    end = start + QPoint(round(grid.model.timescale.day_width * 3), 0)  # type: ignore[union-attr]
    drag(qtbot, grid, start, end, Qt.KeyboardModifier.ControlModifier)
    assert moves.calls == [("t1", "mr", "mr", at(THURSDAY, 6, 30), "copy")]
    item = grid.scene.items_by_key[("t1", "mr")]
    assert item.scene_rect() == grid.scene.rect_of(item.bar)


def test_ctrl_click_with_a_wobble_toggles_without_copying(qtbot: Any, grid: PlanningGrid) -> None:
    moves = Recorder(grid.move_requested)
    clicks = Recorder(grid.bar_clicked)
    viewport = grid.view.viewport()
    ctrl = Qt.KeyboardModifier.ControlModifier
    start = bar_pos(grid, "t1", "mr")
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, ctrl, start)
    qtbot.mouseMove(viewport, start + QPoint(5, 2))
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, ctrl, start + QPoint(5, 2))
    assert moves.calls == []
    assert grid.selected_bars() == [("t1", "mr")]
    assert clicks.calls[0][:2] == ("t1", "mr")
    plain = bar_pos(grid, "t2", "jd")
    no_key = Qt.KeyboardModifier.NoModifier
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, no_key, plain)
    qtbot.mouseMove(viewport, plain + QPoint(4, 3))
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, no_key, plain + QPoint(4, 3))
    assert moves.calls == []
    assert clicks.calls[-1][:2] == ("t2", "jd")


def test_ctrl_drag_back_to_its_place_copies_nothing(qtbot: Any, grid: PlanningGrid) -> None:
    moves = Recorder(grid.move_requested)
    viewport = grid.view.viewport()
    ctrl = Qt.KeyboardModifier.ControlModifier
    start = bar_pos(grid, "t1", "mr")
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, ctrl, start)
    qtbot.mouseMove(viewport, start + QPoint(80, 0))
    assert grid.gestures.state is Gesture.MOVE
    qtbot.mouseMove(viewport, start)
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, ctrl, start)
    assert moves.calls == []


def test_mixed_canton_preview_matches_the_saved_move(qtbot: Any, grid: PlanningGrid) -> None:
    people = [
        resource("ge", "Genève Un", "atelier", 1000),
        resource("vd", "Vaud Deux", "atelier", 2000, canton="VD"),
    ]
    friday = date(2026, 9, 11)
    shared = task("m", at(friday, 6, 30), at(friday + timedelta(3), 16, 30), ["ge", "vd"])
    snap = PlanningSnapshot(
        tasks={shared.id: shared}, resources={item.id: item for item in people}, meta=PlanningMeta()
    )
    model = make_model(grid, snapshot=snap, anchor=date(2026, 9, 14))
    grid.set_model(model)
    moves = Recorder(grid.move_requested)
    viewport = grid.view.viewport()
    rect = grid.scene.items_by_key[("m", "vd")].scene_rect()
    start = grid.view.mapFromScene(QPointF(rect.left() + 10, rect.center().y()))
    target_x = model.timescale.x_of(at(date(2026, 9, 18), 6, 30)) + 10
    target = grid.view.mapFromScene(QPointF(target_x, rect.center().y()))
    no_key = Qt.KeyboardModifier.NoModifier
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, no_key, start)
    qtbot.mouseMove(viewport, start + QPoint(20, 0))
    qtbot.mouseMove(viewport, target)
    drag_state = grid.gestures.bar_drag
    assert drag_state is not None
    preview = drag_state.preview
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, no_key, target)
    ((task_id, from_row, to_row, new_start, mode),) = moves.calls
    assert (from_row, to_row, mode) == ("vd", "vd", "move")
    changes = commands.move_task(
        command_context(snap),
        task_id,
        new_start=new_start,
        from_resource=from_row,
        to_resource=to_row,
    )
    saved = changes[0].after
    assert isinstance(saved, PlanningTask)
    assert (saved.start, saved.end) == (preview.start, preview.end)
    # Laid out on the Geneva calendar of the first assignee: Lundi du Jeûne (VD) is worked.
    calendars = calendar_resolver(snap)
    assert preview.end == calendars("ge").add_work(preview.start, 870)
    assert preview.end != calendars("vd").add_work(preview.start, 870)


def test_resize_keeps_at_least_thirty_minutes(qtbot: Any, grid: PlanningGrid) -> None:
    resizes = Recorder(grid.resize_requested)
    item = grid.scene.items_by_key[("t1", "mr")]
    rect = item.scene_rect()
    edge = grid.view.mapFromScene(QPointF(rect.right() - 2, rect.center().y()))
    drag(qtbot, grid, edge, edge - QPoint(round(rect.width()) + 40, 0))
    assert resizes.calls == [("t1", "mr", None, at(MONDAY, 7))]


def test_resize_start_snaps(qtbot: Any, grid: PlanningGrid) -> None:
    resizes = Recorder(grid.resize_requested)
    item = grid.scene.items_by_key[("t2", "jd")]
    rect = item.scene_rect()
    edge = grid.view.mapFromScene(QPointF(rect.left() + 2, rect.center().y()))
    drag(qtbot, grid, edge, edge + QPoint(minutes_px(grid, 95), 0))
    assert resizes.calls == [("t2", "jd", at(MONDAY + timedelta(1), 8), None)]


def test_escape_cancels_a_drag(qtbot: Any, grid: PlanningGrid) -> None:
    moves = Recorder(grid.move_requested, grid.resize_requested)
    item = grid.scene.items_by_key[("t1", "mr")]
    before = item.scene_rect()
    viewport = grid.view.viewport()
    start = bar_pos(grid, "t1", "mr")
    end = start + QPoint(minutes_px(grid, 300), 30)
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    qtbot.mouseMove(viewport, end)
    assert grid.gestures.state is Gesture.MOVE
    assert item.scene_rect() != before
    qtbot.keyClick(grid.view, Qt.Key.Key_Escape)
    assert item.scene_rect() == before
    qtbot.mouseMove(viewport, end + QPoint(20, 0))
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    assert moves.calls == []
    assert item.scene_rect() == before


def test_drop_on_an_absent_person_warns_but_is_allowed(qtbot: Any, grid: PlanningGrid) -> None:
    moves = Recorder(grid.move_requested)
    viewport = grid.view.viewport()
    start = bar_pos(grid, "t1", "mr")
    target = cell_pos(grid, "lb", at(MONDAY, 9))
    end = QPoint(start.x(), target.y())
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    qtbot.mouseMove(viewport, end)
    item = grid.scene.items_by_key[("t1", "mr")]
    assert item.warn
    assert grid.scene.label.warn
    assert "Vacances" in grid.scene.label.text
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    assert moves.calls == [("t1", "mr", "lb", at(MONDAY, 6, 30), "move")]


def test_moving_onto_a_sibling_row_keeps_the_row(qtbot: Any, grid: PlanningGrid) -> None:
    moves = Recorder(grid.move_requested)
    start = bar_pos(grid, "pair", "lb")
    target = cell_pos(grid, "nf", at(MONDAY + timedelta(2), 9))
    drag(qtbot, grid, start, QPoint(start.x() + minutes_px(grid, 60), target.y()))
    assert moves.calls == [("pair", "lb", "lb", at(MONDAY + timedelta(2), 7, 30), "move")]


def test_range_selection_over_rows_and_time(qtbot: Any, grid: PlanningGrid) -> None:
    ranges = Recorder(grid.range_selected)
    wednesday = MONDAY + timedelta(2)
    start = cell_pos(grid, "mr", at(wednesday, 8, 5))
    end = cell_pos(grid, "jd", at(THURSDAY, 10, 50))
    drag(qtbot, grid, start, end)
    assert ranges.calls == [(["mr", "jd"], at(wednesday, 8), at(THURSDAY, 11))]
    assert grid.scene.range_item.isVisible()
    qtbot.keyClick(grid.view, Qt.Key.Key_Escape)
    assert not grid.scene.range_item.isVisible()


def test_month_range_keeps_half_days_and_resize_takes_half_hours(
    qtbot: Any, grid: PlanningGrid
) -> None:
    ranges = Recorder(grid.range_selected)
    resizes = Recorder(grid.resize_requested)
    grid.set_model(make_model(grid, view="month", density=Density.COMPACT))
    start = cell_pos(grid, "jd", at(THURSDAY, 8))
    drag(qtbot, grid, start, start + QPoint(5, 0))
    assert ranges.calls == [(["jd"], at(THURSDAY, 6, 30), at(THURSDAY, 12))]
    grid.clear_range()
    tuesday = MONDAY + timedelta(days=1)
    rect = grid.scene.items_by_key[("t2", "jd")].scene_rect()
    edge = grid.view.mapFromScene(QPointF(rect.right() - 1, rect.center().y()))
    drag(qtbot, grid, edge, edge - QPoint(round(rect.width()) + 20, 0))
    edge = grid.view.mapFromScene(QPointF(rect.left() + 1, rect.center().y()))
    drag(qtbot, grid, edge, edge + QPoint(round(rect.width()) + 20, 0))
    # Pushed past the other edge, a bar keeps its shortest length: half an hour.
    assert resizes.calls == [
        ("t2", "jd", None, at(tuesday, 7)),
        ("t2", "jd", at(tuesday, 16), None),
    ]
    # Even in the month, a length is set to the half hour, not to the half day.
    grid.set_model(make_model(grid, view="month", density=Density.COMPACT, zoom=4.0))
    model = grid.model
    assert model is not None
    rect = grid.scene.items_by_key[("t2", "jd")].scene_rect()
    grid.view.ensureVisible(rect)
    edge = grid.view.mapFromScene(QPointF(rect.right() - 1, rect.center().y()))
    target = grid.view.mapFromScene(
        QPointF(model.timescale.x_of(at(tuesday, 14, 5)), rect.center().y())
    )
    drag(qtbot, grid, edge, QPoint(target.x(), edge.y()))
    assert resizes.calls[-1] == ("t2", "jd", None, at(tuesday, 14))


def test_shift_drag_in_empty_cells_is_a_lasso(qtbot: Any, grid: PlanningGrid) -> None:
    ranges = Recorder(grid.range_selected)
    start = cell_pos(grid, "mr", at(MONDAY, 13))
    end = cell_pos(grid, "jd", at(MONDAY + timedelta(1), 14))
    drag(qtbot, grid, start, end, Qt.KeyboardModifier.ShiftModifier)
    assert grid.selected_bars() == [("t2", "jd")]
    assert ranges.calls == []
    assert not grid.scene.range_item.isVisible()


def test_double_click_on_an_empty_cell(qtbot: Any, grid: PlanningGrid) -> None:
    cells = Recorder(grid.cell_activated)
    position = cell_pos(grid, "jd", at(THURSDAY, 9, 10))
    qtbot.mouseDClick(
        grid.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, position
    )
    assert cells.calls == [("jd", at(THURSDAY, 9))]


def test_click_selects_and_double_click_opens(qtbot: Any, grid: PlanningGrid) -> None:
    clicks = Recorder(grid.bar_clicked)
    doubles = Recorder(grid.bar_double_clicked)
    changes = Recorder(grid.selection_changed)
    viewport = grid.view.viewport()
    position = bar_pos(grid, "pair", "lb")
    qtbot.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, position)
    assert grid.selected_bars() == [("pair", "lb")]
    assert clicks.calls[0][:2] == ("pair", "lb")
    assert changes.calls == [([("pair", "lb")],)]
    sibling = grid.scene.items_by_key[("pair", "nf")]
    assert sibling.sibling
    other = bar_pos(grid, "t1", "mr")
    qtbot.mouseClick(
        viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, other
    )
    assert grid.selected_bars() == [("pair", "lb"), ("t1", "mr")]
    qtbot.mouseDClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, other)
    assert doubles.calls == [("t1", "mr")]
    empty = cell_pos(grid, "mr", at(THURSDAY, 9))
    qtbot.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, empty)
    assert grid.selected_bars() == []


# ------------------------------------------------------------- keyboard ----


def test_keyboard_intents(qtbot: Any, grid: PlanningGrid) -> None:
    shifts = Recorder(grid.shift_requested)
    reassigns = Recorder(grid.reassign_requested)
    deletes = Recorder(grid.delete_requested)
    details = Recorder(grid.detail_requested)
    view = grid.view
    grid.select([("t1", "mr")])
    qtbot.keyClick(view, Qt.Key.Key_Right)
    qtbot.keyClick(view, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    assert shifts.calls == [([("t1", "mr")], 30, False), ([("t1", "mr")], -30, True)]
    qtbot.keyClick(view, Qt.Key.Key_Down)
    qtbot.keyClick(view, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
    assert reassigns.calls == [("t1", "mr", "jd", False), ("t1", "mr", "jd", True)]
    qtbot.keyClick(view, Qt.Key.Key_Delete)
    qtbot.keyClick(view, Qt.Key.Key_Delete, Qt.KeyboardModifier.ShiftModifier)
    assert deletes.calls == [([("t1", "mr")], False), ([("t1", "mr")], True)]
    qtbot.keyClick(view, Qt.Key.Key_Return)
    qtbot.keyClick(view, Qt.Key.Key_F2)
    assert details.calls == [("t1", "mr"), ("t1", "mr")]
    qtbot.keyClick(view, Qt.Key.Key_Escape)
    assert grid.selected_bars() == []
    qtbot.keyClick(view, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert set(grid.selected_bars()) == set(grid.model.bars_by_key)  # type: ignore[union-attr]


def shifted(snap: PlanningSnapshot, task_id: str, minutes: int) -> tuple[int, int]:
    after = commands.shift_tasks(command_context(snap), [task_id], minutes=minutes)[0].after
    assert isinstance(after, PlanningTask)
    assert after.start is not None
    assert after.end is not None
    return after.start, after.end


def test_month_arrows_step_to_the_half_day_edges(qtbot: Any, grid: PlanningGrid) -> None:
    shifts = Recorder(grid.shift_requested)
    grid.set_model(make_model(grid, view="month", density=Density.COMPACT))
    grid.select([("t1", "mr")])
    qtbot.keyClick(grid.view, Qt.Key.Key_Right)
    qtbot.keyClick(grid.view, Qt.Key.Key_Left)
    assert shifts.calls == [([("t1", "mr")], 330, False), ([("t1", "mr")], -330, False)]
    snap = snapshot()
    # The bar keeps its 330 worked minutes: from 13:00 it ends on Tuesday morning.
    tuesday = MONDAY + timedelta(days=1)
    assert shifted(snap, "t1", 330) == (at(MONDAY, 13), at(tuesday, 8, 30))
    friday = MONDAY - timedelta(days=3)
    assert shifted(snap, "t1", -330) == (at(friday, 6, 30), at(friday, 12))
    # From the afternoon, the next half day is the following morning (210 minutes).
    grid.select([("un", UNASSIGNED_ROW_ID)])
    qtbot.keyClick(grid.view, Qt.Key.Key_Right)
    assert shifts.calls[-1] == ([("un", UNASSIGNED_ROW_ID)], 210, False)
    assert shifted(snap, "un", 210) == (at(THURSDAY, 6, 30), at(THURSDAY, 10))


def test_month_shift_arrows_move_the_end_by_half_hours(qtbot: Any, grid: PlanningGrid) -> None:
    shifts = Recorder(grid.shift_requested)
    grid.set_model(make_model(grid, view="month", density=Density.COMPACT))
    grid.select([("t1", "mr")])
    shift = Qt.KeyboardModifier.ShiftModifier
    qtbot.keyClick(grid.view, Qt.Key.Key_Right, shift)
    assert shifts.calls == [([("t1", "mr")], 30, True)]
    snap = snapshot()
    calendar = calendar_resolver(snap)("mr")
    end = calendar.add_work(at(MONDAY, 12), 30)
    after = commands.resize_task(command_context(snap), "t1", end=end)[0].after
    assert isinstance(after, PlanningTask)
    assert after.end == at(MONDAY, 13, 30)
    qtbot.keyClick(grid.view, Qt.Key.Key_Left, shift)
    assert shifts.calls[-1] == ([("t1", "mr")], -30, True)


def test_month_arrows_group_bars_needing_another_step(qtbot: Any, grid: PlanningGrid) -> None:
    shifts = Recorder(grid.shift_requested)
    grid.set_model(make_model(grid, view="month", density=Density.COMPACT))
    grid.select([("t1", "mr"), ("un", UNASSIGNED_ROW_ID), ("t2", "jd")])
    qtbot.keyClick(grid.view, Qt.Key.Key_Right)
    assert shifts.calls == [
        ([("t1", "mr"), ("t2", "jd")], 330, False),
        ([("un", UNASSIGNED_ROW_ID)], 210, False),
    ]


def test_up_from_the_first_person_skips_headers(qtbot: Any, grid: PlanningGrid) -> None:
    reassigns = Recorder(grid.reassign_requested)
    grid.select([("pair", "lb")])
    qtbot.keyClick(grid.view, Qt.Key.Key_Up)
    assert reassigns.calls == [("pair", "lb", "jd", False)]


def test_delete_on_non_affecte_names_its_row_for_unschedule(qtbot: Any, grid: PlanningGrid) -> None:
    deletes = Recorder(grid.delete_requested)
    grid.select([("un", UNASSIGNED_ROW_ID), ("pair", "lb"), ("pair", "nf")])
    qtbot.keyClick(grid.view, Qt.Key.Key_Delete)
    ((bars, permanent),) = deletes.calls
    assert not permanent
    assert bars_by_task(bars) == {"un": (UNASSIGNED_ROW_ID,), "pair": ("lb", "nf")}
    snap = snapshot()
    context = command_context(snap)
    # Nobody to remove on « Non affecté »: the task goes back to « À planifier ».
    assert commands.unassign(context, "un", UNASSIGNED_ROW_ID) == ()
    unscheduled = commands.unschedule_task(context, "un")[0].after
    assert isinstance(unscheduled, PlanningTask)
    assert unscheduled.start is None


def test_keyboard_reaches_the_bars_and_shows_the_focus(qtbot: Any, grid: PlanningGrid) -> None:
    view = grid.view
    assert view.accessibleName()
    assert grid.header.accessibleName()
    assert grid.rows.accessibleName()
    for widget in (grid.header, grid.rows, grid.corner):
        assert widget.focusProxy() is view
        assert widget.focusPolicy() == Qt.FocusPolicy.ClickFocus
    QApplication.sendEvent(view, QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.TabFocusReason))
    assert grid.rows.focused
    assert grid.corner.focused
    assert grid.scene.focused
    qtbot.keyClick(view, Qt.Key.Key_Right)
    assert grid.selected_bars() == [("un", UNASSIGNED_ROW_ID)]
    grid.select([("t1", "mr"), ("t2", "jd")])
    current = grid.scene.items_by_key[("t2", "jd")]
    other = grid.scene.items_by_key[("t1", "mr")]
    assert current.state().focus
    assert not other.state().focus
    QApplication.sendEvent(view, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.TabFocusReason))
    assert not current.state().focus
    assert not grid.rows.focused


# --------------------------------------------------------------- model ----


def test_set_model_keeps_selection_and_follows_a_reassigned_bar(
    qtbot: Any, grid: PlanningGrid
) -> None:
    grid.select([("t1", "mr")])
    position = grid.view.verticalScrollBar().value()
    qtbot.keyClick(grid.view, Qt.Key.Key_Down)
    snap = snapshot()
    moved = snap.tasks["t1"].model_copy(update={"assignments": {"jd": {}}})
    snap = snap.apply([])
    snap = PlanningSnapshot(
        tasks={**snap.tasks, "t1": moved},
        resources=snap.resources,
        absences=snap.absences,
        meta=snap.meta,
    )
    grid.set_model(make_model(grid, snapshot=snap))
    assert grid.selected_bars() == [("t1", "jd")]
    assert grid.view.verticalScrollBar().value() == position


def test_a_model_arriving_during_a_drag_waits(qtbot: Any, grid: PlanningGrid) -> None:
    viewport = grid.view.viewport()
    start = bar_pos(grid, "t1", "mr")
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    qtbot.mouseMove(viewport, start + QPoint(40, 0))
    serial = grid.serial
    grid.set_model(make_model(grid))
    assert grid.serial == serial
    qtbot.mouseRelease(
        viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(40, 0)
    )
    assert grid.serial > serial


def test_a_model_arriving_between_press_and_drag_waits(qtbot: Any, grid: PlanningGrid) -> None:
    ranges = Recorder(grid.range_selected)
    viewport = grid.view.viewport()
    start = cell_pos(grid, "jd", at(THURSDAY, 9))
    no_key = Qt.KeyboardModifier.NoModifier
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, no_key, start)
    snap = snapshot()
    archived = snap.resources["jd"].model_copy(update={"active": False})
    snap = PlanningSnapshot(
        tasks={key: value for key, value in snap.tasks.items() if key != "t2"},
        resources={**snap.resources, "jd": archived},
        absences=snap.absences,
        meta=snap.meta,
    )
    serial = grid.serial
    grid.set_model(make_model(grid, snapshot=snap))
    assert grid.serial == serial
    qtbot.mouseMove(viewport, start + QPoint(60, 0))
    assert grid.gestures.state is Gesture.RANGE
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, no_key, start + QPoint(60, 0))
    assert ranges.calls[0][0] == ["jd"]
    assert grid.serial > serial
    assert grid.model is not None
    assert grid.model.rows.get("jd") is None


def test_refit_on_resize(qtbot: Any, grid: PlanningGrid) -> None:
    assert grid.model is not None
    width = grid.model.timescale.width
    grid.resize(900, 640)
    qtbot.waitUntil(lambda: grid.model is not None and grid.model.timescale.width < width)
    assert grid.model.timescale.width <= grid.viewport_width()  # type: ignore[union-attr]


def test_pinned_unassigned_row_follows_the_scroll(qtbot: Any, grid: PlanningGrid) -> None:
    grid.resize(1200, 180)
    qtbot.waitUntil(lambda: grid.view.verticalScrollBar().maximum() > 0)
    bar = grid.view.verticalScrollBar()
    bar.setValue(bar.maximum())
    item = grid.scene.items_by_key[("un", UNASSIGNED_ROW_ID)]
    assert grid.scene.pin_offset == bar.value()
    assert item.y() >= bar.value()
    assert grid.scene.band.isVisible()
    assert grid.rows.row_at(5).resource_id == UNASSIGNED_ROW_ID  # type: ignore[union-attr]
    top = grid.view.mapToScene(QPoint(0, 5))
    assert grid.scene.row_at(top.y()).resource_id == UNASSIGNED_ROW_ID  # type: ignore[union-attr]


def test_read_only_blocks_gestures(qtbot: Any, grid: PlanningGrid) -> None:
    moves = Recorder(grid.move_requested, grid.shift_requested, grid.delete_requested)
    clicks = Recorder(grid.bar_clicked)
    grid.set_read_only(True, reason="Lecture seule pour le test")
    assert grid.view.viewport().toolTip() == "Lecture seule pour le test"
    start = bar_pos(grid, "t1", "mr")
    drag(qtbot, grid, start, start + QPoint(minutes_px(grid, 120), 0))
    qtbot.keyClick(grid.view, Qt.Key.Key_Right)
    qtbot.keyClick(grid.view, Qt.Key.Key_Delete)
    assert moves.calls == []
    assert clicks.calls[0][:2] == ("t1", "mr")
    assert not grid.drop_at(task_mime_data("card", source="backlog"), QPointF(10, 60))
    grid.set_read_only(False)
    assert grid.view.viewport().toolTip() == ""


def test_flash_and_pending_marks(grid: PlanningGrid) -> None:
    grid.set_pending(["t1"])
    assert grid.scene.items_by_key[("t1", "mr")].pending
    assert not grid.scene.items_by_key[("t2", "jd")].pending
    grid.flash(["pair"])
    assert grid.scene.flashing() == {"pair"}
    assert grid.scene.items_by_key[("pair", "nf")].flash_level > 0


def test_context_menu_and_hover_signals(grid: PlanningGrid) -> None:
    menus = Recorder(grid.context_menu_requested)
    hovers = Recorder(grid.hover_changed)
    item = grid.scene.items_by_key[("t1", "mr")]
    grid.context_menu_at(item.scene_rect().center(), QPoint(5, 5))
    assert menus.calls[0][:2] == ("t1", "mr")
    assert grid.selected_bars() == [("t1", "mr")]
    model = grid.model
    assert model is not None
    row = model.rows.get("jd")
    assert row is not None
    point = QPointF(model.timescale.x_of(at(THURSDAY, 9, 10)), row.top + 5)
    grid.context_menu_at(point, QPoint(5, 5))
    assert menus.calls[1][:3] == ("", "jd", at(THURSDAY, 9))
    header = model.rows.get(team_row_id("atelier"))
    assert header is not None
    grid.context_menu_at(QPointF(10, header.top + 2), QPoint())
    assert menus.calls[2][:3] == ("", "", -1)
    grid.gestures.hover(item.scene_rect().center(), QPoint(1, 1))
    grid.gestures.hover(QPointF(point), QPoint(2, 2))
    assert [call[0] for call in hovers.calls] == ["t1", ""]


# ---------------------------------------------------------------- drops ----


def test_mime_payloads() -> None:
    mime = task_mime_data("card", source="backlog", resource_id="mr")
    payload = parse_task_mime(mime)
    assert payload is not None
    assert (payload.task_id, payload.source, payload.resource_id) == ("card", "backlog", "mr")
    plain = QMimeData()
    plain.setData(TASK_MIME, b"abc123")
    assert parse_task_mime(plain).task_id == "abc123"  # type: ignore[union-attr]
    assert parse_task_mime(QMimeData()) is None
    assert drag_mode(Qt.KeyboardModifier.ShiftModifier) == "add"
    assert drag_mode(Qt.KeyboardModifier.AltModifier) == "copy"
    assert drag_mode(Qt.KeyboardModifier.NoModifier) == "move"


def test_card_drop_from_the_backlog(grid: PlanningGrid) -> None:
    drops = Recorder(grid.card_dropped)
    model = grid.model
    assert model is not None
    row = model.rows.get("mr")
    assert row is not None
    point = QPointF(model.timescale.x_of(at(THURSDAY, 6, 40)), row.top + row.height / 2)
    mime = task_mime_data("card", source="backlog")
    assert grid.drag_over(mime, point)
    ghost = grid.scene.ghost
    assert ghost.isVisible()
    expected_right = model.timescale.x_of(at(THURSDAY + timedelta(1), 12))
    assert ghost.scene_rect().right() == pytest.approx(expected_right, abs=2)
    assert "2 j" in grid.scene.label.text
    assert grid.drop_at(mime, point)
    assert drops.calls == [("card", "mr", at(THURSDAY, 6, 30))]
    assert not ghost.isVisible()
    header = model.rows.get(team_row_id("atelier"))
    assert header is not None
    # A team header takes the card for its first person.
    assert grid.drop_at(mime, QPointF(point.x(), header.top + 2))
    first = model.rows.rows[model.rows.index_of(header.resource_id) + 1]
    assert drops.calls[-1][:2] == ("card", first.resource_id)
    grid.set_drop_enabled(False)
    assert not grid.drag_over(mime, point)


def test_a_bar_dropped_back_on_the_grid_moves(grid: PlanningGrid) -> None:
    moves = Recorder(grid.move_requested)
    model = grid.model
    assert model is not None
    row = model.rows.get("jd")
    assert row is not None
    point = QPointF(model.timescale.x_of(at(THURSDAY, 13)), row.top + row.height / 2)
    assert grid.drop_at(task_mime_data("t1", resource_id="mr"), point)
    assert moves.calls == [("t1", "mr", "jd", at(THURSDAY, 13), "move")]


def test_dragging_a_bar_out_starts_a_qdrag(
    qtbot: Any, grid: PlanningGrid, monkeypatch: pytest.MonkeyPatch
) -> None:
    exported = Recorder(grid.bar_drag_exported)
    moves = Recorder(grid.move_requested)
    seen: list[Any] = []

    def fake_exec(mime: QMimeData, pixmap: QPixmap) -> Qt.DropAction:
        seen.append((parse_task_mime(mime), pixmap.isNull()))
        return Qt.DropAction.MoveAction

    monkeypatch.setattr(grid, "exec_drag", fake_exec)
    viewport = grid.view.viewport()
    start = bar_pos(grid, "t1", "mr")
    outside = QPoint(viewport.width() + 60, start.y())
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    qtbot.mouseMove(viewport, start + QPoint(20, 0))
    qtbot.mouseMove(viewport, outside)
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, outside)
    assert exported.calls == [("t1",)]
    assert moves.calls == []
    payload, empty = seen[0]
    assert (payload.task_id, payload.source, payload.resource_id) == ("t1", "grid", "mr")
    assert not empty


def test_a_bar_exported_then_dropped_back_is_a_move(
    grid: PlanningGrid, monkeypatch: pytest.MonkeyPatch
) -> None:
    exported = Recorder(grid.bar_drag_exported)
    moves = Recorder(grid.move_requested)
    model = grid.model
    assert model is not None
    row = model.rows.get("jd")
    assert row is not None
    point = QPointF(model.timescale.x_of(at(THURSDAY, 13)), row.top + row.height / 2)

    def drop_back(mime: QMimeData, _pixmap: QPixmap) -> Qt.DropAction:
        assert grid.drop_at(mime, point)
        return Qt.DropAction.MoveAction

    monkeypatch.setattr(grid, "exec_drag", drop_back)
    bar = model.bar("t1", "mr")
    assert bar is not None
    grid.export_bar(bar, grab=60)
    assert exported.calls == []
    assert moves.calls == [("t1", "mr", "jd", at(THURSDAY, 11), "move")]


def move_with(widget: Any, position: QPoint, modifiers: Qt.KeyboardModifier) -> None:
    """Mouse move holding the left button and ``modifiers`` (``QTest`` moves carry none)."""
    point = QPointF(position)
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        point,
        QPointF(widget.mapToGlobal(position)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        modifiers,
    )
    QApplication.sendEvent(widget, event)


def fail_export(_mime: QMimeData, _pixmap: QPixmap) -> Qt.DropAction:
    pytest.fail("the bar must not leave the grid")


@pytest.mark.parametrize(
    ("offset", "modifier"),
    [
        ((0, -1), Qt.KeyboardModifier.NoModifier),
        ((-1, 0), Qt.KeyboardModifier.NoModifier),
        ((1, 0), Qt.KeyboardModifier.ControlModifier),
        ((1, 0), Qt.KeyboardModifier.ShiftModifier),
    ],
)
def test_only_a_plain_move_past_the_right_edge_leaves_the_grid(
    qtbot: Any,
    grid: PlanningGrid,
    monkeypatch: pytest.MonkeyPatch,
    offset: tuple[int, int],
    modifier: Qt.KeyboardModifier,
) -> None:
    monkeypatch.setattr(grid, "exec_drag", fail_export)
    viewport = grid.view.viewport()
    start = bar_pos(grid, "t1", "mr")
    beyond = QPoint(
        viewport.width() + 40 if offset[0] > 0 else -40 if offset[0] < 0 else start.x(),
        -40 if offset[1] < 0 else start.y(),
    )
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, modifier, start)
    move_with(viewport, start + QPoint(offset[0] * 20, offset[1] * 20), modifier)
    move_with(viewport, beyond, modifier)
    assert grid.gestures.state is Gesture.MOVE
    qtbot.keyClick(grid.view, Qt.Key.Key_Escape)


def test_shift_drag_past_the_bottom_still_adds_the_person(
    qtbot: Any, grid: PlanningGrid, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(grid, "exec_drag", fail_export)
    moves = Recorder(grid.move_requested)
    viewport = grid.view.viewport()
    shift = Qt.KeyboardModifier.ShiftModifier
    start = bar_pos(grid, "t1", "mr")
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, shift, start)
    qtbot.mouseMove(viewport, start + QPoint(0, 20))
    qtbot.mouseMove(viewport, QPoint(start.x(), viewport.height() + 14))
    back = cell_pos(grid, "nf", at(MONDAY, 9))
    qtbot.mouseMove(viewport, back)
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, shift, back)
    assert moves.calls == [("t1", "mr", "nf", at(MONDAY, 6, 30), "add")]


def test_a_bar_coming_back_follows_the_held_keys(
    grid: PlanningGrid, monkeypatch: pytest.MonkeyPatch
) -> None:
    moves = Recorder(grid.move_requested)
    exported = Recorder(grid.bar_drag_exported)
    model = grid.model
    assert model is not None
    drops: list[tuple[str, Qt.KeyboardModifier, bool]] = []

    def drop_back(mime: QMimeData, _pixmap: QPixmap) -> Qt.DropAction:
        row_id, modifiers, allowed = drops.pop(0)
        row = model.rows.get(row_id)
        assert row is not None
        point = QPointF(model.timescale.x_of(at(THURSDAY, 9)), row.top + row.height / 2)
        assert grid.drag_over(mime, point, modifiers) is allowed
        assert grid.drop_at(mime, point, modifiers) is allowed
        return Qt.DropAction.MoveAction if allowed else Qt.DropAction.IgnoreAction

    monkeypatch.setattr(grid, "exec_drag", drop_back)
    pair = model.bar("pair", "lb")
    assert pair is not None
    shift = Qt.KeyboardModifier.ShiftModifier
    drops.extend(
        [
            ("jd", shift, True),
            ("nf", shift, False),
            ("jd", Qt.KeyboardModifier.ControlModifier, True),
            ("nf", Qt.KeyboardModifier.NoModifier, True),
        ]
    )
    for _ in range(4):
        grid.export_bar(pair, grab=0)
    wednesday = MONDAY + timedelta(days=2)
    assert moves.calls == [
        ("pair", "lb", "jd", at(wednesday, 6, 30), "add"),
        ("pair", "lb", "jd", at(THURSDAY, 9), "copy"),
        ("pair", "lb", "lb", at(THURSDAY, 9), "move"),
    ]
    assert exported.calls == []

    # Dropped back where it was: nothing to save, and it was not exported either.
    def same_place(mime: QMimeData, _pixmap: QPixmap) -> Qt.DropAction:
        row = model.rows.get("lb")
        assert row is not None
        point = QPointF(model.timescale.x_of(at(wednesday, 6, 30)), row.top + row.height / 2)
        assert grid.drop_at(mime, point, Qt.KeyboardModifier.NoModifier)
        return Qt.DropAction.MoveAction

    monkeypatch.setattr(grid, "exec_drag", same_place)
    grid.export_bar(pair, grab=0)
    assert len(moves.calls) == 3
    assert exported.calls == []


# -------------------------------------------------------------- printing ----


def test_pdf_export_creates_pages(grid: PlanningGrid, tmp_path: Path) -> None:
    path = tmp_path / "planning.pdf"
    pages = export_pdf(grid, path, poste="BUREAU-LM")
    assert pages >= 1
    document = QPdfDocument()
    document.load(str(path))
    assert document.pageCount() == pages
    assert print_title(grid.model) == "Planning — Semaine 44 · 26.10 – 30.10.2026"  # type: ignore[arg-type]  # noqa: RUF001


def cut_model(grid: PlanningGrid) -> GridModel:
    """Week of ``MONDAY`` with a confirmed task running from Friday to next Tuesday."""
    friday = MONDAY + timedelta(days=4)
    late = task("y", at(friday, 6, 30), at(friday + timedelta(4), 12), ["mr"], status="confirmed")
    return make_model(grid, snapshot=with_tasks(snapshot(), late))


def test_a_cut_bar_keeps_its_duration_clear_of_the_end_mark(grid: PlanningGrid) -> None:
    model = cut_model(grid)
    bar = model.bar("y", "mr")
    row = model.rows.get("mr")
    assert bar is not None
    assert row is not None
    assert bar.cut_right
    rect = bar_rect(model, row, bar.start, bar.end, bar.lane)
    style = build_bar_style(paper_theme(), compact=False)
    text = layout_bar_text(bar, rect.width(), rect.height(), style, BarState(printing=True))
    assert text.extra == "2 j ½"
    assert text.extra_x + style.sub_metrics.horizontalAdvance(text.extra) <= (
        rect.width() - CUT_FADE
    )


def test_a_printed_cut_bar_has_no_fade(grid: PlanningGrid) -> None:
    model = cut_model(grid)
    bar = model.bar("y", "mr")
    row = model.rows.get("mr")
    assert bar is not None
    assert row is not None
    rect = bar_rect(model, row, bar.start, bar.end, bar.lane)
    style = build_bar_style(paper_theme(), compact=False)
    width, height = round(rect.width()), round(rect.height())

    def pixels(state: BarState) -> tuple[QColor, QColor]:
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor("#ffffff"))
        painter = QPainter(image)
        paint_bar(painter, bar, QRectF(0, 0, width, height), style, state)
        painter.end()
        return image.pixelColor(width - 8, 3), image.pixelColor(width - 30, 3)

    in_mark, fill = pixels(BarState(printing=True))
    assert in_mark == fill
    faded, fill = pixels(BarState())
    assert faded.lightness() > fill.lightness()


def test_the_pdf_paints_the_bars_for_paper(
    grid: PlanningGrid, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    states: list[BarState] = []
    real = printing.paint_bar

    def spy(painter: QPainter, bar: Any, rect: QRectF, style: Any, state: BarState) -> None:
        states.append(state)
        real(painter, bar, rect, style, state)

    monkeypatch.setattr(printing, "paint_bar", spy)
    assert export_pdf(cut_model(grid), tmp_path / "cut.pdf") == 1
    assert states
    assert all(state.printing for state in states)


def test_page_bands_never_cut_a_row_nor_orphan_a_header(grid: PlanningGrid) -> None:
    model = grid.model
    assert model is not None
    rows = model.rows
    bands = page_bands(rows, 130)
    tops = {row.top for row in rows}
    bottoms = {row.bottom for row in rows}
    assert bands[0][0] == 0
    assert bands[-1][1] == rows.height
    for top, bottom in bands:
        assert top in tops
        assert bottom in bottoms
        last = rows.row_at(bottom - 1)
        assert last is not None
        assert last.kind != "team"


def test_screenshots_of_the_three_views(grid: PlanningGrid, tmp_path: Path) -> None:
    for view, density in (
        ("week", Density.NORMAL),
        ("day", Density.LARGE),
        ("month", Density.COMPACT),
    ):
        grid.set_model(make_model(grid, view=view, density=density, anchor=MONDAY))
        image = grid.grab()
        assert not image.isNull()
        assert image.save(str(tmp_path / f"planning_ui_grid_{view}.png"))


def test_many_rows_print_on_several_pages(tmp_path: Path) -> None:
    people = [
        resource(f"p{index}", f"Personne {index:02d}", "atelier", index) for index in range(70)
    ]
    snap = PlanningSnapshot(resources={item.id: item for item in people}, meta=PlanningMeta())
    model = build_grid_model(
        snap,
        view="week",
        anchor=MONDAY,
        viewport_width=1000,
        today=TODAY,
        now=at(TODAY, 10),
        density=Density.LARGE,
    )
    pages = export_pdf(model, tmp_path / "long.pdf", title="Planning test")
    assert pages > 1


@pytest.mark.parametrize(
    ("title", "subtitle", "extra", "tags"),
    [
        ("Sophie Rochat", "Technicienne", {"canton": "VD"}, ("VD",)),
        ("Nicolas Favre", "Chef monteur", {"canton": "VD"}, ("VD",)),
        ("David Pellet", "Monteur", {"canton": "VD"}, ("VD",)),
        ("Kevin Roth", "Apprenti 2e année", {"apprentice": True}, ("APP.",)),
        ("Jean-Marc Fasel", "Monteur", {"apprentice": True, "canton": "VD"}, ("APP.", "VD")),
        ("Thermolaquage", "Sous-traitant", {"subcontractor": True}, ()),
    ],
)
def test_full_names_fit_the_default_row_column_with_their_tags(
    qapp: QApplication, title: str, subtitle: str, extra: dict[str, Any], tags: tuple[str, ...]
) -> None:
    load_fonts()
    label = RowLabel(
        row_id="r",
        kind="resource",
        title=title,
        subtitle=subtitle,
        initials="RR",
        planned_minutes=900,
        available_minutes=2490,
        **extra,
    )
    fonts = RowFonts()
    room = label_room(ROW_COLUMN_WIDTH, compact=False, gauge=label.load is not None)

    texts = resource_texts(label, room, fonts, two_lines=True, default_canton="GE")

    # Never cut for the tags: they move to the role line when both do not fit.
    assert texts.name == title
    assert texts.tags == tags
    if texts.tags_on_role:
        assert texts.role
    one_line = resource_texts(label, room, fonts, two_lines=False, default_canton="GE")
    assert one_line.role == ""
    assert not one_line.tags_on_role


def test_week_header_names_the_half_days(grid: PlanningGrid) -> None:
    christmas_week = make_model(grid, anchor=date(2026, 12, 21))
    model = make_model(grid)
    columns = {column.day: column for column in model.timescale.columns}

    monday = half_day_labels(model, columns[MONDAY])
    assert [name for name, _left, _right in monday] == ["Matin", "Après-midi"]
    (_m, morning_left, morning_right), (_a, afternoon_left, afternoon_right) = monday
    assert morning_left < morning_right < afternoon_left < afternoon_right
    friday = half_day_labels(model, columns[MONDAY + timedelta(days=4)])
    assert [name for name, _left, _right in friday] == ["Matin"]
    noel = next(c for c in christmas_week.timescale.columns if c.day == date(2026, 12, 25))
    assert half_day_labels(christmas_week, noel) == ()
    assert grid.header.height() == HEADER_HEIGHT
    image = grid.header.grab()
    assert not image.isNull()


def test_open_places_are_drawn_in_non_affecte_but_never_picked(grid: PlanningGrid) -> None:
    base = snapshot()
    short = task("short", at(MONDAY, 6, 30), at(MONDAY, 16, 30), ["jd"], required_people=2)
    snap = PlanningSnapshot(
        tasks={**base.tasks, short.id: short},
        resources=base.resources,
        absences=base.absences,
        meta=base.meta,
    )
    model = make_model(grid, snapshot=snap)
    grid.set_model(model)
    [place] = model.open_places
    row = model.rows.get(UNASSIGNED_ROW_ID)
    assert row is not None
    box = bar_rect(model, row, place.start, place.end, place.lane)
    assert grid.scene.bar_at(box.center()) is None
    image = QImage(int(box.right()) + 4, int(row.bottom) + 2, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    grid.scene.drawBackground(painter, QRectF(0, 0, image.width(), image.height()))
    painter.end()
    # The dashed outline: darker pixels on its top edge than in a plain cell.
    edge = QColor(image.pixel(int(box.left()) + 1, int(box.top())))
    assert edge.lightness() < 200
