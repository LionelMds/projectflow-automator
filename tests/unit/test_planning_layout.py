from __future__ import annotations

from datetime import date

import pytest

from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.layout import (
    BAR_MARGIN,
    COMPACT,
    DENSITY_LABELS,
    LARGE,
    NORMAL,
    TEAM_HEADER_HEIGHT,
    UNASSIGNED_ROW_ID,
    Density,
    GridRows,
    RowLayout,
    TaskSpan,
    assign_lanes,
    density_from_name,
    layout_rows,
    row_height,
    row_ids,
    row_kind,
    row_resources,
    spans_by_row,
    team_row_id,
)
from projectflow.core.planning.models import (
    DEFAULT_TEAMS,
    PlanningMeta,
    PlanningTask,
    Resource,
    Team,
)
from projectflow.core.planning.walltime import wall_time

MON = date(2026, 10, 26)
TUE = date(2026, 10, 27)
NEXT_MON = date(2026, 11, 2)


def at(day: date, text: str) -> int:
    hours, minutes = (int(part) for part in text.split(":"))
    return wall_time(day, hours, minutes)


def span(task_id: str, start: int, end: int) -> TaskSpan:
    return TaskSpan(task_id=task_id, start=start, end=end)


def task(task_id: str, start: int, end: int, *assignees: str, created: str = "") -> PlanningTask:
    return PlanningTask(
        id=task_id,
        title=task_id,
        start=start,
        end=end,
        assignments={assignee: {} for assignee in assignees},
        created_at=created,
    )


# ------------------------------------------------------------------ lanes ----


def test_lanes_stack_only_overlapping_spans() -> None:
    lanes, count = assign_lanes(
        [
            span("a", 0, 10),
            span("b", 10, 20),
            span("c", 5, 12),
            span("d", 12, 30),
            span("e", 31, 40),
        ]
    )

    assert lanes == {"a": 0, "b": 0, "c": 1, "d": 1, "e": 0}
    assert count == 2


def test_touching_spans_share_a_lane() -> None:
    morning = span("morning", at(MON, "06:30"), at(MON, "12:00"))
    afternoon = span("afternoon", at(MON, "12:00"), at(MON, "16:30"))

    assert assign_lanes([afternoon, morning]) == ({"morning": 0, "afternoon": 0}, 1)


def test_equal_spans_keep_the_given_order() -> None:
    first = span("x", 0, 10)
    second = span("y", 0, 10)

    assert assign_lanes([first, second]) == ({"x": 0, "y": 1}, 2)
    assert assign_lanes([second, first]) == ({"y": 0, "x": 1}, 2)


def test_lanes_are_deterministic() -> None:
    spans = [span(f"t{index}", index * 7 % 50, index * 7 % 50 + 15) for index in range(40)]

    assert assign_lanes(spans) == assign_lanes(list(spans))


def test_longer_span_takes_the_upper_lane_when_both_start_together() -> None:
    assert assign_lanes([span("short", 0, 5), span("long", 0, 20)]) == (
        {"long": 0, "short": 1},
        2,
    )


def test_first_fit_reuses_the_lowest_free_lane() -> None:
    lanes, count = assign_lanes(
        [span("a", 0, 100), span("b", 0, 10), span("c", 0, 20), span("d", 15, 30)]
    )

    assert lanes == {"a": 0, "c": 1, "b": 2, "d": 2}
    assert count == 3


def test_no_span_still_gives_one_lane() -> None:
    assert assign_lanes([]) == ({}, 1)
    assert assign_lanes([], window=(0, 10)) == ({}, 1)


def test_only_spans_in_the_window_add_lanes() -> None:
    far = [span("far-1", 0, 10), span("far-2", 0, 10), span("far-3", 0, 10)]
    near = [span("near-1", 100, 120), span("near-2", 110, 130)]

    lanes, count = assign_lanes([*far, *near], window=(100, 200))

    assert count == 2
    assert lanes["near-1"] == 0
    assert lanes["near-2"] == 1
    assert {lanes["far-1"], lanes["far-2"], lanes["far-3"]} == {0, 1}
    assert lanes["far-1"] == 0


def test_window_edges_are_exclusive() -> None:
    lanes, count = assign_lanes(
        [
            span("before", 0, 100),
            span("also-before", 50, 100),
            span("inside", 100, 200),
            span("after", 200, 300),
        ],
        window=(100, 200),
    )

    assert count == 1
    assert lanes == {"before": 0, "also-before": 0, "inside": 0, "after": 0}


