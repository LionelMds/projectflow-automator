"""Planning: the small calendar under « À planifier » and the « Détail » window."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QPoint, Qt
from test_planning_controller import (  # type: ignore[import-not-found]
    NEXT_MONDAY,
    TODAY,
    WEEK,
    data_dirs,  # noqa: F401 - fixture
    make_config,
    make_controller,
    seed_store,
    started,
)

from projectflow.ui.planning.mini_calendar import (
    MiniCalendar,
    add_months,
    month_title,
    month_weeks,
)

OCTOBER = date(2026, 10, 1)


def _calendar(qtbot: Any) -> MiniCalendar:
    calendar = MiniCalendar()
    qtbot.addWidget(calendar)
    calendar.resize(312, calendar.sizeHint().height())
    calendar.set_today(TODAY)
    calendar.set_period(WEEK, WEEK + timedelta(days=4))
    calendar.show()
    qtbot.waitExposed(calendar)
    return calendar


def _click(qtbot: Any, calendar: MiniCalendar, day: date) -> None:
    rect = calendar.day_rect(day)
    assert rect is not None
    qtbot.mouseClick(calendar, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())


def test_a_month_is_six_weeks_from_monday() -> None:
    weeks = month_weeks(date(2026, 10, 22))
    assert len(weeks) == 6
    assert all(len(week) == 7 and week[0].weekday() == 0 for week in weeks)
    assert weeks[0][0] == date(2026, 9, 28)
    assert weeks[-1][-1] == date(2026, 11, 8)
    assert month_title(OCTOBER) == "octobre 2026"
    assert add_months(date(2026, 12, 1), 1) == date(2027, 1, 1)
    assert add_months(date(2026, 1, 1), -1) == date(2025, 12, 1)


def test_a_click_on_a_day_or_a_week_number_chooses_it(qtbot: Any) -> None:
    calendar = _calendar(qtbot)
    chosen: list[date] = []
    calendar.day_chosen.connect(chosen.append)

    assert calendar.month() == OCTOBER
    _click(qtbot, calendar, date(2026, 10, 28))
    _click(qtbot, calendar, date(2026, 11, 3))  # a day of the next month, drawn dimmed
    assert chosen == [date(2026, 10, 28), date(2026, 11, 3)]
    assert calendar.month() == OCTOBER  # only the planning moves the month shown

    rect = calendar.day_rect(date(2026, 10, 12))
    assert rect is not None
    number = QPoint(20, round(rect.center().y()))  # « 42 », left of its Monday
    assert calendar.week_at(number) == date(2026, 10, 12)
    qtbot.mouseClick(calendar, Qt.MouseButton.LeftButton, pos=number)
    assert chosen[-1] == date(2026, 10, 12)


def test_the_month_shown_follows_the_period_and_leafs_on_its_own(qtbot: Any) -> None:
    calendar = _calendar(qtbot)
    months: list[date] = []
    calendar.month_changed.connect(months.append)

    # A week over the end of the month stays on the month it starts in.
    calendar.set_period(date(2026, 10, 26), date(2026, 10, 30))
    assert calendar.month() == OCTOBER
    calendar.set_period(date(2026, 11, 9), date(2026, 11, 13))
    assert calendar.month() == date(2026, 11, 1)
    assert calendar.title() == "novembre 2026"

    # The arrows leaf through the months without choosing a day; the title comes back.
    chosen: list[date] = []
    calendar.day_chosen.connect(chosen.append)
    right = QPoint(calendar.width() - 20, 20)
    qtbot.mouseClick(calendar, Qt.MouseButton.LeftButton, pos=right)
    qtbot.mouseClick(calendar, Qt.MouseButton.LeftButton, pos=right)
    assert calendar.month() == date(2027, 1, 1)
    qtbot.mouseClick(calendar, Qt.MouseButton.LeftButton, pos=QPoint(calendar.width() - 46, 20))
    assert calendar.month() == date(2026, 12, 1)
    qtbot.mouseClick(calendar, Qt.MouseButton.LeftButton, pos=QPoint(40, 20))
    assert calendar.month() == date(2026, 11, 1)
    assert chosen == []
    assert months[-3:] == [date(2027, 1, 1), date(2026, 12, 1), date(2026, 11, 1)]


def test_the_keyboard_moves_a_day_and_chooses_it(qtbot: Any) -> None:
    calendar = _calendar(qtbot)
    chosen: list[date] = []
    calendar.day_chosen.connect(chosen.append)
    calendar.setFocus(Qt.FocusReason.TabFocusReason)

    qtbot.keyClick(calendar, Qt.Key.Key_Right)
    qtbot.keyClick(calendar, Qt.Key.Key_Down)
    qtbot.keyClick(calendar, Qt.Key.Key_Return)
    assert chosen == [WEEK + timedelta(days=8)]
    qtbot.keyClick(calendar, Qt.Key.Key_PageDown)
    assert calendar.month() == date(2026, 11, 1)


@pytest.mark.asyncio
async def test_the_calendar_of_the_tab_moves_the_planning_and_follows_it(
    qtbot: Any, tmp_path: Path
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    calendar = tab.calendar
    tab.grid.slide_ms = 0  # The planning lands at once on the day clicked.

    assert calendar.isVisible()
    assert calendar.today() == TODAY
    assert calendar.period() == tab.period_range() == (WEEK, WEEK + timedelta(days=4))

    _click(qtbot, calendar, date(2026, 10, 28))  # a Wednesday: its week is shown
    assert tab.anchor == NEXT_MONDAY
    assert calendar.period()[0] == NEXT_MONDAY
    controller.refresh_now()
    strip = tab.grid.model.strip
    assert strip is not None
    index = strip.index_of(NEXT_MONDAY)
    assert index is not None
    assert tab.grid.scroll_x == strip.rest(index)

    # Scrolling on in time moves the period tinted in the calendar, and its month.
    tab.grid.page_changed.emit(date(2026, 11, 9))
    assert tab.anchor == date(2026, 11, 9)
    assert calendar.period()[0] == date(2026, 11, 9)
    assert calendar.month() == date(2026, 11, 1)

    tab.set_view("month")
    assert calendar.period() == (date(2026, 11, 1), date(2026, 11, 30))
    tab.set_view("day", anchor=TODAY)
    assert calendar.period() == (TODAY, TODAY)
    await controller.aclose()


@pytest.mark.asyncio
async def test_the_detail_window_follows_the_bar_clicked_while_open(
    qtbot: Any, tmp_path: Path
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    first, second = list(tab.grid.model.bars_by_task)[:2]
    row = tab.grid.model.bars_by_task[second][0].row_id

    # A click alone selects: the window opens on demand only.
    tab.grid.bar_clicked.emit(first, row, Qt.KeyboardModifier.NoModifier)
    assert not tab.detail_open()

    tab.grid.detail_requested.emit(first, row)
    assert tab.detail_open()
    assert tab.detail.task_id() == first
    assert tab.detail_window.windowTitle().endswith("— Détail")
    tab.grid.bar_clicked.emit(second, row, Qt.KeyboardModifier.NoModifier)
    assert tab.detail.task_id() == second
    assert tab.side.backlog.isVisible()  # the cards stay beside the planning

    tab.detail.back_requested.emit()
    assert not tab.detail_open()
    tab.grid.bar_clicked.emit(first, row, Qt.KeyboardModifier.NoModifier)
    assert not tab.detail_open()
    assert tab.detail.task_id() == second

    # The window of a task deleted goes with it.
    tab.grid.detail_requested.emit(first, row)
    assert tab.detail_open()
    tab.detail.action_requested.emit(first, "delete")
    controller.refresh_now()
    assert not tab.detail_open()
    assert tab.detail.mode() == "empty"
    await controller.aclose()
