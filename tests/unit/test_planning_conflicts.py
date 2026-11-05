from __future__ import annotations

from datetime import date

import pytest

from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.commands import calendar_resolver
from projectflow.core.planning.conflicts import (
    BarKey,
    Finding,
    findings_by_bar,
    format_day,
    format_duration,
    resource_load,
)
from projectflow.core.planning.models import (
    Absence,
    Closure,
    PlanningDocument,
    PlanningMeta,
    PlanningTask,
    RecurringOff,
    Resource,
)
from projectflow.core.planning.walltime import WallTime, wall_time

MON = date(2026, 10, 26)
TUE = date(2026, 10, 27)
WED = date(2026, 10, 28)
THU = date(2026, 10, 29)
FRI = date(2026, 10, 30)
SAT = date(2026, 10, 31)
NEXT_MON = date(2026, 11, 2)
CHRISTMAS = date(2026, 12, 25)


def at(day: date, hour: int, minute: int = 0) -> WallTime:
    return wall_time(day, hour, minute)


def make_task(
    task_id: str, start: WallTime, end: WallTime, *people: str, **fields: object
) -> PlanningTask:
    data: dict[str, object] = {
        "id": task_id,
        "title": task_id,
        "start": start,
        "end": end,
        "assignments": {person: {} for person in people},
    }
    return PlanningTask.model_validate(data | fields)


def absence(
    absence_id: str,
    resource_id: str,
    first_day: date,
    last_day: date | None = None,
    **fields: object,
) -> Absence:
    data: dict[str, object] = {
        "id": absence_id,
        "resource_id": resource_id,
        "first_day": first_day,
        "last_day": last_day or first_day,
    }
    return Absence.model_validate(data | fields)


def rows() -> list[Resource]:
    return [
        Resource(id="anna", name="Anna Favre"),
        Resource(id="bruno", name="Bruno Rossier"),
        Resource(id="chloe", name="Chloé Vaudoise", canton="VD"),
        Resource(
            id="leo",
            name="Léo Apprenti",
            apprentice=True,
            recurring_off=(RecurringOff(weekday=2, part="day", reason="ecole"),),
        ),
        Resource(
            id="mia",
            name="Mia Apprentie",
            apprentice=True,
            recurring_off=(RecurringOff(weekday=0, part="afternoon", reason="cie"),),
        ),
    ]


def snapshot(*documents: PlanningDocument, meta: PlanningMeta | None = None) -> PlanningSnapshot:
    return PlanningSnapshot(
        tasks={doc.id: doc for doc in documents if isinstance(doc, PlanningTask)},
        resources={doc.id: doc for doc in [*rows(), *documents] if isinstance(doc, Resource)},
        absences={doc.id: doc for doc in documents if isinstance(doc, Absence)},
        meta=meta or PlanningMeta(),
    )


def findings(
    *documents: PlanningDocument,
    first_day: date = MON,
    last_day: date = FRI,
    meta: PlanningMeta | None = None,
    project_exists: object = None,
) -> dict[BarKey, list[Finding]]:
    state = snapshot(*documents, meta=meta)
    return findings_by_bar(
        state,
        first_day,
        last_day,
        calendar_resolver(state),
        project_exists,  # type: ignore[arg-type]
    )


def messages(found: dict[BarKey, list[Finding]], key: BarKey, kind: str) -> list[str]:
    return [finding.message for finding in found.get(key, []) if finding.kind == kind]


# ------------------------------------------------------------ absences ----


def test_absence_during_a_task_is_reported_on_that_bar_only() -> None:
    found = findings(
        make_task("t", at(MON, 6, 30), at(WED, 16, 30), "anna", "bruno"),
        absence("vac", "anna", TUE, reason="vacances"),
    )
    assert found[("t", "anna")] == [
        Finding(kind="absence", task_id="t", resource_id="anna", message="Vacances le 27.10.2026")
    ]
    assert ("t", "bruno") not in found
    assert found[("t", "anna")][0].label == "Absence"


def test_absence_over_several_days_is_reported_once() -> None:
    found = findings(
        make_task("t", at(MON, 6, 30), at(FRI, 12), "anna"),
        absence("vac", "anna", TUE, THU, reason="vacances"),
    )
    assert messages(found, ("t", "anna"), "absence") == ["Vacances du 27.10.2026 au 29.10.2026"]


