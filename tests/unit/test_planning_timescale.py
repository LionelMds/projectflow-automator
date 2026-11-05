from __future__ import annotations

from datetime import date, timedelta
from itertools import pairwise
from typing import Any

import pytest

from projectflow.core.planning.calendar import WorkCalendar
from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.commands import CommandContext, calendar_resolver, move_task
from projectflow.core.planning.models import (
    Author,
    Closure,
    PlanningMeta,
    PlanningTask,
    Resource,
)
from projectflow.core.planning.timescale import (
    VIEW_KINDS,
    TimeScale,
    ViewKind,
    day_label,
    half_day_end_edges,
    half_day_start_edges,
    iso_week_label,
    month_label,
    month_range,
    move_preserving_work,
    shift_view,
    shown_day,
    shown_days,
    snap_end,
    snap_start,
    view_range,
    week_start,
)
from projectflow.core.planning.walltime import (
    MINUTES_PER_DAY,
    day_start,
    format_wall_time,
    minute_of_day,
    wall_time,
)

MON = date(2026, 10, 26)
TUE = date(2026, 10, 27)
WED = date(2026, 10, 28)
THU = date(2026, 10, 29)
FRI = date(2026, 10, 30)
SAT = date(2026, 10, 31)
SUN = date(2026, 11, 1)
NEXT_MON = date(2026, 11, 2)
NEXT_FRI = date(2026, 11, 6)
PREVIOUS_FRI = date(2026, 10, 23)
MAUNDY_THU = date(2026, 4, 2)
GOOD_FRIDAY = date(2026, 4, 3)
EASTER_MONDAY = date(2026, 4, 6)
EASTER_TUE = date(2026, 4, 7)
JEUNE_GENEVOIS = date(2026, 9, 10)
LUNDI_DU_JEUNE = date(2026, 9, 21)

GE = WorkCalendar()
WIDTH = 120.0


def at(day: date, text: str) -> int:
    hours, minutes = (int(part) for part in text.split(":"))
    return wall_time(day, hours, minutes)


def show(value: int) -> str:
    return format_wall_time(value)


def case_id(value: object) -> str:
    if isinstance(value, int) and value >= MINUTES_PER_DAY:
        return format_wall_time(value)
    return str(value)


def week_scale(day_width: float = WIDTH, calendar: WorkCalendar = GE) -> TimeScale:
    return TimeScale(calendar, MON, SUN, day_width)


# ---------------------------------------------------------------- columns ----


def test_week_shows_monday_to_friday_only() -> None:
    scale = week_scale()

    assert [column.day for column in scale.columns] == [MON, TUE, WED, THU, FRI]
    assert [column.index for column in scale.columns] == [0, 1, 2, 3, 4]
    assert [column.x for column in scale.columns] == [0.0, 120.0, 240.0, 360.0, 480.0]
    assert all(column.width == WIDTH for column in scale.columns)
    assert scale.columns[0].right == scale.columns[1].x
    assert scale.width == 600.0
    assert scale.display_minutes == 600
    assert scale.minute_width == pytest.approx(0.2)
    assert scale.first_day == MON
    assert scale.last_day == FRI
    assert scale.start == at(MON, "06:30")
    assert scale.end == at(FRI, "16:30")


def test_range_bounds_on_a_weekend_start_and_end_on_shown_days() -> None:
    scale = TimeScale(GE, date(2026, 10, 24), SAT, WIDTH)

    assert scale.first_day == MON
    assert scale.last_day == FRI
    assert shown_days(GE, SAT, SUN) == []


def test_friday_is_shown_with_its_morning_only_worked() -> None:
    friday = week_scale().columns[4]

    assert friday.workday
    assert friday.holiday is None
    assert not friday.closed
    assert friday.periods == ((at(FRI, "06:30"), at(FRI, "12:00")),)
    assert week_scale().columns[0].periods == (
        (at(MON, "06:30"), at(MON, "12:00")),
        (at(MON, "13:00"), at(MON, "16:30")),
    )


