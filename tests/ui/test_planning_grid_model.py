from __future__ import annotations

import time
from dataclasses import replace
from datetime import date, timedelta

import pytest

from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.layout import UNASSIGNED_ROW_ID, Density, team_row_id
from projectflow.core.planning.models import (
    Absence,
    PlanningMeta,
    PlanningTask,
    ProjectCache,
    RecurringOff,
    Resource,
)
from projectflow.core.planning.task_types import SIMPLE_TASK_TYPES
from projectflow.core.planning.walltime import wall_time
from projectflow.ui.planning.colors import (
    FILL_AMOUNT_DARK,
    FILL_AMOUNT_LIGHT,
    NO_DOSSIER_COLORS,
    BarColors,
    bar_colors,
    dossier_colors,
    dossier_key,
    mix,
    type_colors,
)
from projectflow.ui.planning.grid_model import (
    EMPTY_UNASSIGNED_HEIGHT,
    RECURRING_PREFIX,
    GridModel,
    RowLabel,
    build_grid_model,
    column_tinted,
    duration_label,
    fitted_day_width,
    format_days,
    period_label,
    recurring_block_id,
    recurring_resource_id,
    short_number,
    span_label,
)

STAMP = "2026-10-01T08:00:00Z"
TODAY = date(2026, 10, 22)
MONDAY = date(2026, 10, 26)
NOW = wall_time(TODAY, 10, 0)


def resource(
    rid: str, name: str, initials: str, team: str, order: int, **extra: object
) -> Resource:
    return Resource.model_validate(
        {
            "id": rid,
            "name": name,
            "initials": initials,
            "team": team,
            "order": order,
            "created_at": STAMP,
            "updated_at": STAMP,
            **extra,
        }
    )


def task(
    tid: str,
    start: int | None,
    end: int | None,
    people: list[str],
    *,
    number: str = "2026-5231",
    **extra: object,
) -> PlanningTask:
    return PlanningTask.model_validate(
        {
            "id": tid,
            "project_number": number,
            "title": f"Tâche {tid}",
            "project_cache": ProjectCache(client="Müller SA").model_dump(),
            "start": start,
            "end": end,
            "assignments": {person: {} for person in people},
            "created_at": STAMP,
            "updated_at": STAMP,
            **extra,
        }
    )


def at(day: date, hour: int, minute: int = 0) -> int:
    return wall_time(day, hour, minute)


def snapshot(*tasks: PlanningTask, absences: tuple[Absence, ...] = ()) -> PlanningSnapshot:
    resources = [
        resource("mr", "Marc Rey", "MR", "atelier", 1000, role="Serrurier"),
        resource("jd", "Julien Dubois", "JD", "atelier", 2000),
        resource(
            "kr",
            "Kevin Roth",
            "KR",
            "atelier",
            3000,
            apprentice=True,
            recurring_off=[RecurringOff(weekday=2).model_dump()],
        ),
        resource("ps", "Paulo Silva", "PS", "atelier", 4000, canton="VD"),
        resource("nf", "Nicolas Favre", "NF", "montage", 1000),
        resource("sg", "Samuel Gay", "SG", "montage", 2000),
        resource("lb", "Luca Bianchi", "LB", "montage", 3000),
        resource("old", "Ancien Employé", "AE", "montage", 4000, active=False),
    ]
    return PlanningSnapshot(
        tasks={item.id: item for item in tasks},
        resources={item.id: item for item in resources},
        absences={item.id: item for item in absences},
        meta=PlanningMeta(),
    )


def build(snap: PlanningSnapshot, **options: object) -> GridModel:
    params: dict[str, object] = {
        "view": "week",
        "anchor": MONDAY,
        "viewport_width": 1000.0,
        "today": TODAY,
        "now": NOW,
    }
    params.update(options)
    return build_grid_model(snap, **params)  # type: ignore[arg-type]


# ------------------------------------------------------------------ rows ----


