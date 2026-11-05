"""Rows of the Planning grid: lanes of overlapping tasks and vertical positions.

A row is a team header, a resource (person or subcontractor) or the « Non affecté » row
of scheduled tasks without assignee. Tasks that overlap in time inside a row are stacked
on lanes, and the row grows by one lane height per lane. Only the tasks inside the visible
range add lanes, so a busy week far away never stretches the rows on screen. Pure geometry
shared by the grid, the row headers and the PDF export; nothing here imports Qt.
"""

from __future__ import annotations

import bisect
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from projectflow.core.planning.models import PlanningTask, Resource
from projectflow.core.planning.walltime import WallTime

if TYPE_CHECKING:
    from projectflow.core.planning.changes import PlanningSnapshot

UNASSIGNED_ROW_ID = "__unassigned__"
UNASSIGNED_LABEL = "Non affecté"
TEAM_ROW_PREFIX = "__team__:"
TEAM_HEADER_HEIGHT = 24
BAR_MARGIN = 2
"""Space kept above and below a bar inside its lane."""

RowKind = Literal["team", "resource", "unassigned"]


class Density(IntEnum):
    """Row density setting; its value is the height of one lane in pixels."""

    COMPACT = 22
    NORMAL = 38
    LARGE = 54


COMPACT = Density.COMPACT
NORMAL = Density.NORMAL
LARGE = Density.LARGE
DENSITY_LABELS: Mapping[Density, str] = {
    Density.COMPACT: "Compacte",
    Density.NORMAL: "Normale",
    Density.LARGE: "Grande",
}


def density_from_name(name: str, default: Density = Density.NORMAL) -> Density:
    """Density stored in settings as ``compact``, ``normal`` or ``large``."""
    try:
        return Density[name.strip().upper()]
    except KeyError:
        return default


@dataclass(frozen=True, slots=True)
class TaskSpan:
    """A task as drawn on one row: its id and wall-clock span."""

    task_id: str
    start: WallTime
    end: WallTime

    def overlaps(self, start: WallTime, end: WallTime) -> bool:
        return self.start < end and self.end > start


@dataclass(frozen=True, slots=True)
class RowLayout:
    """Vertical place of one row; ``resource_id`` is the row id.

    For a team header it is ``team_row_id(key)`` and ``lanes`` is empty. ``lanes`` is a
    read-only copy and stays out of the hash.
    """

    resource_id: str
    top: int
    height: int
    lanes: Mapping[str, int] = field(default_factory=dict, hash=False)
    kind: RowKind = "resource"
    lane_count: int = 1
    lane_height: int = Density.NORMAL

    def __post_init__(self) -> None:
        object.__setattr__(self, "lanes", MappingProxyType(dict(self.lanes)))

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def team_key(self) -> str:
        """Team of a header row, empty for other rows."""
        return self.resource_id.removeprefix(TEAM_ROW_PREFIX) if self.kind == "team" else ""

    @property
    def bar_height(self) -> int:
        return self.lane_height - 2 * BAR_MARGIN

    def lane_top(self, lane: int) -> int:
        return self.top + lane * self.lane_height

    def bar_top(self, lane: int) -> int:
        return self.lane_top(lane) + BAR_MARGIN

    def contains(self, y: float) -> bool:
        return self.top <= y < self.bottom


def team_row_id(team_key: str) -> str:
    return f"{TEAM_ROW_PREFIX}{team_key}"


def row_kind(row_id: str) -> RowKind:
    if row_id == UNASSIGNED_ROW_ID:
        return "unassigned"
    if row_id.startswith(TEAM_ROW_PREFIX):
        return "team"
    return "resource"


def row_height(lane_count: int, density: Density = Density.NORMAL) -> int:
    """Height of a resource row: one lane at least."""
    return max(lane_count, 1) * int(density)


# ------------------------------------------------------------------ lanes ----


def _first_fit(spans: Sequence[TaskSpan]) -> tuple[dict[str, int], int]:
    # Stable sort: equal spans keep the caller's order, so they never swap lanes.
    ordered = sorted(spans, key=lambda span: (span.start, -span.end))
    lane_ends: list[WallTime] = []
    lanes: dict[str, int] = {}
    for span in ordered:
        for lane, lane_end in enumerate(lane_ends):
            if lane_end <= span.start:
                lane_ends[lane] = span.end
                lanes[span.task_id] = lane
                break
        else:
            lanes[span.task_id] = len(lane_ends)
            lane_ends.append(span.end)
    return lanes, len(lane_ends)


def assign_lanes(
    spans: Iterable[TaskSpan], *, window: tuple[WallTime, WallTime] | None = None
) -> tuple[dict[str, int], int]:
    """Lane of every span (first fit) and the row's lane count, at least one.

    Spans sharing a lane never overlap; touching spans (an end equal to a start) share
    one. Longer spans go first when two start together, and equal spans keep the order of
    ``spans``. With a ``window``, only spans overlapping it add lanes; the others get lanes
    among themselves, capped to that count, since they are off-screen.
    """
    items = list(spans)
    if window is None:
        lanes, count = _first_fit(items)
        return lanes, max(count, 1)
    low, high = window
    inside = [span for span in items if span.overlaps(low, high)]
    outside = [span for span in items if not span.overlaps(low, high)]
    lanes, count = _first_fit(inside)
    count = max(count, 1)
    others, _others_count = _first_fit(outside)
    lanes.update({task_id: min(lane, count - 1) for task_id, lane in others.items()})
    return lanes, count


