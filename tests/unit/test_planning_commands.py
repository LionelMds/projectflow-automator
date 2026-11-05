from __future__ import annotations

import random
from collections.abc import Iterable
from datetime import date

import pytest
from pydantic import ValidationError

from projectflow.core.planning.calendar import WorkCalendar
from projectflow.core.planning.changes import DocumentChange, PlanningSnapshot
from projectflow.core.planning.commands import (
    DEFAULT_TASK_MINUTES,
    ORDER_STEP,
    UNASSIGNED,
    CommandContext,
    PlanningCommandError,
    archive_resource,
    assign,
    calendar_resolver,
    card_days,
    create_absence,
    create_resource,
    create_task,
    delete_absence,
    delete_task,
    detach_assignment,
    duplicate_task,
    move_backlog,
    move_task,
    reorder_resource,
    resize_task,
    restore_task,
    schedule_task,
    set_status,
    shift_tasks,
    task_days,
    unassign,
    unschedule_task,
    update_absence,
    update_meta,
    update_resource,
    update_task,
)
from projectflow.core.planning.models import (
    Absence,
    Author,
    Closure,
    PlanningDocument,
    PlanningMeta,
    PlanningTask,
    ProjectCache,
    RecurringOff,
    Resource,
    Team,
)
from projectflow.core.planning.ranks import initial_ranks, is_rank, rank_between
from projectflow.core.planning.walltime import WallTime, wall_time

AUTHOR = Author(initials="LM", name="Lionel Mottiez", poste="Bureau 1", poste_id="poste-1")
EARLIER = "2026-10-01T08:00:00Z"
NOW = "2026-10-22T10:00:00Z"
OLD_AUTHOR = Author(initials="RB", poste="Atelier")

MON = date(2026, 10, 26)
TUE = date(2026, 10, 27)
WED = date(2026, 10, 28)
THU = date(2026, 10, 29)
FRI = date(2026, 10, 30)
SAT = date(2026, 10, 31)
NEXT_MON = date(2026, 11, 2)


def at(day: date, hour: int, minute: int = 0) -> WallTime:
    return wall_time(day, hour, minute)


def make_resource(resource_id: str, name: str, **fields: object) -> Resource:
    data: dict[str, object] = {
        "id": resource_id,
        "name": name,
        "created_at": EARLIER,
        "created_by": OLD_AUTHOR,
        "updated_at": EARLIER,
        "updated_by": OLD_AUTHOR,
    }
    return Resource.model_validate(data | fields)


def make_task(task_id: str, **fields: object) -> PlanningTask:
    people = fields.pop("people", ())
    assert isinstance(people, tuple)
    data: dict[str, object] = {
        "id": task_id,
        "title": f"Tache {task_id}",
        "created_at": EARLIER,
        "created_by": OLD_AUTHOR,
        "updated_at": EARLIER,
        "updated_by": OLD_AUTHOR,
        "assignments": {person: {} for person in people},
    }
    return PlanningTask.model_validate(data | fields)


def make_snapshot(
    *documents: PlanningDocument, meta: PlanningMeta | None = None
) -> PlanningSnapshot:
    return PlanningSnapshot(
        tasks={doc.id: doc for doc in documents if isinstance(doc, PlanningTask)},
        resources={doc.id: doc for doc in documents if isinstance(doc, Resource)},
        absences={doc.id: doc for doc in documents if isinstance(doc, Absence)},
        meta=meta or PlanningMeta(),
    )


def make_ctx(snapshot: PlanningSnapshot) -> CommandContext:
    return CommandContext(
        snapshot=snapshot,
        author=AUTHOR,
        now=NOW,
        calendar_for=calendar_resolver(snapshot),
    )


def people() -> list[Resource]:
    return [
        make_resource("anna", "Anna Favre", order=1000),
        make_resource("bruno", "Bruno Rossier", order=2000),
        make_resource("chloe", "Chloé Vaudoise", canton="VD", order=3000),
        make_resource("dylan", "Dylan Archive", active=False, order=4000),
        make_resource("emma", "Emma Bureau", team="bureau", order=1000),
    ]


def ctx_with(*documents: PlanningDocument, meta: PlanningMeta | None = None) -> CommandContext:
    return make_ctx(make_snapshot(*people(), *documents, meta=meta))


def only(changes: Iterable[DocumentChange], doc_id: str) -> PlanningDocument:
    found = [change.after for change in changes if change.doc_id == doc_id]
    assert len(found) == 1
    document = found[0]
    assert document is not None
    return document


def task_after(changes: Iterable[DocumentChange], doc_id: str) -> PlanningTask:
    document = only(changes, doc_id)
    assert isinstance(document, PlanningTask)
    return document


def first_task(changes: tuple[DocumentChange, ...]) -> PlanningTask:
    document = changes[0].after
    assert isinstance(document, PlanningTask)
    return document


def dump(snapshot: PlanningSnapshot) -> dict[str, object]:
    return {
        "tasks": {key: doc.to_json() for key, doc in snapshot.tasks.items()},
        "resources": {key: doc.to_json() for key, doc in snapshot.resources.items()},
        "absences": {key: doc.to_json() for key, doc in snapshot.absences.items()},
        "meta": snapshot.meta.to_json(),
    }


# ---------------------------------------------------------------- ranks ----


def test_rank_between_open_ends_and_middle() -> None:
    first = rank_between(None, None)
    after = rank_between(first, None)
    before = rank_between(None, first)
    middle = rank_between(before, first)
    assert before < middle < first < after
    assert all(is_rank(rank) for rank in (first, after, before, middle))


def test_rank_between_keeps_order_under_random_insertions() -> None:
    rng = random.Random(118)  # noqa: S311 - reproducible order, not security
    ranks: list[str] = []
    for _ in range(3000):
        position = rng.randint(0, len(ranks))
        low = ranks[position - 1] if position else None
        high = ranks[position] if position < len(ranks) else None
        rank = rank_between(low, high)
        assert is_rank(rank)
        assert low is None or low < rank
        assert high is None or rank < high
        ranks.insert(position, rank)
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(ranks)


def test_rank_between_appends_and_prepends_stay_short() -> None:
    appended: list[str] = []
    prepended: list[str] = []
    for _ in range(500):
        appended.append(rank_between(appended[-1] if appended else None, None))
        prepended.insert(0, rank_between(None, prepended[0] if prepended else None))
    assert appended == sorted(appended)
    assert prepended == sorted(prepended)
    assert max(len(rank) for rank in appended + prepended) <= 16


def test_rank_between_rejects_unusable_bounds() -> None:
    with pytest.raises(ValueError, match="desordre"):
        rank_between("k", "c")
    with pytest.raises(ValueError, match="desordre"):
        rank_between("k", "k")
    with pytest.raises(ValueError, match="invalide"):
        rank_between(None, "")
    with pytest.raises(ValueError, match="invalide"):
        rank_between("A", None)
    with pytest.raises(ValueError, match="invalide"):
        rank_between(None, "i0")


@pytest.mark.parametrize("count", [0, 1, 2, 35, 36, 37, 500])
def test_initial_ranks_are_sorted_distinct_and_valid(count: int) -> None:
    ranks = initial_ranks(count)
    assert len(ranks) == count
    assert list(ranks) == sorted(ranks)
    assert len(set(ranks)) == count
    assert all(is_rank(rank) for rank in ranks)
    if count:
        assert rank_between(None, ranks[0]) < ranks[0]


# --------------------------------------------------------- create task ----


def test_create_task_without_start_joins_the_end_of_the_backlog() -> None:
    ctx = ctx_with(
        make_task("old", backlog_rank="i"),
        make_task("planned", start=at(MON, 6, 30), end=at(MON, 16, 30), people=("anna",)),
    )
    changes = create_task(
        ctx,
        title="  Garde-corps  ",
        task_type="soudure",
        project_number="2026-5245",
        resource_ids=("anna", "bruno", "anna"),
        estimate_minutes=600,
        origin="quick_entry",
    )
    assert len(changes) == 1
    created = first_task(changes)
    assert changes[0].is_creation
    assert created.title == "Garde-corps"
    assert not created.scheduled
    assert created.assignee_ids == ("anna", "bruno")
    assert created.backlog_rank > "i"
    assert created.estimate_minutes == 600
    assert created.origin == "quick_entry"
    assert created.created_by == AUTHOR
    assert created.updated_at == NOW
    backlog = ctx.snapshot.apply(changes).backlog()
    assert [task.id for task in backlog] == ["old", created.id]


def test_create_task_with_start_lasts_its_estimate_on_worked_time() -> None:
    ctx = ctx_with()
    full_day = first_task(create_task(ctx, title="A", task_type="debit", start=at(MON, 6, 30)))
    assert (full_day.start, full_day.end) == (at(MON, 6, 30), at(MON, 16, 30))
    longer = first_task(
        create_task(ctx, title="B", task_type="debit", start=at(THU, 6, 30), estimate_minutes=870)
    )
    assert longer.end == at(FRI, 12)
    rounded = first_task(
        create_task(ctx, title="C", task_type="debit", start=at(MON, 11), estimate_minutes=45)
    )
    assert rounded.end == at(MON, 12)


