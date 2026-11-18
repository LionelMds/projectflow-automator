"""« Hors horaires standard »: a task let out of the working hours inside a day."""

from __future__ import annotations

from datetime import date

import pytest
from test_planning_commands import (  # type: ignore[import-not-found]
    FRI,
    MON,
    NEXT_MON,
    SAT,
    THU,
    TUE,
    at,
    ctx_with,
    first_task,
    make_snapshot,
    make_task,
    people,
    task_after,
)

from projectflow.core.planning.calendar import (
    DEFAULT_WORK_WEEK,
    WorkCalendar,
    continuous_week,
)
from projectflow.core.planning.commands import (
    PlanningCommandError,
    calendar_resolver,
    create_task,
    duplicate_task,
    move_task,
    resize_task,
    set_night,
    set_off_standard,
    shift_tasks,
    task_hours,
    unschedule_task,
)
from projectflow.core.planning.conflicts import findings_by_bar, resource_load
from projectflow.core.planning.models import PlanningTask
from projectflow.core.planning.outlook_sync import day_segments

GE = WorkCalendar()
OPEN = GE.without_breaks()
HALVES = ((390, 720), (720, 990))
CHRISTMAS = date(2026, 12, 25)


def free(task_id: str = "free", **fields: object) -> PlanningTask:
    """Give a task « hors horaires standard » of Anna, two hours on Monday morning by default."""
    data: dict[str, object] = {
        "start": at(MON, 6, 30),
        "end": at(MON, 8, 30),
        "people": ("anna",),
        "off_standard": True,
    }
    return make_task(task_id, **(data | fields))


def held(task_id: str = "held", **fields: object) -> PlanningTask:
    return free(task_id, **({"off_standard": False} | fields))


# -------------------------------------------------------------- calendar ----


def test_the_usual_hours_still_block_lunch_and_friday_afternoon() -> None:
    assert dict(DEFAULT_WORK_WEEK)[0] == ((390, 720), (780, 990))
    assert dict(DEFAULT_WORK_WEEK)[4] == ((390, 720),)
    assert not GE.is_worked(at(MON, 12, 30))
    assert not GE.is_worked(at(FRI, 14))
    assert GE.working_start(at(MON, 12, 30)) == at(MON, 13)
    assert GE.working_start(at(FRI, 13)) == at(NEXT_MON, 6, 30)
    assert GE.working_end(at(FRI, 15)) == at(FRI, 12)
    assert GE.minutes_per_week == 2490


def test_continuous_week_removes_the_break_and_the_short_day() -> None:
    week = continuous_week(DEFAULT_WORK_WEEK)

    assert week == dict.fromkeys(range(5), HALVES)
    assert continuous_week(week) == week
    assert continuous_week({}) == {}
    # A weekday without hours stays not worked; the others take the widest day of the week.
    assert continuous_week({0: ((420, 720),), 2: ((780, 960),)}) == {
        0: ((420, 720), (720, 960)),
        2: ((420, 720), (720, 960)),
    }
    # Noon outside the day: one period.
    assert continuous_week({1: ((780, 900), (930, 1020))}) == {1: ((780, 1020),)}


def test_without_breaks_opens_the_day_and_nothing_else() -> None:
    assert OPEN is GE.without_breaks()
    assert OPEN.without_breaks() is OPEN
    assert OPEN.week == dict.fromkeys(range(5), HALVES)
    assert OPEN.cantons == GE.cantons
    assert OPEN.is_worked(at(MON, 12, 30))
    assert OPEN.is_worked(at(FRI, 14))
    assert OPEN.working_start(at(MON, 12, 30)) == at(MON, 12, 30)
    assert OPEN.working_end(at(FRI, 15)) == at(FRI, 15)
    assert OPEN.work_between(at(MON, 6, 30), at(MON, 16, 30)) == 600
    assert OPEN.whole_days(at(MON, 6, 30), at(MON, 16, 30)) == 1.0
    assert OPEN.whole_days(at(MON, 12), at(MON, 16, 30)) == 0.5
    # The weekend and the holidays stay not worked, before 06:30 and after 16:30 too.
    assert not OPEN.is_worked(at(SAT, 9))
    assert not OPEN.is_workday(CHRISTMAS)
    assert not OPEN.is_worked(at(MON, 5))
    assert not OPEN.is_worked(at(MON, 17))
    assert OPEN.working_start(at(FRI, 17)) == at(NEXT_MON, 6, 30)


def test_without_breaks_keeps_the_closures() -> None:
    closed = WorkCalendar(closures=[TUE])

    assert closed.without_breaks().is_closed(TUE)
    assert not closed.without_breaks().is_workday(TUE)


