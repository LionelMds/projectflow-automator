"""The shared planning, copied one way into an Outlook calendar for the phones.

ProjectFlow is the only source: the calendar (« Planning Balz Métal » of the shared
account) is rebuilt from the planning, then published from Outlook on the web as an ICS
link that iPhones and Android phones subscribe to, read only. A change made in Outlook is
overwritten by the next sync.

Each event carries a key (``task:<id>:<day>``, ``absence:<id>:<first day>``) and the
fingerprint of its content in extended properties: the calendar alone tells which events
exist and which changed, so any poste can take the sync over without any local state.
A task over several days gives one event per worked day (« 06:30 → 16:30 »), as the
phones show it best; a night stays one event at its exact times.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Final, Protocol

from projectflow.core.planning.calendar import WorkCalendar
from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.models import ABSENCE_LABELS, Absence, PlanningTask
from projectflow.core.planning.walltime import MINUTES_PER_DAY, day_start, wall_date

DEFAULT_CALENDAR_NAME: Final = "Planning Balz Métal"
PAST_DAYS: Final = 30
"""Days before today still copied; older events are left as they are."""
FUTURE_DAYS: Final = 365
CONCURRENT_WRITES: Final = 4
FINGERPRINT_DIGITS: Final = 16
DAY_KEY_PARTS: Final = 3

CalendarFor = Callable[[str | None], WorkCalendar]


@dataclass(frozen=True, slots=True)
class EventSpec:
    """One event of the calendar, as ProjectFlow wants it."""

    key: str
    subject: str
    start: datetime
    """Local wall time (Swiss time), without time zone."""
    end: datetime
    body: str = ""
    location: str = ""
    all_day: bool = False
    tentative: bool = False

    @property
    def fingerprint(self) -> str:
        text = "\n".join(
            (
                self.subject,
                self.start.isoformat(),
                self.end.isoformat(),
                self.body,
                self.location,
                str(self.all_day),
                str(self.tentative),
            )
        )
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:FINGERPRINT_DIGITS]


@dataclass(frozen=True, slots=True)
class ExistingEvent:
    event_id: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class SyncPlan:
    creates: tuple[EventSpec, ...] = ()
    updates: tuple[tuple[str, EventSpec], ...] = ()
    """``(event id, wanted content)``."""
    deletes: tuple[tuple[str, str], ...] = ()
    """``(key, event id)``."""

    @property
    def empty(self) -> bool:
        return not (self.creates or self.updates or self.deletes)


@dataclass(slots=True)
class SyncReport:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.created} ajouté(s)", f"{self.updated} modifié(s)"]
        parts.append(f"{self.deleted} supprimé(s)")
        if self.failed:
            parts.append(f"{self.failed} en échec")
        return ", ".join(parts)


class CalendarGateway(Protocol):
    """What the sync needs from Outlook (``graph.outlook_calendar``)."""

    async def ensure_calendar(self, name: str) -> str: ...

    async def list_events(self, calendar_id: str) -> dict[str, ExistingEvent]: ...

    async def create_event(self, calendar_id: str, spec: EventSpec) -> str: ...

    async def update_event(self, event_id: str, spec: EventSpec) -> None: ...

    async def delete_event(self, event_id: str) -> None: ...


# ------------------------------------------------------------- the events ----


def wall_datetime(value: int) -> datetime:
    """Local datetime of a wall time (minutes since day one)."""
    day = wall_date(value)
    return _midnight(day) + timedelta(minutes=value % MINUTES_PER_DAY)


def _midnight(day: date) -> datetime:
    # Wall time of the planning: naive on purpose, Graph gets the time zone beside it.
    return datetime.combine(day, time.min)


def sync_window(today: date) -> tuple[date, date]:
    return today - timedelta(days=PAST_DAYS), today + timedelta(days=FUTURE_DAYS)


def desired_events(
    snapshot: PlanningSnapshot,
    *,
    today: date,
    calendar_for: CalendarFor,
    hide_details: bool = False,
) -> dict[str, EventSpec]:
    """Events the calendar should hold for the planning around ``today``."""
    first, last = sync_window(today)
    names = {
        rid: resource.name for rid, resource in snapshot.resources.items() if not resource.deleted
    }
    events: dict[str, EventSpec] = {}
    for task in snapshot.live_tasks():
        for spec in task_events(
            task,
            snapshot=snapshot,
            names=names,
            calendar=calendar_for(next(iter(task.assignments), None)),
            hide_details=hide_details,
        ):
            if first <= spec.start.date() <= last:
                events[spec.key] = spec
    for absence in snapshot.live_absences():
        name = names.get(absence.resource_id)
        if name is None or absence.last_day < first or absence.first_day > last:
            continue
        spec = absence_event(absence, name, calendar_for(absence.resource_id))
        events[spec.key] = spec
    return events


def task_events(
    task: PlanningTask,
    *,
    snapshot: PlanningSnapshot,
    names: Mapping[str, str],
    calendar: WorkCalendar,
    hide_details: bool = False,
) -> list[EventSpec]:
    """Return the events of one scheduled task: one per worked day, one for a night."""
    if task.start is None or task.end is None:
        return []
    people = [names.get(rid, "?") for rid in task.assignments]
    subject = task.label or "Tâche"
    if people:
        subject = f"{subject} · {', '.join(people)}"
    if task.status == "provisional":
        subject = f"(provisoire) {subject}"
    kind = snapshot.meta.task_type(task.task_type)
    cache = task.project_cache
    lines = [f"Personnes : {', '.join(people) if people else 'non affecté'}"]
    lines.append(f"Type : {kind.label}")
    if not hide_details:
        if cache.client:
            lines.append(f"Client : {cache.client}")
        if task.notes:
            lines.append(f"Notes : {task.notes}")
    lines.append("Copie du planning ProjectFlow : à modifier dans ProjectFlow.")
    body = "\n".join(lines)
    location = "" if hide_details else cache.location
    tentative = task.status == "provisional"
    if task.off_hours:
        return [
            EventSpec(
                key=f"task:{task.id}:{wall_date(task.start).isoformat()}",
                subject=f"Nuit · {subject}",
                start=wall_datetime(task.start),
                end=wall_datetime(task.end),
                body=body,
                location=location,
                tentative=tentative,
            )
        ]
    return [
        EventSpec(
            key=f"task:{task.id}:{wall_date(begin).isoformat()}",
            subject=subject,
            start=wall_datetime(begin),
            end=wall_datetime(finish),
            body=body,
            location=location,
            tentative=tentative,
        )
        for begin, finish in day_segments(task.start, task.end, calendar)
    ]


def day_segments(start: int, end: int, calendar: WorkCalendar) -> list[tuple[int, int]]:
    """``[start, end)`` cut at each day, from its first to its last worked minute.

    A day without work (weekend, holiday) gives no segment, unless the task lies only
    on it: then its own times are kept.
    """
    segments: list[tuple[int, int]] = []
    day = wall_date(start)
    last = wall_date(end - 1)
    while day <= last:
        periods = calendar.day_periods(day)
        if periods:
            low, high = max(start, periods[0][0]), min(end, periods[-1][1])
            if high > low:
                segments.append((low, high))
        day += timedelta(days=1)
    return segments or [(start, end)]


def absence_event(absence: Absence, name: str, calendar: WorkCalendar) -> EventSpec:
    """« Absent : Karim (Vacances) », all day, or the morning or the afternoon only."""
    label = ABSENCE_LABELS.get(absence.reason, absence.reason)
    key = f"absence:{absence.id}:{absence.first_day.isoformat()}"
    subject = f"Absent : {name} ({label})"
    if absence.part == "day" or absence.first_day != absence.last_day:
        first = absence.first_day
        after = absence.last_day + timedelta(days=1)
        return EventSpec(
            key=key,
            subject=subject,
            start=_midnight(first),
            end=_midnight(after),
            body=absence.note,
            all_day=True,
        )
    periods = calendar.day_periods(absence.first_day)
    base = day_start(absence.first_day)
    if not periods:
        periods = ((base + 390, base + 720), (base + 780, base + 990))
    begin, finish = periods[0] if absence.part == "morning" else periods[-1]
    return EventSpec(
        key=key,
        subject=subject,
        start=wall_datetime(begin),
        end=wall_datetime(finish),
        body=absence.note,
    )


# ------------------------------------------------------------------- diff ----


def plan_sync(
    wanted: Mapping[str, EventSpec],
    existing: Mapping[str, ExistingEvent],
    *,
    today: date,
) -> SyncPlan:
    """Tell what to create, change and delete so that the calendar matches ``wanted``.

    An event of ours dated before the window is history: it stays.
    """
    first, _last = sync_window(today)
    creates = [spec for key, spec in wanted.items() if key not in existing]
    updates = [
        (existing[key].event_id, spec)
        for key, spec in wanted.items()
        if key in existing and existing[key].fingerprint != spec.fingerprint
    ]
    deletes = [
        (key, event.event_id)
        for key, event in existing.items()
        if key not in wanted and not _before(key, first)
    ]
    return SyncPlan(tuple(creates), tuple(updates), tuple(deletes))


def _before(key: str, first: date) -> bool:
    parts = key.split(":")
    if len(parts) != DAY_KEY_PARTS:
        return False
    try:
        return date.fromisoformat(parts[2]) < first
    except ValueError:
        return False


# ------------------------------------------------------------------- sync ----


class OutlookCalendarSync:
    """Keeps one Outlook calendar equal to the planning, one way."""

    def __init__(self, gateway: CalendarGateway, *, calendar_name: str) -> None:
        self._gateway = gateway
        self._name = calendar_name.strip() or DEFAULT_CALENDAR_NAME
        self._calendar_id = ""
        self._existing: dict[str, ExistingEvent] | None = None

    @property
    def calendar_name(self) -> str:
        return self._name

    def forget(self) -> None:
        """Read the calendar again at the next sync (repairs changes made in Outlook)."""
        self._existing = None

    async def sync(
        self,
        snapshot: PlanningSnapshot,
        *,
        today: date,
        calendar_for: CalendarFor,
        hide_details: bool = False,
    ) -> SyncReport:
        if not self._calendar_id:
            self._calendar_id = await self._gateway.ensure_calendar(self._name)
        if self._existing is None:
            self._existing = await self._gateway.list_events(self._calendar_id)
        wanted = desired_events(
            snapshot, today=today, calendar_for=calendar_for, hide_details=hide_details
        )
        plan = plan_sync(wanted, self._existing, today=today)
        return await self._apply(plan)

    async def _apply(self, plan: SyncPlan) -> SyncReport:
        report = SyncReport()
        existing = self._existing if self._existing is not None else {}
        limit = asyncio.Semaphore(CONCURRENT_WRITES)

        async def create(spec: EventSpec) -> None:
            async with limit:
                event_id = await self._gateway.create_event(self._calendar_id, spec)
            existing[spec.key] = ExistingEvent(event_id, spec.fingerprint)
            report.created += 1

        async def update(event_id: str, spec: EventSpec) -> None:
            async with limit:
                await self._gateway.update_event(event_id, spec)
            existing[spec.key] = ExistingEvent(event_id, spec.fingerprint)
            report.updated += 1

        async def delete(key: str, event_id: str) -> None:
            async with limit:
                await self._gateway.delete_event(event_id)
            existing.pop(key, None)
            report.deleted += 1

        jobs = [
            *(create(spec) for spec in plan.creates),
            *(update(event_id, spec) for event_id, spec in plan.updates),
            *(delete(key, event_id) for key, event_id in plan.deletes),
        ]
        results = await asyncio.gather(*jobs, return_exceptions=True)
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, BaseException):
                report.failed += 1
                report.errors.append(str(result))
        if report.failed:
            # Something unexpected (an event removed in Outlook…): read it all again next time.
            self._existing = None
        return report


def keys_of(events: Iterable[EventSpec]) -> set[str]:
    return {event.key for event in events}
