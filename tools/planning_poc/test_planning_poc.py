# ruff: noqa: S101, DTZ001
"""Checks of the Planning prototype: time axis, generator and the gesture state machine.

PYTHONPATH=src QT_QPA_PLATFORM=offscreen py -m pytest -q tools/planning_poc
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest
from grid import BarItem, Gesture, GestureController, PlanningScene, PlanningView, ViewKind
from model import GROUPS, Kind, Plan, Resource, Task, generate_plan
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication
from timescale import (
    DAY_SPAN,
    WORK_PER_DAY,
    WorkCalendar,
    assign_lanes,
    cantonal_holidays,
    easter_sunday,
    nearest_half_day_edge,
    shift_half_days,
)
from window import PlanningWindow, export_week_pdf

from projectflow.ui.theme import apply_theme

CALENDAR = WorkCalendar(date(2026, 8, 31), 92)


def at(day: int, month: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute)


def pos(moment: datetime) -> int:
    return CALENDAR.position_of(moment)


# ------------------------------------------------------------ timescale ----


def test_easter_sunday_known_years() -> None:
    assert easter_sunday(2024) == date(2024, 3, 31)
    assert easter_sunday(2025) == date(2025, 4, 20)
    assert easter_sunday(2026) == date(2026, 4, 5)
    assert easter_sunday(2027) == date(2027, 3, 28)


def test_geneva_and_vaud_holidays() -> None:
    holidays = cantonal_holidays(2026)
    expected = {
        date(2026, 1, 1): ("GE", "VD"),
        date(2026, 1, 2): ("VD",),
        date(2026, 4, 3): ("GE", "VD"),
        date(2026, 4, 6): ("GE", "VD"),
        date(2026, 5, 14): ("GE", "VD"),
        date(2026, 5, 25): ("GE", "VD"),
        date(2026, 8, 1): ("GE", "VD"),
        date(2026, 9, 10): ("GE",),
        date(2026, 9, 21): ("VD",),
        date(2026, 12, 25): ("GE", "VD"),
        date(2026, 12, 31): ("GE",),
    }
    assert {day: item.cantons for day, item in holidays.items()} == expected
    assert set(cantonal_holidays(2026, ["GE"])) == {
        day for day, cantons in expected.items() if "GE" in cantons
    }


def test_weekends_are_hidden_and_friday_evening_meets_monday_morning() -> None:
    assert all(day.weekday() < 5 for day in CALENDAR.days)
    assert pos(at(16, 10, 16, 30)) == pos(at(19, 10, 7))
    assert pos(datetime(2026, 10, 17, 10)) == pos(at(19, 10, 7))
    assert CALENDAR.moment_at(pos(at(19, 10, 7)), end=True) == at(16, 10, 16, 30)
    assert CALENDAR.moment_at(pos(at(19, 10, 7))) == at(19, 10, 7)
    assert CALENDAR.holidays.keys() == {date(2026, 9, 10), date(2026, 9, 21)}


def test_working_time_skips_lunch_nights_weekends_and_holidays() -> None:
    assert CALENDAR.add_work(pos(at(9, 10, 11)), 90) == pos(at(9, 10, 13, 30))
    friday = pos(at(4, 9, 15, 30))
    assert CALENDAR.moment_at(CALENDAR.add_work(friday, 120)) == at(7, 9, 8)
    wednesday = pos(at(9, 9, 11))
    after_holiday = CALENDAR.add_work(wednesday, WORK_PER_DAY)
    assert CALENDAR.moment_at(after_holiday) == at(11, 9, 11)
    assert CALENDAR.work_between(wednesday, after_holiday) == WORK_PER_DAY
    assert CALENDAR.work_between(pos(at(19, 10, 7)), pos(at(19, 10, 16, 30))) == WORK_PER_DAY


def test_starts_and_ends_never_sit_in_a_break() -> None:
    assert CALENDAR.working_start(pos(at(19, 10, 12, 15))) == pos(at(19, 10, 13))
    assert CALENDAR.working_start(pos(at(10, 9, 9))) == pos(at(11, 9, 7))
    assert CALENDAR.working_end(pos(at(19, 10, 12, 30))) == pos(at(19, 10, 12))
    assert CALENDAR.working_end(pos(at(11, 9, 7))) == pos(at(9, 9, 16, 30))


def test_half_day_snapping_keeps_the_time_of_day_offset() -> None:
    monday = pos(at(19, 10, 8, 30))
    assert CALENDAR.moment_at(shift_half_days(monday, 1)) == at(19, 10, 14, 30)
    assert CALENDAR.moment_at(shift_half_days(monday, 2)) == at(20, 10, 8, 30)
    assert CALENDAR.moment_at(shift_half_days(monday, -1)) == at(16, 10, 14, 30)
    late_morning = pos(at(19, 10, 11, 45))
    assert CALENDAR.moment_at(shift_half_days(late_morning, 1)) == at(19, 10, 16)
    base = pos(at(19, 10, 7))
    assert nearest_half_day_edge(base + 250, start=True) == base + 360
    assert nearest_half_day_edge(base + 250, start=False) == base + 300
    assert nearest_half_day_edge(base + 500, start=False) == base + DAY_SPAN


def test_lanes_stack_only_overlapping_spans() -> None:
    lanes, count = assign_lanes([(0, 10), (10, 20), (5, 12), (12, 30), (31, 40)])
    assert count == 2
    assert lanes == [0, 0, 1, 1, 0]


def test_generator_meets_the_benchmark_shape() -> None:
    plan = generate_plan(CALENDAR, 40)
    assert len(plan.resources) == 40
    assert {item.group for item in plan.resources} == set(GROUPS)
    assert sum(item.subcontractor for item in plan.resources) == 3
    assert plan.bar_count >= 800
    owners = [len(task.resource_ids) for task in plan.tasks.values()]
    assert 2 in owners
    assert 3 in owners
    for task in plan.tasks.values():
        assert task.start.minute % 30 == 0
        assert task.start < task.end
        assert CALENDAR.working_start(pos(task.start)) == pos(task.start)


# ------------------------------------------------------------- gestures ----


MONDAY = at(19, 10, 8)
DAY = 190.0  # one displayed day; 30 minutes = 10 px


def montage_task(task_id: str, start: datetime, end: datetime, *owners: str) -> Task:
    return Task(task_id, "2026-118", "Montage garde-corps", Kind.MONTAGE, start, end, owners)


@pytest.fixture
def scene(qapp: QApplication) -> Iterator[PlanningScene]:
    apply_theme(qapp)
    resources = [
        Resource("a", "Anne", "Montage"),
        Resource("b", "Bruno", "Montage"),
        Resource("c", "Carla", "Montage"),
    ]
    tasks = {
        "t1": montage_task("t1", MONDAY, at(19, 10, 12), "a"),
        "t2": montage_task("t2", at(20, 10, 7), at(21, 10, 16, 30), "a", "b"),
        "t3": montage_task("t3", at(19, 10, 7), at(23, 10, 16, 30), "c"),
    }
    planning = PlanningScene(Plan(resources, tasks), CALENDAR, at(22, 10, 10))
    planning.apply_view(ViewKind.WEEK, DAY)
    yield planning
    planning.clear()


def drag(controller: GestureController, start: QPointF, *offsets: QPointF) -> None:
    controller.press(start)
    for offset in offsets:
        controller.move(start + offset)
    controller.release(start + offsets[-1])


def body(scene: PlanningScene, task_id: str, resource_id: str) -> QPointF:
    return scene.bars[task_id, resource_id].rect().center()


def controller_for(scene: PlanningScene) -> GestureController:
    return GestureController(scene, scene.sceneRect)


def test_click_selects_and_links_shared_bars(scene: PlanningScene) -> None:
    controller = controller_for(scene)
    controller.press(body(scene, "t2", "a"))
    controller.release(body(scene, "t2", "a"))
    assert scene.selected is scene.bars["t2", "a"]
    assert scene.bars["t2", "b"].linked
    assert scene.plan.tasks["t2"].start == at(20, 10, 7)


def test_move_snaps_to_half_hours_and_keeps_working_duration(scene: PlanningScene) -> None:
    drag(controller_for(scene), body(scene, "t1", "a"), QPointF(30, 0), QPointF(DAY + 4, 0))
    task = scene.plan.tasks["t1"]
    assert (task.start, task.end) == (at(20, 10, 8), at(20, 10, 12))


def test_move_over_lunch_and_weekend(scene: PlanningScene) -> None:
    drag(controller_for(scene), body(scene, "t1", "a"), QPointF(80, 0))
    task = scene.plan.tasks["t1"]
    assert (task.start, task.end) == (at(19, 10, 13), at(20, 10, 7, 30))
    drag(controller_for(scene), body(scene, "t1", "a"), QPointF(4 * DAY, 0))
    task = scene.plan.tasks["t1"]
    assert (task.start, task.end) == (at(23, 10, 13), at(26, 10, 7, 30))


def test_vertical_drag_reassigns_one_row_only(scene: PlanningScene) -> None:
    carla_y = body(scene, "t3", "c").y()
    start = body(scene, "t1", "a")
    drag(controller_for(scene), start, QPointF(0, carla_y - start.y()))
    assert scene.plan.tasks["t1"].resource_ids == ("c",)
    assert scene.layout.rows[scene.layout.row_of("c")].lanes == 2
    shared = body(scene, "t2", "a")
    bruno_y = body(scene, "t2", "b").y()
    drag(controller_for(scene), shared, QPointF(0, bruno_y - shared.y()))
    assert scene.plan.tasks["t2"].resource_ids == ("a", "b")


def test_edges_resize_with_snapping(scene: PlanningScene) -> None:
    controller = controller_for(scene)
    bar = scene.bars["t1", "a"]
    controller.hover(QPointF(bar.rect().right() - 2, bar.rect().center().y()))
    assert controller.cursor == Qt.CursorShape.SizeHorCursor
    right = QPointF(bar.rect().right() - 2, bar.rect().center().y())
    drag(controller, right, QPointF(5, 0), QPointF(43, 0))
    assert scene.plan.tasks["t1"].end == at(19, 10, 14)
    left = QPointF(bar.rect().left() + 2, bar.rect().center().y())
    drag(controller, left, QPointF(-5, 0), QPointF(-31, 0))
    assert scene.plan.tasks["t1"].start == at(16, 10, 16)
    assert scene.plan.tasks["t1"].end == at(19, 10, 14)


def test_escape_cancels_back_to_origin(scene: PlanningScene) -> None:
    controller = controller_for(scene)
    bar = scene.bars["t2", "a"]
    origin = bar.rect()
    sibling = scene.bars["t2", "b"].rect()
    start = origin.center()
    controller.press(start)
    controller.move(start + QPointF(120, 40))
    assert controller.state is Gesture.MOVE
    assert scene.bars["t2", "b"].rect() != sibling
    assert controller.cancel()
    assert bar.rect() == origin
    assert scene.bars["t2", "b"].rect() == sibling
    controller.move(start + QPointF(300, 0))
    controller.release(start + QPointF(300, 0))
    assert bar.rect() == origin
    assert scene.plan.tasks["t2"].start == at(20, 10, 7)


def test_month_view_snaps_to_half_days(scene: PlanningScene) -> None:
    scene.apply_view(ViewKind.MONTH, 57.0)
    drag(controller_for(scene), body(scene, "t1", "a"), QPointF(34, 0))
    task = scene.plan.tasks["t1"]
    assert (task.start, task.end) == (at(19, 10, 14), at(20, 10, 8, 30))
    bar = scene.bars["t1", "a"]
    right = QPointF(bar.rect().right() - 1, bar.rect().center().y())
    drag(controller_for(scene), right, QPointF(5, 0), QPointF(12, 0))
    assert scene.plan.tasks["t1"].end == at(20, 10, 12)
    controller = controller_for(scene)
    start = body(scene, "t1", "a")
    controller.press(start)
    controller.move(start + QPointF(9, 0), fine=True)
    controller.release(start + QPointF(9, 0))
    assert scene.plan.tasks["t1"].start == at(19, 10, 15, 30)


def test_move_onto_the_last_day_keeps_the_working_duration(scene: PlanningScene) -> None:
    origin = scene.plan.tasks["t3"]
    work = CALENDAR.work_between(*scene.span_of(origin))
    for kind, day_width in ((ViewKind.WEEK, DAY), (ViewKind.MONTH, 57.0)):
        scene.commit(origin, scene.bars["t3", "c"], "c")
        scene.apply_view(kind, day_width)
        drag(controller_for(scene), body(scene, "t3", "c"), QPointF(5, 0), QPointF(60 * DAY, 0))
        task = scene.plan.tasks["t3"]
        assert (task.start, task.end) == (at(24, 11, 7), at(30, 11, 16, 30))
        assert CALENDAR.work_between(*scene.span_of(task)) == work


def test_move_puts_an_off_grid_task_back_on_the_half_hour_grid(scene: PlanningScene) -> None:
    scene.commit(
        montage_task("t1", at(20, 10, 7, 15), at(20, 10, 11, 50), "a"), scene.bars["t1", "a"], "a"
    )
    drag(controller_for(scene), body(scene, "t1", "a"), QPointF(41, 0))
    task = scene.plan.tasks["t1"]
    assert (task.start, task.end) == (at(20, 10, 9, 30), at(20, 10, 15, 5))


def test_tasks_cut_by_the_loaded_range_are_not_moved(scene: PlanningScene) -> None:
    early = montage_task("t1", datetime(2026, 8, 20, 7), at(1, 9, 12), "a")
    scene.commit(early, scene.bars["t1", "a"], "a")
    drag(controller_for(scene), body(scene, "t1", "a"), QPointF(5, 0), QPointF(DAY, 0))
    assert scene.plan.tasks["t1"] == early
    assert scene.selected is scene.bars["t1", "a"]


def test_lanes_follow_the_lane_window(scene: PlanningScene) -> None:
    overlap = montage_task("t4", at(19, 10, 9), at(19, 10, 11), "a")
    scene.plan.tasks["t4"] = overlap
    scene.bars["t4", "a"] = bar = BarItem(scene, overlap, "a")
    scene.task_bars["t4"] = [bar]
    scene.addItem(bar)
    row = scene.layout.row_of("a")
    scene.relayout()
    assert scene.layout.rows[row].lanes == 2
    week = CALENDAR.week_range(CALENDAR.index_of(date(2026, 10, 26)) or 0)
    scene.set_lane_window((week.start * DAY_SPAN, week.stop * DAY_SPAN))
    assert scene.layout.rows[row].lanes == 1
    assert {scene.bars[key].lane for key in (("t1", "a"), ("t4", "a"))} == {0}
    scene.set_lane_window(None)
    assert scene.layout.rows[row].lanes == 2


def press_release_double_click_then_drag(view: PlanningView, start: QPointF, dx: float) -> None:
    viewport = view.viewport()
    point = QPointF(view.mapFromScene(start))
    left, none = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton
    modifiers = Qt.KeyboardModifier.NoModifier

    def send(kind: QEvent.Type, where: QPointF, button: Qt.MouseButton, *, held: bool) -> None:
        screen = QPointF(viewport.mapToGlobal(where.toPoint()))
        event = QMouseEvent(kind, where, screen, button, left if held else none, modifiers)
        QApplication.sendEvent(viewport, event)

    send(QEvent.Type.MouseButtonPress, point, left, held=True)
    send(QEvent.Type.MouseButtonRelease, point, left, held=False)
    send(QEvent.Type.MouseButtonDblClick, point, left, held=True)
    for step in range(1, 5):
        send(QEvent.Type.MouseMove, point + QPointF(dx * step / 4, 0), none, held=True)
    send(QEvent.Type.MouseButtonRelease, point + QPointF(dx, 0), left, held=False)


def test_a_quick_second_press_still_drags(qapp: QApplication, scene: PlanningScene) -> None:
    window = PlanningWindow(PlanningScene(scene.plan, CALENDAR, scene.now))
    window.resize(1200, 500)
    window.show()
    qapp.processEvents()
    window.set_view(ViewKind.WEEK, CALENDAR.index_of(MONDAY.date()))
    bar = window.scene.bars["t1", "a"]
    press_release_double_click_then_drag(
        window.view, bar.rect().center(), window.scene.scale.day_width
    )
    assert scene.plan.tasks["t1"].start == at(20, 10, 8)
    window.close()


def test_generator_and_calendar_on_tiny_ranges() -> None:
    one_day = WorkCalendar(date(2026, 10, 19), 1)
    assert generate_plan(one_day, 12).tasks
    with pytest.raises(ValueError, match="ouvrable"):
        WorkCalendar(date(2026, 12, 25), 1)


def test_window_view_follows_the_scene_height(qapp: QApplication) -> None:
    apply_theme(qapp)
    window = PlanningWindow(PlanningScene(generate_plan(CALENDAR, 40), CALENDAR, at(22, 10, 10)))
    window.resize(1200, 700)
    window.show()
    qapp.processEvents()
    window.set_view(ViewKind.WEEK, CALENDAR.index_of(date(2026, 10, 19)))
    scene, view = window.scene, window.view
    layout = scene.layout
    alone = {
        bar.resource_id: bar for bar in window.visible_bars() if len(bar.task.resource_ids) == 1
    }
    single = [key for key in alone if layout.rows[layout.row_of(key)].lanes == 1]
    mover, blocker = alone[single[0]], alone[single[1]]
    height = layout.height
    moved = replace(
        mover.task, start=blocker.task.start, end=blocker.task.end, resource_ids=(single[1],)
    )
    scene.commit(moved, mover, single[1])
    assert scene.layout.height > height
    assert view.sceneRect() == scene.sceneRect()
    scroll = view.verticalScrollBar()
    assert scroll.maximum() + view.viewport().height() == pytest.approx(scene.layout.height, abs=1)
    window.close()


def test_window_paints_and_exports_a_week(qapp: QApplication, tmp_path: Path) -> None:
    apply_theme(qapp)
    plan = generate_plan(CALENDAR, 12)
    window = PlanningWindow(PlanningScene(plan, CALENDAR, at(22, 10, 10)))
    window.resize(1200, 700)
    window.show()
    qapp.processEvents()
    scene = window.scene
    for kind in ViewKind:
        window.set_view(kind, CALENDAR.index_of(date(2026, 10, 22)))
        assert not window.grab().isNull()
        assert window.left_day() == window.anchor
        shown = window.view.visible_scene_rect()
        assert scene.lane_window is not None
        low, high = scene.lane_window
        assert low <= scene.scale.position_at(shown.left() + 1)
        assert scene.scale.position_at(shown.right() - 1) <= high
    path = tmp_path / "semaine.pdf"
    assert export_week_pdf(window, path, CALENDAR.index_of(date(2026, 10, 22))) == 1
    assert path.read_bytes().startswith(b"%PDF")
    assert window.kind is ViewKind.MONTH
    window.close()


def test_pdf_adds_pages_rather_than_shrinking_text(qapp: QApplication, tmp_path: Path) -> None:
    apply_theme(qapp)
    window = PlanningWindow(PlanningScene(generate_plan(CALENDAR, 40), CALENDAR, at(22, 10, 10)))
    window.resize(1200, 700)
    window.show()
    qapp.processEvents()
    path = tmp_path / "semaine.pdf"
    pages = export_week_pdf(window, path, CALENDAR.index_of(date(2026, 10, 22)))
    assert pages >= 2
    assert len(re.findall(rb"/Type\s*/Page\b", path.read_bytes())) == pages
    window.close()


def test_hover_and_ctrl_wheel_zoom_keep_the_time_under_the_pointer(
    qapp: QApplication,
) -> None:
    apply_theme(qapp)
    window = PlanningWindow(PlanningScene(generate_plan(CALENDAR, 12), CALENDAR, at(22, 10, 10)))
    window.resize(1200, 700)
    window.show()
    qapp.processEvents()
    window.set_view(ViewKind.WEEK, CALENDAR.index_of(date(2026, 10, 22)))
    scene, view = window.scene, window.view
    bar = window.visible_bars()[0]
    view.controller.hover(bar.rect().center())
    assert scene.hovered is bar
    assert bar.hovered
    width = scene.scale.day_width
    before = scene.scale.position_at(view.mapToScene(300, 0).x())
    window.zoom(2.0, 300.0)
    assert scene.scale.day_width == pytest.approx(2 * width)
    assert scene.scale.position_at(view.mapToScene(300, 0).x()) == pytest.approx(before, abs=3)
    window.close()
