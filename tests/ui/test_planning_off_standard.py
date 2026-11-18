"""Planning: the « Hors horaires standard » switch in the Détail panel and on the grid."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QPoint, QPointF
from test_planning_controller import (  # type: ignore[import-not-found]
    data_dirs,  # noqa: F401 - fixture
    make_config,
    make_controller,
    seed_store,
    snapshot_of,
    started,
)
from test_planning_detail import (  # type: ignore[import-not-found]
    FRIDAY,
    NIGHT,
    OTHER,
    _click,
    _detail,
)
from test_planning_grid import (  # type: ignore[import-not-found]
    MONDAY,
    Recorder,
    at,
    bar_pos,
    drag,
    grid,  # noqa: F401 - fixture
    make_model,
    snapshot,
    task,
    with_tasks,
)

from projectflow.core.planning.walltime import wall_time
from projectflow.ui.planning.detail import OFF_STANDARD_TEXT
from projectflow.ui.planning.grid import PlanningGrid, task_mime_data

GRID_FRIDAY = MONDAY + timedelta(days=4)
NEXT_MONDAY = MONDAY + timedelta(days=7)
FREE = OTHER.model_copy(
    update={
        "id": "free",
        "off_standard": True,
        "start": wall_time(FRIDAY, 11, 0),
        "end": wall_time(FRIDAY, 15, 0),
    }
)


# ----------------------------------------------------------------- détail ----


def test_the_switch_sits_under_the_night_one_and_asks_for_the_change(qtbot: Any) -> None:
    host = _detail(qtbot, OTHER)
    detail = host.detail
    switch = detail.off_standard_button

    assert switch.text() == OFF_STANDARD_TEXT == "Hors horaires standard"
    assert switch.isCheckable()
    assert not switch.isChecked()
    assert switch.isEnabled()
    assert "vendredi après-midi" in switch.toolTip()
    origin = QPoint(0, 0)
    assert switch.mapTo(detail, origin).y() > detail.night_button.mapTo(detail, origin).y()
    with qtbot.waitSignal(detail.field_changed) as blocker:
        _click(qtbot, switch)
    assert blocker.args == ["other", "off_standard", True]


def test_a_task_let_out_shows_its_hours_and_counts_the_lunch_hour(qtbot: Any) -> None:
    host = _detail(qtbot, FREE)
    detail = host.detail

    assert detail.off_standard_button.isChecked()
    assert detail.start_row.value() == "ven 23 oct · 11:00"
    assert detail.end_row.value() == "ven 23 oct · 15:00"
    assert detail.duration_label.text() == "Durée : 4 h"
    with qtbot.waitSignal(detail.field_changed) as blocker:
        _click(qtbot, detail.off_standard_button)
    assert blocker.args == ["free", "off_standard", False]


def test_the_end_of_a_task_let_out_is_set_on_friday_afternoon(qtbot: Any) -> None:
    host = _detail(qtbot, FREE)
    detail = host.detail

    _click(qtbot, detail.end_row)
    detail.end_picker.step(1, 1)  # 16:00
    with qtbot.waitSignal(detail.dates_changed) as blocker:
        _click(qtbot, detail.end_row)

    assert blocker.args == ["free", wall_time(FRIDAY, 11, 0), wall_time(FRIDAY, 16, 0)]


def test_night_work_and_a_read_only_planning_lock_the_switch(qtbot: Any) -> None:
    night_host = _detail(qtbot, NIGHT)
    locked_host = _detail(qtbot, OTHER, read_only=True)
    night, locked = night_host.detail, locked_host.detail

    assert not night.off_standard_button.isEnabled()
    assert not night.off_standard_button.isChecked()
    assert not locked.off_standard_button.isEnabled()


# ------------------------------------------------------------------- grid ----


def free_grid(grid: PlanningGrid) -> None:  # noqa: F811 - the fixture, as an argument
    """Add a bar and a card « hors horaires standard » to the grid of the fixture."""
    snap = with_tasks(
        snapshot(),
        task("fbar", at(MONDAY, 6, 30), at(MONDAY, 8, 30), ["nf"], off_standard=True),
        task("fcard", None, None, [], estimate_minutes=120, off_standard=True),
    )
    grid.set_model(make_model(grid, snapshot=snap))


def test_a_bar_let_out_is_dropped_on_friday_afternoon(qtbot: Any, grid: PlanningGrid) -> None:  # noqa: F811
    free_grid(grid)
    moves = Recorder(grid.move_requested)
    model = grid.model
    assert model is not None
    scale = model.timescale
    travel = round(scale.x_of(at(GRID_FRIDAY, 13)) - scale.x_of(at(MONDAY, 6, 30)))
    start = bar_pos(grid, "fbar", "nf")

    drag(qtbot, grid, start, start + QPoint(travel, 0))

    assert moves.calls == [("fbar", "nf", "nf", at(GRID_FRIDAY, 13), "move")]


def test_a_bar_on_the_usual_hours_is_still_sent_past_friday_afternoon(
    qtbot: Any,
    grid: PlanningGrid,  # noqa: F811
) -> None:
    moves = Recorder(grid.move_requested)
    model = grid.model
    assert model is not None
    scale = model.timescale
    travel = round(scale.x_of(at(GRID_FRIDAY, 13)) - scale.x_of(at(MONDAY, 6, 30)))
    start = bar_pos(grid, "t1", "mr")

    drag(qtbot, grid, start, start + QPoint(travel, 0))

    assert moves.calls == [("t1", "mr", "mr", at(NEXT_MONDAY, 6, 30), "move")]


def test_a_card_let_out_is_placed_over_the_lunch_hour(grid: PlanningGrid) -> None:  # noqa: F811
    free_grid(grid)
    drops = Recorder(grid.card_dropped)
    model = grid.model
    assert model is not None
    row = model.rows.get("mr")
    assert row is not None
    tuesday = MONDAY + timedelta(days=1)
    point = QPointF(model.timescale.x_of(at(tuesday, 12, 5)), row.top + row.height / 2)

    assert grid.drag_over(task_mime_data("fcard", source="backlog"), point)
    ghost = grid.scene.ghost
    assert ghost.scene_rect().right() == pytest.approx(model.timescale.x_of(at(tuesday, 14)), abs=2)
    assert grid.drop_at(task_mime_data("fcard", source="backlog"), point)
    # The card of the fixture, on the usual hours, waits for the end of the break.
    assert grid.drop_at(task_mime_data("card", source="backlog"), point)

    assert drops.calls == [("fcard", "mr", at(tuesday, 12)), ("card", "mr", at(tuesday, 13))]


def test_the_bar_of_a_task_let_out_counts_its_whole_span(grid: PlanningGrid) -> None:  # noqa: F811
    snap = with_tasks(
        snapshot(),
        task("free", at(MONDAY, 11), at(MONDAY, 14), ["nf"], off_standard=True),
        task("held", at(MONDAY, 11), at(MONDAY, 14), ["jd"]),
        task("day", at(GRID_FRIDAY, 6, 30), at(GRID_FRIDAY, 16, 30), ["nf"], off_standard=True),
    )
    model = make_model(grid, snapshot=snap)

    assert model.bar("free", "nf").duration == "3 h"  # type: ignore[union-attr]
    assert model.bar("held", "jd").duration == "2 h"  # type: ignore[union-attr]
    assert model.bar("day", "nf").duration == "1 j"  # type: ignore[union-attr]
    assert model.task_calendar("free") is model.calendar_of("nf").without_breaks()
    assert model.task_calendar("held") is model.calendar_of("jd")
    # No finding for a start in the break, and the grid still hatches Friday afternoon.
    assert model.bar("free", "nf").findings == ()  # type: ignore[union-attr]
    assert any(info.off for info in model.days if info.day == GRID_FRIDAY)


# ------------------------------------------------------------- controller ----


@pytest.mark.asyncio
async def test_the_switch_is_saved_and_undone_as_one_step(qtbot: Any, tmp_path: Path) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    service = controller.service
    assert service is not None
    before = snapshot_of(controller).tasks["t-fab"]
    tab.grid.detail_requested.emit("t-fab", "mre")

    tab.detail.field_changed.emit("t-fab", "off_standard", True)  # noqa: FBT003

    after = snapshot_of(controller).tasks["t-fab"]
    assert after.off_standard
    assert (after.start, after.end) == (before.start, before.end)
    assert service.undo_label is not None
    assert service.undo_label.startswith("Sortir des horaires standard")
    controller.refresh_now()
    assert tab.detail.off_standard_button.isChecked()

    tab.detail.field_changed.emit("t-fab", "off_standard", False)  # noqa: FBT003
    assert not snapshot_of(controller).tasks["t-fab"].off_standard
    assert service.undo_label.startswith("Remettre aux horaires standard")
    controller.undo()
    assert snapshot_of(controller).tasks["t-fab"].off_standard
    await controller.aclose()
