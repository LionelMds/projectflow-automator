"""Night work (« travail de nuit »): model, commands, quick entry, rest and load."""

from __future__ import annotations

from datetime import date

import pytest
from test_planning_commands import (
    MON,
    THU,
    TUE,
    WED,
    at,
    ctx_with,
    first_task,
    make_task,
    task_after,
)
from test_planning_quick_entry import parse

from projectflow.core.planning.commands import (
    PlanningCommandError,
    create_task,
    duplicate_task,
    move_task,
    resize_task,
    schedule_task,
    set_night,
    shift_tasks,
    unschedule_task,
)
from projectflow.core.planning.conflicts import findings_by_bar, resource_load
from projectflow.core.planning.models import PlanningTask, document_from_json
from projectflow.core.planning.night import (
    MIN_REST_MINUTES,
    NIGHT_HOURS_MESSAGE,
    NIGHT_WEEKEND_MESSAGE,
    default_night,
    night_day,
    night_error,
    night_label,
    night_of,
    rest_spans,
    rest_text,
    shift_nights,
)
from projectflow.core.planning.walltime import format_wall_time, wall_time

FRI = date(2026, 10, 30)
SAT = date(2026, 10, 31)
SUN = date(2026, 11, 1)


def night_task(task_id: str = "n1", day: date = MON, **fields: object) -> PlanningTask:
    start, end = default_night(day)
    data: dict[str, object] = {"start": start, "end": end, "off_hours": True, "people": ("anna",)}
    return make_task(task_id, **(data | fields))


# ----------------------------------------------------------------- rules ----


def test_a_night_belongs_to_its_evening() -> None:
    assert night_of(at(MON, 21)) == MON
    assert night_of(at(TUE, 1)) == MON
    assert night_of(at(TUE, 10)) is None
    assert night_day(at(TUE, 10)) == TUE


@pytest.mark.parametrize(
    ("start", "end", "error"),
    [
        (at(MON, 21), at(TUE, 5), None),
        (at(THU, 22), at(FRI, 6, 30), None),
        (at(TUE, 18), at(TUE, 23), None),
        (at(TUE, 1), at(TUE, 5), None),
        (at(FRI, 21), at(SAT, 5), NIGHT_WEEKEND_MESSAGE),
        (at(SAT, 21), at(SUN, 5), NIGHT_WEEKEND_MESSAGE),
        (at(SUN, 22), at(MON, 4), NIGHT_WEEKEND_MESSAGE),
        (at(SAT, 2), at(SAT, 5), NIGHT_WEEKEND_MESSAGE),
        (at(MON, 21), at(TUE, 7), NIGHT_HOURS_MESSAGE),
        (at(MON, 17), at(MON, 23), NIGHT_HOURS_MESSAGE),
        (at(MON, 6), at(MON, 9), NIGHT_HOURS_MESSAGE),
    ],
)
def test_night_windows(start: int, end: int, error: str | None) -> None:
    assert night_error(start, end) == error


def test_nights_skip_friday_to_sunday() -> None:
    assert shift_nights(THU, 1) == date(2026, 11, 2)
    assert shift_nights(date(2026, 11, 2), -1) == THU
    assert shift_nights(MON, 2) == WED


def test_rest_spans_and_texts() -> None:
    start, end = default_night(MON)
    assert rest_spans(start, end) == ((at(MON, 13), start), (end, at(TUE, 13)))
    assert MIN_REST_MINUTES == 480
    assert (
        rest_text(start, end) == "Repos : dès lun 26.10 13:00 avant · jusqu'à mar 27.10 13:00 après"
    )
    assert night_label(at(THU, 22), at(FRI, 5)) == (
        "\N{CRESCENT MOON} nuit jeu 29.10 22:00 → ven 30.10 05:00 · 7 h"
    )


# ----------------------------------------------------------------- model ----


def test_off_hours_round_trips_and_defaults_to_false() -> None:
    task = night_task()
    data = task.to_json()
    assert data["off_hours"] is True
    back = document_from_json("task", data)
    assert isinstance(back, PlanningTask)
    assert back.off_hours
    assert (back.start, back.end) == (task.start, task.end)
    older = dict(data)
    del older["off_hours"]
    assert not PlanningTask.model_validate(older).off_hours


# -------------------------------------------------------------- commands ----


