from __future__ import annotations

from datetime import date, timedelta

import pytest

from projectflow.core.planning.calendar import (
    DEFAULT_WORK_WEEK,
    FRIDAY,
    FULL_DAY,
    SNAP_MINUTES,
    WorkCalendar,
    cantonal_holidays,
    easter_sunday,
    snap,
)
from projectflow.core.planning.walltime import (
    MINUTES_PER_DAY,
    day_start,
    format_hhmm,
    format_wall_time,
    minute_of_day,
    parse_wall_time,
    wall_date,
    wall_time,
)

# Week of Monday 26 October 2026, without holiday.
MON = date(2026, 10, 26)
TUE = date(2026, 10, 27)
THU = date(2026, 10, 29)
FRI = date(2026, 10, 30)
SAT = date(2026, 10, 31)
SUN = date(2026, 11, 1)
NEXT_MON = date(2026, 11, 2)
PREVIOUS_FRI = date(2026, 10, 23)
# Easter 2026: Good Friday 3 April, Easter Monday 6 April.
MAUNDY_THU = date(2026, 4, 2)
GOOD_FRIDAY = date(2026, 4, 3)
EASTER_MONDAY = date(2026, 4, 6)
EASTER_TUE = date(2026, 4, 7)


def at(day: date, text: str) -> int:
    hours, minutes = (int(part) for part in text.split(":"))
    return wall_time(day, hours, minutes)


def show(value: int) -> str:
    return format_wall_time(value)


def case_id(value: object) -> str:
    """Readable parametrize ids: instants as text, durations as numbers."""
    if isinstance(value, int) and value >= MINUTES_PER_DAY:
        return format_wall_time(value)
    return str(value)


GE = WorkCalendar()
VD = WorkCalendar(cantons=("VD",))


# ------------------------------------------------------------- walltime ----


def test_wall_time_counts_minutes_from_the_date_ordinal() -> None:
    value = wall_time(MON, 6, 30)

    assert value == MON.toordinal() * MINUTES_PER_DAY + 390
    assert wall_date(value) == MON
    assert minute_of_day(value) == 390
    assert day_start(MON) == wall_time(MON) == MON.toordinal() * MINUTES_PER_DAY


def test_wall_time_accepts_the_end_of_the_day_as_next_midnight() -> None:
    assert wall_time(MON, 24, 0) == day_start(TUE)
    assert format_wall_time(wall_time(MON, 24)) == "2026-10-27T00:00"


@pytest.mark.parametrize(("hour", "minute"), [(24, 1), (-1, 0), (0, -1), (25, 0)])
def test_wall_time_rejects_times_outside_the_day(hour: int, minute: int) -> None:
    with pytest.raises(ValueError, match="Heure hors de la journee"):
        wall_time(MON, hour, minute)


def test_wall_times_sort_and_subtract_as_minutes() -> None:
    assert at(FRI, "12:00") < at(SAT, "00:00") < at(NEXT_MON, "06:30")
    assert at(TUE, "06:30") - at(MON, "16:30") == 14 * 60


@pytest.mark.parametrize(
    "text",
    ["2026-10-26T06:30", "2026-01-01T00:00", "2026-12-31T23:59", "2028-02-29T16:30"],
)
def test_format_and_parse_round_trip(text: str) -> None:
    value = parse_wall_time(text)

    assert format_wall_time(value) == text
    assert parse_wall_time(format_wall_time(value)) == value


@pytest.mark.parametrize(
    "text",
    [
        "2026-10-26 06:30",
        "2026-10-26T06:30:00",
        "2026-10-26T06:30:59.123",
        "  2026-10-26T06:30\n",
    ],
)
def test_parse_accepts_space_seconds_and_surrounding_blanks(text: str) -> None:
    assert parse_wall_time(text) == at(MON, "06:30")