def test_half_day_absences_only_conflict_with_their_half() -> None:
    morning_off = absence("off", "anna", MON, part="morning")
    afternoon = make_task("pm", at(MON, 13), at(MON, 16, 30), "anna")
    morning = make_task("am", at(MON, 6, 30), at(MON, 8), "anna")
    found = findings(afternoon, morning, morning_off)
    assert ("pm", "anna") not in found
    assert messages(found, ("am", "anna"), "absence") == ["Absent le 26.10.2026 (matin)"]
    friday = findings(
        make_task("fri", at(FRI, 6, 30), at(FRI, 12), "anna"),
        absence("off", "anna", FRI, part="afternoon"),
    )
    assert friday == {}


def test_absence_on_non_worked_days_is_ignored() -> None:
    found = findings(
        make_task("t", at(date(2026, 12, 24), 6, 30), at(date(2026, 12, 28), 12), "anna"),
        absence("holiday", "anna", CHRISTMAS, date(2026, 12, 27), reason="conge"),
        first_day=date(2026, 12, 21),
        last_day=date(2026, 12, 31),
    )
    assert found == {}


def test_deleted_absences_are_ignored() -> None:
    found = findings(
        make_task("t", at(MON, 6, 30), at(MON, 16, 30), "anna"),
        absence("vac", "anna", MON, deleted_at="2026-10-20T08:00:00Z"),
    )
    assert found == {}


def test_recurring_school_day_conflicts_with_tasks_over_that_weekday() -> None:
    week = make_task("week", at(MON, 6, 30), at(FRI, 12), "leo", "anna")
    found = findings(week)
    assert messages(found, ("week", "leo"), "absence") == ["École professionnelle chaque mercredi"]
    assert ("week", "anna") not in found
    thursday = make_task("thu", at(THU, 6, 30), at(THU, 16, 30), "leo")
    assert findings(thursday) == {}


def test_recurring_afternoon_off_spares_morning_tasks() -> None:
    morning = make_task("am", at(MON, 6, 30), at(MON, 12), "mia")
    afternoon = make_task("pm", at(MON, 13), at(MON, 14), "mia")
    found = findings(morning, afternoon)
    assert ("am", "mia") not in found
    assert messages(found, ("pm", "mia"), "absence") == ["Cours CIE chaque lundi (après-midi)"]


def test_recurring_day_and_absence_on_the_same_day_are_both_listed() -> None:
    found = findings(
        make_task("t", at(WED, 6, 30), at(WED, 16, 30), "leo"),
        absence("sick", "leo", WED, reason="absent"),
    )
    assert messages(found, ("t", "leo"), "absence") == [
        "Absent le 28.10.2026",
        "École professionnelle chaque mercredi",
    ]


# --------------------------------------------------------- non worked ----


def test_task_starting_on_a_holiday() -> None:
    found = findings(
        make_task("t", at(CHRISTMAS, 6, 30), at(CHRISTMAS, 12), "anna"),
        first_day=date(2026, 12, 21),
        last_day=CHRISTMAS,
    )
    assert messages(found, ("t", "anna"), "non_worked") == [
        "Début un jour férié : Noël (25.12.2026)"
    ]


def test_holidays_follow_the_canton_of_each_row() -> None:
    saint_berchtold = date(2026, 1, 2)
    found = findings(
        make_task("t", at(saint_berchtold, 6, 30), at(saint_berchtold, 12), "anna", "chloe"),
        first_day=saint_berchtold,
        last_day=saint_berchtold,
    )
    assert ("t", "anna") not in found
    assert messages(found, ("t", "chloe"), "non_worked") == [
        "Début un jour férié : Saint-Berchtold (02.01.2026)"
    ]


def test_task_starting_during_a_closure_on_a_weekend_or_at_lunch() -> None:
    meta = PlanningMeta(closures=(Closure(first_day=MON, last_day=TUE),))
    closed = findings(make_task("t", at(MON, 6, 30), at(MON, 12), "anna"), meta=meta)
    assert messages(closed, ("t", "anna"), "non_worked") == [
        "Début pendant la fermeture de l'entreprise (26.10.2026)"
    ]
    weekend = findings(
        make_task("t", at(SAT, 6, 30), at(SAT, 12), "anna"), first_day=MON, last_day=SAT
    )
    assert messages(weekend, ("t", "anna"), "non_worked") == [
        "Début un samedi non travaillé (31.10.2026)"
    ]
    lunch = findings(make_task("t", at(MON, 12), at(MON, 14), "anna"))
    assert messages(lunch, ("t", "anna"), "non_worked") == ["Début hors des horaires de travail"]


def test_unassigned_bar_uses_the_default_calendar() -> None:
    found = findings(
        make_task("t", at(CHRISTMAS, 6, 30), at(CHRISTMAS, 12)),
        first_day=CHRISTMAS,
        last_day=CHRISTMAS,
    )
    assert [finding.kind for finding in found[("t", None)]] == ["non_worked", "missing_people"]