def test_create_task_snaps_and_moves_starts_onto_worked_time() -> None:
    ctx = ctx_with()
    snapped = first_task(
        create_task(ctx, title="A", task_type="debit", start=at(MON, 6, 40), end=at(MON, 9, 50))
    )
    assert (snapped.start, snapped.end) == (at(MON, 6, 30), at(MON, 10))
    lunch = first_task(
        create_task(ctx, title="B", task_type="debit", start=at(MON, 12, 10), end=at(MON, 15))
    )
    assert lunch.start == at(MON, 13)
    weekend = first_task(create_task(ctx, title="C", task_type="debit", start=at(SAT, 9)))
    assert (weekend.start, weekend.end) == (at(NEXT_MON, 6, 30), at(NEXT_MON, 16, 30))


def test_create_task_uses_the_canton_of_the_first_person() -> None:
    ctx = ctx_with()
    geneva = first_task(
        create_task(
            ctx,
            title="A",
            task_type="montage",
            start=at(date(2026, 9, 10), 6, 30),
            resource_ids=("anna",),
        )
    )
    vaud = first_task(
        create_task(
            ctx,
            title="B",
            task_type="montage",
            start=at(date(2026, 9, 10), 6, 30),
            resource_ids=("chloe",),
        )
    )
    assert geneva.start == at(date(2026, 9, 11), 6, 30)
    assert vaud.start == at(date(2026, 9, 10), 6, 30)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"end": at(MON, 9)}, "besoin d'un debut"),
        ({"start": at(MON, 10), "end": at(MON, 9)}, "fin doit suivre"),
        ({"start": at(MON, 10), "end": at(MON, 10)}, "fin doit suivre"),
        ({"task_type": "inconnu"}, "Type de tache inconnu"),
        ({"resource_ids": ("nobody",)}, "introuvable"),
        ({"resource_ids": ("dylan",)}, "archivee"),
        ({"required_people": 0}, "required_people"),
        ({"status": "lost"}, "status"),
    ],
)
def test_create_task_refuses_impossible_tasks(fields: dict[str, object], message: str) -> None:
    arguments: dict[str, object] = {"title": "A", "task_type": "debit"} | fields
    with pytest.raises(PlanningCommandError, match=message):
        create_task(ctx_with(), **arguments)  # type: ignore[arg-type]


# --------------------------------------------------------- schedule ----


def test_schedule_task_lasts_one_worked_day_without_estimate() -> None:
    ctx = ctx_with(make_task("card", backlog_rank="i", people=("anna",)))
    task = task_after(
        schedule_task(ctx, "card", start=at(FRI, 6, 30), resource_ids=("bruno",)), "card"
    )
    assert task.start == at(FRI, 6, 30)
    assert task.end == at(NEXT_MON, 10)
    assert task.assignee_ids == ("bruno",)
    assert ctx.snapshot.tasks["card"].assignee_ids == ("anna",)


def test_schedule_task_keeps_people_foreseen_without_resource_ids() -> None:
    ctx = ctx_with(make_task("card", estimate_minutes=870, people=("chloe", "anna")))
    task = task_after(schedule_task(ctx, "card", start=at(THU, 6, 30), resource_ids=()), "card")
    assert task.assignee_ids == ("chloe", "anna")
    assert task.end == at(FRI, 12)
    assert task.estimate_minutes == 870


def test_schedule_task_on_the_unassigned_row_and_without_archived_people() -> None:
    # Regression: (UNASSIGNED,) kept the people foreseen, and an archived person foreseen
    # put the task on a hidden row.
    ctx = ctx_with(
        make_task("card", backlog_rank="i", people=("anna",)),
        make_task("mixed", backlog_rank="j", people=("dylan", "bruno")),
        make_task("gone", backlog_rank="k", people=("dylan",)),
    )
    free = task_after(
        schedule_task(ctx, "card", start=at(MON, 6, 30), resource_ids=(UNASSIGNED,)), "card"
    )
    assert free.scheduled
    assert free.assignments == {}
    mixed = task_after(schedule_task(ctx, "mixed", start=at(MON, 6, 30), resource_ids=()), "mixed")
    assert mixed.assignee_ids == ("bruno",)
    gone = task_after(schedule_task(ctx, "gone", start=at(MON, 6, 30), resource_ids=()), "gone")
    assert gone.scheduled
    assert gone.assignments == {}
    assert (gone.start, gone.end) == (at(MON, 6, 30), at(MON, 16, 30))


def test_duplicate_task_leaves_archived_people_out() -> None:
    ctx = ctx_with(
        make_task("t", start=at(MON, 6, 30), end=at(MON, 12), people=("dylan", "anna")),
        make_task("card", backlog_rank="i", people=("dylan",)),
    )
    assert first_task(duplicate_task(ctx, "t")).assignee_ids == ("anna",)
    assert first_task(duplicate_task(ctx, "card")).assignments == {}
    unassigned = first_task(duplicate_task(ctx, "t", resource_ids=(UNASSIGNED,)))
    assert unassigned.assignments == {}
    assert unassigned.scheduled


def test_schedule_task_with_explicit_end() -> None:
    ctx = ctx_with(make_task("card"))
    task = task_after(
        schedule_task(ctx, "card", start=at(MON, 6, 30), end=at(TUE, 12), resource_ids=("anna",)),
        "card",
    )
    assert (task.start, task.end) == (at(MON, 6, 30), at(TUE, 12))


def test_schedule_task_refuses_a_deleted_card() -> None:
    ctx = ctx_with(make_task("card", deleted_at=EARLIER))
    with pytest.raises(PlanningCommandError, match="supprimee"):
        schedule_task(ctx, "card", start=at(MON, 6, 30), resource_ids=())
    with pytest.raises(PlanningCommandError, match="introuvable"):
        schedule_task(ctx, "missing", start=at(MON, 6, 30), resource_ids=())


# ------------------------------------------------------------- move ----


def test_move_task_from_friday_keeps_worked_minutes_over_the_weekend() -> None:
    ctx = ctx_with(make_task("t", start=at(MON, 6, 30), end=at(MON, 16, 30), people=("anna",)))
    task = task_after(move_task(ctx, "t", new_start=at(FRI, 6, 30)), "t")
    assert (task.start, task.end) == (at(FRI, 6, 30), at(NEXT_MON, 10))


def test_move_task_from_thursday_afternoon_to_friday_morning() -> None:
    ctx = ctx_with(make_task("t", start=at(THU, 13), end=at(FRI, 12), people=("anna",)))
    task = task_after(move_task(ctx, "t", new_start=at(FRI, 6, 30)), "t")
    assert (task.start, task.end) == (at(FRI, 6, 30), at(NEXT_MON, 10))
    back = task_after(move_task(ctx, "t", new_start=at(WED, 6, 30)), "t")
    assert (back.start, back.end) == (at(WED, 6, 30), at(WED, 16, 30))


def test_move_task_skips_christmas() -> None:
    christmas_eve = date(2026, 12, 24)
    ctx = ctx_with(
        make_task(
            "t", start=at(christmas_eve, 6, 30), end=at(christmas_eve, 16, 30), people=("anna",)
        )
    )
    task = task_after(move_task(ctx, "t", new_start=at(christmas_eve, 13)), "t")
    assert (task.start, task.end) == (at(christmas_eve, 13), at(date(2026, 12, 28), 12))


def test_move_task_vertically_uses_the_calendar_of_the_target_person() -> None:
    wednesday = date(2026, 9, 9)
    ctx = ctx_with(
        make_task("t", start=at(wednesday, 6, 30), end=at(date(2026, 9, 11), 12), people=("anna",))
    )
    task = task_after(
        move_task(
            ctx, "t", new_start=at(wednesday, 6, 30), from_resource="anna", to_resource="chloe"
        ),
        "t",
    )
    # 870 worked minutes in Geneva (Jeune genevois on Thursday); Vaud works that Thursday.
    assert task.end == at(date(2026, 9, 10), 12)
    assert task.assignee_ids == ("chloe",)


def test_move_task_measures_with_the_bar_calendar_whatever_row_is_grabbed() -> None:
    # Regression: the grabbed row's calendar measured the bar, so a shared GE/VD task
    # grew by a day when its VD person was replaced, and drags depended on the row.
    tuesday, wednesday, friday = date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 11)
    ctx = ctx_with(
        make_task("t", start=at(wednesday, 6, 30), end=at(friday, 12), people=("anna", "chloe"))
    )
    replaced = task_after(
        move_task(
            ctx, "t", new_start=at(wednesday, 6, 30), from_resource="chloe", to_resource="bruno"
        ),
        "t",
    )
    assert replaced.assignee_ids == ("anna", "bruno")
    assert (replaced.start, replaced.end) == (at(wednesday, 6, 30), at(friday, 12))
    for row in ("anna", "chloe"):
        dragged = task_after(
            move_task(ctx, "t", new_start=at(tuesday, 6, 30), from_resource=row), "t"
        )
        assert (dragged.start, dragged.end) == (at(tuesday, 6, 30), at(wednesday, 12)), row
    first_replaced = task_after(
        move_task(
            ctx, "t", new_start=at(wednesday, 6, 30), from_resource="anna", to_resource="emma"
        ),
        "t",
    )
    assert first_replaced.assignee_ids == ("emma", "chloe")
    assert first_replaced.end == at(friday, 12)