def test_holidays_are_shown_but_not_worked() -> None:
    scale = TimeScale(GE, date(2026, 3, 30), EASTER_TUE, WIDTH)
    by_day = {column.day: column for column in scale.columns}

    assert list(by_day) == [
        date(2026, 3, 30),
        date(2026, 3, 31),
        date(2026, 4, 1),
        MAUNDY_THU,
        GOOD_FRIDAY,
        EASTER_MONDAY,
        EASTER_TUE,
    ]
    assert by_day[GOOD_FRIDAY].holiday == "Vendredi saint"
    assert not by_day[GOOD_FRIDAY].workday
    assert by_day[GOOD_FRIDAY].periods == ()
    assert by_day[EASTER_MONDAY].holiday == "Lundi de Pâques"
    assert by_day[MAUNDY_THU].workday
    assert by_day[MAUNDY_THU].holiday is None
    assert scale.x_of(at(GOOD_FRIDAY, "09:00")) == pytest.approx(4 * WIDTH + 150 * 0.2)


def test_holidays_follow_the_calendar_cantons() -> None:
    first, last = date(2026, 12, 28), date(2027, 1, 8)
    geneva = {column.day: column.holiday for column in TimeScale(GE, first, last, WIDTH).columns}
    vaud = {
        column.day: column.holiday
        for column in TimeScale(WorkCalendar(cantons=("VD",)), first, last, WIDTH).columns
    }

    assert geneva[date(2026, 12, 31)] == "Restauration de la République"
    assert vaud[date(2026, 12, 31)] is None
    assert geneva[date(2027, 1, 1)] == vaud[date(2027, 1, 1)] == "Nouvel An"
    # 2 January 2027 is a Saturday: not shown even in Vaud.
    assert date(2027, 1, 2) not in vaud


def test_closures_are_shown_flagged_closed() -> None:
    scale = week_scale(calendar=WorkCalendar(closures=[TUE]))
    tuesday = scale.columns[1]

    assert tuesday.closed
    assert not tuesday.workday
    assert tuesday.holiday is None
    assert tuesday.periods == ()


@pytest.mark.parametrize(
    ("first", "last", "day_width"),
    [(SAT, SUN, WIDTH), (TUE, MON, WIDTH), (MON, FRI, 0.0), (MON, FRI, -5.0)],
)
def test_invalid_scales_are_refused(first: date, last: date, day_width: float) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        TimeScale(GE, first, last, day_width)


def test_columns_by_day() -> None:
    scale = week_scale()

    assert scale.index_of(THU) == 3
    assert scale.index_of(SAT) is None
    column = scale.column_of(WED)
    assert column is not None
    assert column.x == 240.0
    assert scale.column_of(NEXT_MON) is None


# -------------------------------------------------------------- positions ----


@pytest.mark.parametrize(
    ("value", "x"),
    [
        (at(MON, "06:30"), 0.0),
        (at(MON, "11:30"), 60.0),
        (at(MON, "12:30"), 72.0),
        (at(MON, "05:00"), 0.0),
        (day_start(MON), 0.0),
        (at(MON, "16:30"), 120.0),
        (at(MON, "20:00"), 120.0),
        (day_start(TUE), 120.0),
        (at(TUE, "06:30"), 120.0),
        (at(FRI, "12:00"), 546.0),
        (at(FRI, "14:00"), 570.0),
        (at(FRI, "16:30"), 600.0),
        (at(SAT, "10:00"), 600.0),
        (at(SUN, "23:00"), 600.0),
        (at(date(2026, 10, 20), "10:00"), 0.0),
        (at(date(2026, 11, 10), "10:00"), 600.0),
    ],
    ids=case_id,
)
def test_x_of_collapses_nights_and_weekends_onto_day_edges(value: int, x: float) -> None:
    assert week_scale().x_of(value) == pytest.approx(x)


