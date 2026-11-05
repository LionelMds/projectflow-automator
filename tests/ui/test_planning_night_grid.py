"""Night work on the grid: bars over their evening's column, 24 h day view, rest blocks."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from test_planning_grid import (
    MONDAY,
    Recorder,
    at,
    bar_pos,
    drag,
    make_model,
    snapshot,
    task,
    with_tasks,
)
from test_planning_grid_model import build
from test_planning_grid_model import snapshot as model_snapshot
from test_planning_grid_model import task as model_task

from projectflow.core.planning.night import default_night
from projectflow.ui.planning.grid import PlanningGrid
from projectflow.ui.planning.grid_items import bar_rect, build_grid_style, paint_background
from projectflow.ui.planning.hovercard import HoverCard, HoverInfo
from projectflow.ui.theme import build_theme


@pytest.fixture
def night_grid(qtbot: Any) -> PlanningGrid:
    widget = PlanningGrid()
    qtbot.addWidget(widget)
    widget.resize(1200, 640)
    widget.show()
    qtbot.waitExposed(widget)
    return widget


TUESDAY = MONDAY + timedelta(days=1)
WEDNESDAY = MONDAY + timedelta(days=2)
SATURDAY = MONDAY + timedelta(days=5)


def night(tid: str, day: date, people: list[str], **extra: Any) -> Any:
    start, end = default_night(day)
    return model_task(tid, start, end, people, off_hours=True, **extra)


# ------------------------------------------------------------ grid model ----


def test_a_night_bar_fills_the_column_of_its_evening() -> None:
    model = build(model_snapshot(night("n", MONDAY, ["mr"])))
    bar = model.bar("n", "mr")
    assert bar is not None
    assert bar.night
    assert (bar.start, bar.end) == default_night(MONDAY)
    assert model.display_span(bar.start, bar.end, night=True) == (
        at(MONDAY, 6, 30),
        at(MONDAY, 16, 30),
    )
    assert bar.duration == bar.time_range == "21:00–05:00"  # noqa: RUF001
    assert bar.badges[0].kind == "night"
    assert not bar.cut_left
    assert not bar.cut_right
    assert not model.night_axis


def test_a_night_takes_a_lane_next_to_the_day_work_of_its_evening() -> None:
    day = model_task("d", at(MONDAY, 6, 30), at(MONDAY, 12), ["mr"])
    model = build(model_snapshot(night("n", MONDAY, ["mr"]), day))
    row = model.rows.get("mr")
    assert row is not None
    assert row.lane_count == 2
    assert {row.lanes["n"], row.lanes["d"]} == {0, 1}


def test_weekends_stay_hidden_with_night_work() -> None:
    model = build(model_snapshot(night("n", date(2026, 10, 29), ["mr"])))
    days = [column.day for column in model.timescale.columns]
    assert SATURDAY not in days
    assert len(days) == 5


def test_day_view_of_a_night_shows_the_24_hours() -> None:
    snap = model_snapshot(night("n", MONDAY, ["mr"]))
    model = build(snap, view="day", anchor=MONDAY)
    assert model.night_axis
    assert model.timescale.window == (0, 1440)
    assert model.timescale.start == at(MONDAY, 0)
    bar = model.bar("n", "mr")
    assert bar is not None
    assert model.display_span(bar.start, bar.end, night=True) == (bar.start, bar.end)
    assert bar.cut_right
    morning = build(snap, view="day", anchor=TUESDAY)
    assert morning.night_axis
    plain = build(snap, view="day", anchor=WEDNESDAY)
    assert not plain.night_axis
    assert plain.timescale.window == (390, 990)
    assert build(snap, view="day", anchor=WEDNESDAY, show_night=True).night_axis


def test_rest_blocks_before_and_after_a_night() -> None:
    model = build(model_snapshot(night("n", MONDAY, ["mr", "jd"])))
    blocks = model.rests_by_row["mr"]
    assert [(block.start, block.end, block.before) for block in blocks] == [
        (at(MONDAY, 13), at(MONDAY, 21), True),
        (at(TUESDAY, 5), at(TUESDAY, 13), False),
    ]
    assert len(model.rests_by_row["jd"]) == 2
    assert "lb" not in model.rests_by_row
    assert model.resting_during("mr", at(TUESDAY, 6, 30), at(TUESDAY, 12)) is not None
    assert model.resting_during("mr", at(TUESDAY, 13), at(TUESDAY, 16, 30)) is None
    # A rest is not an absence: the row's available time is unchanged.
    label = model.label("mr")
    assert label is not None
    assert label.available_minutes == 4 * 540 + 330


def test_a_morning_task_after_a_night_gets_the_rest_badge() -> None:
    morning = model_task("m", at(TUESDAY, 6, 30), at(TUESDAY, 12), ["mr"])
    model = build(model_snapshot(night("n", MONDAY, ["mr"]), morning))
    bar = model.bar("m", "mr")
    assert bar is not None
    assert bar.has_conflict
    assert any(text.startswith("Repos de 8 h non respecté") for text in bar.findings)
    night_bar = model.bar("n", "mr")
    assert night_bar is not None
    assert night_bar.has_conflict


def test_night_minutes_count_in_the_row_load() -> None:
    model = build(model_snapshot(night("n", MONDAY, ["mr"])))
    label = model.label("mr")
    assert label is not None
    assert label.planned_minutes == 480


# --------------------------------------------------------------- painting ----


def paint(model: Any, *, dark: bool) -> QImage:
    image = QImage(800, 400, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    style = build_grid_style(build_theme("dark" if dark else "light", "acier"))
    paint_background(painter, model, QRectF(0, 0, 800, 400), style)
    painter.end()
    return image


def test_rest_blocks_paint_in_both_themes(qtbot: Any) -> None:
    model = build(model_snapshot(night("n", MONDAY, ["mr"])), viewport_width=800.0)
    for dark in (False, True):
        image = paint(model, dark=dark)
        assert not image.isNull()
    row = model.rows.get("mr")
    assert row is not None
    bar = model.bar("n", "mr")
    assert bar is not None
    rect = bar_rect(model, row, bar.start, bar.end, bar.lane, night=True)
    column = model.timescale.columns[0]
    assert abs(rect.width() - column.width) <= 4


def test_hover_card_tells_the_rest(qtbot: Any) -> None:
    card = HoverCard()
    qtbot.addWidget(card)
    card.show_for(
        HoverInfo(title="Aéroport", when="nuit", duration="8 h", rest="Repos : dès lun"),
        QPoint(10, 10),
    )
    assert "Repos : dès lun" in card.when_label.text()


# --------------------------------------------------------------- gestures ----


def test_dragging_a_night_changes_its_evening_only(qtbot: Any, night_grid: PlanningGrid) -> None:
    grid = night_grid
    snap = with_tasks(snapshot(), task("n", *default_night(MONDAY), ["mr"], off_hours=True))
    grid.set_model(make_model(grid, snapshot=snap))
    moves = Recorder(grid.move_requested)
    start = bar_pos(grid, "n", "mr", 0.3)
    model = grid.model
    assert model is not None
    width = round(model.timescale.day_width * 2)
    drag(qtbot, grid, start, start + QPoint(width, 0))
    assert moves.calls == [("n", "mr", "mr", at(WEDNESDAY, 21), "move")]


def test_dragging_a_night_onto_friday_warns(qtbot: Any, night_grid: PlanningGrid) -> None:
    grid = night_grid
    snap = with_tasks(snapshot(), task("n", *default_night(MONDAY), ["mr"], off_hours=True))
    grid.set_model(make_model(grid, snapshot=snap))
    model = grid.model
    assert model is not None
    viewport = grid.view.viewport()
    start = bar_pos(grid, "n", "mr", 0.3)
    end = start + QPoint(round(model.timescale.day_width * 4), 0)
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    qtbot.mouseMove(viewport, start + QPoint(20, 0))
    qtbot.mouseMove(viewport, end)
    assert "week-end" in grid.scene.label.text
    assert grid.scene.label.warn
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)


def test_a_night_bar_has_no_resize_edge_in_the_week(qtbot: Any, night_grid: PlanningGrid) -> None:
    grid = night_grid
    snap = with_tasks(snapshot(), task("n", *default_night(MONDAY), ["mr"], off_hours=True))
    grid.set_model(make_model(grid, snapshot=snap))
    item = grid.scene.items_by_key[("n", "mr")]
    assert item.zone_at(1) == "body"
    assert item.zone_at(item.width - 1) == "body"