def test_parse_reads_24_00_as_next_midnight() -> None:
    assert parse_wall_time("2026-10-26T24:00") == day_start(TUE)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "2026-10-26",
        "26.10.2026 06:30",
        "2026-10-26T6:30",
        "2026-10-26T06:30Z",
        "2026-10-26T06:30+01:00",
        "2026-10-26T24:30",
        "2026-13-01T06:30",
        "2026-02-30T06:30",
        "lundi",
    ],
)
def test_parse_rejects_unreadable_text(text: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - date() raises its own message
        parse_wall_time(text)


@pytest.mark.parametrize(
    "text",
    ["2026-10-26T06:60", "2026-10-26T11:75", "2026-10-26T23:60", "2026-10-26T24:30"],
)
def test_parse_rejects_minutes_above_59(text: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        parse_wall_time(text)


@pytest.mark.parametrize(
    ("minutes", "text"), [(0, "00:00"), (390, "06:30"), (990, "16:30"), (1440, "24:00")]
)
def test_format_hhmm(minutes: int, text: str) -> None:
    assert format_hhmm(minutes) == text


# ------------------------------------------------------------ holidays ----


@pytest.mark.parametrize(
    ("year", "easter"),
    [
        (2024, date(2024, 3, 31)),
        (2025, date(2025, 4, 20)),
        (2026, date(2026, 4, 5)),
        (2027, date(2027, 3, 28)),
        (2038, date(2038, 4, 25)),
        (2285, date(2285, 3, 22)),
    ],
)
def test_easter_sunday(year: int, easter: date) -> None:
    assert easter_sunday(year) == easter
    assert easter.weekday() == 6


GE_HOLIDAYS = {
    2025: {
        date(2025, 1, 1): "Nouvel An",
        date(2025, 4, 18): "Vendredi saint",
        date(2025, 4, 21): "Lundi de Pâques",
        date(2025, 5, 29): "Ascension",
        date(2025, 6, 9): "Lundi de Pentecôte",
        date(2025, 8, 1): "Fête nationale",
        date(2025, 9, 11): "Jeûne genevois",
        date(2025, 12, 25): "Noël",
        date(2025, 12, 31): "Restauration de la République",
    },
    2026: {
        date(2026, 1, 1): "Nouvel An",
        date(2026, 4, 3): "Vendredi saint",
        date(2026, 4, 6): "Lundi de Pâques",
        date(2026, 5, 14): "Ascension",
        date(2026, 5, 25): "Lundi de Pentecôte",
        date(2026, 8, 1): "Fête nationale",
        date(2026, 9, 10): "Jeûne genevois",
        date(2026, 12, 25): "Noël",
        date(2026, 12, 31): "Restauration de la République",
    },
    2027: {
        date(2027, 1, 1): "Nouvel An",
        date(2027, 3, 26): "Vendredi saint",
        date(2027, 3, 29): "Lundi de Pâques",
        date(2027, 5, 6): "Ascension",
        date(2027, 5, 17): "Lundi de Pentecôte",
        date(2027, 8, 1): "Fête nationale",
        date(2027, 9, 9): "Jeûne genevois",
        date(2027, 12, 25): "Noël",
        date(2027, 12, 31): "Restauration de la République",
    },
}

VD_HOLIDAYS = {
    2025: {
        date(2025, 1, 1): "Nouvel An",
        date(2025, 1, 2): "Saint-Berchtold",
        date(2025, 4, 18): "Vendredi saint",
        date(2025, 4, 21): "Lundi de Pâques",
        date(2025, 5, 29): "Ascension",
        date(2025, 6, 9): "Lundi de Pentecôte",
        date(2025, 8, 1): "Fête nationale",
        date(2025, 9, 22): "Lundi du Jeûne",
        date(2025, 12, 25): "Noël",
    },
    2026: {
        date(2026, 1, 1): "Nouvel An",
        date(2026, 1, 2): "Saint-Berchtold",
        date(2026, 4, 3): "Vendredi saint",
        date(2026, 4, 6): "Lundi de Pâques",
        date(2026, 5, 14): "Ascension",
        date(2026, 5, 25): "Lundi de Pentecôte",
        date(2026, 8, 1): "Fête nationale",
        date(2026, 9, 21): "Lundi du Jeûne",
        date(2026, 12, 25): "Noël",
    },
    2027: {
        date(2027, 1, 1): "Nouvel An",
        date(2027, 1, 2): "Saint-Berchtold",
        date(2027, 3, 26): "Vendredi saint",
        date(2027, 3, 29): "Lundi de Pâques",
        date(2027, 5, 6): "Ascension",
        date(2027, 5, 17): "Lundi de Pentecôte",
        date(2027, 8, 1): "Fête nationale",
        date(2027, 9, 20): "Lundi du Jeûne",
        date(2027, 12, 25): "Noël",
    },
}


@pytest.mark.parametrize("year", [2025, 2026, 2027])
def test_geneva_holidays(year: int) -> None:
    holidays = cantonal_holidays(year, ("GE",))

    assert {day: holiday.name for day, holiday in holidays.items()} == GE_HOLIDAYS[year]
    assert all(holiday.cantons == ("GE",) for holiday in holidays.values())


@pytest.mark.parametrize("year", [2025, 2026, 2027])
def test_vaud_holidays(year: int) -> None:
    holidays = cantonal_holidays(year, ("VD",))

    assert {day: holiday.name for day, holiday in holidays.items()} == VD_HOLIDAYS[year]
    assert all(holiday.cantons == ("VD",) for holiday in holidays.values())


@pytest.mark.parametrize("year", [2025, 2026, 2027])
def test_both_cantons_merge_and_keep_which_canton_observes_each_day(year: int) -> None:
    holidays = cantonal_holidays(year)

    assert set(holidays) == set(GE_HOLIDAYS[year]) | set(VD_HOLIDAYS[year])
    assert holidays[date(year, 1, 2)].cantons == ("VD",)
    assert holidays[date(year, 12, 31)].cantons == ("GE",)
    assert holidays[date(year, 12, 25)].cantons == ("GE", "VD")


def test_canton_codes_are_case_insensitive() -> None:
    assert cantonal_holidays(2026, ("ge",)) == cantonal_holidays(2026, ("GE",))
    assert cantonal_holidays(2026, ()) == {}


@pytest.mark.parametrize(
    ("year", "jeune_genevois", "lundi_du_jeune"),
    [
        # 1 September 2024 is a Sunday: it is the first Sunday itself.
        (2024, date(2024, 9, 5), date(2024, 9, 16)),
        # 1 September 2022 is a Thursday: the Jeûne genevois is the next Thursday.
        (2022, date(2022, 9, 8), date(2022, 9, 19)),
        # 1 September 2029 is a Saturday.
        (2029, date(2029, 9, 6), date(2029, 9, 17)),
    ],
)
def test_jeune_dates_around_the_first_of_september(
    year: int, jeune_genevois: date, lundi_du_jeune: date
) -> None:
    holidays = cantonal_holidays(year)

    assert holidays[jeune_genevois].name == "Jeûne genevois"
    assert jeune_genevois.weekday() == 3
    assert holidays[lundi_du_jeune].name == "Lundi du Jeûne"
    assert lundi_du_jeune.weekday() == 0


# ------------------------------------------------------- work calendar ----


def test_default_calendar_works_41_hours_30_from_monday_to_friday_in_geneva() -> None:
    assert GE.cantons == ("GE",)
    assert GE.workdays == frozenset({0, 1, 2, 3, 4})
    assert GE.week == dict(DEFAULT_WORK_WEEK)
    assert GE.periods_of_weekday(0) == FULL_DAY
    assert GE.periods_of_weekday(4) == FRIDAY
    assert GE.periods_of_weekday(5) == ()
    assert GE.minutes_per_week == 2490
    assert GE.day_start_minute == 390
    assert GE.day_end_minute == 990


def test_worked_minutes_per_day() -> None:
    assert [GE.worked_minutes(MON + timedelta(days=offset)) for offset in range(7)] == [
        540,
        540,
        540,
        540,
        330,
        0,
        0,
    ]
    assert GE.worked_minutes(GOOD_FRIDAY) == 0
    assert GE.work_between(day_start(MON), day_start(NEXT_MON)) == GE.minutes_per_week


def test_holidays_depend_on_the_canton() -> None:
    saint_berchtold = date(2026, 1, 2)
    restauration = date(2026, 12, 31)

    assert GE.is_workday(saint_berchtold)
    assert not VD.is_workday(saint_berchtold)
    assert not GE.is_workday(restauration)
    assert VD.is_workday(restauration)
    holiday = GE.holiday(date(2026, 9, 10))
    assert holiday is not None
    assert holiday.name == "Jeûne genevois"
    assert VD.holiday(date(2026, 9, 10)) is None
    assert WorkCalendar(cantons=("vd",)).cantons == ("VD",)


def test_holidays_between_crosses_years_in_order() -> None:
    found = GE.holidays_between(date(2026, 12, 20), date(2027, 1, 10))

    assert [(holiday.day, holiday.name) for holiday in found] == [
        (date(2026, 12, 25), "Noël"),
        (date(2026, 12, 31), "Restauration de la République"),
        (date(2027, 1, 1), "Nouvel An"),
    ]
    assert GE.holidays_between(MON, SUN) == []


def test_closures_are_not_worked_but_are_not_holidays() -> None:
    closed = WorkCalendar(closures=[TUE])

    assert closed.is_closed(TUE)
    assert not closed.is_workday(TUE)
    assert closed.holiday(TUE) is None
    assert closed.day_periods(TUE) == ()
    assert closed.minutes_per_week == 2490
    assert closed.work_between(day_start(MON), day_start(NEXT_MON)) == 2490 - 540
    assert closed.add_work(at(MON, "16:00"), 60) == at(date(2026, 10, 28), "07:00")


def test_weekends_holidays_and_closures_are_not_workdays() -> None:
    assert GE.is_workday(MON)
    assert GE.is_workday(FRI)
    assert not GE.is_workday(SAT)
    assert not GE.is_workday(SUN)
    assert not GE.is_workday(GOOD_FRIDAY)
    assert not GE.is_closed(GOOD_FRIDAY)


def test_custom_week() -> None:
    calendar = WorkCalendar(week={0: ((8 * 60, 12 * 60),), 2: ((13 * 60, 17 * 60),), 9: ()})

    assert calendar.workdays == frozenset({0, 2})
    assert calendar.minutes_per_week == 480
    assert calendar.day_start_minute == 480
    assert calendar.day_end_minute == 1020
    assert calendar.add_work(at(MON, "11:00"), 120) == at(date(2026, 10, 28), "14:00")


@pytest.mark.parametrize(
    "week",
    [
        {},
        {5: ()},
        {0: ((600, 500),)},
        {0: ((390, 720), (700, 990))},
        {0: ((390, 1500),)},
        {0: ((-10, 390),)},
    ],
)
def test_invalid_weeks_are_refused(week: dict[int, tuple[tuple[int, int], ...]]) -> None:
    with pytest.raises(ValueError, match="Horaires de travail"):
        WorkCalendar(week=week)


def test_periods_are_sorted_and_may_touch() -> None:
    calendar = WorkCalendar(week={0: ((720, 990), (390, 720))})

    assert calendar.periods_of_weekday(0) == ((390, 720), (720, 990))
    assert calendar.add_work(at(MON, "11:30"), 60) == at(MON, "12:30")


def test_day_periods_are_instants_of_that_day() -> None:
    assert GE.day_periods(MON) == (
        (at(MON, "06:30"), at(MON, "12:00")),
        (at(MON, "13:00"), at(MON, "16:30")),
    )
    assert GE.day_periods(FRI) == ((at(FRI, "06:30"), at(FRI, "12:00")),)
    assert GE.day_periods(SAT) == ()
    assert GE.day_periods(GOOD_FRIDAY) == ()


def test_next_and_previous_workdays_skip_weekends_and_holidays() -> None:
    assert GE.next_workday(FRI) == NEXT_MON
    assert GE.next_workday(SAT) == NEXT_MON
    assert GE.next_workday(MON) == TUE
    assert GE.previous_workday(NEXT_MON) == FRI
    assert GE.previous_workday(SUN) == FRI
    assert GE.next_workday(MAUNDY_THU) == EASTER_TUE
    assert GE.previous_workday(EASTER_TUE) == MAUNDY_THU


def test_days_between_lists_working_days_only() -> None:
    assert GE.days_between(MON, NEXT_MON) == [
        MON,
        TUE,
        date(2026, 10, 28),
        THU,
        FRI,
        NEXT_MON,
    ]
    assert GE.days_between(MAUNDY_THU, EASTER_TUE) == [MAUNDY_THU, EASTER_TUE]
    assert GE.days_between(TUE, MON) == []


@pytest.mark.parametrize(
    ("day", "text", "worked"),
    [
        (MON, "06:29", False),
        (MON, "06:30", True),
        (MON, "11:59", True),
        (MON, "12:00", False),
        (MON, "12:59", False),
        (MON, "13:00", True),
        (MON, "16:29", True),
        (MON, "16:30", False),
        (FRI, "11:59", True),
        (FRI, "12:00", False),
        (FRI, "14:00", False),
        (SAT, "09:00", False),
        (GOOD_FRIDAY, "09:00", False),
    ],
)
def test_is_worked(day: date, text: str, worked: object) -> None:
    assert GE.is_worked(at(day, text)) is worked


# ------------------------------------------------------- working edges ----


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (at(MON, "00:00"), at(MON, "06:30")),
        (at(MON, "05:00"), at(MON, "06:30")),
        (at(MON, "06:30"), at(MON, "06:30")),
        (at(MON, "09:15"), at(MON, "09:15")),
        (at(MON, "11:59"), at(MON, "11:59")),
        (at(MON, "12:00"), at(MON, "13:00")),
        (at(MON, "12:30"), at(MON, "13:00")),
        (at(MON, "13:00"), at(MON, "13:00")),
        (at(MON, "16:30"), at(TUE, "06:30")),
        (at(MON, "20:00"), at(TUE, "06:30")),
        (at(FRI, "11:30"), at(FRI, "11:30")),
        (at(FRI, "12:00"), at(NEXT_MON, "06:30")),
        (at(FRI, "14:00"), at(NEXT_MON, "06:30")),
        (at(SAT, "10:00"), at(NEXT_MON, "06:30")),
        (at(SUN, "23:59"), at(NEXT_MON, "06:30")),
        (at(MAUNDY_THU, "16:30"), at(EASTER_TUE, "06:30")),
        (at(GOOD_FRIDAY, "08:00"), at(EASTER_TUE, "06:30")),
    ],
    ids=case_id,
)
def test_working_start_moves_out_of_breaks_nights_weekends_and_holidays(
    value: int, expected: int
) -> None:
    assert show(GE.working_start(value)) == show(expected)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (at(MON, "06:00"), at(PREVIOUS_FRI, "12:00")),
        (at(MON, "06:30"), at(PREVIOUS_FRI, "12:00")),
        (at(MON, "07:00"), at(MON, "07:00")),
        (at(MON, "12:00"), at(MON, "12:00")),
        (at(MON, "12:30"), at(MON, "12:00")),
        (at(MON, "13:00"), at(MON, "12:00")),
        (at(MON, "13:30"), at(MON, "13:30")),
        (at(MON, "16:30"), at(MON, "16:30")),
        (at(MON, "20:00"), at(MON, "16:30")),
        (day_start(TUE), at(MON, "16:30")),
        (at(FRI, "12:00"), at(FRI, "12:00")),
        (at(FRI, "16:30"), at(FRI, "12:00")),
        (at(SAT, "10:00"), at(FRI, "12:00")),
        (at(NEXT_MON, "06:30"), at(FRI, "12:00")),
        (at(EASTER_TUE, "06:30"), at(MAUNDY_THU, "16:30")),
        (at(EASTER_MONDAY, "10:00"), at(MAUNDY_THU, "16:30")),
    ],
    ids=case_id,
)
def test_working_end_moves_back_out_of_breaks_nights_weekends_and_holidays(
    value: int, expected: int
) -> None:
    assert show(GE.working_end(value)) == show(expected)