def test_move_task_vertical_replaces_only_the_dragged_person() -> None:
    ctx = ctx_with(
        make_task(
            "t",
            start=at(MON, 6, 30),
            end=at(MON, 16, 30),
            people=("anna", "bruno"),
            assignments={"anna": {"note": "chef"}, "bruno": {}},
        )
    )
    task = task_after(
        move_task(ctx, "t", new_start=at(TUE, 6, 30), from_resource="bruno", to_resource="chloe"),
        "t",
    )
    assert task.assignments == {"anna": {"note": "chef"}, "chloe": {}}
    assert (task.start, task.end) == (at(TUE, 6, 30), at(TUE, 16, 30))
    replaced_first = task_after(
        move_task(ctx, "t", new_start=at(MON, 6, 30), from_resource="anna", to_resource="emma"),
        "t",
    )
    assert replaced_first.assignee_ids == ("emma", "bruno")
    assert replaced_first.assignments["emma"] == {"note": "chef"}


def test_move_task_onto_someone_already_assigned_changes_nothing() -> None:
    ctx = ctx_with(
        make_task("t", start=at(MON, 6, 30), end=at(MON, 16, 30), people=("anna", "bruno"))
    )
    assert (
        move_task(ctx, "t", new_start=at(TUE, 6, 30), from_resource="anna", to_resource="bruno")
        == ()
    )


def test_move_task_to_and_from_the_unassigned_row() -> None:
    ctx = ctx_with(
        make_task("t", start=at(MON, 6, 30), end=at(MON, 16, 30), people=("anna",)),
        make_task("free", start=at(MON, 6, 30), end=at(MON, 12)),
    )
    unassigned = task_after(
        move_task(ctx, "t", new_start=at(MON, 6, 30), from_resource="anna", to_resource=UNASSIGNED),
        "t",
    )
    assert unassigned.scheduled
    assert unassigned.assignments == {}
    taken = task_after(
        move_task(
            ctx, "free", new_start=at(TUE, 6, 30), from_resource=UNASSIGNED, to_resource="bruno"
        ),
        "free",
    )
    assert taken.assignee_ids == ("bruno",)
    assert (taken.start, taken.end) == (at(TUE, 6, 30), at(TUE, 12))
    with pytest.raises(PlanningCommandError, match="Non affecte"):
        move_task(ctx, "t", new_start=at(MON, 6, 30), from_resource=UNASSIGNED, to_resource="bruno")


def test_move_task_refusals_and_no_change() -> None:
    ctx = ctx_with(
        make_task("t", start=at(MON, 6, 30), end=at(MON, 16, 30), people=("anna",)),
        make_task("card"),
    )
    assert move_task(ctx, "t", new_start=at(MON, 6, 40)) == ()
    with pytest.raises(PlanningCommandError, match="pas planifiee"):
        move_task(ctx, "card", new_start=at(MON, 6, 30))
    with pytest.raises(PlanningCommandError, match="pas affectee"):
        move_task(ctx, "t", new_start=at(MON, 6, 30), from_resource="bruno", to_resource="chloe")
    with pytest.raises(PlanningCommandError, match="archivee"):
        move_task(ctx, "t", new_start=at(MON, 6, 30), from_resource="anna", to_resource="dylan")


# ----------------------------------------------------------- resize ----


def test_resize_task_edges_snap_onto_worked_time() -> None:
    ctx = ctx_with(make_task("t", start=at(MON, 6, 30), end=at(TUE, 16, 30), people=("anna",)))
    shorter = task_after(resize_task(ctx, "t", end=at(TUE, 12, 40)), "t")
    assert shorter.end == at(TUE, 12)
    later = task_after(resize_task(ctx, "t", start=at(MON, 12, 20)), "t")
    assert later.start == at(MON, 13)
    longer = task_after(resize_task(ctx, "t", end=at(SAT, 10)), "t")
    assert longer.end == at(FRI, 12)
    assert resize_task(ctx, "t") == ()


def test_resize_task_keeps_at_least_thirty_minutes() -> None:
    # Regression: an edge dragged onto or past the other one raised instead of stopping.
    ctx = ctx_with(make_task("t", start=at(MON, 13), end=at(MON, 16, 30), people=("anna",)))
    assert task_after(resize_task(ctx, "t", end=at(MON, 12, 30)), "t").end == at(MON, 13, 30)
    assert task_after(resize_task(ctx, "t", end=at(MON, 6)), "t").end == at(MON, 13, 30)
    assert task_after(resize_task(ctx, "t", start=at(MON, 16, 30)), "t").start == at(MON, 16)
    assert task_after(resize_task(ctx, "t", start=at(TUE, 9)), "t").start == at(MON, 16)
    minimal = task_after(resize_task(ctx, "t", end=at(MON, 13, 30)), "t")
    assert minimal.end == at(MON, 13, 30)
    morning = ctx_with(make_task("m", start=at(MON, 6, 30), end=at(MON, 8), people=("anna",)))
    assert task_after(resize_task(morning, "m", end=at(MON, 6, 30)), "m").end == at(MON, 7)
    afternoon = ctx_with(make_task("a", start=at(MON, 13), end=at(MON, 14), people=("anna",)))
    assert task_after(resize_task(afternoon, "a", start=at(MON, 14)), "a").start == at(MON, 13, 30)
    with pytest.raises(PlanningCommandError, match="fin doit suivre"):
        resize_task(ctx, "t", start=at(MON, 15), end=at(MON, 14))
    both = task_after(resize_task(ctx, "t", start=at(MON, 11, 30), end=at(MON, 13, 10)), "t")
    assert (both.start, both.end) == (at(MON, 11, 30), at(MON, 12))


# ---------------------------------------------------- assign / unassign ----


def test_assign_and_unassign_a_second_person() -> None:
    ctx = ctx_with(make_task("t", start=at(MON, 6, 30), end=at(MON, 16, 30), people=("anna",)))
    assigned = task_after(assign(ctx, "t", "bruno"), "t")
    assert assigned.assignee_ids == ("anna", "bruno")
    assert assign(ctx, "t", "anna") == ()
    with pytest.raises(PlanningCommandError, match="archivee"):
        assign(ctx, "t", "dylan")
    two = make_ctx(ctx.snapshot.apply(assign(ctx, "t", "bruno")))
    kept = task_after(unassign(two, "t", "anna"), "t")
    assert kept.scheduled
    assert kept.assignee_ids == ("bruno",)
    assert unassign(ctx, "t", "chloe") == ()


def test_unassign_the_last_person_sends_the_task_back_to_the_backlog() -> None:
    ctx = ctx_with(
        make_task("card", backlog_rank="m"),
        make_task(
            "t",
            start=at(THU, 13),
            end=at(FRI, 12),
            people=("anna",),
            origin="sortie_dossier",
            estimate_minutes=60,
        ),
    )
    changes = unassign(ctx, "t", "anna")
    task = task_after(changes, "t")
    assert not task.scheduled
    assert task.assignments == {}
    assert task.estimate_minutes == 540
    assert task.origin == "sortie_dossier"
    assert task.backlog_rank > "m"
    assert [card.id for card in ctx.snapshot.apply(changes).backlog()] == ["card", "t"]


def test_unassign_a_person_foreseen_keeps_the_card_in_the_backlog() -> None:
    ctx = ctx_with(make_task("card", people=("anna",), backlog_rank="i"))
    task = task_after(unassign(ctx, "card", "anna"), "card")
    assert task.assignments == {}
    assert task.backlog_rank == "i"


def test_unschedule_task_keeps_people_as_foreseen() -> None:
    ctx = ctx_with(
        make_task("t", start=at(MON, 6, 30), end=at(TUE, 12), people=("anna", "bruno")),
    )
    task = task_after(unschedule_task(ctx, "t"), "t")
    assert not task.scheduled
    assert task.assignee_ids == ("anna", "bruno")
    assert task.estimate_minutes == 540 + 330
    assert unschedule_task(make_ctx(ctx.snapshot.apply(unschedule_task(ctx, "t"))), "t") == ()


# -------------------------------------------------------- duplicate ----


