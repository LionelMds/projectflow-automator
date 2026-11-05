"""Quick entry bar of the planning: one typed line, read back as chips while typing.

The bar parses the line with ``parse_entry`` (debounced) and shows what it understood: a
row of chips (dossier, title, type, people, dates, flags, warnings in amber and where
Entrée sends the line) and the plain-language interpretation. While typing, a popup
completes the word that ends at the cursor: dossiers after digits, people after letters,
teams too after ``+`` or ``@``, task types and absences after ``/``. A word the parser
already reads (a known dossier, initials, a day such as ``mar``) gets no completion, so
that Entrée creates the task. ``InlineEditor`` reuses the bar, compact, over a grid cell.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final, Literal

from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFocusEvent,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QHideEvent,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QResizeEvent,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListView,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from projectflow.core.planning.calendar import DAYS_PER_WEEK, WorkCalendar
from projectflow.core.planning.models import ABSENCE_LABELS, DEFAULT_TASK_TYPES, Resource
from projectflow.core.planning.quick_entry import (
    DAY_ABBREVIATIONS,
    PART_LABELS,
    WEEK_LAST_WEEKDAY,
    EntryContext,
    ParsedEntry,
    ProjectInfo,
    Token,
    fold_text,
    format_days,
    format_duration,
    format_span,
    parse_entry,
)
from projectflow.core.planning.walltime import WallTime, format_hhmm, minute_of_day, wall_date
from projectflow.ui.planning.grid_model import short_number
from projectflow.ui.planning.sync_badge import feedback_color
from projectflow.ui.theme import BODY_FAMILY, body_font, current_theme, heading_font, theme_notifier
from projectflow.ui.widgets.industry import MARK_GUTTER, paint_corner_marks

ChipTone = Literal["project", "plain", "muted", "warn", "go", "example", "hint"]
SuggestionKind = Literal["project", "person", "team", "type", "absence"]


@dataclass(frozen=True, slots=True)
class Availability:
    """Availability of a resource for the typed entry, as the completion shows it.

    ``text`` is the short note of the popup (« libre », « 62 % », « absent »); people who
    are not ``available`` are proposed after the others.
    """

    text: str
    available: bool = True


AvailabilityProvider = Callable[[str, ParsedEntry | None], "str | Availability"]
"""Availability of a resource id for the typed entry; a plain text does not reorder."""

DEBOUNCE_MS: Final = 80
FIELD_HEIGHT: Final = 40
COMPACT_FIELD_HEIGHT: Final = 32
PREFIX_WIDTH: Final = 38
COMPACT_PREFIX_WIDTH: Final = 26
FIELD_FONT_PX: Final = 15
COMPACT_FONT_PX: Final = 13
PREFIX_FONT_PX: Final = 22
COMPACT_PREFIX_FONT_PX: Final = 17
FIELD_GAP: Final = 10
HINT_FONT_PX: Final = 11
HINT_PADDING: Final = 6
HINT_HEIGHT: Final = 20
FOCUS_BORDER: Final = 2.0
CHIP_HEIGHT: Final = 24
COMPACT_CHIP_HEIGHT: Final = 22
CHIP_PADDING: Final = 8
HINT_CHIP_PADDING: Final = 2
CHIP_GAP: Final = 6
CHIP_FONT_PX: Final = 12
SWATCH_SIZE: Final = 8
SWATCH_GAP: Final = 6
BAR_SPACING: Final = 6
INTERPRETATION_FONT_PX: Final = 12
INTERPRETATION_PADDING: Final = 2
POPUP_WIDTH: Final = 360
POPUP_MAX_WIDTH: Final = 560
POPUP_ROW_HEIGHT: Final = 28
POPUP_GAP: Final = 2
POPUP_KEY_WIDTH: Final = 78
POPUP_KEY_PX: Final = 15
POPUP_DETAIL_PX: Final = 13
POPUP_NOTE_PX: Final = 12
POPUP_RAIL: Final = 2
POPUP_SWATCH: Final = 10
POPUP_ROWS: Final = 8
"""Rows the popup shows at once; it scrolls through the others."""
MAX_SUGGESTIONS: Final = 30
MIN_DOSSIER_DIGITS: Final = 2
MIN_NAME_LETTERS: Final = 2
SHAKE_MS: Final = 360
SHAKE_PX: Final = 6.0
SHAKE_WAVES: Final = 3
FLASH_MS: Final = 700
NOTE_CHIP_CHARS: Final = 40

PLACEHOLDER: Final = "Tapez une tâche : n° de dossier, travail, jours, initiales"
COMPACT_PLACEHOLDER: Final = "ex. débit portail 1,5j"
EXAMPLES: Final = (
    "montage marquise jeu-ven",
    "débit portail 1,5j lun",
    "relevé de cotes 2p",
    "livraison ven matin",
)
"""Examples without dossier nor person, for a planning that has neither yet."""
MAX_EXAMPLE_PROJECTS: Final = 4
DEADLINE_WEEKS: Final = 2
EXAMPLES_LABEL: Final = "Exemples :"
EXAMPLES_HINT: Final = "+ personne ou équipe · / type ou absence · ? provisoire · ! urgent"
KEEP_PROJECT_HINT: Final = "Maj+Entrée garde le dossier"
DESTINATION_LABELS: Final[Mapping[str, str]] = {
    "grid": "Entrée → planning",
    "unassigned": "Entrée → ligne Non affecté",
    "backlog": "Entrée → À planifier",
    "absence": "Entrée → absence",
}
# Canonical command word of each absence reason, as proposed by the completion.
ABSENCE_COMMAND_WORDS: Final = (
    ("vac", "vacances"),
    ("absent", "absent"),
    ("ecole", "ecole"),
    ("cie", "cie"),
    ("militaire", "militaire"),
    ("formation", "formation"),
    ("recup", "recuperation"),
    ("conge", "conge"),
)
_WHEN_KINDS: Final = frozenset({"day", "date", "week", "time", "part", "duration"})
_DOSSIER_QUERY_RE: Final = re.compile(r"\d[\d-]*")
_NAME_QUERY_RE: Final = re.compile(r"[a-z][a-z'-]*")
_NUMBER_PARTS_RE: Final = re.compile(r"(\d+)")
_SEPARATORS: Final = " \t,;"
_SUGGESTION_ROLE: Final = Qt.ItemDataRole.UserRole + 1
_HANDLED_KEYS: Final = frozenset(
    {
        Qt.Key.Key_Return,
        Qt.Key.Key_Enter,
        Qt.Key.Key_Escape,
        Qt.Key.Key_Up,
        Qt.Key.Key_Down,
        Qt.Key.Key_Tab,
        Qt.Key.Key_Backtab,
    }
)


# ------------------------------------------------------------------ chips ----


@dataclass(frozen=True, slots=True)
class EntryChip:
    """One chip under the field; ``start``/``end`` select its source text when clicked.

    ``strong`` is drawn first in semi-bold (a dossier number, initials, the title).
    """

    kind: str
    text: str = ""
    tone: ChipTone = "plain"
    start: int = -1
    end: int = -1
    strong: str = ""
    swatch: str = ""
    swatch_dark: str = ""

    @property
    def has_source(self) -> bool:
        return 0 <= self.start < self.end

    @property
    def clickable(self) -> bool:
        return self.has_source or self.kind == "example"

    @property
    def label(self) -> str:
        return f"{self.strong}{self.text}"


def day_text(day: date, today: date) -> str:
    """``jeu 22.10``, with the year when it is not the current one."""
    year = "" if day.year == today.year else f".{day.year}"
    return f"{DAY_ABBREVIATIONS[day.weekday()]} {day.day:02d}.{day.month:02d}{year}"


def instant_text(value: WallTime, today: date) -> str:
    """``jeu 22.10 06:30``."""
    return f"{day_text(wall_date(value), today)} {format_hhmm(minute_of_day(value))}"


def entry_length(entry: ParsedEntry, calendar: WorkCalendar) -> str:
    """Length of the entry as the interpretation line says it: ``½ j``, ``2 j``, ``3 h 30``.

    A dated entry tells the length its bar will show (``format_span`` on the calendar of
    its first person: ``ven ½j`` fills the Friday morning, a whole working day); a card
    tells its estimate, the working days kept by the parser first.
    """
    if entry.start is not None and entry.end is not None:
        return format_span(calendar, entry.start, entry.end)
    if entry.estimate_days > 0:
        return format_days(entry.estimate_days)
    return format_duration(entry.estimate_minutes) if entry.estimate_minutes else ""


def _example_people(context: EntryContext) -> list[str]:
    """Give the initials of the active people, in their order (no subcontractor)."""
    return [
        resource.initials
        for resource in context.resources
        if resource.active
        and not resource.deleted
        and not resource.is_subcontractor
        and resource.initials
    ]


def _example_numbers(context: EntryContext, projects: Sequence[ProjectInfo]) -> list[str]:
    """Most recent dossiers the parser finds, as typed: the short number this year."""
    numbers: list[str] = []
    for project in projects:
        if context.find_project(project.number) is None:
            continue
        numbers.append(short_number(project.number, context.default_year))
        if len(numbers) == MAX_EXAMPLE_PROJECTS:
            break
    return numbers


def _week_range_text(today: date) -> str:
    """Monday to Friday of next week as typed: ``26-30.10``, ``30.11-04.12``."""
    monday = today + timedelta(days=DAYS_PER_WEEK - today.weekday())
    friday = monday + timedelta(days=WEEK_LAST_WEEKDAY)
    if monday.month == friday.month:
        return f"{monday.day:02d}-{friday.day:02d}.{friday.month:02d}"
    return f"{monday.day:02d}.{monday.month:02d}-{friday.day:02d}.{friday.month:02d}"


def _joined(*parts: str) -> str:
    return " ".join(part for part in parts if part)


def absence_example(context: EntryContext | None) -> str:
    """``/vac PS 26-30.10``: an absence typed with the planning's first person, next week.

    Empty when the planning has nobody yet: a message then gives no example rather than
    initials nobody has.
    """
    if context is None:
        return ""
    people = _example_people(context)
    if not people:
        return ""
    return f"/vac {people[0]} {_week_range_text(context.today)}"


def entry_examples(
    context: EntryContext | None, projects: Sequence[ProjectInfo] = ()
) -> tuple[str, ...]:
    """Examples typed with the planning's own people and dossiers.

    The first people's initials and the latest dossiers of the répertoire: a click never
    types initials or a number the planning does not know. Each example is read back and
    kept only when it reads without warning, else its generic form (``EXAMPLES``, no
    dossier, no person) when that one does; ``EXAMPLES`` when nothing is left.
    """
    if context is None:
        return EXAMPLES
    people = _example_people(context)
    numbers = _example_numbers(context, projects)
    if not people and not numbers:
        return EXAMPLES

    def number(index: int) -> str:
        return numbers[min(index, len(numbers) - 1)] if numbers else ""

    def person(index: int) -> str:
        return people[index] if index < len(people) else people[0] if people else ""

    first = number(0)
    for project in projects:
        if first and short_number(project.number, context.default_year) == first:
            first = project.number  # The full number once, to show both forms.
            break
    today = context.today
    deadline = today + timedelta(
        days=(WEEK_LAST_WEEKDAY - today.weekday()) % DAYS_PER_WEEK + DAYS_PER_WEEK * DEADLINE_WEEKS
    )
    personal = (
        _joined(first, "montage marquise jeu-ven", *people[:2]),
        _joined(number(1), "débit portail 1,5j", person(2), "lun"),
        _joined(number(2), f"relevé de cotes 2p <{deadline.day:02d}.{deadline.month:02d}"),
        _joined("livraison", number(3), "ven matin"),
    )
    kept: list[str] = []
    for text, generic in zip(personal, EXAMPLES, strict=True):
        chosen = next((item for item in (text, generic) if _reads_cleanly(item, context)), "")
        if chosen and chosen not in kept:
            kept.append(chosen)
    absence = absence_example(context)
    if absence and _reads_cleanly(absence, context):
        kept.append(absence)
    return tuple(kept) or EXAMPLES


def _reads_cleanly(text: str, context: EntryContext) -> bool:
    entry = parse_entry(text, context)
    return entry.destination != "invalid" and not entry.warnings


def example_chips(
    examples: Sequence[str] = EXAMPLES, *, hint: bool = True
) -> tuple[EntryChip, ...]:
    """Chips shown while the field is empty; a click types the example.

    ``hint`` adds the syntax reminder as a last chip; the bar shows it on its own line.
    """
    chips = [EntryChip("hint", EXAMPLES_LABEL, tone="hint")]
    chips.extend(EntryChip("example", text, tone="example") for text in examples)
    if hint:
        chips.append(EntryChip("hint", EXAMPLES_HINT, tone="hint"))
    return tuple(chips)


def entry_chips(text: str, entry: ParsedEntry, context: EntryContext) -> tuple[EntryChip, ...]:
    """Chips telling what ``parse_entry`` understood of ``text``."""
    return _ChipBuilder(text, entry, context).build()


def _merged_spans(text: str, tokens: Iterable[Token]) -> list[tuple[int, int]]:
    """Token spans in text order, joined when only separators stand between them."""
    merged: list[tuple[int, int]] = []
    for token in sorted(tokens, key=lambda item: item.start):
        if merged and not text[merged[-1][1] : token.start].strip(_SEPARATORS):
            merged[-1] = (merged[-1][0], max(merged[-1][1], token.end))
        else:
            merged.append((token.start, token.end))
    return merged


def _first_span(text: str, tokens: Iterable[Token]) -> tuple[int, int]:
    spans = _merged_spans(text, tokens)
    return spans[0] if spans else (-1, -1)


def _leftover_span(text: str, tokens: Sequence[Token]) -> tuple[int, int]:
    """From the first to the last character no token claims: where the title was typed."""
    covered = [False] * len(text)
    for token in tokens:
        for index in range(token.start, min(token.end, len(text))):
            covered[index] = True
    free = [
        index
        for index, char in enumerate(text)
        if not covered[index] and char not in _SEPARATORS and char not in "+@"
    ]
    return (free[0], free[-1] + 1) if free else (-1, -1)


class _ChipBuilder:
    def __init__(self, text: str, entry: ParsedEntry, context: EntryContext) -> None:
        self.text = text
        self.entry = entry
        self.context = context
        self.resources = {resource.id: resource for resource in context.resources}
        self.types = {kind.key: kind for kind in (*DEFAULT_TASK_TYPES, *context.task_types)}
        self.chips: list[EntryChip] = []
        self.shown: set[str] = set()
        self.valid = entry.destination != "invalid"

    def build(self) -> tuple[EntryChip, ...]:
        if self.entry.absence is not None:
            self._absence()
        else:
            self._project()
            self._title()
            self._type()
        self._people()
        if self.entry.absence is None:
            self._when()
            self._required()
            self._flags()
        self._warnings()
        destination = DESTINATION_LABELS.get(self.entry.destination)
        if destination is not None:
            self.chips.append(EntryChip("destination", destination, tone="go"))
        return tuple(self.chips)

    # ----------------------------------------------------------- helpers ----

    def _tokens(self, *kinds: str, ambiguous: bool = False) -> list[Token]:
        return [
            token
            for token in self.entry.tokens
            if token.kind in kinds and token.ambiguous == ambiguous
        ]

    def _add(
        self,
        kind: str,
        span: tuple[int, int],
        *,
        text: str = "",
        strong: str = "",
        tone: ChipTone = "plain",
        swatch: str = "",
        swatch_dark: str = "",
    ) -> None:
        self.chips.append(
            EntryChip(
                kind,
                text=text,
                tone=tone,
                start=span[0],
                end=span[1],
                strong=strong,
                swatch=swatch,
                swatch_dark=swatch_dark,
            )
        )

    def _calendar(self) -> WorkCalendar:
        calendar_for = self.context.calendar_for
        people = self.entry.resource_ids
        if calendar_for is not None and people:
            return calendar_for(people[0])
        return self.context.calendar

    # -------------------------------------------------------------- what ----

    def _project(self) -> None:
        entry = self.entry
        tokens = [token for token in entry.tokens if token.kind == "project"]
        span = (tokens[0].start, tokens[0].end) if tokens else (-1, -1)
        if not entry.project_number:
            if self.valid:
                self._add("project", span, text="Sans dossier", tone="muted")
            return
        if entry.project is None:
            if tokens:
                self.shown.add(tokens[0].message)
            self._add("project", span, text=f"{entry.project_number} introuvable", tone="warn")
            return
        client = f" · {entry.project.client}" if entry.project.client else ""
        self._add("project", span, strong=entry.project_number, text=client, tone="project")

    def _title(self) -> None:
        title = self.entry.title
        if not title:
            return
        span = _leftover_span(self.text, self.entry.tokens)
        tone: ChipTone = "plain" if span[0] >= 0 else "muted"
        self._add("title", span, strong=title[:1].upper() + title[1:], tone=tone)

    def _type(self) -> None:
        if not self.valid:
            return
        kind = self.types.get(self.entry.task_type)
        if kind is None:
            return
        span = _first_span(self.text, self._tokens("type"))
        self._add("type", span, text=kind.label, swatch=kind.color, swatch_dark=kind.color_dark)

    def _absence(self) -> None:
        absence = self.entry.absence
        if absence is None:
            return
        span = _first_span(self.text, self._tokens("absence"))
        label_text = ABSENCE_LABELS.get(absence.reason, absence.reason)
        self._add("absence", span, strong=label_text, tone="project")
        today = self.context.today
        days = day_text(absence.first_day, today)
        if absence.last_day != absence.first_day:
            days = f"{days} → {day_text(absence.last_day, today)}"
        # Working days only: a holiday or a weekend alone counts 0 j (its warning says why).
        worked = len(self._calendar().days_between(absence.first_day, absence.last_day))
        if absence.part != "day":
            days = f"{days} {PART_LABELS[absence.part]}"
            count = format_days(0.5 if worked else 0)
        else:
            count = format_days(worked)
        when = _first_span(self.text, self._tokens(*_WHEN_KINDS))
        self._add("when", when, text=f"{days} · {count}")

    # --------------------------------------------------------------- who ----

    def _people(self) -> None:
        persons = self._tokens("person")
        teams = self._tokens("team")
        for resource_id in self.entry.resource_ids:
            resource = self.resources.get(resource_id)
            token = next((item for item in persons if item.value == resource_id), None)
            if token is None and resource is not None:
                token = next((item for item in teams if item.value == resource.team), None)
            span = (token.start, token.end) if token is not None else (-1, -1)
            strong, text = _person_label(resource, resource_id)
            self._add("person", span, strong=strong, text=text)

    # -------------------------------------------------------------- when ----

    def _when(self) -> None:
        entry = self.entry
        span = _first_span(self.text, self._tokens(*_WHEN_KINDS))
        length = entry_length(entry, self._calendar())
        if entry.start is not None and entry.end is not None:
            today = self.context.today
            when = f"{instant_text(entry.start, today)} → {instant_text(entry.end, today)}"
            self._add("when", span, text=f"{when} · {length}" if length else when)
        elif length:
            self._add("when", span, text=f"~{length}")

    def _required(self) -> None:
        entry = self.entry
        assigned = len(entry.resource_ids)
        if entry.required_people <= max(1, assigned):
            return
        span = _first_span(self.text, self._tokens("people"))
        text = (
            f"{assigned}/{entry.required_people} pers."
            if assigned
            else f"{entry.required_people} pers. prévues"
        )
        self._add("people", span, text=text)

    def _flags(self) -> None:
        entry = self.entry
        if entry.provisional:
            self._add(
                "provisional",
                _first_span(self.text, self._tokens("provisional")),
                text="Provisoire",
            )
        if entry.urgent:
            self._add("urgent", _first_span(self.text, self._tokens("urgent")), text="Urgent")
        if entry.deadline is not None:
            span = _first_span(self.text, self._tokens("deadline"))
            self._add(
                "deadline", span, text=f"Avant le {day_text(entry.deadline, self.context.today)}"
            )
        if entry.notes:
            note = entry.notes.splitlines()[0]
            if len(note) > NOTE_CHIP_CHARS:
                note = f"{note[: NOTE_CHIP_CHARS - 1].rstrip()}…"
            span = _first_span(self.text, self._tokens("note"))
            self._add("note", span, text=f"Note : {note}", tone="muted")

    def _warnings(self) -> None:
        for warning in self.entry.warnings:
            if warning in self.shown:
                continue
            self.shown.add(warning)
            token = next((item for item in self.entry.tokens if item.message == warning), None)
            span = (token.start, token.end) if token is not None else (-1, -1)
            self._add("warning", span, text=warning, tone="warn")


def _person_label(resource: Resource | None, resource_id: str) -> tuple[str, str]:
    """Split a person chip into initials (semi-bold) and name: ``("LBI", " · Luca Bianchi")``."""
    if resource is None:
        return resource_id, ""
    if resource.initials:
        return resource.initials, f" · {resource.name}"
    return resource.name, ""


# ------------------------------------------------------------ completion ----


@dataclass(frozen=True, slots=True)
class Suggestion:
    """A completion of the word under the cursor; ``insert`` replaces the word."""

    kind: SuggestionKind
    key: str
    detail: str
    insert: str
    note: str = ""
    swatch: str = ""
    swatch_dark: str = ""


def word_bounds(text: str, cursor: int) -> tuple[int, int]:
    """Start and end of the word around ``cursor`` (words are split by spaces)."""
    start = max(0, min(cursor, len(text)))
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    end = max(0, min(cursor, len(text)))
    while end < len(text) and not text[end].isspace():
        end += 1
    return start, end


def suggestions_for(
    word: str,
    context: EntryContext,
    projects: Sequence[ProjectInfo] = (),
    *,
    availability: Callable[[str], str | Availability] | None = None,
) -> tuple[Suggestion, ...]:
    """Completions of ``word`` (the text typed before the cursor), ``MAX_SUGGESTIONS`` at most.

    Nothing is proposed once the word already is one of the completions, so that Entrée
    creates the task instead of accepting a completion that changes nothing.
    """
    folded = fold_text(word)
    items: list[Suggestion]
    if folded.startswith("/"):
        items = _command_suggestions(folded[1:], context)
    elif folded[:1] in {"+", "@"} and folded:
        teams = _team_suggestions(folded[1:], word[0], context)
        items = teams + _people_suggestions(folded[1:], word[0], context, availability)
    elif (
        _DOSSIER_QUERY_RE.fullmatch(folded)
        and sum(char.isdigit() for char in folded) >= MIN_DOSSIER_DIGITS
    ):
        items = _project_suggestions(folded, projects)
    elif len(folded) >= MIN_NAME_LETTERS and _NAME_QUERY_RE.fullmatch(folded):
        # Bare letters never propose a team: "montage" is a task keyword, "+montage" the team.
        items = _people_suggestions(folded, "", context, availability)
    else:
        items = []
    if any(fold_text(item.insert) == folded for item in items):
        return ()
    return tuple(items[:MAX_SUGGESTIONS])


def _number_key(number: str) -> tuple[int, ...]:
    return tuple(int(part) for part in _NUMBER_PARTS_RE.findall(number))


def _number_matches(number: str, query: str) -> bool:
    _year, _dash, rest = number.partition("-")
    return number.startswith(query) or (bool(rest) and rest.startswith(query))


def _project_suggestions(query: str, projects: Sequence[ProjectInfo]) -> list[Suggestion]:
    found = sorted(
        (project for project in projects if _number_matches(project.number, query)),
        key=lambda project: _number_key(project.number),
        reverse=True,
    )
    return [
        Suggestion(
            kind="project",
            key=project.number,
            detail=" · ".join(
                part
                for part in (
                    project.client,
                    project.designation,
                    f"géré par {project.managed_by}" if project.managed_by else "",
                )
                if part
            ),
            insert=project.number,
        )
        for project in found
    ]


def _active(context: EntryContext) -> list[Resource]:
    return [resource for resource in context.resources if resource.active and not resource.deleted]


def _person_rank(resource: Resource, query: str) -> int | None:
    if not query:
        return 0
    initials = fold_text(resource.initials)
    names = fold_text(resource.name).split()
    if initials.startswith(query):
        return 0
    if names and names[0].startswith(query):
        return 1
    if any(name.startswith(query) for name in names[1:]):
        return 2
    return None


def _read_availability(value: str | Availability) -> Availability:
    return value if isinstance(value, Availability) else Availability(value)


@dataclass(frozen=True, slots=True)
class _RankedPerson:
    away: bool
    rank: int
    order: int
    name: str
    resource: Resource
    note: str


def _people_suggestions(
    query: str,
    prefix: str,
    context: EntryContext,
    availability: Callable[[str], str | Availability] | None,
) -> list[Suggestion]:
    active = _active(context)
    initials_count: dict[str, int] = {}
    for resource in active:
        key = fold_text(resource.initials)
        initials_count[key] = initials_count.get(key, 0) + 1
    teams = {team.key: team.name for team in context.teams}
    ranked: list[_RankedPerson] = []
    for resource in active:
        rank = _person_rank(resource, query)
        if rank is None:
            continue
        state = (
            _read_availability(availability(resource.id))
            if availability is not None
            else Availability("")
        )
        ranked.append(
            _RankedPerson(
                not state.available, rank, resource.order, resource.name, resource, state.text
            )
        )
    # The people who are not available for the typed dates come last.
    ranked.sort(key=lambda item: (item.away, item.rank, item.order, item.name))
    suggestions: list[Suggestion] = []
    for item in ranked:
        resource = item.resource
        unique = bool(resource.initials) and initials_count[fold_text(resource.initials)] == 1
        written = resource.initials if unique else resource.name
        team = "Sous-traitant" if resource.is_subcontractor else teams.get(resource.team, "")
        detail = " · ".join(part for part in (resource.name, team) if part)
        suggestions.append(
            Suggestion(
                kind="person",
                key=resource.initials or resource.name,
                detail=detail,
                insert=f"{prefix}{written}",
                note=item.note,
            )
        )
    return suggestions


def _team_suggestions(query: str, prefix: str, context: EntryContext) -> list[Suggestion]:
    members: dict[str, int] = {}
    for resource in _active(context):
        members[resource.team] = members.get(resource.team, 0) + 1
    suggestions: list[Suggestion] = []
    for team in sorted(context.teams, key=lambda item: item.order):
        names = [fold_text(name) for name in (team.alias, team.key, team.name) if name]
        if query and not any(name.startswith(query) for name in names):
            continue
        count = members.get(team.key, 0)
        people = f"{count} personne{'s' if count > 1 else ''}"
        written = f"{prefix}{team.alias or team.key}"
        suggestions.append(
            Suggestion(kind="team", key=written, detail=f"{team.name} · {people}", insert=written)
        )
    return suggestions


def _command_suggestions(query: str, context: EntryContext) -> list[Suggestion]:
    suggestions: list[Suggestion] = []
    for kind in context.task_types:
        words = (kind.key, kind.label, *kind.keywords)
        if query and not any(fold_text(word).startswith(query) for word in words):
            continue
        suggestions.append(
            Suggestion(
                kind="type",
                key=f"/{kind.key}",
                detail=kind.label,
                insert=f"/{kind.key}",
                swatch=kind.color,
                swatch_dark=kind.color_dark,
            )
        )
    for word, reason in ABSENCE_COMMAND_WORDS:
        absence = ABSENCE_LABELS.get(reason, reason)
        if query and not (word.startswith(query) or fold_text(absence).startswith(query)):
            continue
        suggestions.append(
            Suggestion(
                kind="absence", key=f"/{word}", detail=f"Absence · {absence}", insert=f"/{word}"
            )
        )
    return suggestions


# ----------------------------------------------------------------- widgets ----


def _swatch_color(light: str, dark: str) -> QColor:
    return QColor(dark if current_theme().is_dark and dark else light)


class ChipStrip(QWidget):
    """Painted, wrapping row of chips; a click on a chip emits it.

    Chips set with ``wrap=False`` stay on one line: those that do not fit are left out
    (no rectangle, never drawn). The full bar (not ``compact``) always keeps one row, so
    that the planning under it does not move when the chips come and go; chips that would
    take a second row are squeezed first (people by their initials, no reminder chip).
    """

    chip_clicked = Signal(object)

    def __init__(self, parent: QWidget | None = None, *, compact: bool = False) -> None:
        super().__init__(parent)
        self._chips: tuple[EntryChip, ...] = ()
        self._wrap = True
        self._reserve = not compact
        self._squeezed = False
        self._height = COMPACT_CHIP_HEIGHT if compact else CHIP_HEIGHT
        self._font = body_font(CHIP_FONT_PX)
        self._strong_font = body_font(CHIP_FONT_PX, QFont.Weight.DemiBold)
        self._rects: list[QRect] = []
        self._rects_width = -1
        self._hovered = -1
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def set_chips(self, chips: Sequence[EntryChip], *, wrap: bool = True) -> None:
        chips = tuple(chips)
        if chips == self._chips and wrap == self._wrap:
            return
        self._chips = chips
        self._wrap = wrap
        self._rects_width = -1
        self._hovered = -1
        self.updateGeometry()
        self.update()

    def chips(self) -> tuple[EntryChip, ...]:
        return self._chips

    def chip_rect(self, index: int) -> QRect:
        """Rectangle of a chip; null for a chip left out of a single line."""
        return self._layout(self.width())[index]

    def shown_chips(self) -> tuple[EntryChip, ...]:
        """Chips drawn at the current width (all of them, unless on a single line)."""
        rects = self._layout(self.width())
        return tuple(chip for chip, rect in zip(self._chips, rects, strict=True) if rect.isValid())

    def chip_at(self, point: QPoint) -> int:
        for index, rect in enumerate(self._layout(self.width())):
            if rect.contains(point):
                return index
        return -1

    # ------------------------------------------------------------ layout ----

    @property
    def squeezed(self) -> bool:
        """Whether the chips are squeezed to keep one row at the current width."""
        self._layout(self.width())
        return self._squeezed

    def _shows_text(self, chip: EntryChip) -> bool:
        """Tell whether the chip shows its text (a squeezed person shows initials only)."""
        return not (self._squeezed and chip.kind == "person" and chip.strong)

    def _chip_width(self, chip: EntryChip) -> int:
        if self._squeezed and chip.tone == "hint":
            return 0
        padding = HINT_CHIP_PADDING if chip.tone == "hint" else CHIP_PADDING
        width = 2 * padding
        if chip.swatch:
            width += SWATCH_SIZE + SWATCH_GAP
        width += QFontMetrics(self._strong_font).horizontalAdvance(chip.strong)
        if self._shows_text(chip):
            width += QFontMetrics(self._font).horizontalAdvance(chip.text)
        return width

    def _layout(self, width: int) -> list[QRect]:
        if width == self._rects_width:
            return self._rects
        width = max(width, 1)
        self._squeezed = False
        rects = self._place(width)
        if self._wrap and self._reserve and len({rect.y() for rect in rects if rect.isValid()}) > 1:
            self._squeezed = True
            rects = self._place(width)
        self._rects, self._rects_width = rects, width
        return rects

    def _place(self, width: int) -> list[QRect]:
        rects: list[QRect] = []
        x = y = 0
        full = False
        for chip in self._chips:
            chip_width = min(self._chip_width(chip), width)
            if not chip_width or full or (x and x + chip_width > width and not self._wrap):
                # Left out: squeezed away, or the single line is full (and so for the next).
                full = full or bool(chip_width)
                rects.append(QRect())
                continue
            if x and x + chip_width > width:
                x = 0
                y += self._height + CHIP_GAP
            rects.append(QRect(x, y, chip_width, self._height))
            x += chip_width + CHIP_GAP
        return rects

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        rects = self._layout(width)
        bottom = max((rect.bottom() + 1 for rect in rects if rect.isValid()), default=0)
        return max(bottom, self._height) if self._reserve else bottom

    def sizeHint(self) -> QSize:  # noqa: N802
        width = self.width() if self.width() > 1 else POPUP_WIDTH * 2
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        reserved = self._chips or self._reserve
        return QSize(0, self.heightForWidth(self.width()) if reserved else 0)

    # ------------------------------------------------------------- mouse ----

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        index = self.chip_at(event.position().toPoint())
        if index != self._hovered:
            self._hovered = index
            clickable = index >= 0 and self._chips[index].clickable
            self.setCursor(
                Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor
            )
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self._hovered = -1
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        index = self.chip_at(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and index >= 0:
            chip = self._chips[index]
            if chip.clickable:
                self.chip_clicked.emit(chip)
                event.accept()
                return
        super().mousePressEvent(event)

    # ------------------------------------------------------------- paint ----

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        for index, (chip, rect) in enumerate(
            zip(self._chips, self._layout(self.width()), strict=True)
        ):
            if rect.isValid():
                self._paint_chip(painter, chip, QRectF(rect), hovered=index == self._hovered)
        painter.end()

    def _tone_colors(
        self, chip: EntryChip, *, hovered: bool
    ) -> tuple[QColor | None, QColor | None, QColor]:
        """Ground, border and ink of a chip."""
        theme = current_theme()
        tones: dict[str, tuple[QColor | None, QColor | None, QColor]] = {
            "project": (
                theme.color("accent-100"),
                theme.color("accent-300"),
                theme.color("accent-800"),
            ),
            "plain": (
                theme.color("neutral-100"),
                theme.color("text", 0.10),
                theme.color("neutral-900"),
            ),
            "muted": (None, theme.color("text", 0.18), theme.color("neutral-700")),
            "warn": (feedback_color("warn", background=True), None, feedback_color("warn")),
            "go": (theme.color("accent"), theme.color("accent"), theme.color("bg")),
            "example": (None, theme.color("text", 0.34), theme.color("neutral-800")),
            "hint": (None, None, theme.color("neutral-700")),
        }
        ground, border, ink = tones[chip.tone]
        if hovered and chip.clickable and chip.tone != "go":
            border = theme.color("accent")
            if chip.tone in {"example", "muted", "plain"}:
                ink = theme.color("accent-800")
        return ground, border, ink

    def _paint_chip(
        self, painter: QPainter, chip: EntryChip, rect: QRectF, *, hovered: bool
    ) -> None:
        ground, border, ink = self._tone_colors(chip, hovered=hovered)
        if ground is not None:
            painter.fillRect(rect, ground)
        if border is not None:
            pen = QPen(border)
            pen.setWidthF(1.0)
            if chip.tone == "example" and not hovered:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        padding = HINT_CHIP_PADDING if chip.tone == "hint" else CHIP_PADDING
        left = rect.left() + padding
        right = rect.right() - padding + 1
        if chip.swatch:
            top = rect.center().y() - SWATCH_SIZE / 2
            painter.fillRect(
                QRectF(left, top, SWATCH_SIZE, SWATCH_SIZE),
                _swatch_color(chip.swatch, chip.swatch_dark),
            )
            left += SWATCH_SIZE + SWATCH_GAP
        painter.setPen(ink)
        flags = int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        if chip.strong:
            painter.setFont(self._strong_font)
            metrics = painter.fontMetrics()
            strong = metrics.elidedText(chip.strong, Qt.TextElideMode.ElideRight, int(right - left))
            painter.drawText(QRectF(left, rect.top(), right - left, rect.height()), flags, strong)
            left += metrics.horizontalAdvance(strong)
        if chip.text and right > left and self._shows_text(chip):
            painter.setFont(self._font)
            text = painter.fontMetrics().elidedText(
                chip.text, Qt.TextElideMode.ElideRight, int(right - left)
            )
            painter.drawText(QRectF(left, rect.top(), right - left, rect.height()), flags, text)


def _key_column_width(items: Sequence[Suggestion]) -> int:
    """Width of the key column, the same on every row so that the details line up."""
    metrics = QFontMetrics(heading_font(POPUP_KEY_PX))
    widest = max((metrics.horizontalAdvance(item.key) for item in items), default=0)
    return max(POPUP_KEY_WIDTH, widest + FIELD_GAP)


def _popup_width(items: Sequence[Suggestion]) -> int:
    """Width showing every row whole, between ``POPUP_WIDTH`` and ``POPUP_MAX_WIDTH``."""
    detail = QFontMetrics(body_font(POPUP_DETAIL_PX))
    note = QFontMetrics(body_font(POPUP_NOTE_PX))
    key_width = _key_column_width(items)
    widest = 0
    for item in items:
        width = key_width + detail.horizontalAdvance(item.detail)
        if item.swatch:
            width += POPUP_SWATCH + SWATCH_GAP
        if item.note:
            width += note.horizontalAdvance(item.note) + FIELD_GAP
        widest = max(widest, width)
    return max(POPUP_WIDTH, min(POPUP_MAX_WIDTH, widest + 2 * FIELD_GAP + 2))


class _SuggestionDelegate(QStyledItemDelegate):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.key_width = POPUP_KEY_WIDTH

    def sizeHint(  # noqa: N802
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> QSize:
        return QSize(super().sizeHint(option, index).width(), POPUP_ROW_HEIGHT)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        item = index.data(_SUGGESTION_ROLE)
        if not isinstance(item, Suggestion):
            return
        theme = current_theme()
        rect = QRectF(option.rect)
        state = option.state
        painter.save()
        if state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, theme.color("accent-100"))
            painter.fillRect(
                QRectF(rect.left(), rect.top(), POPUP_RAIL, rect.height()), theme.color("accent")
            )
        elif state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(rect, theme.color("text", 0.05))
        left = rect.left() + FIELD_GAP
        right = rect.right() - FIELD_GAP
        flags = int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        if item.swatch:
            top = rect.center().y() - POPUP_SWATCH / 2
            painter.fillRect(
                QRectF(left, top, POPUP_SWATCH, POPUP_SWATCH),
                _swatch_color(item.swatch, item.swatch_dark),
            )
            left += POPUP_SWATCH + SWATCH_GAP
        painter.setFont(heading_font(POPUP_KEY_PX))
        painter.setPen(theme.color("text"))
        painter.drawText(QRectF(left, rect.top(), self.key_width, rect.height()), flags, item.key)
        left += self.key_width
        if item.note:
            painter.setFont(body_font(POPUP_NOTE_PX))
            painter.setPen(theme.color("neutral-700"))
            note_width = painter.fontMetrics().horizontalAdvance(item.note)
            painter.drawText(
                QRectF(right - note_width, rect.top(), note_width, rect.height()),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                item.note,
            )
            right -= note_width + FIELD_GAP
        painter.setFont(body_font(POPUP_DETAIL_PX))
        painter.setPen(theme.color("neutral-800"))
        detail = painter.fontMetrics().elidedText(
            item.detail, Qt.TextElideMode.ElideRight, max(0, int(right - left))
        )
        painter.drawText(
            QRectF(left, rect.top(), max(0.0, right - left), rect.height()), flags, detail
        )
        painter.restore()


class SuggestionPopup(QFrame):
    """Completion list under the field: a window that never takes the focus."""

    picked = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._items: tuple[Suggestion, ...] = ()
        self._width = POPUP_WIDTH
        self._model = QStandardItemModel(self)
        self._view = QListView(self)
        self._view.setModel(self._model)
        self._delegate = _SuggestionDelegate(self._view)
        self._view.setItemDelegate(self._delegate)
        self._view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._view.setMouseTracking(True)
        self._view.setUniformItemSizes(True)
        self._view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.setFrameShape(QFrame.Shape.NoFrame)
        self._view.setStyleSheet("QListView { background: transparent; border: 0; outline: 0; }")
        self._view.pressed.connect(self._on_pressed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.addWidget(self._view)

    @property
    def view(self) -> QListView:
        return self._view

    def suggestions(self) -> tuple[Suggestion, ...]:
        return self._items

    def set_suggestions(self, items: Sequence[Suggestion]) -> None:
        items = tuple(items)
        if items == self._items:
            return
        self._items = items
        self._delegate.key_width = _key_column_width(items)
        scrolls = len(items) > POPUP_ROWS
        scroll_bar = self._view.verticalScrollBar().sizeHint().width() if scrolls else 0
        self._width = min(POPUP_MAX_WIDTH, _popup_width(items) + scroll_bar)
        self._model.clear()
        for suggestion in items:
            row = QStandardItem(suggestion.key)
            row.setData(suggestion, _SUGGESTION_ROLE)
            row.setEditable(False)
            self._model.appendRow(row)
        self.set_current_row(0)
        self.setFixedHeight(min(len(items), POPUP_ROWS) * POPUP_ROW_HEIGHT + 2)

    def preferred_width(self) -> int:
        """Width showing the rows whole (``POPUP_WIDTH`` at least)."""
        return self._width

    def current_row(self) -> int:
        return self._view.currentIndex().row() if self._items else -1

    def set_current_row(self, row: int) -> None:
        if not self._items:
            return
        index = self._model.index(row % len(self._items), 0)
        self._view.setCurrentIndex(index)
        self._view.scrollTo(index)

    def step(self, delta: int) -> None:
        self.set_current_row(self.current_row() + delta)

    def current(self) -> Suggestion | None:
        row = self.current_row()
        return self._items[row] if 0 <= row < len(self._items) else None

    def show_at(self, position: QPoint, width: int) -> None:
        self.setFixedWidth(width)
        screen = QGuiApplication.screenAt(position) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            position = QPoint(
                max(area.left(), min(position.x(), area.right() - width)),
                min(position.y(), area.bottom() - self.height()),
            )
        self.move(position)
        if not self.isVisible():
            self.show()
        self.raise_()

    def _on_pressed(self, index: QModelIndex) -> None:
        if 0 <= index.row() < len(self._items):
            self.picked.emit(self._items[index.row()])

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        theme = current_theme()
        rect = QRectF(self.rect())
        painter.fillRect(rect, theme.color("bg"))
        pen = QPen(theme.color("text", 0.30))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        painter.end()


class _EntryEdit(QLineEdit):
    """Line edit that hands Entrée, Échap, arrows and Tab to the bar before Qt does.

    Ctrl+Z and Ctrl+Y stay with the planning when the field has nothing to undo or redo
    (right after a line was created, say): the planning undoes its own actions.
    """

    focus_changed = Signal(bool)
    read_only_changed = Signal(bool)
    _CLAIMED: Final = frozenset(
        {Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Up, Qt.Key.Key_Down}
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._handler: Callable[[QKeyEvent], bool] | None = None

    def set_key_handler(self, handler: Callable[[QKeyEvent], bool] | None) -> None:
        self._handler = handler

    def _leaves_history(self, event: QKeyEvent) -> bool:
        """Undo or redo with nothing to undo or redo in the field."""
        if event.matches(QKeySequence.StandardKey.Undo):
            return not self.isUndoAvailable()
        if event.matches(QKeySequence.StandardKey.Redo):
            return not self.isRedoAvailable()
        return False

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ReadOnlyChange:
            self.read_only_changed.emit(self.isReadOnly())
        if isinstance(event, QKeyEvent):
            if event.type() == QEvent.Type.ShortcutOverride:
                if event.key() in self._CLAIMED:
                    event.accept()
                    return True
                if self._leaves_history(event):
                    # Not overridden: the planning's own Ctrl+Z / Ctrl+Y shortcut fires.
                    event.ignore()
                    return False
            if (
                event.type() == QEvent.Type.KeyPress
                and self._handler is not None
                and self._handler(event)
            ):
                event.accept()
                return True
        return super().event(event)

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.focus_changed.emit(True)  # noqa: FBT003

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self.focus_changed.emit(False)  # noqa: FBT003


class _EntryFrame(QWidget):
    """Field box: accent « » » prefix, hairline border, 2 px accent border and marks on focus."""

    def __init__(self, edit: _EntryEdit, *, compact: bool) -> None:
        super().__init__()
        self._edit = edit
        self._compact = compact
        self._prefix_width = COMPACT_PREFIX_WIDTH if compact else PREFIX_WIDTH
        self._field_height = COMPACT_FIELD_HEIGHT if compact else FIELD_HEIGHT
        self._hint = (
            ""
            if compact
            else QKeySequence("Ctrl+K").toString(QKeySequence.SequenceFormat.NativeText)
        )
        self._hint_font = body_font(HINT_FONT_PX)
        self._offset = 0.0
        self._flash = False
        layout = QHBoxLayout(self)
        layout.setSpacing(0)
        layout.addWidget(edit)
        self._layout = layout
        self._apply_margins()
        self.setFixedHeight(self._field_height + 2 * MARK_GUTTER)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _hint_width(self) -> int:
        if not self._hint:
            return 0
        return QFontMetrics(self._hint_font).horizontalAdvance(self._hint) + 2 * HINT_PADDING

    def _apply_margins(self) -> None:
        shift = round(self._offset)
        left = MARK_GUTTER + self._prefix_width + FIELD_GAP + shift
        right = (
            MARK_GUTTER + FIELD_GAP + self._hint_width() + (FIELD_GAP if self._hint else 0) - shift
        )
        self._layout.setContentsMargins(left, MARK_GUTTER + 1, right, MARK_GUTTER + 1)

    def set_offset(self, offset: float) -> None:
        self._offset = offset
        self._apply_margins()
        self.update()

    def set_flash(self, *, flash: bool) -> None:
        self._flash = flash
        self.update()

    @property
    def flashing(self) -> bool:
        return self._flash

    def field_rect(self) -> QRectF:
        g = MARK_GUTTER
        return (
            QRectF(self.rect())
            .adjusted(g + 0.5, g + 0.5, -g - 0.5, -g - 0.5)
            .translated(round(self._offset), 0)
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._edit.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        theme = current_theme()
        rect = self.field_rect()
        focused = self._edit.hasFocus()
        painter.fillRect(rect, theme.color("surface"))
        prefix = QRectF(rect.left(), rect.top(), self._prefix_width, rect.height())
        painter.fillRect(prefix, theme.color("err") if self._flash else theme.color("accent"))
        painter.setPen(theme.color("bg"))
        painter.setFont(heading_font(COMPACT_PREFIX_FONT_PX if self._compact else PREFIX_FONT_PX))
        painter.drawText(prefix, int(Qt.AlignmentFlag.AlignCenter), "»")
        if self._hint:
            self._paint_hint(painter, rect)
        if self._flash:
            border, width = theme.color("err"), FOCUS_BORDER
        elif focused:
            border, width = theme.color("accent"), FOCUS_BORDER
        else:
            border, width = theme.color("text", 0.34), 1.0
        pen = QPen(border)
        pen.setWidthF(width)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        inset = (width - 1.0) / 2
        painter.drawRect(rect.adjusted(inset, inset, -inset, -inset))
        if focused and not self._compact:
            paint_corner_marks(painter, rect)
        painter.end()

    def _paint_hint(self, painter: QPainter, rect: QRectF) -> None:
        theme = current_theme()
        width = self._hint_width()
        box = QRectF(
            rect.right() - FIELD_GAP - width,
            rect.center().y() - HINT_HEIGHT / 2,
            width,
            HINT_HEIGHT,
        )
        pen = QPen(theme.color("text", 0.26))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.drawRect(box)
        painter.setPen(theme.color("neutral-700"))
        painter.setFont(self._hint_font)
        painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), self._hint)


class _InterpretationLine(QWidget):
    """The line under the chips, always there in the full bar: the grid never jumps.

    It reads the interpretation on one line, elided (the whole sentence in the tooltip),
    or the syntax reminder while the field is empty (``hint``).
    """

    def __init__(self) -> None:
        super().__init__()
        self._text = ""
        self._hint = False
        self._font = body_font(INTERPRETATION_FONT_PX)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(QFontMetrics(self._font).height() + INTERPRETATION_PADDING)

    def text(self) -> str:
        return self._text

    @property
    def hint(self) -> bool:
        return self._hint

    def set_line(self, text: str, *, hint: bool = False) -> None:
        if (text, hint) == (self._text, self._hint):
            return
        self._text, self._hint = text, hint
        self.setAccessibleDescription(text)
        self._update_tooltip()
        self.update()

    def _update_tooltip(self) -> None:
        elided = QFontMetrics(self._font).horizontalAdvance(self._text) > self.width()
        self.setToolTip(self._text if elided and not self._hint else "")

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_tooltip()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        if not self._text:
            return
        painter = QPainter(self)
        painter.setFont(self._font)
        painter.setPen(current_theme().color("neutral-700" if self._hint else "neutral-800"))
        text = painter.fontMetrics().elidedText(
            self._text, Qt.TextElideMode.ElideRight, self.width()
        )
        flags = int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        painter.drawText(QRectF(self.rect()), flags, text)
        painter.end()


class QuickEntryBar(QWidget):
    """The Ctrl+K field of the planning tab (``compact`` for the inline editor).

    ``entry_submitted(entry, keep_project)`` fires on Entrée (``keep_project`` False) or
    Maj+Entrée (True: the dossier number stays typed for the next line), while the line is
    still in the field: a handler that cannot create it calls ``refuse()`` and the text
    stays. In the compact bar, Tab emits ``entry_submitted_next(entry)`` instead (create,
    then go on to the next cell). A line that cannot be created (``destination ==
    "invalid"``) is refused with a shake and ``entry_rejected``. ``entry_changed`` follows
    the parse while typing (``None`` for an empty field). Échap clears the field, leaves
    it and emits ``cancelled``.

    The empty field shows examples typed with the planning's own people and dossiers;
    none while the field is read-only. The chips row and the interpretation line keep
    their room in the full bar, so that the planning under it never moves while typing;
    an empty read-only field (nothing can be typed) gives that room back.
    """

    entry_submitted = Signal(object, bool)
    entry_submitted_next = Signal(object)
    entry_rejected = Signal(object)
    entry_changed = Signal(object)
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None, *, compact: bool = False) -> None:
        super().__init__(parent)
        self._compact = compact
        self._provider: Callable[[], EntryContext] | None = None
        self._availability: AvailabilityProvider | None = None
        self._projects: tuple[ProjectInfo, ...] = ()
        self._projects_version = 0
        self._examples_key: tuple[object, ...] | None = None
        self._examples: tuple[str, ...] = EXAMPLES
        self._context: EntryContext | None = None
        self._entry: ParsedEntry | None = None
        self._watched_window: QWidget | None = None
        self._submitting = False
        self._refused = False

        self._edit = _EntryEdit()
        self._edit.setProperty("role", "bare")
        font_px = COMPACT_FONT_PX if compact else FIELD_FONT_PX
        self._edit.setStyleSheet(
            f'QLineEdit {{ font-family: "{BODY_FAMILY}"; font-size: {font_px}px; }}'
        )
        self._edit.setPlaceholderText(COMPACT_PLACEHOLDER if compact else PLACEHOLDER)
        self._edit.setAccessibleName("Saisie rapide d'une tâche")
        self._edit.set_key_handler(self._handle_key)
        self._frame = _EntryFrame(self._edit, compact=compact)
        self._chips = ChipStrip(compact=compact)
        self._chips.setVisible(not compact)
        self._interpretation = _InterpretationLine()
        self._interpretation.setVisible(not compact)
        self._popup = SuggestionPopup(self)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self.refresh)
        self._shake = QVariantAnimation(self)
        self._shake.setDuration(SHAKE_MS)
        self._shake.setStartValue(0.0)
        self._shake.setEndValue(1.0)
        self._shake.valueChanged.connect(self._on_shake)
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.setInterval(FLASH_MS)
        self._flash_timer.timeout.connect(lambda: self._frame.set_flash(flash=False))

        self._edit.textChanged.connect(self._schedule)
        self._edit.cursorPositionChanged.connect(self._on_cursor_moved)
        self._edit.selectionChanged.connect(self._on_cursor_moved)
        self._edit.focus_changed.connect(self._on_focus_changed)
        self._edit.read_only_changed.connect(self._on_read_only_changed)
        self._chips.chip_clicked.connect(self._on_chip_clicked)
        self._popup.picked.connect(self.accept_suggestion)
        theme_notifier().changed.connect(self._repaint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(BAR_SPACING)
        layout.addWidget(self._frame)
        chips_row = QHBoxLayout()
        chips_row.setContentsMargins(MARK_GUTTER, 0, MARK_GUTTER, 0)
        chips_row.addWidget(self._chips)
        layout.addLayout(chips_row)
        interpretation_row = QHBoxLayout()
        interpretation_row.setContentsMargins(MARK_GUTTER, 0, MARK_GUTTER, 0)
        interpretation_row.addWidget(self._interpretation)
        layout.addLayout(interpretation_row)
        self.refresh()

    # ------------------------------------------------------------ inputs ----

    def set_context_provider(self, provider: Callable[[], EntryContext] | None) -> None:
        """Callable giving the parsing context (today, week shown, people, types…)."""
        self._provider = provider
        self.refresh()

    def set_projects(self, projects: Sequence[ProjectInfo]) -> None:
        """Dossiers proposed after digits, the most recent number first."""
        self._projects = tuple(projects)
        self._projects_version += 1
        if not self._edit.text().strip():
            self.refresh()

    def set_availability_provider(self, provider: AvailabilityProvider | None) -> None:
        self._availability = provider

    # ------------------------------------------------------------- state ----

    @property
    def compact(self) -> bool:
        return self._compact

    @property
    def line_edit(self) -> QLineEdit:
        return self._edit

    @property
    def chip_strip(self) -> ChipStrip:
        return self._chips

    @property
    def popup(self) -> SuggestionPopup:
        return self._popup

    @property
    def entry(self) -> ParsedEntry | None:
        """Last parse of the field, ``None`` when it is empty."""
        return self._entry

    @property
    def flashing(self) -> bool:
        return self._frame.flashing

    def text(self) -> str:
        return self._edit.text()

    def set_text(self, text: str) -> None:
        self._edit.setText(text)
        self._edit.setCursorPosition(len(text))
        self.refresh()

    def clear(self) -> None:
        self.set_text("")

    def chips(self) -> tuple[EntryChip, ...]:
        return self._chips.chips()

    def interpretation(self) -> str:
        """Tell what the typed line will do; empty for an empty field (not the reminder)."""
        line = self._interpretation
        return "" if line.hint or self._compact else line.text()

    def examples(self) -> tuple[str, ...]:
        """Examples offered under the empty field (none while it is read-only)."""
        return tuple(chip.text for chip in self._chips.chips() if chip.kind == "example")

    def suggestions_visible(self) -> bool:
        return self._popup.isVisible()

    def suggestions(self) -> tuple[Suggestion, ...]:
        return self._popup.suggestions() if self._popup.isVisible() else ()

    def focus_entry(self, prefill: str = "") -> None:
        """Put the cursor in the field (Ctrl+K); ``prefill`` replaces its text."""
        self._edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        if prefill:
            self.set_text(prefill)
        else:
            self._edit.end(False)  # noqa: FBT003

    # ----------------------------------------------------------- parsing ----

    def refresh(self) -> ParsedEntry | None:
        """Parse the field now (instead of waiting for the debounce) and redraw the chips."""
        self._timer.stop()
        previous = self._entry
        text = self._edit.text()
        self._context = self._provider() if self._provider is not None else None
        if not text.strip() or self._context is None:
            self._entry = None
            self._show_idle(text)
        else:
            entry = parse_entry(text, self._context)
            self._entry = entry
            chips = list(entry_chips(text, entry, self._context))
            if not self._compact and entry.project_number and entry.destination != "invalid":
                chips.append(EntryChip("hint", KEEP_PROJECT_HINT, tone="hint"))
            self._show_chips(chips)
            self._show_interpretation(entry.interpretation)
        self._update_popup()
        if self._entry != previous:
            self.entry_changed.emit(self._entry)
        return self._entry

    def submit(self, *, keep_project: bool = False, advance: bool = False) -> bool:
        """Emit the typed line; ``False`` (and a shake) when it is invalid or refused.

        ``advance`` (Tab in the compact bar) emits ``entry_submitted_next`` instead of
        ``entry_submitted``. The field is cleared after the signal, unless a handler called
        ``refuse()`` or typed another line.
        """
        entry = self.refresh()
        if entry is None or entry.destination == "invalid":
            self._reject(entry)
            return False
        text = self._edit.text()
        kept = ""
        if keep_project and entry.project_number:
            token = next((item for item in entry.tokens if item.kind == "project"), None)
            kept = f"{token.text if token is not None else entry.project_number} "
        self._popup.hide()
        self._submitting, self._refused = True, False
        try:
            if advance:
                self.entry_submitted_next.emit(entry)
            else:
                self.entry_submitted.emit(entry, keep_project)
        finally:
            self._submitting = False
        if self._refused:
            self._refused = False
            self._shake_field()
            return False
        if self._edit.text() == text:
            self.set_text(kept)
        return True

    def refuse(self) -> None:
        """Keep the line in the field: the planning could not create it (read-only, say).

        Called from an ``entry_submitted`` handler; the field shakes once the handler
        returns. Called at another time, the field only shakes.
        """
        if self._submitting:
            self._refused = True
        else:
            self._shake_field()

    def _schedule(self) -> None:
        self._timer.start()

    def _keeps_room(self) -> bool:
        """Whether the full bar keeps its chips row and line: not while read-only and empty."""
        return not self._compact and (
            not self._edit.isReadOnly() or bool(self._edit.text().strip())
        )

    def _show_chips(self, chips: Sequence[EntryChip], *, wrap: bool = True) -> None:
        self._chips.set_chips(chips, wrap=wrap)
        self._chips.setVisible(bool(chips) or self._keeps_room())

    def _show_interpretation(self, text: str, *, hint: bool = False) -> None:
        self._interpretation.set_line(text, hint=hint)
        self._interpretation.setVisible(self._keeps_room())

    def _show_idle(self, text: str) -> None:
        """Examples (on one line) and the syntax reminder under an empty, editable field."""
        if self._compact or text.strip() or self._edit.isReadOnly():
            self._show_chips(())
            self._show_interpretation("")
            return
        self._show_chips(example_chips(self._current_examples(), hint=False), wrap=False)
        self._show_interpretation(EXAMPLES_HINT, hint=True)

    def _current_examples(self) -> tuple[str, ...]:
        """``entry_examples`` for the current context, read again only when it changed."""
        context = self._context
        if context is None:
            return EXAMPLES
        key = (
            context.today,
            context.default_year,
            tuple(_example_people(context)),
            self._projects_version,
        )
        if key != self._examples_key:
            self._examples_key = key
            self._examples = entry_examples(context, self._projects)
        return self._examples

    def _reject(self, entry: ParsedEntry | None) -> None:
        self._shake_field()
        self.entry_rejected.emit(entry)

    def _shake_field(self) -> None:
        self._frame.set_flash(flash=True)
        self._flash_timer.start()
        self._shake.stop()
        self._shake.start()

    def _on_shake(self, value: object) -> None:
        progress = float(value) if isinstance(value, (int, float)) else 1.0
        wave = math.sin(progress * SHAKE_WAVES * 2 * math.pi)
        self._frame.set_offset(SHAKE_PX * wave * (1.0 - progress))

    # -------------------------------------------------------- completion ----

    def accept_suggestion(self, suggestion: object) -> None:
        """Replace the word under the cursor with ``suggestion`` (a ``Suggestion``)."""
        if not isinstance(suggestion, Suggestion):
            return
        text, cursor = self._edit.text(), self._edit.cursorPosition()
        start, end = word_bounds(text, cursor)
        tail = text[end:]
        inserted = suggestion.insert if tail.startswith(" ") else f"{suggestion.insert} "
        self._popup.hide()
        # Selected then typed over, not set: Ctrl+Z gives the word back.
        self._edit.setSelection(start, end - start)
        self._edit.insert(inserted)
        self._edit.setCursorPosition(start + len(suggestion.insert) + 1)
        self.refresh()

    def _availability_of(self, resource_id: str) -> str | Availability:
        if self._availability is None:
            return ""
        return self._availability(resource_id, self._entry)

    def _word_is_read(self, start: int, end: int) -> bool:
        """Whether the parser already gives the word a meaning a completion would change.

        A known dossier, initials, a team, a type, but also a day (``mar``), a part of day
        (``mat``) or a date: Entrée must create what the chips show. Unknown or ambiguous
        words (``LB`` for two people, a dossier not in the répertoire) are completed.
        """
        entry, context = self._entry, self._context
        if entry is None or context is None:
            return False
        for token in entry.tokens:
            if token.ambiguous or token.end <= start or token.start >= end:
                continue
            if token.kind == "project" and context.find_project(token.value) is None:
                continue
            return True
        return False

    def _update_popup(self) -> None:
        context = self._context
        if (
            context is None
            or not self._edit.hasFocus()
            or self._edit.hasSelectedText()
            or not self.isVisible()
        ):
            self._popup.hide()
            return
        text, cursor = self._edit.text(), self._edit.cursorPosition()
        start, end = word_bounds(text, cursor)
        # Only the word being typed, at the cursor: a completion replaces the whole word.
        if end != cursor or self._word_is_read(start, end):
            self._popup.hide()
            return
        availability = self._availability_of if self._availability is not None else None
        items = suggestions_for(
            text[start:cursor], context, self._projects, availability=availability
        )
        if not items:
            self._popup.hide()
            return
        self._popup.set_suggestions(items)
        width = max(POPUP_WIDTH, min(self._popup.preferred_width(), self._frame.width()))
        self._popup.show_at(self._popup_position(start, width), width)
        self._watch_window()

    def _popup_position(self, word_start: int, width: int) -> QPoint:
        text = self._edit.text()
        cursor = self._edit.cursorPosition()
        advance = self._edit.fontMetrics().horizontalAdvance(text[word_start:cursor])
        x = max(0, self._edit.cursorRect().left() - advance)
        frame_rect = self._frame.field_rect()
        left = self._edit.mapTo(self._frame, QPoint(x, 0)).x()
        left = int(min(left, max(frame_rect.left(), frame_rect.right() - width)))
        below = QPoint(left, int(frame_rect.bottom()) + POPUP_GAP)
        return self._frame.mapToGlobal(below)

    def _watch_window(self) -> None:
        window = self.window()
        if window is self._watched_window:
            return
        if self._watched_window is not None:
            self._watched_window.removeEventFilter(self)
        window.installEventFilter(self)
        self._watched_window = window

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._watched_window and event.type() in {
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.Hide,
            QEvent.Type.WindowDeactivate,
        }:
            self._popup.hide()
        return super().eventFilter(watched, event)

    # -------------------------------------------------------------- keys ----

    def _handle_key(self, event: QKeyEvent) -> bool:
        key = event.key()
        modifiers = event.modifiers()
        commanded = bool(
            modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
        )
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if commanded or key not in _HANDLED_KEYS:
            return False
        if self._timer.isActive():
            # Typed faster than the debounce: the popup must match the text, not a stale one.
            self.refresh()
        if self._popup.isVisible() and self._popup_key(key):
            return True
        if key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self.submit(keep_project=shift and not self._compact)
            return True
        if key == Qt.Key.Key_Escape:
            self._escape()
            return True
        if self._compact and key in {Qt.Key.Key_Tab, Qt.Key.Key_Backtab}:
            # The cell editor keeps Tab: create and go on (Maj+Tab does nothing).
            if key == Qt.Key.Key_Tab and not shift:
                self.submit(advance=True)
            return True
        return False

    def _popup_key(self, key: int) -> bool:
        if key in {Qt.Key.Key_Up, Qt.Key.Key_Down}:
            self._popup.step(1 if key == Qt.Key.Key_Down else -1)
            return True
        if key in {Qt.Key.Key_Tab, Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            suggestion = self._popup.current()
            if suggestion is not None:
                self.accept_suggestion(suggestion)
            return True
        if key == Qt.Key.Key_Escape:
            self._popup.hide()
            return True
        return False

    def _escape(self) -> None:
        self._popup.hide()
        self.clear()
        if not self._compact:
            self._edit.clearFocus()
        self.cancelled.emit()

    # ------------------------------------------------------------ events ----

    def _on_cursor_moved(self) -> None:
        # Typing moves the cursor too (the debounce is then running); only moving it with
        # the arrows or a click closes the popup, which then waits for the next keystroke.
        if not self._timer.isActive():
            self._popup.hide()

    def _on_focus_changed(self, focused: bool) -> None:  # noqa: FBT001
        self._frame.update()
        if not focused:
            self._popup.hide()

    def _on_chip_clicked(self, chip: object) -> None:
        if not isinstance(chip, EntryChip):
            return
        if chip.kind == "example":
            if not self._edit.isReadOnly():
                self.focus_entry(chip.text)
            return
        self._edit.setFocus(Qt.FocusReason.MouseFocusReason)
        if chip.has_source:
            self._edit.setSelection(chip.start, chip.end - chip.start)

    def _on_read_only_changed(self, _read_only: bool) -> None:  # noqa: FBT001
        self.refresh()

    def _repaint(self) -> None:
        self._frame.update()
        self._interpretation.update()
        self._chips.update()
        self._popup.update()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        self._popup.hide()
        super().hideEvent(event)

    def sizeHint(self) -> QSize:  # noqa: N802
        hint = super().sizeHint()
        return QSize(max(hint.width(), POPUP_WIDTH), hint.height())