def test_hours_of_follows_the_switch_of_the_task() -> None:
    assert GE.hours_of(off_standard=False) is GE
    assert GE.hours_of(off_standard=True) is OPEN
    ctx = ctx_with(free(), held())
    calendar_for = calendar_resolver(ctx.snapshot)

    assert task_hours(ctx.snapshot.tasks["held"], calendar_for) is calendar_for("anna")
    assert task_hours(ctx.snapshot.tasks["free"], calendar_for) is (
        calendar_for("anna").without_breaks()
    )


# -------------------------------------------------------------- commands ----


def test_a_task_on_the_usual_hours_is_still_moved_out_of_the_blocked_ones() -> None:
    ctx = ctx_with(held())

    moved = task_after(move_task(ctx, "held", new_start=at(FRI, 13)), "held")
    assert (moved.start, moved.end) == (at(NEXT_MON, 6, 30), at(NEXT_MON, 8, 30))
    lunch = task_after(move_task(ctx, "held", new_start=at(MON, 12)), "held")
    assert (lunch.start, lunch.end) == (at(MON, 13), at(MON, 15))
    ctx = ctx_with(held(start=at(MON, 11), end=at(MON, 12)))
    # An end stretched into the lunch break comes back to noon: nothing changes.
    assert resize_task(ctx, "held", end=at(MON, 12, 30)) == ()


def test_the_switch_lets_a_task_out_without_moving_it() -> None:
    ctx = ctx_with(held())

    changed = task_after(set_off_standard(ctx, "held", True), "held")  # noqa: FBT003

    assert changed.off_standard
    assert (changed.start, changed.end) == (at(MON, 6, 30), at(MON, 8, 30))
    assert set_off_standard(ctx_with(free()), "free", True) == ()  # noqa: FBT003


def test_a_task_let_out_goes_on_friday_afternoon_and_over_lunch() -> None:
    ctx = ctx_with(free())

    friday = task_after(move_task(ctx, "free", new_start=at(FRI, 13)), "free")
    assert (friday.start, friday.end) == (at(FRI, 13), at(FRI, 15))
    lunch = task_after(move_task(ctx, "free", new_start=at(MON, 11, 30)), "free")
    assert (lunch.start, lunch.end) == (at(MON, 11, 30), at(MON, 13, 30))
    # Its length counts the lunch hour: three hours from 11:00 end at 14:00.
    ctx = ctx_with(free(start=at(MON, 11), end=at(MON, 12)))
    stretched = task_after(resize_task(ctx, "free", end=at(MON, 14)), "free")
    assert (stretched.start, stretched.end) == (at(MON, 11), at(MON, 14))
    earlier = task_after(resize_task(ctx, "free", end=at(MON, 12, 30)), "free")
    assert earlier.end == at(MON, 12, 30)


def test_a_task_let_out_still_skips_the_weekend_and_the_evening() -> None:
    ctx = ctx_with(free())

    weekend = task_after(move_task(ctx, "free", new_start=at(SAT, 9)), "free")
    assert weekend.start == at(NEXT_MON, 6, 30)
    late = task_after(move_task(ctx, "free", new_start=at(FRI, 16)), "free")
    assert (late.start, late.end) == (at(FRI, 16), at(NEXT_MON, 8))


def test_a_whole_day_let_out_lasts_ten_hours() -> None:
    ctx = ctx_with(free(start=at(MON, 6, 30), end=at(MON, 16, 30)))

    moved = task_after(move_task(ctx, "free", new_start=at(FRI, 6, 30)), "free")

    assert (moved.start, moved.end) == (at(FRI, 6, 30), at(FRI, 16, 30))


def test_the_keyboard_steps_a_task_let_out_through_noon() -> None:
    ctx = ctx_with(
        free(start=at(MON, 11, 30), end=at(MON, 12)),
        held(start=at(MON, 11, 30), end=at(MON, 12)),
    )

    changes = shift_tasks(ctx, ["free", "held"], minutes=30)

    out = task_after(changes, "free")
    kept = task_after(changes, "held")
    assert (out.start, out.end) == (at(MON, 12), at(MON, 12, 30))
    assert (kept.start, kept.end) == (at(MON, 13), at(MON, 13, 30))


def test_put_back_on_the_usual_hours_a_task_keeps_the_time_it_lasted() -> None:
    ctx = ctx_with(
        free("friday", start=at(FRI, 13), end=at(FRI, 15)),
        free("lunch", start=at(MON, 12), end=at(MON, 13)),
        free("across", start=at(MON, 11), end=at(MON, 14)),
    )

    friday = task_after(set_off_standard(ctx, "friday", False), "friday")  # noqa: FBT003
    lunch = task_after(set_off_standard(ctx, "lunch", False), "lunch")  # noqa: FBT003
    across = task_after(set_off_standard(ctx, "across", False), "across")  # noqa: FBT003

    assert not friday.off_standard
    assert (friday.start, friday.end) == (at(NEXT_MON, 6, 30), at(NEXT_MON, 8, 30))
    assert (lunch.start, lunch.end) == (at(MON, 13), at(MON, 14))
    # Three hours from 11:00 on the usual hours: one before lunch, two after.
    assert (across.start, across.end) == (at(MON, 11), at(MON, 15))