def test_unassigned_row_comes_first_and_holds_tasks_without_people() -> None:
    model = build(snapshot(task("u", at(MONDAY, 6, 30), at(MONDAY, 12), [])))
    ids = [row.resource_id for row in model.rows]
    assert ids[0] == UNASSIGNED_ROW_ID
    assert ids[1] == team_row_id("atelier")
    bar = model.bar("u", UNASSIGNED_ROW_ID)
    assert bar is not None
    label = model.label(UNASSIGNED_ROW_ID)
    assert label is not None
    assert label.title == "Non affecté"
    assert label.count == 1


def test_empty_unassigned_row_shrinks_and_rows_follow() -> None:
    model = build(snapshot(task("t", at(MONDAY, 6, 30), at(MONDAY, 12), ["mr"])))
    pinned = model.rows.get(UNASSIGNED_ROW_ID)
    assert pinned is not None
    assert pinned.height == EMPTY_UNASSIGNED_HEIGHT
    tops = [row.top for row in model.rows]
    bottoms = [row.bottom for row in model.rows]
    assert tops[1:] == bottoms[:-1]
    assert model.rows.height == bottoms[-1]


def test_collapsed_team_keeps_its_header_only() -> None:
    snap = snapshot(task("m", at(MONDAY, 6, 30), at(MONDAY, 12), ["nf"]))
    model = build(snap, collapsed_teams={"montage"})
    ids = [row.resource_id for row in model.rows]
    assert team_row_id("montage") in ids
    assert "nf" not in ids
    assert model.bars_by_task.get("m") is None
    header = model.label(team_row_id("montage"))
    assert header is not None
    assert header.collapsed
    assert header.count == 3


def test_archived_resource_row_returns_while_it_holds_a_task() -> None:
    plain = build(snapshot())
    assert plain.rows.get("old") is None
    busy = build(snapshot(task("o", at(MONDAY, 6, 30), at(MONDAY, 16, 30), ["old"])))
    assert busy.rows.get("old") is not None
    label = busy.label("old")
    assert label is not None
    assert label.archived


def test_hidden_people_lose_their_row_and_their_bars() -> None:
    model = build(
        snapshot(task("h", at(MONDAY, 6, 30), at(MONDAY, 12), ["jd"])), hidden_resource_ids={"jd"}
    )
    assert model.rows.get("jd") is None
    assert model.bars_by_task.get("h") is None
    assert model.label(UNASSIGNED_ROW_ID).count == 0  # type: ignore[union-attr]


def test_row_labels_carry_initials_flags_and_load() -> None:
    model = build(snapshot(task("t", at(MONDAY, 6, 30), at(MONDAY + timedelta(1), 16, 30), ["mr"])))
    marc = model.label("mr")
    kevin = model.label("kr")
    paulo = model.label("ps")
    assert marc is not None
    assert kevin is not None
    assert paulo is not None
    assert (marc.initials, marc.subtitle, marc.team_key) == ("MR", "Serrurier", "atelier")
    assert marc.planned_minutes == 2 * 540
    assert marc.available_minutes == 2490
    assert marc.load == pytest.approx(1080 / 2490)
    assert kevin.apprentice
    assert kevin.available_minutes == 2490 - 540
    assert paulo.canton == "VD"
    assert model.default_canton == "GE"


def test_a_subcontractor_row_has_no_load() -> None:
    label = RowLabel(
        row_id="galva",
        kind="resource",
        title="Galvanisation",
        subcontractor=True,
        planned_minutes=3 * 540,
        available_minutes=540,
    )
    assert label.load is None
    assert not label.absent_all_range
    assert not RowLabel(row_id="g", kind="resource", title="G", subcontractor=True).absent_all_range


# ------------------------------------------------------------------ days ----


