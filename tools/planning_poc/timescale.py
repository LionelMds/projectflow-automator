"""Time axis of the Planning grid: working calendar, display positions, snapping, lanes.

Only weekdays are shown, each one from DAY_START to DAY_END with the lunch break
drawn but not worked, so Friday evening is directly followed by Monday morning.
Every instant is handled as a *display position*: whole minutes along that
compressed axis, 0 being the first displayed morning. Pixels are one
multiplication away (``TimeScale``), so the body, the header and the row column
all share the same mapping. Nothing here imports Qt.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum

# Placeholder working hours, to be confirmed by the workshop.
DAY_START = time(7, 0)
LUNCH_START = time(12, 0)
LUNCH_END = time(13, 0)
DAY_END = time(16, 30)
SNAP_MINUTES = 30
CANTONS = ("GE", "VD")


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


DAY_SPAN = _minutes(DAY_END) - _minutes(DAY_START)
"""Displayed minutes per day, lunch included (570 with the placeholder hours)."""
MORNING = (0, _minutes(LUNCH_START) - _minutes(DAY_START))
AFTERNOON = (_minutes(LUNCH_END) - _minutes(DAY_START), DAY_SPAN)
WORK_PERIODS = (MORNING, AFTERNOON)
WORK_PER_DAY = sum(end - start for start, end in WORK_PERIODS)
"""Worked minutes per day (510 with the placeholder hours)."""

_HALF_STARTS = (MORNING[0], AFTERNOON[0])
_HALF_LENGTHS = (MORNING[1] - MORNING[0], AFTERNOON[1] - AFTERNOON[0])


class Snap(Enum):
    HALF_HOUR = "half-hour"
    HALF_DAY = "half-day"


# ------------------------------------------------------------ holidays ----


@dataclass(frozen=True, slots=True)
class Holiday:
    day: date
    name: str
    cantons: tuple[str, ...]


def easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    golden = year % 19
    century, year_of_century = divmod(year, 100)
    leap_centuries, century_rest = divmod(century, 4)
    correction = (century + 8) // 25
    moon = (century - correction + 1) // 3
    epact = (19 * golden + century - leap_centuries - moon + 15) % 30
    quarter, quarter_rest = divmod(year_of_century, 4)
    weekday = (32 + 2 * century_rest + 2 * quarter - epact - quarter_rest) % 7
    shift = (golden + 11 * epact + 22 * weekday) // 451
    month, day = divmod(epact + weekday - 7 * shift + 114, 31)
    return date(year, month, day + 1)


def _nth_sunday(year: int, month: int, nth: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (nth - 1))


def cantonal_holidays(year: int, cantons: Iterable[str] = CANTONS) -> dict[date, Holiday]:
    """Public holidays of Geneva and Vaud for ``year``, limited to ``cantons``."""
    easter = easter_sunday(year)
    both = ("GE", "VD")
    entries = (
        (date(year, 1, 1), "Nouvel An", both),
        (date(year, 1, 2), "Saint-Berchtold", ("VD",)),
        (easter - timedelta(days=2), "Vendredi saint", both),
        (easter + timedelta(days=1), "Lundi de Pâques", both),
        (easter + timedelta(days=39), "Ascension", both),
        (easter + timedelta(days=50), "Lundi de Pentecôte", both),
        (date(year, 8, 1), "Fête nationale", both),
        (_nth_sunday(year, 9, 1) + timedelta(days=4), "Jeûne genevois", ("GE",)),
        (_nth_sunday(year, 9, 3) + timedelta(days=1), "Lundi du Jeûne", ("VD",)),
        (date(year, 12, 25), "Noël", both),
        (date(year, 12, 31), "Restauration de la République", ("GE",)),
    )
    wanted = set(cantons)
    holidays: dict[date, Holiday] = {}
    for day, name, where in entries:
        kept = tuple(canton for canton in where if canton in wanted)
        if kept:
            holidays[day] = Holiday(day=day, name=name, cantons=kept)
    return holidays


# ------------------------------------------------------------ calendar ----


class WorkCalendar:
    """Weekdays shown on the axis and the holidays among them that are not worked."""

    __slots__ = ("_index", "_working", "days", "holidays")

    def __init__(self, first: date, day_count: int, cantons: Iterable[str] = CANTONS) -> None:
        span = [first + timedelta(days=offset) for offset in range(max(day_count, 0))]
        self.days: tuple[date, ...] = tuple(day for day in span if day.weekday() < 5)
        if not self.days:
            raise ValueError("La plage du planning ne contient aucun jour ouvrable.")
        wanted = tuple(cantons)
        holidays: dict[date, Holiday] = {}
        for year in range(self.days[0].year, self.days[-1].year + 1):
            holidays.update(cantonal_holidays(year, wanted))
        self._index = {day: index for index, day in enumerate(self.days)}
        self.holidays = {day: holiday for day, holiday in holidays.items() if day in self._index}
        self._working = tuple(day not in self.holidays for day in self.days)
        if not any(self._working):
            raise ValueError("La plage du planning ne contient aucun jour ouvrable.")

    def __len__(self) -> int:
        return len(self.days)

    @property
    def end(self) -> int:
        """Display position of the last evening."""
        return len(self.days) * DAY_SPAN

    def index_of(self, day: date) -> int | None:
        return self._index.get(day)

    def index_at_or_after(self, day: date) -> int:
        return min(bisect.bisect_left(self.days, day), len(self.days) - 1)

    def is_working(self, index: int) -> bool:
        return self._working[index]

    def week_start(self, index: int) -> int:
        """Index of the Monday (or first shown day) of the week holding ``index``."""
        day = self.days[index]
        return bisect.bisect_left(self.days, day - timedelta(days=day.weekday()))

    def week_range(self, index: int) -> range:
        start = self.week_start(index)
        monday = self.days[start] - timedelta(days=self.days[start].weekday())
        return range(start, bisect.bisect_left(self.days, monday + timedelta(days=7)))

    def month_range(self, index: int) -> range:
        day = self.days[index]
        first = day.replace(day=1)
        following = (first + timedelta(days=32)).replace(day=1)
        return range(bisect.bisect_left(self.days, first), bisect.bisect_left(self.days, following))

    # Display positions ------------------------------------------------

    def position_of(self, moment: datetime) -> int:
        """Display position of ``moment``; nights and weekends collapse onto a day edge."""
        index = self._index.get(moment.date())
        if index is None:
            return min(bisect.bisect_left(self.days, moment.date()), len(self.days)) * DAY_SPAN
        offset = _minutes(moment.time()) - _minutes(DAY_START)
        return index * DAY_SPAN + min(max(offset, 0), DAY_SPAN)

    def moment_at(self, position: float, *, end: bool = False) -> datetime:
        """Datetime at ``position``; a day edge reads as the evening before when ``end``."""
        clamped = min(max(round(position), 0), self.end)
        index, offset = divmod(clamped, DAY_SPAN)
        if index == len(self.days) or (end and offset == 0 and index > 0):
            index, offset = index - 1, DAY_SPAN
        return datetime.combine(self.days[index], DAY_START) + timedelta(minutes=offset)

    # Working time -----------------------------------------------------

    def work_between(self, start: int, end: int) -> int:
        """Worked minutes between two display positions (lunch and holidays excluded)."""
        if end <= start:
            return 0
        total = 0
        last = min((end - 1) // DAY_SPAN, len(self.days) - 1)
        for index in range(max(start // DAY_SPAN, 0), last + 1):
            if not self._working[index]:
                continue
            base = index * DAY_SPAN
            for low, high in WORK_PERIODS:
                total += max(0, min(end, base + high) - max(start, base + low))
        return total

    def add_work(self, start: int, minutes: int) -> int:
        """Display position reached after ``minutes`` of working time from ``start``."""
        remaining = minutes
        for index in range(max(start // DAY_SPAN, 0), len(self.days)):
            if not self._working[index]:
                continue
            base = index * DAY_SPAN
            for low, high in WORK_PERIODS:
                begin = max(start, base + low)
                if begin >= base + high:
                    continue
                if remaining <= base + high - begin:
                    return begin + remaining
                remaining -= base + high - begin
        return self.end

    def latest_start(self, minutes: int) -> int:
        """Latest display position from which ``minutes`` of work still end by the last evening."""
        remaining = minutes
        for index in range(len(self.days) - 1, -1, -1):
            if not self._working[index]:
                continue
            base = index * DAY_SPAN
            for low, high in reversed(WORK_PERIODS):
                if remaining <= high - low:
                    return base + high - remaining
                remaining -= high - low
        return 0

    def working_start(self, position: int) -> int:
        """First worked instant at or after ``position``: a start never sits in a break."""
        for index in range(max(position // DAY_SPAN, 0), len(self.days)):
            if not self._working[index]:
                continue
            base = index * DAY_SPAN
            for low, high in WORK_PERIODS:
                if position < base + high:
                    return max(position, base + low)
        return self.working_end(self.end) - SNAP_MINUTES

    def working_end(self, position: int) -> int:
        """Last worked instant at or before ``position``: an end never sits in a break."""
        for index in range(min((position - 1) // DAY_SPAN, len(self.days) - 1), -1, -1):
            if not self._working[index]:
                continue
            base = index * DAY_SPAN
            for low, high in reversed(WORK_PERIODS):
                if position > base + low:
                    return min(position, base + high)
        return self.working_start(0) + SNAP_MINUTES


# ------------------------------------------------------------- snapping ----


def snap_minutes(value: float, step: int = SNAP_MINUTES) -> int:
    """Round a position or a delta to the half-hour grid, halves up (days start on the grid)."""
    return math.floor(value / step + 0.5) * step


def shift_half_days(position: int, halves: int) -> int:
    """Move by whole half-days, keeping the offset inside the morning or afternoon."""
    index, offset = divmod(position, DAY_SPAN)
    half = 0 if offset < MORNING[1] else 1
    within = max(0, offset - _HALF_STARTS[half])
    target_index, target_half = divmod(index * 2 + half + halves, 2)
    within = min(within, _HALF_LENGTHS[target_half] - SNAP_MINUTES)
    return target_index * DAY_SPAN + _HALF_STARTS[target_half] + within


def nearest_half_day_edge(position: float, *, start: bool) -> int:
    """Closest half-day boundary: 07:00 / 13:00 for a start, 12:00 / 16:30 for an end."""
    base = int(position // DAY_SPAN) * DAY_SPAN
    middle = AFTERNOON[0] if start else MORNING[1]
    candidates = (base, base + middle, base + DAY_SPAN)
    return min(candidates, key=lambda candidate: abs(candidate - position))


# ---------------------------------------------------------------- scale ----


@dataclass(frozen=True, slots=True)
class TimeScale:
    """Pixel mapping of the display axis: one width per displayed day."""

    calendar: WorkCalendar
    day_width: float

    @property
    def minute_width(self) -> float:
        return self.day_width / DAY_SPAN

    @property
    def width(self) -> float:
        return len(self.calendar) * self.day_width

    def x_of(self, position: float) -> float:
        return position * self.day_width / DAY_SPAN

    def position_at(self, x: float) -> float:
        return x * DAY_SPAN / self.day_width

    def day_x(self, index: int) -> float:
        return index * self.day_width

    def day_at(self, x: float) -> int:
        return min(max(int(x // self.day_width), 0), len(self.calendar) - 1)

    def visible_days(self, left: float, right: float) -> range:
        first = max(int(left // self.day_width), 0)
        last = min(int(right // self.day_width) + 1, len(self.calendar))
        return range(first, max(first, last))


# ---------------------------------------------------------------- lanes ----


def assign_lanes(spans: Sequence[tuple[int, int]]) -> tuple[list[int], int]:
    """First-fit lane of every span so that spans sharing a lane never overlap."""
    order = sorted(range(len(spans)), key=lambda index: (spans[index][0], -spans[index][1]))
    lane_ends: list[int] = []
    lanes = [0] * len(spans)
    for index in order:
        start, end = spans[index]
        for lane, lane_end in enumerate(lane_ends):
            if lane_end <= start:
                lane_ends[lane] = end
                lanes[index] = lane
                break
        else:
            lanes[index] = len(lane_ends)
            lane_ends.append(end)
    return lanes, max(1, len(lane_ends))
