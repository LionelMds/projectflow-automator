"""View model of the Planning grid, built once per snapshot and view without any Qt.

``build_grid_model`` turns a ``PlanningSnapshot`` into what the grid, its time header, its
row column and the PDF export draw: the time axis fitted to the viewport, the stacked
rows (« Non affecté » first, team headers, people and subcontractors), one ``BarData``
per task and row with its colours, texts and badges, the absences of each row and the
days off of each column. Bars only hold wall-clock instants; pixels come from the
``TimeScale``, so a new viewport width only needs ``GridModel.refit``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from types import MappingProxyType
from typing import Literal

from projectflow.core.planning.calendar import WorkCalendar, cantonal_holidays
from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.commands import CalendarFor, calendar_resolver
from projectflow.core.planning.conflicts import Finding, findings_by_bar, resource_load
from projectflow.core.planning.layout import (
    UNASSIGNED_LABEL,
    UNASSIGNED_ROW_ID,
    Density,
    GridRows,
    RowKind,
    RowLayout,
    TaskSpan,
    layout_rows,
    row_ids,
    row_kind,
    row_resources,
    spans_by_row,
)
from projectflow.core.planning.models import (
    ABSENCE_LABELS,
    STATUS_LABELS,
    Absence,
    DayPart,
    PlanningMeta,
    PlanningTask,
    Resource,
    TaskStatus,
)
from projectflow.core.planning.night import (
    MIN_REST_MINUTES,
    night_day,
    night_hours,
    rest_spans,
)
from projectflow.core.planning.timescale import (
    TimeScale,
    ViewKind,
    day_label,
    iso_week_label,
    month_label,
    shown_days,
    view_range,
)
from projectflow.core.planning.walltime import (
    MINUTES_PER_HOUR,
    WallTime,
    day_start,
    format_hhmm,
    minute_of_day,
    wall_date,
)
from projectflow.ui.planning.colors import BarColors, ColorBy, bar_colors

BadgeKind = Literal["night", "conflict", "people", "shared"]
BarKey = tuple[str, str]
"""``(task_id, row_id)`` of one bar; the row id of « Non affecté » is ``UNASSIGNED_ROW_ID``."""
ProjectExists = Callable[[str], bool]

DAY_VIEW_MIN_HOUR_WIDTH = 60.0
WEEK_MIN_DAY_WIDTH = 90.0
MONTH_MIN_DAY_WIDTH = 22.0
NIGHT_AXIS_MIN_HOUR_WIDTH = 40.0
"""The day view of a night draws 24 hours: narrower hours, so that it fits a screen."""
NIGHT_WINDOW = (0, 24 * MINUTES_PER_HOUR)
REST_LABEL = "Repos"
NIGHT_TOOLTIP = "Travail de nuit"
MIN_VIEWPORT_WIDTH = 200.0
EMPTY_UNASSIGNED_HEIGHT = 20
"""Height of « Non affecté » while it holds no bar in the shown range."""
FIT_MARGIN = 0.5
"""Pixels kept free so that a fitted axis never needs a horizontal scroll bar."""
NOON_MINUTE = 12 * MINUTES_PER_HOUR
"""Split between morning and afternoon on a day worked in a single period."""

TIMES = "×"  # noqa: RUF001
EN_DASH = "–"  # noqa: RUF001
ARROW = "→"
HALF = "½"
CONFLICT_GLYPH = "▲"
UNASSIGNED_SUBTITLE = "dates sans personne"
OPEN_PLACE_PREFIX = "à-pourvoir:"
"""Lane key of a task's « à pourvoir » place in « Non affecté » (never a task id)."""
OPEN_PLACE_TEXT = "à pourvoir"
YEAR_DIGITS = 4
MIN_NUMBER_PARTS = 2
CENTURY = 100

WEEKDAY_SHORT = ("lun", "mar", "mer", "jeu", "ven", "sam", "dim")
_PEOPLE_FINDING = "missing_people"
RECURRING_PREFIX = "recurring:"
"""Lead of the ``absence_id`` of a recurring day off, never a document id."""
_RECURRING_PARTS = 4


def recurring_block_id(resource_id: str, weekday: int, part: str) -> str:
    """``recurring:2:day:<resource id>``: a recurring day off of one person."""
    return f"{RECURRING_PREFIX}{weekday}:{part}:{resource_id}"


def recurring_resource_id(block_id: str) -> str | None:
    """Person of a recurring day off's id, None for any other id (a real absence)."""
    if not block_id.startswith(RECURRING_PREFIX):
        return None
    parts = block_id.split(":", _RECURRING_PARTS - 1)
    return parts[-1] if len(parts) == _RECURRING_PARTS and parts[-1] else None


def column_tinted(view: str, day: date) -> bool:
    """Whether the column of ``day`` takes the alternate (« bi-ton ») background.

    Day and week views alternate days from the Monday of each week (Monday plain,
    Tuesday tinted…); the month view, whose days are narrow, alternates ISO weeks.
    """
    if view == "month":
        return day.isocalendar()[1] % 2 == 1
    return day.weekday() % 2 == 1


# ------------------------------------------------------------------ data ----


@dataclass(frozen=True, slots=True)
class Badge:
    """A small tag at the top right of a bar: ``▲`` conflict, ``n/m`` people, ``TIMES N`` shared."""

    kind: BadgeKind
    text: str
    tooltip: str