def test_friday_afternoon_is_flagged_off_but_not_lunch() -> None:
    model = build(snapshot())
    monday, friday = model.days[0], model.days[4]
    assert monday.off == ()  # the lunch break is not hatched
    assert not monday.short
    assert friday.short
    assert (12 * 60, 16 * 60 + 30) in friday.off
    assert [info.day.weekday() for info in model.days] == [0, 1, 2, 3, 4]


def test_holidays_follow_the_canton_of_each_row() -> None:
    jeune = build(snapshot(), anchor=date(2026, 9, 7))
    thursday = jeune.days[3]
    assert thursday.holiday == "Jeûne genevois"
    assert not thursday.workday
    vaud = jeune.days_of("ps")[3]
    assert vaud.holiday is None
    assert vaud.workday
    assert jeune.holidays[date(2026, 9, 10)].label == "Jeûne genevois (GE)"
    lundi = build(snapshot(), anchor=date(2026, 9, 21))
    assert lundi.days[0].holiday is None
    assert lundi.days_of("ps")[0].holiday == "Lundi du Jeûne"
    assert lundi.days_of(UNASSIGNED_ROW_ID)[0].holiday is None
    assert lundi.holidays[date(2026, 9, 21)].label == "Lundi du Jeûne (VD)"


def test_today_and_now() -> None:
    model = build(snapshot(), anchor=TODAY)
    assert model.today_index == 3
    assert model.now_visible
    assert not build(snapshot()).now_visible
    assert model.period_label == "Semaine 43 · 19.10 – 23.10.2026"  # noqa: RUF001


# ------------------------------------------------------------------ bars ----


def test_bars_cut_by_the_visible_range() -> None:
    long = task("l", at(MONDAY - timedelta(3), 6, 30), at(MONDAY + timedelta(7), 12), ["mr"])
    model = build(snapshot(long))
    bar = model.bar("l", "mr")
    assert bar is not None
    assert bar.cut_left
    assert bar.cut_right
    inside = build(snapshot(task("i", at(MONDAY, 6, 30), at(MONDAY, 12), ["mr"]))).bar("i", "mr")
    assert inside is not None
    assert not inside.cut_left
    assert not inside.cut_right


def test_badges_conflict_people_and_shared_in_order() -> None:
    vacation = Absence.model_validate(
        {
            "id": "a",
            "resource_id": "lb",
            "reason": "vacances",
            "first_day": MONDAY.isoformat(),
            "last_day": MONDAY.isoformat(),
            "created_at": STAMP,
            "updated_at": STAMP,
        }
    )
    snap = snapshot(
        task("short", at(MONDAY, 6, 30), at(MONDAY, 16, 30), ["nf", "lb"], required_people=3),
        task("full", at(MONDAY + timedelta(1), 6, 30), at(MONDAY + timedelta(1), 12), ["nf", "sg"]),
        absences=(vacation,),
    )
    model = build(snap)
    absent = model.bar("short", "lb")
    assert absent is not None
    assert [badge.kind for badge in absent.badges] == ["conflict", "people"]
    assert absent.badges[0].text == "▲"
    assert "Vacances" in absent.badges[0].tooltip
    assert absent.badges[1].text == "2/3"
    present = model.bar("short", "nf")
    assert present is not None
    assert [badge.kind for badge in present.badges] == ["people"]
    shared = model.bar("full", "sg")
    assert shared is not None
    assert [badge.text for badge in shared.badges] == ["×2"]  # noqa: RUF001
    assert shared.shared_with == ("nf",)
    assert shared.people == ("NF", "SG")