def test_weekend_collapses_onto_the_line_between_friday_and_monday() -> None:
    scale = TimeScale(GE, MON, NEXT_FRI, WIDTH)

    assert scale.x_of(at(FRI, "16:30")) == 600.0
    assert scale.x_of(at(SAT, "12:00")) == 600.0
    assert scale.x_of(at(SUN, "12:00")) == 600.0
    assert scale.x_of(at(NEXT_MON, "06:30")) == 600.0
    assert scale.x_of(at(NEXT_MON, "07:00")) == pytest.approx(606.0)
    assert scale.span_x(at(FRI, "06:30"), at(NEXT_MON, "12:00")) == (480.0, 666.0)


@pytest.mark.parametrize(
    ("x", "start", "end"),
    [
        (0.0, at(MON, "06:30"), at(MON, "06:30")),
        (60.0, at(MON, "11:30"), at(MON, "11:30")),
        (0.09, at(MON, "06:30"), at(MON, "06:30")),
        (0.11, at(MON, "06:31"), at(MON, "06:31")),
        (120.0, at(TUE, "06:30"), at(MON, "16:30")),
        (119.95, at(TUE, "06:30"), at(MON, "16:30")),
        (119.85, at(MON, "16:29"), at(MON, "16:29")),
        (480.0, at(FRI, "06:30"), at(THU, "16:30")),
        (570.0, at(FRI, "14:00"), at(FRI, "14:00")),
        (600.0, at(FRI, "16:30"), at(FRI, "16:30")),
        (-50.0, at(MON, "06:30"), at(MON, "06:30")),
        (1000.0, at(FRI, "16:30"), at(FRI, "16:30")),
    ],
)
def test_time_at_reads_a_day_boundary_as_the_previous_evening_for_ends(
    x: float, start: int, end: int
) -> None:
    scale = week_scale()

    assert show(scale.time_at(x)) == show(start)
    assert show(scale.time_at(x, end=True)) == show(end)


def test_time_at_across_the_weekend() -> None:
    scale = TimeScale(GE, MON, NEXT_FRI, WIDTH)

    assert scale.time_at(600.0) == at(NEXT_MON, "06:30")
    assert scale.time_at(600.0, end=True) == at(FRI, "16:30")


@pytest.mark.parametrize("day_width", [120.0, 137.0, 33.3, 1000.0])
def test_x_of_and_time_at_round_trip(day_width: float) -> None:
    scale = TimeScale(GE, MON, NEXT_FRI, day_width)
    for column in scale.columns:
        base = day_start(column.day) + GE.day_start_minute
        for offset in range(0, scale.display_minutes, 10):
            assert scale.time_at(scale.x_of(base + offset)) == base + offset
        for offset in range(10, scale.display_minutes + 1, 10):
            assert scale.time_at(scale.x_of(base + offset), end=True) == base + offset


def test_x_of_is_monotonic() -> None:
    scale = TimeScale(GE, MON, NEXT_FRI, WIDTH)
    values = range(day_start(SUN - timedelta(days=8)), day_start(NEXT_FRI + timedelta(days=3)), 7)
    xs = [scale.x_of(value) for value in values]

    assert xs == sorted(xs)
    assert xs[0] == 0.0
    assert xs[-1] == scale.width


@pytest.mark.parametrize(
    ("left", "right", "days"),
    [
        (0.0, 120.0, [MON]),
        (60.0, 180.0, [MON, TUE]),
        (120.0, 240.0, [TUE]),
        (119.0, 121.0, [MON, TUE]),
        (-100.0, 10_000.0, [MON, TUE, WED, THU, FRI]),
        (300.0, 300.0, []),
        (400.0, 300.0, []),
        (700.0, 800.0, []),
        (-300.0, -10.0, []),
    ],
)
def test_visible_columns(left: float, right: float, days: list[date]) -> None:
    assert [column.day for column in week_scale().visible_columns(left, right)] == days


@pytest.mark.parametrize(
    ("x", "day"),
    [(-5.0, MON), (0.0, MON), (119.99, MON), (120.0, TUE), (599.0, FRI), (600.0, FRI), (900, FRI)],
)
def test_column_at_clamps(x: float, day: date) -> None:
    assert week_scale().column_at(x).day == day