@dataclass(frozen=True, slots=True)
class BarData:
    """One task drawn on one row."""

    task_id: str
    row_id: str
    start: WallTime
    end: WallTime
    lane: int
    colors: BarColors
    number: str
    """Short dossier number: ``118`` this year, ``25-4987`` another year, ``118-1``."""
    project_number: str
    client: str
    title: str
    type_label: str
    duration: str
    """« 2 j », « 1 j ½ », « ½ j » for whole half days, else worked hours « 13 h 30 »."""
    time_range: str
    """Hours of a bar within one day (07:00 to 11:30), else empty."""
    status: TaskStatus
    badges: tuple[Badge, ...] = ()
    shared_with: tuple[str, ...] = ()
    """Other rows showing the same task."""
    people: tuple[str, ...] = ()
    """Initials (or names) of every assignee, in order."""
    findings: tuple[str, ...] = ()
    """Messages of every finding of the bar, missing people included."""
    urgent: bool = False
    cut_left: bool = False
    cut_right: bool = False
    night: bool = False
    """Night work: drawn over its evening's whole column unless the axis shows the night."""

    @property
    def key(self) -> BarKey:
        return (self.task_id, self.row_id)

    @property
    def provisional(self) -> bool:
        return self.status == "provisional"

    @property
    def done(self) -> bool:
        return self.status == "done"

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def has_conflict(self) -> bool:
        return any(badge.kind == "conflict" for badge in self.badges)


@dataclass(frozen=True, slots=True)
class OpenPlace:
    """Places still to fill on a task that needs more people, shown in « Non affecté ».

    Drawn dashed as « 5212 · à pourvoir (1) » for the task's dates; not a bar: nothing
    is selected, moved or deleted through it.
    """

    task_id: str
    start: WallTime
    end: WallTime
    lane: int
    number: str
    title: str
    missing: int

    @property
    def text(self) -> str:
        return f"{self.number or self.title} · {OPEN_PLACE_TEXT} ({self.missing})"


@dataclass(frozen=True, slots=True)
class RowLabel:
    """What the frozen row column shows for one row."""

    row_id: str
    kind: RowKind
    title: str
    subtitle: str = ""
    initials: str = ""
    team_key: str = ""
    subcontractor: bool = False
    apprentice: bool = False
    archived: bool = False
    collapsed: bool = False
    count: int = 0
    """Team header: its people; « Non affecté »: its bars in the visible range."""
    planned_minutes: int = 0
    available_minutes: int = 0
    canton: str = ""

    @property
    def load(self) -> float | None:
        """Planned over available worked time in the visible range; None without any.

        A subcontractor takes several jobs at once: it has no load.
        """
        if self.kind != "resource" or self.subcontractor or self.available_minutes <= 0:
            return None
        return self.planned_minutes / self.available_minutes

    @property
    def absent_all_range(self) -> bool:
        return self.kind == "resource" and not self.subcontractor and self.available_minutes <= 0


@dataclass(frozen=True, slots=True)
class AbsenceBlock:
    """A hatched span of a row: an absence, or a recurring day off such as school."""

    row_id: str
    start: WallTime
    end: WallTime
    label: str
    reason: str
    recurring: bool = False
    note: str = ""
    absence_id: str = ""
    """Document id of the absence, or ``recurring_block_id`` for a recurring day off."""


@dataclass(frozen=True, slots=True)
class RestBlock:
    """Rest kept by one person around a night: 8 h before its start, or after its end."""

    row_id: str
    task_id: str
    start: WallTime
    end: WallTime
    before: bool
    label: str = REST_LABEL


@dataclass(frozen=True, slots=True)
class DayInfo:
    """Days off of one shown column, for one calendar (the axis's or a row's canton).

    ``off`` holds the minutes of the day drawn but not worked (lunch, Friday afternoon,
    or the whole drawn day of a holiday or a closure), as ``(start, end)`` minutes.
    """

    day: date
    index: int
    workday: bool
    holiday: str | None
    closed: bool
    closure: str
    today: bool
    off: tuple[tuple[int, int], ...]
    short: bool
    """A working day ending before the others (Friday at noon)."""


@dataclass(frozen=True, slots=True)
class DayName:
    """A public holiday named in the time header, with the cantons it applies to."""

    name: str
    cantons: tuple[str, ...]
    everywhere: bool
    """True when it applies to every canton shown in the grid."""

    @property
    def label(self) -> str:
        if self.everywhere:
            return self.name
        return f"{self.name} ({', '.join(self.cantons)})"


