from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from openpyxl import load_workbook

from projectflow.core.planning.changes import DocumentChange, PlanningSnapshot
from projectflow.core.planning.commands import calendar_resolver
from projectflow.core.planning.local_store import LocalPlanningStore
from projectflow.core.planning.models import (
    Absence,
    PlanningDocument,
    PlanningMeta,
    PlanningTask,
    Resource,
    document_from_json,
)
from projectflow.core.planning.store import StoreUnavailableError
from projectflow.core.planning.walltime import MINUTES_PER_DAY, minute_of_day, wall_date
from projectflow.demo import (
    DEMO_DOSSIERS,
    DEMO_POSTE_NAME,
    build_demo_environment,
    demo_planning_documents,
    demo_project_numbers,
)
from projectflow.exceptions import GraphError

THURSDAY = date(2026, 10, 22)
NOON = 12 * 60
FRIDAY = 4


def _split(
    documents: tuple[PlanningDocument, ...],
) -> tuple[list[Resource], list[PlanningTask], list[Absence]]:
    resources = [d for d in documents if isinstance(d, Resource)]
    tasks = [d for d in documents if isinstance(d, PlanningTask)]
    absences = [d for d in documents if isinstance(d, Absence)]
    return resources, tasks, absences


def test_demo_roster_is_plausible() -> None:
    resources, _tasks, _absences = _split(demo_planning_documents(THURSDAY))

    people = [r for r in resources if not r.is_subcontractor]
    subcontractors = [r for r in resources if r.is_subcontractor]
    assert len(people) == 12
    assert {r.name for r in subcontractors} == {"Galvanisation", "Thermolaquage"}
    assert {r.team for r in people} == {"bureau", "atelier", "montage"}
    apprentices = [r for r in people if r.apprentice]
    assert len(apprentices) == 1
    assert [(off.weekday, off.reason) for off in apprentices[0].recurring_off] == [(2, "ecole")]
    assert len({r.initials for r in resources}) == len(resources)


def test_demo_planning_has_every_kind_of_situation() -> None:
    documents = demo_planning_documents(THURSDAY)
    resources, tasks, absences = _split(documents)
    by_id = {r.id: r for r in resources}
    scheduled = [t for t in tasks if t.scheduled]
    backlog = [t for t in tasks if not t.scheduled]

    assert sum(isinstance(d, PlanningMeta) for d in documents) == 1
    assert 5 <= len(backlog) <= 6
    assert any(t.origin == "sortie_dossier" and t.title.startswith("Fabrication") for t in backlog)
    assert [a.reason for a in absences] == ["vacances"]
    assert any(t.status == "provisional" for t in scheduled)
    assert any(len(t.assignments) == 2 for t in scheduled)
    unassigned = [t for t in scheduled if not t.assignments]
    assert [t.task_type for t in unassigned] == ["livraison"]
    assert any(len(t.assignments) == 3 for t in scheduled)
    # No « Requis » any more: every task keeps the model's default.
    assert all(t.required_people == 1 for t in tasks)
    assert any(by_id[r].is_subcontractor for t in scheduled for r in t.assignments)
    # Signed « initiales · poste », as every change of the shared planning.
    assert all(d.created_by.label and d.created_at for d in documents)


def test_demo_tasks_are_on_existing_demo_dossiers() -> None:
    numbers = demo_project_numbers(THURSDAY.year)
    _resources, tasks, _absences = _split(demo_planning_documents(THURSDAY))
    clients = {dossier.client for dossier in DEMO_DOSSIERS}

    with_dossier = [t for t in tasks if t.project_number]
    assert {t.project_number for t in with_dossier} == set(numbers)
    assert all(t.project_cache.client in clients for t in with_dossier)
    assert all(t.project_cache.designation for t in with_dossier)
    released = [t.project_cache.atelier_release for t in tasks if t.origin == "sortie_dossier"]
    assert all(date.fromisoformat(day) <= THURSDAY for day in released)


@pytest.mark.parametrize(
    "today",
    [
        THURSDAY,
        date(2026, 10, 19),  # Monday
        date(2026, 10, 23),  # Friday
        date(2026, 10, 24),  # Saturday
        date(2026, 12, 24),  # Christmas on Friday
        date(2027, 4, 1),  # Good Friday and Easter Monday around
    ],
)
def test_demo_tasks_keep_weekends_and_friday_afternoons_free(today: date) -> None:
    _resources, tasks, _absences = _split(demo_planning_documents(today))
    monday = today - timedelta(days=today.weekday())

    for task in tasks:
        if task.start is None or task.end is None:
            continue
        start_day, end_day = wall_date(task.start), wall_date(task.end)
        assert start_day.weekday() < 5, task.title
        assert end_day.weekday() < 5, task.title
        assert monday <= start_day < monday + timedelta(days=14), task.title
        if start_day.weekday() == FRIDAY:
            assert minute_of_day(task.start) < NOON, task.title
        if end_day.weekday() == FRIDAY:
            assert minute_of_day(task.end) <= NOON, task.title