# ---------------------------------------------------------- durations ----


@pytest.mark.parametrize(
    ("start", "end", "minutes"),
    [
        (at(MON, "06:30"), at(MON, "16:30"), 540),
        (at(MON, "00:00"), day_start(TUE), 540),
        (at(MON, "11:00"), at(MON, "14:00"), 120),
        (at(MON, "12:00"), at(MON, "13:00"), 0),
        (at(MON, "12:15"), at(MON, "13:30"), 30),
        (at(FRI, "06:30"), at(NEXT_MON, "06:30"), 330),
        (at(FRI, "11:00"), at(NEXT_MON, "07:00"), 90),
        (at(SAT, "08:00"), at(SUN, "17:00"), 0),
        (at(MAUNDY_THU, "16:00"), at(EASTER_TUE, "07:00"), 60),
        (day_start(date(2026, 3, 30)), day_start(date(2026, 4, 6)), 2160),
        (at(MON, "14:00"), at(MON, "14:00"), 0),
        (at(MON, "14:00"), at(MON, "10:00"), 0),
    ],
)
def test_work_between(start: int, end: int, minutes: int) -> None:
    assert GE.work_between(start, end) == minutes


@pytest.mark.parametrize(
    ("start", "minutes", "expected"),
    [
        # A morning ends at noon, not at the next period's start.
        (at(MON, "06:30"), 330, at(MON, "12:00")),
        (at(MON, "06:30"), 360, at(MON, "13:30")),
        (at(MON, "11:00"), 120, at(MON, "14:00")),
        (at(MON, "06:30"), 540, at(MON, "16:30")),
        (at(MON, "06:30"), 570, at(TUE, "07:00")),
        (at(MON, "12:15"), 30, at(MON, "13:30")),
        (at(MON, "05:00"), 30, at(MON, "07:00")),
        (at(MON, "12:00"), 0, at(MON, "13:00")),
        (at(FRI, "11:00"), 60, at(FRI, "12:00")),
        (at(FRI, "11:00"), 90, at(NEXT_MON, "07:00")),
        (at(FRI, "12:00"), 30, at(NEXT_MON, "07:00")),
        (at(SAT, "08:00"), 30, at(NEXT_MON, "07:00")),
        (at(MON, "06:30"), 2490, at(FRI, "12:00")),
        (at(MON, "06:30"), 2520, at(NEXT_MON, "07:00")),
        (at(MAUNDY_THU, "15:30"), 120, at(EASTER_TUE, "07:30")),
        (at(GOOD_FRIDAY, "08:00"), 60, at(EASTER_TUE, "07:30")),
    ],
    ids=case_id,
)
def test_add_work(start: int, minutes: int, expected: int) -> None:
    assert show(GE.add_work(start, minutes)) == show(expected)


