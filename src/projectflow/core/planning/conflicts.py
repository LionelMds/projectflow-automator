"""Problems shown as badges on the bars of the planning, and the load of a row.

A bar is one task on one row: ``(task_id, resource_id)``, with None for « Non affecté ».
Findings never block an edit; they only tell the planner what to look at: a person
absent during the task, a task starting on a day nobody works, a person booked beyond a
full day, a dossier that does not exist.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Literal

from projectflow.core.planning.calendar import WorkCalendar
from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.models import (
    ABSENCE_LABELS,
    Absence,
    DayPart,
    PlanningTask,
    RecurringOff,
    Resource,
)
from projectflow.core.planning.night import MIN_REST_MINUTES, moment_text
from projectflow.core.planning.walltime import (
    MINUTES_PER_DAY,
    MINUTES_PER_HOUR,
    WallTime,
    day_start,
    wall_date,
)

FindingKind = Literal["absence", "non_worked", "overload", "rest", "unknown_project"]
FINDING_KINDS: tuple[FindingKind, ...] = (
    "absence",
    "non_worked",
    "overload",
    "rest",
    "unknown_project",
)
FINDING_LABELS: Mapping[FindingKind, str] = {
    "absence": "Absence",
    "non_worked": "Jour non travaillé",
    "overload": "Surcharge",
    "rest": "Repos insuffisant",
    "unknown_project": "Dossier introuvable",
}
REST_MESSAGE = f"Repos de {MIN_REST_MINUTES // MINUTES_PER_HOUR} h non respecté"
WEEKDAY_NAMES = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
PART_LABELS: Mapping[str, str] = {"day": "journée", "morning": "matin", "afternoon": "après-midi"}
NOON_MINUTE = 12 * MINUTES_PER_HOUR
"""Split between morning and afternoon on a weekday worked in a single period."""

BarKey = tuple[str, str | None]
CalendarFor = Callable[[str | None], WorkCalendar]
ProjectExists = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class Finding:
    kind: FindingKind
    task_id: str
    resource_id: str | None
    message: str

    @property
    def label(self) -> str:
        return FINDING_LABELS[self.kind]


@dataclass(frozen=True, slots=True)
class _Off:
    """One absence (or recurring day off) of a resource on one day, as an interval."""

    start: WallTime
    end: WallTime
    message: str
    key: str


def findings_by_bar(
    snapshot: PlanningSnapshot,
    first_day: date,
    last_day: date,
    calendar_for: CalendarFor,
    project_exists: ProjectExists | None = None,
) -> dict[BarKey, list[Finding]]:
    """Return the findings of every bar of a task overlapping ``first_day``..``last_day``.

    Only bars with at least one finding are keys. A finding concerns the whole bar, so
    a bar crossing the edge of the period also reports what happens outside it.
    """
    low = day_start(first_day)
    high = day_start(last_day + timedelta(days=1))
    scheduled = _scheduled_tasks(snapshot)
    visible = [task for task in scheduled if _start(task) < high and _end(task) > low]
    rows = _Rows(snapshot, scheduled, visible, calendar_for)
    result: dict[BarKey, list[Finding]] = {}
    for task in visible:
        shared = _task_findings(task, project_exists)
        if not task.assignments:
            findings = [*_non_worked_findings(task, None, calendar_for(None)), *shared]
            if findings:
                result[(task.id, None)] = findings
            continue
        for resource_id in task.assignments:
            findings = [
                *rows.findings(task, resource_id),
                *(replace(item, resource_id=resource_id) for item in shared),
            ]
            if findings:
                result[(task.id, resource_id)] = findings
    return result


def resource_load(
    snapshot: PlanningSnapshot,
    resource_id: str,
    first_day: date,
    last_day: date,
    calendar: WorkCalendar,
) -> tuple[int, int]:
    """``(planned, available)`` worked minutes of a row over ``first_day``..``last_day``.

    Available time is the worked time of the calendar minus absences and recurring days
    off; planned time counts every scheduled task of the row, whatever its status, and
    the real minutes of its night work on top of the worked time.
    """
    low = day_start(first_day)
    high = day_start(last_day + timedelta(days=1))
    planned = sum(
        _planned_minutes(task, max(_start(task), low), min(_end(task), high), calendar)
        for task in _scheduled_tasks(snapshot)
        if resource_id in task.assignments
    )
    resource = snapshot.resources.get(resource_id)
    absences = _absences_by_resource(snapshot).get(resource_id, ())
    available = 0
    for day in _days(first_day, last_day):
        worked = calendar.worked_minutes(day)
        if worked:
            offs = _day_offs(resource, absences, calendar, day)
            available += worked - _covered_minutes(offs, calendar)
    return planned, available


def _planned_minutes(
    task: PlanningTask, start: WallTime, end: WallTime, calendar: WorkCalendar
) -> int:
    """Load of ``task`` in ``[start, end)``: worked minutes, or real ones for night work."""
    if task.off_hours:
        return max(end - start, 0)
    return calendar.work_between(start, end)


def format_duration(minutes: int) -> str:
    """``540`` -> ``9 h``, ``810`` -> ``13 h 30``, ``30`` -> ``30 min``."""
    hours, rest = divmod(minutes, MINUTES_PER_HOUR)
    if not hours:
        return f"{rest} min"
    return f"{hours} h {rest:02d}" if rest else f"{hours} h"


def format_day(day: date) -> str:
    """Swiss date, e.g. ``26.10.2026``."""
    return f"{day.day:02d}.{day.month:02d}.{day.year}"


# ------------------------------------------------------------- findings ----


class _Rows:
    """Tasks, absences and daily load of each row, computed once per row."""

    __slots__ = ("_absences", "_calendar_for", "_overloads", "_snapshot", "_tasks", "_visible")

    def __init__(
        self,
        snapshot: PlanningSnapshot,
        scheduled: Sequence[PlanningTask],
        visible: Sequence[PlanningTask],
        calendar_for: CalendarFor,
    ) -> None:
        self._snapshot = snapshot
        self._calendar_for = calendar_for
        self._absences = _absences_by_resource(snapshot)
        self._tasks: dict[str, list[PlanningTask]] = defaultdict(list)
        for task in scheduled:
            for resource_id in task.assignments:
                self._tasks[resource_id].append(task)
        self._visible: dict[str, list[PlanningTask]] = defaultdict(list)
        for task in visible:
            for resource_id in task.assignments:
                self._visible[resource_id].append(task)
        self._overloads: dict[str, dict[date, tuple[int, int]]] = {}

    def findings(self, task: PlanningTask, resource_id: str) -> list[Finding]:
        """Absence, non worked start and overload of one person's bar."""
        calendar = self._calendar_for(resource_id)
        resource = self._snapshot.resources.get(resource_id)
        offs = _offs(resource, self._absences.get(resource_id, ()), calendar, task)
        overloaded = self._overloads.get(resource_id)
        if overloaded is None:
            overloaded = _overloaded_days(
                self._tasks[resource_id], self._visible[resource_id], calendar
            )
            self._overloads[resource_id] = overloaded
        # A subcontractor takes several jobs at once: never overloaded, no rest kept.
        subcontractor = resource is not None and resource.is_subcontractor
        overload = (
            [] if subcontractor else _overload_findings(task, resource_id, overloaded, calendar)
        )
        rest = [] if subcontractor else _rest_findings(task, resource_id, self._tasks[resource_id])
        return [
            *_absence_findings(task, resource_id, offs, calendar),
            *_non_worked_findings(task, resource_id, calendar),
            *overload,
            *rest,
        ]