def test_nobody_is_booked_twice_in_an_ordinary_week() -> None:
    documents = demo_planning_documents(THURSDAY)
    resources, tasks, absences = _split(documents)
    busy: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for task in tasks:
        if task.start is not None and task.end is not None:
            for resource_id in task.assignments:
                busy[resource_id].append((task.start, task.end, task.title))

    for resource_id, spans in busy.items():
        spans.sort()
        for (_start, end, title), (next_start, _end, next_title) in pairwise(spans):
            assert end <= next_start, f"{resource_id}: {title} / {next_title}"

    apprentice = next(r for r in resources if r.apprentice)
    for start, end, title in busy[apprentice.id]:
        days = {wall_date(start + offset) for offset in range(0, end - start, 30)}
        assert all(day.weekday() != 2 for day in days), title  # School on Wednesday.
    vacation = absences[0]
    for start, _end, title in busy[vacation.resource_id]:
        assert not vacation.first_day <= wall_date(start) <= vacation.last_day, title


def test_demo_tasks_fit_the_working_hours_of_their_rows() -> None:
    documents = demo_planning_documents(THURSDAY)
    snapshot = PlanningSnapshot().apply(DocumentChange.creation(d) for d in documents)
    calendar_for = calendar_resolver(snapshot)

    for task in snapshot.tasks.values():
        if task.start is None or task.end is None:
            continue
        calendar = calendar_for(next(iter(task.assignments), None))
        assert calendar.is_worked(task.start), task.title
        assert calendar.work_between(task.start, task.end) > 0, task.title
        assert task.end - task.start < 14 * MINUTES_PER_DAY


def test_demo_documents_read_back_from_json() -> None:
    for document in demo_planning_documents(THURSDAY):
        assert document_from_json(document.kind, document.to_json()) == document


@pytest.mark.asyncio
async def test_demo_store_is_seeded_once(tmp_path: Path) -> None:
    config, services = build_demo_environment(base_dir=tmp_path)
    poste = services.planning_poste_config()

    store = await services.create_planning_store(poste)
    first = await store.changes(None)
    again = await services.seed_planning(store)
    reopened = await services.create_planning_store(services.planning_poste_config())
    second = await reopened.changes(None)

    assert isinstance(store, LocalPlanningStore)
    assert store.directory == tmp_path / "Planning"
    assert len(first.documents) == len(demo_planning_documents(date.today()))
    assert again is False
    assert {(d.kind, d.doc_id, d.etag) for d in second.documents} == {
        (d.kind, d.doc_id, d.etag) for d in first.documents
    }
    assert config.paths.racine_projets is not None


def test_demo_poste_is_identified_and_kept_apart(tmp_path: Path) -> None:
    _config, services = build_demo_environment(base_dir=tmp_path)

    poste = services.planning_poste_config()

    assert poste.author().label == f"DEMO · {DEMO_POSTE_NAME}"
    assert poste.backend == "onedrive"
    assert services.planning_config_path() == tmp_path / "planning.json"
    assert services.planning_config_path().exists()
    poste.user_initials = "AB"
    services.save_planning_poste_config(poste)
    assert services.planning_poste_config().user_initials == "AB"


@pytest.mark.asyncio
async def test_demo_seed_creates_the_dossiers_in_free_rows(tmp_path: Path) -> None:
    config, services = build_demo_environment(base_dir=tmp_path)
    year = date.today().year
    workbook = load_workbook(services.workbook_path)
    workbook[str(year)]["C3"] = "Client existant"  # 4996 already used in this demo
    workbook.save(services.workbook_path)
    workbook.close()

    store = await services.create_planning_store(services.planning_poste_config())

    changes = await store.changes(None)
    numbers = {
        str(d.data.get("project_number"))
        for d in changes.documents
        if d.kind == "task" and d.data and d.data.get("project_number")
    }
    assert f"{year}-4995" not in numbers  # Still « disponible » for a new project.
    assert f"{year}-4996" not in numbers
    assert numbers == {f"{year}-{project_id}" for project_id in range(4997, 5005)}
    workbook = load_workbook(services.workbook_path)
    sheet = workbook[str(year)]
    rows = {row[0]: row for row in sheet.iter_rows(min_row=2, values_only=True)}
    workbook.close()
    assert rows[f"{year}-4996"][2] == "Client existant"
    assert rows[f"{year}-4997"][2] == DEMO_DOSSIERS[0].client
    assert rows[f"{year}-4997"][4] == DEMO_DOSSIERS[0].designation
    assert not rows[f"{year}-4995"][2]
    assert config.paths.racine_projets is not None
    project_dir = config.paths.racine_projets / str(year) / f"{year}-4997"
    fiche = services.fiche().locate_fiche(project_dir)
    assert services.fiche().read_fiche(fiche).societe == DEMO_DOSSIERS[0].client


@pytest.mark.asyncio
async def test_demo_sharepoint_shows_the_missing_authorization(tmp_path: Path) -> None:
    _config, services = build_demo_environment(base_dir=tmp_path)
    poste = services.planning_poste_config()
    poste.backend = "sharepoint"

    with pytest.raises(StoreUnavailableError, match="autorisation") as raised:
        await services.create_planning_store(poste)

    cause = raised.value.__cause__
    assert isinstance(cause, GraphError)
    assert cause.status_code == 403


@pytest.mark.asyncio
async def test_demo_onedrive_uses_the_demo_folder_without_any_request(tmp_path: Path) -> None:
    _config, services = build_demo_environment(base_dir=tmp_path)
    poste = services.planning_poste_config()
    assert poste.backend == "onedrive"

    store = await services.create_planning_store(poste)

    assert (await store.changes(None)).documents
    await store.aclose()
