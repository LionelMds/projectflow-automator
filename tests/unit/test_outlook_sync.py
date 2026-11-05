"""Copy of the planning into an Outlook calendar: events, diff, sync, Graph payloads."""

# ruff: noqa: DTZ001 - the planning keeps naive Swiss wall times on purpose

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest

from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.commands import calendar_resolver
from projectflow.core.planning.models import Absence, PlanningTask, Resource
from projectflow.core.planning.outlook_sync import (
    EventSpec,
    ExistingEvent,
    OutlookCalendarSync,
    desired_events,
    plan_sync,
)
from projectflow.core.planning.walltime import wall_time
from projectflow.exceptions import GraphError
from projectflow.graph.outlook_calendar import (
    FINGERPRINT_PROPERTY,
    KEY_PROPERTY,
    OutlookCalendarGateway,
    event_payload,
)

TODAY = date(2026, 11, 5)
MONDAY = date(2026, 11, 9)
STAMP = "2026-11-05T08:00:00Z"


def _doc(model: Any, **fields: Any) -> Any:
    base = {"created_at": STAMP, "updated_at": STAMP}
    return model.model_validate({**base, **fields})


def snapshot(*tasks: PlanningTask, absences: tuple[Absence, ...] = ()) -> PlanningSnapshot:
    people = {
        "kdu": _doc(Resource, id="kdu", name="Karim"),
        "lm": _doc(Resource, id="lm", name="Lionel"),
    }
    return PlanningSnapshot(
        tasks={task.id: task for task in tasks},
        resources=people,
        absences={absence.id: absence for absence in absences},
    )


def task(tid: str, start: int, end: int, people: tuple[str, ...] = ("kdu",), **extra: Any) -> Any:
    return _doc(
        PlanningTask,
        id=tid,
        project_number="2026-5271",
        title="Local grillagé",
        start=start,
        end=end,
        assignments={rid: {} for rid in people},
        project_cache={"client": "EasyJet", "location": "Aéroport"},
        **extra,
    )


def events_of(snap: PlanningSnapshot, **options: Any) -> dict[str, EventSpec]:
    return desired_events(snap, today=TODAY, calendar_for=calendar_resolver(snap), **options)


# ------------------------------------------------------------------ events ----


def test_a_task_over_two_days_gives_one_event_per_worked_day() -> None:
    snap = snapshot(
        task("a", wall_time(MONDAY, 13), wall_time(date(2026, 11, 10), 12), ("kdu", "lm"))
    )
    events = events_of(snap)
    assert sorted(events) == ["task:a:2026-11-09", "task:a:2026-11-10"]
    first, second = events["task:a:2026-11-09"], events["task:a:2026-11-10"]
    assert first.subject == "2026-5271 - Local grillagé · Karim, Lionel"
    assert (first.start, first.end) == (datetime(2026, 11, 9, 13), datetime(2026, 11, 9, 16, 30))
    assert (second.start, second.end) == (
        datetime(2026, 11, 10, 6, 30),
        datetime(2026, 11, 10, 12),
    )
    assert "Client : EasyJet" in first.body
    assert first.location == "Aéroport"
    hidden = events_of(snap, hide_details=True)["task:a:2026-11-09"]
    assert "EasyJet" not in hidden.body
    assert not hidden.location


def test_weekends_nights_provisional_and_far_tasks() -> None:
    friday, monday = date(2026, 11, 13), date(2026, 11, 16)
    over_weekend = task("w", wall_time(friday, 6, 30), wall_time(monday, 12))
    night = task("n", wall_time(MONDAY, 22), wall_time(date(2026, 11, 10), 5), off_hours=True)
    provisional = task("p", wall_time(MONDAY, 7), wall_time(MONDAY, 9), status="provisional")
    far = task("f", wall_time(date(2028, 1, 10), 7), wall_time(date(2028, 1, 10), 9))
    events = events_of(snapshot(over_weekend, night, provisional, far))
    assert sorted(key for key in events if key.startswith("task:w")) == [
        "task:w:2026-11-13",
        "task:w:2026-11-16",
    ]
    assert events["task:n:2026-11-09"].subject.startswith("Nuit · ")
    assert events["task:n:2026-11-09"].end == datetime(2026, 11, 10, 5)
    assert events["task:p:2026-11-09"].tentative
    assert events["task:p:2026-11-09"].subject.startswith("(provisoire) ")
    assert not any(key.startswith("task:f") for key in events)


