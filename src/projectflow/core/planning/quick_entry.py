"""Quick entry: one line of text turned into a planning task or an absence.

The Ctrl+K bar, typing on a grid cell, a range drawn over several rows and the detail
panel share this grammar, e.g. ``2026-121 montage marquise jeu-ven LBI YCH``. Elements
come in any order. Matching ignores case and accents; the title keeps the typed text.
Days are working days, as people count them and as the bars read them back: a morning
start counts its day, an afternoon start half of it, and Friday is a day that ends at
noon (``jeu 2j`` ends on Friday at 12:00, ``jeu apm 2j`` on Monday at 12:00, ``lun 5j``
on Friday at 12:00); hours and minutes are worked time. A card without date keeps its
days (``ParsedEntry.estimate_days``) until it is placed. An absence counts working days
too (``/vac LBI lun 5j`` is Monday to Friday).
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import lru_cache
from typing import Literal

from projectflow.core.planning.calendar import (
    DAYS_PER_WEEK,
    SNAP_MINUTES,
    WorkCalendar,
    WorkPeriod,
    snap,
)
from projectflow.core.planning.models import (
    ABSENCE_LABELS,
    DEFAULT_TASK_TYPE,
    DEFAULT_TASK_TYPES,
    AbsenceReason,
    DayPart,
    Resource,
    TaskType,
    Team,
    default_task_type_key,
)
from projectflow.core.planning.night import (
    DEFAULT_NIGHT_END,
    DEFAULT_NIGHT_START,
    MIN_NIGHT_MINUTES,
    MOON,
    NIGHT_EARLIEST_START,
    NIGHT_MORNING_START,
    NIGHT_WEEKEND_MESSAGE,
    elapsed_text,
    night_allowed,
    night_window,
)
from projectflow.core.planning.walltime import (
    MINUTES_PER_DAY,
    MINUTES_PER_HOUR,
    WallTime,
    day_start,
    format_hhmm,
    minute_of_day,
    wall_date,
)

Destination = Literal["grid", "unassigned", "backlog", "absence", "invalid"]
TokenKind = Literal[
    "project",
    "person",
    "team",
    "type",
    "absence",
    "day",
    "date",
    "week",
    "part",
    "time",
    "duration",
    "people",
    "provisional",
    "urgent",
    "deadline",
    "note",
    "night",
]

DAY_MINUTES = 540
"""Load of a typed day without date (the Monday-Thursday day), for ``estimate_minutes``.