def test_duplicate_task_defaults_right_after_the_original() -> None:
    ctx = ctx_with(
        make_task(
            "t",
            start=at(THU, 6, 30),
            end=at(THU, 16, 30),
            people=("anna", "bruno"),
            status="done",
            planner_task_id="planner-1",
            notes="Voir plan",
        )
    )
    changes = duplicate_task(ctx, "t")
    assert len(changes) == 1
    copy = first_task(changes)
    assert copy.id != "t"
    assert changes[0].is_creation
    assert (copy.start, copy.end) == (at(FRI, 6, 30), at(NEXT_MON, 10))
    assert copy.assignee_ids == ("anna", "bruno")
    assert copy.status == "confirmed"
    assert copy.planner_task_id == ""
    assert copy.notes == "Voir plan"
    assert copy.created_by == AUTHOR
    assert copy.created_at == NOW
    assert ctx.snapshot.tasks["t"].status == "done"


def test_duplicate_task_on_other_people_and_start() -> None:
    ctx = ctx_with(make_task("t", start=at(MON, 6, 30), end=at(MON, 12), people=("anna",)))
    copy = first_task(duplicate_task(ctx, "t", new_start=at(WED, 13), resource_ids=("chloe",)))
    assert (copy.start, copy.end) == (at(WED, 13), at(THU, 8, 30))
    assert copy.assignee_ids == ("chloe",)


def test_duplicate_card_follows_the_original_in_the_backlog() -> None:
    ctx = ctx_with(
        make_task("a", backlog_rank="c"),
        make_task("b", backlog_rank="d"),
        make_task("c", backlog_rank="k"),
    )
    changes = duplicate_task(ctx, "b")
    copy = first_task(changes)
    order = [card.id for card in ctx.snapshot.apply(changes).backlog()]
    assert order == ["a", "b", copy.id, "c"]


def test_duplicate_card_on_the_grid() -> None:
    ctx = ctx_with(make_task("card", estimate_minutes=120, people=("anna",)))
    copy = first_task(duplicate_task(ctx, "card", new_start=at(MON, 6, 30)))
    assert (copy.start, copy.end) == (at(MON, 6, 30), at(MON, 8, 30))


# ----------------------------------------------------------- detach ----


def test_detach_assignment_splits_one_person_off() -> None:
    ctx = ctx_with(
        make_task(
            "t",
            start=at(MON, 6, 30),
            end=at(TUE, 16, 30),
            people=("anna", "bruno"),
            required_people=2,
            project_number="2026-5245",
            urgent=True,
        )
    )
    changes = detach_assignment(ctx, "t", "bruno")
    assert len(changes) == 2
    original = task_after(changes, "t")
    part = changes[1].after
    assert isinstance(part, PlanningTask)
    assert changes[1].is_creation
    assert original.assignee_ids == ("anna",)
    assert original.required_people == 1
    assert part.assignee_ids == ("bruno",)
    assert part.required_people == 1
    assert (part.start, part.end) == (original.start, original.end)
    assert part.project_number == "2026-5245"
    assert part.urgent
    assert part.id != "t"


def test_detach_assignment_refusals() -> None:
    ctx = ctx_with(make_task("t", start=at(MON, 6, 30), end=at(MON, 12), people=("anna",)))
    with pytest.raises(PlanningCommandError, match="rien a detacher"):
        detach_assignment(ctx, "t", "anna")
    with pytest.raises(PlanningCommandError, match="pas affectee"):
        detach_assignment(ctx, "t", "bruno")


def test_detach_from_a_card_with_unusable_ranks_writes_each_card_once() -> None:
    ctx = ctx_with(
        make_task("a", people=("anna", "bruno"), created_at="2026-10-01T08:00:00Z"),
        make_task("b", created_at="2026-10-02T08:00:00Z"),
    )
    changes = detach_assignment(ctx, "a", "bruno")
    ids = [change.doc_id for change in changes]
    assert len(ids) == len(set(ids))
    original = task_after(changes, "a")
    assert original.assignee_ids == ("anna",)
    part = changes[1].after
    assert isinstance(part, PlanningTask)
    order = [card.id for card in ctx.snapshot.apply(changes).backlog()]
    assert order == ["a", part.id, "b"]
    assert all(is_rank(card.backlog_rank) for card in ctx.snapshot.apply(changes).backlog())


# ---------------------------------------------------------- details ----


def test_set_status_and_update_task() -> None:
    ctx = ctx_with(
        make_task(
            "t",
            project_number="2026-0001",
            project_cache=ProjectCache(client="Client SA"),
            project_fingerprint="abc",
        )
    )
    assert task_after(set_status(ctx, "t", "provisional"), "t").status == "provisional"
    assert set_status(ctx, "t", "confirmed") == ()
    with pytest.raises(PlanningCommandError, match="Statut"):
        set_status(ctx, "t", "lost")  # type: ignore[arg-type]
    updated = task_after(
        update_task(ctx, "t", title="  Nouveau  ", urgent=True, deadline=FRI, notes="x"), "t"
    )
    assert updated.title == "Nouveau"
    assert updated.urgent
    assert updated.deadline == FRI
    assert updated.project_cache.client == "Client SA"
    renumbered = task_after(update_task(ctx, "t", project_number="2026-0002"), "t")
    assert renumbered.project_cache == ProjectCache()
    assert renumbered.project_fingerprint == ""
    assert update_task(ctx, "t", title="Tache t") == ()


def test_update_task_new_project_drops_the_old_fingerprint() -> None:
    # Regression: a new number given with its cache kept the previous dossier's fingerprint.
    ctx = ctx_with(
        make_task(
            "t",
            project_number="2026-0001",
            project_cache=ProjectCache(client="A"),
            project_fingerprint="fp-of-0001",
        )
    )
    with_cache = task_after(
        update_task(ctx, "t", project_number="2026-0002", project_cache=ProjectCache(client="B")),
        "t",
    )
    assert with_cache.project_cache.client == "B"
    assert with_cache.project_fingerprint == ""
    complete = task_after(
        update_task(
            ctx,
            "t",
            project_number="2026-0002",
            project_cache=ProjectCache(client="B"),
            project_fingerprint="fp-of-0002",
        ),
        "t",
    )
    assert complete.project_fingerprint == "fp-of-0002"
    refreshed = task_after(update_task(ctx, "t", project_fingerprint="fp-new"), "t")
    assert refreshed.project_fingerprint == "fp-new"
    assert refreshed.project_cache.client == "A"
    same = task_after(update_task(ctx, "t", project_number=" 2026-0001 ", notes="x"), "t")
    assert same.project_fingerprint == "fp-of-0001"


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"start": 0}, "non modifiable"),
        ({"task_type": "inconnu"}, "Type de tache inconnu"),
        ({"estimate_minutes": -30}, "estimate_minutes"),
        ({"required_people": 0}, "required_people"),
    ],
)
def test_update_task_refusals(fields: dict[str, object], message: str) -> None:
    with pytest.raises(PlanningCommandError, match=message):
        update_task(ctx_with(make_task("t")), "t", **fields)


def test_delete_and_restore_task() -> None:
    ctx = ctx_with(make_task("t", start=at(MON, 6, 30), end=at(MON, 12), people=("anna",)))
    deleted = task_after(delete_task(ctx, "t"), "t")
    assert deleted.deleted_at == NOW
    assert deleted.deleted_by == AUTHOR
    assert deleted.scheduled
    after_delete = make_ctx(ctx.snapshot.apply(delete_task(ctx, "t")))
    assert delete_task(after_delete, "t") == ()
    with pytest.raises(PlanningCommandError, match="supprimee"):
        move_task(after_delete, "t", new_start=at(TUE, 6, 30))
    restored = task_after(restore_task(after_delete, "t"), "t")
    assert restored.deleted_at is None
    assert restored.deleted_by is None
    assert restore_task(ctx, "t") == ()


# ---------------------------------------------------------- backlog ----


def backlog_ctx() -> CommandContext:
    return ctx_with(
        make_task("a", backlog_rank="c"),
        make_task("b", backlog_rank="i"),
        make_task("c", backlog_rank="r"),
        make_task("planned", start=at(MON, 6, 30), end=at(MON, 12)),
    )


def order_after(ctx: CommandContext, changes: tuple[DocumentChange, ...]) -> list[str]:
    return [card.id for card in ctx.snapshot.apply(changes).backlog()]


def test_move_backlog_rewrites_only_the_moved_card() -> None:
    ctx = backlog_ctx()
    to_top = move_backlog(ctx, "c", below_id="a")
    assert [change.doc_id for change in to_top] == ["c"]
    assert order_after(ctx, to_top) == ["c", "a", "b"]
    between = move_backlog(ctx, "a", above_id="b", below_id="c")
    assert order_after(ctx, between) == ["b", "a", "c"]
    to_end = move_backlog(ctx, "a")
    assert order_after(ctx, to_end) == ["b", "c", "a"]
    after_b = move_backlog(ctx, "c", above_id="a")
    assert order_after(ctx, after_b) == ["a", "c", "b"]


def test_move_backlog_without_move_changes_nothing() -> None:
    ctx = backlog_ctx()
    assert move_backlog(ctx, "b", above_id="a") == ()
    assert move_backlog(ctx, "b", below_id="c") == ()
    assert move_backlog(ctx, "c") == ()
    assert move_backlog(ctx, "b", above_id="b") == ()