# ----------------------------------------------------------- overload ----


def test_two_full_days_on_the_same_person_overload_both_bars() -> None:
    found = findings(
        make_task("a", at(MON, 6, 30), at(MON, 16, 30), "anna"),
        make_task("b", at(MON, 6, 30), at(MON, 16, 30), "anna", "bruno"),
    )
    expected = "Surcharge le 26.10.2026 : 18 h planifiées pour 9 h"
    assert messages(found, ("a", "anna"), "overload") == [expected]
    assert messages(found, ("b", "anna"), "overload") == [expected]
    assert ("b", "bruno") not in found


def test_a_subcontractor_is_never_overloaded() -> None:
    galva = Resource(id="galva", name="Galvanisation", kind_of_resource="subcontractor")
    found = findings(
        galva,
        make_task("a", at(MON, 6, 30), at(MON, 16, 30), "galva"),
        make_task("b", at(MON, 6, 30), at(MON, 16, 30), "galva"),
        make_task("c", at(MON, 6, 30), at(MON, 16, 30), "galva"),
    )
    assert messages(found, ("a", "galva"), "overload") == []
    assert messages(found, ("c", "galva"), "overload") == []


def test_tasks_within_one_day_of_work_are_not_an_overload() -> None:
    disjoint = findings(
        make_task("a", at(MON, 6, 30), at(MON, 12), "anna"),
        make_task("b", at(MON, 13), at(MON, 16, 30), "anna"),
    )
    assert disjoint == {}
    partly_parallel = findings(
        make_task("a", at(MON, 6, 30), at(MON, 12), "anna"),
        make_task("b", at(MON, 10), at(MON, 12), "anna"),
    )
    assert partly_parallel == {}


def test_friday_capacity_is_a_half_day() -> None:
    found = findings(
        make_task("a", at(FRI, 6, 30), at(FRI, 12), "anna"),
        make_task("b", at(FRI, 11), at(FRI, 12), "anna"),
    )
    assert messages(found, ("b", "anna"), "overload") == [
        "Surcharge le 30.10.2026 : 6 h 30 planifiées pour 5 h 30"
    ]


def test_overload_over_several_days_names_the_first_one() -> None:
    found = findings(
        make_task("a", at(MON, 6, 30), at(WED, 16, 30), "anna"),
        make_task("b", at(MON, 6, 30), at(TUE, 16, 30), "anna"),
    )
    assert messages(found, ("a", "anna"), "overload") == [
        "Surcharge le 26.10.2026 : 18 h planifiées pour 9 h (et 1 autre jour)"
    ]


def test_overload_outside_the_period_still_marks_a_bar_crossing_it() -> None:
    found = findings(
        make_task("long", at(MON, 6, 30), at(TUE, 16, 30), "anna"),
        make_task("monday", at(MON, 6, 30), at(MON, 16, 30), "anna"),
        first_day=TUE,
        last_day=TUE,
    )
    assert messages(found, ("long", "anna"), "overload") == [
        "Surcharge le 26.10.2026 : 18 h planifiées pour 9 h"
    ]
    assert ("monday", "anna") not in found


# ------------------------------------------------------- task findings ----


def test_missing_people_on_every_bar_and_on_the_unassigned_row() -> None:
    found = findings(
        make_task("two", at(MON, 6, 30), at(MON, 12), "anna", required_people=2),
        make_task("nobody", at(TUE, 6, 30), at(TUE, 12)),
        make_task("crew", at(WED, 6, 30), at(WED, 12), required_people=3),
    )
    assert found[("two", "anna")] == [
        Finding(
            kind="missing_people",
            task_id="two",
            resource_id="anna",
            message="1 personne manquante sur 2 requises",
        )
    ]
    assert messages(found, ("nobody", None), "missing_people") == [
        "1 personne manquante sur 1 requise"
    ]
    assert messages(found, ("crew", None), "missing_people") == [
        "3 personnes manquantes sur 3 requises"
    ]


def test_unknown_dossier_is_reported_on_each_bar() -> None:
    known = {"2026-0001"}
    found = findings(
        make_task("bad", at(MON, 6, 30), at(MON, 12), "anna", "bruno", project_number="2026-9999"),
        make_task("good", at(TUE, 6, 30), at(TUE, 12), "anna", project_number="2026-0001"),
        make_task("none", at(WED, 6, 30), at(WED, 12), "anna"),
        project_exists=known.__contains__,
    )
    assert set(found) == {("bad", "anna"), ("bad", "bruno")}
    assert messages(found, ("bad", "bruno"), "unknown_project") == ["Dossier 2026-9999 introuvable"]
    unchecked = findings(
        make_task("bad", at(MON, 6, 30), at(MON, 12), "anna", project_number="2026-9999")
    )
    assert unchecked == {}


