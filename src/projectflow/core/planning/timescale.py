"""Display axis of the Planning grid, shared by the grid body, the time header and the PDF.

Only the calendar's working weekdays are shown (Monday to Friday): Saturday and Sunday
never appear, while holidays and closures stay on the axis, flagged as not worked. Every
shown day spans the same display minutes, from the earliest worked minute of the week to
the latest (06:30-16:30), so the lunch break and Friday afternoon are drawn but not worked
and Friday evening is directly followed by Monday morning. Nothing here imports Qt.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from projectflow.core.planning.calendar import DAYS_PER_WEEK, SNAP_MINUTES, WorkCalendar, snap
from projectflow.core.planning.walltime import (
    MINUTES_PER_DAY,
    WallTime,
    day_start,
    minute_of_day,
    wall_date,
)

ViewKind = Literal["day", "week", "month"]
VIEW_KINDS: tuple[ViewKind, ...] = ("day", "week", "month")
WEEKDAYS: frozenset[int] = frozenset(range(5))
"""Weekdays shown by default, Monday (0) to Friday (4)."""
MONTHS_PER_YEAR = 12

WEEKDAY_NAMES = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
WEEKDAY_ABBREVIATIONS = ("lu", "ma", "me", "je", "ve", "sa", "di")
MONTH_NAMES = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)

Period = tuple[WallTime, WallTime]


@dataclass(frozen=True, slots=True)
class DayColumn:
    """One shown day; ``periods`` are its worked periods, empty when not worked.

    ``width`` is the day width up to float rounding, so that ``right`` is exactly the next
    column's ``x``.
    """

    day: date
    index: int
    x: float
    width: float
    workday: bool
    holiday: str | None
    periods: tuple[Period, ...]
    closed: bool = False

    @property
    def right(self) -> float:
        return self.x + self.width


@dataclass(frozen=True, slots=True)
class ColumnGroup:
    """Consecutive columns of one ISO week or one month, for the upper header band."""

    key: date
    label: str
    columns: range
    x: float
    width: float


# ---------------------------------------------------------------- labels ----


def iso_week_label(day: date, *, short: bool = False) -> str:
    """``Semaine 44``, or ``S44`` when ``short``."""
    week = day.isocalendar().week
    return f"S{week}" if short else f"Semaine {week}"


def month_label(day: date) -> str:
    """``Octobre 2026``."""
    return f"{MONTH_NAMES[day.month - 1].capitalize()} {day.year}"


def day_label(day: date, *, short: bool = False) -> str:
    """``Lundi 26.10``, or ``Lu 26`` when ``short``."""
    if short:
        return f"{WEEKDAY_ABBREVIATIONS[day.weekday()].capitalize()} {day.day}"
    return f"{WEEKDAY_NAMES[day.weekday()].capitalize()} {day.day:02d}.{day.month:02d}"


# ------------------------------------------------------------ navigation ----
# ``weekdays`` must be the axis calendar's ``workdays`` when the work week of the shared
# settings may differ from Monday to Friday: ``TimeScale`` shows only those days.


def week_start(day: date) -> date:
    """Monday of the week holding ``day``."""
    return day - timedelta(days=day.weekday())


def month_range(day: date) -> tuple[date, date]:
    """First and last day of the month holding ``day``."""
    first = day.replace(day=1)
    following = _add_months(first, 1)
    return first, following - timedelta(days=1)


def shown_day(day: date, weekdays: Collection[int] = WEEKDAYS) -> date:
    """``day`` itself when shown, otherwise the next shown day (a Saturday gives Monday)."""
    if not weekdays:
        raise ValueError("Aucun jour de la semaine affiche.")
    candidate = day
    while candidate.weekday() not in weekdays:
        candidate += timedelta(days=1)
    return candidate


def view_range(
    day: date, view: ViewKind, weekdays: Collection[int] = WEEKDAYS
) -> tuple[date, date]:
    """First and last calendar day of the view around ``day``."""
    _check_view(view)
    if view == "day":
        shown = shown_day(day, weekdays)
        return shown, shown
    if view == "week":
        monday = week_start(day)
        return monday, monday + timedelta(days=DAYS_PER_WEEK - 1)
    return month_range(day)


def shift_view(day: date, view: ViewKind, steps: int, weekdays: Collection[int] = WEEKDAYS) -> date:
    """Anchor day ``steps`` views later (earlier when negative).

    The day view moves by shown days, so Friday + 1 is Monday; the month view returns the
    first day of the target month.
    """
    _check_view(view)
    if view == "week":
        return day + timedelta(days=DAYS_PER_WEEK * steps)
    if view == "month":
        return _add_months(day.replace(day=1), steps)
    current = shown_day(day, weekdays)
    direction = 1 if steps >= 0 else -1
    for _ in range(abs(steps)):
        current += timedelta(days=direction)
        while current.weekday() not in weekdays:
            current += timedelta(days=direction)
    return current


def _add_months(first: date, months: int) -> date:
    year, month = divmod(first.year * MONTHS_PER_YEAR + first.month - 1 + months, MONTHS_PER_YEAR)
    return date(year, month + 1, 1)


def _check_view(view: str) -> None:
    if view not in VIEW_KINDS:
        raise ValueError(f"Vue de planning inconnue: {view!r}")


# --------------------------------------------------------------- snapping ----


def _half_day_edge(value: WallTime, edges: tuple[int, ...]) -> WallTime:
    """Closest of ``edges`` (minutes of the day of ``value``), halves going later."""
    minute = min(max(minute_of_day(value), edges[0]), edges[-1])
    nearest = min(edges, key=lambda edge: (abs(edge - minute), -edge))
    return day_start(wall_date(value)) + nearest


def half_day_start_edges(calendar: WorkCalendar) -> tuple[int, ...]:
    """Minutes where a month-view start may land: period starts and the end of the day."""
    starts = {start for periods in calendar.week.values() for start, _end in periods}
    return tuple(sorted(starts | {calendar.day_end_minute}))


def half_day_end_edges(calendar: WorkCalendar) -> tuple[int, ...]:
    """Minutes where a month-view end may land: period ends and the start of the day."""
    ends = {end for periods in calendar.week.values() for _start, end in periods}
    return tuple(sorted(ends | {calendar.day_start_minute}))


def snap_start(calendar: WorkCalendar, value: WallTime, view: ViewKind) -> WallTime:
    """Start snapped for ``view``, always on a worked instant.

    Day and week views round to the half-hour; the month view takes the closest half-day
    start (06:30 or 13:00). A start in a break, on Friday afternoon, a weekend or a holiday
    moves to the next worked instant.
    """
    _check_view(view)
    if view == "month":
        candidate = _half_day_edge(value, half_day_start_edges(calendar))
    else:
        candidate = snap(value, SNAP_MINUTES)
    return calendar.working_start(candidate)


def snap_end(calendar: WorkCalendar, value: WallTime, view: ViewKind) -> WallTime:
    """End snapped for ``view``, always just after a worked minute.

    Day and week views round to the half-hour; the month view takes the closest half-day
    end (12:00 or 16:30). An end in a break or a non-worked day moves back to the previous
    worked instant, so a Friday afternoon end gives Friday 12:00.
    """
    _check_view(view)
    if view == "month":
        candidate = _half_day_edge(value, half_day_end_edges(calendar))
    else:
        candidate = snap(value, SNAP_MINUTES)
    return calendar.working_end(candidate)


def move_preserving_work(
    start: WallTime,
    end: WallTime,
    new_start: WallTime,
    calendar: WorkCalendar,
    *,
    target: WorkCalendar | None = None,
) -> tuple[WallTime, WallTime]:
    """Move a task to ``new_start`` keeping its worked minutes; gives the new start and end.

    Same rule as the saved move (``commands.move_task``), so a bar lands where its drag
    preview showed it. The worked minutes are measured on ``calendar``, the row the bar
    leaves; a bar lying only on days closed since then keeps its weekday hours. They are
    rounded up to the half-hour, 30 at least. The start snaps to the half-hour and to a
    worked instant of ``target``, the row it is dropped on (``calendar`` when None), and the
    end follows from ``add_work``: a task moved across a weekend or a holiday grows on the
    wall clock but not in work.
    """
    worked = calendar.work_between(start, end)
    if worked <= 0:
        worked = WorkCalendar(week=calendar.week, cantons=()).work_between(start, end)
    minutes = max(SNAP_MINUTES, -(-worked // SNAP_MINUTES) * SNAP_MINUTES)
    row = target or calendar
    first = row.working_start(snap(new_start, SNAP_MINUTES))
    return first, row.add_work(first, minutes)


# ------------------------------------------------------------------ scale ----


def shown_days(calendar: WorkCalendar, first_day: date, last_day: date) -> list[date]:
    """Days of ``first_day``..``last_day`` with a column: the calendar's weekdays."""
    weekdays = calendar.workdays
    days: list[date] = []
    day = first_day
    while day <= last_day:
        if day.weekday() in weekdays:
            days.append(day)
        day += timedelta(days=1)
    return days


