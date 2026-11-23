"""« Alerte notification »: when a task rings, for whom, and what it says.

A task carries ``reminders`` (``models.Reminder``): « 30 min avant » follows its start,
« à un moment précis » stays where it was set. The planning has no server: each poste
works out by itself, from the snapshot it holds, the alerts that came due since it last
looked (``due_alerts``) and keeps what it showed in its own ``AlertLog``.

A poste rings for the tasks of the person it belongs to (« qui je suis » in the
settings); a task nobody is assigned to rings for whoever created it, and a poste
without identity rings for every task.

``AlertRules`` holds what the poste chose in its settings: alerts on or off, for whom,
the hours of silence, and the two summaries (« Ma journée » in the morning, the next
working day in the evening) that ``digest_alerts`` builds at the times chosen.
"""

from __future__ import annotations

from collections.abc import Container, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.models import PlanningTask, Reminder
from projectflow.core.planning.night import WEEKDAY_SHORT
from projectflow.core.planning.walltime import (
    MINUTES_PER_DAY,
    MINUTES_PER_HOUR,
    WallTime,
    day_start,
    format_hhmm,
    minute_of_day,
    wall_date,
)

BEFORE_PRESETS: tuple[int, ...] = (0, 5, 15, 30, 60, 120, MINUTES_PER_DAY)
"""« Avant le début » offered in one click, in minutes."""
MISSED_AFTER_MINUTES = 5
"""An alert found this long after its time was missed (the poste was off or asleep)."""
LOOK_BACK_MINUTES = 3 * MINUTES_PER_DAY
"""A poste back after a long stop tells the alerts of the last days only."""
UNTITLED = "Tâche du planning"
DIGEST_WINDOW_MINUTES = 4 * MINUTES_PER_HOUR
"""A summary is still told this long after its time (a poste started late), not later."""
DIGEST_LINES = 3
"""Tasks named in a summary; the others are counted."""
SATURDAY = 5
AlertAudience = Literal["mine", "all"]


@dataclass(frozen=True, slots=True)
class AlertRules:
    """What a poste chose for its alerts (see ``PlanningPosteConfig.alert_rules``)."""

    enabled: bool = True
    audience: AlertAudience = "mine"
    """``mine``: the tasks of the person of the poste; ``all``: every task."""
    system: bool = True
    """Also show the alerts in the notification centre of Windows or macOS."""
    sound: bool = False
    quiet: bool = False
    """Hours of silence: an alert due then goes to the history without showing."""
    quiet_from: int = 18 * MINUTES_PER_HOUR
    quiet_to: int = 6 * MINUTES_PER_HOUR
    quiet_weekend: bool = True
    morning: int | None = None
    """Minute of the day of « Ma journée », None when it is off."""
    evening: int | None = None
    """Minute of the day of the summary of the next working day, None when it is off."""

    def is_quiet(self, now: WallTime) -> bool:
        """Whether ``now`` falls in the hours of silence (they may run over midnight)."""
        if not self.quiet:
            return False
        if self.quiet_weekend and wall_date(now).weekday() >= SATURDAY:
            return True
        minute = minute_of_day(now)
        if self.quiet_from == self.quiet_to:
            return False
        if self.quiet_from < self.quiet_to:
            return self.quiet_from <= minute < self.quiet_to
        return minute >= self.quiet_from or minute < self.quiet_to


@dataclass(frozen=True, slots=True)
class Alert:
    """One alert come due on this poste."""

    key: str
    """Task, alert and time it was due: a task that moves rings again at its new time."""
    task_id: str
    due: WallTime
    title: str
    body: str
    missed: bool = False
    day: int = 0
    """Summary only: ordinal of the day it tells (there is no task to open then)."""


def lead_text(minutes: int) -> str:
    """« 30 min », « 1 h 30 », « 2 j »."""
    days, rest = divmod(minutes, MINUTES_PER_DAY)
    if days and not rest:
        return f"{days} j"
    hours, mins = divmod(minutes, MINUTES_PER_HOUR)
    if hours and mins:
        return f"{hours} h {mins:02d}"
    return f"{hours} h" if hours else f"{mins} min"


def moment_text(value: WallTime, today: date | None = None) -> str:
    """« lun 23.11 à 07:00 »; « aujourd'hui à 07:00 », « demain à 07:00 » near ``today``."""
    day = wall_date(value)
    clock = format_hhmm(minute_of_day(value))
    if today is not None:
        gap = (day - today).days
        if gap == 0:
            return f"aujourd'hui à {clock}"
        if gap == 1:
            return f"demain à {clock}"
    return f"{WEEKDAY_SHORT[day.weekday()]} {day.day:02d}.{day.month:02d} à {clock}"


def reminder_text(reminder: Reminder, today: date | None = None) -> str:
    """How an alert reads on its chip: « 30 min avant », « Au début », « lun 23.11 à 07:00 »."""
    if reminder.kind == "at" and reminder.at is not None:
        return moment_text(reminder.at, today)
    if reminder.minutes == 0:
        return "Au début"
    return f"{lead_text(reminder.minutes)} avant"


def reminder_due(task: PlanningTask, reminder: Reminder) -> WallTime | None:
    """When ``reminder`` rings; None for « avant le début » of a task without dates."""
    if reminder.kind == "at":
        return reminder.at
    if task.start is None:
        return None
    return task.start - reminder.minutes


def my_resource_id(snapshot: PlanningSnapshot, *, initials: str, m365_id: str = "") -> str:
    """Row of the person this poste belongs to, or « » when it cannot be told."""
    wanted = initials.strip().casefold()
    by_initials = ""
    for resource in snapshot.resources.values():
        if resource.deleted:
            continue
        if m365_id and resource.m365_user_id == m365_id:
            return resource.id
        if wanted and not by_initials and resource.initials.strip().casefold() == wanted:
            by_initials = resource.id
    return by_initials


