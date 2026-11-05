"""Night work (« travail de nuit »): tasks outside the working calendar, e.g. airport jobs.

A night task keeps its exact wall-clock times (snapped to the half hour only) and lasts
real elapsed time, never worked time. A night belongs to the evening it starts: it starts
between 18:00 and 23:30, or after midnight before 06:00 (then it is still the previous
evening's night), and ends by 06:30 the next morning at the latest. Nights start Monday to
Thursday evenings only: a Thursday night ends on Friday morning, but there is no night
work on Friday evening nor during the weekend. Around every night the person rests
``MIN_REST_MINUTES`` (8 h, the derogation of the Swiss labour law the workshop uses)
before its start and after its end. Nothing here imports Qt.
"""

from __future__ import annotations

from datetime import date, timedelta

from projectflow.core.planning.calendar import SNAP_MINUTES, snap
from projectflow.core.planning.walltime import (
    MINUTES_PER_DAY,
    MINUTES_PER_HOUR,
    WallTime,
    day_start,
    format_hhmm,
    minute_of_day,
    wall_date,
)

NIGHT_EARLIEST_START = 18 * MINUTES_PER_HOUR
"""A night starts at 18:00 at the earliest (in the evening of its day)."""
NIGHT_MORNING_START = 6 * MINUTES_PER_HOUR
"""A start after midnight but before 06:00 belongs to the previous evening's night."""
NIGHT_LATEST_END = 6 * MINUTES_PER_HOUR + 30
"""A night ends at 06:30 the next morning at the latest."""
DEFAULT_NIGHT_START = 21 * MINUTES_PER_HOUR
DEFAULT_NIGHT_END = 5 * MINUTES_PER_HOUR
DEFAULT_NIGHT_MINUTES = MINUTES_PER_DAY - DEFAULT_NIGHT_START + DEFAULT_NIGHT_END
"""21:00 to 05:00: 8 h."""
MIN_NIGHT_MINUTES = SNAP_MINUTES
NIGHT_WEEKDAYS: frozenset[int] = frozenset({0, 1, 2, 3})
"""Evenings a night may start: Monday to Thursday."""
MIN_REST_MINUTES = 480
"""Rest kept before and after a night: 8 h (not the usual 11 h)."""

MOON = "\N{CRESCENT MOON}"
EN_DASH = "\N{EN DASH}"
ARROW = "\N{RIGHTWARDS ARROW}"
NIGHT_WEEKEND_MESSAGE = "Pas de travail de nuit le vendredi soir ni le week-end"
NIGHT_HOURS_MESSAGE = "Une nuit commence entre 18:00 et 06:00 et finit à 06:30 au plus tard"
NIGHT_SHORT_MESSAGE = "Une nuit dure au moins 30 minutes"
WEEKDAY_SHORT = ("lun", "mar", "mer", "jeu", "ven", "sam", "dim")


def night_of(start: WallTime) -> date | None:
    """Evening day of the night starting at ``start``; None for a daytime start."""
    minute = minute_of_day(start)
    if minute >= NIGHT_EARLIEST_START:
        return wall_date(start)
    if minute < NIGHT_MORNING_START:
        return wall_date(start) - timedelta(days=1)
    return None


def night_day(start: WallTime) -> date:
    """Day whose column shows a night task: its evening, else the day it starts."""
    day = night_of(start)
    return day if day is not None else wall_date(start)


def night_window(day: date) -> tuple[WallTime, WallTime]:
    """Night of ``day``: from 18:00 to 06:30 the next morning."""
    base = day_start(day)
    return base + NIGHT_EARLIEST_START, base + MINUTES_PER_DAY + NIGHT_LATEST_END


def night_allowed(day: date) -> bool:
    """Whether a night may start on the evening of ``day`` (Monday to Thursday)."""
    return day.weekday() in NIGHT_WEEKDAYS