def test_a_task_short_of_people_keeps_its_places_in_non_affecte() -> None:
    snap = snapshot(
        task("short", at(MONDAY, 6, 30), at(MONDAY, 16, 30), ["nf", "sg"], required_people=3),
        task("pair", at(MONDAY, 13), at(MONDAY, 16, 30), ["mr"], number="", required_people=2),
        task("delivery", at(MONDAY, 6, 30), at(MONDAY, 12), [], number="2026-5207"),
        task("full", at(MONDAY, 6, 30), at(MONDAY, 12), ["jd", "ps"], required_people=2),
        task("nobody", at(MONDAY, 6, 30), at(MONDAY, 12), [], required_people=3),
    )

    model = build(snap)

    places = {place.task_id: place for place in model.open_places}
    assert set(places) == {"short", "pair"}
    assert places["short"].text == "5231 · à pourvoir (1)"
    assert places["pair"].text == "Tâche pair · à pourvoir (1)"
    assert (places["short"].start, places["short"].end) == (at(MONDAY, 6, 30), at(MONDAY, 16, 30))
    # Not a bar: nothing to select, move or delete through it.
    assert {bar.task_id for bar in model.bars_by_row[UNASSIGNED_ROW_ID]} == {"delivery", "nobody"}
    assert model.bar("short", UNASSIGNED_ROW_ID) is None
    # Its own lane, beside the dated tasks without anybody, and counted with them.
    unassigned = model.rows.get(UNASSIGNED_ROW_ID)
    assert unassigned is not None
    lanes = {bar.lane for bar in model.bars_by_row[UNASSIGNED_ROW_ID]}
    assert places["short"].lane not in lanes
    assert unassigned.lane_count == len(lanes) + 1
    assert model.labels[UNASSIGNED_ROW_ID].count == 4


def test_unknown_dossier_is_a_conflict() -> None:
    snap = snapshot(task("x", at(MONDAY, 6, 30), at(MONDAY, 12), ["mr"], number="2026-9999"))
    model = build(snap, project_exists=lambda number: number != "2026-9999")
    bar = model.bar("x", "mr")
    assert bar is not None
    assert bar.has_conflict
    assert "introuvable" in bar.badges[0].tooltip


def test_short_dossier_numbers() -> None:
    assert short_number("2026-118", 2026) == "118"
    assert short_number("2025-4987", 2026) == "25-4987"
    assert short_number("2026-118-1", 2026) == "118-1"
    assert short_number("ABC", 2026) == "ABC"
    snap = snapshot(
        task("a", at(MONDAY, 6, 30), at(MONDAY, 12), ["mr"], number="2026-5198-2"),
        task("b", at(MONDAY, 13), at(MONDAY, 16, 30), ["mr"], number="2025-4987"),
    )
    model = build(snap)
    assert model.bar("a", "mr").number == "5198-2"  # type: ignore[union-attr]
    assert model.bar("b", "mr").number == "25-4987"  # type: ignore[union-attr]


def test_status_flags_and_texts() -> None:
    snap = snapshot(
        task("p", at(MONDAY, 6, 30), at(MONDAY, 12), ["mr"], status="provisional"),
        task("d", at(MONDAY, 13), at(MONDAY, 16, 30), ["mr"], status="done"),
    )
    model = build(snap)
    provisional = model.bar("p", "mr")
    done = model.bar("d", "mr")
    assert provisional is not None
    assert done is not None
    assert provisional.provisional
    assert not provisional.done
    assert done.done
    assert provisional.client == "Müller SA"
    assert provisional.duration == "½ j"
    assert provisional.time_range == "06:30–12:00"  # noqa: RUF001


def test_durations_in_working_days_or_hours() -> None:
    model = build(snapshot())
    calendar = model.calendar_of("mr")
    thursday = MONDAY + timedelta(3)
    assert duration_label(calendar, at(thursday, 6, 30), at(thursday + timedelta(1), 12)) == "2 j"
    assert duration_label(calendar, at(MONDAY, 6, 30), at(MONDAY + timedelta(1), 12)) == "1 j ½"
    assert duration_label(calendar, at(MONDAY, 7), at(MONDAY, 14)) == "6 h"
    assert duration_label(calendar, at(MONDAY, 7), at(MONDAY, 7, 30)) == "30 min"
    assert format_days(0.5) == "½ j"
    text = span_label(calendar, at(MONDAY, 6, 30), at(MONDAY + timedelta(1), 12))
    assert text == "lun 26.10 06:30 → mar 27.10 12:00 · 1 j ½"