def test_a_card_only_takes_the_switch() -> None:
    ctx = ctx_with(make_task("card", estimate_days=1.5))

    card = task_after(set_off_standard(ctx, "card", True), "card")  # noqa: FBT003

    assert card.off_standard
    assert (card.start, card.end, card.estimate_days) == (None, None, 1.5)


def test_night_work_is_never_hors_horaires_standard() -> None:
    night = make_task("night", start=at(MON, 21), end=at(TUE, 5), people=("anna",), off_hours=True)
    with pytest.raises(PlanningCommandError, match="nuit"):
        set_off_standard(ctx_with(night), "night", True)  # noqa: FBT003

    became = task_after(set_night(ctx_with(free()), "free", True), "free")  # noqa: FBT003
    assert became.off_hours
    assert not became.off_standard
    card = task_after(
        set_night(ctx_with(make_task("card", off_standard=True)), "card", True),  # noqa: FBT003
        "card",
    )
    assert (card.off_hours, card.off_standard) == (True, False)


def test_a_task_may_be_created_out_of_the_usual_hours() -> None:
    ctx = ctx_with()

    task = first_task(
        create_task(
            ctx,
            title="Livraison",
            task_type="montage",
            resource_ids=["anna"],
            start=at(FRI, 13),
            end=at(FRI, 15),
            off_standard=True,
        )
    )
    usual = first_task(
        create_task(
            ctx, title="Livraison", task_type="montage", resource_ids=["anna"], start=at(FRI, 13)
        )
    )
    night = first_task(
        create_task(
            ctx,
            title="Pose",
            task_type="montage",
            resource_ids=["anna"],
            start=at(MON, 21),
            off_hours=True,
            off_standard=True,
        )
    )

    assert task.off_standard
    assert (task.start, task.end) == (at(FRI, 13), at(FRI, 15))
    assert usual.start == at(NEXT_MON, 6, 30)
    assert (night.off_hours, night.off_standard) == (True, False)


def test_a_copy_keeps_the_switch_and_its_hours() -> None:
    ctx = ctx_with(free(start=at(FRI, 13), end=at(FRI, 15)))

    copy = first_task(duplicate_task(ctx, "free"))

    assert copy.off_standard
    # Right after the original: 15:00 to 16:30, the last half hour on Monday morning.
    assert (copy.start, copy.end) == (at(FRI, 15), at(NEXT_MON, 7))


def test_back_to_a_planifier_the_length_is_measured_without_breaks() -> None:
    ctx = ctx_with(free(start=at(MON, 6, 30), end=at(MON, 16, 30)))

    card = task_after(unschedule_task(ctx, "free"), "free")

    assert card.off_standard
    assert card.estimate_minutes == 600
    assert card.estimate_days == 1.0


# ------------------------------------------------------------- conflicts ----


def non_worked(*tasks: PlanningTask) -> dict[str, list[str]]:
    snapshot = make_snapshot(*people(), *tasks)
    found = findings_by_bar(snapshot, MON, FRI, calendar_resolver(snapshot))
    return {
        task_id: [finding.message for finding in findings if finding.kind == "non_worked"]
        for (task_id, _row), findings in found.items()
    }


def test_a_task_let_out_is_not_reported_out_of_the_working_hours() -> None:
    found = non_worked(
        free("lunch", start=at(MON, 12, 30), end=at(MON, 13, 30)),
        free("friday", start=at(FRI, 13), end=at(FRI, 15), people=("bruno",)),
        held("stray", start=at(TUE, 12, 30), end=at(TUE, 13, 30), people=("emma",)),
    )

    assert not found.get("lunch")
    assert not found.get("friday")
    assert found["stray"] == ["Début hors des horaires de travail"]


def test_the_load_counts_the_lunch_hour_of_a_task_let_out() -> None:
    snapshot = make_snapshot(*people(), free(start=at(MON, 6, 30), end=at(MON, 16, 30)))
    planned, available = resource_load(snapshot, "anna", MON, MON, GE)
    usual = make_snapshot(*people(), held(start=at(MON, 6, 30), end=at(MON, 16, 30)))

    assert (planned, available) == (600, 540)
    assert resource_load(usual, "anna", MON, MON, GE) == (540, 540)


# --------------------------------------------------------------- outlook ----


def test_the_outlook_copy_follows_the_hours_of_the_task() -> None:
    start, end = at(THU, 13), at(FRI, 15)

    assert day_segments(start, end, GE) == [
        (at(THU, 13), at(THU, 16, 30)),
        (at(FRI, 6, 30), at(FRI, 12)),
    ]
    assert day_segments(start, end, OPEN) == [
        (at(THU, 13), at(THU, 16, 30)),
        (at(FRI, 6, 30), at(FRI, 15)),
    ]