A dated day follows the calendar instead (``WorkCalendar.add_days``)."""
HALF_DAY_MINUTES = DAY_MINUTES // 2
MAX_DURATION_MINUTES = 250 * DAY_MINUTES
"""Longest duration a line may add up to, about a year of work."""
MIN_PREFIX_LETTERS = 3
# A date typed without year further back than this means the same date next year.
PAST_DATE_DAYS = 30
# A week number this far behind the current one means that week next year.
PAST_WEEKS = 4
# A range whose end comes before its start wraps to next year only if it stays this short.
MAX_WRAP_DAYS = 92
YEARS_BACK = 1
YEARS_AHEAD = 5
WEEK_LAST_WEEKDAY = 4
CENTURY = 2000
TWO_DIGIT_YEARS = 100
UNREADABLE_WARNING = "Saisie non comprise : vérifier les dates et les durées"

# Initials equal to one of these words only count when typed in capitals ("relevé de
# cotes" keeps its "de", "DE" is a person).
COMMON_WORDS = frozenset(
    {
        "le",
        "la",
        "les",
        "de",
        "du",
        "des",
        "et",
        "en",
        "au",
        "aux",
        "un",
        "une",
        "sur",
        "par",
        "pour",
        "avec",
        "a",
        "ou",
        "pas",
        "ce",
        "se",
        "sa",
        "son",
        "ses",
        "ne",
        "il",
        "on",
        "y",
        "dans",
        "chez",
        "sans",
        "sous",
    }
)
# A number right after one of these words is a colour, a profile or a grade, never a dossier.
MATERIAL_PREFIXES = frozenset(
    {
        "ral",
        "hea",
        "heb",
        "hem",
        "ipe",
        "ipn",
        "inp",
        "upn",
        "unp",
        "upe",
        "inox",
        "aisi",
        "dn",
        "x",
        "ø",
    }
)
# Nor a number followed by one of these units: "150 pces", "200 x 100".
UNIT_WORDS = frozenset(
    {
        "mm",
        "cm",
        "m",
        "ml",
        "m2",
        "m3",
        "kg",
        "t",
        "l",
        "x",
        "pc",
        "pcs",
        "pce",
        "pces",
        "piece",
        "pieces",
        "ex",
    }
)
# "1.5 jours" is a duration, not the 1st of May.
DURATION_UNIT_WORDS = frozenset(
    {
        "j",
        "jr",
        "jrs",
        "jour",
        "jours",
        "journee",
        "journees",
        "h",
        "hr",
        "hrs",
        "heure",
        "heures",
        "min",
        "minute",
        "minutes",
        "mn",
    }
)
_MEASURE_WORDS = UNIT_WORDS | DURATION_UNIT_WORDS
ABSENCE_COMMANDS: dict[str, AbsenceReason] = {
    "vac": "vacances",
    "vacances": "vacances",
    "absent": "absent",
    "abs": "absent",
    "absence": "absent",
    "malade": "absent",
    "ecole": "ecole",
    "cie": "cie",
    "militaire": "militaire",
    "mil": "militaire",
    "pc": "militaire",
    "formation": "formation",
    "recup": "recuperation",
    "recuperation": "recuperation",
    "conge": "conge",
}
DAY_ABBREVIATIONS = ("lun", "mar", "mer", "jeu", "ven", "sam", "dim")
DAY_NAMES = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
PART_LABELS: dict[str, str] = {"morning": "matin", "afternoon": "après-midi"}

_WEEKDAY_WORDS: dict[str, int] = {
    **{abbreviation: index for index, abbreviation in enumerate(DAY_ABBREVIATIONS)},
    **{name: index for index, name in enumerate(DAY_NAMES)},
}
# Number of working days after today.
_RELATIVE_DAYS: dict[str, int] = {
    "auj": 0,
    "aujourd'hui": 0,
    "aujourdhui": 0,
    "demain": 1,
    "apres-demain": 2,
    "apres demain": 2,
}
_CHAR_FIXES: dict[str, str] = {
    "\u2018": "'",
    "\u2019": "'",
    "\u02bc": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u2010": "-",
    "\u2011": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u00d7": "x",
    "½": "½",
}

_END = r"(?=$|[\s,;?!\"«»])"
_RANGE = r"(?:\s*-\s*|\s+(?:a|au|jusqu'au|jusqu'a)\s+)"
_DAY_WORD = (
    r"(?:aujourd'?hui|auj|apres[- ]demain|demain|lundi|lun|mardi|mar|mercredi|mer"
    r"|jeudi|jeu|vendredi|ven|samedi|sam|dimanche|dim)"
)
_DATE_A = r"(?P<ad>\d{1,2})\.(?P<am>\d{1,2})(?:\.(?P<ay>\d{4}|\d{2}))?"
_DATE_B = r"(?P<bd>\d{1,2})\.(?P<bm>\d{1,2})(?:\.(?P<by>\d{4}|\d{2}))?"
_WEEK_PREFIX = r"(?:semaine\s|sem\.?\s?|s)"

_SEPARATOR_RE = re.compile(r"[\s,;]+")
_WORD_RE = re.compile(r"[^\s,;?!\"«»+]+")
_END_RE = re.compile(_END)
_NEXT_WORD_RE = re.compile(r"\s*([^\s,;?!\"«»+]+)")
_NOTE_RE = re.compile(r"\"(?P<q>[^\"]*)\"?|«(?P<g>[^»]*)»?")
_DEADLINE_RE = re.compile(
    r"(?:<\s?|avant\s+(?:le\s+)?)(?:(?P<d>\d{1,2})\.(?P<m>\d{1,2})(?:\.(?P<y>\d{4}|\d{2}))?"
    r"|(?P<w>" + _DAY_WORD + r"))" + _END
)
_COMMAND_RE = re.compile(r"/(?P<w>[^\s,;?!\"«»+/]+)")
_FLAGS_RE = re.compile(r"[?!]+")
_WEEK_RE = re.compile(
    _WEEK_PREFIX + r"(?P<a>\d{1,2})(?:\s*-\s*" + _WEEK_PREFIX + r"?(?P<b>\d{1,2}))?" + _END
)
_DATE_RE = re.compile(r"(?:du\s+)?" + _DATE_A + r"(?:" + _RANGE + _DATE_B + r")?" + _END)
_SHORT_DATE_RANGE_RE = re.compile(r"(?:du\s+)?(?P<ad>\d{1,2})" + _RANGE + _DATE_B + _END)
_TIME_RANGE_RE = re.compile(
    r"(?:de\s+|des\s+)?(?P<ah>\d{1,2})(?P<au>\s?[h:](?P<am>\d{2})?)?"
    r"(?:\s*-\s*|\s+(?:a|jusqu'a)\s+)(?P<bh>\d{1,2})\s?[h:](?P<bm>\d{2})?" + _END
)
_START_TIME_RE = re.compile(
    r"(?:a\s+partir\s+de|a|des|vers)\s+(?P<h>\d{1,2})\s?[h:](?P<m>\d{2})?" + _END
)
_CLOCK_RE = re.compile(r"(?P<h>\d{1,2}):(?P<m>\d{2})" + _END)
_PEOPLE_RE = re.compile(r"(?P<n>\d{1,2})\s?(?:personnes?|pers\.?|p)" + _END)
# "1j½", "1 j ½" (as format_days writes it), "1 ½ j", "1 1/2 j", "1 jour et demi".
_DAYS_RE = re.compile(
    r"(?:(?P<n>\d+(?:[.,]\d+)?)(?P<h1>\s?½|\s1/2)?|(?P<h0>½|1/2|demi-?))"
    r"\s?(?:journees?|jours?|jrs?|j)(?P<h2>\s?½|\s?1/2|\s+et\s+demie?)?" + _END
)
_HOURS_RE = re.compile(
    r"(?P<n>\d+[.,]\d+)\s?(?:heures?|hrs?|h)" + _END + r"|(?P<w>\d+)\s?(?:heures?|hrs?)" + _END
)
_MINUTES_RE = re.compile(r"(?P<n>\d+)\s?(?:minutes?|min|mn)" + _END)
_BARE_HOUR_RE = re.compile(r"(?P<h>\d{1,2})\s?h(?P<m>\d{2})?" + _END)
_PART_RE = re.compile(r"(?P<w>matinee|matin|mat|apres[- ]?midi|aprem|apm|pm|am)" + _END)
_DAY_RE = re.compile(
    r"(?:du\s+)?(?P<a>" + _DAY_WORD + r")(?:" + _RANGE + r"(?P<b>" + _DAY_WORD + r")"
    r"|\s+(?P<next>prochaine?))?" + _END
)
_FLAG_WORD_RE = re.compile(r"(?P<w>urgente?|provisoire)" + _END)
_NIGHT_RE = re.compile(r"(?:de\s+)?nuit" + _END)
_DOSSIER_RE = re.compile(r"(?:(?P<y>\d{4})-)?(?P<n>\d{3,4})(?:-(?P<s>\d{1,2}))?" + _END)
_BOLT_RE = re.compile(r"m\d{1,2}")
_TEXT_WORDS_RE = re.compile(r"[^\W_]+")
_SPACES_RE = re.compile(r"\s+")
_SPACE_BEFORE_PUNCTUATION_RE = re.compile(r"\s+([,.])")
_REPEATED_PUNCTUATION_RE = re.compile(r"([,;])(?:\s*[,;])+")
_TITLE_EDGES = " ,;:.-+/·"


@dataclass(frozen=True, slots=True)
class ProjectInfo:
    """Dossier details found in the répertoire for a typed number."""

    number: str
    client: str = ""
    contact: str = ""
    designation: str = ""
    managed_by: str = ""


@dataclass(frozen=True, slots=True)
class EntryContext:
    """What the parser needs to know besides the text.

    ``find_project`` receives a normalised number (``2026-118`` or ``2026-118-1``). The
    ``cell_*`` fields describe the grid cell or the range the text was typed on.
    ``calendar_for`` gives a resource's calendar (its canton): the first person of the
    entry sets the working days; ``calendar`` serves when nobody is named.
    """

    today: date
    week_start: date
    calendar: WorkCalendar
    resources: Sequence[Resource]
    teams: Sequence[Team]
    task_types: Sequence[TaskType]
    find_project: Callable[[str], ProjectInfo | None]
    default_year: int
    cell_resource_ids: tuple[str, ...] = ()
    cell_start: WallTime | None = None
    cell_end: WallTime | None = None
    calendar_for: Callable[[str | None], WorkCalendar] | None = None


@dataclass(frozen=True, slots=True)
class Token:
    """A recognised part of the text, ``start``/``end`` being offsets in the typed text.

    ``value`` is the normalised meaning: dossier number, resource id (candidate ids joined
    by commas when ambiguous), team key, type key, absence reason, ISO day or
    ``first/last`` days, ``HH:MM`` or ``HH:MM-HH:MM``, minutes, working days followed by
    ``j`` (``1.5j``), people count, part of day.
    """

    kind: TokenKind
    text: str
    value: str
    start: int
    end: int
    ambiguous: bool = False
    message: str = ""


@dataclass(frozen=True, slots=True)
class AbsenceDraft:
    """``note`` gathers the leftover text and the quoted notes, for ``Absence.note``."""

    resource_ids: tuple[str, ...]
    reason: AbsenceReason
    first_day: date
    last_day: date
    part: DayPart = "day"
    note: str = ""


@dataclass(frozen=True, slots=True)
class ParsedEntry:
    """What the line asks for.

    ``estimate_minutes`` is the worked time between ``start`` and ``end`` for a dated
    task; for a card it is the typed load, a day counting ``DAY_MINUTES``.
    ``estimate_days`` gives the working days: as typed when the duration has no hours and
    no typed range fixes the end (``ven ½j`` keeps ½ j), else those the dates cover (0
    when they are not whole or half days, or for a card with hours). ``create_task``
    takes both. ``off_hours`` marks night work (``nuit``, or hours typed in the evening
    such as ``22h-5h``): its dates are exact and ``estimate_minutes`` is real time.
    """

    project_number: str
    project: ProjectInfo | None
    title: str
    task_type: str
    resource_ids: tuple[str, ...]
    start: WallTime | None
    end: WallTime | None
    estimate_minutes: int
    required_people: int
    provisional: bool
    urgent: bool
    deadline: date | None
    notes: str
    absence: AbsenceDraft | None
    destination: Destination
    tokens: tuple[Token, ...]
    warnings: tuple[str, ...]
    interpretation: str
    estimate_days: float = 0.0
    ignored_duration: bool = False
    off_hours: bool = False


def parse_entry(text: str, context: EntryContext) -> ParsedEntry:
    """Read one line of quick entry; never raises on user text."""
    try:
        return _Parser(text, context).parse()
    except (ValueError, OverflowError):  # a calendar without any working day ahead
        return _unreadable_entry(text)


def _unreadable_entry(text: str) -> ParsedEntry:
    return ParsedEntry(
        project_number="",
        project=None,
        title="",
        task_type=DEFAULT_TASK_TYPE,
        resource_ids=(),
        start=None,
        end=None,
        estimate_minutes=0,
        required_people=1,
        provisional=False,
        urgent=False,
        deadline=None,
        notes="",
        absence=None,
        destination="invalid",
        tokens=(),
        warnings=(UNREADABLE_WARNING,),
        interpretation="Saisie incomplète" if text.strip() else "Saisie vide",
    )


@lru_cache(maxsize=4096)
def _fold_char(char: str) -> str:
    fixed = _CHAR_FIXES.get(char)
    if fixed is not None:
        return fixed
    decomposed = unicodedata.normalize("NFKD", char)
    return "".join(part for part in decomposed if not unicodedata.combining(part)).casefold()


def _command_word(label: str) -> str:
    """``/command`` of a type label: folded, letters and digits only (« Sous-traitance »)."""
    return "".join(char for char in fold_text(label) if char.isalnum())


def fold_text(text: str) -> str:
    """Text for matching: no accents, case folded, typographic quotes and dashes made plain."""
    return "".join(_fold_char(char) for char in text)


def format_duration(minutes: int) -> str:
    """Worked minutes in hours: ``870`` -> ``14 h 30``, ``540`` -> ``9 h``, ``30`` -> ``30 min``."""
    hours, rest = divmod(max(minutes, 0), MINUTES_PER_HOUR)
    if not hours:
        return f"{rest} min"
    return f"{hours} h {rest:02d}" if rest else f"{hours} h"


def round_days(days: float) -> float:
    """Working days to the half day, halves up as ``format_days`` shows them: 1.25 -> 1.5."""
    return _half_days(days) / 2


def format_days(days: float) -> str:
    """Working days to the half day: ``2`` -> ``2 j``, ``1.5`` -> ``1 j ½``, ``0.5`` -> ``½ j``."""
    whole, half = divmod(max(_half_days(days), 0), 2)
    if not whole:
        return "½ j" if half else "0 j"
    return f"{whole} j ½" if half else f"{whole} j"


def format_span(calendar: WorkCalendar, start: WallTime, end: WallTime) -> str:
    """Length of a dated task: its working days when whole or half (« 2 j »), else hours."""
    days = calendar.whole_days(start, end)
    if days is not None:
        return format_days(days)
    return format_duration(calendar.work_between(start, end))


def _half_days(days: float) -> int:
    """Count the half days in ``days``, rounded half up."""
    return math.floor(days * 2 + 0.5)


def _fold_with_origin(text: str) -> tuple[str, tuple[int, ...]]:
    chars: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        folded = _fold_char(char)
        chars.append(folded)
        origin.extend([index] * len(folded))
    return "".join(chars), tuple(origin)


def _monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _next_year(day: date) -> date:
    try:
        return day.replace(year=day.year + 1)
    except ValueError:  # 29 February
        return day.replace(year=day.year + 1, day=day.day - 1)


def _wrapped(day: date, after: date) -> date:
    """End of a range typed before its start: next year for ``28.12-05.01``, else as typed."""
    if day >= after:
        return day
    later = _next_year(day)
    return later if (later - after).days <= MAX_WRAP_DAYS else day


def _day_before(day_number: int, last: date) -> date | None:
    """Start of ``29-02.11``: in the month of the end, or the month before."""
    year, month = last.year, last.month
    if day_number > last.day:
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    try:
        return date(year, month, day_number)
    except ValueError:
        return None


def _clock(hour_text: str, minute_text: str | None) -> int | None:
    hour, minute = int(hour_text), int(minute_text or 0)
    value = hour * MINUTES_PER_HOUR + minute
    if minute >= MINUTES_PER_HOUR or value > MINUTES_PER_DAY:
        return None
    return value


def _round_duration(minutes: int) -> int:
    return max(SNAP_MINUTES, (minutes + SNAP_MINUTES // 2) // SNAP_MINUTES * SNAP_MINUTES)


def _typed_days(match: re.Match[str]) -> float:
    count = float(match["n"].replace(",", ".")) if match["n"] else 0.0
    halves = sum(1 for name in ("h0", "h1", "h2") if match[name])
    return count + halves / 2


def _hours_minutes(match: re.Match[str]) -> int:
    if match["n"]:
        return round(float(match["n"].replace(",", ".")) * MINUTES_PER_HOUR)
    return int(match["w"]) * MINUTES_PER_HOUR


def _plain_minutes(match: re.Match[str]) -> int:
    return int(match["n"])


_DURATION_READERS: tuple[tuple[re.Pattern[str], Callable[[re.Match[str]], int]], ...] = (
    (_HOURS_RE, _hours_minutes),
    (_MINUTES_RE, _plain_minutes),
)


def _span_value(first: date, last: date) -> str:
    return first.isoformat() if first == last else f"{first.isoformat()}/{last.isoformat()}"


def _clean_title(text: str) -> str:
    text = _SPACES_RE.sub(" ", text)
    text = _SPACE_BEFORE_PUNCTUATION_RE.sub(r"\1", text)
    text = _REPEATED_PUNCTUATION_RE.sub(r"\1", text)
    return text.strip(_TITLE_EDGES)


def _capitalised(text: str) -> str:
    return text[:1].upper() + text[1:]


@dataclass(frozen=True, slots=True)
class _Span:
    first: date
    last: date
    is_range: bool
    week_reference: bool = False


@dataclass(frozen=True, slots=True)
class _DayWord:
    first: str
    last: str | None
    next_week: bool
    token_index: int


class _Directory:
    """Resources, teams and task types, indexed by their folded names."""

    def __init__(self, context: EntryContext) -> None:
        self.resources = {resource.id: resource for resource in context.resources}
        self.active = tuple(
            resource for resource in context.resources if resource.active and not resource.deleted
        )
        self.teams = tuple(context.teams)
        self.task_types = tuple(context.task_types)
        self.default_type = default_task_type_key(self.task_types)
        self.initials: dict[str, list[str]] = {}
        for resource in self.active:
            if resource.initials.strip():
                key = fold_text(resource.initials.strip())
                self.initials.setdefault(key, []).append(resource.id)
        self.inactive: dict[str, list[str]] = {}
        for resource in context.resources:
            if resource.active or resource.deleted:
                continue
            names = (resource.initials.strip(), *resource.name.split()[:1])
            for key in dict.fromkeys(fold_text(name) for name in names if name):
                self.inactive.setdefault(key, []).append(resource.id)
        self.first_names = tuple(
            (fold_text(resource.name).split()[0], resource.id)
            for resource in self.active
            if resource.name.split()
        )
        full_names = (
            (" ".join(fold_text(resource.name).split()), resource.id)
            for resource in self.active
            if len(resource.name.split()) > 1
        )
        self.full_names = tuple(sorted(full_names, key=lambda item: -len(item[0])))
        self.keyword_types: dict[str, str] = {}
        for kind in self.task_types:
            for keyword in kind.keywords:
                self.keyword_types.setdefault(fold_text(keyword), kind.key)
        for kind in self.task_types:
            self.keyword_types.setdefault(fold_text(kind.key), kind.key)

    def people(self, word: str, original: str, *, forced: bool) -> tuple[str, ...]:
        """Resource ids that ``word`` names: initials, first name, then first-name prefix."""
        if word in COMMON_WORDS and not original.isupper() and not forced:
            return ()
        found = tuple(self.initials.get(word, ()))
        if found:
            return found
        if word in self.keyword_types and not forced:
            return ()
        found = tuple(resource_id for name, resource_id in self.first_names if name == word)
        if found or len(word) < MIN_PREFIX_LETTERS or not (forced or word.isalpha()):
            return found
        return tuple(resource_id for name, resource_id in self.first_names if name.startswith(word))

    def inactive_people(self, word: str, original: str, *, forced: bool) -> tuple[str, ...]:
        """Inactive resources whose initials or first name are ``word``."""
        if not forced and (
            word in self.keyword_types or (word in COMMON_WORDS and not original.isupper())
        ):
            return ()
        return tuple(self.inactive.get(word, ()))

    def full_name_at(self, folded: str, position: int) -> tuple[int, tuple[str, ...]] | None:
        """End and ids of the longest full name typed at ``position``."""
        for name, _ in self.full_names:
            end = position + len(name)
            if folded.startswith(name, position) and _END_RE.match(folded, end):
                ids = tuple(other_id for other, other_id in self.full_names if other == name)
                return end, ids
        return None

    def team(self, word: str) -> Team | None:
        def names(team: Team) -> tuple[str, ...]:
            return tuple(fold_text(name) for name in (team.key, team.alias, team.name) if name)

        exact = [team for team in self.teams if word in names(team)]
        if exact:
            return exact[0]
        if len(word) < MIN_PREFIX_LETTERS:
            return None
        prefixed = [
            team for team in self.teams if any(name.startswith(word) for name in names(team))
        ]
        return prefixed[0] if len(prefixed) == 1 else None

    def members(self, team: Team) -> tuple[str, ...]:
        return tuple(resource.id for resource in self.active if resource.team == team.key)

    def types_for_command(self, word: str) -> tuple[str, ...]:
        exact = [kind.key for kind in self.task_types if fold_text(kind.key) == word] or [
            kind.key for kind in self.task_types if _command_word(kind.label) == _command_word(word)
        ]
        if exact:
            return tuple(exact[:1])
        team = next(
            (team for team in self.teams if word in {fold_text(team.key), fold_text(team.alias)}),
            None,
        )
        first_of_team = self.type_of_team(team.key) if team is not None else None
        if first_of_team is not None:
            return (first_of_team,)
        if len(word) < MIN_PREFIX_LETTERS:
            return ()
        return tuple(
            kind.key
            for kind in self.task_types
            if fold_text(kind.key).startswith(word) or fold_text(kind.label).startswith(word)
        )

    def type_from_text(self, text: str) -> str | None:
        """Type of the first task-type keyword in ``text`` (a final ``s`` is ignored)."""
        for word in _TEXT_WORDS_RE.findall(fold_text(text)):
            for candidate in (word, word.removesuffix("s")):
                key = self.keyword_types.get(candidate)
                if key is not None:
                    return key
        return None

    def type_of_team(self, team_key: str) -> str | None:
        return next((kind.key for kind in self.task_types if kind.team == team_key), None)

    def type_of_resource(self, resource_id: str) -> str | None:
        resource = self.resources.get(resource_id)
        return self.type_of_team(resource.team) if resource is not None else None

    def type_label(self, key: str) -> str:
        for kind in (*self.task_types, *DEFAULT_TASK_TYPES):
            if kind.key == key:
                return kind.label
        return key

    def label(self, resource_id: str) -> str:
        resource = self.resources.get(resource_id)
        return resource.short_label if resource is not None else resource_id

    def long_label(self, resource_id: str) -> str:
        resource = self.resources.get(resource_id)
        if resource is None:
            return resource_id
        return f"{resource.initials} {resource.name}".strip()


class _Parser:
    def __init__(self, text: str, context: EntryContext) -> None:
        self.text = text
        self.context = context
        self.calendar = context.calendar
        self.directory = _Directory(context)
        self.folded, self.origin = _fold_with_origin(text)
        self.tokens: list[Token] = []
        self.spans: list[tuple[int, int]] = []
        self.warnings: list[str] = []
        self.previous_word = ""
        # The word just skipped was a number after RAL, HEA...: "RAL 7016 mat" is a finish.
        self.material_value = False
        self.people: list[str] = []
        self.notes: list[str] = []
        self.task_type = ""
        self.absence_reason: AbsenceReason | None = None
        self.project_number = ""
        self.project: ProjectInfo | None = None
        self.dossier_seen = False
        self.typed_spans: list[_Span] = []
        self.day_words: list[_DayWord] = []
        self.part: DayPart | None = None
        self.start_minute: int | None = None
        self.end_minute: int | None = None
        self.bare_hours: list[tuple[int, int]] = []
        # Typed durations: worked minutes of the hours, and working days as half days.
        self.durations: list[int] = []
        self.half_days = 0
        self.required: int | None = None
        self.provisional = False
        self.urgent = False
        self.deadline: date | None = None
        self.ignored_duration = False
        self.night_word = False
        self.off_hours = False

    # ------------------------------------------------------------- result ----

    def parse(self) -> ParsedEntry:
        self._scan()
        people = self._people()
        if self.context.calendar_for is not None and people:
            self.calendar = self.context.calendar_for(people[0])
        self._resolve_bare_hours()
        span = self._resolve_span()
        typed_title = _clean_title(self._leftover())
        task_type = self._task_type(typed_title, people)
        absence = None
        if self.absence_reason is not None:
            absence = self._absence(span, people, typed_title)
        night = self._night(span) if absence is None else None
        start: WallTime | None
        end: WallTime | None
        if night is not None:
            start, end = night
            self.off_hours = True
        else:
            start, end = (None, None) if absence is not None else self._schedule(span)
        if start is not None:
            self._warn_if_past(wall_date(start))
        destination = self._destination(typed_title, absence, start, people)
        title = typed_title
        if absence is None and destination != "invalid":
            title = self._title(typed_title, task_type)
        estimate, estimate_days = self._estimate(start, end, absence)
        entry = ParsedEntry(
            project_number=self.project_number,
            project=self.project,
            title=title,
            task_type=task_type,
            resource_ids=people,
            start=start,
            end=end,
            estimate_minutes=estimate,
            required_people=self.required or max(1, len(people)),
            provisional=self.provisional,
            urgent=self.urgent,
            deadline=self.deadline,
            notes="\n".join(self.notes),
            absence=absence,
            destination=destination,
            tokens=tuple(self.tokens),
            warnings=(),
            interpretation="",
            estimate_days=estimate_days,
            ignored_duration=self.ignored_duration,
            off_hours=self.off_hours,
        )
        return replace(
            entry,
            warnings=tuple(dict.fromkeys(self.warnings)),
            interpretation=self._describe(entry),
        )

    def _estimate(
        self, start: WallTime | None, end: WallTime | None, absence: AbsenceDraft | None
    ) -> tuple[int, float]:
        """``estimate_minutes`` and ``estimate_days`` of the entry.

        Days typed alone keep their count once dated: ``ven ½j`` and ``jeu 1j½`` both end
        on Friday at noon, which ``whole_days`` would read as 1 j and 2 j.
        """
        if absence is not None:
            return 0, 0.0
        if self.off_hours and start is not None and end is not None:
            return end - start, 0.0
        typed_days = self.half_days / 2 if not self.durations else 0.0
        if start is None or end is None:
            return self._duration_minutes(), typed_days
        minutes = self.calendar.work_between(start, end)
        if typed_days and not self.ignored_duration:
            return minutes, typed_days
        return minutes, self.calendar.whole_days(start, end) or 0.0

    def _duration_minutes(self) -> int:
        """Typed durations as a load: the hours as typed, a day as ``DAY_MINUTES``."""
        return sum(self.durations) + self.half_days * HALF_DAY_MINUTES

    def _people(self) -> tuple[str, ...]:
        """Typed people; the cell's rows only when nobody, even unknown, was typed."""
        typed = tuple(dict.fromkeys(self.people))
        if typed or any(token.kind in {"person", "team"} for token in self.tokens):
            return typed
        return self.context.cell_resource_ids

    def _warn_if_past(self, day: date) -> None:
        if day < self.context.today:
            self._warn(f"Date passée : {self._day_label(day)}")

    def _title(self, typed_title: str, task_type: str) -> str:
        if typed_title:
            return typed_title
        if self.project is not None and self.project.designation:
            return self.project.designation
        return self.directory.type_label(task_type)

    def _task_type(self, title: str, people: tuple[str, ...]) -> str:
        if self.task_type:
            return self.task_type
        designation = self.project.designation if self.project is not None else ""
        found = self.directory.type_from_text(title or designation)
        if found is None and people:
            found = self.directory.type_of_resource(people[0])
        return found or self.directory.default_type

    def _destination(
        self,
        title: str,
        absence: AbsenceDraft | None,
        start: WallTime | None,
        people: tuple[str, ...],
    ) -> Destination:
        flags = {"provisional", "urgent"}
        meaningful = bool(title or self.project_number) or any(
            token.kind not in flags and not token.ambiguous for token in self.tokens
        )
        if not meaningful:
            return "invalid"
        if absence is not None:
            if absence.resource_ids:
                return "absence"
            self._warn("Indiquer qui est absent")
            return "invalid"
        if start is None:
            return "backlog"
        return "grid" if people else "unassigned"

    # ------------------------------------------------------------ scanning ----

    def _scan(self) -> None:
        steps: tuple[Callable[[int], int | None], ...] = (
            self._scan_note,
            self._scan_upper_initials,
            self._scan_deadline,
            self._scan_prefixed,
            self._scan_command,
            self._scan_flags,
            self._scan_week,
            self._scan_date,
            self._scan_time_range,
            self._scan_start_time,
            self._scan_people_count,
            self._scan_duration,
            self._scan_bare_hour,
            self._scan_part,
            self._scan_night,
            self._scan_day,
            self._scan_flag_word,
            self._scan_dossier,
            self._scan_person,
        )
        position = 0
        while position < len(self.folded):
            separator = _SEPARATOR_RE.match(self.folded, position)
            if separator is not None:
                position = separator.end()
                continue
            for step in steps:
                found = step(position)
                if found is not None:
                    position = found
                    break
            else:
                position = self._skip_word(position)

    def _original_span(self, start: int, end: int) -> tuple[int, int]:
        begin = self.origin[start] if start < len(self.origin) else len(self.text)
        finish = self.origin[end - 1] + 1 if end > start else begin
        while finish < len(self.text) and unicodedata.combining(self.text[finish]):
            finish += 1
        return begin, finish

    def _source(self, start: int, end: int) -> str:
        begin, finish = self._original_span(start, end)
        return self.text[begin:finish]

    def _warn(self, message: str) -> None:
        self.warnings.append(message)

    def _add(
        self,
        kind: TokenKind,
        start: int,
        end: int,
        value: str = "",
        *,
        ambiguous: bool = False,
        message: str = "",
    ) -> int:
        begin, finish = self._original_span(start, end)
        self.tokens.append(
            Token(
                kind=kind,
                text=self.text[begin:finish],
                value=value,
                start=begin,
                end=finish,
                ambiguous=ambiguous,
                message=message,
            )
        )
        self.spans.append((begin, finish))
        if message:
            self._warn(message)
        self.previous_word = ""
        self.material_value = False
        return end

    def _skip_word(self, position: int) -> int:
        match = _WORD_RE.match(self.folded, position)
        end = match.end() if match is not None else position + 1
        word = self.folded[position:end].rstrip(".:")
        self.material_value = self._after_material() and word.isdigit()
        self.previous_word = word
        return end

    def _scan_note(self, position: int) -> int | None:
        if self.folded[position] == '"' and position and self.folded[position - 1].isalnum():
            return None  # an inch mark: tube 2"
        match = _NOTE_RE.match(self.folded, position)
        if match is None:
            return None
        group = "q" if match["q"] is not None else "g"
        note = self._source(*match.span(group)).strip()
        if note:
            self.notes.append(note)
        closing = '"' if group == "q" else "»"
        closed = len(match.group()) > 1 and match.group().endswith(closing)
        message = "" if closed else "Guillemet non fermé : la suite est une note"
        return self._add("note", match.start(), match.end(), note, message=message)

    def _scan_upper_initials(self, position: int) -> int | None:
        match = _WORD_RE.match(self.folded, position)
        if match is None:
            return None
        word = match.group().rstrip(".:")
        end = position + len(word)
        if not word or word in MATERIAL_PREFIXES or not self._source(position, end).isupper():
            return None
        ids = tuple(self.directory.initials.get(word, ()))
        return self._add_people(position, end, ids) if ids else None

    def _scan_deadline(self, position: int) -> int | None:
        match = _DEADLINE_RE.match(self.folded, position)
        if match is None:
            return None
        if match["w"] is not None:
            day: date | None = self._resolve_day_word(match["w"], reference=None, next_week=False)
        else:
            day = self._make_date(match["d"], match["m"], match["y"])
        if day is None:
            text = self._source(*match.span())
            return self._add(
                "deadline", *match.span(), ambiguous=True, message=f"Date invalide : {text}"
            )
        self.deadline = self.deadline or day
        return self._add("deadline", *match.span(), day.isoformat())

    def _scan_prefixed(self, position: int) -> int | None:
        if self.folded[position] not in "+@":
            return None
        inner = position + 1
        full = self.directory.full_name_at(self.folded, inner)
        if full is not None:
            return self._add_people(position, full[0], full[1])
        match = _WORD_RE.match(self.folded, inner)
        if match is None:
            self.spans.append(self._original_span(position, inner))
            return inner
        word = match.group().rstrip(".:")
        end = inner + len(word)
        ids = self.directory.people(word, self._source(inner, end), forced=True)
        if ids:
            return self._add_people(position, end, ids)
        team = self.directory.team(word)
        if team is None:
            return self._add_unknown(position, inner, end)
        members = self.directory.members(team)
        self.people.extend(members)
        message = "" if members else f"L'équipe {team.name} n'a aucun membre actif"
        return self._add("team", position, end, team.key, message=message)

    def _add_unknown(self, position: int, inner: int, end: int) -> int:
        word, original = self.folded[inner:end], self._source(inner, end)
        inactive = self.directory.inactive_people(word, original, forced=True)
        if inactive:
            return self._add_inactive(position, end, inactive)
        message = f"« {self._source(position, end)} » : personne ou équipe inconnue"
        return self._add("person", position, end, ambiguous=True, message=message)

    def _scan_command(self, position: int) -> int | None:
        match = _COMMAND_RE.match(self.folded, position)
        if match is None:
            return None
        word = match["w"]
        reason = ABSENCE_COMMANDS.get(word)
        if reason is not None:
            self.absence_reason = self.absence_reason or reason
            return self._add("absence", *match.span(), reason)
        keys = self.directory.types_for_command(word)
        if len(keys) == 1:
            self.task_type = self.task_type or keys[0]
            return self._add("type", *match.span(), keys[0])
        text = self._source(*match.span())
        if keys:
            labels = ", ".join(self.directory.type_label(key) for key in keys)
            message = f"« {text} » : plusieurs types possibles ({labels})"
        else:
            message = f"Commande « {text} » inconnue"
        return self._add("type", *match.span(), ",".join(keys), ambiguous=True, message=message)

    def _scan_flags(self, position: int) -> int | None:
        match = _FLAGS_RE.match(self.folded, position)
        if match is None:
            return None
        for offset, char in enumerate(match.group()):
            at = match.start() + offset
            if char == "?":
                self.provisional = True
                self._add("provisional", at, at + 1)
            else:
                self.urgent = True
                self._add("urgent", at, at + 1)
        return match.end()

    def _scan_week(self, position: int) -> int | None:
        match = _WEEK_RE.match(self.folded, position)
        if match is None:
            return None
        text = self._source(*match.span())
        first = self._iso_monday(int(match["a"]))
        last_monday = first
        if match["b"] is not None and first is not None:
            last_monday = self._iso_monday(int(match["b"]), after=first)
        if first is None or last_monday is None:
            message = f"Semaine invalide : {text}"
            return self._add("week", *match.span(), ambiguous=True, message=message)
        if last_monday < first:
            message = f"Fin avant le début : {text}"
            return self._add("week", *match.span(), ambiguous=True, message=message)
        last = last_monday + timedelta(days=WEEK_LAST_WEEKDAY)
        single = match["b"] is None
        self.typed_spans.append(_Span(first, last, is_range=True, week_reference=single))
        return self._add("week", *match.span(), _span_value(first, last))

    def _scan_date(self, position: int) -> int | None:
        match = _SHORT_DATE_RANGE_RE.match(self.folded, position) or _DATE_RE.match(
            self.folded, position
        )
        if match is None:
            return None
        groups = match.groupdict()
        if groups.get("ay") is None and groups["by"] is None and self._is_measure(match.end()):
            return None
        if "am" in groups:
            first = self._make_date(groups["ad"], groups["am"], groups["ay"])
            last = first
            if groups["bd"] is not None and first is not None:
                last = self._make_date(groups["bd"], groups["bm"], groups["by"], after=first)
        else:
            last = self._make_date(groups["bd"], groups["bm"], groups["by"])
            first = _day_before(int(groups["ad"]), last) if last is not None else None
        text = self._source(*match.span())
        if first is None or last is None:
            return self._add(
                "date", *match.span(), ambiguous=True, message=f"Date invalide : {text}"
            )
        if last < first:
            message = f"Fin avant le début : {text}"
            return self._add("date", *match.span(), ambiguous=True, message=message)
        self.typed_spans.append(_Span(first, last, is_range=last > first))
        return self._add("date", *match.span(), _span_value(first, last))

    def _scan_time_range(self, position: int) -> int | None:
        match = _TIME_RANGE_RE.match(self.folded, position)
        if match is None:
            return None
        first = _clock(match["ah"], match["am"])
        last = _clock(match["bh"], match["bm"])
        if first is None or last is None:
            return self._invalid_time(match)
        if match["au"] is None and max(first, last) < self.calendar.day_start_minute:
            # "soudure 2 à 3h" is an estimate, not a night shift.
            minutes = max(first, last)
            if minutes > 0:
                text = self._source(*match.span())
                duration = format_duration(_round_duration(minutes))
                self._warn(f"« {text} » compris comme une durée de {duration}")
            return self._add_duration(match.start(), match.end(), minutes)
        return self._set_times(match, snap(first), snap(last), raw=(first, last))

    def _scan_start_time(self, position: int) -> int | None:
        match = _START_TIME_RE.match(self.folded, position) or _CLOCK_RE.match(
            self.folded, position
        )
        if match is None:
            return None
        value = _clock(match["h"], match["m"])
        if value is None:
            return self._invalid_time(match)
        return self._set_times(match, snap(value), None, raw=(value, None))

    def _invalid_time(self, match: re.Match[str]) -> int:
        message = f"Heure invalide : {self._source(*match.span())}"
        return self._add("time", *match.span(), ambiguous=True, message=message)

    def _set_times(
        self,
        match: re.Match[str],
        first: int,
        last: int | None,
        *,
        raw: tuple[int, int | None],
    ) -> int:
        text = self._source(*match.span())
        value = format_hhmm(first) if last is None else f"{format_hhmm(first)}-{format_hhmm(last)}"
        message = "" if (first, last) == raw else f"{text} arrondi à {value}"
        if self.start_minute is not None:
            message = "Plusieurs heures de début : la première est retenue"
        else:
            self.start_minute, self.end_minute = first, last
        return self._add("time", *match.span(), value, message=message)

    def _scan_people_count(self, position: int) -> int | None:
        match = _PEOPLE_RE.match(self.folded, position)
        if match is None:
            return None
        count = int(match["n"])
        if count < 1:
            message = f"Nombre de personnes invalide : {self._source(*match.span())}"
            return self._add("people", *match.span(), ambiguous=True, message=message)
        self.required = self.required or count
        return self._add("people", *match.span(), str(count))

    def _scan_duration(self, position: int) -> int | None:
        days = _DAYS_RE.match(self.folded, position)
        if days is not None:
            return self._add_days(days.start(), days.end(), _typed_days(days))
        for pattern, reader in _DURATION_READERS:
            match = pattern.match(self.folded, position)
            if match is not None:
                return self._add_duration(match.start(), match.end(), reader(match))
        return None

    def _add_days(self, start: int, end: int, days: float) -> int:
        text = self._source(start, end)
        if days <= 0:
            return self._add(
                "duration", start, end, ambiguous=True, message=f"Durée nulle : {text}"
            )
        if self._too_long(days * DAY_MINUTES):
            return self._add(
                "duration", start, end, ambiguous=True, message=f"Durée trop longue : {text}"
            )
        halves = max(_half_days(days), 1)
        message = "" if halves == days * 2 else f"{text} arrondi à {format_days(halves / 2)}"
        self.half_days += halves
        return self._add("duration", start, end, f"{halves / 2:g}j", message=message)

    def _add_duration(self, start: int, end: int, minutes: int) -> int:
        text = self._source(start, end)
        if minutes <= 0:
            return self._add(
                "duration", start, end, ambiguous=True, message=f"Durée nulle : {text}"
            )
        if self._too_long(minutes):
            return self._add(
                "duration", start, end, ambiguous=True, message=f"Durée trop longue : {text}"
            )
        rounded = _round_duration(minutes)
        message = "" if rounded == minutes else f"{text} arrondi à {format_duration(rounded)}"
        self.durations.append(rounded)
        return self._add("duration", start, end, str(rounded), message=message)

    def _too_long(self, minutes: float) -> bool:
        return self._duration_minutes() + minutes > MAX_DURATION_MINUTES

    def _scan_bare_hour(self, position: int) -> int | None:
        match = _BARE_HOUR_RE.match(self.folded, position)
        if match is None:
            return None
        value = _clock(match["h"], match["m"])
        if value is None:
            return self._invalid_time(match)
        self.bare_hours.append((len(self.tokens), value))
        return self._add("time", *match.span())

    def _scan_part(self, position: int) -> int | None:
        match = _PART_RE.match(self.folded, position)
        if match is None:
            return None
        if match["w"] == "am":
            message = "« am » est ambigu : écrire « matin » ou « aprem »"
            return self._add("part", *match.span(), ambiguous=True, message=message)
        if match["w"] == "mat" and self.material_value:
            return self._skip_word(position)
        part: DayPart = "morning" if match["w"].startswith("mat") else "afternoon"
        self.part = self.part or part
        return self._add("part", *match.span(), part)

    def _scan_night(self, position: int) -> int | None:
        match = _NIGHT_RE.match(self.folded, position)
        if match is None:
            return None
        self.night_word = True
        return self._add("night", *match.span(), "nuit")

    def _scan_day(self, position: int) -> int | None:
        match = _DAY_RE.match(self.folded, position)
        if match is None:
            return None
        word = _DayWord(
            first=match["a"],
            last=match["b"],
            next_week=match["next"] is not None,
            token_index=len(self.tokens),
        )
        self.day_words.append(word)
        return self._add("day", *match.span())

    def _scan_flag_word(self, position: int) -> int | None:
        match = _FLAG_WORD_RE.match(self.folded, position)
        if match is None:
            return None
        if match["w"] == "provisoire":
            self.provisional = True
            return self._add("provisional", *match.span())
        self.urgent = True
        return self._add("urgent", *match.span())

    def _scan_dossier(self, position: int) -> int | None:
        if self.dossier_seen or self._after_material():
            return None
        match = _DOSSIER_RE.match(self.folded, position)
        if match is None or self._unit_follows(match.end()):
            return None
        self.dossier_seen = True
        number, project = self._find_project(match["y"], match["n"], match["s"])
        self.project_number, self.project = number, project
        if project is not None:
            return self._add("project", *match.span(), number)
        message = f"{number} introuvable dans le répertoire"
        return self._add("project", *match.span(), number, ambiguous=True, message=message)

    def _scan_person(self, position: int) -> int | None:
        full = self.directory.full_name_at(self.folded, position)
        if full is not None:
            return self._add_people(position, full[0], full[1])
        match = _WORD_RE.match(self.folded, position)
        if match is None:
            return None
        word = match.group().rstrip(".:")
        end = position + len(word)
        if not word:
            return None
        original = self._source(position, end)
        ids = self.directory.people(word, original, forced=False)
        if ids:
            return self._add_people(position, end, ids)
        inactive = self.directory.inactive_people(word, original, forced=False)
        return self._add_inactive(position, end, inactive) if inactive else None

    def _add_people(self, start: int, end: int, ids: tuple[str, ...]) -> int:
        if len(ids) == 1:
            self.people.append(ids[0])
            return self._add("person", start, end, ids[0])
        names = ", ".join(self.directory.long_label(resource_id) for resource_id in ids)
        message = f"« {self._source(start, end)} » : plusieurs personnes possibles ({names})"
        return self._add("person", start, end, ",".join(ids), ambiguous=True, message=message)

    def _add_inactive(self, start: int, end: int, ids: tuple[str, ...]) -> int:
        names = ", ".join(self.directory.long_label(resource_id) for resource_id in ids)
        message = f"« {self._source(start, end)} » : personne inactive ({names})"
        return self._add("person", start, end, ",".join(ids), ambiguous=True, message=message)

    def _after_material(self) -> bool:
        word = self.previous_word
        return word in MATERIAL_PREFIXES or _BOLT_RE.fullmatch(word) is not None

    def _unit_follows(self, end: int, units: frozenset[str] = UNIT_WORDS) -> bool:
        match = _NEXT_WORD_RE.match(self.folded, end)
        return match is not None and match.group(1).rstrip(".") in units

    def _is_measure(self, end: int) -> bool:
        """Whether a ``1.5`` ending at ``end`` is a measure or a duration, not a date."""
        return self._after_material() or self._unit_follows(end, _MEASURE_WORDS)

    def _find_project(
        self, year: str | None, number: str, sub: str | None
    ) -> tuple[str, ProjectInfo | None]:
        suffix = f"-{int(sub)}" if sub else ""
        default_year = self.context.default_year
        years = (int(year),) if year else (default_year, default_year - 1)
        for candidate_year in years:
            candidate = f"{candidate_year}-{number}{suffix}"
            project = self.context.find_project(candidate)
            if project is not None:
                return candidate, project
        return f"{years[0]}-{number}{suffix}", None

    # --------------------------------------------------------------- days ----

    def _make_date(
        self,
        day_text: str,
        month_text: str,
        year_text: str | None,
        *,
        after: date | None = None,
    ) -> date | None:
        """Typed date; without year, the next one unless recent (or the range would be long)."""
        today = self.context.today
        if year_text:
            year = int(year_text)
            year += CENTURY if year < TWO_DIGIT_YEARS else 0
            if not today.year - YEARS_BACK <= year <= today.year + YEARS_AHEAD:
                return None
        else:
            year = (after or today).year
        try:
            day = date(year, int(month_text), int(day_text))
        except ValueError:
            return None
        if year_text:
            return day
        if after is not None:
            return _wrapped(day, after)
        return _next_year(day) if day < today - timedelta(days=PAST_DATE_DAYS) else day

    def _iso_monday(self, week: int, *, after: date | None = None) -> date | None:
        today = self.context.today.isocalendar()
        if after is not None:
            year = after.isocalendar().year
        else:
            year = today.year + (1 if week < today.week - PAST_WEEKS else 0)
        try:
            monday = date.fromisocalendar(year, week, 1)
            if after is not None and monday < after:
                later = date.fromisocalendar(year + 1, week, 1)
                monday = later if (later - after).days <= MAX_WRAP_DAYS else monday
        except ValueError:
            return None
        return monday

    def _resolve_day_word(self, word: str, *, reference: date | None, next_week: bool) -> date:
        today = self.context.today
        if word in _RELATIVE_DAYS:
            day = today
            for _ in range(_RELATIVE_DAYS[word]):
                day = self.calendar.next_workday(day)
            return day
        weekday = _WEEKDAY_WORDS[word]
        if next_week:
            return today + timedelta(days=(weekday - today.weekday() - 1) % DAYS_PER_WEEK + 1)
        monday = _monday_of(reference or self.context.week_start)
        day = monday + timedelta(days=weekday)
        if reference is None and monday == _monday_of(today) and day < today:
            day += timedelta(days=DAYS_PER_WEEK)
        return day

    def _day_word_span(self, word: _DayWord, reference: date | None) -> _Span:
        first = self._resolve_day_word(word.first, reference=reference, next_week=word.next_week)
        last = first
        if word.last is not None and word.last in _WEEKDAY_WORDS:
            offset = (_WEEKDAY_WORDS[word.last] - first.weekday()) % DAYS_PER_WEEK
            last = first + timedelta(days=offset)
        elif word.last is not None:
            last = max(first, self._resolve_day_word(word.last, reference=None, next_week=False))
        token = self.tokens[word.token_index]
        self.tokens[word.token_index] = replace(token, value=_span_value(first, last))
        return _Span(first, last, is_range=last > first)

    def _resolve_span(self) -> _Span | None:
        typed = self.typed_spans
        if len(typed) > 1:
            self._warn("Plusieurs dates : la première est retenue")
        span = typed[0] if typed else None
        if not self.day_words:
            return span
        if len(self.day_words) > 1:
            self._warn("Plusieurs jours : le premier est retenu")
        word = self.day_words[0]
        if span is None or span.week_reference:
            return self._day_word_span(word, span.first if span is not None else None)
        named = self._day_word_span(word, None)
        same = (
            named.first.weekday() == span.first.weekday()
            if word.first in _WEEKDAY_WORDS
            else named.first == span.first
        )
        if not same:
            text = self.tokens[word.token_index].text
            self._warn(
                f"« {text} » ne correspond pas au {self._day_label(span.first)} : "
                "la date est retenue"
            )
        return span

    def _worked_span(self, span: _Span) -> tuple[date, date]:
        calendar = self.calendar
        first, last = span.first, span.last
        if not calendar.is_workday(first):
            moved = calendar.next_workday(first)
            self._warn(
                f"{self._day_label(first)} n'est pas travaillé{self._closed_reason(first)} : "
                f"début le {self._day_label(moved)}"
            )
            first = moved
        if last > first and not calendar.is_workday(last):
            last = calendar.previous_workday(last)
        return first, max(first, last)

    def _closed_reason(self, day: date) -> str:
        holiday = self.calendar.holiday(day)
        if holiday is not None:
            return f" ({holiday.name})"
        return " (fermeture)" if self.calendar.is_closed(day) else ""

    # ------------------------------------------------------------- times ----

    def _resolve_bare_hours(self) -> None:
        """Read a bare ``7h`` as a start time on a typed day inside working hours, else hours.

        On a cell without typed day it is a duration: the cell already gives the start.
        """
        has_day = bool(self.typed_spans or self.day_words)
        opening, closing = self.calendar.day_start_minute, self.calendar.day_end_minute
        for index, minutes in self.bare_hours:
            token = self.tokens[index]
            if has_day and self.start_minute is None and opening <= minutes <= closing:
                value = snap(minutes)
                message = "" if value == minutes else f"{token.text} arrondi à {format_hhmm(value)}"
                self.start_minute = value
                self.tokens[index] = replace(token, value=format_hhmm(value), message=message)
            elif minutes <= 0 or self._too_long(minutes):
                problem = "Durée nulle" if minutes <= 0 else "Durée trop longue"
                message = f"{problem} : {token.text}"
                self.tokens[index] = replace(
                    token, kind="duration", ambiguous=True, message=message
                )
            else:
                value = _round_duration(minutes)
                message = (
                    "" if value == minutes else f"{token.text} arrondi à {format_duration(value)}"
                )
                if has_day and minutes > closing:
                    message = (
                        f"« {token.text} » compris comme une durée de {format_duration(value)}"
                    )
                self.durations.append(value)
                self.tokens[index] = replace(
                    token, kind="duration", value=str(value), message=message
                )
            if message:
                self._warn(message)

    def _periods(self, day: date) -> tuple[WorkPeriod, ...]:
        return self.calendar.periods_of_weekday(day.weekday())

    def _afternoon(self, day: date) -> WorkPeriod:
        periods = self._periods(day)
        if len(periods) > 1:
            return periods[1][0], periods[-1][1]
        self._warn(f"Pas d'après-midi travaillé le {DAY_NAMES[day.weekday()]} : matin retenu")
        return periods[0]

    def _start_minute_of(self, day: date) -> int:
        if self.start_minute is not None:
            return self.start_minute
        if self.part == "afternoon":
            return self._afternoon(day)[0]
        return self._periods(day)[0][0]

    def _end_minute_of(self, day: date) -> int:
        if self.end_minute is not None:
            return self.end_minute
        if self.part == "morning":
            return self._periods(day)[0][1]
        if self.part == "afternoon":
            return self._afternoon(day)[1]
        return self._periods(day)[-1][1]

    def _night(self, span: _Span | None) -> tuple[WallTime, WallTime] | None:
        """Exact span of night work, None when the entry is not one.

        ``nuit`` or a start typed from 18:00 makes a night (a start before 06:00 too with
        ``nuit``: still the evening's night); a range ending before it starts ends the next
        morning, ``nuit`` alone lasts from 21:00 to 05:00, and a night ends by 06:30. A
        night typed on a Friday, Saturday or Sunday evening is refused with a warning and
        the entry is read as daytime work on that day's hours.
        """
        first = self.start_minute
        evening_start = first is not None and first >= NIGHT_EARLIEST_START
        if not (self.night_word or evening_start):
            return None
        day = self._night_day(span)
        if not night_allowed(day):
            # The evening hours mean nothing by day: the day's own hours are kept.
            self._warn(NIGHT_WEEKEND_MESSAGE)
            self.start_minute = self.end_minute = None
            return None
        base = day_start(day)
        start = self._night_start(base)
        typed = start != base + DEFAULT_NIGHT_START or first == DEFAULT_NIGHT_START
        latest = night_window(day)[1]
        if self.end_minute is not None and typed:
            end = base + self.end_minute
            while end <= start:
                end += MINUTES_PER_DAY
            self._ignore_duration()
        elif self.durations or self.half_days:
            end = start + self._duration_minutes()
        else:
            end = max(base + MINUTES_PER_DAY + DEFAULT_NIGHT_END, start + MIN_NIGHT_MINUTES)
        if end > latest:
            self._warn("Une nuit finit à 06:30 au plus tard : fin à 06:30")
            end = max(latest, start + MIN_NIGHT_MINUTES)
        return start, end

    def _night_day(self, span: _Span | None) -> date:
        """Evening of a night: the typed day, else the cell's, else today."""
        context = self.context
        if span is not None:
            if span.is_range:
                self._warn(f"Une nuit à la fois : nuit du {self._day_label(span.first)} retenue")
            return span.first
        if context.cell_start is not None:
            return wall_date(context.cell_start)
        return context.today

    def _night_start(self, base: WallTime) -> WallTime:
        """Start of the night of the day at ``base``: the typed evening hour, else 21:00.

        With ``nuit``, an hour before 06:00 is after midnight, still that night.
        """
        first = self.start_minute
        if first is not None and first >= NIGHT_EARLIEST_START:
            return base + first
        if first is not None and first < NIGHT_MORNING_START and self.night_word:
            return base + MINUTES_PER_DAY + first
        if first is not None:
            self._warn(f"{format_hhmm(first)} n'est pas une heure de nuit : début à 21:00")
        return base + DEFAULT_NIGHT_START

    def _schedule(self, span: _Span | None) -> tuple[WallTime | None, WallTime | None]:
        context = self.context
        typed_time = (
            self.start_minute is not None or self.end_minute is not None or self.part is not None
        )
        if span is None and not typed_time:
            if context.cell_start is None:
                return None, None
            start = self.calendar.working_start(context.cell_start)
            if start != context.cell_start:
                reason = self._closed_reason(wall_date(context.cell_start))
                self._warn(f"Début reporté au {self._instant_label(start)}{reason}")
            return start, self._end_after(start, wall_date(start), from_cell=True)
        from_cell = span is None
        if span is None:
            base = (
                wall_date(context.cell_start) if context.cell_start is not None else context.today
            )
            span = _Span(base, base, is_range=False)
        first, last = self._worked_span(span)
        typed_start = day_start(first) + self._start_minute_of(first)
        start = self.calendar.working_start(typed_start)
        if wall_date(start) != first:
            closing = format_hhmm(self._periods(first)[-1][1])
            self._warn(
                f"Le {DAY_NAMES[first.weekday()]} finit à {closing} : "
                f"début reporté au {self._instant_label(start)}"
            )
        if span.is_range or self.end_minute is not None:
            typed_end = day_start(last) + self._end_minute_of(last)
            end = self._typed_end(typed_start, typed_end, start, single_day=not span.is_range)
            self._ignore_duration()
        else:
            end = self._end_after(start, wall_date(start), from_cell=from_cell)
        if end <= start:
            self._warn("La fin précède le début : 30 min retenues")
            end = self.calendar.add_work(start, SNAP_MINUTES)
        elif span.is_range and self.part is not None and wall_date(end) != wall_date(start):
            self._warn(
                f"Plage continue du {self._instant_label(start)} au {self._instant_label(end)}"
            )
        return start, end

    def _typed_end(
        self, typed_start: WallTime, typed_end: WallTime, start: WallTime, *, single_day: bool
    ) -> WallTime:
        """End of a typed range; hours typed outside working time keep their length."""
        if (
            single_day
            and typed_end > typed_start
            and not self.calendar.work_between(typed_start, typed_end)
        ):
            hours = (
                f"{format_hhmm(minute_of_day(typed_start))}-{format_hhmm(minute_of_day(typed_end))}"
            )
            length = typed_end - typed_start
            self._warn(
                f"{hours} hors des heures de travail : durée de {format_duration(length)} gardée"
            )
            return self.calendar.add_work(start, length)
        return self.calendar.working_end(typed_end)

    def _end_after(self, start: WallTime, day: date, *, from_cell: bool) -> WallTime:
        """End of a task without typed range: duration, part of day, cell, time or day."""
        cell_end = self.context.cell_end
        if self.half_days or self.durations:
            return self._end_of_durations(start)
        if self.part is not None:
            return self.calendar.working_end(day_start(day) + self._end_minute_of(day))
        if from_cell and cell_end is not None and self.calendar.working_end(cell_end) > start:
            return self.calendar.working_end(cell_end)
        if self.start_minute is not None:
            return self.calendar.add_work(start, SNAP_MINUTES)
        return self.calendar.working_end(day_start(day) + self._end_minute_of(day))

    def _end_of_durations(self, start: WallTime) -> WallTime:
        """Working days counted from the day of ``start``, then the worked hours after them."""
        end = start
        if self.half_days:
            end = self.calendar.add_days(start, self.half_days / 2)
        minutes = sum(self.durations)
        return self.calendar.add_work(end, minutes) if minutes else end

    def _ignore_duration(self) -> None:
        if not (self.durations or self.half_days):
            return
        self.ignored_duration = True
        message = "Durée ignorée : la plage tapée fixe la fin"
        self._warn(message)
        self.tokens = [
            replace(token, message=message) if token.kind == "duration" else token
            for token in self.tokens
        ]

    # ----------------------------------------------------------- absences ----

    def _absence(
        self, span: _Span | None, people: tuple[str, ...], typed_title: str
    ) -> AbsenceDraft:
        reason: AbsenceReason = self.absence_reason or "absent"
        first, last, half_day = self._absence_days(span)
        if self.start_minute is not None or self.end_minute is not None:
            self._warn("Les heures ne comptent pas pour une absence : seuls les jours sont retenus")
        part: DayPart = self.part or ("morning" if half_day else "day")
        if part == "afternoon" and first == last and len(self._periods(first)) <= 1:
            self._warn(f"Pas d'après-midi travaillé le {DAY_NAMES[first.weekday()]}")
        if not self.calendar.days_between(first, last):
            if first == last:
                self._warn(
                    f"{self._day_label(first)} n'est pas travaillé{self._closed_reason(first)}"
                )
            else:
                self._warn(
                    f"Aucun jour travaillé du {self._day_label(first)} au {self._day_label(last)}"
                )
        self._warn_if_past(first)
        note = "\n".join(text for text in (typed_title, *self.notes) if text)
        return AbsenceDraft(
            resource_ids=people,
            reason=reason,
            first_day=first,
            last_day=last,
            part=part,
            note=note,
        )

    def _absence_days(self, span: _Span | None) -> tuple[date, date, bool]:
        """First and last day, and whether a typed duration asks for a half day."""
        context = self.context
        if span is not None:
            first, last = span.first, span.last
        elif context.cell_start is not None:
            first = wall_date(context.cell_start)
            last = wall_date(context.cell_end - 1) if context.cell_end is not None else first
        else:
            first = last = context.today
        duration = self._duration_minutes()
        if duration and (span is None or not span.is_range):
            return self._counted_days(first, duration)
        if duration:
            self._ignore_duration()
        return first, max(first, last), False

    def _counted_days(self, first: date, minutes: int) -> tuple[date, date, bool]:
        """``5j`` of absence: five working days, whatever their hours; ``½j`` a half day."""
        if not self.calendar.is_workday(first):
            first, _ = self._worked_span(_Span(first, first, is_range=False))
        if minutes <= HALF_DAY_MINUTES:
            if minutes < HALF_DAY_MINUTES:
                self._warn("Absence comptée en demi-journée")
            return first, first, True
        count = -(-minutes // DAY_MINUTES)
        if count * DAY_MINUTES != minutes:
            self._warn(f"Absence comptée en jours entiers : {count} j")
        last = first
        for _ in range(count - 1):
            last = self.calendar.next_workday(last)
        return first, last, False

    # -------------------------------------------------------- description ----

    def _leftover(self) -> str:
        pieces: list[str] = []
        cursor = 0
        for begin, finish in sorted(self.spans):
            if begin > cursor:
                pieces.append(self.text[cursor:begin])
            cursor = max(cursor, finish)
        pieces.append(self.text[cursor:])
        return " ".join(pieces)

    def _day_label(self, day: date) -> str:
        year = "" if day.year == self.context.today.year else f".{day.year}"
        return f"{DAY_ABBREVIATIONS[day.weekday()]} {day.day:02d}.{day.month:02d}{year}"

    def _instant_label(self, value: WallTime) -> str:
        return f"{self._day_label(wall_date(value))} {format_hhmm(minute_of_day(value))}"

    def _describe(self, entry: ParsedEntry) -> str:
        if entry.absence is not None:
            return self._describe_absence(entry.absence)
        if entry.destination == "invalid":
            return "Saisie incomplète" if self.text.strip() else "Saisie vide"
        return " · ".join((*self._describe_what(entry), *self._describe_when(entry)))

    def _describe_what(self, entry: ParsedEntry) -> list[str]:
        parts: list[str] = []
        if entry.project_number:
            client = entry.project.client if entry.project is not None else ""
            parts.append(f"{entry.project_number} {client}".strip())
        if entry.title:
            parts.append(_capitalised(entry.title))
        labels = ", ".join(self.directory.label(resource_id) for resource_id in entry.resource_ids)
        if entry.start is not None:
            parts.append(labels or "Non affecté")
        elif labels:
            parts.append(labels)
        return parts

    def _describe_when(self, entry: ParsedEntry) -> list[str]:
        if entry.off_hours and entry.start is not None and entry.end is not None:
            moments = f"{self._instant_label(entry.start)} → {self._instant_label(entry.end)}"
            parts = [f"{MOON} nuit {moments}", elapsed_text(entry.end - entry.start)]
        elif entry.start is not None and entry.end is not None:
            # The length its bar will show (``format_span``), not the days as typed.
            parts = [
                f"{self._instant_label(entry.start)} → {self._instant_label(entry.end)}",
                format_span(self.calendar, entry.start, entry.end),
            ]
        else:
            parts = ["À planifier"]
            typed = self._typed_duration()
            if typed:
                parts.append(typed)
        if entry.required_people > max(1, len(entry.resource_ids)):
            parts.append(f"{entry.required_people} pers.")
        if entry.deadline is not None:
            parts.append(f"avant le {self._day_label(entry.deadline)}")
        parts.extend(
            label
            for label, flag in (("provisoire", entry.provisional), ("urgent", entry.urgent))
            if flag
        )
        return parts

    def _typed_duration(self) -> str:
        """Duration of a card as typed: ``2 j``, ``4 h 30``, ``1 j 4 h``."""
        pieces: list[str] = []
        if self.half_days:
            pieces.append(format_days(self.half_days / 2))
        minutes = sum(self.durations)
        if minutes:
            pieces.append(format_duration(minutes))
        return " ".join(pieces)

    def _describe_absence(self, absence: AbsenceDraft) -> str:
        people = ", ".join(
            self.directory.label(resource_id) for resource_id in absence.resource_ids
        )
        days = self._day_label(absence.first_day)
        if absence.last_day != absence.first_day:
            days = f"{days} → {self._day_label(absence.last_day)}"
        if absence.part != "day":
            days = f"{days} {PART_LABELS[absence.part]}"
        label = ABSENCE_LABELS.get(absence.reason, absence.reason)
        return " · ".join((label, people or "qui ?", days, *absence.note.splitlines()))