def test_create_keeps_exact_times_and_real_length() -> None:
    ctx = ctx_with()
    task = first_task(
        create_task(
            ctx,
            title="Aéroport",
            task_type="montage",
            start=at(WED, 22),
            end=at(THU, 5),
            resource_ids=("anna",),
            off_hours=True,
        )
    )
    assert task.off_hours
    assert (format_wall_time(task.start or 0), format_wall_time(task.end or 0)) == (
        "2026-10-28T22:00",
        "2026-10-29T05:00",
    )


def test_create_without_end_lasts_eight_hours_from_nine_pm() -> None:
    ctx = ctx_with()
    task = first_task(
        create_task(ctx, title="Nuit", task_type="montage", start=at(TUE, 10), off_hours=True)
    )
    assert (task.start, task.end) == default_night(TUE)


def test_create_on_a_friday_evening_is_refused() -> None:
    ctx = ctx_with()
    with pytest.raises(PlanningCommandError, match="vendredi soir"):
        create_task(ctx, title="Nuit", task_type="montage", start=at(FRI, 21), off_hours=True)


def test_move_keeps_the_clock_times_and_the_duration() -> None:
    ctx = ctx_with(night_task())
    moved = task_after(move_task(ctx, "n1", new_start=at(WED, 21)), "n1")
    assert (moved.start, moved.end) == default_night(WED)


def test_move_onto_a_friday_evening_is_refused() -> None:
    ctx = ctx_with(night_task())
    with pytest.raises(PlanningCommandError, match="week-end"):
        move_task(ctx, "n1", new_start=at(FRI, 21))


def test_move_past_half_past_six_is_refused() -> None:
    ctx = ctx_with(night_task())
    with pytest.raises(PlanningCommandError, match="06:30"):
        move_task(ctx, "n1", new_start=at(MON, 23))


def test_resize_snaps_to_the_half_hour_only() -> None:
    ctx = ctx_with(night_task())
    resized = task_after(resize_task(ctx, "n1", end=at(TUE, 6, 10)), "n1")
    assert resized.end == at(TUE, 6)
    shortened = task_after(resize_task(ctx, "n1", start=at(TUE, 4, 50)), "n1")
    assert shortened.start == at(TUE, 4, 30)


def test_shift_by_days_jumps_from_thursday_to_monday() -> None:
    ctx = ctx_with(night_task(day=THU))
    shifted = task_after(shift_tasks(ctx, ["n1"], days=1), "n1")
    assert (shifted.start, shifted.end) == default_night(date(2026, 11, 2))
    later = task_after(shift_tasks(ctx, ["n1"], minutes=30), "n1")
    assert later.start == at(THU, 21, 30)


def test_duplicate_goes_to_the_next_night() -> None:
    ctx = ctx_with(night_task(day=THU))
    copy = first_task(duplicate_task(ctx, "n1"))
    assert copy.off_hours
    assert (copy.start, copy.end) == default_night(date(2026, 11, 2))


def test_unschedule_and_schedule_keep_the_real_length() -> None:
    ctx = ctx_with(night_task(start=at(MON, 22), end=at(TUE, 5)))
    card = task_after(unschedule_task(ctx, "n1"), "n1")
    assert card.off_hours
    assert (card.estimate_minutes, card.estimate_days) == (420, 0.0)
    placed = task_after(
        schedule_task(ctx_with(card), "n1", start=at(WED, 9), resource_ids=("anna",)), "n1"
    )
    assert (placed.start, placed.end) == (at(WED, 21), at(THU, 4))


def test_set_night_on_and_off() -> None:
    day_task = make_task("d1", start=at(TUE, 6, 30), end=at(TUE, 15, 30), people=("anna",))
    ctx = ctx_with(day_task)
    night = task_after(set_night(ctx, "d1", night=True), "d1")
    assert night.off_hours
    assert (night.start, night.end) == default_night(TUE)
    back = task_after(set_night(ctx_with(night), "d1", night=False), "d1")
    assert not back.off_hours
    # Back on worked time from the first worked instant: Wednesday 06:30, 8 h of work.
    assert (back.start, back.end) == (at(WED, 6, 30), at(WED, 15, 30))


def test_set_night_on_a_friday_is_refused() -> None:
    ctx = ctx_with(make_task("d1", start=at(FRI, 6, 30), end=at(FRI, 12), people=("anna",)))
    with pytest.raises(PlanningCommandError, match="vendredi soir"):
        set_night(ctx, "d1", night=True)


