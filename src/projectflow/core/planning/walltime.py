"""Swiss wall-clock instants stored as whole minutes, without time zone.

The planning keeps local times: a task set at 06:30 stays at 06:30 on every poste and
across daylight saving changes. An instant is a plain ``int``, the date ordinal times
1440 plus the minutes since midnight, which keeps arithmetic, sorting and JSON trivial
and never builds a naive ``datetime``.
"""

from __future__ import annotations

import re
from datetime import date

MINUTES_PER_DAY = 1440
MINUTES_PER_HOUR = 60
HOURS_PER_DAY = 24

WallTime = int

_WALL_TIME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?$")


def wall_time(day: date, hour: int = 0, minute: int = 0) -> WallTime:
    """Instant of ``day`` at ``hour:minute``; 24:00 is accepted as the end of the day."""
    end_of_day = hour == HOURS_PER_DAY and minute == 0
    if not (0 <= hour < HOURS_PER_DAY and 0 <= minute < MINUTES_PER_HOUR) and not end_of_day:
        raise ValueError(f"Heure hors de la journee: {hour:02d}:{minute:02d}")
    return day.toordinal() * MINUTES_PER_DAY + hour * MINUTES_PER_HOUR + minute


def wall_date(value: WallTime) -> date:
    return date.fromordinal(value // MINUTES_PER_DAY)


def minute_of_day(value: WallTime) -> int:
    return value % MINUTES_PER_DAY


def day_start(day: date) -> WallTime:
    return day.toordinal() * MINUTES_PER_DAY


def format_wall_time(value: WallTime) -> str:
    """ISO text without zone, e.g. ``2026-10-26T06:30``."""
    hours, minutes = divmod(minute_of_day(value), MINUTES_PER_HOUR)
    return f"{wall_date(value).isoformat()}T{hours:02d}:{minutes:02d}"


def parse_wall_time(text: str) -> WallTime:
    match = _WALL_TIME_RE.match(text.strip())
    if match is None:
        raise ValueError(f"Date et heure illisibles: {text!r}")
    year, month, day, hour, minute = (int(part) for part in match.groups())
    return wall_time(date(year, month, day), hour, minute)


def format_hhmm(minutes: int) -> str:
    """``390`` -> ``06:30``."""
    hours, rest = divmod(minutes, MINUTES_PER_HOUR)
    return f"{hours:02d}:{rest:02d}"
