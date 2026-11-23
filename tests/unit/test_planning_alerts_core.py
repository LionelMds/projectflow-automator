"""« Alerte notification »: the alerts of a task, when they ring, and their history."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from test_planning_commands import (  # type: ignore[import-not-found]
    MON,
    TUE,
    at,
    ctx_with,
    first_task,
    make_ctx,
    make_resource,
    make_snapshot,
    make_task,
    task_after,
)

from projectflow.core.planning.alert_log import LOG_FILE, MAX_ENTRIES, AlertLog
from projectflow.core.planning.alerts import (
    Alert,
    concerns,
    due_alerts,
    lead_text,
    moment_text,
    my_resource_id,
    reminder_due,
    reminder_text,
)
from projectflow.core.planning.commands import (
    PlanningCommandError,
    duplicate_task,
    move_task,
    set_reminders,
)
from projectflow.core.planning.models import (
    MAX_REMINDERS,
    Author,
    PlanningTask,
    Reminder,
    document_from_json,
)
from projectflow.core.planning.walltime import MINUTES_PER_DAY

HALF_HOUR = Reminder(minutes=30)
DAY_BEFORE = Reminder(minutes=MINUTES_PER_DAY)
AT_SIX = Reminder(kind="at", at=at(MON, 6))


def visit(task_id: str = "visit", **fields: object) -> PlanningTask:
    """Give a task of Anna on Monday from 08:00 to 10:00, with « 30 min avant »."""
    data: dict[str, object] = {
        "start": at(MON, 8),
        "end": at(MON, 10),
        "people": ("anna",),
        "reminders": (HALF_HOUR,),
    }
    return make_task(task_id, **(data | fields))


# ----------------------------------------------------------------- model ----


def test_a_task_keeps_its_alerts_through_json() -> None:
    task = visit(reminders=(HALF_HOUR, AT_SIX))

    data = task.to_json()
    assert data["reminders"] == [
        {"kind": "before", "minutes": 30, "at": None},
        {"kind": "at", "minutes": 0, "at": "2026-10-26T06:00"},
    ]
    assert document_from_json("task", data) == task


def test_a_task_written_before_the_alerts_reads_without_any() -> None:
    data = visit().to_json()
    del data["reminders"]

    task = document_from_json("task", data)

    assert isinstance(task, PlanningTask)
    assert task.reminders == ()


def test_an_alert_at_a_moment_needs_its_moment() -> None:
    with pytest.raises(ValidationError):
        Reminder(kind="at")
    with pytest.raises(ValidationError):
        Reminder(minutes=-5)


def test_alert_texts() -> None:
    assert lead_text(5) == "5 min"
    assert lead_text(60) == "1 h"
    assert lead_text(90) == "1 h 30"
    assert lead_text(2 * MINUTES_PER_DAY) == "2 j"
    assert reminder_text(Reminder(minutes=0)) == "Au début"
    assert reminder_text(HALF_HOUR) == "30 min avant"
    assert reminder_text(DAY_BEFORE) == "1 j avant"
    assert reminder_text(AT_SIX) == "lun 26.10 à 06:00"
    assert moment_text(at(MON, 6), MON) == "aujourd'hui à 06:00"
    assert moment_text(at(TUE, 7, 30), MON) == "demain à 07:30"


# -------------------------------------------------------------- commands ----


def test_set_reminders_orders_them_and_drops_doubles() -> None:
    ctx = ctx_with(visit(reminders=()))

    changes = set_reminders(
        ctx, "visit", [AT_SIX, HALF_HOUR, {"kind": "before", "minutes": 30}, DAY_BEFORE]
    )

    assert task_after(changes, "visit").reminders == (DAY_BEFORE, HALF_HOUR, AT_SIX)
    same = make_ctx(ctx.snapshot.apply(changes))
    assert set_reminders(same, "visit", [DAY_BEFORE, HALF_HOUR, AT_SIX]) == ()


def test_set_reminders_refuses_too_many_and_unreadable_ones() -> None:
    ctx = ctx_with(visit())

    with pytest.raises(PlanningCommandError, match="Au plus"):
        set_reminders(ctx, "visit", [Reminder(minutes=m) for m in range(MAX_REMINDERS + 1)])
    with pytest.raises(PlanningCommandError):
        set_reminders(ctx, "visit", [{"kind": "at"}])


def test_an_alert_before_the_start_follows_the_task_that_moves() -> None:
    ctx = ctx_with(visit(reminders=(HALF_HOUR, AT_SIX)))

    moved = task_after(move_task(ctx, "visit", new_start=at(TUE, 8)), "visit")

    assert reminder_due(moved, HALF_HOUR) == at(TUE, 7, 30)
    assert reminder_due(moved, AT_SIX) == at(MON, 6)


def test_a_copy_keeps_the_alerts_before_the_start_only() -> None:
    ctx = ctx_with(visit(reminders=(HALF_HOUR, AT_SIX)))

    copy = first_task(duplicate_task(ctx, "visit"))

    assert copy.reminders == (HALF_HOUR,)


# ------------------------------------------------------------- what rings ----


def test_an_alert_rings_once_its_time_has_come() -> None:
    snapshot = make_snapshot(make_resource("anna", "Anna Favre", initials="AF"), visit())

    early = due_alerts(snapshot, now=at(MON, 7, 29), since=at(MON, 7))
    alerts = due_alerts(snapshot, now=at(MON, 7, 30), since=at(MON, 7, 29))

    assert early == []
    assert [(alert.task_id, alert.due, alert.missed) for alert in alerts] == [
        ("visit", at(MON, 7, 30), False)
    ]
    assert alerts[0].title == "Tache visit"
    assert alerts[0].body == "Commence dans 30 min · aujourd'hui à 08:00 · AF"
    assert due_alerts(snapshot, now=at(MON, 7, 31), since=at(MON, 7, 30)) == []
    assert due_alerts(snapshot, now=at(MON, 7, 31), since=at(MON, 7), known={alerts[0].key}) == []


def test_an_alert_found_late_is_missed_and_old_ones_are_left() -> None:
    snapshot = make_snapshot(
        visit(),
        visit("old", start=at(MON, 8) - 9 * MINUTES_PER_DAY, end=at(MON, 10) - 9 * MINUTES_PER_DAY),
    )

    alerts = due_alerts(snapshot, now=at(MON, 9), since=0)

    assert [(alert.task_id, alert.missed) for alert in alerts] == [("visit", True)]


def test_finished_deleted_and_unscheduled_tasks_do_not_ring() -> None:
    snapshot = make_snapshot(
        visit("done", status="done"),
        visit("gone", deleted_at="2026-10-20T08:00:00Z"),
        visit("card", start=None, end=None),
        visit("card-at", start=None, end=None, reminders=(Reminder(kind="at", at=at(MON, 7, 30)),)),
    )

    alerts = due_alerts(snapshot, now=at(MON, 7, 30), since=at(MON, 7))

    assert [alert.task_id for alert in alerts] == ["card-at"]
    assert alerts[0].body == "Rappel · tâche à planifier"


def test_a_poste_rings_for_the_tasks_of_its_person() -> None:
    anna = make_resource("anna", "Anna Favre", initials="AF")
    bruno = make_resource("bruno", "Bruno Rossier", initials="BR", m365_user_id="m-2")
    snapshot = make_snapshot(anna, bruno, visit())
    task = snapshot.tasks["visit"]

    assert my_resource_id(snapshot, initials="af") == "anna"
    assert my_resource_id(snapshot, initials="AF", m365_id="m-2") == "bruno"
    assert my_resource_id(snapshot, initials="ZZ") == ""
    assert concerns(task, resource_id="anna", initials="AF")
    assert not concerns(task, resource_id="bruno", initials="BR")
    assert not concerns(task, resource_id="", initials="ZZ")
    assert concerns(task, resource_id="", initials="")
    window = {"now": at(MON, 7, 30), "since": at(MON, 7)}
    assert due_alerts(snapshot, **window, resource_id="bruno", initials="BR") == []
    assert len(due_alerts(snapshot, **window, resource_id="anna", initials="AF")) == 1


def test_a_task_nobody_does_rings_for_who_created_it() -> None:
    task = visit(people=(), created_by=Author(initials="LM"))

    assert concerns(task, resource_id="", initials="lm")
    assert not concerns(task, resource_id="anna", initials="AF")
    assert concerns(visit(people=(), created_by=Author()), resource_id="anna", initials="AF")


# --------------------------------------------------------------- history ----


def alert(key: str, due: int, *, missed: bool = False) -> Alert:
    return Alert(key=key, task_id="visit", due=due, title="Visite", body="…", missed=missed)


def test_the_history_is_kept_from_one_start_to_the_next(tmp_path: Path) -> None:
    log = AlertLog(tmp_path)
    assert log.last_check is None

    added = log.record([alert("a", at(MON, 7)), alert("b", at(MON, 8))], now=at(MON, 8))
    again = log.record([alert("a", at(MON, 7))], now=at(MON, 8, 1))

    assert [entry.key for entry in added] == ["a", "b"]
    assert again == []
    assert "a" in log
    assert log.unread_count == 2
    reopened = AlertLog(tmp_path)
    assert [entry.key for entry in reopened.entries] == ["b", "a"]
    assert reopened.last_check == at(MON, 8, 1)
    assert (tmp_path / LOG_FILE).is_file()


def test_reading_putting_off_and_removing(tmp_path: Path) -> None:
    log = AlertLog(tmp_path)
    log.record([alert("a", at(MON, 7)), alert("b", at(MON, 8))], now=at(MON, 8))

    log.mark_read("a")
    log.snooze("b", at(MON, 9))

    assert log.unread_count == 0
    assert log.wake(at(MON, 8, 59)) == []
    woken = log.wake(at(MON, 9))
    assert [entry.key for entry in woken] == ["b"]
    assert not woken[0].read
    assert not woken[0].snoozed
    assert AlertLog(tmp_path).unread_count == 1
    log.mark_all_read()
    log.remove("a")
    assert [entry.key for entry in AlertLog(tmp_path).entries] == ["b"]
    log.clear()
    assert AlertLog(tmp_path).entries == []


def test_the_history_forgets_what_is_old_and_survives_a_broken_file(tmp_path: Path) -> None:
    log = AlertLog(tmp_path)
    old = at(MON, 7) - 90 * MINUTES_PER_DAY
    log.record([alert("old", old)], now=old)
    log.record(
        [alert(f"k{index}", at(MON, 7) + index) for index in range(MAX_ENTRIES + 5)],
        now=at(MON, 12),
    )

    assert "old" not in log
    assert len(log.entries) == MAX_ENTRIES
    assert log.entries[0].key == f"k{MAX_ENTRIES + 4}"

    (tmp_path / LOG_FILE).write_text("{not json", encoding="utf-8")
    broken = AlertLog(tmp_path)
    assert broken.entries == []
    assert broken.last_check is None


def test_a_history_without_folder_stays_in_memory() -> None:
    log = AlertLog()

    log.record([alert("a", at(MON, 7))], now=at(MON, 7))

    assert log.unread_count == 1