# ---------------------------------------------------------------- density ----


def test_density_values_are_lane_heights() -> None:
    assert (int(COMPACT), int(NORMAL), int(LARGE)) == (22, 38, 54)
    assert Density.COMPACT is COMPACT
    assert set(DENSITY_LABELS) == set(Density)


@pytest.mark.parametrize(
    ("name", "density"),
    [("compact", COMPACT), ("NORMAL", NORMAL), (" large ", LARGE), ("", NORMAL), ("x", NORMAL)],
)
def test_density_from_name(name: str, density: Density) -> None:
    assert density_from_name(name) is density


def test_density_from_name_default() -> None:
    assert density_from_name("unknown", COMPACT) is COMPACT


@pytest.mark.parametrize(
    ("lanes", "density", "height"),
    [(0, NORMAL, 38), (1, NORMAL, 38), (3, COMPACT, 66), (2, LARGE, 108)],
)
def test_row_height(lanes: int, density: Density, height: int) -> None:
    assert row_height(lanes, density) == height


# ------------------------------------------------------------------- rows ----


def test_row_ids_and_kinds() -> None:
    assert team_row_id("atelier") == "__team__:atelier"
    assert row_kind(team_row_id("atelier")) == "team"
    assert row_kind(UNASSIGNED_ROW_ID) == "unassigned"
    assert row_kind("4f2a") == "resource"


def test_row_ids_insert_team_headers_and_the_unassigned_row() -> None:
    resources = [
        Resource(id="ana", name="Ana", team="bureau"),
        Resource(id="ben", name="Ben", team="bureau"),
        Resource(id="carl", name="Carl", team="atelier"),
        Resource(id="galva", name="Galvaswiss", team="soustraitants"),
    ]

    assert row_ids(resources) == [
        UNASSIGNED_ROW_ID,
        "__team__:bureau",
        "ana",
        "ben",
        "__team__:atelier",
        "carl",
        "__team__:soustraitants",
        "galva",
    ]
    assert row_ids(resources, team_headers=False, unassigned=False) == [
        "ana",
        "ben",
        "carl",
        "galva",
    ]
    assert row_ids([]) == [UNASSIGNED_ROW_ID]


def test_row_ids_give_each_team_one_header_when_its_resources_are_apart() -> None:
    # Regression: two teams with the same order were interleaved by active_resources().
    meta = PlanningMeta(teams=(*DEFAULT_TEAMS, Team(key="peinture", name="Peinture")))
    snapshot = PlanningSnapshot(
        resources={
            "a": Resource(id="a", name="Alice", team="bureau"),
            "b": Resource(id="b", name="Bruno", team="peinture"),
            "c": Resource(id="c", name="Carla", team="bureau"),
        },
        meta=meta,
    )
    assert [resource.id for resource in snapshot.active_resources()] == ["a", "c", "b"]

    ids = row_ids(snapshot.active_resources())

    assert ids == [UNASSIGNED_ROW_ID, "__team__:bureau", "a", "c", "__team__:peinture", "b"]
    rows = layout_rows(ids, {})
    header = rows.get(team_row_id("bureau"))
    assert header is not None
    assert header.top == int(NORMAL)
    assert [row.resource_id for row in rows].count(team_row_id("bureau")) == 1


def test_tasks_of_an_archived_resource_stay_on_the_grid() -> None:
    # Regression: a task assigned only to an archived resource was drawn on no row.
    snapshot = PlanningSnapshot(
        tasks={
            "t1": task("t1", at(NEXT_MON, "06:30"), at(NEXT_MON, "16:30"), "ben"),
            "t2": task("t2", at(MON, "06:30"), at(MON, "12:00"), "ana", "ben"),
        },
        resources={
            "ana": Resource(id="ana", name="Ana"),
            "ben": Resource(id="ben", name="Ben", active=False),
            "zoe": Resource(id="zoe", name="Zoé", active=False),
            "old": Resource(id="old", name="Old", deleted_at="2026-10-01T08:00:00Z"),
        },
    )

    resources = row_resources(snapshot)
    ids = row_ids(resources)
    rows = layout_rows(ids, spans_by_row(snapshot.live_tasks(), shown=set(ids)))

    assert [resource.id for resource in resources] == ["ana", "ben"]
    assert ids == [UNASSIGNED_ROW_ID, "__team__:atelier", "ana", "ben"]
    ben = rows.get("ben")
    assert ben is not None
    assert set(ben.lanes) == {"t1", "t2"}
    this_week = (at(MON, "06:30"), at(MON, "16:30"))
    far_week = (at(TUE, "06:30"), at(TUE, "16:30"))
    assert [resource.id for resource in row_resources(snapshot, window=this_week)] == [
        "ana",
        "ben",
    ]
    assert [resource.id for resource in row_resources(snapshot, window=far_week)] == ["ana"]