class TimeScale:
    """Pixel mapping of the display axis: one column of ``day_width`` per shown day.

    The columns' days off (``workday``, ``holiday``, ``periods``, ``closed``) follow the
    axis calendar; a row of another canton paints ``columns_for(row_calendar)`` and snaps
    with its own calendar.
    """

    __slots__ = (
        "_calendar",
        "_columns",
        "_day_width",
        "_edges",
        "_first_minute",
        "_index",
        "_ordinals",
        "_row_columns",
        "_span",
    )

    def __init__(
        self,
        calendar: WorkCalendar,
        first_day: date,
        last_day: date,
        day_width: float,
        *,
        window: tuple[int, int] | None = None,
    ) -> None:
        """``window`` replaces the drawn minutes of a day (``(0, 1440)``: the whole night)."""
        if not day_width > 0:
            raise ValueError("Largeur de jour invalide.")
        days = shown_days(calendar, first_day, last_day)
        if not days:
            raise ValueError("La plage du planning ne contient aucun jour affiche.")
        low, high = window or (calendar.day_start_minute, calendar.day_end_minute)
        if not 0 <= low < high <= MINUTES_PER_DAY:
            raise ValueError("Fenetre de journee invalide.")
        self._calendar = calendar
        self._day_width = float(day_width)
        self._first_minute = low
        self._span = high - low
        self._ordinals = tuple(day.toordinal() for day in days)
        self._index = {day: index for index, day in enumerate(days)}
        # Day edges computed once, so lookups by x agree with the drawn columns.
        self._edges = tuple(self._x(index * self._span) for index in range(len(days) + 1))
        self._columns = tuple(self._column(index, day, calendar) for index, day in enumerate(days))
        self._row_columns: dict[WorkCalendar, tuple[DayColumn, ...]] = {}

    def _column(self, index: int, day: date, calendar: WorkCalendar) -> DayColumn:
        holiday = calendar.holiday(day)
        left = self._edges[index]
        return DayColumn(
            day=day,
            index=index,
            x=left,
            width=self._edges[index + 1] - left,
            workday=calendar.is_workday(day),
            holiday=holiday.name if holiday is not None else None,
            periods=calendar.day_periods(day),
            closed=calendar.is_closed(day),
        )

    def _x(self, position: int) -> float:
        return position * self._day_width / self._span

    # Properties -------------------------------------------------------

    @property
    def calendar(self) -> WorkCalendar:
        return self._calendar

    @property
    def columns(self) -> tuple[DayColumn, ...]:
        return self._columns

    @property
    def first_day(self) -> date:
        return self._columns[0].day

    @property
    def last_day(self) -> date:
        return self._columns[-1].day

    @property
    def day_width(self) -> float:
        return self._day_width

    @property
    def display_minutes(self) -> int:
        """Minutes drawn per day (600 for 06:30-16:30)."""
        return self._span

    @property
    def first_minute(self) -> int:
        """First drawn minute of every day (390, 06:30, by default; 0 on the night axis)."""
        return self._first_minute

    @property
    def window(self) -> tuple[int, int]:
        """Drawn minutes of a day, ``(first, last)``."""
        return self._first_minute, self._first_minute + self._span

    @property
    def full_day(self) -> bool:
        """Whether the axis draws the whole 24 hours (the day view of a night)."""
        return self._first_minute == 0 and self._span == MINUTES_PER_DAY

    @property
    def minute_width(self) -> float:
        return self._day_width / self._span

    @property
    def width(self) -> float:
        return self._edges[-1]

    @property
    def start(self) -> WallTime:
        """First drawn instant, the left edge."""
        return day_start(self.first_day) + self._first_minute

    @property
    def end(self) -> WallTime:
        """Last drawn instant, the right edge."""
        return day_start(self.last_day) + self._first_minute + self._span

    # Mapping ----------------------------------------------------------

    def index_of(self, day: date) -> int | None:
        return self._index.get(day)

    def column_of(self, day: date) -> DayColumn | None:
        index = self._index.get(day)
        return self._columns[index] if index is not None else None

    def columns_for(self, calendar: WorkCalendar) -> tuple[DayColumn, ...]:
        """Give the columns with the days off of ``calendar``: a row's canton and closures.

        Positions stay the axis's, so a Vaud row on a Geneva axis shades Lundi du Jeûne.
        """
        if calendar is self._calendar:
            return self._columns
        columns = self._row_columns.get(calendar)
        if columns is None:
            columns = tuple(
                self._column(column.index, column.day, calendar) for column in self._columns
            )
            self._row_columns[calendar] = columns
        return columns

    def x_of(self, value: WallTime) -> float:
        """Horizontal position of ``value``.

        Nights, weekends and days outside the range collapse onto the nearest day edge; the
        end of a day (16:30) is that day's right edge, which is also the next column's left.
        """
        day = wall_date(value)
        index = self._index.get(day)
        if index is None:
            return self._edges[bisect.bisect_left(self._ordinals, day.toordinal())]
        offset = min(max(minute_of_day(value) - self._first_minute, 0), self._span)
        return self._x(index * self._span + offset)

    def span_x(self, start: WallTime, end: WallTime) -> tuple[float, float]:
        return self.x_of(start), self.x_of(end)

    def time_at(self, x: float, *, end: bool = False) -> WallTime:
        """Instant at ``x``, to the minute.

        A day boundary reads as the next morning, or as the previous evening when ``end``
        (an end dropped on the line between Monday and Tuesday is Monday 16:30).
        """
        last = len(self._columns) * self._span
        position = min(max(math.floor(x * self._span / self._day_width + 0.5), 0), last)
        index, offset = divmod(position, self._span)
        if index == len(self._columns) or (end and offset == 0 and index > 0):
            index, offset = index - 1, self._span
        return day_start(self._columns[index].day) + self._first_minute + offset

    def column_at(self, x: float) -> DayColumn:
        """Column under ``x``, clamped to the first and last column."""
        index = bisect.bisect_right(self._edges, x) - 1
        return self._columns[min(max(index, 0), len(self._columns) - 1)]

    def visible_columns(self, left: float, right: float) -> tuple[DayColumn, ...]:
        """Columns overlapping ``[left, right)``."""
        if right <= left:
            return ()
        first = max(bisect.bisect_right(self._edges, left) - 1, 0)
        last = min(bisect.bisect_left(self._edges, right), len(self._columns))
        return self._columns[first:last]

    # Header groups ----------------------------------------------------

    def week_groups(self, *, short: bool = False) -> tuple[ColumnGroup, ...]:
        """Columns grouped by ISO week, labelled ``Semaine 44`` (``S44`` when ``short``)."""
        return self._groups(week_start, lambda day: iso_week_label(day, short=short))

    def month_groups(self) -> tuple[ColumnGroup, ...]:
        """Columns grouped by month, labelled ``Octobre 2026``."""
        return self._groups(lambda day: day.replace(day=1), month_label)

    def _groups(
        self, key_of: Callable[[date], date], label_of: Callable[[date], str]
    ) -> tuple[ColumnGroup, ...]:
        keys = [key_of(column.day) for column in self._columns]
        groups: list[ColumnGroup] = []
        first = 0
        for index, column in enumerate(self._columns):
            following = index + 1
            if following < len(keys) and keys[following] == keys[index]:
                continue
            head = self._columns[first]
            groups.append(
                ColumnGroup(
                    key=keys[first],
                    label=label_of(head.day),
                    columns=range(first, following),
                    x=head.x,
                    width=column.right - head.x,
                )
            )
            first = following
        return tuple(groups)

    # Snapping ---------------------------------------------------------
    # The row's calendar is required: the axis calendar only knows the holidays of its
    # canton, while the saved commands snap on the calendar of the row.

    def snap_start(self, value: WallTime, view: ViewKind, *, calendar: WorkCalendar) -> WallTime:
        return snap_start(calendar, value, view)

    def snap_end(self, value: WallTime, view: ViewKind, *, calendar: WorkCalendar) -> WallTime:
        return snap_end(calendar, value, view)