@pytest.mark.parametrize("day_width", [33.3, 47.7, 70.1, 137.0, 0.7])
def test_lookups_agree_with_the_columns_for_fractional_widths(day_width: float) -> None:
    # Regression: floor(x / day_width) put a column's own left edge in the previous column.
    scale = TimeScale(GE, date(2026, 6, 1), date(2026, 10, 31), day_width)
    columns = scale.columns

    for column in columns:
        assert scale.column_at(column.x) is column, column.day
        assert scale.visible_columns(column.x, column.right) == (column,), column.day
        assert scale.time_at(column.x) == at(column.day, "06:30")
        assert scale.x_of(at(column.day, "06:30")) == column.x
        assert scale.column_at(column.x + day_width / 2) is column
    for left, right in pairwise(columns):
        assert left.right == right.x
    assert columns[-1].right == scale.width


# ------------------------------------------------------------ header bands ----


def test_week_groups() -> None:
    groups = TimeScale(GE, MON, NEXT_FRI, WIDTH).week_groups()

    assert [(group.key, group.label, group.columns) for group in groups] == [
        (MON, "Semaine 44", range(5)),
        (NEXT_MON, "Semaine 45", range(5, 10)),
    ]
    assert [(group.x, group.width) for group in groups] == [(0.0, 600.0), (600.0, 600.0)]
    assert TimeScale(GE, MON, FRI, WIDTH).week_groups(short=True)[0].label == "S44"


def test_month_groups_split_a_week() -> None:
    groups = TimeScale(GE, date(2026, 9, 28), date(2026, 10, 2), WIDTH).month_groups()

    assert [(group.key, group.label, group.columns) for group in groups] == [
        (date(2026, 9, 1), "Septembre 2026", range(3)),
        (date(2026, 10, 1), "Octobre 2026", range(3, 5)),
    ]
    assert [(group.x, group.width) for group in groups] == [(0.0, 360.0), (360.0, 240.0)]


# --------------------------------------------------------------- snapping ----


@pytest.mark.parametrize("view", ["day", "week"])
@pytest.mark.parametrize(
    ("value", "start", "end"),
    [
        (at(MON, "08:00"), at(MON, "08:00"), at(MON, "08:00")),
        (at(MON, "08:10"), at(MON, "08:00"), at(MON, "08:00")),
        (at(MON, "08:15"), at(MON, "08:30"), at(MON, "08:30")),
        (at(MON, "11:50"), at(MON, "13:00"), at(MON, "12:00")),
        (at(MON, "12:20"), at(MON, "13:00"), at(MON, "12:00")),
        (at(MON, "12:50"), at(MON, "13:00"), at(MON, "12:00")),
        (at(MON, "16:20"), at(TUE, "06:30"), at(MON, "16:30")),
        (at(MON, "06:40"), at(MON, "06:30"), at(PREVIOUS_FRI, "12:00")),
        (at(MON, "03:00"), at(MON, "06:30"), at(PREVIOUS_FRI, "12:00")),
        (at(MON, "23:50"), at(TUE, "06:30"), at(MON, "16:30")),
        (at(FRI, "11:40"), at(FRI, "11:30"), at(FRI, "11:30")),
        (at(FRI, "12:10"), at(NEXT_MON, "06:30"), at(FRI, "12:00")),
        (at(FRI, "14:40"), at(NEXT_MON, "06:30"), at(FRI, "12:00")),
        (at(SAT, "10:00"), at(NEXT_MON, "06:30"), at(FRI, "12:00")),
        (at(GOOD_FRIDAY, "08:00"), at(EASTER_TUE, "06:30"), at(MAUNDY_THU, "16:30")),
    ],
    ids=case_id,
)
def test_half_hour_snapping(value: int, start: int, end: int, view: ViewKind) -> None:
    assert show(snap_start(GE, value, view)) == show(start)
    assert show(snap_end(GE, value, view)) == show(end)