def test_move_backlog_renumbers_cards_without_usable_ranks() -> None:
    ctx = ctx_with(
        make_task("a", created_at="2026-10-01T08:00:00Z"),
        make_task("b", created_at="2026-10-02T08:00:00Z"),
        make_task("c", created_at="2026-10-03T08:00:00Z"),
    )
    changes = move_backlog(ctx, "c", above_id="a")
    assert order_after(ctx, changes) == ["a", "c", "b"]
    assert {change.doc_id for change in changes} == {"a", "b", "c"}
    assert all(is_rank(task_after(changes, doc).backlog_rank) for doc in ("a", "b", "c"))


def test_move_backlog_refusals() -> None:
    ctx = backlog_ctx()
    with pytest.raises(PlanningCommandError, match="a planifier"):
        move_backlog(ctx, "planned", above_id="a")
    with pytest.raises(PlanningCommandError, match="voisine"):
        move_backlog(ctx, "a", above_id="planned")


# ------------------------------------------------------------ shift ----


@pytest.mark.parametrize(
    ("start", "end", "minutes", "days", "expected"),
    [
        (at(MON, 6, 30), at(MON, 7, 30), 30, 0, (at(MON, 7), at(MON, 8))),
        (at(MON, 11, 30), at(MON, 12), 30, 0, (at(MON, 13), at(MON, 13, 30))),
        (at(FRI, 11, 30), at(FRI, 12), 30, 0, (at(NEXT_MON, 6, 30), at(NEXT_MON, 7))),
        (at(NEXT_MON, 6, 30), at(NEXT_MON, 7), -30, 0, (at(FRI, 11, 30), at(FRI, 12))),
        (at(FRI, 6, 30), at(FRI, 12), 0, 1, (at(NEXT_MON, 6, 30), at(NEXT_MON, 12))),
        (at(NEXT_MON, 6, 30), at(NEXT_MON, 16, 30), 0, -1, (at(FRI, 6, 30), at(NEXT_MON, 10))),
        # Regression: an afternoon shifted onto a Friday skipped it, or stayed on Monday.
        (at(THU, 13), at(THU, 16, 30), 0, 1, (at(FRI, 6, 30), at(FRI, 10))),
        (at(NEXT_MON, 13), at(NEXT_MON, 16, 30), 0, -1, (at(FRI, 6, 30), at(FRI, 10))),
        (at(THU, 14), at(THU, 15), 0, 1, (at(FRI, 6, 30), at(FRI, 7, 30))),
        (at(WED, 13), at(WED, 16, 30), 0, 1, (at(THU, 13), at(THU, 16, 30))),
        (at(FRI, 6, 30), at(FRI, 10), 0, 1, (at(NEXT_MON, 6, 30), at(NEXT_MON, 10))),
    ],
)
def test_shift_tasks_moves_by_worked_time(
    start: WallTime,
    end: WallTime,
    minutes: int,
    days: int,
    expected: tuple[WallTime, WallTime],
) -> None:
    ctx = ctx_with(make_task("t", start=start, end=end, people=("anna",)))
    task = task_after(shift_tasks(ctx, ["t"], minutes=minutes, days=days), "t")
    assert (task.start, task.end) == expected


def test_shift_tasks_handles_several_tasks_once_each() -> None:
    ctx = ctx_with(
        make_task("a", start=at(MON, 6, 30), end=at(MON, 12), people=("anna",)),
        make_task("b", start=at(TUE, 6, 30), end=at(TUE, 12), people=("chloe",)),
    )
    changes = shift_tasks(ctx, ["a", "b", "a"], minutes=60)
    assert [change.doc_id for change in changes] == ["a", "b"]
    assert task_after(changes, "a").start == at(MON, 7, 30)
    assert shift_tasks(ctx, ["a"]) == ()
    with pytest.raises(PlanningCommandError, match="pas planifiee"):
        shift_tasks(ctx_with(make_task("card")), ["card"], minutes=30)


# -------------------------------------------------------- resources ----


def test_create_resource_defaults() -> None:
    ctx = ctx_with()
    changes = create_resource(ctx, name="  Jean   Dupont ", canton="vd")
    resource = changes[0].after
    assert isinstance(resource, Resource)
    assert changes[0].is_creation
    assert resource.name == "Jean Dupont"
    assert resource.initials == "JD"
    assert resource.team == "atelier"
    # The archived row keeps its place: a new row goes after it.
    assert resource.order == 4000 + ORDER_STEP
    assert resource.canton == "VD"
    assert resource.created_by == AUTHOR
    subcontractor = create_resource(ctx, name="Galvaswiss AG", kind_of_resource="subcontractor")[0]
    assert isinstance(subcontractor.after, Resource)
    assert subcontractor.after.team == "soustraitants"
    assert subcontractor.after.order == ORDER_STEP


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"name": "   "}, "nom est obligatoire"),
        ({"name": "X", "team": "cuisine"}, "Equipe inconnue"),
        ({"name": "X", "canton": "ZH"}, "Canton inconnu"),
    ],
)
def test_create_resource_refusals(fields: dict[str, object], message: str) -> None:
    with pytest.raises(PlanningCommandError, match=message):
        create_resource(ctx_with(), **fields)  # type: ignore[arg-type]


def test_update_and_archive_resource() -> None:
    ctx = ctx_with()
    moved = only(update_resource(ctx, "anna", team="bureau", role=" Cheffe "), "anna")
    assert isinstance(moved, Resource)
    assert moved.team == "bureau"
    assert moved.role == "Cheffe"
    assert moved.order == 1000 + ORDER_STEP
    school = (RecurringOff(weekday=2, reason="ecole"),)
    apprentice = only(update_resource(ctx, "bruno", apprentice=True, recurring_off=school), "bruno")
    assert isinstance(apprentice, Resource)
    assert apprentice.recurring_off == school
    assert update_resource(ctx, "anna", name="Anna Favre") == ()
    with pytest.raises(PlanningCommandError, match="non modifiable"):
        update_resource(ctx, "anna", order=5)
    archived = only(archive_resource(ctx, "anna"), "anna")
    assert isinstance(archived, Resource)
    assert not archived.active
    assert not archived.deleted
    assert archive_resource(ctx, "dylan") == ()


def team_order(ctx: CommandContext, changes: tuple[DocumentChange, ...]) -> list[str]:
    return [
        row.id for row in ctx.snapshot.apply(changes).active_resources() if row.team == "atelier"
    ]


def test_reorder_resource_inside_its_team() -> None:
    ctx = ctx_with()
    up = reorder_resource(ctx, "chloe", above_id="anna", below_id="bruno")
    assert [change.doc_id for change in up] == ["chloe"]
    assert team_order(ctx, up) == ["anna", "chloe", "bruno"]
    top = reorder_resource(ctx, "bruno", above_id="emma", below_id="anna")
    assert team_order(ctx, top) == ["bruno", "anna", "chloe"]
    bottom = reorder_resource(ctx, "anna", above_id="chloe")
    assert team_order(ctx, bottom) == ["bruno", "chloe", "anna"]
    assert reorder_resource(ctx, "bruno", above_id="anna") == ()
    with pytest.raises(PlanningCommandError, match="voisine"):
        reorder_resource(ctx, "anna", above_id="nobody")
    with pytest.raises(PlanningCommandError, match="voisine"):
        reorder_resource(ctx, "anna", above_id="dylan")


def test_reorder_resource_with_neighbours_of_other_teams() -> None:
    # Regression: a no-op drop of the only row of a team raised an error.
    galva = make_resource("galva", "Galva", team="soustraitants", order=1000)
    fanny = make_resource("fanny", "Fanny Bureau", team="bureau", order=2000)
    ctx = ctx_with(galva, fanny)
    assert reorder_resource(ctx, "galva", above_id="chloe") == ()
    assert reorder_resource(ctx, "galva", above_id="chloe", below_id=None) == ()
    top = reorder_resource(ctx, "chloe", above_id="fanny")
    assert team_order(ctx, top) == ["chloe", "anna", "bruno"]
    bottom = reorder_resource(ctx, "anna", below_id="galva")
    assert team_order(ctx, bottom) == ["bruno", "chloe", "anna"]
    alone = make_ctx(make_snapshot(*people()))
    assert reorder_resource(alone, "emma", below_id="anna") == ()
    with pytest.raises(PlanningCommandError, match="reste dans son equipe"):
        reorder_resource(ctx, "anna", above_id="emma", below_id="fanny")
    with pytest.raises(PlanningCommandError, match="reste dans son equipe"):
        reorder_resource(ctx, "anna", above_id="galva")
    with pytest.raises(PlanningCommandError, match="reste dans son equipe"):
        reorder_resource(ctx, "anna", below_id="emma")


def test_reorder_resource_renumbers_a_team_without_gap() -> None:
    rows = [
        make_resource("a", "A", order=1),
        make_resource("b", "B", order=2),
        make_resource("c", "C", order=3),
    ]
    ctx = make_ctx(make_snapshot(*rows))
    changes = reorder_resource(ctx, "c", above_id="a", below_id="b")
    assert team_order(ctx, changes) == ["a", "c", "b"]
    assert {change.doc_id for change in changes} == {"a", "b", "c"}