def _task_name(task: PlanningTask) -> str:
    return task.display_title or task.project_number or "tâche"


def _rest_findings(
    task: PlanningTask, resource_id: str, tasks: Sequence[PlanningTask]
) -> list[Finding]:
    """Rest of 8 h around the nights of one person, for the bar of ``task``.

    A night bar reports the other tasks of the person within 8 h of its start or end, and
    such a task reports the night it is too close to.
    """
    messages: list[str] = []
    for other in tasks:
        if other.id == task.id or not (task.off_hours or other.off_hours):
            continue
        night, near = (task, other) if task.off_hours else (other, task)
        night_start, night_end = _start(night), _end(night)
        near_start, near_end = _start(near), _end(near)
        if near_start >= night_end + MIN_REST_MINUTES or near_end <= night_start - MIN_REST_MINUTES:
            continue
        if near_start < night_end and near_end > night_start:
            gap = "pendant la nuit"
        elif near_start >= night_end:
            gap = f"{format_duration(near_start - night_end)} après la nuit"
        else:
            gap = f"{format_duration(night_start - near_end)} avant la nuit"
        kind = "" if task.off_hours else "nuit "
        subject = f"{kind}« {_task_name(other)} » ({moment_text(_start(other))})"
        message = f"{REST_MESSAGE} : {gap}, {subject}"
        if message not in messages:
            messages.append(message)
    return [
        Finding(kind="rest", task_id=task.id, resource_id=resource_id, message=message)
        for message in messages
    ]