def test_findings_keep_a_stable_order_on_a_bar() -> None:
    found = findings(
        make_task(
            "t", at(CHRISTMAS, 6, 30), at(date(2026, 12, 28), 16, 30), "anna", required_people=2
        ),
        make_task("u", at(date(2026, 12, 28), 6, 30), at(date(2026, 12, 28), 16, 30), "anna"),
        absence("vac", "anna", date(2026, 12, 28)),
        first_day=date(2026, 12, 21),
        last_day=date(2026, 12, 31),
        project_exists=lambda _number: False,
    )
    kinds = [finding.kind for finding in found[("t", "anna")]]
    assert kinds == ["absence", "non_worked", "overload", "missing_people"]


def test_only_live_scheduled_tasks_of_the_period_are_considered() -> None:
    found = findings(
        make_task("next", at(NEXT_MON, 6, 30), at(NEXT_MON, 12), required_people=2),
        make_task("gone", at(MON, 6, 30), at(MON, 12), required_people=2, deleted_at="x"),
        PlanningTask(id="card", title="card", required_people=3),
        make_task("edge", at(FRI, 6, 30), at(NEXT_MON, 10), required_people=2),
    )
    assert set(found) == {("edge", None)}


# -------------------------------------------------------------- load ----


@pytest.mark.parametrize(
    ("documents", "available"),
    [
        ((), 2490),
        ((absence("a", "anna", TUE),), 2490 - 540),
        ((absence("a", "anna", MON, part="morning"),), 2490 - 330),
        ((absence("a", "anna", FRI, part="afternoon"),), 2490),
        (
            (
                absence("a", "anna", MON, part="morning"),
                absence("b", "anna", MON, part="afternoon"),
            ),
            1950,
        ),
        ((absence("a", "anna", SAT, date(2026, 11, 1)),), 2490),
    ],
)
def test_resource_load_availability(documents: tuple[Absence, ...], available: int) -> None:
    state = snapshot(*documents)
    assert resource_load(state, "anna", MON, FRI, calendar_resolver(state)("anna")) == (
        0,
        available,
    )


def test_resource_load_counts_recurring_days_off_once() -> None:
    state = snapshot(absence("sick", "leo", WED))
    calendar = calendar_resolver(state)("leo")
    assert resource_load(state, "leo", MON, FRI, calendar) == (0, 2490 - 540)


def test_resource_load_with_holidays() -> None:
    state = snapshot()
    calendar = calendar_resolver(state)("anna")
    christmas_week = resource_load(state, "anna", date(2026, 12, 21), CHRISTMAS, calendar)
    assert christmas_week == (0, 2490 - 330)


def test_resource_load_planned_minutes_are_clipped_to_the_period() -> None:
    state = snapshot(
        make_task("a", at(MON, 6, 30), at(TUE, 12), "anna"),
        make_task("b", at(FRI, 6, 30), at(NEXT_MON, 10), "anna", "bruno"),
        make_task("c", at(NEXT_MON, 6, 30), at(NEXT_MON, 12), "anna"),
        make_task("d", at(WED, 6, 30), at(WED, 12), "bruno"),
        make_task("e", at(WED, 6, 30), at(WED, 12), "anna", deleted_at="x"),
        absence("vac", "anna", MON),
    )
    calendar = calendar_resolver(state)("anna")
    assert resource_load(state, "anna", MON, FRI, calendar) == (870 + 330, 2490 - 540)


@pytest.mark.parametrize(
    ("minutes", "text"),
    [(540, "9 h"), (810, "13 h 30"), (30, "30 min"), (390, "6 h 30"), (0, "0 min")],
)
def test_format_duration(minutes: int, text: str) -> None:
    assert format_duration(minutes) == text


def test_format_day() -> None:
    assert format_day(date(2026, 1, 2)) == "02.01.2026"


def test_tasks_of_the_same_person_elsewhere_do_not_count_as_overload() -> None:
    found = findings(
        make_task("now", at(MON, 6, 30), at(MON, 16, 30), "anna"),
        make_task("later", at(NEXT_MON, 6, 30), at(NEXT_MON, 16, 30), "anna"),
        make_task("before", at(date(2026, 10, 19), 6, 30), at(date(2026, 10, 19), 16, 30), "anna"),
    )
    assert found == {}