# --------------------------------------------------------- absences ----


def test_create_update_and_delete_absence() -> None:
    ctx = ctx_with()
    changes = create_absence(ctx, resource_id="anna", first_day=TUE, reason="vacances")
    absence = changes[0].after
    assert isinstance(absence, Absence)
    assert (absence.first_day, absence.last_day) == (TUE, TUE)
    assert absence.created_by == AUTHOR
    with_absence = make_ctx(ctx.snapshot.apply(changes))
    longer = only(update_absence(with_absence, absence.id, last_day=FRI, note=" ok "), absence.id)
    assert isinstance(longer, Absence)
    assert longer.last_day == FRI
    assert longer.note == "ok"
    with pytest.raises(PlanningCommandError, match="precede"):
        update_absence(with_absence, absence.id, last_day=MON)
    deleted = only(delete_absence(with_absence, absence.id), absence.id)
    assert deleted.deleted_at == NOW
    gone = make_ctx(with_absence.snapshot.apply(delete_absence(with_absence, absence.id)))
    assert delete_absence(gone, absence.id) == ()
    with pytest.raises(PlanningCommandError, match="Absence introuvable"):
        update_absence(gone, absence.id, note="x")


def test_create_absence_refusals() -> None:
    ctx = ctx_with()
    with pytest.raises(PlanningCommandError, match="precede"):
        create_absence(ctx, resource_id="anna", first_day=FRI, last_day=MON)
    with pytest.raises(PlanningCommandError, match="introuvable"):
        create_absence(ctx, resource_id="nobody", first_day=MON)
    with pytest.raises(PlanningCommandError, match="reason"):
        create_absence(ctx, resource_id="anna", first_day=MON, reason="plage")  # type: ignore[arg-type]


# --------------------------------------------------------- settings ----


def test_update_meta_creates_the_meta_the_first_time() -> None:
    ctx = ctx_with()
    changes = update_meta(ctx, default_canton="vd")
    assert changes[0].is_creation
    meta = changes[0].after
    assert isinstance(meta, PlanningMeta)
    assert meta.default_canton == "VD"
    assert meta.created_by == AUTHOR
    saved = make_ctx(ctx.snapshot.apply(changes))
    again = update_meta(saved, auto_card_on_sortie=False)
    assert not again[0].is_creation
    assert update_meta(saved, default_canton="VD") == ()


def test_update_meta_refusals() -> None:
    ctx = ctx_with()
    with pytest.raises(PlanningCommandError, match="non modifiable"):
        update_meta(ctx, id="other")
    with pytest.raises(PlanningCommandError, match="Canton"):
        update_meta(ctx, default_canton="ZH")
    with pytest.raises(PlanningCommandError, match="Horaires"):
        update_meta(ctx, work_week={0: ((600, 500),)})


def test_update_meta_checks_closures_and_teams_in_use() -> None:
    # Regression: a reversed closure was saved and then silently ignored by the calendar.
    ctx = ctx_with()
    with pytest.raises(ValidationError, match="fermeture se termine"):
        Closure(first_day=date(2026, 12, 30), last_day=date(2026, 12, 24))
    one_day = Closure(first_day=date(2026, 12, 28), last_day=date(2026, 12, 28))
    assert update_meta(ctx, closures=(one_day,))
    teams = ctx.snapshot.meta.teams
    with pytest.raises(PlanningCommandError, match="Emma Bureau"):
        update_meta(ctx, teams=tuple(team for team in teams if team.key != "bureau"))
    archived = ctx_with(make_resource("old", "Old Monteur", team="montage", active=False))
    assert update_meta(archived, teams=tuple(team for team in teams if team.key != "montage"))
    renamed = tuple(
        team.model_copy(update={"name": "Bureau"}) if team.key == "bureau" else team
        for team in teams
    )
    assert update_meta(ctx, teams=renamed)


def test_calendar_resolver_uses_cantons_and_closures() -> None:
    meta = PlanningMeta(
        closures=(Closure(first_day=date(2026, 12, 28), last_day=date(2026, 12, 30)),),
    )
    snapshot = make_snapshot(*people(), meta=meta)
    calendar_for = calendar_resolver(snapshot)
    assert calendar_for(None).cantons == ("GE",)
    assert calendar_for("chloe").cantons == ("VD",)
    assert calendar_for("nobody").cantons == ("GE",)
    assert calendar_for("anna") is calendar_for(None)
    assert not calendar_for("anna").is_workday(date(2026, 12, 29))
    assert calendar_for("chloe").is_workday(date(2026, 9, 10))
    assert not calendar_for("anna").is_workday(date(2026, 9, 10))
    assert isinstance(calendar_for("anna"), WorkCalendar)


# ------------------------------------------------------------ purity ----


def test_commands_never_modify_their_snapshot_and_undo_restores_it() -> None:
    ctx = ctx_with(
        make_task("t", start=at(MON, 6, 30), end=at(TUE, 12), people=("anna", "bruno")),
        make_task("card", backlog_rank="i", people=("chloe",)),
        make_task("other", backlog_rank="k"),
    )
    before = dump(ctx.snapshot)
    batches = [
        move_task(ctx, "t", new_start=at(WED, 6, 30), from_resource="anna", to_resource="chloe"),
        resize_task(ctx, "t", end=at(WED, 12)),
        assign(ctx, "t", "emma"),
        unassign(ctx, "t", "anna"),
        unschedule_task(ctx, "t"),
        duplicate_task(ctx, "t"),
        detach_assignment(ctx, "t", "bruno"),
        schedule_task(ctx, "card", start=at(MON, 6, 30), resource_ids=()),
        move_backlog(ctx, "card", above_id="other"),
        shift_tasks(ctx, ["t"], minutes=-30),
        update_task(ctx, "t", title="Nouveau"),
        delete_task(ctx, "t"),
        reorder_resource(ctx, "chloe", below_id="anna"),
        update_meta(ctx, default_canton="VD"),
    ]
    assert dump(ctx.snapshot) == before
    for changes in batches:
        assert changes
        assert all(
            change.after is not None and change.after.updated_at == NOW for change in changes
        )
        undone = ctx.snapshot.apply(changes).apply(change.inverse() for change in reversed(changes))
        assert dump(undone) == before


def test_default_duration_constant_is_one_full_day() -> None:
    assert WorkCalendar().worked_minutes(MON) == DEFAULT_TASK_MINUTES


# ------------------------------------------------------------- edges ----


def test_rank_between_edge_digits() -> None:
    assert rank_between(None, "1i") == "1"
    assert rank_between("1", "2i") == "2"
    assert rank_between(None, "1") == "0z"
    assert rank_between("zz", None) == "zz1"


def test_schedule_task_again_keeps_its_worked_length() -> None:
    ctx = ctx_with(make_task("t", start=at(MON, 6, 30), end=at(TUE, 12), people=("anna",)))
    task = task_after(schedule_task(ctx, "t", start=at(THU, 6, 30), resource_ids=()), "t")
    assert (task.start, task.end) == (at(THU, 6, 30), at(FRI, 12))


def test_move_task_lying_on_a_holiday_keeps_its_weekday_hours() -> None:
    christmas = date(2026, 12, 25)
    ctx = ctx_with(
        make_task("t", start=at(christmas, 6, 30), end=at(christmas, 12), people=("anna",))
    )
    task = task_after(move_task(ctx, "t", new_start=at(date(2026, 12, 28), 6, 30)), "t")
    assert task.end == at(date(2026, 12, 28), 12)


def test_validation_errors_become_command_errors() -> None:
    ctx = ctx_with(make_task("t"))
    with pytest.raises(PlanningCommandError, match="deadline"):
        update_task(ctx, "t", deadline="demain")


def test_update_resource_canton_and_team_refusals() -> None:
    ctx = ctx_with()
    vaud = only(update_resource(ctx, "anna", canton=" vd "), "anna")
    assert isinstance(vaud, Resource)
    assert vaud.canton == "VD"
    with pytest.raises(PlanningCommandError, match="Equipe inconnue"):
        update_resource(ctx, "anna", team="cuisine")
    with pytest.raises(PlanningCommandError, match="nom est obligatoire"):
        update_resource(ctx, "anna", name=" ")
    with pytest.raises(PlanningCommandError, match="introuvable"):
        update_resource(ctx, "nobody", name="X")


def test_create_resource_falls_back_to_the_first_team() -> None:
    meta = PlanningMeta(teams=(Team(key="b", name="B", order=2), Team(key="a", name="A", order=1)))
    changes = create_resource(
        make_ctx(make_snapshot(meta=meta)), name="Galva", kind_of_resource="subcontractor"
    )
    resource = changes[0].after
    assert isinstance(resource, Resource)
    assert resource.team == "a"
    assert (
        create_resource(make_ctx(make_snapshot(meta=meta)), name="X", team="b")[0].after is not None
    )