def test_absences_and_recurring_days_off() -> None:
    vacation = Absence.model_validate(
        {
            "id": "v",
            "resource_id": "nf",
            "reason": "vacances",
            "first_day": (MONDAY - timedelta(3)).isoformat(),
            "last_day": (MONDAY + timedelta(1)).isoformat(),
            "created_at": STAMP,
            "updated_at": STAMP,
        }
    )
    model = build(snapshot(absences=(vacation,)))
    blocks = model.absences_by_row["nf"]
    assert len(blocks) == 1
    assert blocks[0].label == "Vacances"
    assert blocks[0].start == at(MONDAY, 6, 30)
    assert blocks[0].end == at(MONDAY + timedelta(1), 16, 30)
    school = model.absences_by_row["kr"]
    assert [block.recurring for block in school] == [True]
    assert school[0].label == "École professionnelle"
    assert model.absent_during("kr", at(MONDAY + timedelta(2), 8), at(MONDAY + timedelta(2), 9))
    # Each block carries what the grid needs to open it: the absence, or its person.
    assert blocks[0].absence_id == "v"
    assert model.absence_block("v") == blocks[0]
    assert school[0].absence_id.startswith(RECURRING_PREFIX)
    assert recurring_resource_id(school[0].absence_id) == "kr"
    assert recurring_resource_id("v") is None
    assert recurring_resource_id(recurring_block_id("res:with:colons", 2, "day")) == (
        "res:with:colons"
    )


# ------------------------------------------------------------ bi-ton ----


def test_alternate_columns_per_view() -> None:
    snap = snapshot()
    # Week: Monday plain, Tuesday tinted, Wednesday plain… counted from each Monday.
    week = build(snap)
    assert [column.day.weekday() for column in week.timescale.columns] == [0, 1, 2, 3, 4]
    assert week.tinted_columns == (1, 3)
    # Day view: the same rule on its single day.
    assert build(snap, view="day", anchor=MONDAY).tinted_columns == ()
    assert build(snap, view="day", anchor=MONDAY + timedelta(1)).tinted_columns == (0,)
    # Month: whole ISO weeks alternate, odd weeks tinted (week 45 starts on 2 November).
    month = build(snap, view="month", anchor=MONDAY, viewport_width=1400.0)
    tinted = set(month.tinted_columns)
    for column in month.timescale.columns:
        week_number = column.day.isocalendar()[1]
        assert (column.index in tinted) == (week_number % 2 == 1), column.day
    weeks = {column.day.isocalendar()[1] for column in month.timescale.columns}
    assert {43, 44} <= weeks
    assert column_tinted("month", date(2026, 11, 2))
    assert not column_tinted("month", date(2026, 10, 30))
    assert column_tinted("week", date(2026, 10, 27))
    assert not column_tinted("week", date(2026, 10, 26))


# ----------------------------------------------------------------- scale ----


def test_axis_fits_the_viewport_per_view() -> None:
    week = build(snapshot(), viewport_width=1000.5)
    assert week.timescale.day_width == pytest.approx(200.0)
    assert week.timescale.width <= 1000.5
    day = build(snapshot(), view="day", viewport_width=400.0)
    assert day.timescale.day_width == pytest.approx(600.0)
    wide = build(snapshot(), view="day", viewport_width=1200.5)
    assert wide.timescale.day_width == pytest.approx(1200.0)
    month = build(snapshot(), view="month", anchor=date(2026, 10, 5), viewport_width=1100.5)
    assert len(month.timescale.columns) == 22
    assert month.timescale.day_width == pytest.approx(50.0)
    assert fitted_day_width("week", 100.0, 5, 600) == 90.0
    refit = week.refit(800.5)
    assert refit.timescale.day_width == pytest.approx(160.0)
    assert refit.bars == week.bars


