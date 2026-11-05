"""Working calendar of the workshop: hours per weekday, Geneva and Vaud public holidays.

Monday to Thursday are worked 06:30-12:00 and 13:00-16:30, Friday 06:30-12:00 (41 h 30
per week); Saturday and Sunday are never worked. All instants are wall-clock minutes
(``walltime``); durations are worked minutes, so the lunch break, Friday afternoon, the
weekend and holidays never count.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from projectflow.core.planning.walltime import (
    MINUTES_PER_DAY,
    WallTime,
    day_start,
    minute_of_day,
    wall_date,
)

WorkPeriod = tuple[int, int]
"""Worked minutes since midnight, start included, end excluded."""

WorkWeek = Mapping[int, tuple[WorkPeriod, ...]]
"""Worked periods per weekday (0 = Monday); a missing weekday is not worked."""

FULL_DAY: tuple[WorkPeriod, ...] = ((6 * 60 + 30, 12 * 60), (13 * 60, 16 * 60 + 30))
FRIDAY: tuple[WorkPeriod, ...] = ((6 * 60 + 30, 12 * 60),)
DEFAULT_WORK_WEEK: WorkWeek = {0: FULL_DAY, 1: FULL_DAY, 2: FULL_DAY, 3: FULL_DAY, 4: FRIDAY}
SNAP_MINUTES = 30
CANTONS = ("GE", "VD")
# A search for a worked instant stops after ten years without one.
MAX_SEARCH_DAYS = 3660
DAYS_PER_WEEK = 7

_JEUNE_GENEVOIS_SHIFT = 4  # Thursday after the first Sunday of September.
_ASCENSION_SHIFT = 39
_WHIT_MONDAY_SHIFT = 50


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
    """Official public holidays of Geneva and Vaud in ``year``, limited to ``cantons``."""
    easter = easter_sunday(year)
    both = ("GE", "VD")
    entries = (
        (date(year, 1, 1), "Nouvel An", both),
        (date(year, 1, 2), "Saint-Berchtold", ("VD",)),
        (easter - timedelta(days=2), "Vendredi saint", both),
        (easter + timedelta(days=1), "Lundi de Pâques", both),
        (easter + timedelta(days=_ASCENSION_SHIFT), "Ascension", both),
        (easter + timedelta(days=_WHIT_MONDAY_SHIFT), "Lundi de Pentecôte", both),
        (date(year, 8, 1), "Fête nationale", both),
        (
            _nth_sunday(year, 9, 1) + timedelta(days=_JEUNE_GENEVOIS_SHIFT),
            "Jeûne genevois",
            ("GE",),
        ),
        (_nth_sunday(year, 9, 3) + timedelta(days=1), "Lundi du Jeûne", ("VD",)),
        (date(year, 12, 25), "Noël", both),
        (date(year, 12, 31), "Restauration de la République", ("GE",)),
    )
    wanted = {canton.upper() for canton in cantons}
    holidays: dict[date, Holiday] = {}
    for day, name, where in entries:
        kept = tuple(canton for canton in where if canton in wanted)
        if kept:
            holidays[day] = Holiday(day=day, name=name, cantons=kept)
    return holidays


def normalize_canton(text: str) -> str:
    """``" ge "`` -> ``"GE"``; raises ValueError for a canton the planning does not know."""
    canton = text.strip().upper()
    if canton not in CANTONS:
        raise ValueError(f"Canton inconnu pour le planning: {text!r}")
    return canton


def snap(value: WallTime, step: int = SNAP_MINUTES) -> WallTime:
    """Round to the nearest ``step`` on the wall clock, halves up, inside the same day."""
    minutes = minute_of_day(value)
    snapped = min(max((minutes + step // 2) // step * step, 0), MINUTES_PER_DAY)
    return value - minutes + snapped


def _checked_periods(periods: Iterable[WorkPeriod]) -> tuple[WorkPeriod, ...]:
    ordered = tuple(sorted((int(start), int(end)) for start, end in periods))
    previous_end = 0
    for start, end in ordered:
        if not previous_end <= start < end <= MINUTES_PER_DAY:
            raise ValueError("Horaires de travail invalides.")
        previous_end = end
    return ordered


def _half_round(fraction: float) -> float:
    return round(fraction * 2) / 2


def _same_day_days(first: int, last: int, count: int) -> float:
    """Days of the periods ``first`` to ``last`` of a day of ``count`` periods: all is 1 j."""
    periods = last - first + 1
    return 1.0 if periods == count else periods / 2


class WorkCalendar:
    """Worked periods of one canton, with company closures."""

    __slots__ = ("_cantons", "_closures", "_holidays", "_week")

    def __init__(
        self,
        *,
        week: WorkWeek = DEFAULT_WORK_WEEK,
        cantons: Iterable[str] = CANTONS[:1],
        closures: Iterable[date] = (),
    ) -> None:
        checked = {
            weekday: _checked_periods(periods)
            for weekday, periods in week.items()
            if 0 <= weekday < DAYS_PER_WEEK
        }
        self._week = {weekday: periods for weekday, periods in checked.items() if periods}
        if not self._week:
            raise ValueError("Horaires de travail vides.")
        self._cantons = tuple(normalize_canton(canton) for canton in cantons)
        self._closures = frozenset(closures)
        self._holidays: dict[int, dict[date, Holiday]] = {}

    @property
    def week(self) -> Mapping[int, tuple[WorkPeriod, ...]]:
        return dict(self._week)

    @property
    def cantons(self) -> tuple[str, ...]:
        return self._cantons

    @property
    def workdays(self) -> frozenset[int]:
        """Weekdays with worked periods (Monday to Friday by default)."""
        return frozenset(self._week)

    @property
    def day_start_minute(self) -> int:
        """Earliest worked minute of any weekday: the left edge of a displayed day."""
        return min(periods[0][0] for periods in self._week.values())

    @property
    def day_end_minute(self) -> int:
        """Latest worked minute of any weekday: the right edge of a displayed day."""
        return max(periods[-1][1] for periods in self._week.values())

    @property
    def minutes_per_week(self) -> int:
        """Worked minutes of a week without holiday (2490, 41 h 30, by default)."""
        return sum(end - start for periods in self._week.values() for start, end in periods)

    def periods_of_weekday(self, weekday: int) -> tuple[WorkPeriod, ...]:
        return self._week.get(weekday, ())

    def holiday(self, day: date) -> Holiday | None:
        holidays = self._holidays.get(day.year)
        if holidays is None:
            holidays = cantonal_holidays(day.year, self._cantons)
            self._holidays[day.year] = holidays
        return holidays.get(day)

    def holidays_between(self, first: date, last: date) -> list[Holiday]:
        found: list[Holiday] = []
        for year in range(first.year, last.year + 1):
            self.holiday(date(year, 1, 1))
            found.extend(
                holiday for day, holiday in self._holidays[year].items() if first <= day <= last
            )
        return sorted(found, key=lambda holiday: holiday.day)

    def is_closed(self, day: date) -> bool:
        return day in self._closures

    def is_workday(self, day: date) -> bool:
        return (
            day.weekday() in self._week and day not in self._closures and self.holiday(day) is None
        )

    def worked_minutes(self, day: date) -> int:
        """Worked minutes of ``day`` (540 Monday to Thursday, 330 Friday, 0 otherwise)."""
        return sum(end - start for start, end in self.day_periods(day))

    def next_workday(self, day: date) -> date:
        """First working day strictly after ``day``."""
        candidate = day
        for _ in range(MAX_SEARCH_DAYS):
            candidate += timedelta(days=1)
            if self.is_workday(candidate):
                return candidate
        raise ValueError("Aucun jour ouvrable dans le calendrier.")

    def previous_workday(self, day: date) -> date:
        """Last working day strictly before ``day``."""
        candidate = day
        for _ in range(MAX_SEARCH_DAYS):
            candidate -= timedelta(days=1)
            if self.is_workday(candidate):
                return candidate
        raise ValueError("Aucun jour ouvrable dans le calendrier.")

    def day_periods(self, day: date) -> tuple[tuple[WallTime, WallTime], ...]:
        """Worked periods of ``day`` as instants; empty for weekends, holidays, closures."""
        if not self.is_workday(day):
            return ()
        base = day_start(day)
        return tuple((base + start, base + end) for start, end in self._week[day.weekday()])

    def work_between(self, start: WallTime, end: WallTime) -> int:
        """Worked minutes in ``[start, end)``."""
        if end <= start:
            return 0
        total = 0
        day = wall_date(start)
        last = wall_date(end - 1)
        while day <= last:
            for low, high in self.day_periods(day):
                total += max(0, min(end, high) - max(start, low))
            day += timedelta(days=1)
        return total

    def is_worked(self, value: WallTime) -> bool:
        """Whether the minute starting at ``value`` is worked."""
        return any(low <= value < high for low, high in self.day_periods(wall_date(value)))

    def working_start(self, value: WallTime) -> WallTime:
        """First worked instant at or after ``value``: a start never sits in a break."""
        day = wall_date(value)
        for _ in range(MAX_SEARCH_DAYS):
            for low, high in self.day_periods(day):
                if value < high:
                    return max(value, low)
            day += timedelta(days=1)
        raise ValueError("Aucun jour ouvrable dans le calendrier.")

    def working_end(self, value: WallTime) -> WallTime:
        """Last worked instant at or before ``value``: an end never sits in a break."""
        day = wall_date(value)
        for _ in range(MAX_SEARCH_DAYS):
            for low, high in reversed(self.day_periods(day)):
                if value > low:
                    return min(value, high)
            day -= timedelta(days=1)
        raise ValueError("Aucun jour ouvrable dans le calendrier.")

    def add_work(self, start: WallTime, minutes: int) -> WallTime:
        """Instant reached after ``minutes`` of work from ``start``.

        An end that falls exactly at the end of a period stays there (12:00 or 16:30)
        instead of jumping to the next period's start.
        """
        if minutes < 0:
            return self.subtract_work(start, -minutes)
        current = self.working_start(start)
        remaining = minutes
        day = wall_date(current)
        for _ in range(MAX_SEARCH_DAYS):
            for low, high in self.day_periods(day):
                if high <= current:
                    continue
                begin = max(current, low)
                if remaining <= high - begin:
                    return begin + remaining
                remaining -= high - begin
            day += timedelta(days=1)
        raise ValueError("Aucun jour ouvrable dans le calendrier.")

    def subtract_work(self, end: WallTime, minutes: int) -> WallTime:
        """Instant from which ``minutes`` of work end exactly at ``end``."""
        if minutes < 0:
            return self.add_work(end, -minutes)
        current = self.working_end(end)
        remaining = minutes
        day = wall_date(current)
        for _ in range(MAX_SEARCH_DAYS):
            for low, high in reversed(self.day_periods(day)):
                if low >= current:
                    continue
                finish = min(current, high)
                if remaining <= finish - low:
                    return finish - remaining
                remaining -= finish - low
            day -= timedelta(days=1)
        raise ValueError("Aucun jour ouvrable dans le calendrier.")

    def add_days(self, start: WallTime, days: float) -> WallTime:
        """End of a duration counted in working days, as people say it (« 3 j »).

        Days are counted as ``whole_days`` reads them back, so a bar says what it was
        given: the period of ``start`` counts from its beginning (an afternoon start is
        half of its day, a start at 09:00 the whole morning), a day of two periods has two
        halves and Friday, worked until noon, is one whole day. The end is the first
        period end where the count reaches ``days``, rounded to the half day (½ j at
        least): « 2 j » from Thursday 13:00 ends on Monday at 12:00. Where Friday makes
        the count jump by a whole day, the end goes past rather than short (« 1 j » from
        Thursday 13:00 ends on Friday at 12:00, nine hours of work).
        """
        target = max(round(days * 2), 1) / 2
        current = self.working_start(start)
        first = wall_date(current)
        periods = self.day_periods(first)
        index = next(i for i, (_low, high) in enumerate(periods) if current < high)
        for last in range(index, len(periods)):
            if _same_day_days(index, last, len(periods)) >= target:
                return periods[last][1]
        leading = _half_round((len(periods) - index) / len(periods))
        day = first
        for between in range(MAX_SEARCH_DAYS):
            # ``between`` whole working days lie between the first day and this one.
            day = self.next_workday(day)
            periods = self.day_periods(day)
            for last, (_low, high) in enumerate(periods):
                if between + leading + _half_round((last + 1) / len(periods)) >= target:
                    return high
        raise ValueError("Aucun jour ouvrable dans le calendrier.")

    def whole_days(self, start: WallTime, end: WallTime) -> float | None:
        """Working days covered by ``[start, end)`` when it is exactly whole or half days.

        ``None`` when the span starts or ends inside a period: it is then shown in hours.
        ``add_days`` counts the same way.
        """
        if end <= start:
            return None
        first, last = wall_date(start), wall_date(end - 1)
        first_periods, last_periods = self.day_periods(first), self.day_periods(last)
        if not first_periods or not last_periods:
            return None
        starts = [low for low, _ in first_periods]
        ends = [high for _, high in last_periods]
        if start not in starts or end not in ends:
            return None
        if first == last:
            return _same_day_days(starts.index(start), ends.index(end), len(first_periods))
        days = len(self.days_between(first, last))
        leading = (len(first_periods) - starts.index(start)) / len(first_periods)
        trailing = (ends.index(end) + 1) / len(last_periods)
        return days - 2 + _half_round(leading) + _half_round(trailing)

    def days_between(self, first: date, last: date) -> list[date]:
        """Working days from ``first`` to ``last`` included."""
        days: list[date] = []
        day = first
        while day <= last:
            if self.is_workday(day):
                days.append(day)
            day += timedelta(days=1)
        return days