@pytest.mark.parametrize(
    ("value", "start", "end"),
    [
        (at(MON, "08:00"), at(MON, "06:30"), at(PREVIOUS_FRI, "12:00")),
        (at(MON, "09:40"), at(MON, "06:30"), at(MON, "12:00")),
        # Halfway between 06:30 and 13:00 goes to the later edge.
        (at(MON, "09:45"), at(MON, "13:00"), at(MON, "12:00")),
        (at(MON, "10:00"), at(MON, "13:00"), at(MON, "12:00")),
        (at(MON, "14:00"), at(MON, "13:00"), at(MON, "12:00")),
        (at(MON, "15:00"), at(TUE, "06:30"), at(MON, "16:30")),
        (at(MON, "04:00"), at(MON, "06:30"), at(PREVIOUS_FRI, "12:00")),
        (at(MON, "21:00"), at(TUE, "06:30"), at(MON, "16:30")),
        (at(FRI, "08:00"), at(FRI, "06:30"), at(THU, "16:30")),
        (at(FRI, "10:00"), at(NEXT_MON, "06:30"), at(FRI, "12:00")),
        (at(FRI, "14:00"), at(NEXT_MON, "06:30"), at(FRI, "12:00")),
        (at(FRI, "15:00"), at(NEXT_MON, "06:30"), at(FRI, "12:00")),
        (at(GOOD_FRIDAY, "10:00"), at(EASTER_TUE, "06:30"), at(MAUNDY_THU, "16:30")),
    ],
    ids=case_id,
)
def test_month_view_snaps_to_half_days(value: int, start: int, end: int) -> None:
    assert show(snap_start(GE, value, "month")) == show(start)
    assert show(snap_end(GE, value, "month")) == show(end)


def test_half_day_edges_follow_the_work_week() -> None:
    assert half_day_start_edges(GE) == (390, 780, 990)
    assert half_day_end_edges(GE) == (390, 720, 990)


@pytest.mark.parametrize("view", VIEW_KINDS)
def test_snapped_instants_are_always_worked(view: ViewKind) -> None:
    first = day_start(date(2026, 3, 29))
    for value in range(first, first + 15 * MINUTES_PER_DAY, 10):
        start = snap_start(GE, value, view)
        end = snap_end(GE, value, view)
        assert GE.is_worked(start), show(value)
        assert GE.is_worked(end - 1), show(value)
        assert minute_of_day(start) % 30 == 0
        assert minute_of_day(end) % 30 == 0
        if view == "month":
            assert minute_of_day(start) in {390, 780}
            assert minute_of_day(end) in {720, 990}


def test_scale_snapping_uses_the_given_row_calendar() -> None:
    calendar = WorkCalendar(closures=[TUE])
    scale = week_scale()

    assert scale.snap_start(at(MON, "16:20"), "week", calendar=calendar) == at(WED, "06:30")
    assert scale.snap_end(at(WED, "06:40"), "day", calendar=calendar) == at(MON, "16:30")
    assert scale.snap_start(at(MON, "10:00"), "month", calendar=calendar) == at(MON, "13:00")
    assert scale.snap_start(at(MON, "16:20"), "week", calendar=GE) == at(TUE, "06:30")


def test_a_vaud_row_snaps_with_its_own_holidays_on_a_geneva_axis() -> None:
    # Regression: the axis calendar used to snap every row, ignoring Lundi du Jeûne.
    vaud = WorkCalendar(cantons=("VD",))
    scale = TimeScale(GE, LUNDI_DU_JEUNE, date(2026, 9, 25), WIDTH)

    assert scale.snap_start(at(LUNDI_DU_JEUNE, "08:00"), "week", calendar=vaud) == at(
        LUNDI_DU_JEUNE + timedelta(days=1), "06:30"
    )
    assert scale.snap_start(at(LUNDI_DU_JEUNE, "08:00"), "week", calendar=GE) == at(
        LUNDI_DU_JEUNE, "08:00"
    )
    assert scale.snap_end(at(LUNDI_DU_JEUNE, "10:00"), "month", calendar=vaud) == at(
        date(2026, 9, 18), "12:00"
    )