def spans_by_row(
    tasks: Iterable[PlanningTask], *, shown: Collection[str] | None = None
) -> dict[str, list[TaskSpan]]:
    """Spans of the scheduled live tasks per row id.

    A task shows on the row of each assignee, or on the « Non affecté » row without one.
    With ``shown`` (the row ids laid out), assignees without a row are skipped and a task
    left with none goes on « Non affecté », so no scheduled task ever disappears. Rows list
    older tasks first, so equal spans keep the same lanes on every poste.
    """
    rows: dict[str, list[TaskSpan]] = {}
    ordered = sorted(tasks, key=lambda task: (task.created_at, task.id))
    for task in ordered:
        if task.deleted or task.start is None or task.end is None:
            continue
        span = TaskSpan(task_id=task.id, start=task.start, end=task.end)
        assignees = task.assignee_ids
        if shown is not None:
            assignees = tuple(row_id for row_id in assignees if row_id in shown)
        for row_id in assignees or (UNASSIGNED_ROW_ID,):
            rows.setdefault(row_id, []).append(span)
    return rows


def row_resources(
    snapshot: PlanningSnapshot, *, window: tuple[WallTime, WallTime] | None = None
) -> list[Resource]:
    """Resources with a row: the active ones, and archived ones still holding a task.

    An archived row comes back while a live scheduled task (overlapping ``window`` when
    given) is assigned to it, so that work stays visible and can be moved to someone
    else. Same order as ``PlanningSnapshot.active_resources``.
    """
    busy = {
        resource_id
        for task in snapshot.live_tasks()
        if task.start is not None
        and task.end is not None
        and (window is None or (task.start < window[1] and task.end > window[0]))
        for resource_id in task.assignee_ids
    }
    team_order = {team.key: team.order for team in snapshot.meta.teams}
    return sorted(
        (
            resource
            for resource in snapshot.resources.values()
            if not resource.deleted and (resource.active or resource.id in busy)
        ),
        key=lambda resource: (
            team_order.get(resource.team, len(team_order)),
            resource.order,
            resource.name.casefold(),
        ),
    )


def row_ids(
    resources: Iterable[Resource], *, team_headers: bool = True, unassigned: bool = True
) -> list[str]:
    """Row ids in display order.

    The « Non affecté » row comes first, then one header per team followed by all its
    resources: teams in order of their first resource, resources in the given order, so a
    team whose resources are not adjacent in ``resources`` still has one header.
    """
    teams: dict[str, list[str]] = {}
    for resource in resources:
        teams.setdefault(resource.team, []).append(resource.id)
    ids = [UNASSIGNED_ROW_ID] if unassigned else []
    for team, members in teams.items():
        if team_headers:
            ids.append(team_row_id(team))
        ids.extend(members)
    return ids


# ------------------------------------------------------------------- rows ----


class GridRows:
    """Rows stacked from the top, with lookups by position and by id."""

    __slots__ = ("_height", "_index", "_rows", "_tops")

    def __init__(self, rows: Sequence[RowLayout]) -> None:
        self._rows = tuple(rows)
        self._tops = tuple(row.top for row in self._rows)
        self._index = {row.resource_id: index for index, row in enumerate(self._rows)}
        self._height = self._rows[-1].bottom if self._rows else 0

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self) -> Iterator[RowLayout]:
        return iter(self._rows)

    def __getitem__(self, index: int) -> RowLayout:
        return self._rows[index]

    @property
    def rows(self) -> tuple[RowLayout, ...]:
        return self._rows

    @property
    def height(self) -> int:
        return self._height

    def get(self, row_id: str) -> RowLayout | None:
        index = self._index.get(row_id)
        return self._rows[index] if index is not None else None

    def index_of(self, row_id: str) -> int | None:
        return self._index.get(row_id)

    def index_at(self, y: float, *, clamp: bool = False) -> int | None:
        """Index of the row under ``y``; outside the rows None, or the nearest when ``clamp``."""
        if not self._rows:
            return None
        if not 0 <= y < self._height:
            if not clamp:
                return None
            return 0 if y < 0 else len(self._rows) - 1
        return bisect.bisect_right(self._tops, y) - 1

    def row_at(self, y: float, *, clamp: bool = False) -> RowLayout | None:
        index = self.index_at(y, clamp=clamp)
        return self._rows[index] if index is not None else None

    def visible(self, top: float, bottom: float) -> tuple[RowLayout, ...]:
        """Rows overlapping ``[top, bottom)``."""
        if bottom <= top or bottom <= 0 or top >= self._height:
            return ()
        first = max(bisect.bisect_right(self._tops, top) - 1, 0)
        last = bisect.bisect_left(self._tops, bottom)
        return self._rows[first:last]


def layout_rows(
    ids: Sequence[str],
    spans: Mapping[str, Iterable[TaskSpan]],
    *,
    density: Density = Density.NORMAL,
    window: tuple[WallTime, WallTime] | None = None,
    team_height: int = TEAM_HEADER_HEIGHT,
) -> GridRows:
    """Stack the rows ``ids`` (from ``row_ids``) with lanes for their ``spans``.

    ``window`` is the visible wall-clock range whose overlaps set the row heights.
    """
    lane_height = int(density)
    rows: list[RowLayout] = []
    top = 0
    for row_id in ids:
        kind = row_kind(row_id)
        if kind == "team":
            row = RowLayout(
                resource_id=row_id,
                top=top,
                height=team_height,
                kind=kind,
                lane_count=0,
                lane_height=lane_height,
            )
        else:
            lanes, count = assign_lanes(spans.get(row_id, ()), window=window)
            row = RowLayout(
                resource_id=row_id,
                top=top,
                height=row_height(count, density),
                lanes=lanes,
                kind=kind,
                lane_count=count,
                lane_height=lane_height,
            )
        rows.append(row)
        top = row.bottom
    return GridRows(rows)