def test_reorder_resource_to_the_end_and_onto_itself() -> None:
    ctx = ctx_with()
    end = reorder_resource(ctx, "anna")
    assert team_order(ctx, end) == ["bruno", "chloe", "anna"]
    assert reorder_resource(ctx, "anna", above_id="anna") == ()
    assert reorder_resource(ctx, "emma") == ()


def test_absence_edits_check_their_resource() -> None:
    ctx = ctx_with(Absence(id="off", resource_id="anna", first_day=MON, last_day=MON))
    moved = only(update_absence(ctx, "off", resource_id="bruno"), "off")
    assert isinstance(moved, Absence)
    assert moved.resource_id == "bruno"
    with pytest.raises(PlanningCommandError, match="introuvable"):
        update_absence(ctx, "off", resource_id="nobody")
    with pytest.raises(PlanningCommandError, match="non modifiable"):
        update_absence(ctx, "off", reason_text="x")
    with pytest.raises(PlanningCommandError, match="Absence introuvable"):
        delete_absence(ctx, "missing")


def test_update_meta_requires_a_default_canton() -> None:
    with pytest.raises(PlanningCommandError, match="Canton par defaut"):
        update_meta(ctx_with(), default_canton=" ")


# ------------------------------------------------------- working days ----

SEPT_WED = date(2026, 9, 9)  # the Jeune genevois is on Thursday 10 September 2026
SEPT_THU = date(2026, 9, 10)
SEPT_FRI = date(2026, 9, 11)
SEPT_MON = date(2026, 9, 21)  # Lundi du Jeune, Vaud only
SEPT_TUE = date(2026, 9, 22)


def test_create_task_stores_days_and_places_them_as_working_days() -> None:
    ctx = ctx_with()
    card = first_task(
        create_task(ctx, title="A", task_type="debit", estimate_minutes=1080, estimate_days=2)
    )
    assert (card.scheduled, card.estimate_days, card.estimate_minutes) == (False, 2, 1080)
    thursday = first_task(
        create_task(ctx, title="B", task_type="debit", start=at(THU, 6, 30), estimate_days=2)
    )
    assert (thursday.start, thursday.end) == (at(THU, 6, 30), at(FRI, 12))
    week = first_task(
        create_task(ctx, title="C", task_type="debit", start=at(MON, 6, 30), estimate_days=5)
    )
    assert week.end == at(FRI, 12)
    afternoon = first_task(
        create_task(ctx, title="D", task_type="debit", start=at(MON, 13), estimate_days=0.5)
    )
    assert (afternoon.start, afternoon.end) == (at(MON, 13), at(MON, 16, 30))


def test_create_task_days_win_over_minutes_and_an_end_wins_over_both() -> None:
    ctx = ctx_with()
    days = first_task(
        create_task(
            ctx,
            title="A",
            task_type="debit",
            start=at(THU, 6, 30),
            estimate_minutes=60,
            estimate_days=1,
        )
    )
    assert days.end == at(THU, 16, 30)
    ended = first_task(
        create_task(
            ctx,
            title="B",
            task_type="debit",
            start=at(MON, 6, 30),
            end=at(MON, 12),
            estimate_days=3,
        )
    )
    assert (ended.end, ended.estimate_days) == (at(MON, 12), 3)
    with pytest.raises(PlanningCommandError, match="estimate_days"):
        create_task(ctx, title="C", task_type="debit", estimate_days=-1)


def test_create_task_days_skip_the_holidays_of_the_first_person() -> None:
    ctx = ctx_with()
    geneva = first_task(
        create_task(
            ctx,
            title="A",
            task_type="montage",
            start=at(SEPT_WED, 6, 30),
            estimate_days=2,
            resource_ids=("anna",),
        )
    )
    vaud = first_task(
        create_task(
            ctx,
            title="B",
            task_type="montage",
            start=at(SEPT_WED, 6, 30),
            estimate_days=2,
            resource_ids=("chloe",),
        )
    )
    assert geneva.end == at(SEPT_FRI, 12)
    assert vaud.end == at(SEPT_THU, 16, 30)


def test_schedule_task_places_a_card_by_its_working_days() -> None:
    ctx = ctx_with(make_task("card", estimate_minutes=1080, estimate_days=2, people=("anna",)))
    thursday = task_after(schedule_task(ctx, "card", start=at(THU, 6, 30), resource_ids=()), "card")
    assert (thursday.start, thursday.end) == (at(THU, 6, 30), at(FRI, 12))
    assert (thursday.estimate_days, thursday.estimate_minutes) == (2, 1080)
    ended = task_after(
        schedule_task(ctx, "card", start=at(MON, 6, 30), end=at(MON, 12), resource_ids=()), "card"
    )
    assert ended.end == at(MON, 12)


def test_schedule_task_days_follow_the_canton_of_the_first_assignee() -> None:
    ctx = ctx_with(make_task("card", estimate_days=2))
    friday = date(2026, 9, 18)
    vaud = task_after(
        schedule_task(ctx, "card", start=at(friday, 6, 30), resource_ids=("chloe", "anna")),
        "card",
    )
    geneva = task_after(
        schedule_task(ctx, "card", start=at(friday, 6, 30), resource_ids=("anna", "chloe")),
        "card",
    )
    assert vaud.end == at(SEPT_TUE, 16, 30)
    assert geneva.end == at(SEPT_MON, 16, 30)


def test_schedule_task_again_ignores_the_days_of_its_estimate() -> None:
    ctx = ctx_with(
        make_task("t", start=at(MON, 6, 30), end=at(TUE, 12), estimate_days=5, people=("anna",))
    )
    task = task_after(schedule_task(ctx, "t", start=at(THU, 6, 30), resource_ids=()), "t")
    assert (task.start, task.end) == (at(THU, 6, 30), at(FRI, 12))


@pytest.mark.parametrize(
    ("start", "end", "people_ids", "days", "minutes"),
    [
        (at(MON, 6, 30), at(TUE, 12), ("anna",), 1.5, 870),
        (at(THU, 6, 30), at(FRI, 12), ("anna",), 2, 870),
        (at(MON, 13), at(MON, 16, 30), ("anna",), 0.5, 210),
        (at(MON, 6, 30), at(MON, 10), ("anna",), 0, 210),
        (at(MON, 10), at(TUE, 16, 30), ("anna",), 0, 870),
        (at(SEPT_WED, 6, 30), at(SEPT_THU, 16, 30), ("chloe",), 2, 1080),
        (at(SEPT_WED, 6, 30), at(SEPT_FRI, 12), ("anna",), 2, 870),
    ],
)
def test_unschedule_task_keeps_whole_days_as_estimate(
    start: WallTime, end: WallTime, people_ids: tuple[str, ...], days: float, minutes: int
) -> None:
    ctx = ctx_with(make_task("t", start=start, end=end, estimate_days=4, people=people_ids))
    task = task_after(unschedule_task(ctx, "t"), "t")
    assert not task.scheduled
    assert (task.estimate_days, task.estimate_minutes) == (days, minutes)


def test_unassign_the_last_person_keeps_whole_days_on_their_calendar() -> None:
    ctx = ctx_with(
        make_task("t", start=at(SEPT_WED, 6, 30), end=at(SEPT_THU, 16, 30), people=("chloe",))
    )
    task = task_after(unassign(ctx, "t", "chloe"), "t")
    assert (task.estimate_days, task.estimate_minutes) == (2, 1080)
    half = ctx_with(make_task("h", start=at(THU, 13), end=at(FRI, 12), people=("anna",)))
    assert task_after(unassign(half, "h", "anna"), "h").estimate_days == 1.5


def test_a_card_goes_back_and_forth_with_its_days() -> None:
    ctx = ctx_with(make_task("card", estimate_days=2, people=("anna",)))
    placed = make_ctx(
        ctx.snapshot.apply(schedule_task(ctx, "card", start=at(THU, 6, 30), resource_ids=()))
    )
    back = make_ctx(placed.snapshot.apply(unschedule_task(placed, "card")))
    assert back.snapshot.tasks["card"].estimate_days == 2
    again = task_after(schedule_task(back, "card", start=at(MON, 6, 30), resource_ids=()), "card")
    assert (again.start, again.end) == (at(MON, 6, 30), at(TUE, 16, 30))