@pytest.mark.parametrize(
    ("end", "minutes", "expected"),
    [
        (at(MON, "12:00"), 330, at(MON, "06:30")),
        (at(MON, "16:30"), 210, at(MON, "13:00")),
        (at(MON, "14:00"), 120, at(MON, "11:00")),
        (at(MON, "13:00"), 30, at(MON, "11:30")),
        (at(MON, "07:00"), 60, at(PREVIOUS_FRI, "11:30")),
        (at(MON, "06:30"), 30, at(PREVIOUS_FRI, "11:30")),
        (at(TUE, "06:30"), 30, at(MON, "16:00")),
        (at(FRI, "16:30"), 2490, at(MON, "06:30")),
        (at(EASTER_TUE, "07:30"), 120, at(MAUNDY_THU, "15:30")),
    ],
    ids=case_id,
)
def test_subtract_work(end: int, minutes: int, expected: int) -> None:
    assert show(GE.subtract_work(end, minutes)) == show(expected)


def test_negative_durations_go_the_other_way() -> None:
    assert GE.add_work(at(MON, "14:00"), -120) == at(MON, "11:00")
    assert GE.subtract_work(at(MON, "11:00"), -120) == at(MON, "14:00")


def _instants(first: date, days: int, step: int) -> list[int]:
    begin = day_start(first)
    return list(range(begin, begin + days * MINUTES_PER_DAY, step))