def test_spans_by_row_puts_tasks_without_a_shown_row_on_unassigned() -> None:
    tasks = [
        task("lost", at(MON, "06:30"), at(MON, "12:00"), "gone"),
        task("half", at(MON, "13:00"), at(MON, "16:30"), "gone", "ana"),
        task("free", at(TUE, "06:30"), at(TUE, "08:00")),
    ]

    rows = spans_by_row(tasks, shown={UNASSIGNED_ROW_ID, "ana"})

    assert {row: [item.task_id for item in items] for row, items in rows.items()} == {
        UNASSIGNED_ROW_ID: ["free", "lost"],
        "ana": ["half"],
    }
    assert set(spans_by_row(tasks)) == {"gone", "ana", UNASSIGNED_ROW_ID}


def test_row_layouts_are_hashable_and_their_lanes_read_only() -> None:
    lanes = {"a": 0}
    row = RowLayout(resource_id="ana", top=0, height=38, lanes=lanes)
    lanes["b"] = 1

    assert row.lanes == {"a": 0}
    assert hash(row) == hash(RowLayout(resource_id="ana", top=0, height=38, lanes={"a": 0}))
    assert row == RowLayout(resource_id="ana", top=0, height=38, lanes={"a": 0})
    assert row != RowLayout(resource_id="ana", top=0, height=38, lanes={"a": 1})
    assert len({row, *_sample_layout()}) == 7
    with pytest.raises(TypeError):
        row.lanes["c"] = 2  # type: ignore[index]


def test_spans_by_row_places_tasks_on_each_assignee_or_unassigned() -> None:
    tasks = [
        task("pair", at(MON, "06:30"), at(MON, "12:00"), "ana", "ben", created="2026-10-02"),
        task("solo", at(MON, "13:00"), at(MON, "16:30"), "ana", created="2026-10-01"),
        task("loose", at(TUE, "06:30"), at(TUE, "08:00")),
        PlanningTask(id="backlog", title="À planifier"),
        task("gone", at(MON, "06:30"), at(MON, "07:00"), "ana").model_copy(
            update={"deleted_at": "2026-10-20T10:00:00Z"}
        ),
    ]

    rows = spans_by_row(tasks)

    assert {row: [item.task_id for item in items] for row, items in rows.items()} == {
        "ana": ["solo", "pair"],
        "ben": ["pair"],
        UNASSIGNED_ROW_ID: ["loose"],
    }
    assert rows["ben"][0] == span("pair", at(MON, "06:30"), at(MON, "12:00"))


def test_spans_by_row_orders_ties_by_creation_on_every_poste() -> None:
    older = task("older", at(MON, "06:30"), at(MON, "12:00"), "ana", created="2026-10-01")
    newer = task("newer", at(MON, "06:30"), at(MON, "12:00"), "ana", created="2026-10-05")

    for tasks in ([older, newer], [newer, older]):
        lanes, _count = assign_lanes(spans_by_row(tasks)["ana"])
        assert lanes == {"older": 0, "newer": 1}


def _sample_layout(density: Density = NORMAL) -> GridRows:
    ids = [
        UNASSIGNED_ROW_ID,
        team_row_id("bureau"),
        "ana",
        "ben",
        team_row_id("atelier"),
        "carl",
    ]
    spans = {
        "ana": [
            span("a1", at(MON, "06:30"), at(MON, "12:00")),
            span("a2", at(MON, "08:00"), at(MON, "16:30")),
            span("a3", at(MON, "09:00"), at(TUE, "12:00")),
        ],
        "carl": [span("c1", at(MON, "06:30"), at(MON, "16:30"))],
        "nobody-shown": [span("x", at(MON, "06:30"), at(MON, "16:30"))],
    }
    return layout_rows(ids, spans, density=density)


