"""Planning: time scrolls over one strip of pages side by side."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from test_planning_controller import (  # type: ignore[import-not-found]
    data_dirs,  # noqa: F401 - fixture
    make_config,
    make_controller,
    seed_store,
    started,
)
from test_planning_grid import (  # type: ignore[import-not-found]
    MONDAY,
    TODAY,
    at,
    make_model,
    snapshot,
    task,
    with_tasks,
)

from projectflow.core.planning.layout import Density
from projectflow.core.planning.strip import WEEKS_BACK
from projectflow.core.planning.walltime import wall_date
from projectflow.ui.planning.grid import FREE_STEP, WHEEL_NOTCH, PlanningGrid
from projectflow.ui.planning.grid_model import NIGHT_WINDOW, GridModel, build_grid_model
from projectflow.ui.planning.planning_tab import PlanningTab
from projectflow.ui.planning.printing import printable_model

NEXT_MONDAY = MONDAY + timedelta(days=7)
PREVIOUS_MONDAY = MONDAY - timedelta(days=7)
FRIDAY = MONDAY + timedelta(days=4)


def strip_model(grid: PlanningGrid, **options: Any) -> GridModel:
    """Model on the strip of pages laid out around ``MONDAY`` (as the tab builds it)."""
    return make_model(grid, **{"origin": MONDAY, **options})


@pytest.fixture
def grid(qtbot: Any) -> PlanningGrid:
    widget = PlanningGrid()
    qtbot.addWidget(widget)
    widget.resize(1200, 640)
    widget.show()
    qtbot.waitExposed(widget)
    widget.slide_ms = 0  # Slides land at once; the animation has its own tests.
    widget.set_model(strip_model(widget))
    return widget


def wheel(grid: PlanningGrid, pages: int = 1) -> None:
    """Scroll a touchpad by ``pages`` page widths (back in time when negative)."""
    assert grid.model is not None
    strip = grid.model.strip
    assert strip is not None
    for _ in range(abs(pages)):
        page = strip.middle_page(float(grid.scroll_x))
        width = round(strip.width_of(page if pages > 0 else page - 1))
        grid.scroll_sideways(0, -width if pages > 0 else width)


def pages_told(grid: PlanningGrid) -> list[date]:
    told: list[date] = []
    grid.page_changed.connect(told.append)
    return told


def rest_of(grid: PlanningGrid, day: date) -> int:
    assert grid.model is not None
    strip = grid.model.strip
    assert strip is not None
    index = strip.index_of(day)
    assert index is not None
    return strip.rest(index)


# ------------------------------------------------------------------ model ----


def test_the_model_draws_the_page_and_its_two_neighbours(grid: PlanningGrid) -> None:
    model = grid.model
    assert model is not None
    strip = model.strip
    assert strip is not None

    assert model.page == WEEKS_BACK
    assert (model.first_day, model.last_day) == (MONDAY, MONDAY + timedelta(days=6))
    assert model.timescale.first_day == PREVIOUS_MONDAY
    assert model.timescale.last_day == NEXT_MONDAY + timedelta(days=4)
    assert len(model.timescale.columns) == 15
    # The axis sits at its place on the strip, which the body scrolls over entirely.
    assert model.timescale.left == pytest.approx(strip.left_of(WEEKS_BACK - 1))
    assert model.timescale.x_of(at(MONDAY, 6, 30)) == pytest.approx(strip.left_of(WEEKS_BACK))
    assert model.scene_width == pytest.approx(strip.total)
    assert grid.scene.sceneRect().width() == pytest.approx(strip.total)
    assert model.period_label.startswith("Semaine 44")


def test_a_week_page_is_as_wide_as_the_viewport(grid: PlanningGrid) -> None:
    model = grid.model
    assert model is not None
    assert model.strip is not None

    assert model.strip.fits
    assert model.strip.width_of(model.page) == pytest.approx(grid.viewport_width())
    assert model.timescale.day_width == pytest.approx(grid.viewport_width() / 5)


def test_rows_are_as_high_as_the_page_stacking_the_most_bars(grid: PlanningGrid) -> None:
    busy = with_tasks(
        snapshot(),
        task("n1", at(NEXT_MONDAY, 6, 30), at(NEXT_MONDAY, 16, 30), ["mr"]),
        task("n2", at(NEXT_MONDAY, 8), at(NEXT_MONDAY, 12), ["mr"]),
        task("n3", at(NEXT_MONDAY, 9), at(NEXT_MONDAY, 11), ["mr"]),
    )
    here = strip_model(grid, snapshot=busy)
    alone = make_model(grid, snapshot=busy)

    # This week « mr » holds one bar; next week three overlap: its row is ready for them,
    # so that the rows line up when the next page slides in.
    assert alone.rows.get("mr").lane_count == 1  # type: ignore[union-attr]
    assert here.rows.get("mr").lane_count == 3  # type: ignore[union-attr]
    assert here.rows.get("mr").height == 3 * Density.NORMAL  # type: ignore[union-attr]
    there = strip_model(grid, snapshot=busy, anchor=NEXT_MONDAY)
    assert there.rows.get("mr").height == here.rows.get("mr").height  # type: ignore[union-attr]


def test_load_and_counts_are_those_of_the_page_named(grid: PlanningGrid) -> None:
    busy = with_tasks(
        snapshot(),
        task("next", at(NEXT_MONDAY, 6, 30), at(NEXT_MONDAY, 16, 30), ["mr"]),
        task("wait", at(NEXT_MONDAY, 6, 30), at(NEXT_MONDAY, 12), []),
    )
    here = strip_model(grid, snapshot=busy)
    alone = make_model(grid, snapshot=busy)

    assert {bar.task_id for bar in here.bars} >= {"next", "wait", "t1"}
    assert {bar.task_id for bar in here.page_bars} == {bar.task_id for bar in alone.bars}
    label, single = here.labels["mr"], alone.labels["mr"]
    assert (label.planned_minutes, label.available_minutes) == (
        single.planned_minutes,
        single.available_minutes,
    )
    assert label.available_minutes == 2490
    assert here.labels["__unassigned__"].count == alone.labels["__unassigned__"].count == 1


def test_a_date_beyond_the_strip_lays_it_out_again_around_it(grid: PlanningGrid) -> None:
    far = MONDAY + timedelta(weeks=300)
    model = strip_model(grid, anchor=far)

    assert model.strip is not None
    assert model.strip.origin == far
    assert model.page == WEEKS_BACK
    assert model.first_day == far


def test_a_weekend_day_opens_the_day_view_on_monday(grid: PlanningGrid) -> None:
    saturday = MONDAY + timedelta(days=5)
    model = strip_model(grid, view="day", anchor=saturday)

    assert model.strip is not None
    assert model.strip.pages[model.page].first_day == NEXT_MONDAY
    assert [column.day for column in model.timescale.columns] == [
        FRIDAY,
        NEXT_MONDAY,
        NEXT_MONDAY + timedelta(days=1),
    ]


def test_the_day_of_a_night_keeps_its_own_24_hour_axis(grid: PlanningGrid) -> None:
    tuesday = MONDAY + timedelta(days=1)
    night = with_tasks(
        snapshot(), task("night", at(MONDAY, 21), at(tuesday, 5), ["jd"], off_hours=True)
    )
    model = strip_model(grid, view="day", anchor=MONDAY, snapshot=night)
    scale = model.timescale

    assert [column.day for column in scale.columns] == [MONDAY - timedelta(days=3), MONDAY, tuesday]
    assert scale.window_of(MONDAY) == NIGHT_WINDOW
    assert scale.window_of(tuesday) == NIGHT_WINDOW
    assert scale.window_of(MONDAY - timedelta(days=3)) == (390, 990)
    # Every day page has the same width, whatever hours it draws.
    assert {round(column.width, 3) for column in scale.columns} == {round(scale.day_width, 3)}
    bar = model.bar("night", "jd")
    assert bar is not None
    assert model.night_shown(bar.start, bar.end)
    left, right = scale.span_x(*model.display_span(bar.start, bar.end, night=True))
    assert left == pytest.approx(scale.x_of(at(MONDAY, 21)))
    assert right == pytest.approx(scale.x_of(at(tuesday, 5)))
    # The Friday before knows nothing of that night.
    friday = strip_model(grid, view="day", anchor=MONDAY - timedelta(days=3), snapshot=night)
    assert friday.timescale.window_of(MONDAY - timedelta(days=3)) == (390, 990)
    assert friday.timescale.window_of(MONDAY) == NIGHT_WINDOW


def test_a_print_keeps_the_page_named_alone(grid: PlanningGrid) -> None:
    busy = with_tasks(
        snapshot(), task("next", at(NEXT_MONDAY, 6, 30), at(NEXT_MONDAY, 16, 30), ["mr"])
    )
    printed = printable_model(strip_model(grid, snapshot=busy), 1000.0)

    assert printed.strip is None
    assert (printed.timescale.first_day, printed.timescale.last_day) == (MONDAY, FRIDAY)
    assert printed.timescale.left == 0.0
    assert "next" not in {bar.task_id for bar in printed.bars}
    assert {bar.task_id for bar in printed.bars} == {"t1", "t2", "pair", "un"}
    assert len(printed.days) == 5


# ------------------------------------------------------------------- grid ----


def test_the_grid_opens_on_the_page_of_its_model(grid: PlanningGrid) -> None:
    # Not at the left end of the strip, a year earlier.
    assert grid.scroll_x == rest_of(grid, MONDAY) > 0
    assert grid.view.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert grid.visible_range() == (MONDAY, FRIDAY)


def test_a_grid_shown_after_its_model_still_opens_on_the_page(qtbot: Any) -> None:
    widget = PlanningGrid()
    qtbot.addWidget(widget)
    widget.slide_ms = 0
    widget.set_model(strip_model(widget))  # Built before the grid has its size.
    widget.resize(1200, 640)
    widget.show()
    qtbot.waitExposed(widget)

    qtbot.waitUntil(lambda: widget.model is not None and widget.model.viewport_width > 900)
    assert widget.scroll_x == rest_of(widget, MONDAY)
    assert widget.visible_range() == (MONDAY, FRIDAY)


def test_a_notch_scrolls_freely_and_nothing_pulls_back(qtbot: Any, grid: PlanningGrid) -> None:
    told = pages_told(grid)
    rest = rest_of(grid, MONDAY)

    grid.scroll_sideways(-WHEEL_NOTCH)
    # No resistance: a notch scrolls its pixels, and the page edge does not call it back.
    assert grid.scroll_x == rest + FREE_STEP
    assert told == []
    qtbot.wait(500)
    assert grid.scroll_x == rest + FREE_STEP

    grid.scroll_sideways(WHEEL_NOTCH)
    assert grid.scroll_x == rest


def test_the_wheel_runs_on_across_the_pages(grid: PlanningGrid) -> None:
    told = pages_told(grid)
    start = grid.scroll_x
    notches = round(grid.viewport_width() / FREE_STEP) + 2

    for _ in range(notches):
        grid.scroll_sideways(-WHEEL_NOTCH)

    # Past the end of the week, without stopping at its edge.
    assert grid.scroll_x == start + notches * FREE_STEP
    assert grid.scroll_x not in {rest_of(grid, MONDAY), rest_of(grid, NEXT_MONDAY)}
    assert told == [NEXT_MONDAY]


def test_the_wheel_turns_back_as_it_turns_ahead(grid: PlanningGrid) -> None:
    told = pages_told(grid)

    wheel(grid)
    wheel(grid, -2)

    assert grid.scroll_x == rest_of(grid, PREVIOUS_MONDAY)
    assert told == [NEXT_MONDAY, MONDAY, PREVIOUS_MONDAY]


def test_a_model_following_the_scroll_never_moves_it(grid: PlanningGrid) -> None:
    told = pages_told(grid)
    wheel(grid)
    there = grid.scroll_x

    # The owner rebuilds for the page told; a model for the page before may still arrive.
    grid.set_model(strip_model(grid, anchor=MONDAY))
    assert grid.scroll_x == there
    grid.set_model(strip_model(grid, anchor=NEXT_MONDAY))
    assert grid.scroll_x == there
    assert grid.model is not None
    assert grid.model.timescale.last_day == NEXT_MONDAY + timedelta(days=11)
    assert told == [NEXT_MONDAY, NEXT_MONDAY][: len(told)]
    assert told[-1] == NEXT_MONDAY


def test_a_touchpad_is_followed_to_the_pixel(grid: PlanningGrid) -> None:
    start = grid.scroll_x

    for _ in range(4):
        grid.scroll_sideways(-20)  # A smooth wheel: a sixth of a notch at a time.
    grid.scroll_sideways(0, -37)  # Pixels of a touchpad, the other kind of delta.

    assert grid.scroll_x == start + 4 * round(20 / WHEEL_NOTCH * FREE_STEP) + 37


def test_an_asked_page_next_door_slides_into_place(qtbot: Any, grid: PlanningGrid) -> None:
    told = pages_told(grid)
    grid.slide_ms = 120

    grid.set_model(strip_model(grid, anchor=NEXT_MONDAY), asked=True)
    assert grid.sliding
    assert grid.scroll_x != rest_of(grid, NEXT_MONDAY)
    qtbot.waitUntil(lambda: not grid.sliding, timeout=3000)

    assert grid.scroll_x == rest_of(grid, NEXT_MONDAY)
    assert told == []  # An asked page is not told back to who asked for it.


def test_an_asked_page_far_away_is_shown_at_once(grid: PlanningGrid) -> None:
    grid.slide_ms = 120
    far = MONDAY + timedelta(weeks=20)

    grid.set_model(strip_model(grid, anchor=far), asked=True)

    assert not grid.sliding
    assert grid.scroll_x == rest_of(grid, far)
    assert grid.visible_range() == (far, far + timedelta(days=4))


def test_a_wheel_slide_is_animated_and_told_on_the_way(qtbot: Any, grid: PlanningGrid) -> None:
    told = pages_told(grid)
    grid.slide_ms = 120
    start = grid.scroll_x
    notches = round(grid.viewport_width() / FREE_STEP) + 1

    for _ in range(notches):
        grid.scroll_sideways(-WHEEL_NOTCH)
    assert grid.sliding
    # A model arriving while the strip slides waits for it to land.
    waiting = strip_model(grid, anchor=NEXT_MONDAY)
    qtbot.waitUntil(lambda: bool(told), timeout=3000)
    grid.set_model(waiting)
    qtbot.waitUntil(lambda: not grid.sliding, timeout=3000)

    assert told == [NEXT_MONDAY]
    assert grid.scroll_x == start + notches * FREE_STEP
    assert grid.model is not None
    assert grid.model.first_day == NEXT_MONDAY


def test_zoomed_pages_scroll_freely_across_their_edges(grid: PlanningGrid) -> None:
    grid.set_model(strip_model(grid, zoom=2.0))
    model = grid.model
    assert model is not None
    assert model.strip is not None
    told = pages_told(grid)
    start = grid.scroll_x

    assert not model.strip.fits
    assert start == model.strip.rest(model.page)
    grid.scroll_sideways(-WHEEL_NOTCH)
    step = grid.scroll_x - start
    assert step == FREE_STEP
    assert grid.scroll_x not in {model.strip.rest(model.page), model.strip.rest(model.page + 1)}
    assert told == []
    # On it goes, past the end of the week, without stopping there.
    for _ in range(15):
        grid.scroll_sideways(-WHEEL_NOTCH)
    assert grid.scroll_x == start + 16 * step
    assert grid.scroll_x + grid.viewport_width() / 2 > model.strip.left_of(model.page + 1)
    assert told == [NEXT_MONDAY]
    grid.scroll_sideways(0, 37)  # A touchpad is followed to the pixel.
    assert grid.scroll_x == start + 16 * step - 37
    grid.scroll_sideways(-30)  # A smooth wheel too, a quarter of a notch at a time.
    assert grid.scroll_x == start + 16 * step - 37 + FREE_STEP // 4


def test_a_zoom_keeps_the_instant_under_the_pointer(grid: PlanningGrid) -> None:
    model = grid.model
    assert model is not None
    pointer = 700.0
    before = model.timescale.time_at(grid.scroll_x + pointer)

    grid.keep_point(pointer)
    grid.set_model(strip_model(grid, zoom=2.5))

    zoomed = grid.model
    assert zoomed is not None
    after = zoomed.timescale.time_at(grid.scroll_x + pointer)
    assert abs(after - before) <= 2
    assert MONDAY < wall_date(before) <= FRIDAY


def test_back_to_a_zoom_where_a_page_fits_nothing_snaps(grid: PlanningGrid) -> None:
    grid.set_model(strip_model(grid, zoom=2.0))
    for _ in range(3):
        grid.scroll_sideways(-WHEEL_NOTCH)
    model = grid.model
    assert model is not None
    pointer = 600.0
    before = model.timescale.time_at(grid.scroll_x + pointer)

    grid.keep_point(pointer)
    grid.set_model(strip_model(grid, zoom=1.0))

    fitted = grid.model
    assert fitted is not None
    assert fitted.strip is not None
    assert fitted.strip.fits
    # The instant under the pointer stays there: no page edge pulls the strip.
    assert abs(fitted.timescale.time_at(grid.scroll_x + pointer) - before) <= 3
    assert grid.scroll_x not in fitted.strip.rests()


def test_another_view_opens_on_the_page_of_its_model(grid: PlanningGrid) -> None:
    told = pages_told(grid)

    grid.set_model(strip_model(grid, view="month", anchor=date(2026, 10, 1)))

    model = grid.model
    assert model is not None
    assert model.strip is not None
    assert model.strip.view == "month"
    assert grid.scroll_x == model.strip.rest(model.page)
    assert told == []  # Never a page read with the pages of the view before.
    grid.set_model(strip_model(grid, view="day", anchor=MONDAY))
    assert grid.scroll_x == rest_of(grid, MONDAY)
    assert grid.visible_range() == (MONDAY, MONDAY)
    assert told == []


def test_the_month_fits_and_rests_month_by_month(grid: PlanningGrid) -> None:
    grid.set_model(strip_model(grid, view="month", anchor=date(2026, 10, 1)))
    model = grid.model
    assert model is not None
    assert model.strip is not None
    told = pages_told(grid)

    assert model.strip.fits
    assert model.strip.width_of(model.page) <= grid.viewport_width()
    wheel(grid)
    assert grid.scroll_x == model.strip.rest(model.page + 1)
    # November 2026 begins on a Sunday: it is named by Monday the 2nd.
    assert told == [date(2026, 11, 2)]


def test_a_new_viewport_width_keeps_the_page_in_place(qtbot: Any, grid: PlanningGrid) -> None:
    wheel(grid)

    grid.resize(900, 640)
    qtbot.waitUntil(lambda: grid.model is not None and grid.model.viewport_width < 800)

    assert grid.model is not None
    assert grid.model.strip is not None
    assert grid.model.strip.viewport == pytest.approx(grid.viewport_width())
    assert grid.scroll_x == rest_of(grid, NEXT_MONDAY)


def test_dragging_the_header_leaves_the_strip_where_it_is_let_go(grid: PlanningGrid) -> None:
    told = pages_told(grid)
    start = grid.scroll_x

    grid.pan_start(800.0)
    grid.pan_move(500.0)
    assert grid.panning
    assert grid.scroll_x == start + 300
    grid.pan_end()
    assert not grid.panning
    assert grid.scroll_x == start + 300
    # However far it is dragged, the strip follows.
    grid.pan_start(100.0)
    grid.pan_move(1100.0)
    grid.pan_move(1700.0)
    grid.pan_end()
    assert grid.scroll_x == start + 300 - 1600
    assert told[-1] == PREVIOUS_MONDAY


def test_the_header_is_dragged_or_clicked(qtbot: Any, grid: PlanningGrid) -> None:
    days: list[date] = []
    grid.day_clicked.connect(days.append)
    header = grid.header
    start = grid.scroll_x

    QTest.mouseClick(header, Qt.MouseButton.LeftButton, pos=QPoint(40, 30))
    assert days == [MONDAY]
    QTest.mousePress(header, Qt.MouseButton.LeftButton, pos=QPoint(700, 30))
    QTest.mouseMove(header, QPoint(500, 30))
    QTest.mouseMove(header, QPoint(300, 30))
    assert grid.scroll_x == start + 400
    QTest.mouseRelease(header, Qt.MouseButton.LeftButton, pos=QPoint(300, 30))
    assert days == [MONDAY]  # A drag is not a click.
    assert grid.scroll_x == start + 400


def test_the_wheel_over_the_days_scrolls_in_time(grid: PlanningGrid) -> None:
    start = grid.scroll_x

    grid.header.wheel_scrolled.emit(-2 * WHEEL_NOTCH, 0)

    assert grid.scroll_x == start + 2 * FREE_STEP


def test_the_header_follows_the_body_without_shift(grid: PlanningGrid) -> None:
    grid.set_model(strip_model(grid, zoom=2.0))
    grid.scroll_sideways(0, -333)

    assert grid.header.x0 == grid.scroll_x
    assert grid.header.day_at(10.0) == grid.model.timescale.column_at(grid.scroll_x + 10.0).day  # type: ignore[union-attr]


def test_a_bar_is_placed_on_the_strip_and_still_moves(qtbot: Any, grid: PlanningGrid) -> None:
    moves: list[tuple[Any, ...]] = []
    grid.move_requested.connect(lambda *args: moves.append(args))
    item = grid.scene.items_by_key[("t1", "mr")]
    model = grid.model
    assert model is not None

    assert item.scene_rect().left() >= model.timescale.x_of(at(MONDAY, 6, 30))
    centre = grid.view.mapFromScene(item.scene_rect().center())
    assert 0 <= centre.x() <= grid.viewport_width()
    target = centre + QPoint(round(model.timescale.day_width), 0)
    viewport = grid.view.viewport()
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=centre)
    QTest.mouseMove(viewport, centre + QPoint(20, 0))
    QTest.mouseMove(viewport, target)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=target)

    assert moves == [("t1", "mr", "mr", at(MONDAY + timedelta(days=1), 6, 30), "move")]


# -------------------------------------------------------------------- tab ----


@pytest.fixture
def tab(qtbot: Any) -> PlanningTab:
    """Give a tab redrawn as the controller does it: one model per change of view."""
    widget = PlanningTab()
    qtbot.addWidget(widget)
    widget.resize(1440, 860)
    widget.set_today(TODAY)
    widget.show()
    qtbot.waitExposed(widget)
    widget.grid.slide_ms = 0

    def rebuild() -> None:
        widget.set_model(
            build_grid_model(
                snapshot(),
                view=widget.view,
                anchor=widget.anchor,
                viewport_width=widget.grid.viewport_width(),
                today=TODAY,
                now=at(TODAY, 10),
                density=widget.row_height,
                zoom=widget.zoom,
                origin=widget.origin,
            )
        )

    widget.view_changed.connect(rebuild)
    rebuild()
    return widget


THIS_WEEK = TODAY - timedelta(days=TODAY.weekday())


def test_the_title_names_the_page_under_the_middle(tab: PlanningTab) -> None:
    grid = tab.grid
    assert tab.anchor == THIS_WEEK
    assert grid.scroll_x == rest_of(grid, THIS_WEEK)
    title = tab.period_button.toolTip()

    wheel(grid)

    assert tab.anchor == THIS_WEEK + timedelta(days=7)
    assert tab.period_button.toolTip() != title
    assert "Semaine 44" in tab.status_texts()[0] or "44" in tab.period_button.toolTip()
    # The model that followed the scroll did not pull the grid back.
    assert grid.scroll_x == rest_of(grid, THIS_WEEK + timedelta(days=7))
    assert grid.model is not None
    assert grid.model.first_day == THIS_WEEK + timedelta(days=7)


def test_arrows_and_today_ask_for_their_page(tab: PlanningTab) -> None:
    grid = tab.grid

    tab.step(1)
    assert tab.anchor == THIS_WEEK + timedelta(days=7)
    assert grid.scroll_x == rest_of(grid, tab.anchor)
    tab.step(-3)
    assert grid.scroll_x == rest_of(grid, THIS_WEEK - timedelta(days=14))
    tab.go_today()
    assert tab.anchor == THIS_WEEK
    assert grid.scroll_x == rest_of(grid, THIS_WEEK)


def test_a_click_on_a_view_button_recentres_the_view(tab: PlanningTab) -> None:
    grid = tab.grid
    sizes: list[bool] = []
    tab.display_size_changed.connect(lambda: sizes.append(True))
    tab.set_zoom(2.5)
    for _ in range(4):
        grid.scroll_sideways(-WHEEL_NOTCH)
    assert grid.scroll_x != rest_of(grid, tab.anchor)
    sizes.clear()

    tab.view_buttons["week"].click()  # The view already shown.

    assert tab.view == "week"
    assert tab.zoom == 1.0
    assert tab.zooms() == {}
    assert sizes == [True]
    assert grid.model is not None
    assert grid.model.strip is not None
    assert grid.model.strip.fits
    assert grid.scroll_x == rest_of(grid, tab.anchor)
    assert grid.visible_range() == (tab.anchor, tab.anchor + timedelta(days=4))


def test_a_view_button_gives_the_other_view_at_its_default_zoom(tab: PlanningTab) -> None:
    grid = tab.grid
    tab.set_view("day")
    tab.set_zoom(3.0)
    tab.set_view("week")
    assert tab.zooms() == {"day": 3.0}

    tab.view_buttons["day"].click()

    assert (tab.view, tab.zoom) == ("day", 1.0)
    assert tab.anchor == TODAY
    assert grid.scroll_x == rest_of(grid, TODAY)
    assert grid.visible_range() == (TODAY, TODAY)


def test_ctrl_wheel_zooms_about_the_pointer_and_frees_the_scroll(tab: PlanningTab) -> None:
    grid = tab.grid
    model = grid.model
    assert model is not None
    pointer = 500.0
    before = model.timescale.time_at(grid.scroll_x + pointer)

    grid.zoom_requested.emit(3, pointer)

    zoomed = grid.model
    assert zoomed is not None
    assert zoomed.strip is not None
    assert tab.zoom > 1.0
    assert not zoomed.strip.fits
    assert abs(zoomed.timescale.time_at(grid.scroll_x + pointer) - before) <= 3


def test_a_date_beyond_the_strip_is_shown_on_a_strip_laid_out_again(tab: PlanningTab) -> None:
    far = THIS_WEEK + timedelta(weeks=400)

    tab.set_anchor(far + timedelta(days=2))

    assert tab.anchor == far
    assert tab.origin == far
    assert tab.grid.scroll_x == rest_of(tab.grid, far)
    assert tab.grid.visible_range() == (far, far + timedelta(days=4))


def test_the_status_bar_counts_the_bars_of_the_page(tab: PlanningTab) -> None:
    tab.set_anchor(MONDAY)

    # « pair » is drawn on two rows: five bars for four tasks.
    assert "5 barres" in tab._period_summary()  # noqa: SLF001
    tab.step(1)
    assert "0 barres" in tab._period_summary()  # noqa: SLF001


# ------------------------------------------------------------- controller ----


@pytest.mark.asyncio
async def test_the_planning_scrolls_with_its_controller(qtbot: Any, tmp_path: Path) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    tab.grid.slide_ms = 0
    grid = tab.grid
    here = tab.anchor
    assert grid.model is not None
    assert grid.model.strip is not None
    assert grid.scroll_x == rest_of(grid, here) > 0

    wheel(grid)
    assert tab.anchor == here + timedelta(days=7)
    controller.refresh_now()
    assert grid.model.first_day == here + timedelta(days=7)
    assert grid.scroll_x == rest_of(grid, tab.anchor)
    tab.step(-1)
    controller.refresh_now()
    assert grid.scroll_x == rest_of(grid, here)
    await controller.aclose()