@dataclass(frozen=True, slots=True)
class GridModel:
    """Everything the Planning grid draws for one view, built by ``build_grid_model``."""

    snapshot: PlanningSnapshot
    view: ViewKind
    anchor: date
    first_day: date
    last_day: date
    timescale: TimeScale
    rows: GridRows
    labels: Mapping[str, RowLabel]
    bars: tuple[BarData, ...]
    absences: tuple[AbsenceBlock, ...]
    days: tuple[DayInfo, ...]
    holidays: Mapping[date, DayName]
    closures: Mapping[date, str]
    today: date
    now: WallTime
    density: Density
    color_by: ColorBy
    calendar_for: CalendarFor
    viewport_width: float
    bars_by_row: Mapping[str, tuple[BarData, ...]]
    bars_by_task: Mapping[str, tuple[BarData, ...]]
    bars_by_key: Mapping[BarKey, BarData]
    absences_by_row: Mapping[str, tuple[AbsenceBlock, ...]]
    day_cache: dict[WorkCalendar, tuple[DayInfo, ...]] = field(
        default_factory=dict, compare=False, repr=False
    )
    open_places: tuple[OpenPlace, ...] = ()
    """Tasks short of people: their places « à pourvoir » in the « Non affecté » row."""
    rests: tuple[RestBlock, ...] = ()
    """Rest of 8 h around the nights, on the row of each assignee."""
    rests_by_row: Mapping[str, tuple[RestBlock, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )

    @property
    def night_axis(self) -> bool:
        """Whether the axis draws the whole 24 hours: night bars sit at their real place."""
        return self.timescale.full_day

    def display_span(
        self, start: WallTime, end: WallTime, *, night: bool = False
    ) -> tuple[WallTime, WallTime]:
        """Instants a bar is drawn between: a night fills its evening's column.

        The 06:30-16:30 axis has no room for a night: it takes the whole column of the
        day it belongs to (``night.night_day``), its hours written on the bar.
        """
        if not night or self.night_axis:
            return start, end
        return column_span(self.timescale, night_day(start))

    def resting_during(
        self, row_id: str, start: WallTime, end: WallTime, *, task_id: str = ""
    ) -> RestBlock | None:
        """First rest of ``row_id`` (of another task than ``task_id``) overlapping a span."""
        return next(
            (
                block
                for block in self.rests_by_row.get(row_id, ())
                if block.task_id != task_id and block.start < end and block.end > start
            ),
            None,
        )

    # Lookups ----------------------------------------------------------

    @property
    def start(self) -> WallTime:
        return self.timescale.start

    @property
    def end(self) -> WallTime:
        return self.timescale.end

    @property
    def meta(self) -> PlanningMeta:
        return self.snapshot.meta

    @property
    def default_canton(self) -> str:
        """Canton of the time axis, the shared default one."""
        cantons = self.timescale.calendar.cantons
        return cantons[0] if cantons else ""

    def calendar_of(self, row_id: str) -> WorkCalendar:
        """Calendar of a row: its person's canton, the default one for other rows."""
        return self.calendar_for(row_id if row_kind(row_id) == "resource" else None)

    def task_calendar(self, task_id: str) -> WorkCalendar:
        """Calendar a task's length is measured with: its first assignee's, as in the commands."""
        task = self.snapshot.tasks.get(task_id)
        return self.calendar_for(next(iter(task.assignments), None) if task is not None else None)

    def days_of(self, row_id: str) -> tuple[DayInfo, ...]:
        """Days off of every column for the calendar of ``row_id``."""
        calendar = self.calendar_of(row_id)
        if calendar is self.timescale.calendar:
            return self.days
        cached = self.day_cache.get(calendar)
        if cached is None:
            cached = day_infos(self.timescale, calendar, today=self.today, closures=self.closures)
            self.day_cache[calendar] = cached
        return cached

    def task(self, task_id: str) -> PlanningTask | None:
        task = self.snapshot.tasks.get(task_id)
        return task if task is not None and not task.deleted else None

    def label(self, row_id: str) -> RowLabel | None:
        return self.labels.get(row_id)

    def bar(self, task_id: str, row_id: str) -> BarData | None:
        return self.bars_by_key.get((task_id, row_id))

    def preview_bar(
        self, task_id: str, row_id: str, start: WallTime, end: WallTime
    ) -> BarData | None:
        """Bar of ``task_id`` as it would look at ``start``..``end`` on ``row_id`` (no badges)."""
        task = self.task(task_id)
        if task is None or end <= start:
            return None
        context = _BarContext(
            meta=self.meta,
            color_by=self.color_by,
            calendar_for=self.calendar_for,
            scale=self.timescale,
            findings={},
            resources=self.snapshot.resources,
            laid_out=frozenset(),
            year=self.today.year,
        )
        moved = task.model_copy(update={"start": start, "end": end})
        return replace(_bar_data(moved, row_id, 0, context), badges=())

    def editable_row_ids(self) -> tuple[str, ...]:
        """Rows that hold bars, top to bottom (team headers left out)."""
        return tuple(row.resource_id for row in self.rows if row.kind != "team")

    def absent_during(self, row_id: str, start: WallTime, end: WallTime) -> AbsenceBlock | None:
        """First absence of ``row_id`` overlapping ``[start, end)``."""
        return next(
            (
                block
                for block in self.absences_by_row.get(row_id, ())
                if block.start < end and block.end > start
            ),
            None,
        )

    def absence_block(self, absence_id: str) -> AbsenceBlock | None:
        """First drawn block of ``absence_id`` (a document id or a recurring day off)."""
        return next((block for block in self.absences if block.absence_id == absence_id), None)

    @property
    def tinted_columns(self) -> tuple[int, ...]:
        """Indexes of the columns drawn on the alternate background (see ``column_tinted``)."""
        columns = self.timescale.columns
        return tuple(column.index for column in columns if column_tinted(self.view, column.day))

    @property
    def today_index(self) -> int | None:
        return self.timescale.index_of(self.today)

    @property
    def now_visible(self) -> bool:
        if self.timescale.index_of(wall_date(self.now)) is None:
            return False
        minute = minute_of_day(self.now)
        low, high = self.timescale.window
        return low <= minute <= high

    @property
    def period_label(self) -> str:
        return period_label(self.view, self.timescale.first_day, self.timescale.last_day)

    # Geometry ---------------------------------------------------------

    def refit(self, viewport_width: float) -> GridModel:
        """Return the same model with the axis fitted to a new viewport width."""
        width = fitted_day_width(
            self.view,
            viewport_width,
            len(self.timescale.columns),
            self.timescale.display_minutes,
            night=self.night_axis,
        )
        return replace(
            self.with_day_width(width), viewport_width=max(viewport_width, MIN_VIEWPORT_WIDTH)
        )

    def with_day_width(self, day_width: float) -> GridModel:
        if day_width == self.timescale.day_width:
            return self
        scale = TimeScale(
            self.timescale.calendar,
            self.first_day,
            self.last_day,
            day_width,
            window=self.timescale.window,
        )
        return replace(self, timescale=scale)


# --------------------------------------------------------------- helpers ----


def fitted_day_width(
    view: ViewKind,
    viewport_width: float,
    column_count: int,
    display_minutes: int,
    *,
    night: bool = False,
) -> float:
    """Return the width of a day for ``view`` in a viewport of ``viewport_width``.

    The day view shows the whole drawn day (60 px per hour at least, 40 px for the 24
    hours of a night), the week and month views fit their shown days in the viewport.
    """
    width = max(viewport_width, MIN_VIEWPORT_WIDTH) - FIT_MARGIN
    if view == "day":
        hour = NIGHT_AXIS_MIN_HOUR_WIDTH if night else DAY_VIEW_MIN_HOUR_WIDTH
        return max(width, hour * display_minutes / MINUTES_PER_HOUR)
    minimum = WEEK_MIN_DAY_WIDTH if view == "week" else MONTH_MIN_DAY_WIDTH
    return max(width / max(column_count, 1), minimum)


def short_number(number: str, current_year: int) -> str:
    """Dossier number as the workshop says it.

    ``2026-118`` -> ``118`` this year, ``2025-4987`` -> ``25-4987``, ``2026-118-1`` -> ``118-1``.
    """
    parts = [part for part in number.strip().split("-") if part]
    if len(parts) < MIN_NUMBER_PARTS or len(parts[0]) != YEAR_DIGITS or not parts[0].isdigit():
        return number.strip()
    rest = "-".join(parts[1:])
    if int(parts[0]) == current_year:
        return rest
    return f"{int(parts[0]) % CENTURY:02d}-{rest}"


def format_days(days: float) -> str:
    """``2.0`` -> ``2 j``, ``0.5`` -> ``½ j``, ``1.5`` -> ``1 j ½``."""
    whole, half = divmod(round(days * 2), 2)
    if not whole:
        return f"{HALF} j"
    return f"{whole} j {HALF}" if half else f"{whole} j"


def format_hours(minutes: int) -> str:
    """Worked minutes: ``810`` -> ``13 h 30``, ``30`` -> ``30 min``."""
    hours, rest = divmod(max(minutes, 0), MINUTES_PER_HOUR)
    if not hours:
        return f"{rest} min"
    return f"{hours} h {rest:02d}" if rest else f"{hours} h"


def duration_label(calendar: WorkCalendar, start: WallTime, end: WallTime) -> str:
    """Working days when the span is whole half days, else worked hours."""
    days = calendar.whole_days(start, end)
    if days is not None:
        return format_days(days)
    return format_hours(calendar.work_between(start, end))


def moment_label(value: WallTime) -> str:
    """``lun 26.10 06:30``."""
    day = wall_date(value)
    return (
        f"{WEEKDAY_SHORT[day.weekday()]} {day.day:02d}.{day.month:02d} "
        f"{format_hhmm(minute_of_day(value))}"
    )


def span_label(calendar: WorkCalendar, start: WallTime, end: WallTime) -> str:
    """``lun 26.10 06:30 → mar 27.10 12:00 · 1 j ½``."""
    duration = duration_label(calendar, start, end)
    return f"{moment_label(start)} {ARROW} {moment_label(end)} · {duration}"


def period_label(view: ViewKind, first: date, last: date) -> str:
    """Name the shown period: week number and dates, month, or day."""
    if view == "day":
        return f"{day_label(first)}.{first.year}"
    if view == "month":
        return month_label(first)
    return (
        f"{iso_week_label(first)} · {first.day:02d}.{first.month:02d} {EN_DASH} "
        f"{last.day:02d}.{last.month:02d}.{last.year}"
    )


def time_range_label(start: WallTime, end: WallTime) -> str:
    if wall_date(start) != wall_date(end - 1):
        return ""
    return f"{format_hhmm(minute_of_day(start))}{EN_DASH}{format_hhmm(minute_of_day(end))}"


def _gaps(periods: Sequence[tuple[int, int]], low: int, high: int) -> tuple[tuple[int, int], ...]:
    """Hatched parts of a day: before its first period and after its last one.

    The lunch break between two periods stays plain, the user found it too busy.
    """
    gaps: list[tuple[int, int]] = []
    cursor = low
    for index, (start, end) in enumerate(periods):
        if start > cursor and index == 0:
            gaps.append((cursor, min(start, high)))
        cursor = max(cursor, end)
    if cursor < high:
        gaps.append((cursor, high))
    return tuple(gap for gap in gaps if gap[0] < gap[1])


def day_infos(
    scale: TimeScale, calendar: WorkCalendar, *, today: date, closures: Mapping[date, str]
) -> tuple[DayInfo, ...]:
    """Days off of every column of ``scale`` for ``calendar``."""
    low, high = scale.window
    infos: list[DayInfo] = []
    for column in scale.columns_for(calendar):
        base = day_start(column.day)
        periods = tuple((start - base, end - base) for start, end in column.periods)
        infos.append(
            DayInfo(
                day=column.day,
                index=column.index,
                workday=column.workday,
                holiday=column.holiday,
                closed=column.closed,
                closure=closures.get(column.day, "") if column.closed else "",
                today=column.day == today,
                off=_gaps(periods, low, high),
                short=bool(periods) and periods[-1][1] < high,
            )
        )
    return tuple(infos)


def _closure_labels(meta: PlanningMeta) -> dict[date, str]:
    labels: dict[date, str] = {}
    for closure in meta.closures:
        day = closure.first_day
        while day <= closure.last_day:
            labels[day] = closure.label
            day += timedelta(days=1)
    return labels


def _holiday_names(scale: TimeScale, cantons: Collection[str]) -> dict[date, DayName]:
    wanted = tuple(sorted(set(cantons)))
    names: dict[date, DayName] = {}
    for year in range(scale.first_day.year, scale.last_day.year + 1):
        for day, holiday in cantonal_holidays(year, wanted).items():
            if scale.index_of(day) is not None:
                names[day] = DayName(
                    name=holiday.name,
                    cantons=holiday.cantons,
                    everywhere=set(holiday.cantons) == set(wanted),
                )
    return names


def _visible_row_ids(resources: Sequence[Resource], collapsed: Collection[str]) -> list[str]:
    """Row ids of ``row_ids`` without the people of collapsed teams (their header stays)."""
    team_of = {resource.id: resource.team for resource in resources}
    return [
        row_id
        for row_id in row_ids(resources)
        if row_kind(row_id) != "resource" or team_of[row_id] not in collapsed
    ]


def _people(task: PlanningTask, resources: Mapping[str, Resource]) -> tuple[str, ...]:
    return tuple(
        resources[rid].short_label if rid in resources else "?" for rid in task.assignee_ids
    )


def _badges(
    task: PlanningTask, findings: Sequence[Finding], people: Sequence[str]
) -> tuple[Badge, ...]:
    badges: list[Badge] = []
    if task.off_hours:
        badges.append(Badge(kind="night", text="", tooltip=NIGHT_TOOLTIP))
    problems = [finding.message for finding in findings if finding.kind != _PEOPLE_FINDING]
    if problems:
        badges.append(Badge(kind="conflict", text=CONFLICT_GLYPH, tooltip="\n".join(problems)))
    assigned = len(task.assignments)
    # A plain task of « Non affecté » obviously misses its one person: no « 0/1 » tag.
    if assigned < task.required_people and (assigned or task.required_people > 1):
        plural = "s" if task.required_people > 1 else ""
        badges.append(
            Badge(
                kind="people",
                text=f"{assigned}/{task.required_people}",
                tooltip=f"{assigned} personne{'s' if assigned > 1 else ''} affectée"
                f"{'s' if assigned > 1 else ''} sur {task.required_people} requise{plural}",
            )
        )
    if assigned > 1 and not any(badge.kind == "people" for badge in badges):
        badges.append(
            Badge(
                kind="shared",
                text=f"{TIMES}{assigned}",
                tooltip=f"Tâche partagée : {', '.join(people)}",
            )
        )
    return tuple(badges)


@dataclass(frozen=True, slots=True)
class _BarContext:
    meta: PlanningMeta
    color_by: ColorBy
    calendar_for: CalendarFor
    scale: TimeScale
    findings: Mapping[tuple[str, str | None], list[Finding]]
    resources: Mapping[str, Resource]
    laid_out: frozenset[str]
    year: int


def _bar_data(task: PlanningTask, row_id: str, lane: int, context: _BarContext) -> BarData:
    start = task.start if task.start is not None else context.scale.start
    end = task.end if task.end is not None else start
    unassigned = row_id == UNASSIGNED_ROW_ID
    findings = context.findings.get((task.id, None if unassigned else row_id), [])
    people = _people(task, context.resources)
    calendar = context.calendar_for(next(iter(task.assignments), None))
    cache = task.project_cache
    night = task.off_hours
    display = (start, end)
    if night and not context.scale.full_day:
        display = column_span(context.scale, night_day(start))
    return BarData(
        task_id=task.id,
        row_id=row_id,
        start=start,
        end=end,
        lane=lane,
        colors=bar_colors(task, context.meta, context.color_by),
        number=short_number(task.project_number, context.year),
        project_number=task.project_number,
        client=cache.client,
        title=task.title or cache.designation,
        type_label=context.meta.task_type(task.task_type).label,
        duration=night_hours(start, end) if night else duration_label(calendar, start, end),
        time_range=night_hours(start, end) if night else time_range_label(start, end),
        status=task.status,
        badges=_badges(task, findings, people),
        shared_with=tuple(
            rid for rid in task.assignee_ids if rid != row_id and rid in context.laid_out
        ),
        people=people,
        findings=tuple(finding.message for finding in findings),
        urgent=task.urgent,
        cut_left=display[0] < context.scale.start,
        cut_right=display[1] > context.scale.end,
        night=night,
    )


def column_span(scale: TimeScale, day: date) -> tuple[WallTime, WallTime]:
    """Drawn instants of the column of ``day``: the whole drawn day."""
    low, high = scale.window
    base = day_start(day)
    return base + low, base + high


def _part_span(
    day: date, part: DayPart, window: tuple[int, int], calendar: WorkCalendar
) -> tuple[WallTime, WallTime]:
    base = day_start(day)
    low, high = window
    if part == "day":
        return base + low, base + high
    periods = calendar.periods_of_weekday(day.weekday())
    split = periods[0][1] if len(periods) > 1 else NOON_MINUTE
    return (base + low, base + split) if part == "morning" else (base + split, base + high)


def _absence_blocks(
    absence: Absence, scale: TimeScale, calendar: WorkCalendar
) -> list[AbsenceBlock]:
    first = max(absence.first_day, scale.first_day)
    last = min(absence.last_day, scale.last_day)
    if first > last:
        return []
    axis = scale.calendar
    if absence.part == "day":
        spans = [
            (
                _part_span(first, "day", scale.window, calendar)[0],
                _part_span(last, "day", scale.window, calendar)[1],
            )
        ]
    else:
        spans = [
            _part_span(day, absence.part, scale.window, calendar)
            for day in shown_days(axis, first, last)
        ]
    return [
        AbsenceBlock(
            row_id=absence.resource_id,
            start=start,
            end=end,
            label=absence.label,
            reason=absence.reason,
            note=absence.note,
            absence_id=absence.id,
        )
        for start, end in spans
        if start < end
    ]


def _recurring_blocks(
    resource: Resource, scale: TimeScale, calendar: WorkCalendar
) -> list[AbsenceBlock]:
    blocks: list[AbsenceBlock] = []
    for off in resource.recurring_off:
        for column in scale.columns:
            if column.day.weekday() != off.weekday:
                continue
            start, end = _part_span(column.day, off.part, scale.window, calendar)
            blocks.append(
                AbsenceBlock(
                    row_id=resource.id,
                    start=start,
                    end=end,
                    label=ABSENCE_LABELS.get(off.reason, off.reason),
                    reason=off.reason,
                    recurring=True,
                    absence_id=recurring_block_id(resource.id, off.weekday, off.part),
                )
            )
    return blocks


def _absences(
    snapshot: PlanningSnapshot,
    scale: TimeScale,
    shown: Mapping[str, Resource],
    calendar_for: CalendarFor,
) -> list[AbsenceBlock]:
    blocks: list[AbsenceBlock] = []
    for absence in snapshot.live_absences():
        if absence.resource_id in shown:
            blocks.extend(_absence_blocks(absence, scale, calendar_for(absence.resource_id)))
    for resource in shown.values():
        if resource.recurring_off:
            blocks.extend(_recurring_blocks(resource, scale, calendar_for(resource.id)))
    return sorted(blocks, key=lambda block: (block.row_id, block.start, block.end))


def _resource_label(
    resource: Resource,
    snapshot: PlanningSnapshot,
    scale: TimeScale,
    calendar: WorkCalendar,
) -> RowLabel:
    # ``snapshot`` only holds the tasks of this row in the range: the load stays cheap.
    planned, available = resource_load(
        snapshot, resource.id, scale.first_day, scale.last_day, calendar
    )
    return RowLabel(
        row_id=resource.id,
        kind="resource",
        title=resource.name,
        subtitle=resource.role,
        initials=resource.initials or resource.name[:3].upper(),
        team_key=resource.team,
        subcontractor=resource.is_subcontractor,
        apprentice=resource.apprentice,
        archived=not resource.active,
        planned_minutes=planned,
        available_minutes=available,
        canton=calendar.cantons[0] if calendar.cantons else "",
    )


def _labels(
    snapshot: PlanningSnapshot,
    *,
    ids: Sequence[str],
    resources: Sequence[Resource],
    collapsed: Collection[str],
    scale: TimeScale,
    calendar_for: CalendarFor,
    unassigned_count: int,
    tasks_by_row: Mapping[str, Sequence[PlanningTask]],
) -> dict[str, RowLabel]:
    by_id = {resource.id: resource for resource in resources}
    members: dict[str, int] = defaultdict(int)
    for resource in resources:
        members[resource.team] += 1
    labels: dict[str, RowLabel] = {}
    for row_id in ids:
        kind = row_kind(row_id)
        if kind == "unassigned":
            labels[row_id] = RowLabel(
                row_id=row_id,
                kind=kind,
                title=UNASSIGNED_LABEL,
                subtitle=UNASSIGNED_SUBTITLE,
                count=unassigned_count,
            )
        elif kind == "team":
            key = row_id.split(":", 1)[1]
            team = snapshot.meta.team(key)
            labels[row_id] = RowLabel(
                row_id=row_id,
                kind=kind,
                title=team.name if team is not None else key,
                team_key=key,
                collapsed=key in collapsed,
                count=members[key],
            )
        else:
            own = replace(snapshot, tasks={task.id: task for task in tasks_by_row.get(row_id, ())})
            labels[row_id] = _resource_label(by_id[row_id], own, scale, calendar_for(row_id))
    return labels


def _overlapping(
    tasks: Iterable[PlanningTask], low: WallTime, high: WallTime
) -> list[PlanningTask]:
    return [
        task
        for task in tasks
        if task.start is not None and task.end is not None and task.start < high and task.end > low
    ]


def _nearby(snapshot: PlanningSnapshot, tasks: Sequence[PlanningTask]) -> PlanningSnapshot:
    """Snapshot reduced to the tasks a finding of ``tasks`` can depend on.

    A finding concerns a whole bar, and an overload only counts the days along the bars:
    the tasks overlapping the full span of ``tasks`` are all it needs.
    """
    spans = [
        (task.start, task.end) for task in tasks if task.start is not None and task.end is not None
    ]
    if not spans:
        return replace(snapshot, tasks={})
    # The rest around a night reaches 8 h past its bar.
    low = min(start for start, _end in spans) - MIN_REST_MINUTES
    high = max(end for _start, end in spans) + MIN_REST_MINUTES
    return replace(
        snapshot, tasks={task.id: task for task in _overlapping(snapshot.live_tasks(), low, high)}
    )


def _by_assignee(tasks: Iterable[PlanningTask]) -> dict[str, list[PlanningTask]]:
    grouped: dict[str, list[PlanningTask]] = defaultdict(list)
    for task in tasks:
        for resource_id in task.assignments:
            grouped[resource_id].append(task)
    return grouped


def _group(
    bars: Iterable[BarData], key: Callable[[BarData], str]
) -> dict[str, tuple[BarData, ...]]:
    grouped: dict[str, list[BarData]] = defaultdict(list)
    for bar in bars:
        grouped[key(bar)].append(bar)
    return {name: tuple(items) for name, items in grouped.items()}


# ----------------------------------------------------------------- build ----


def build_grid_model(
    snapshot: PlanningSnapshot,
    *,
    view: ViewKind,
    anchor: date,
    viewport_width: float,
    today: date,
    now: WallTime,
    density: Density = Density.NORMAL,
    color_by: ColorBy = "type",
    hidden_resource_ids: Collection[str] = (),
    collapsed_teams: Collection[str] = (),
    project_exists: ProjectExists | None = None,
    show_night: bool = False,
) -> GridModel:
    """Build what the grid draws for ``view`` around ``anchor``.

    Hidden people get no row and their bars are not drawn (they never fall onto « Non
    affecté »); a collapsed team keeps its header. Archived people come back while they
    hold a task in the range. The day view draws the 24 hours when the day holds night
    work (or with ``show_night``); elsewhere a night fills the column of its evening.
    """
    calendar_for = calendar_resolver(snapshot)
    axis = calendar_for(None)
    first, last = view_range(anchor, view, axis.workdays)
    columns = len(shown_days(axis, first, last))
    night_axis = view == "day" and (show_night or _has_night(snapshot, first))
    day_window = NIGHT_WINDOW if night_axis else None
    probe = TimeScale(axis, first, last, 1.0, window=day_window)
    width = fitted_day_width(view, viewport_width, columns, probe.display_minutes, night=night_axis)
    scale = TimeScale(axis, first, last, width, window=day_window)
    window = (scale.start, scale.end)
    resources = row_resources(snapshot, window=window)
    hidden = set(hidden_resource_ids)
    visible = [resource for resource in resources if resource.id not in hidden]
    collapsed = set(collapsed_teams)
    tasks = _overlapping(snapshot.live_tasks(), *window)
    spans: dict[str, list[TaskSpan]] = spans_by_row(
        tasks, shown={resource.id for resource in resources}
    )
    places = _open_place_spans(tasks, spans)
    if places:
        # After the dated tasks without anybody: they keep their lanes.
        spans.setdefault(UNASSIGNED_ROW_ID, []).extend(places)
    if not night_axis:
        spans, places = _night_columns(spans, places, snapshot, scale)
    ids = _visible_row_ids(visible, collapsed)
    rows = _shrink_empty_unassigned(layout_rows(ids, spans, density=density, window=window), spans)
    laid_out = frozenset(row.resource_id for row in rows if row.kind == "resource")
    context = _BarContext(
        meta=snapshot.meta,
        color_by=color_by,
        calendar_for=calendar_for,
        scale=scale,
        findings=findings_by_bar(
            _nearby(snapshot, tasks), first, last, calendar_for, project_exists
        ),
        resources=snapshot.resources,
        laid_out=laid_out,
        year=today.year,
    )
    bars = tuple(
        _bar_data(
            snapshot.tasks[span.task_id], row.resource_id, row.lanes.get(span.task_id, 0), context
        )
        for row in rows
        if row.kind != "team"
        for span in spans.get(row.resource_id, ())
        if not span.task_id.startswith(OPEN_PLACE_PREFIX)
    )
    unassigned_row = rows.get(UNASSIGNED_ROW_ID)
    open_places = (
        tuple(_open_place(span, unassigned_row, snapshot, today.year) for span in places)
        if unassigned_row is not None
        else ()
    )
    shown = {resource.id: resource for resource in visible if resource.id in laid_out}
    absences = tuple(_absences(snapshot, scale, shown, calendar_for))
    rests = tuple(_rests(snapshot, scale, shown))
    closures = _closure_labels(snapshot.meta)
    cantons = {canton for resource in visible for canton in calendar_for(resource.id).cantons}
    cantons.update(axis.cantons)
    by_row = _group(bars, lambda bar: bar.row_id)
    return GridModel(
        snapshot=snapshot,
        view=view,
        anchor=anchor,
        first_day=first,
        last_day=last,
        timescale=scale,
        rows=rows,
        labels=MappingProxyType(
            _labels(
                snapshot,
                ids=ids,
                resources=visible,
                collapsed=collapsed,
                scale=scale,
                calendar_for=calendar_for,
                unassigned_count=len(by_row.get(UNASSIGNED_ROW_ID, ())) + len(open_places),
                tasks_by_row=_by_assignee(tasks),
            )
        ),
        bars=bars,
        absences=absences,
        days=day_infos(scale, axis, today=today, closures=closures),
        holidays=MappingProxyType(_holiday_names(scale, cantons)),
        closures=MappingProxyType(closures),
        today=today,
        now=now,
        density=density,
        color_by=color_by,
        calendar_for=calendar_for,
        viewport_width=max(viewport_width, MIN_VIEWPORT_WIDTH),
        bars_by_row=MappingProxyType(by_row),
        bars_by_task=MappingProxyType(_group(bars, lambda bar: bar.task_id)),
        bars_by_key=MappingProxyType({bar.key: bar for bar in bars}),
        absences_by_row=MappingProxyType(_group_absences(absences)),
        open_places=open_places,
        rests=rests,
        rests_by_row=MappingProxyType(_group_rests(rests)),
    )


def _night_tasks(snapshot: PlanningSnapshot, low: WallTime, high: WallTime) -> list[PlanningTask]:
    return [task for task in _overlapping(snapshot.live_tasks(), low, high) if task.off_hours]


def _has_night(snapshot: PlanningSnapshot, day: date) -> bool:
    """Whether night work overlaps ``day`` (its evening, or the morning after a night)."""
    return bool(_night_tasks(snapshot, day_start(day), day_start(day + timedelta(days=1))))


def _night_columns(
    spans: Mapping[str, Sequence[TaskSpan]],
    places: Sequence[TaskSpan],
    snapshot: PlanningSnapshot,
    scale: TimeScale,
) -> tuple[dict[str, list[TaskSpan]], list[TaskSpan]]:
    """Spans with every night over its evening's column, so that it takes a lane there."""

    def placed(span: TaskSpan) -> TaskSpan:
        task = snapshot.tasks.get(span.task_id.removeprefix(OPEN_PLACE_PREFIX))
        if task is None or not task.off_hours:
            return span
        start, end = column_span(scale, night_day(span.start))
        return TaskSpan(task_id=span.task_id, start=start, end=end)

    moved = {row_id: [placed(span) for span in items] for row_id, items in spans.items()}
    return moved, [placed(span) for span in places]


def _rests(
    snapshot: PlanningSnapshot, scale: TimeScale, shown: Mapping[str, Resource]
) -> list[RestBlock]:
    """Rest blocks of the nights near the shown range, on the rows of their people.

    Subcontractors keep no rest. Blocks outside the drawn range are left out.
    """
    low, high = scale.start, scale.end
    blocks: list[RestBlock] = []
    for task in _night_tasks(snapshot, low - MIN_REST_MINUTES, high + MIN_REST_MINUTES):
        if task.start is None or task.end is None:
            continue
        for resource_id in task.assignments:
            resource = shown.get(resource_id)
            if resource is None or resource.is_subcontractor:
                continue
            for index, (start, end) in enumerate(rest_spans(task.start, task.end)):
                if start < high and end > low:
                    blocks.append(
                        RestBlock(
                            row_id=resource_id,
                            task_id=task.id,
                            start=start,
                            end=end,
                            before=index == 0,
                        )
                    )
    return sorted(blocks, key=lambda block: (block.row_id, block.start, block.end))


def _open_place_spans(
    tasks: Iterable[PlanningTask], spans: Mapping[str, Sequence[TaskSpan]]
) -> list[TaskSpan]:
    """Spans of the places to fill: tasks with someone, but fewer than required.

    A task already on « Non affecté » (nobody, or nobody shown) is there as itself.
    """
    unassigned = {span.task_id for span in spans.get(UNASSIGNED_ROW_ID, ())}
    return [
        TaskSpan(task_id=f"{OPEN_PLACE_PREFIX}{task.id}", start=task.start, end=task.end)
        for task in sorted(tasks, key=lambda item: (item.created_at, item.id))
        if not task.deleted
        and task.start is not None
        and task.end is not None
        and 0 < len(task.assignments) < task.required_people
        and task.id not in unassigned
    ]


def _open_place(span: TaskSpan, row: RowLayout, snapshot: PlanningSnapshot, year: int) -> OpenPlace:
    task = snapshot.tasks[span.task_id.removeprefix(OPEN_PLACE_PREFIX)]
    return OpenPlace(
        task_id=task.id,
        start=span.start,
        end=span.end,
        lane=row.lanes.get(span.task_id, 0),
        number=short_number(task.project_number, year) if task.project_number else "",
        title=task.title or task.project_cache.designation,
        missing=task.required_people - len(task.assignments),
    )


def _shrink_empty_unassigned(rows: GridRows, spans: Mapping[str, Sequence[TaskSpan]]) -> GridRows:
    """Reduce « Non affecté » to a thin row while no dated task waits on it."""
    pinned = rows.get(UNASSIGNED_ROW_ID)
    if pinned is None or spans.get(UNASSIGNED_ROW_ID):
        return rows
    shrink = pinned.height - EMPTY_UNASSIGNED_HEIGHT
    if shrink <= 0:
        return rows
    return GridRows(
        [
            replace(row, height=EMPTY_UNASSIGNED_HEIGHT)
            if row.resource_id == UNASSIGNED_ROW_ID
            else replace(row, top=row.top - shrink)
            for row in rows
        ]
    )


def _group_absences(blocks: Iterable[AbsenceBlock]) -> dict[str, tuple[AbsenceBlock, ...]]:
    grouped: dict[str, list[AbsenceBlock]] = defaultdict(list)
    for block in blocks:
        grouped[block.row_id].append(block)
    return {row_id: tuple(items) for row_id, items in grouped.items()}


def _group_rests(blocks: Iterable[RestBlock]) -> dict[str, tuple[RestBlock, ...]]:
    grouped: dict[str, list[RestBlock]] = defaultdict(list)
    for block in blocks:
        grouped[block.row_id].append(block)
    return {row_id: tuple(items) for row_id, items in grouped.items()}
