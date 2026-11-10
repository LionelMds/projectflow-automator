"""Appointments dragged in from another calendar, turned into tasks of the planning.

They come from an ``.ics`` file (any calendar exports one: Outlook, Apple Calendar,
Google…), from calendar text dragged as is, or from the items selected in the classic
Outlook of the poste (``outlook.selection``). All give ``ImportedEvent`` in local wall
time, then ``drafts_for_drop`` says what to create:

* the dossier number is looked for in the subject (« 2026-5154 - Pose porte »), a short
  one (« 5154 ») only when the répertoire knows it; the rest of the subject is the title,
  left empty when it only repeats the designation;
* dropped in « À planifier », a task keeps the length of its appointment as estimate;
* dropped on the grid, it goes on the row and the day of the drop and keeps the hour of
  its appointment; several appointments keep their days apart from one another.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from projectflow.core.numero import canonical_project_number
from projectflow.core.planning.calendar import WorkCalendar
from projectflow.core.planning.models import MAX_ESTIMATE_DAYS
from projectflow.core.planning.quick_entry import ProjectInfo
from projectflow.core.planning.walltime import WallTime, day_start, wall_date

ESTIMATE_STEP: Final = 30
"""Estimates are rounded to the half hour, the precision of the planning."""
MAX_NOTES: Final = 2000
DEFAULT_LENGTH: Final = timedelta(hours=1)
"""Length of an appointment that gives a start and no end."""
FindProject = Callable[[str], ProjectInfo | None]

_FULL_NUMBER_RE = re.compile(
    r"(?<![\w-])(?P<y>(?:19|20)\d{2})\s*[-\u2010-\u2015\u2212]\s*(?P<n>\d{3,5})"
    r"(?:[-_](?P<s>[A-Za-z]*\d+[A-Za-z0-9]*)|\s(?P<t>[A-Za-z]+\d+[A-Za-z0-9]*))?(?!\w)"
)
"""``2026-5154``, ``2024-4493-A1``, ``2026-5102-REV03``, ``2026-5102 REV03``."""
_SHORT_NUMBER_RE = re.compile(r"(?<![\w.,:/-])(?P<n>\d{3,5})(?![\w.,:/-])(?!\s*h\b)")
_TITLE_EDGES: Final = " -:;,.|/\u2013\u2014\u00b7"
_SPACES_RE = re.compile(r"\s+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True, slots=True)
class ImportedEvent:
    """An appointment (or an Outlook task or mail) brought in, in local wall time."""

    subject: str
    start: datetime | None = None
    """Naive, local (Swiss) time; None for an item without date (a mail, a task)."""
    end: datetime | None = None
    all_day: bool = False
    location: str = ""
    notes: str = ""


@dataclass(frozen=True, slots=True)
class ImportDraft:
    """What to create for one ``ImportedEvent``."""

    title: str
    project_number: str = ""
    project: ProjectInfo | None = None
    start: WallTime | None = None
    estimate_minutes: int = 0
    estimate_days: float = 0.0
    notes: str = ""


# ----------------------------------------------------------------- the .ics ----


def is_calendar_text(text: str) -> bool:
    return "BEGIN:VCALENDAR" in text[:4096].upper()


def parse_ics(text: str) -> list[ImportedEvent]:
    """Events (``VEVENT``) and dated to-dos (``VTODO``) of an iCalendar text.

    A repeated appointment gives its first occurrence; a cancelled one is left out. Times
    in UTC or in a known zone are brought to the local time of the poste; a zone unknown
    here (« W. Europe Standard Time ») is taken as local.
    """
    events: list[ImportedEvent] = []
    current: dict[str, tuple[dict[str, str], str]] | None = None
    kind = ""
    for name, params, value in _content_lines(text):
        if name == "BEGIN" and value.upper() in {"VEVENT", "VTODO"}:
            current, kind = {}, value.upper()
        elif name == "END" and current is not None and value.upper() == kind:
            event = _event_of(current, todo=kind == "VTODO")
            if event is not None:
                events.append(event)
            current = None
        elif current is not None and name not in current:
            current[name] = (params, value)
    return events


def _content_lines(text: str) -> Iterable[tuple[str, dict[str, str], str]]:
    unfolded: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in {" ", "\t"} and unfolded:
            unfolded[-1] += raw[1:]
        elif raw.strip():
            unfolded.append(raw)
    for line in unfolded:
        head, colon, value = _split_value(line)
        if not colon:
            continue
        name, *raw_params = head.split(";")
        params: dict[str, str] = {}
        for raw_param in raw_params:
            key, _, param_value = raw_param.partition("=")
            params[key.strip().upper()] = param_value.strip().strip('"')
        yield name.strip().upper(), params, value


def _split_value(line: str) -> tuple[str, str, str]:
    """``NAME;PARAM="a:b":value`` cut at the first colon outside quotes."""
    quoted = False
    for index, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == ":" and not quoted:
            return line[:index], ":", line[index + 1 :]
    return line, "", ""


def _event_of(props: dict[str, tuple[dict[str, str], str]], *, todo: bool) -> ImportedEvent | None:
    status = props.get("STATUS", ({}, ""))[1].strip().upper()
    if status == "CANCELLED":
        return None
    subject = _text(props.get("SUMMARY", ({}, ""))[1])
    start = _when(*props["DTSTART"]) if "DTSTART" in props else None
    end_key = "DUE" if todo else "DTEND"
    end = _when(*props[end_key]) if end_key in props else None
    if todo and start is None and end is not None:
        start, end = end, None
    if start is None:
        if not subject:
            return None
        return ImportedEvent(subject=subject, notes=_notes(props))
    moment, all_day = start
    finish = end[0] if end is not None else None
    if finish is None and "DURATION" in props:
        length = _duration(props["DURATION"][1])
        finish = moment + length if length is not None else None
    if finish is None or finish <= moment:
        finish = moment + (timedelta(days=1) if all_day else DEFAULT_LENGTH)
    return ImportedEvent(
        subject=subject,
        start=moment,
        end=finish,
        all_day=all_day,
        location=_text(props.get("LOCATION", ({}, ""))[1]),
        notes=_notes(props),
    )


def _notes(props: dict[str, tuple[dict[str, str], str]]) -> str:
    return _text(props.get("DESCRIPTION", ({}, ""))[1])


def _text(value: str) -> str:
    """Unescape an iCalendar text: escaped new lines, commas, semicolons and backslashes."""
    out: list[str] = []
    chars = iter(value)
    for char in chars:
        if char != "\\":
            out.append(char)
            continue
        following = next(chars, "")
        out.append("\n" if following in {"n", "N"} else following)
    return "".join(out).strip()


def _when(params: dict[str, str], value: str) -> tuple[datetime, bool] | None:
    """Local wall time of a ``DTSTART``/``DTEND``, and whether it is a whole day."""
    raw = value.strip()
    try:
        if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", raw):
            return datetime.strptime(raw[:8], "%Y%m%d"), True  # noqa: DTZ007 - a day
        moment = datetime.strptime(raw.rstrip("Zz")[:15], "%Y%m%dT%H%M%S")  # noqa: DTZ007
    except ValueError:
        return None
    if raw.upper().endswith("Z"):
        return moment.replace(tzinfo=UTC).astimezone().replace(tzinfo=None), False
    zone = params.get("TZID", "")
    if zone:
        try:
            known = ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            return moment, False  # « W. Europe Standard Time »: Swiss time, as here
        return moment.replace(tzinfo=known).astimezone().replace(tzinfo=None), False
    return moment, False


_DURATION_RE = re.compile(
    r"^(?P<sign>[+-])?P(?:(?P<w>\d+)W)?(?:(?P<d>\d+)D)?"
    r"(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?$"
)


def _duration(value: str) -> timedelta | None:
    match = _DURATION_RE.match(value.strip().upper())
    if match is None:
        return None
    parts = {key: int(match[key] or 0) for key in ("w", "d", "h", "m", "s")}
    length = timedelta(
        weeks=parts["w"], days=parts["d"], hours=parts["h"], minutes=parts["m"], seconds=parts["s"]
    )
    return None if match["sign"] == "-" else length


# ---------------------------------------------------------------- dossier ----


def find_dossier(
    subject: str, find_project: FindProject, *, default_year: int
) -> tuple[str, ProjectInfo | None, str]:
    """Dossier of a subject: ``(number, répertoire details, rest of the subject)``.

    A full number is kept even when the répertoire does not know it (a dossier of another
    year is looked up later); a short one (« 5154 ») is kept only when the répertoire knows
    it, this year or the last.
    """
    match = _FULL_NUMBER_RE.search(subject)
    if match is not None:
        suffix = match["s"] or match["t"]
        raw = f"{match['y']}-{match['n']}" + (f"-{suffix}" if suffix else "")
        number = canonical_project_number(raw)
        return number, find_project(number), _without(subject, match.start(), match.end())
    for short in _SHORT_NUMBER_RE.finditer(subject):
        for year in (default_year, default_year - 1):
            number = f"{year}-{short['n']}"
            project = find_project(number)
            if project is not None:
                return number, project, _without(subject, short.start(), short.end())
    return "", None, _clean(subject)


def _without(text: str, start: int, end: int) -> str:
    return _clean(f"{text[:start]} {text[end:]}")


def _clean(text: str) -> str:
    return _SPACES_RE.sub(" ", text).strip(_TITLE_EDGES)


# ------------------------------------------------------------------ drafts ----


def wall_of(moment: datetime) -> WallTime:
    return day_start(moment.date()) + moment.hour * 60 + moment.minute


def event_estimate(event: ImportedEvent, calendar: WorkCalendar) -> tuple[int, float]:
    """``(minutes, days)`` of work an appointment stands for; ``(0, 0)`` without date.

    A whole-day appointment counts its working days (all its days when none is worked);
    a timed one its worked minutes on ``calendar``, or its own length when it lies
    outside the working hours, rounded to the half hour.
    """
    if event.start is None:
        return 0, 0.0
    if event.all_day:
        first = event.start.date()
        after = event.end.date() if event.end is not None else first + timedelta(days=1)
        days = [first + timedelta(days=n) for n in range(max((after - first).days, 1))]
        worked = sum(1 for day in days if calendar.is_workday(day)) or len(days)
        return 0, float(min(worked, MAX_ESTIMATE_DAYS))
    start = wall_of(event.start)
    end = wall_of(event.end) if event.end is not None else start + 60
    minutes = calendar.work_between(start, end) or max(end - start, 0)
    steps = max(round(minutes / ESTIMATE_STEP), 1)
    return steps * ESTIMATE_STEP, 0.0


def drafts_for_drop(
    events: Sequence[ImportedEvent],
    *,
    find_project: FindProject,
    default_year: int,
    calendar: WorkCalendar,
    drop_start: WallTime | None = None,
) -> list[ImportDraft]:
    """Tasks to create for ``events`` dropped at ``drop_start`` (None: « À planifier »).

    On the grid, the earliest dated appointment goes on the day of the drop at its own
    hour (the first worked instant from there), the others keep their days apart from it;
    an item without date takes the drop instant itself.
    """
    dated = [event.start.date() for event in events if event.start is not None]
    shift = wall_date(drop_start) - min(dated) if drop_start is not None and dated else timedelta(0)
    drafts: list[ImportDraft] = []
    for event in events:
        number, project, rest = find_dossier(event.subject, find_project, default_year=default_year)
        designation = project.designation if project is not None else ""
        title = "" if number and rest.casefold() == designation.strip().casefold() else rest
        if not title and not number:
            title = "Rendez-vous importé"
        minutes, days = event_estimate(event, calendar)
        start: WallTime | None = None
        if drop_start is not None:
            start = _placed(event, shift, drop_start, calendar)
        drafts.append(
            ImportDraft(
                title=title,
                project_number=number,
                project=project,
                start=start,
                estimate_minutes=minutes,
                estimate_days=days,
                notes=_draft_notes(event),
            )
        )
    return drafts


def _placed(
    event: ImportedEvent, shift: timedelta, drop_start: WallTime, calendar: WorkCalendar
) -> WallTime:
    if event.start is None:
        return drop_start
    moment = event.start + shift
    if event.all_day:
        return calendar.working_start(day_start(moment.date()))
    return calendar.working_start(wall_of(moment))


def _draft_notes(event: ImportedEvent) -> str:
    lines = [f"Lieu : {event.location}"] if event.location else []
    body = _BLANK_LINES_RE.sub("\n\n", event.notes.replace("\r\n", "\n")).strip()
    if body:
        lines.append(body)
    text = "\n".join(lines)
    return text if len(text) <= MAX_NOTES else text[: MAX_NOTES - 1].rstrip() + "…"