def _task_findings(task: PlanningTask, project_exists: ProjectExists | None) -> list[Finding]:
    """Return the findings of the task itself, repeated on each of its bars."""
    findings: list[Finding] = []
    number = task.project_number.strip()
    if number and project_exists is not None and not project_exists(number):
        findings.append(
            Finding(
                kind="unknown_project",
                task_id=task.id,
                resource_id=None,
                message=f"Dossier {number} introuvable",
            )
        )
    return findings


def _absence_findings(
    task: PlanningTask,
    resource_id: str,
    offs: Sequence[_Off],
    calendar: WorkCalendar,
) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[str] = set()
    for off in offs:
        if off.key in seen:
            continue
        overlap_start = max(off.start, _start(task))
        overlap_end = min(off.end, _end(task))
        if _planned_minutes(task, overlap_start, overlap_end, calendar) > 0:
            seen.add(off.key)
            findings.append(
                Finding(
                    kind="absence", task_id=task.id, resource_id=resource_id, message=off.message
                )
            )
    return findings


def _non_worked_findings(
    task: PlanningTask,
    resource_id: str | None,
    calendar: WorkCalendar,
) -> list[Finding]:
    start = _start(task)
    # Night work starts outside the working hours on purpose.
    if task.off_hours or calendar.is_worked(start):
        return []
    day = wall_date(start)
    holiday = calendar.holiday(day)
    if holiday is not None:
        message = f"Début un jour férié : {holiday.name} ({format_day(day)})"
    elif calendar.is_closed(day):
        message = f"Début pendant la fermeture de l'entreprise ({format_day(day)})"
    elif not calendar.periods_of_weekday(day.weekday()):
        message = f"Début un {WEEKDAY_NAMES[day.weekday()]} non travaillé ({format_day(day)})"
    else:
        message = "Début hors des horaires de travail"
    return [Finding(kind="non_worked", task_id=task.id, resource_id=resource_id, message=message)]


def _overloaded_days(
    tasks: Sequence[PlanningTask],
    visible: Sequence[PlanningTask],
    calendar: WorkCalendar,
) -> dict[date, tuple[int, int]]:
    """``(planned, worked)`` minutes of the days, along the visible bars, booked beyond 100 %."""
    if not visible:
        return {}
    low = min(_start(task) for task in visible)
    high = max(_end(task) for task in visible)
    load: dict[date, int] = defaultdict(int)
    for task in tasks:
        start = max(_start(task), low)
        end = min(_end(task), high)
        for day, minutes in _daily_minutes(start, end, calendar):
            load[day] += minutes
    return {
        day: (planned, calendar.worked_minutes(day))
        for day, planned in load.items()
        if planned > calendar.worked_minutes(day)
    }


def _overload_findings(
    task: PlanningTask,
    resource_id: str,
    overloaded: Mapping[date, tuple[int, int]],
    calendar: WorkCalendar,
) -> list[Finding]:
    days = [
        day
        for day, minutes in _daily_minutes(_start(task), _end(task), calendar)
        if minutes and day in overloaded
    ]
    if not days:
        return []
    planned, worked = overloaded[days[0]]
    message = (
        f"Surcharge le {format_day(days[0])} : {format_duration(planned)} planifiées "
        f"pour {format_duration(worked)}"
    )
    if len(days) > 1:
        others = len(days) - 1
        message += (
            f" (et {others} autre{'s' if others > 1 else ''} jour{'s' if others > 1 else ''})"
        )
    return [Finding(kind="overload", task_id=task.id, resource_id=resource_id, message=message)]