def test_absences_are_all_day_or_half_days() -> None:
    week = _doc(
        Absence,
        id="v",
        resource_id="kdu",
        reason="vacances",
        first_day=MONDAY,
        last_day=date(2026, 11, 13),
    )
    morning = _doc(
        Absence, id="m", resource_id="lm", first_day=MONDAY, last_day=MONDAY, part="morning"
    )
    events = events_of(snapshot(absences=(week, morning)))
    vacation = events["absence:v:2026-11-09"]
    assert vacation.subject == "Absent : Karim (Vacances)"
    assert vacation.all_day
    assert (vacation.start, vacation.end) == (datetime(2026, 11, 9), datetime(2026, 11, 14))
    half = events["absence:m:2026-11-09"]
    assert (half.start, half.end) == (datetime(2026, 11, 9, 6, 30), datetime(2026, 11, 9, 12))


# -------------------------------------------------------------------- diff ----


def test_plan_creates_updates_deletes_and_keeps_history() -> None:
    spec = EventSpec("task:a:2026-11-09", "A", datetime(2026, 11, 9, 7), datetime(2026, 11, 9, 9))
    changed = EventSpec(
        "task:b:2026-11-10", "B", datetime(2026, 11, 10, 7), datetime(2026, 11, 10, 9)
    )
    existing = {
        "task:b:2026-11-10": ExistingEvent("eb", "old"),
        "task:c:2026-11-11": ExistingEvent("ec", "x"),
        "task:d:2025-01-06": ExistingEvent("ed", "x"),  # history: left alone
    }
    plan = plan_sync({spec.key: spec, changed.key: changed}, existing, today=TODAY)
    assert plan.creates == (spec,)
    assert plan.updates == (("eb", changed),)
    assert plan.deletes == (("task:c:2026-11-11", "ec"),)
    same = {changed.key: ExistingEvent("eb", changed.fingerprint)}
    assert plan_sync({changed.key: changed}, same, today=TODAY).empty


# -------------------------------------------------------------------- sync ----


class FakeGateway:
    def __init__(self) -> None:
        self.events: dict[str, tuple[str, EventSpec]] = {}
        self.calls: list[str] = []
        self.fail_next = False
        self.lists = 0

    async def ensure_calendar(self, name: str) -> str:
        self.calls.append(f"calendar {name}")
        return "cal"

    async def list_events(self, calendar_id: str) -> dict[str, ExistingEvent]:
        self.lists += 1
        return {
            key: ExistingEvent(eid, spec.fingerprint) for eid, (key, spec) in self.events.items()
        }

    async def create_event(self, calendar_id: str, spec: EventSpec) -> str:
        if self.fail_next:
            self.fail_next = False
            raise GraphError("boom", status_code=500)
        event_id = f"e{len(self.events) + len(self.calls)}"
        self.calls.append(f"create {spec.key}")
        self.events[event_id] = (spec.key, spec)
        return event_id

    async def update_event(self, event_id: str, spec: EventSpec) -> None:
        self.calls.append(f"update {spec.key}")
        self.events[event_id] = (spec.key, spec)

    async def delete_event(self, event_id: str) -> None:
        key, _spec = self.events.pop(event_id)
        self.calls.append(f"delete {key}")