@pytest.mark.parametrize("minutes", [1, 30, 60, 210, 330, 540, 570, 1000, 2490, 5000])
def test_add_and_subtract_work_are_inverse(minutes: int) -> None:
    for start in _instants(MAUNDY_THU - timedelta(days=4), 10, 30):
        end = GE.add_work(start, minutes)
        first = GE.working_start(start)
        assert GE.work_between(start, end) == minutes, show(start)
        assert GE.subtract_work(end, minutes) == first, show(start)
        assert GE.is_worked(end - 1), show(start)
    for end in _instants(MAUNDY_THU - timedelta(days=4), 10, 30):
        start = GE.subtract_work(end, minutes)
        assert GE.work_between(start, end) == minutes, show(end)
        assert GE.add_work(start, minutes) == GE.working_end(end), show(end)
        assert GE.is_worked(start), show(end)


def test_searches_give_up_without_any_workday() -> None:
    every_day_closed = WorkCalendar(
        closures=[MON + timedelta(days=offset) for offset in range(-3700, 3700)]
    )

    with pytest.raises(ValueError, match="Aucun jour ouvrable"):
        every_day_closed.working_start(at(MON, "08:00"))
    with pytest.raises(ValueError, match="Aucun jour ouvrable"):
        every_day_closed.next_workday(MON)