def test_period_labels() -> None:
    assert period_label("day", MONDAY, MONDAY) == "Lundi 26.10.2026"
    assert period_label("month", date(2026, 10, 1), date(2026, 10, 30)) == "Octobre 2026"


# ---------------------------------------------------------------- colors ----


def test_colours_by_type_and_dossier() -> None:
    meta = PlanningMeta()
    soudure = type_colors(meta, "soudure")
    assert soudure.stripe == "#b7684c"
    assert soudure.stripe_dark == "#df8c6f"
    assert type_colors(meta, "nope") == soudure
    assert dossier_key("2026-118-1") == dossier_key("2026-118") == "2026-118"
    assert dossier_colors("2026-118-1") == dossier_colors("2026-118")
    assert dossier_colors("") == NO_DOSSIER_COLORS
    one = task("a", None, None, [], number="2026-5231")
    assert bar_colors(one, meta, "dossier") == dossier_colors("2026-5231")
    assert bar_colors(one, meta, "type") == soudure
    background = "#ffffff"
    assert soudure.fill_for(background, dark=False) == mix(background, "#b7684c", FILL_AMOUNT_LIGHT)
    assert soudure.fill_for("#000000", dark=True) == mix("#000000", "#df8c6f", FILL_AMOUNT_DARK)
    model = build(
        snapshot(task("c", at(MONDAY, 6, 30), at(MONDAY, 12), ["mr"])), color_by="dossier"
    )
    assert model.bar("c", "mr").colors == dossier_colors("2026-5231")  # type: ignore[union-attr]


def test_bars_follow_the_configured_types() -> None:
    meta = PlanningMeta(task_types=SIMPLE_TASK_TYPES)
    snap = replace(
        snapshot(
            task("a", at(MONDAY, 6, 30), at(MONDAY, 12), ["mr"], task_type="montage"),
            task("b", at(MONDAY, 13), at(MONDAY, 16, 30), ["mr"], task_type="soudure"),
        ),
        meta=meta,
    )
    model = build(snap)
    montage = model.bar("a", "mr")
    removed = model.bar("b", "mr")
    assert montage is not None
    assert removed is not None
    assert montage.colors == BarColors(stripe="#4a925c", stripe_dark="#6fb880")
    # « soudure » is no longer configured: the bar takes the first type, Atelier.
    assert removed.colors == BarColors(stripe="#4f7ea8", stripe_dark="#78a6d1")
    assert removed.type_label == "Atelier"


# ----------------------------------------------------------------- speed ----


def test_building_forty_rows_over_three_months_stays_fast() -> None:
    teams = ("bureau", "atelier", "montage", "soustraitants")
    people = [
        resource(f"r{index}", f"Personne {index:02d}", f"P{index:02d}", teams[index % 4], index)
        for index in range(40)
    ]
    first = date(2026, 9, 1)
    tasks: list[PlanningTask] = []
    for index in range(900):
        day = first + timedelta(days=index % 90)
        while day.weekday() > 3:
            day += timedelta(days=1)
        owner = people[index % 40].id
        helper = people[(index * 7) % 40].id
        tasks.append(
            task(
                f"t{index}",
                at(day, 6, 30),
                at(day + timedelta(days=index % 3), 12),
                [owner] if index % 5 else [owner, helper],
                number=f"2026-{5000 + index % 120}",
            )
        )
    snap = PlanningSnapshot(
        tasks={item.id: item for item in tasks},
        resources={item.id: item for item in people},
        meta=PlanningMeta(),
    )
    started = time.perf_counter()
    month = build(snap, view="month", anchor=date(2026, 10, 1), density=Density.COMPACT)
    week = build(snap, view="week", anchor=date(2026, 10, 12))
    elapsed = time.perf_counter() - started
    assert len([row for row in month.rows if row.kind == "resource"]) == 40
    assert len(month.bars) > 200
    assert week.bars
    assert elapsed < 2.0