@pytest.mark.asyncio
async def test_sync_writes_only_what_changed() -> None:
    gateway = FakeGateway()
    sync = OutlookCalendarSync(gateway, calendar_name="Planning Balz Métal")
    one = task("a", wall_time(MONDAY, 7), wall_time(MONDAY, 9))
    snap = snapshot(one)
    report = await sync.sync(snap, today=TODAY, calendar_for=calendar_resolver(snap))
    assert (report.created, report.updated, report.deleted) == (1, 0, 0)
    report = await sync.sync(snap, today=TODAY, calendar_for=calendar_resolver(snap))
    assert (report.created, report.updated, report.deleted) == (0, 0, 0)
    moved = snapshot(
        one.model_copy(update={"start": wall_time(MONDAY, 8), "end": wall_time(MONDAY, 10)})
    )
    report = await sync.sync(moved, today=TODAY, calendar_for=calendar_resolver(moved))
    assert report.updated == 1
    report = await sync.sync(snapshot(), today=TODAY, calendar_for=calendar_resolver(moved))
    assert report.deleted == 1
    assert gateway.events == {}
    assert gateway.lists == 1  # read once, then kept in memory


@pytest.mark.asyncio
async def test_a_failed_write_is_counted_and_the_calendar_read_again() -> None:
    gateway = FakeGateway()
    gateway.fail_next = True
    sync = OutlookCalendarSync(gateway, calendar_name="")
    snap = snapshot(task("a", wall_time(MONDAY, 7), wall_time(MONDAY, 9)))
    report = await sync.sync(snap, today=TODAY, calendar_for=calendar_resolver(snap))
    assert (report.created, report.failed) == (0, 1)
    assert "calendar Planning Balz Métal" in gateway.calls
    report = await sync.sync(snap, today=TODAY, calendar_for=calendar_resolver(snap))
    assert report.created == 1
    assert gateway.lists == 2


# ------------------------------------------------------------------- Graph ----


class FakeGraph:
    def __init__(self, pages: dict[str, dict[str, Any]]) -> None:
        self.pages = pages
        self.sent: list[tuple[str, str, Any]] = []

    async def get(self, path: str, **_: Any) -> dict[str, Any]:
        self.sent.append(("GET", path, None))
        for prefix, page in self.pages.items():
            if path.startswith(prefix):
                return page
        return {"value": []}

    async def post(self, path: str, *, json: Any = None, **_: Any) -> dict[str, Any]:
        self.sent.append(("POST", path, json))
        return {"id": "new"}

    async def patch(self, path: str, *, json: Any, **_: Any) -> dict[str, Any]:
        self.sent.append(("PATCH", path, json))
        return {}

    async def delete(self, path: str, **_: Any) -> dict[str, Any]:
        self.sent.append(("DELETE", path, None))
        raise GraphError("gone", status_code=404)


@pytest.mark.asyncio
async def test_gateway_finds_or_creates_the_calendar_and_reads_our_events() -> None:
    graph = FakeGraph(
        {
            "/me/calendars?": {"value": [{"id": "c1", "name": "planning balz métal"}]},
            "/me/calendars/c1/events": {
                "value": [
                    {
                        "id": "e1",
                        "singleValueExtendedProperties": [
                            {"id": KEY_PROPERTY, "value": "task:a:2026-11-09"},
                            {"id": FINGERPRINT_PROPERTY, "value": "f1"},
                        ],
                    },
                    {"id": "by-hand"},
                ]
            },
        }
    )
    gateway = OutlookCalendarGateway(graph)  # type: ignore[arg-type]
    assert await gateway.ensure_calendar("Planning Balz Métal") == "c1"
    assert await gateway.list_events("c1") == {"task:a:2026-11-09": ExistingEvent("e1", "f1")}
    assert await gateway.ensure_calendar("Autre") == "new"
    await gateway.delete_event("gone")  # already removed in Outlook: fine


def test_payload_has_swiss_time_no_reminder_and_tentative() -> None:
    timed = EventSpec(
        "k", "S", datetime(2026, 11, 9, 6, 30), datetime(2026, 11, 9, 12), tentative=True
    )
    payload = event_payload(timed)
    assert payload["start"] == {
        "dateTime": "2026-11-09T06:30:00",
        "timeZone": "W. Europe Standard Time",
    }
    assert payload["showAs"] == "tentative"
    assert payload["isReminderOn"] is False
    day = EventSpec("k", "S", datetime(2026, 11, 9), datetime(2026, 11, 14), all_day=True)
    assert event_payload(day)["end"]["dateTime"] == "2026-11-14T00:00:00"
    assert event_payload(day)["isAllDay"] is True
