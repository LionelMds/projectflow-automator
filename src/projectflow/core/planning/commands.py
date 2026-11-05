"""Editing commands of the planning: pure functions from a snapshot to changes.

Each command reads a ``PlanningSnapshot`` through a ``CommandContext`` and returns the
``DocumentChange`` tuple to apply at once (one undo step); an empty tuple means nothing
changes, and the inputs are never modified. Every written document is stamped with the
context's author and time. Instants are snapped to the half hour and kept on worked
time of the calendar of the row concerned; durations are worked minutes, so a task
keeps its amount of work when it moves over a lunch break, a Friday afternoon, a
weekend or a public holiday.

A task without dates waits in « À planifier »; its assignments are then the people
foreseen (« pressentis »). A scheduled task without assignment sits on the « Non
affecté » row, whose row id is ``UNASSIGNED``. A card estimated in working days
(``estimate_days``, to the half day) is placed by days, as people count them: « 2 j »
from a Thursday ends on Friday at noon, from Thursday 13:00 on Monday at noon. A card
back from the grid keeps its own days while its bar is still what they gave, else the
whole or half days its dates covered.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any, TypeVar

from pydantic import ValidationError

from projectflow.core.planning.calendar import CANTONS, SNAP_MINUTES, WorkCalendar, snap
from projectflow.core.planning.changes import DocumentChange, PlanningSnapshot
from projectflow.core.planning.models import (
    STATUS_LABELS,
    Absence,
    AbsenceReason,
    Author,
    DayPart,
    PlanningDocument,
    PlanningMeta,
    PlanningTask,
    ProjectCache,
    RecurringOff,
    Resource,
    ResourceKind,
    TaskOrigin,
    TaskStatus,
    TaskType,
    default_task_type_key,
)
from projectflow.core.planning.night import (
    MIN_NIGHT_MINUTES,
    default_night,
    fitted_night,
    night_day,
    night_error,
    night_start_at,
    shift_nights,
)
from projectflow.core.planning.quick_entry import DAY_MINUTES, MAX_DURATION_MINUTES, round_days
from projectflow.core.planning.ranks import initial_ranks, rank_between
from projectflow.core.planning.walltime import (
    MINUTES_PER_DAY,
    WallTime,
    day_start,
    minute_of_day,
    wall_date,
)
from projectflow.exceptions import ProjectFlowError

UNASSIGNED = "__unassigned__"
"""Row id of « Non affecté »."""
MIN_TASK_MINUTES = SNAP_MINUTES
DEFAULT_TASK_MINUTES = 540
"""Duration given to a card without estimate: one full worked day (Monday to Thursday)."""
MIN_ESTIMATE_DAYS = 0.5
MAX_ESTIMATE_DAYS = MAX_DURATION_MINUTES // DAY_MINUTES
"""Longest estimate in working days, the longest duration quick entry accepts."""
INVALID_DAYS_MESSAGE = "Valeur invalide pour le champ estimate_days."
ORDER_STEP = 1000
"""Gap between the ``order`` of two rows, so that a move rewrites one row only."""
DEFAULT_TEAM = "atelier"
SUBCONTRACTOR_TEAM = "soustraitants"
INITIALS_LENGTH = 3

_ENVELOPE_FIELDS = frozenset(
    {
        "id",
        "schema_version",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "deleted_at",
        "deleted_by",
    }
)
_TASK_FIELDS = frozenset(
    {
        "title",
        "task_type",
        "project_number",
        "project_cache",
        "project_fingerprint",
        "notes",
        "deadline",
        "urgent",
        "required_people",
        "estimate_minutes",
        "estimate_days",
    }
)
_RESOURCE_FIELDS = frozenset(
    {
        "name",
        "initials",
        "kind_of_resource",
        "team",
        "role",
        "apprentice",
        "canton",
        "color",
        "active",
        "m365_user_id",
        "recurring_off",
    }
)
_ABSENCE_FIELDS = frozenset({"resource_id", "reason", "first_day", "last_day", "part", "note"})
_META_FIELDS = frozenset(
    {
        "work_week",
        "default_canton",
        "closures",
        "teams",
        "task_types",
        "auto_card_on_sortie",
        "min_app_version",
    }
)

_Document = TypeVar("_Document", bound=PlanningDocument)
CalendarFor = Callable[[str | None], WorkCalendar]
Changes = tuple[DocumentChange, ...]


class PlanningCommandError(ProjectFlowError):
    """A planning edit that cannot be made; the message is meant for the user."""


@dataclass(frozen=True, slots=True)
class CommandContext:
    snapshot: PlanningSnapshot
    author: Author
    now: str
    """UTC time stamped on every changed document (``utc_now_text`` format)."""
    calendar_for: CalendarFor
    """Calendar of a row: resource id, or None for « Non affecté » and the default canton."""


def calendar_resolver(snapshot: PlanningSnapshot) -> CalendarFor:
    """``calendar_for`` built from the shared settings: hours, closures, canton of each row."""
    meta = snapshot.meta
    closures = tuple(
        closure.first_day + timedelta(days=offset)
        for closure in meta.closures
        for offset in range((closure.last_day - closure.first_day).days + 1)
    )
    default_canton = (meta.default_canton or CANTONS[0]).upper()
    calendars: dict[str, WorkCalendar] = {}

    def calendar_for(resource_id: str | None) -> WorkCalendar:
        resource = snapshot.resources.get(resource_id) if resource_id else None
        canton = (resource.canton.upper() if resource is not None else "") or default_canton
        calendar = calendars.get(canton)
        if calendar is None:
            calendar = WorkCalendar(week=meta.work_week, cantons=(canton,), closures=closures)
            calendars[canton] = calendar
        return calendar

    return calendar_for


# ---------------------------------------------------------------- tasks ----


def create_task(
    ctx: CommandContext,
    *,
    title: str,
    task_type: str,
    project_number: str = "",
    project_cache: ProjectCache | None = None,
    start: WallTime | None = None,
    end: WallTime | None = None,
    resource_ids: Sequence[str] = (),
    estimate_minutes: int = 0,
    estimate_days: float = 0.0,
    required_people: int = 1,
    status: TaskStatus = "confirmed",
    deadline: date | None = None,
    urgent: bool = False,
    notes: str = "",
    origin: TaskOrigin = "grid",
    off_hours: bool = False,
) -> Changes:
    """Create a task, always the first change returned.

    Without ``start`` it joins the end of « À planifier » and ``resource_ids`` are the
    people foreseen; with ``start`` and no ``end`` it lasts its estimate on the calendar
    of the first person: ``estimate_days`` working days when set, else
    ``estimate_minutes`` of work (one worked day when 0). ``estimate_days`` is rounded
    to the half day as ``format_days`` shows it (halves up, ½ j at least). A night task
    (``off_hours``) keeps its exact times and lasts real time (8 h without estimate).
    """
    people = _checked_resource_ids(ctx, resource_ids)
    _check_task_type(ctx, task_type)
    days = _estimate_days(estimate_days)
    data: dict[str, object] = {
        "title": title.strip(),
        "task_type": task_type,
        "project_number": project_number.strip(),
        "project_cache": project_cache or ProjectCache(),
        "estimate_minutes": estimate_minutes,
        "estimate_days": days,
        "required_people": required_people,
        "status": status,
        "deadline": deadline,
        "urgent": urgent,
        "notes": notes,
        "origin": origin,
        "assignments": {resource_id: {} for resource_id in people},
        "off_hours": off_hours,
    }
    extra: list[DocumentChange] = []
    if start is None:
        if end is not None:
            raise PlanningCommandError("Une tache planifiee a besoin d'un debut.")
        cards = ctx.snapshot.backlog()
        data["backlog_rank"], extra = _backlog_rank(ctx, cards, len(cards))
    elif off_hours:
        data["start"], data["end"] = _night_span(start, end, estimate_minutes)
        data["estimate_days"] = 0.0
    else:
        calendar = _calendar(ctx, people[0] if people else None)
        data["start"], data["end"] = _new_span(calendar, start, end, estimate_minutes, days)
    task = _validated(PlanningTask, data)
    return (DocumentChange.creation(task.stamped(ctx.author, now=ctx.now)), *extra)


def schedule_task(
    ctx: CommandContext,
    task_id: str,
    *,
    start: WallTime,
    resource_ids: Sequence[str],
    end: WallTime | None = None,
) -> Changes:
    """Put a card of « À planifier » on the grid (or plan a scheduled task again).

    ``resource_ids`` replace the people foreseen, and ``(UNASSIGNED,)`` puts the task on
    « Non affecté »; when empty, the people foreseen who are still active become the
    assignees. Without ``end`` a card lasts its estimate on the calendar of the first
    assignee: its working days when ``estimate_days`` is set, else its worked minutes
    (one worked day when 0); a task already on the grid keeps its worked minutes.
    """
    task = _task(ctx, task_id)
    people = _checked_resource_ids(ctx, resource_ids) if resource_ids else _active_ids(ctx, task)
    assignments = {rid: dict(task.assignments.get(rid, {})) for rid in people}
    if task.off_hours:
        elapsed = task.end - task.start if task.start is not None and task.end is not None else 0
        begin, finish = _night_span(start, end, elapsed or task.estimate_minutes)
        return _save(ctx, task, start=begin, end=finish, assignments=assignments)
    calendar = _calendar(ctx, next(iter(assignments), None))
    if task.start is not None and task.end is not None:
        minutes = _worked(_task_calendar(ctx, task), task.start, task.end)
        days = 0.0
    else:
        minutes, days = task.estimate_minutes, task.estimate_days
    begin, finish = _new_span(calendar, start, end, minutes, days)
    return _save(ctx, task, start=begin, end=finish, assignments=assignments)


def move_task(
    ctx: CommandContext,
    task_id: str,
    *,
    new_start: WallTime,
    from_resource: str | None = None,
    to_resource: str | None = None,
) -> Changes:
    """Drag a bar: new start, same worked minutes.

    The minutes are measured on the bar's calendar (its first assignee's) and laid out on
    the calendar of the first assignee after the move, whichever row was grabbed. A
    vertical move (``to_resource`` differs from ``from_resource``) replaces only
    ``from_resource`` by ``to_resource``; ``UNASSIGNED`` as target removes
    ``from_resource``, ``UNASSIGNED`` or None as source is the « Non affecté » row.
    Dropping on someone already assigned changes nothing.
    """
    task = _task(ctx, task_id)
    start, end = _span_of(task)
    source = None if from_resource in (None, UNASSIGNED) else from_resource
    target = source if to_resource is None else (None if to_resource == UNASSIGNED else to_resource)
    assignments = {rid: dict(value) for rid, value in task.assignments.items()}
    minutes = _worked(_task_calendar(ctx, task), start, end)
    if target != source:
        if source is None and assignments:
            raise PlanningCommandError("Cette tache n'est pas sur la ligne Non affecte.")
        if source is not None and source not in assignments:
            raise PlanningCommandError("Cette personne n'est pas affectee a cette tache.")
        if target is not None:
            if target in assignments:
                return ()
            _assignable(ctx, target)
        assignments = _replaced(assignments, source, target)
    if task.off_hours:
        begin = snap(new_start)
        finish = begin + (end - start)
        _check_night(begin, finish)
    else:
        begin, finish = _span(_calendar(ctx, next(iter(assignments), None)), new_start, minutes)
    return _save(ctx, task, start=begin, end=finish, assignments=assignments)


def resize_task(
    ctx: CommandContext,
    task_id: str,
    *,
    start: WallTime | None = None,
    end: WallTime | None = None,
) -> Changes:
    """Drag an edge of a bar; the task keeps at least 30 worked minutes.

    A single edge dragged onto or past the other one stops 30 worked minutes from it;
    both edges given must make a valid span.
    """
    task = _task(ctx, task_id)
    old_start, old_end = _span_of(task)
    if task.off_hours:
        return _resize_night(ctx, task, start=start, end=end)
    calendar = _task_calendar(ctx, task)
    begin = old_start if start is None else calendar.working_start(snap(start))
    finish = old_end if end is None else calendar.working_end(snap(end))
    too_short = calendar.work_between(begin, finish) < MIN_TASK_MINUTES
    if too_short and start is None and end is not None:
        finish = calendar.add_work(begin, MIN_TASK_MINUTES)
    elif too_short and start is not None and end is None:
        begin = calendar.subtract_work(finish, MIN_TASK_MINUTES)
    else:
        _check_span(calendar, begin, finish)
    return _save(ctx, task, start=begin, end=finish)


def assign(ctx: CommandContext, task_id: str, resource_id: str) -> Changes:
    """Add a person (or a subcontractor) to a task, or to the people foreseen of a card."""
    task = _task(ctx, task_id)
    if resource_id in task.assignments:
        return ()
    _assignable(ctx, resource_id)
    return _save(ctx, task, assignments={**task.assignments, resource_id: {}})


def unassign(ctx: CommandContext, task_id: str, resource_id: str) -> Changes:
    """Remove a person; without anybody left a scheduled task goes back to « À planifier »."""
    task = _task(ctx, task_id)
    if resource_id not in task.assignments:
        return ()
    remaining = {rid: dict(value) for rid, value in task.assignments.items() if rid != resource_id}
    if remaining or task.start is None or task.end is None:
        return _save(ctx, task, assignments=remaining)
    return _to_backlog(ctx, task, assignments=remaining, calendar=_calendar(ctx, resource_id))


def unschedule_task(ctx: CommandContext, task_id: str) -> Changes:
    """Back to « À planifier »: the assignees stay as people foreseen."""
    task = _task(ctx, task_id)
    if task.start is None or task.end is None:
        return ()
    return _to_backlog(
        ctx, task, assignments=dict(task.assignments), calendar=_task_calendar(ctx, task)
    )


def duplicate_task(
    ctx: CommandContext,
    task_id: str,
    *,
    new_start: WallTime | None = None,
    resource_ids: Sequence[str] | None = None,
) -> Changes:
    """Independent copy with a new id, always the first change returned.

    By default a scheduled copy starts right after the original on the same people (those
    still active) and a card copy follows the original in « À planifier ». The copy keeps
    the estimate (minutes and days); a scheduled copy keeps the worked minutes of the
    original, a card copy placed with ``new_start`` lasts its estimate. A finished task
    is copied as confirmed, and the copy is not linked to the Planner task of the original.
    """
    task = _task(ctx, task_id)
    if resource_ids is None:
        people = _active_ids(ctx, task)
    else:
        people = _checked_resource_ids(ctx, resource_ids)
    data = _copy_data(task)
    data["assignments"] = {rid: dict(task.assignments.get(rid, {})) for rid in people}
    if task.status == "done":
        data["status"] = "confirmed"
    extra: list[DocumentChange] = []
    calendar = _calendar(ctx, people[0] if people else None)
    if task.off_hours and task.start is not None and task.end is not None:
        if new_start is None:
            evening = night_day(task.start)
            begin = task.start + (shift_nights(evening, 1) - evening).days * MINUTES_PER_DAY
        else:
            begin = night_start_at(new_start)
        data["start"], data["end"] = begin, begin + (task.end - task.start)
        _check_night(begin, begin + (task.end - task.start))
    elif task.off_hours and new_start is not None:
        data["start"], data["end"] = _night_span(new_start, None, task.estimate_minutes)
    elif task.start is not None and task.end is not None:
        minutes = _worked(_task_calendar(ctx, task), task.start, task.end)
        begin = task.end if new_start is None else new_start
        data["start"], data["end"] = _span(calendar, begin, minutes)
    elif new_start is not None:
        data["start"], data["end"] = _new_span(
            calendar, new_start, None, task.estimate_minutes, task.estimate_days
        )
    else:
        cards = ctx.snapshot.backlog()
        position = next(index for index, card in enumerate(cards) if card.id == task.id) + 1
        data["backlog_rank"], extra = _backlog_rank(ctx, cards, position)
    copy = _validated(PlanningTask, data)
    return (DocumentChange.creation(copy.stamped(ctx.author, now=ctx.now)), *extra)


def detach_assignment(ctx: CommandContext, task_id: str, resource_id: str) -> Changes:
    """Make the part of one person an independent task, the second change returned.

    The new task keeps every field of the original, for that person alone; the original
    loses that person and needs one person less.
    """
    task = _task(ctx, task_id)
    if resource_id not in task.assignments:
        raise PlanningCommandError("Cette personne n'est pas affectee a cette tache.")
    if len(task.assignments) == 1:
        raise PlanningCommandError("Seule personne de la tache: rien a detacher.")
    remaining = {rid: dict(value) for rid, value in task.assignments.items() if rid != resource_id}
    update: dict[str, object] = {
        "assignments": remaining,
        "required_people": max(1, task.required_people - 1),
    }
    data = _copy_data(task)
    data["assignments"] = {resource_id: dict(task.assignments[resource_id])}
    data["required_people"] = 1
    extra: list[DocumentChange] = []
    if not task.scheduled:
        cards = ctx.snapshot.backlog()
        position = next(index for index, card in enumerate(cards) if card.id == task.id) + 1
        data["backlog_rank"], extra = _backlog_rank(ctx, cards, position)
        # A renumbered list includes the original: its new rank joins its own change.
        for change in extra:
            if change.doc_id == task.id and isinstance(change.after, PlanningTask):
                update["backlog_rank"] = change.after.backlog_rank
        extra = [change for change in extra if change.doc_id != task.id]
    original = _save(ctx, task, **update)
    part = _validated(PlanningTask, data)
    return (*original, DocumentChange.creation(part.stamped(ctx.author, now=ctx.now)), *extra)


def set_status(ctx: CommandContext, task_id: str, status: TaskStatus) -> Changes:
    if status not in STATUS_LABELS:
        raise PlanningCommandError(f"Statut inconnu: {status}")
    return _save(ctx, _task(ctx, task_id), status=status)


def update_task(ctx: CommandContext, task_id: str, **fields: object) -> Changes:
    """Change the details of a task (not its dates nor its people).

    A new ``project_number`` clears the copied dossier details and their fingerprint,
    unless ``project_cache`` and ``project_fingerprint`` come with it. ``estimate_days``
    is rounded like in ``create_task``.
    """
    task = _task(ctx, task_id)
    _check_fields(fields, _TASK_FIELDS)
    update = _stripped(fields, ("title", "project_number"))
    days = update.get("estimate_days")
    if isinstance(days, int | float):
        update["estimate_days"] = _estimate_days(days)
    if "task_type" in update:
        _check_task_type(ctx, str(update["task_type"]))
    if "project_number" in update and update["project_number"] != task.project_number:
        update = {"project_cache": ProjectCache(), "project_fingerprint": ""} | update
    return _save(ctx, task, **update)


def delete_task(ctx: CommandContext, task_id: str) -> Changes:
    """Soft deletion: the task keeps its content and can be restored."""
    task = _task(ctx, task_id, deleted_ok=True)
    if task.deleted:
        return ()
    return _save(ctx, task, deleted_at=ctx.now, deleted_by=ctx.author)


def restore_task(ctx: CommandContext, task_id: str) -> Changes:
    task = _task(ctx, task_id, deleted_ok=True)
    if not task.deleted:
        return ()
    return _save(ctx, task, deleted_at=None, deleted_by=None)


def move_backlog(
    ctx: CommandContext,
    task_id: str,
    *,
    above_id: str | None = None,
    below_id: str | None = None,
) -> Changes:
    """Reorder « À planifier »: the card drops right under the card ``above_id``.

    Without ``above_id`` it drops right over ``below_id``, without either at the end.
    Only that card is rewritten, unless ranks written by another tool are unusable: the
    whole list then gets new ranks.
    """
    task = _task(ctx, task_id)
    if task.scheduled:
        raise PlanningCommandError("Seules les taches a planifier se classent dans la liste.")
    ordered = ctx.snapshot.backlog()
    current = next(index for index, card in enumerate(ordered) if card.id == task_id)
    cards = [card for card in ordered if card.id != task_id]
    positions = {card.id: index for index, card in enumerate(cards)}
    if task_id in (above_id, below_id):
        return ()
    if above_id is not None:
        position = _position(positions, above_id) + 1
    elif below_id is not None:
        position = _position(positions, below_id)
    else:
        position = len(cards)
    if position == current:
        return ()
    rank, extra = _backlog_rank(ctx, cards, position)
    return (*_save(ctx, task, backlog_rank=rank), *extra)


def shift_tasks(
    ctx: CommandContext,
    task_ids: Iterable[str],
    *,
    minutes: int = 0,
    days: int = 0,
) -> Changes:
    """Move bars with the keyboard by worked ``days`` then worked ``minutes`` (signed).

    Each task keeps its worked minutes on its own calendar. A day shift keeps the time of
    day and always lands on the target working day: an afternoon start shifted onto a
    Friday starts Friday morning. A start in a break moves to the next worked instant.
    """
    changes: list[DocumentChange] = []
    for task_id in dict.fromkeys(task_ids):
        task = _task(ctx, task_id)
        start, end = _span_of(task)
        if task.off_hours:
            changes.extend(_shift_night(ctx, task, minutes=minutes, days=days))
            continue
        calendar = _task_calendar(ctx, task)
        worked = _worked(calendar, start, end)
        moved = _shift_workdays(calendar, start, days)
        if minutes:
            moved = calendar.add_work(calendar.working_start(moved), minutes)
        begin, finish = _span(calendar, moved, worked)
        changes.extend(_save(ctx, task, start=begin, end=finish))
    return tuple(changes)


def set_night(ctx: CommandContext, task_id: str, night: bool) -> Changes:  # noqa: FBT001
    """Make a task night work (« Travail de nuit ») or put it back on worked time.

    A scheduled task becoming night work takes the default night of its first day (21:00
    to 05:00), refused on a Friday, Saturday or Sunday evening. Back on worked time it
    keeps its length as worked minutes from the first worked instant after its start.
    """
    task = _task(ctx, task_id)
    if task.off_hours == night:
        return ()
    if task.start is None or task.end is None:
        days = 0.0 if night else task.estimate_days
        return _save(ctx, task, off_hours=night, estimate_days=days)
    if night:
        begin, finish = default_night(wall_date(task.start))
        _check_night(begin, finish)
        return _save(ctx, task, off_hours=True, start=begin, end=finish, estimate_days=0.0)
    begin, finish = _span(_task_calendar(ctx, task), task.start, task.end - task.start)
    return _save(ctx, task, off_hours=False, start=begin, end=finish)


def task_days(calendar: WorkCalendar, task: PlanningTask) -> float:
    """Working days of a scheduled task as its bar, its drag label and the Détail say them.

    The whole or half days its dates cover (``WorkCalendar.whole_days``), so every view
    of a task tells the same length; 0 when they cover none (the bar reads in hours) or
    the task has no dates. ``calendar`` is the one of its first assignee (``calendar_for``).
    """
    if task.start is None or task.end is None:
        return 0.0
    return calendar.whole_days(task.start, task.end) or 0.0


def card_days(calendar: WorkCalendar, task: PlanningTask) -> float:
    """Working days a scheduled task keeps as a card when it goes back to « À planifier ».

    Its own ``estimate_days`` while the bar is still what they placed: a card goes back
    and forth without growing where a Friday made ``whole_days`` read more (« ½ j » on a
    Friday morning reads 1 j) or where its dates cover no whole half day (« 2 j » dropped
    on a Thursday at 11:00). Else ``task_days``.
    """
    if task.start is None or task.end is None:
        return 0.0
    days = task.estimate_days
    if 0 < days <= MAX_ESTIMATE_DAYS:
        kept = _estimate_days(days)
        if _end_of_days(calendar, task.start, kept) == task.end:
            return kept
    return task_days(calendar, task)


# ------------------------------------------------------------ resources ----


def create_resource(
    ctx: CommandContext,
    *,
    name: str,
    initials: str = "",
    kind_of_resource: ResourceKind = "person",
    team: str | None = None,
    role: str = "",
    apprentice: bool = False,
    canton: str = "",
    color: str = "",
    m365_user_id: str = "",
    recurring_off: Sequence[RecurringOff] = (),
) -> Changes:
    """Add a row at the end of its team; initials default to those of the name."""
    clean_name = _required_name(name)
    team_key = _team_for(ctx, team, kind_of_resource=kind_of_resource)
    resource = _validated(
        Resource,
        {
            "name": clean_name,
            "initials": initials.strip() or _initials(clean_name),
            "kind_of_resource": kind_of_resource,
            "team": team_key,
            "role": role.strip(),
            "apprentice": apprentice,
            "canton": _canton(canton),
            "color": color,
            "m365_user_id": m365_user_id,
            "recurring_off": tuple(recurring_off),
            "order": _next_order(ctx, team_key, exclude=None),
        },
    )
    return (DocumentChange.creation(resource.stamped(ctx.author, now=ctx.now)),)


def update_resource(ctx: CommandContext, resource_id: str, **fields: object) -> Changes:
    """Change a row; a new team puts it at the end of that team."""
    resource = _resource(ctx, resource_id)
    _check_fields(fields, _RESOURCE_FIELDS)
    update = _stripped(fields, ("name", "initials", "role"))
    if "name" in update:
        update["name"] = _required_name(str(update["name"]))
    if "canton" in update:
        update["canton"] = _canton(str(update["canton"]))
    if "team" in update and update["team"] != resource.team:
        team_key = str(update["team"])
        if ctx.snapshot.meta.team(team_key) is None:
            raise PlanningCommandError(f"Equipe inconnue: {team_key}")
        update["order"] = _next_order(ctx, team_key, exclude=resource_id)
    return _save(ctx, resource, **update)


def archive_resource(ctx: CommandContext, resource_id: str) -> Changes:
    """Hide a row; its tasks and absences are kept (rows are never deleted)."""
    return _save(ctx, _resource(ctx, resource_id), active=False)


def reorder_resource(
    ctx: CommandContext,
    resource_id: str,
    *,
    above_id: str | None = None,
    below_id: str | None = None,
) -> Changes:
    """Move a row inside its team: right under ``above_id``, else right over ``below_id``.

    A neighbour from a team shown above stands for the top of the row's team, one from a
    team shown below for its bottom (a row dropped at the top of its team has the last
    row of the previous team above it); a drop between two rows of another team is
    refused. Only the moved row is rewritten while there is room between the ``order``
    of its neighbours, otherwise the team is renumbered.
    """
    resource = _resource(ctx, resource_id)
    shown = ctx.snapshot.active_resources()
    indexes = {row.id: index for index, row in enumerate(shown)}
    for neighbour in (above_id, below_id):
        if neighbour is not None and neighbour not in indexes:
            raise PlanningCommandError("Ligne voisine introuvable.")
    if resource_id in (above_id, below_id):
        return ()
    team_rows = [row for row in shown if row.team == resource.team]
    rows = [row for row in team_rows if row.id != resource_id]
    positions = {row.id: index for index, row in enumerate(rows)}
    if above_id in positions:
        position = positions[above_id] + 1
    elif below_id in positions:
        position = positions[below_id]
    else:
        team_indexes = [indexes[row.id] for row in team_rows] or [len(shown)]
        above_ok = above_id is None or indexes[above_id] < min(team_indexes)
        below_ok = below_id is None or indexes[below_id] > max(team_indexes)
        if not (above_ok and below_ok):
            raise PlanningCommandError("Une ligne reste dans son equipe.")
        position = 0 if above_id is not None else len(rows)
    current = next((index for index, row in enumerate(team_rows) if row.id == resource_id), None)
    if position == current:
        return ()
    order = _order_between(
        rows[position - 1].order if position > 0 else None,
        rows[position].order if position < len(rows) else None,
        current=resource.order,
    )
    if order is not None:
        return _save(ctx, resource, order=order)
    renumbered = [*rows[:position], resource, *rows[position:]]
    changes: list[DocumentChange] = []
    for index, row in enumerate(renumbered):
        changes.extend(_save(ctx, row, order=(index + 1) * ORDER_STEP))
    return tuple(changes)


# ------------------------------------------------------------- absences ----


def create_absence(
    ctx: CommandContext,
    *,
    resource_id: str,
    first_day: date,
    last_day: date | None = None,
    reason: AbsenceReason = "absent",
    part: DayPart = "day",
    note: str = "",
) -> Changes:
    """Record an absence from ``first_day`` to ``last_day`` included (one day by default)."""
    _resource(ctx, resource_id)
    final_day = first_day if last_day is None else last_day
    if final_day < first_day:
        raise PlanningCommandError("La fin de l'absence precede son debut.")
    absence = _validated(
        Absence,
        {
            "resource_id": resource_id,
            "reason": reason,
            "first_day": first_day,
            "last_day": final_day,
            "part": part,
            "note": note.strip(),
        },
    )
    return (DocumentChange.creation(absence.stamped(ctx.author, now=ctx.now)),)


def update_absence(ctx: CommandContext, absence_id: str, **fields: object) -> Changes:
    absence = _absence(ctx, absence_id)
    _check_fields(fields, _ABSENCE_FIELDS)
    update = _stripped(fields, ("note",))
    if "resource_id" in update:
        _resource(ctx, str(update["resource_id"]))
    first_day = update.get("first_day", absence.first_day)
    last_day = update.get("last_day", absence.last_day)
    if isinstance(first_day, date) and isinstance(last_day, date) and last_day < first_day:
        raise PlanningCommandError("La fin de l'absence precede son debut.")
    return _save(ctx, absence, **update)


def delete_absence(ctx: CommandContext, absence_id: str) -> Changes:
    """Soft deletion, like tasks."""
    absence = ctx.snapshot.absences.get(absence_id)
    if absence is None:
        raise PlanningCommandError("Absence introuvable.")
    if absence.deleted:
        return ()
    return _save(ctx, absence, deleted_at=ctx.now, deleted_by=ctx.author)


# ------------------------------------------------------------- settings ----


def update_meta(ctx: CommandContext, **fields: object) -> Changes:
    """Change the shared settings; the first change of a never saved meta is a creation.

    A closure must not end before it starts, and a team still used by a shown row
    cannot be removed.
    """
    meta = ctx.snapshot.meta
    _check_fields(fields, _META_FIELDS)
    update = dict(fields)
    if "default_canton" in update:
        canton = _canton(str(update["default_canton"]))
        if not canton:
            raise PlanningCommandError("Canton par defaut requis.")
        update["default_canton"] = canton
    changes = _save(ctx, meta, **update)
    if not changes:
        return ()
    after = changes[0].after
    if isinstance(after, PlanningMeta):
        try:
            WorkCalendar(week=after.work_week)
        except ValueError as error:
            raise PlanningCommandError(str(error)) from error
        if "closures" in update:
            _check_closures(after)
        if "teams" in update:
            _check_removed_teams(ctx, before=meta, after=after)
    if not meta.created_at and after is not None:
        return (DocumentChange.creation(after),)
    return changes


def retyped_tasks(
    snapshot: PlanningSnapshot,
    task_types: Sequence[TaskType],
    mapping: Mapping[str, str],
) -> dict[str, str]:
    """Return the new type of each live task ``replace_task_types`` would change, by id.

    A task takes ``mapping[its type]``; a type missing from ``task_types`` (removed, or
    mapped to nothing known) becomes the default type of the new list.
    """
    keys = {kind.key for kind in task_types}
    fallback = default_task_type_key(task_types)
    retyped: dict[str, str] = {}
    for task in snapshot.live_tasks():
        target = mapping.get(task.task_type, task.task_type)
        if target not in keys:
            target = fallback
        if target != task.task_type:
            retyped[task.id] = target
    return retyped


def replace_task_types(
    ctx: CommandContext,
    task_types: Sequence[TaskType],
    mapping: Mapping[str, str] | None = None,
) -> Changes:
    """Set the shared list of task types, moving the tasks of removed types (one step).

    ``mapping`` gives the new key of an old one (see ``retyped_tasks``). The list keeps
    one type at least, with distinct keys and a label each.
    """
    kinds = tuple(task_types)
    if not kinds:
        raise PlanningCommandError("Il faut garder au moins un type de tache.")
    keys = [kind.key for kind in kinds]
    if len(set(keys)) != len(keys) or not all(key.strip() for key in keys):
        raise PlanningCommandError("Chaque type de tache a sa propre cle.")
    if not all(kind.label.strip() for kind in kinds):
        raise PlanningCommandError("Chaque type de tache a un nom.")
    meta_changes = update_meta(ctx, task_types=kinds)
    after = ctx.snapshot.apply(meta_changes) if meta_changes else ctx.snapshot
    moved = replace(ctx, snapshot=after)
    task_changes: list[DocumentChange] = []
    for task_id, key in retyped_tasks(ctx.snapshot, kinds, mapping or {}).items():
        task_changes.extend(_save(moved, _task(moved, task_id), task_type=key))
    return (*meta_changes, *task_changes)


def _check_closures(meta: PlanningMeta) -> None:
    for closure in meta.closures:
        if closure.last_day < closure.first_day:
            raise PlanningCommandError("La fin de la fermeture precede son debut.")


def _check_removed_teams(ctx: CommandContext, *, before: PlanningMeta, after: PlanningMeta) -> None:
    removed = {team.key for team in before.teams} - {team.key for team in after.teams}
    for row in ctx.snapshot.active_resources():
        if row.team in removed:
            raise PlanningCommandError(f"Equipe encore utilisee par {row.name}: {row.team}")


# -------------------------------------------------------------- helpers ----


def _validated(model: type[_Document], data: Mapping[str, object]) -> _Document:
    try:
        return model.model_validate(dict(data))
    except ValidationError as error:
        raise PlanningCommandError(_validation_message(error)) from error


def _validation_message(error: ValidationError) -> str:
    details = error.errors()
    if not details:
        return "Valeurs invalides."
    first = details[0]
    cause = first.get("ctx", {}).get("error")
    if first["type"] == "value_error" and cause is not None:
        return str(cause)
    field = ".".join(str(part) for part in first["loc"])
    return f"Valeur invalide pour le champ {field}."


def _save(ctx: CommandContext, document: _Document, **fields: object) -> Changes:
    """Change of ``document`` with ``fields`` replaced, or nothing when it is the same."""
    revised = _validated(type(document), {**document.model_dump(), **fields})
    if revised.to_json() == document.to_json():
        return ()
    return (DocumentChange.update(document, revised.stamped(ctx.author, now=ctx.now)),)


def _check_fields(fields: Mapping[str, object], allowed: frozenset[str]) -> None:
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise PlanningCommandError(f"Champ non modifiable: {', '.join(unknown)}")


def _stripped(fields: Mapping[str, object], names: Iterable[str]) -> dict[str, object]:
    update = dict(fields)
    for name in names:
        value = update.get(name)
        if isinstance(value, str):
            update[name] = value.strip()
    return update


def _task(ctx: CommandContext, task_id: str, *, deleted_ok: bool = False) -> PlanningTask:
    task = ctx.snapshot.tasks.get(task_id)
    if task is None:
        raise PlanningCommandError("Tache introuvable.")
    if task.deleted and not deleted_ok:
        raise PlanningCommandError("Cette tache a ete supprimee.")
    return task


def _span_of(task: PlanningTask) -> tuple[WallTime, WallTime]:
    if task.start is None or task.end is None:
        raise PlanningCommandError("Cette tache n'est pas planifiee.")
    return task.start, task.end


def _resource(ctx: CommandContext, resource_id: str) -> Resource:
    resource = ctx.snapshot.resources.get(resource_id)
    if resource is None or resource.deleted:
        raise PlanningCommandError("Personne ou sous-traitant introuvable.")
    return resource


def _absence(ctx: CommandContext, absence_id: str) -> Absence:
    absence = ctx.snapshot.absences.get(absence_id)
    if absence is None or absence.deleted:
        raise PlanningCommandError("Absence introuvable.")
    return absence


def _assignable(ctx: CommandContext, resource_id: str) -> Resource:
    resource = _resource(ctx, resource_id)
    if not resource.active:
        raise PlanningCommandError(f"La ligne {resource.name} est archivee.")
    return resource


def _checked_resource_ids(ctx: CommandContext, resource_ids: Iterable[str]) -> tuple[str, ...]:
    people = tuple(rid for rid in dict.fromkeys(resource_ids) if rid != UNASSIGNED)
    for resource_id in people:
        _assignable(ctx, resource_id)
    return people


def _active_ids(ctx: CommandContext, task: PlanningTask) -> tuple[str, ...]:
    """Assignees of ``task`` whose row is still shown (not archived nor deleted)."""
    rows = ctx.snapshot.resources
    return tuple(
        rid
        for rid in task.assignments
        if (row := rows.get(rid)) is not None and row.active and not row.deleted
    )


def _check_task_type(ctx: CommandContext, key: str) -> None:
    known = {kind.key for kind in ctx.snapshot.meta.task_types}
    if key not in known:
        raise PlanningCommandError(f"Type de tache inconnu: {key}")


def _calendar(ctx: CommandContext, resource_id: str | None) -> WorkCalendar:
    return ctx.calendar_for(None if resource_id in (None, UNASSIGNED) else resource_id)


def _task_calendar(ctx: CommandContext, task: PlanningTask) -> WorkCalendar:
    """Calendar of the first assignee, the one the bar's length is measured with."""
    return _calendar(ctx, next(iter(task.assignments), None))