# ----------------------------------------------------------- quick entry ----


def test_quick_entry_range_crossing_midnight() -> None:
    entry = parse("2026-121 jeu 22h-5h LBI")
    assert entry.off_hours
    assert entry.interpretation.endswith(
        "\N{CRESCENT MOON} nuit jeu 22.10 22:00 → ven 23.10 05:00 · 7 h"
    )
    assert entry.estimate_minutes == 420
    assert not entry.warnings


def test_quick_entry_clock_range_and_start_with_duration() -> None:
    entry = parse("mer 21:00-05:00 LBI")
    assert entry.off_hours
    assert (format_wall_time(entry.start or 0), format_wall_time(entry.end or 0)) == (
        "2026-10-28T21:00",
        "2026-10-29T05:00",
    )
    short = parse("jeu à 20h 3h LBI")
    assert short.off_hours
    assert (short.end or 0) - (short.start or 0) == 180


def test_quick_entry_word_nuit_defaults_to_nine_pm_five_am() -> None:
    entry = parse("pose nuit mar LBI")
    assert entry.off_hours
    assert (entry.start, entry.end) == (
        wall_time(date(2026, 10, 27), 21),
        wall_time(date(2026, 10, 28), 5),
    )


def test_quick_entry_night_ends_by_half_past_six() -> None:
    entry = parse("lun nuit 23h-8h LBI")
    assert entry.end == wall_time(date(2026, 10, 27), 6, 30)
    assert "Une nuit finit à 06:30 au plus tard : fin à 06:30" in entry.warnings


@pytest.mark.parametrize("text", ["ven nuit LBI", "sam 22h-4h LBI", "dim nuit LBI"])
def test_quick_entry_weekend_night_is_refused(text: str) -> None:
    entry = parse(text)
    assert not entry.off_hours
    assert NIGHT_WEEKEND_MESSAGE in entry.warnings


def test_quick_entry_daytime_hours_stay_worked_time() -> None:
    entry = parse("lun 14h-16h LBI")
    assert not entry.off_hours


# ------------------------------------------------------------ rest, load ----


def rest_findings(*tasks: PlanningTask) -> dict[tuple[str, str | None], list[str]]:
    ctx = ctx_with(*tasks)
    found = findings_by_bar(ctx.snapshot, MON, date(2026, 11, 1), ctx.calendar_for)
    return {
        key: [item.message for item in items if item.kind == "rest"]
        for key, items in found.items()
        if any(item.kind == "rest" for item in items)
    }


def test_a_morning_task_after_a_night_breaks_the_rest() -> None:
    morning = make_task("m1", start=at(TUE, 6, 30), end=at(TUE, 12), people=("anna",))
    found = rest_findings(night_task(), morning)
    assert set(found) == {("n1", "anna"), ("m1", "anna")}
    assert found[("n1", "anna")][0].startswith("Repos de 8 h non respecté : 1 h 30 après la nuit")
    assert "nuit « Tache n1 »" in found[("m1", "anna")][0]


def test_exactly_eight_hours_of_rest_is_enough() -> None:
    before = make_task("b1", start=at(MON, 6, 30), end=at(MON, 13), people=("anna",))
    after = make_task("a1", start=at(TUE, 13), end=at(TUE, 16, 30), people=("anna",))
    assert rest_findings(night_task(), before, after) == {}


def test_rest_is_per_person() -> None:
    other = make_task("o1", start=at(TUE, 6, 30), end=at(TUE, 12), people=("bruno",))
    assert rest_findings(night_task(), other) == {}


def test_a_night_raises_no_non_worked_finding() -> None:
    ctx = ctx_with(night_task())
    found = findings_by_bar(ctx.snapshot, MON, TUE, ctx.calendar_for)
    assert all(item.kind != "non_worked" for items in found.values() for item in items)


def test_night_minutes_count_on_top_of_the_day() -> None:
    day = make_task("d1", start=at(MON, 6, 30), end=at(MON, 12), people=("anna",))
    ctx = ctx_with(night_task(), day)
    planned, available = resource_load(ctx.snapshot, "anna", MON, TUE, ctx.calendar_for("anna"))
    assert planned == 330 + 480
    assert available == 2 * 540