# ------------------------------------------------------------- absences ----


def _offs(
    resource: Resource | None,
    absences: Sequence[Absence],
    calendar: WorkCalendar,
    task: PlanningTask,
) -> list[_Off]:
    offs: list[_Off] = []
    for day in _days(wall_date(_start(task)), wall_date(_end(task) - 1)):
        offs.extend(_day_offs(resource, absences, calendar, day))
    return offs


def _day_offs(
    resource: Resource | None,
    absences: Sequence[Absence],
    calendar: WorkCalendar,
    day: date,
) -> list[_Off]:
    offs: list[_Off] = []
    for absence in absences:
        if absence.first_day <= day <= absence.last_day:
            start, end = _part_interval(day, absence.part, calendar)
            offs.append(_Off(start, end, _absence_message(absence), f"absence:{absence.id}"))
    recurring: Iterable[RecurringOff] = resource.recurring_off if resource is not None else ()
    for index, off in enumerate(recurring):
        if off.weekday == day.weekday():
            start, end = _part_interval(day, off.part, calendar)
            offs.append(_Off(start, end, _recurring_message(off), f"recurring:{index}"))
    return offs


def _part_interval(day: date, part: DayPart, calendar: WorkCalendar) -> tuple[WallTime, WallTime]:
    base = day_start(day)
    if part == "day":
        return base, base + MINUTES_PER_DAY
    periods = calendar.periods_of_weekday(day.weekday())
    split = periods[0][1] if len(periods) > 1 else NOON_MINUTE
    if part == "morning":
        return base, base + split
    return base + split, base + MINUTES_PER_DAY


def _covered_minutes(offs: Sequence[_Off], calendar: WorkCalendar) -> int:
    """Worked minutes under the union of ``offs`` (overlapping absences count once)."""
    covered = 0
    current_start: WallTime | None = None
    current_end: WallTime = 0
    for off in sorted(offs, key=lambda item: item.start):
        if current_start is None or off.start > current_end:
            if current_start is not None:
                covered += calendar.work_between(current_start, current_end)
            current_start, current_end = off.start, off.end
        else:
            current_end = max(current_end, off.end)
    if current_start is not None:
        covered += calendar.work_between(current_start, current_end)
    return covered


def _absence_message(absence: Absence) -> str:
    part = f" ({PART_LABELS[absence.part]})" if absence.part != "day" else ""
    first = format_day(absence.first_day)
    if absence.first_day == absence.last_day:
        return f"{absence.label} le {first}{part}"
    return f"{absence.label} du {first} au {format_day(absence.last_day)}{part}"


def _recurring_message(off: RecurringOff) -> str:
    part = f" ({PART_LABELS[off.part]})" if off.part != "day" else ""
    label = ABSENCE_LABELS.get(off.reason, off.reason)
    return f"{label} chaque {WEEKDAY_NAMES[off.weekday]}{part}"


# -------------------------------------------------------------- helpers ----


def _scheduled_tasks(snapshot: PlanningSnapshot) -> list[PlanningTask]:
    return sorted(
        (task for task in snapshot.live_tasks() if task.scheduled),
        key=lambda task: (_start(task), task.id),
    )


def _absences_by_resource(snapshot: PlanningSnapshot) -> dict[str, list[Absence]]:
    absences: dict[str, list[Absence]] = defaultdict(list)
    for absence in snapshot.live_absences():
        absences[absence.resource_id].append(absence)
    return absences


def _start(task: PlanningTask) -> WallTime:
    return task.start if task.start is not None else 0


def _end(task: PlanningTask) -> WallTime:
    return task.end if task.end is not None else 0


def _days(first: date, last: date) -> Iterator[date]:
    day = first
    while day <= last:
        yield day
        day += timedelta(days=1)


def _daily_minutes(
    start: WallTime,
    end: WallTime,
    calendar: WorkCalendar,
) -> Iterator[tuple[date, int]]:
    """Worked minutes of ``[start, end)`` per day."""
    if end <= start:
        return
    for day in _days(wall_date(start), wall_date(end - 1)):
        base = day_start(day)
        yield day, calendar.work_between(max(start, base), min(end, base + MINUTES_PER_DAY))