# ------------------------------------------------------- working days ----

WED = date(2026, 10, 28)


@pytest.mark.parametrize(
    ("start", "days", "expected"),
    [
        (at(MON, "06:30"), 0.5, at(MON, "12:00")),
        (at(MON, "06:30"), 1, at(MON, "16:30")),
        (at(MON, "06:30"), 5, at(FRI, "12:00")),
        # An afternoon start is half of its day.
        (at(MON, "13:00"), 0.5, at(MON, "16:30")),
        (at(MON, "13:00"), 1, at(TUE, "12:00")),
        (at(THU, "13:00"), 2, at(NEXT_MON, "12:00")),
        (at(WED, "13:00"), 3, at(NEXT_MON, "12:00")),
        # Friday, worked until noon, is a whole day: the count jumps past it.
        (at(THU, "06:30"), 2, at(FRI, "12:00")),
        (at(THU, "13:00"), 1, at(FRI, "12:00")),
        (at(FRI, "06:30"), 0.5, at(FRI, "12:00")),
        # A start inside a period counts that period from its beginning.
        (at(MON, "09:00"), 0.5, at(MON, "12:00")),
        (at(MON, "12:00"), 0.5, at(MON, "16:30")),
        (at(MAUNDY_THU, "13:00"), 1, at(EASTER_TUE, "12:00")),
    ],
    ids=case_id,
)
def test_add_days_counts_halves_from_the_period_of_the_start(
    start: int, days: float, expected: int
) -> None:
    assert show(GE.add_days(start, days)) == show(expected)