def test_layout_stacks_rows_from_lane_counts() -> None:
    layout = _sample_layout()

    assert [(row.resource_id, row.kind, row.top, row.height) for row in layout] == [
        (UNASSIGNED_ROW_ID, "unassigned", 0, 38),
        ("__team__:bureau", "team", 38, TEAM_HEADER_HEIGHT),
        ("ana", "resource", 62, 3 * 38),
        ("ben", "resource", 176, 38),
        ("__team__:atelier", "team", 214, TEAM_HEADER_HEIGHT),
        ("carl", "resource", 238, 38),
    ]
    assert layout.height == 276
    assert len(layout) == 6
    assert layout[2].lanes == {"a1": 0, "a2": 1, "a3": 2}
    assert layout[2].lane_count == 3
    assert layout[1].lanes == {}
    assert layout[1].lane_count == 0
    assert layout[1].team_key == "bureau"
    assert layout[2].team_key == ""
    assert layout.get("nobody-shown") is None


def test_density_changes_lane_heights_but_not_team_headers() -> None:
    compact = _sample_layout(COMPACT)
    large = _sample_layout(LARGE)

    assert compact.get("ana") is not None
    assert [row.height for row in compact] == [22, 24, 66, 22, 24, 22]
    assert [row.height for row in large] == [54, 24, 162, 54, 24, 54]
    assert layout_rows([team_row_id("atelier")], {}, team_height=30).height == 30


def test_bar_geometry_inside_lanes() -> None:
    ana = _sample_layout().get("ana")

    assert ana is not None
    assert ana.lane_height == 38
    assert ana.lane_top(0) == 62
    assert ana.lane_top(2) == 62 + 2 * 38
    assert ana.bar_top(1) == 62 + 38 + BAR_MARGIN
    assert ana.bar_height == 38 - 2 * BAR_MARGIN
    assert ana.bottom == 176
    assert ana.contains(62)
    assert not ana.contains(176)


@pytest.mark.parametrize(
    ("y", "row_id"),
    [
        (0, UNASSIGNED_ROW_ID),
        (37.9, UNASSIGNED_ROW_ID),
        (38, "__team__:bureau"),
        (61.5, "__team__:bureau"),
        (62, "ana"),
        (175, "ana"),
        (176, "ben"),
        (275.9, "carl"),
        (276, None),
        (-1, None),
    ],
)
def test_row_at(y: float, row_id: str | None) -> None:
    row = _sample_layout().row_at(y)

    assert (row.resource_id if row is not None else None) == row_id


def test_row_at_clamps_on_request() -> None:
    layout = _sample_layout()

    first = layout.row_at(-50, clamp=True)
    last = layout.row_at(5000, clamp=True)
    assert first is not None
    assert first.resource_id == UNASSIGNED_ROW_ID
    assert last is not None
    assert last.resource_id == "carl"
    assert layout.index_at(100) == 2
    assert layout.index_at(5000) is None
    assert layout.index_of("ben") == 3
    assert layout.index_of("zoe") is None


@pytest.mark.parametrize(
    ("top", "bottom", "row_ids_shown"),
    [
        (0, 38, [UNASSIGNED_ROW_ID]),
        (37, 63, [UNASSIGNED_ROW_ID, "__team__:bureau", "ana"]),
        (100, 200, ["ana", "ben"]),
        (176, 214, ["ben"]),
        (
            -100,
            10_000,
            [UNASSIGNED_ROW_ID, "__team__:bureau", "ana", "ben", "__team__:atelier", "carl"],
        ),
        (276, 400, []),
        (-100, 0, []),
        (100, 100, []),
    ],
)
def test_visible_rows(top: float, bottom: float, row_ids_shown: list[str]) -> None:
    rows = _sample_layout().visible(top, bottom)

    assert [row.resource_id for row in rows] == row_ids_shown


def test_window_keeps_far_overlaps_from_growing_rows() -> None:
    spans = {
        "ana": [
            span("far-1", at(NEXT_MON, "06:30"), at(NEXT_MON, "16:30")),
            span("far-2", at(NEXT_MON, "06:30"), at(NEXT_MON, "16:30")),
            span("near", at(MON, "06:30"), at(MON, "12:00")),
        ]
    }

    everything = layout_rows(["ana"], spans)
    this_week = layout_rows(["ana"], spans, window=(at(MON, "06:30"), at(MON, "16:30")))

    assert everything[0].lane_count == 2
    assert this_week[0].lane_count == 1
    assert this_week[0].lanes == {"near": 0, "far-1": 0, "far-2": 0}


def test_empty_layout() -> None:
    layout = layout_rows([], {})

    assert layout.height == 0
    assert len(layout) == 0
    assert layout.rows == ()
    assert layout.row_at(0) is None
    assert layout.row_at(0, clamp=True) is None
    assert layout.visible(0, 100) == ()