def test_columns_for_a_row_calendar_keep_positions_and_take_its_days_off() -> None:
    vaud = WorkCalendar(cantons=("VD",))
    first, last = date(2026, 9, 7), date(2026, 9, 25)
    scale = TimeScale(GE, first, last, 33.3)

    rows = scale.columns_for(vaud)
    geneva = {column.day: column for column in scale.columns}
    by_day = {column.day: column for column in rows}

    assert [(c.day, c.index, c.x, c.width) for c in rows] == [
        (c.day, c.index, c.x, c.width) for c in scale.columns
    ]
    assert geneva[JEUNE_GENEVOIS].holiday == "Jeûne genevois"
    assert not geneva[JEUNE_GENEVOIS].workday
    assert by_day[JEUNE_GENEVOIS].holiday is None
    assert by_day[JEUNE_GENEVOIS].workday
    assert by_day[JEUNE_GENEVOIS].periods == vaud.day_periods(JEUNE_GENEVOIS)
    assert geneva[LUNDI_DU_JEUNE].workday
    assert by_day[LUNDI_DU_JEUNE].holiday == "Lundi du Jeûne"
    assert by_day[LUNDI_DU_JEUNE].periods == ()
    assert scale.columns_for(vaud) is rows
    assert scale.columns_for(GE) is scale.columns
    closed = scale.columns_for(WorkCalendar(closures=[first]))
    assert closed[0].closed
    assert not closed[0].workday


def test_unknown_view_is_refused() -> None:
    bad_view: Any = "year"

    with pytest.raises(ValueError, match="Vue de planning inconnue"):
        snap_start(GE, at(MON, "08:00"), bad_view)
    with pytest.raises(ValueError, match="Vue de planning inconnue"):
        snap_end(GE, at(MON, "08:00"), bad_view)
    with pytest.raises(ValueError, match="Vue de planning inconnue"):
        view_range(MON, bad_view)


# ---------------------------------------------------------------- moving ----


@pytest.mark.parametrize(
    ("start", "end", "new_start", "expected"),
    [
        # 540 worked minutes moved onto Friday morning end on Monday morning.
        (
            at(THU, "13:00"),
            at(FRI, "12:00"),
            at(FRI, "06:30"),
            (at(FRI, "06:30"), at(NEXT_MON, "10:00")),
        ),
        (
            at(THU, "13:00"),
            at(FRI, "12:00"),
            at(MON, "12:00"),
            (at(MON, "13:00"), at(TUE, "12:00")),
        ),
        (
            at(MON, "06:30"),
            at(MON, "16:30"),
            at(SAT, "09:00"),
            (at(NEXT_MON, "06:30"), at(NEXT_MON, "16:30")),
        ),
        (
            at(MON, "06:30"),
            at(MON, "16:30"),
            at(MAUNDY_THU, "13:00"),
            (at(MAUNDY_THU, "13:00"), at(EASTER_TUE, "12:00")),
        ),
        (
            at(FRI, "11:00"),
            at(NEXT_MON, "07:00"),
            at(TUE, "16:00"),
            (at(TUE, "16:00"), at(WED, "07:30")),
        ),
        # Without worked minutes, a task keeps half an hour.
        (
            at(SAT, "08:00"),
            at(SAT, "10:00"),
            at(MON, "08:00"),
            (at(MON, "08:00"), at(MON, "08:30")),
        ),
        # A bar on a holiday keeps the weekday hours of that day.
        (
            at(GOOD_FRIDAY, "06:30"),
            at(GOOD_FRIDAY, "12:00"),
            at(MON, "06:30"),
            (at(MON, "06:30"), at(MON, "12:00")),
        ),
        # Worked minutes are rounded up to the half-hour.
        (
            at(MON, "06:30"),
            at(MON, "07:15"),
            at(WED, "08:00"),
            (at(WED, "08:00"), at(WED, "09:00")),
        ),
        # The new start snaps to the half-hour.
        (
            at(MON, "06:30"),
            at(MON, "07:30"),
            at(WED, "08:10"),
            (at(WED, "08:00"), at(WED, "09:00")),
        ),
        (
            at(MON, "06:30"),
            at(MON, "07:30"),
            at(WED, "11:50"),
            (at(WED, "13:00"), at(WED, "14:00")),
        ),
    ],
    ids=case_id,
)
def test_move_preserving_work(
    start: int, end: int, new_start: int, expected: tuple[int, int]
) -> None:
    moved = move_preserving_work(start, end, new_start, GE)

    assert (show(moved[0]), show(moved[1])) == (show(expected[0]), show(expected[1]))