@pytest.mark.parametrize("days", [0.5, 1, 1.5, 2, 2.5, 3, 4.5, 5, 7.5])
def test_whole_days_reads_back_what_add_days_placed(days: float) -> None:
    """The bar (whole_days) says what the card (add_days) gave, past a Friday by ½ j."""
    for day in GE.days_between(MAUNDY_THU - timedelta(days=10), MAUNDY_THU + timedelta(days=10)):
        for low, _high in GE.day_periods(day):
            end = GE.add_days(low, days)
            read = GE.whole_days(low, end)
            assert read is not None, show(low)
            assert days <= read <= days + 0.5, show(low)
            # Reading it again places the same bar.
            assert GE.add_days(low, read) == end, show(low)


# ---------------------------------------------------------------- snap ----


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("06:30", "06:30"),
        ("06:44", "06:30"),
        ("06:45", "07:00"),
        ("06:15", "06:30"),
        ("06:14", "06:00"),
        ("12:10", "12:00"),
        ("16:20", "16:30"),
        ("00:10", "00:00"),
    ],
)
def test_snap_rounds_to_the_half_hour_halves_up(text: str, expected: str) -> None:
    assert snap(at(MON, text)) == at(MON, expected)


def test_snap_late_evening_reaches_next_midnight() -> None:
    assert snap(at(MON, "23:50")) == day_start(TUE)


def test_snap_with_another_step() -> None:
    assert SNAP_MINUTES == 30
    assert snap(at(MON, "07:29"), 60) == at(MON, "07:00")
    assert snap(at(MON, "07:30"), 60) == at(MON, "08:00")
    assert snap(at(MON, "07:07"), 15) == at(MON, "07:00")