def _duration(minutes: int) -> int:
    """Worked minutes rounded up to the half hour, at least 30; 0 means one worked day."""
    if minutes <= 0:
        return DEFAULT_TASK_MINUTES
    return max(MIN_TASK_MINUTES, -(-minutes // SNAP_MINUTES) * SNAP_MINUTES)


def _worked(calendar: WorkCalendar, start: WallTime, end: WallTime) -> int:
    """Worked minutes of a bar; one lying on days closed since then keeps its weekday hours."""
    minutes = calendar.work_between(start, end)
    if minutes <= 0:
        minutes = WorkCalendar(week=calendar.week, cantons=()).work_between(start, end)
    return max(MIN_TASK_MINUTES, minutes)


def _span(calendar: WorkCalendar, start: WallTime, minutes: int) -> tuple[WallTime, WallTime]:
    begin = calendar.working_start(snap(start))
    return begin, calendar.add_work(begin, _duration(minutes))


def _new_span(
    calendar: WorkCalendar,
    start: WallTime,
    end: WallTime | None,
    estimate_minutes: int,
    estimate_days: float = 0.0,
) -> tuple[WallTime, WallTime]:
    """Span of a task placed at ``start``: ``end`` when given, else its estimate."""
    days = _estimate_days(estimate_days)
    if end is None and days:
        begin = calendar.working_start(snap(start))
        return begin, _end_of_days(calendar, begin, days)
    if end is None:
        return _span(calendar, start, estimate_minutes)
    begin = calendar.working_start(snap(start))
    finish = calendar.working_end(snap(end))
    _check_span(calendar, begin, finish)
    return begin, finish


def _estimate_days(days: float) -> float:
    """Days of an estimate as shown and placed: to the half day, halves up, ½ j at least.

    ``format_days`` rounds halves up and ``WorkCalendar.add_days`` to even: an estimate
    stored rounded reads and places alike.
    """
    if not 0 <= days <= MAX_ESTIMATE_DAYS:  # also refuses NaN
        raise PlanningCommandError(INVALID_DAYS_MESSAGE)
    return max(round_days(days), MIN_ESTIMATE_DAYS) if days else 0.0


def _end_of_days(calendar: WorkCalendar, begin: WallTime, days: float) -> WallTime:
    return max(calendar.add_days(begin, days), calendar.add_work(begin, MIN_TASK_MINUTES))


def _check_span(calendar: WorkCalendar, start: WallTime, end: WallTime) -> None:
    if end <= start:
        raise PlanningCommandError("La fin doit suivre le debut.")
    if calendar.work_between(start, end) < MIN_TASK_MINUTES:
        raise PlanningCommandError("Une tache dure au moins 30 minutes de travail.")


def _check_night(start: WallTime, end: WallTime) -> None:
    error = night_error(start, end)
    if error is not None:
        raise PlanningCommandError(error)


def _night_span(start: WallTime, end: WallTime | None, minutes: int) -> tuple[WallTime, WallTime]:
    """Night placed at ``start``: until ``end`` when given, else ``minutes`` cut at 06:30.

    A daytime ``start`` (a card dropped on a day) takes 21:00 of its day.
    """
    begin = night_start_at(start)
    if end is None:
        begin, finish = fitted_night(begin, minutes)
    else:
        finish = snap(end)
    _check_night(begin, finish)
    return begin, finish


def _resize_night(
    ctx: CommandContext, task: PlanningTask, *, start: WallTime | None, end: WallTime | None
) -> Changes:
    """Resize a night: edges snapped to the half hour only, 30 minutes at least."""
    old_start, old_end = _span_of(task)
    begin = old_start if start is None else snap(start)
    finish = old_end if end is None else snap(end)
    if finish - begin < MIN_NIGHT_MINUTES and start is None:
        finish = begin + MIN_NIGHT_MINUTES
    elif finish - begin < MIN_NIGHT_MINUTES and end is None:
        begin = finish - MIN_NIGHT_MINUTES
    _check_night(begin, finish)
    return _save(ctx, task, start=begin, end=finish)


def _shift_night(ctx: CommandContext, task: PlanningTask, *, minutes: int, days: int) -> Changes:
    """Shift a night by ``days`` nights (Friday to Sunday skipped), then real ``minutes``."""
    start, end = _span_of(task)
    moved = start
    if days:
        evening = night_day(start)
        moved += (shift_nights(evening, days) - evening).days * MINUTES_PER_DAY
    begin = snap(moved + minutes)
    finish = begin + (end - start)
    _check_night(begin, finish)
    return _save(ctx, task, start=begin, end=finish)


def _shift_workdays(calendar: WorkCalendar, value: WallTime, days: int) -> WallTime:
    """Keep the time of day, ``days`` working days later (earlier when negative).

    A time after the target day's last period (an afternoon moved to a Friday) becomes
    the start of that period, so the start stays on the target day.
    """
    if not days:
        return value
    day = wall_date(value)
    for _ in range(abs(days)):
        day = calendar.next_workday(day) if days > 0 else calendar.previous_workday(day)
    moved = day_start(day) + minute_of_day(value)
    last_start, last_end = calendar.day_periods(day)[-1]
    return last_start if moved >= last_end else moved


def _replaced(
    assignments: Mapping[str, dict[str, Any]],
    source: str | None,
    target: str | None,
) -> dict[str, dict[str, Any]]:
    """Replace ``source`` by ``target`` at the same place of the assignments."""
    if source is None:
        return {**assignments, target: {}} if target is not None else dict(assignments)
    replaced: dict[str, dict[str, Any]] = {}
    for resource_id, value in assignments.items():
        if resource_id != source:
            replaced[resource_id] = value
        elif target is not None:
            replaced[target] = value
    return replaced


def _copy_data(task: PlanningTask) -> dict[str, object]:
    data: dict[str, object] = task.model_dump(exclude=set(_ENVELOPE_FIELDS))
    data["planner_task_id"] = ""
    return data


def _to_backlog(
    ctx: CommandContext,
    task: PlanningTask,
    *,
    assignments: Mapping[str, dict[str, Any]],
    calendar: WorkCalendar,
) -> Changes:
    """Task without dates at the end of « À planifier », its length kept as estimate.

    The worked minutes of its dates become ``estimate_minutes`` and its working days
    ``estimate_days`` (``card_days``), both measured on ``calendar``.
    """
    start, end = _span_of(task)
    cards = ctx.snapshot.backlog()
    rank, extra = _backlog_rank(ctx, cards, len(cards))
    night = task.off_hours
    moved = _save(
        ctx,
        task,
        start=None,
        end=None,
        assignments=dict(assignments),
        estimate_minutes=end - start if night else _worked(calendar, start, end),
        estimate_days=0.0 if night else card_days(calendar, task),
        backlog_rank=rank,
    )
    return (*moved, *extra)


def _backlog_rank(
    ctx: CommandContext,
    cards: Sequence[PlanningTask],
    position: int,
) -> tuple[str, list[DocumentChange]]:
    """Rank for a card inserted at ``position`` of ``cards`` (which it is not part of).

    When the neighbours' ranks leave no room (empty or equal ranks written elsewhere),
    every card gets a new rank and their changes are returned too.
    """
    before = cards[position - 1].backlog_rank if position > 0 else None
    after = cards[position].backlog_rank if position < len(cards) else None
    try:
        return rank_between(before, after), []
    except ValueError:
        pass
    ranks = initial_ranks(len(cards) + 1)
    changes: list[DocumentChange] = []
    for index, card in enumerate(cards):
        changes.extend(_save(ctx, card, backlog_rank=ranks[index + (index >= position)]))
    return ranks[position], changes


def _position(positions: Mapping[str, int], card_id: str) -> int:
    position = positions.get(card_id)
    if position is None:
        raise PlanningCommandError("Carte voisine introuvable dans la liste a planifier.")
    return position


def _required_name(name: str) -> str:
    clean = " ".join(name.split())
    if not clean:
        raise PlanningCommandError("Le nom est obligatoire.")
    return clean


def _initials(name: str) -> str:
    return "".join(word[0] for word in name.split() if word[0].isalnum())[:INITIALS_LENGTH].upper()


def _canton(canton: str) -> str:
    clean = canton.strip().upper()
    if clean and clean not in CANTONS:
        raise PlanningCommandError(f"Canton inconnu: {canton}")
    return clean


def _team_for(ctx: CommandContext, team: str | None, *, kind_of_resource: ResourceKind) -> str:
    meta = ctx.snapshot.meta
    if team is not None:
        if meta.team(team) is None:
            raise PlanningCommandError(f"Equipe inconnue: {team}")
        return team
    preferred = SUBCONTRACTOR_TEAM if kind_of_resource == "subcontractor" else DEFAULT_TEAM
    if meta.team(preferred) is not None or not meta.teams:
        return preferred
    return min(meta.teams, key=lambda candidate: candidate.order).key


def _order_between(low: int | None, high: int | None, *, current: int) -> int | None:
    """``order`` strictly between two neighbour rows, None when the team must be renumbered."""
    if low is None:
        return current if high is None else high - ORDER_STEP
    if high is None:
        return low + ORDER_STEP
    return (low + high) // 2 if high - low > 1 else None


def _next_order(ctx: CommandContext, team: str, *, exclude: str | None) -> int:
    orders = [
        resource.order
        for resource in ctx.snapshot.resources.values()
        if resource.team == team and not resource.deleted and resource.id != exclude
    ]
    return max(orders, default=0) + ORDER_STEP