def test_move_of_a_bar_on_a_day_closed_since_keeps_its_weekday_hours() -> None:
    closed = WorkCalendar(closures=[TUE])

    moved = move_preserving_work(at(TUE, "06:30"), at(TUE, "16:30"), at(NEXT_MON, "06:30"), closed)

    assert moved == (at(NEXT_MON, "06:30"), at(NEXT_MON, "16:30"))


def test_move_to_another_row_uses_its_calendar() -> None:
    vaud = WorkCalendar(cantons=("VD",))
    wednesday = JEUNE_GENEVOIS - timedelta(days=1)
    start, end = at(wednesday, "06:30"), at(JEUNE_GENEVOIS + timedelta(days=1), "12:00")

    # 870 worked minutes in Geneva; Vaud works on Jeûne genevois.
    assert move_preserving_work(start, end, start, GE, target=vaud) == (
        start,
        at(JEUNE_GENEVOIS, "12:00"),
    )
    assert move_preserving_work(start, end, at(LUNDI_DU_JEUNE, "08:00"), GE, target=vaud)[0] == at(
        LUNDI_DU_JEUNE + timedelta(days=1), "06:30"
    )


@pytest.mark.parametrize(
    ("start", "end", "new_start", "target"),
    [
        (at(TUE, "06:30"), at(TUE, "16:30"), at(NEXT_MON, "06:30"), "ana"),
        (at(GOOD_FRIDAY, "06:30"), at(GOOD_FRIDAY, "12:00"), at(MON, "06:30"), "ana"),
        (at(MON, "06:30"), at(MON, "07:15"), at(WED, "08:00"), "ana"),
        (at(MON, "06:30"), at(MON, "07:30"), at(WED, "08:10"), "ana"),
        (at(MON, "06:30"), at(MON, "07:30"), at(SAT, "09:20"), "ana"),
        (at(SAT, "08:00"), at(SAT, "10:00"), at(MON, "08:00"), "ana"),
        (at(THU, "09:00"), at(NEXT_MON, "08:00"), at(FRI, "11:40"), "ana"),
        (at(JEUNE_GENEVOIS, "06:30"), at(JEUNE_GENEVOIS, "16:30"), at(WED, "06:30"), "ana"),
        (
            at(JEUNE_GENEVOIS - timedelta(days=1), "06:30"),
            at(JEUNE_GENEVOIS + timedelta(days=1), "12:00"),
            at(JEUNE_GENEVOIS - timedelta(days=1), "06:30"),
            "vera",
        ),
        (at(MON, "06:30"), at(MON, "16:30"), at(LUNDI_DU_JEUNE, "08:00"), "vera"),
    ],
    ids=case_id,
)
def test_move_preview_matches_the_saved_move(
    start: int, end: int, new_start: int, target: str
) -> None:
    # Regression: the drag preview and commands.move_task used to give different spans.
    task = PlanningTask(id="t", title="t", start=start, end=end, assignments={"ana": {}})
    snapshot = PlanningSnapshot(
        tasks={task.id: task},
        resources={
            "ana": Resource(id="ana", name="Ana"),
            "vera": Resource(id="vera", name="Vera", canton="VD"),
        },
        meta=PlanningMeta(closures=(Closure(first_day=TUE, last_day=TUE),)),
    )
    ctx = CommandContext(
        snapshot=snapshot,
        author=Author(initials="LM", poste="Bureau"),
        now="2026-10-22T10:00:00Z",
        calendar_for=calendar_resolver(snapshot),
    )

    preview = move_preserving_work(
        start, end, new_start, ctx.calendar_for("ana"), target=ctx.calendar_for(target)
    )
    changes = move_task(ctx, "t", new_start=new_start, from_resource="ana", to_resource=target)
    saved = next(change.after for change in changes if change.doc_id == "t")

    assert isinstance(saved, PlanningTask)
    assert (show(preview[0]), show(preview[1])) == (show(saved.start or 0), show(saved.end or 0))