def concerns(task: PlanningTask, *, resource_id: str, initials: str) -> bool:
    """Whether the alerts of ``task`` ring on the poste of this person."""
    mine = initials.strip().casefold()
    if not resource_id and not mine:
        return True
    if task.assignments:
        return bool(resource_id) and resource_id in task.assignments
    author = task.created_by.initials.strip().casefold()
    return not author or author == mine


def alert_body(
    task: PlanningTask, reminder: Reminder, snapshot: PlanningSnapshot, today: date | None
) -> str:
    """Second line of an alert: when the task starts, then who does it."""
    if task.start is None:
        lead = "Rappel · tâche à planifier"
    elif reminder.kind == "at":
        lead = f"Rappel · début {moment_text(task.start, today)}"
    elif reminder.minutes == 0:
        lead = f"Commence maintenant · {moment_text(task.start, today)}"
    else:
        lead = f"Commence dans {lead_text(reminder.minutes)} · {moment_text(task.start, today)}"
    people = [
        resource.initials or resource.name
        for resource_id in task.assignments
        if (resource := snapshot.resources.get(resource_id)) is not None
    ]
    return f"{lead} · {', '.join(people)}" if people else lead


def due_alerts(
    snapshot: PlanningSnapshot,
    *,
    now: WallTime,
    since: WallTime,
    known: Container[str] = (),
    resource_id: str = "",
    initials: str = "",
) -> list[Alert]:
    """Alerts due after ``since`` and up to ``now`` that ``known`` does not hold yet.

    Finished and deleted tasks never ring. The oldest comes first.
    """
    today = wall_date(now)
    floor = max(since, now - LOOK_BACK_MINUTES)
    found: list[Alert] = []
    for task in snapshot.live_tasks():
        if not task.reminders or task.status == "done":
            continue
        if not concerns(task, resource_id=resource_id, initials=initials):
            continue
        for reminder in task.reminders:
            due = reminder_due(task, reminder)
            if due is None or not floor < due <= now:
                continue
            key = f"{task.id}|{reminder.key}|{due}"
            if key in known:
                continue
            found.append(
                Alert(
                    key=key,
                    task_id=task.id,
                    due=due,
                    title=task.label or UNTITLED,
                    body=alert_body(task, reminder, snapshot, today),
                    missed=now - due > MISSED_AFTER_MINUTES,
                )
            )
    return sorted(found, key=lambda alert: (alert.due, alert.key))


# ------------------------------------------------------------- summaries ----


def next_working_day(day: date) -> date:
    """Give the day after ``day``, Monday after a Friday: Saturday is never worked."""
    following = day + timedelta(days=1)
    while following.weekday() >= SATURDAY:
        following += timedelta(days=1)
    return following


def tasks_of_day(
    snapshot: PlanningSnapshot, day: date, *, resource_id: str = "", initials: str = ""
) -> list[PlanningTask]:
    """Tasks on the planning during ``day`` that concern the poste, the earliest first."""
    first = day_start(day)
    last = first + MINUTES_PER_DAY
    found = [
        task
        for task in snapshot.live_tasks()
        if task.start is not None
        and task.end is not None
        and task.status != "done"
        and task.start < last
        and task.end > first
        and concerns(task, resource_id=resource_id, initials=initials)
    ]
    return sorted(found, key=lambda task: (task.start or 0, task.id))


def digest_body(tasks: Sequence[PlanningTask], day: date) -> str:
    """« 3 tâches · 07:00 Pose garde-corps · 13:00 Livraison · +1 »."""
    first = day_start(day)
    count = "1 tâche" if len(tasks) == 1 else f"{len(tasks)} tâches"
    lines = [count]
    for task in tasks[:DIGEST_LINES]:
        start = task.start if task.start is not None else first
        clock = format_hhmm(minute_of_day(start)) if start >= first else "en cours"
        lines.append(f"{clock} {task.label or UNTITLED}")
    if len(tasks) > DIGEST_LINES:
        lines.append(f"+{len(tasks) - DIGEST_LINES}")
    return " · ".join(lines)


def day_title(day: date) -> str:
    return f"{WEEKDAY_SHORT[day.weekday()]} {day.day:02d}.{day.month:02d}"


def digest_alerts(
    snapshot: PlanningSnapshot,
    *,
    now: WallTime,
    rules: AlertRules,
    known: Container[str] = (),
    resource_id: str = "",
    initials: str = "",
) -> list[Alert]:
    """Summaries whose time has come today and that ``known`` does not hold yet.

    « Ma journée » tells the tasks of today, the evening summary those of the next
    working day. A day without task tells nothing.
    """
    today = wall_date(now)
    minute = minute_of_day(now)
    plans = (
        ("morning", rules.morning, today, "Ma journée"),
        ("evening", rules.evening, next_working_day(today), "À venir"),
    )
    found: list[Alert] = []
    for name, time, day, heading in plans:
        if time is None or not time <= minute <= time + DIGEST_WINDOW_MINUTES:
            continue
        key = f"digest|{name}|{today.isoformat()}"
        if key in known:
            continue
        tasks = tasks_of_day(snapshot, day, resource_id=resource_id, initials=initials)
        if not tasks:
            continue
        found.append(
            Alert(
                key=key,
                task_id="",
                due=day_start(today) + time,
                title=f"{heading} · {day_title(day)}",
                body=digest_body(tasks, day),
                day=day.toordinal(),
            )
        )
    return found