@pytest.mark.parametrize(
    ("days", "start", "end"),
    [
        # whole_days reads these bars as 1 j, 2 j, nothing, nothing: the card keeps its own.
        (0.5, at(FRI, 6, 30), at(FRI, 12)),
        (1.5, at(THU, 6, 30), at(FRI, 12)),
        (2, at(THU, 11), at(FRI, 12)),
        (1, at(MON, 15), at(TUE, 12)),
        # An afternoon start is half of its day: 1 j from Monday 13:00 ends Tuesday noon.
        (1, at(MON, 13), at(TUE, 12)),
        (2, at(THU, 6, 30), at(FRI, 12)),
        (2, at(THU, 13), at(NEXT_MON, 12)),
        (0.5, at(MON, 13), at(MON, 16, 30)),
    ],
)
def test_a_card_keeps_its_days_through_place_and_send_back(
    days: float, start: WallTime, end: WallTime
) -> None:
    ctx = ctx_with(make_task("card", estimate_days=days, people=("anna",)))
    placed = make_ctx(ctx.snapshot.apply(schedule_task(ctx, "card", start=start, resource_ids=())))
    bar = placed.snapshot.tasks["card"]
    assert (bar.start, bar.end) == (start, end)
    unscheduled = task_after(unschedule_task(placed, "card"), "card")
    deleted = task_after(unassign(placed, "card", "anna"), "card")
    assert unscheduled.estimate_days == deleted.estimate_days == days
    back = make_ctx(placed.snapshot.apply(unschedule_task(placed, "card")))
    again = task_after(schedule_task(back, "card", start=start, resource_ids=()), "card")
    assert (again.start, again.end) == (start, end)


@pytest.mark.parametrize(
    ("fields", "days"),
    [
        # A Friday morning is the whole Friday: 1 j, whatever estimate placed it.
        ({"start": at(FRI, 6, 30), "end": at(FRI, 12), "estimate_days": 0.5}, 1),
        ({"start": at(FRI, 6, 30), "end": at(FRI, 12)}, 1),
        ({"start": at(THU, 6, 30), "end": at(FRI, 12), "estimate_days": 1.5}, 2),
        ({"start": at(THU, 6, 30), "end": at(FRI, 12), "estimate_days": 3}, 2),
        # Not whole half days: the bar reads in hours.
        ({"start": at(THU, 11), "end": at(FRI, 12), "estimate_days": 2}, 0),
        ({"start": at(THU, 11), "end": at(FRI, 12)}, 0),
        ({"start": at(THU, 13), "end": at(NEXT_MON, 12), "estimate_days": 2}, 2),
        ({"start": at(MON, 6, 30), "end": at(MON, 16, 30), "estimate_days": 1.25}, 1),
        ({"start": at(MON, 6, 30), "end": at(TUE, 12), "estimate_days": 1.25}, 1.5),
        ({"estimate_days": 2}, 0),
    ],
)
def test_task_days_reads_a_bar_as_the_bar_shows_it(fields: dict[str, object], days: float) -> None:
    calendar = WorkCalendar(cantons=("GE",))
    task = make_task("t", **fields)
    assert task_days(calendar, task) == days
    if task.start is not None and task.end is not None:
        # The bar, the drag label and the Détail read whole_days: the hover says the same.
        assert task_days(calendar, task) == (calendar.whole_days(task.start, task.end) or 0)


@pytest.mark.parametrize(
    ("fields", "days"),
    [
        ({"start": at(FRI, 6, 30), "end": at(FRI, 12), "estimate_days": 0.5}, 0.5),
        ({"start": at(FRI, 6, 30), "end": at(FRI, 12)}, 1),
        ({"start": at(THU, 6, 30), "end": at(FRI, 12), "estimate_days": 1.5}, 1.5),
        ({"start": at(THU, 6, 30), "end": at(FRI, 12), "estimate_days": 3}, 2),
        ({"start": at(THU, 11), "end": at(FRI, 12), "estimate_days": 2}, 2),
        ({"start": at(THU, 13), "end": at(NEXT_MON, 12), "estimate_days": 2}, 2),
        ({"estimate_days": 2}, 0),
    ],
)
def test_card_days_keep_the_days_that_placed_the_bar(
    fields: dict[str, object], days: float
) -> None:
    calendar = WorkCalendar(cantons=("GE",))
    assert card_days(calendar, make_task("t", **fields)) == days


def test_a_card_dropped_on_an_afternoon_reads_its_days_everywhere() -> None:
    """~2 j dropped on Thursday 13:00: Monday noon, and 2 j on the bar and the hover."""
    calendar = WorkCalendar(cantons=("GE",))
    ctx = ctx_with(make_task("card", estimate_days=2, people=("anna",)))
    bar = task_after(schedule_task(ctx, "card", start=at(THU, 13), resource_ids=()), "card")
    assert (bar.start, bar.end) == (at(THU, 13), at(NEXT_MON, 12))
    assert calendar.whole_days(at(THU, 13), at(NEXT_MON, 12)) == 2
    assert task_days(calendar, bar) == 2
    three = ctx_with(make_task("card", estimate_days=3, people=("anna",)))
    wide = task_after(schedule_task(three, "card", start=at(WED, 13), resource_ids=()), "card")
    assert wide.end == at(NEXT_MON, 12)
    assert task_days(calendar, wide) == 3


def test_a_bar_changed_since_placed_goes_back_with_the_days_it_covers() -> None:
    ctx = ctx_with(make_task("card", estimate_days=2, people=("anna",)))
    placed = make_ctx(
        ctx.snapshot.apply(schedule_task(ctx, "card", start=at(THU, 6, 30), resource_ids=()))
    )
    moved = make_ctx(placed.snapshot.apply(move_task(placed, "card", new_start=at(MON, 6, 30))))
    assert moved.snapshot.tasks["card"].end == at(TUE, 12)
    assert task_after(unschedule_task(moved, "card"), "card").estimate_days == 1.5
    resized = make_ctx(placed.snapshot.apply(resize_task(placed, "card", end=at(FRI, 10))))
    assert task_after(unschedule_task(resized, "card"), "card").estimate_days == 0


@pytest.mark.parametrize(
    ("typed", "stored", "end"),
    [
        (1.25, 1.5, at(TUE, 12)),
        (2.25, 2.5, at(WED, 12)),
        (0.25, 0.5, at(MON, 12)),
        (0.2, 0.5, at(MON, 12)),
        (3.75, 4, at(THU, 16, 30)),
        (2, 2, at(TUE, 16, 30)),
        (0, 0, at(MON, 16, 30)),
    ],
)
def test_days_are_stored_rounded_as_they_are_shown(
    typed: float, stored: float, end: WallTime
) -> None:
    ctx = ctx_with(make_task("card", estimate_days=3))
    updated = task_after(update_task(ctx, "card", estimate_days=typed), "card")
    created = first_task(create_task(ctx, title="A", task_type="debit", estimate_days=typed))
    assert updated.estimate_days == created.estimate_days == stored
    placed = first_task(
        create_task(
            ctx,
            title="B",
            task_type="debit",
            start=at(MON, 6, 30),
            estimate_days=typed,
            resource_ids=("anna",),
        )
    )
    assert (placed.estimate_days, placed.end) == (stored, end)


def test_days_stored_unrounded_elsewhere_are_placed_as_shown() -> None:
    ctx = ctx_with(make_task("card", estimate_days=1.25, people=("anna",)))
    task = task_after(schedule_task(ctx, "card", start=at(MON, 6, 30), resource_ids=()), "card")
    assert task.end == at(TUE, 12)


@pytest.mark.parametrize("days", [float("inf"), float("nan"), -0.5, 251])
def test_invalid_days_are_refused(days: float) -> None:
    ctx = ctx_with(make_task("card"))
    with pytest.raises(PlanningCommandError, match="estimate_days"):
        update_task(ctx, "card", estimate_days=days)
    with pytest.raises(PlanningCommandError, match="estimate_days"):
        create_task(ctx, title="A", task_type="debit", estimate_days=days)


def test_a_card_with_unreadable_days_cannot_be_placed() -> None:
    card = make_task("card", people=("anna",)).model_copy(update={"estimate_days": float("inf")})
    with pytest.raises(PlanningCommandError, match="estimate_days"):
        schedule_task(ctx_with(card), "card", start=at(MON, 6, 30), resource_ids=())


def test_duplicate_task_keeps_minutes_and_days() -> None:
    ctx = ctx_with(
        make_task("card", estimate_minutes=1080, estimate_days=2, people=("anna",)),
        make_task(
            "t",
            start=at(MON, 6, 30),
            end=at(MON, 16, 30),
            estimate_minutes=600,
            estimate_days=1,
            people=("anna",),
        ),
    )
    card_copy = first_task(duplicate_task(ctx, "card"))
    assert (card_copy.estimate_minutes, card_copy.estimate_days) == (1080, 2)
    placed_copy = first_task(duplicate_task(ctx, "card", new_start=at(THU, 6, 30)))
    assert (placed_copy.start, placed_copy.end) == (at(THU, 6, 30), at(FRI, 12))
    task_copy = first_task(duplicate_task(ctx, "t"))
    assert (task_copy.estimate_minutes, task_copy.estimate_days) == (600, 1)
    assert (task_copy.start, task_copy.end) == (at(TUE, 6, 30), at(TUE, 16, 30))


def test_update_task_changes_the_days_of_the_estimate() -> None:
    ctx = ctx_with(make_task("card"))
    assert task_after(update_task(ctx, "card", estimate_days=1.5), "card").estimate_days == 1.5
    with pytest.raises(PlanningCommandError, match="estimate_days"):
        update_task(ctx, "card", estimate_days=-1)