def test_moves_keep_the_worked_minutes_everywhere() -> None:
    start, end = at(THU, "09:00"), at(NEXT_MON, "08:00")
    worked = GE.work_between(start, end)
    first = day_start(date(2026, 3, 27))
    for new_start in range(first, first + 20 * MINUTES_PER_DAY, 30):
        moved_start, moved_end = move_preserving_work(start, end, new_start, GE)
        assert GE.work_between(moved_start, moved_end) == worked, show(new_start)
        assert GE.is_worked(moved_start)
        assert moved_start >= new_start


# ------------------------------------------------------------ navigation ----


def test_week_start_and_month_range() -> None:
    assert week_start(SUN) == MON
    assert week_start(MON) == MON
    assert week_start(date(2027, 1, 1)) == date(2026, 12, 28)
    assert month_range(date(2026, 2, 15)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert month_range(date(2028, 2, 1)) == (date(2028, 2, 1), date(2028, 2, 29))
    assert month_range(date(2026, 12, 31)) == (date(2026, 12, 1), date(2026, 12, 31))


def test_shown_day_and_view_ranges() -> None:
    assert shown_day(SAT) == NEXT_MON
    assert shown_day(MON) == MON
    assert view_range(SAT, "day") == (NEXT_MON, NEXT_MON)
    assert view_range(THU, "day") == (THU, THU)
    assert view_range(THU, "week") == (MON, SUN)
    assert view_range(THU, "month") == (date(2026, 10, 1), date(2026, 10, 31))
    with pytest.raises(ValueError, match="Aucun jour"):
        shown_day(MON, ())


@pytest.mark.parametrize(
    ("day", "view", "steps", "expected"),
    [
        (FRI, "day", 1, NEXT_MON),
        (MON, "day", -1, PREVIOUS_FRI),
        (MON, "day", 5, NEXT_MON),
        (SAT, "day", 0, NEXT_MON),
        (SAT, "day", 1, date(2026, 11, 3)),
        (THU, "week", -2, date(2026, 10, 15)),
        (THU, "week", 1, date(2026, 11, 5)),
        (date(2026, 1, 31), "month", 1, date(2026, 2, 1)),
        (date(2026, 1, 15), "month", -1, date(2025, 12, 1)),
        (date(2026, 11, 15), "month", 14, date(2028, 1, 1)),
    ],
)
def test_shift_view(day: date, view: ViewKind, steps: int, expected: date) -> None:
    assert shift_view(day, view, steps) == expected


def test_navigation_follows_an_edited_work_week_through_its_workdays() -> None:
    four_days = WorkCalendar(week=dict.fromkeys(range(4), ((390, 720), (780, 990))))
    weekdays = four_days.workdays

    first, last = view_range(FRI, "day", weekdays)
    assert (first, last) == (NEXT_MON, NEXT_MON)
    assert TimeScale(four_days, first, last, WIDTH).first_day == NEXT_MON
    assert shift_view(THU, "day", 1, weekdays) == NEXT_MON
    assert shift_view(NEXT_MON, "day", -1, weekdays) == THU
    assert shown_day(SAT, weekdays) == NEXT_MON
    # The Monday-Friday default has no column on that calendar.
    with pytest.raises(ValueError, match="aucun jour"):
        TimeScale(four_days, *view_range(FRI, "day"), WIDTH)


def test_labels() -> None:
    assert iso_week_label(MON) == "Semaine 44"
    assert iso_week_label(MON, short=True) == "S44"
    assert iso_week_label(date(2027, 1, 1), short=True) == "S53"
    assert month_label(MON) == "Octobre 2026"
    assert month_label(date(2026, 8, 3)) == "Août 2026"
    assert day_label(MON) == "Lundi 26.10"
    assert day_label(MON, short=True) == "Lu 26"
    assert day_label(NEXT_FRI) == "Vendredi 06.11"