def night_error(start: WallTime, end: WallTime) -> str | None:
    """Why ``[start, end)`` is not a valid night, None when it is one."""
    day = night_of(start)
    if day is None:
        return NIGHT_HOURS_MESSAGE
    if not night_allowed(day):
        return NIGHT_WEEKEND_MESSAGE
    if end - start < MIN_NIGHT_MINUTES:
        return NIGHT_SHORT_MESSAGE
    if end > night_window(day)[1]:
        return NIGHT_HOURS_MESSAGE
    return None


def is_night_span(start: WallTime, end: WallTime) -> bool:
    return night_error(start, end) is None


def default_night(day: date) -> tuple[WallTime, WallTime]:
    """21:00 on ``day`` to 05:00 the next morning."""
    base = day_start(day)
    return base + DEFAULT_NIGHT_START, base + MINUTES_PER_DAY + DEFAULT_NIGHT_END


def night_start_at(value: WallTime) -> WallTime:
    """Start of a night placed at ``value``: snapped, or 21:00 of its day when in daytime."""
    snapped = snap(value)
    if night_of(snapped) is not None:
        return snapped
    return default_night(wall_date(value))[0]


def night_minutes(minutes: int) -> int:
    """Elapsed minutes of a night estimate: rounded up to 30 minutes, 8 h when none."""
    if minutes <= 0:
        return DEFAULT_NIGHT_MINUTES
    return max(MIN_NIGHT_MINUTES, -(-minutes // SNAP_MINUTES) * SNAP_MINUTES)


def fitted_night(start: WallTime, minutes: int) -> tuple[WallTime, WallTime]:
    """Night from ``start`` lasting ``minutes``, cut at 06:30 the next morning."""
    day = night_day(start)
    end = min(start + night_minutes(minutes), night_window(day)[1])
    return start, max(end, start + MIN_NIGHT_MINUTES)


def shift_nights(day: date, nights: int) -> date:
    """Evening ``nights`` nights after ``day`` (before when negative), Friday to Sunday skipped."""
    step = 1 if nights >= 0 else -1
    current = day
    for _ in range(abs(nights)):
        current += timedelta(days=step)
        while not night_allowed(current):
            current += timedelta(days=step)
    return current


def rest_spans(start: WallTime, end: WallTime) -> tuple[tuple[WallTime, WallTime], ...]:
    """Rest kept around a night: 8 h before its start and 8 h after its end."""
    return ((start - MIN_REST_MINUTES, start), (end, end + MIN_REST_MINUTES))


def night_hours(start: WallTime, end: WallTime) -> str:
    """Hours of a night, ``22:00`` to ``05:00`` joined by an en dash."""
    return f"{format_hhmm(minute_of_day(start))}{EN_DASH}{format_hhmm(minute_of_day(end))}"


def elapsed_text(minutes: int) -> str:
    """Elapsed time: ``480`` -> ``8 h``, ``450`` -> ``7 h 30``, ``30`` -> ``30 min``."""
    hours, rest = divmod(max(minutes, 0), MINUTES_PER_HOUR)
    if not hours:
        return f"{rest} min"
    return f"{hours} h {rest:02d}" if rest else f"{hours} h"


def moment_text(value: WallTime) -> str:
    """``jeu 29.10 22:00``."""
    day = wall_date(value)
    return (
        f"{WEEKDAY_SHORT[day.weekday()]} {day.day:02d}.{day.month:02d} "
        f"{format_hhmm(minute_of_day(value))}"
    )


def night_label(start: WallTime, end: WallTime) -> str:
    """``🌙 nuit jeu 29.10 22:00 → ven 30.10 05:00 · 7 h``."""
    return (
        f"{MOON} nuit {moment_text(start)} {ARROW} {moment_text(end)} · {elapsed_text(end - start)}"
    )


def rest_text(start: WallTime, end: WallTime) -> str:
    """``Repos : dès lun 13:00 avant · jusqu'à mar 13:00 après``."""
    (before, _start), (_end, after) = rest_spans(start, end)
    return f"Repos : dès {moment_text(before)} avant · jusqu'à {moment_text(after)} après"
