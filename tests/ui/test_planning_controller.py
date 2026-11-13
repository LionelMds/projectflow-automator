from __future__ import annotations

import asyncio
import itertools
from collections.abc import Callable, Coroutine, Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QDate, QEvent, QObject, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMenu

from projectflow.core.planning import commands
from projectflow.core.planning import poste_config as poste_config_module
from projectflow.core.planning.changes import DocumentChange, PlanningSnapshot
from projectflow.core.planning.commands import calendar_resolver
from projectflow.core.planning.layout import UNASSIGNED_ROW_ID
from projectflow.core.planning.local_store import LocalPlanningStore
from projectflow.core.planning.models import (
    Absence,
    Author,
    PlanningDocument,
    PlanningMeta,
    PlanningTask,
    ProjectCache,
    RecurringOff,
    Resource,
)
from projectflow.core.planning.poste_config import PlanningPosteConfig
from projectflow.core.planning.service import PlanningEvent
from projectflow.core.planning.store import PlanningStore, StoreChanges, StoreUnavailableError
from projectflow.core.planning.walltime import wall_date, wall_time
from projectflow.core.repertoire_service import RepertoireRow, RepertoireSnapshot
from projectflow.exceptions import GraphError, ProjectCreationError
from projectflow.ui import planning_controller as planning_module
from projectflow.ui.planning.planning_tab import PlanningTab
from projectflow.ui.planning_controller import (
    PlanningController,
    PlanningHooks,
    capitalized,
    conflict_text,
    dossier_release,
    failure_status,
    half_day_start,
    merge_changes,
    next_free_half_day,
    project_from_row,
    remote_verb,
    task_name,
)

TODAY = date(2026, 10, 22)
"""A Thursday: the week shown runs from Monday 19 to Friday 23 October 2026."""
WEEK = date(2026, 10, 19)
NEXT_MONDAY = date(2026, 10, 26)
STAMP = "2026-10-20T08:00:00Z"
SEEDER = Author(initials="SBE", name="Sophie Berset", poste="BUREAU-2", poste_id="seed")


def at(day: date, hour: int, minute: int = 0) -> int:
    return wall_time(day, hour, minute)


# ------------------------------------------------------------------ data ----

PEOPLE: tuple[tuple[str, str, str, str, dict[str, Any]], ...] = (
    ("sbe", "Sophie Berset", "SBE", "bureau", {"role": "Dessinatrice"}),
    ("pla", "Pierre Lambiel", "PLA", "bureau", {"role": "Chef de projet"}),
    ("mre", "Marc Rey", "MRE", "atelier", {"role": "Serrurier"}),
    ("jdu", "Julien Dubois", "JDU", "atelier", {"role": "Soudeur"}),
    ("psi", "Paulo Silva", "PSI", "atelier", {"role": "Serrurier"}),
    ("dco", "Dario Costa", "DCO", "atelier", {"role": "Serrurier", "canton": "VD"}),
    (
        "kro",
        "Kevin Roth",
        "KRO",
        "atelier",
        {
            "role": "Apprenti 2e",
            "apprentice": True,
            "recurring_off": (RecurringOff(weekday=2, part="day", reason="ecole"),),
        },
    ),
    ("kdu", "Karim Dumas", "KDU", "montage", {"role": "Chef monteur"}),
    ("lbi", "Luca Bianchi", "LBI", "montage", {"role": "Monteur"}),
    ("ych", "Yann Chevalley", "YCH", "montage", {"role": "Monteur"}),
    ("nfa", "Nicolas Favre", "NFA", "montage", {"role": "Monteur"}),
    ("gal", "Galvanisation", "GAL", "soustraitants", {"kind_of_resource": "subcontractor"}),
    ("thl", "Thermolaquage", "THL", "soustraitants", {"kind_of_resource": "subcontractor"}),
)
DOSSIERS: tuple[tuple[str, str, str, str], ...] = (
    ("2026-118", "Müller SA", "M. Müller", "Garde-corps escalier"),
    ("2026-121", "Commune de Sion", "Mme Rey", "Marquise verre école"),
    ("2026-124", "PPE Les Vergers", "M. Gay", "Portail coulissant"),
    ("2026-129", "Bonvin & Fils", "M. Bonvin", "Main courante inox"),
    ("2026-131", "Zufferey SA", "Mme Zufferey", "Barrière parking"),
    ("2025-4987", "Rossier Sàrl", "M. Rossier", "Escalier extérieur"),
)


def stamped(document: PlanningDocument) -> PlanningDocument:
    return document.model_copy(
        update={
            "created_at": STAMP,
            "created_by": SEEDER,
            "updated_at": STAMP,
            "updated_by": SEEDER,
        }
    )


def resources() -> list[Resource]:
    rows: list[Resource] = []
    for index, (rid, name, initials, team, extra) in enumerate(PEOPLE):
        resource = Resource.model_validate(
            {
                "id": rid,
                "name": name,
                "initials": initials,
                "team": team,
                "order": (index + 1) * 1000,
                **extra,
            }
        )
        rows.append(resource)
    return rows


def cache(number: str) -> ProjectCache:
    for dossier, client, contact, designation in DOSSIERS:
        if dossier == number:
            return ProjectCache(client=client, contact=contact, designation=designation)
    return ProjectCache()


def task(  # noqa: PLR0917 - compact seed rows
    tid: str,
    number: str,
    title: str,
    start: int | None,
    end: int | None,
    people: Iterable[str],
    **extra: Any,
) -> PlanningTask:
    return PlanningTask.model_validate(
        {
            "id": tid,
            "project_number": number,
            "project_cache": cache(number).model_dump(),
            "title": title,
            "start": start,
            "end": end,
            "assignments": {person: {} for person in people},
            **extra,
        }
    )


def seed_documents(week: date = WEEK) -> list[PlanningDocument]:
    """Build a plausible week of the workshop around ``week`` (a Monday)."""
    day = [week + timedelta(days=offset) for offset in range(12)]
    tasks = [
        task(
            "t-releve",
            "2026-121",
            "Relevé de cotes",
            at(day[0], 6, 30),
            at(day[0], 12),
            ["sbe"],
            task_type="releve",
        ),
        task(
            "t-dessin",
            "2026-124",
            "Plans d'atelier portail",
            at(day[0], 13),
            at(day[2], 16, 30),
            ["pla"],
            task_type="dessin",
        ),
        task(
            "t-fab",
            "2026-118",
            "Fabrication garde-corps",
            at(day[0], 6, 30),
            at(day[2], 16, 30),
            ["mre", "jdu"],
            task_type="soudure",
            required_people=2,
        ),
        task(
            "t-debit",
            "2026-129",
            "Débit main courante",
            at(day[1], 6, 30),
            at(day[1], 16, 30),
            ["psi"],
            task_type="debit",
        ),
        task(
            "t-portail",
            "2026-124",
            "Soudure portail",
            at(day[2], 6, 30),
            at(day[4], 12),
            ["psi", "dco"],
            task_type="soudure",
            status="provisional",
        ),
        task(
            "t-stock",
            "",
            "Rangement stock tubes",
            at(day[0], 6, 30),
            at(day[0], 16, 30),
            ["kro"],
            task_type="interne",
        ),
        task(
            "t-aide",
            "2026-129",
            "Main courante - aide",
            at(day[3], 6, 30),
            at(day[4], 12),
            ["kro"],
            task_type="finition",
        ),
        task(
            "t-pose",
            "2026-121",
            "Pose marquise verre",
            at(day[0], 6, 30),
            at(day[1], 16, 30),
            ["kdu", "ych"],
            task_type="montage",
            required_people=3,
            urgent=True,
        ),
        task(
            "t-balcons",
            "2026-131",
            "Pose barrière parking",
            at(day[2], 6, 30),
            at(day[4], 12),
            ["kdu", "ych", "nfa"],
            task_type="montage",
        ),
        task(
            "t-galva",
            "2026-118",
            "Galvanisation garde-corps",
            at(day[3], 6, 30),
            at(day[7], 16, 30),
            ["gal"],
            task_type="soustraitance",
        ),
        task(
            "t-thermo",
            "2026-124",
            "Thermolaquage portail",
            at(day[7], 6, 30),
            at(day[8], 16, 30),
            ["thl"],
            task_type="soustraitance",
        ),
        task(
            "t-livraison",
            "2026-121",
            "Livraison verre",
            at(day[4], 6, 30),
            at(day[4], 12),
            [],
            task_type="livraison",
        ),
        task(
            "t-next",
            "2026-118",
            "Pose garde-corps",
            at(day[7], 6, 30),
            at(day[8], 16, 30),
            ["kdu", "lbi"],
            task_type="montage",
        ),
    ]
    cards = [
        task(
            "c-escalier",
            "2025-4987",
            "Escalier extérieur - fabrication",
            None,
            None,
            [],
            estimate_days=3,
            backlog_rank="b",
            origin="sortie_dossier",
        ),
        task(
            "c-barriere",
            "2026-131",
            "Barrière parking - finitions",
            None,
            None,
            ["nfa"],
            estimate_days=2,
            required_people=2,
            backlog_rank="c",
        ),
        task(
            "c-sav",
            "2026-124",
            "SAV portail - réglage",
            None,
            None,
            [],
            estimate_days=0.5,
            urgent=True,
            backlog_rank="d",
            task_type="sav",
        ),
        task(
            "c-metre",
            "2026-129",
            "Relevé main courante",
            None,
            None,
            ["sbe"],
            estimate_minutes=180,
            backlog_rank="e",
            task_type="releve",
        ),
        task(
            "c-interne",
            "",
            "Entretien machines",
            None,
            None,
            [],
            estimate_days=1,
            backlog_rank="f",
            task_type="interne",
        ),
    ]
    vacation = Absence.model_validate(
        {
            "id": "a-vac",
            "resource_id": "lbi",
            "reason": "vacances",
            "first_day": day[0].isoformat(),
            "last_day": day[4].isoformat(),
        }
    )
    return [stamped(item) for item in (PlanningMeta(), *resources(), *tasks, *cards, vacation)]


async def seed_store(directory: Path, documents: Iterable[PlanningDocument] | None = None) -> None:
    store = LocalPlanningStore(directory)
    for document in documents if documents is not None else seed_documents():
        number = document.project_number if isinstance(document, PlanningTask) else ""
        await store.create(document.kind, document.id, document.to_json(), project=number)
    await store.aclose()


def repertoire_rows(year: int) -> tuple[RepertoireRow, ...]:
    rows = [
        RepertoireRow(row_index=index, values=(number, "", client, contact, designation))
        for index, (number, client, contact, designation) in enumerate(DOSSIERS)
        if number.startswith(str(year))
    ]
    rows.append(RepertoireRow(row_index=99, values=(f"{year}-999", "", "", "", "")))
    return tuple(rows)


# --------------------------------------------------------------- harness ----


class Hooks:
    """Records what the controller asks of the application."""

    def __init__(self) -> None:
        self.tasks: set[asyncio.Task[None]] = set()
        self.badges: list[int] = []
        self.logs: list[str] = []
        self.opened: list[tuple[str, str]] = []
        self.saved: list[PlanningPosteConfig] = []
        self.members_asked = 0

    async def read_repertoire(self, year: int) -> RepertoireSnapshot:
        return RepertoireSnapshot(year=year, rows=repertoire_rows(year), next_available=None)

    async def planner_members(self) -> list[tuple[str, str, str]]:
        self.members_asked += 1
        return [("m-1", "Marc Rey", "marc.rey@example.com")]

    def schedule(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.ensure_future(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def ask(self, ask: Callable[[], Any]) -> Any:
        return ask()

    def opener(self, name: str) -> Callable[[str], None]:
        return lambda value: self.opened.append((name, value))

    def build(self) -> PlanningHooks:
        return PlanningHooks(
            read_repertoire=self.read_repertoire,
            open_project_dir=self.opener("folder"),
            open_fiche=self.opener("fiche"),
            show_in_repertoire=self.opener("repertoire"),
            load_in_creation=self.opener("creation"),
            prepare_sortie=self.opener("sortie"),
            open_settings=self.opener("settings"),
            planner_members=self.planner_members,
            set_nav_badge=self.badges.append,
            schedule=self.schedule,
            ask=self.ask,
            log=self.logs.append,
            save_config=self.saved.append,
        )


class Clock:
    def __init__(self) -> None:
        self._seconds = itertools.count()

    def __call__(self) -> str:
        second = next(self._seconds)
        return f"2026-10-22T09:{second // 60 % 60:02d}:{second % 60:02d}Z"


@pytest.fixture
def data_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    """Cache and settings of the tests in ``tmp_path``; ``dirs["data"]`` may be switched."""
    dirs = {"data": tmp_path / "data-a", "config": tmp_path / "config"}
    monkeypatch.setattr(poste_config_module, "data_dir", lambda: dirs["data"])
    monkeypatch.setattr(poste_config_module, "config_dir", lambda: dirs["config"])
    return dirs


def make_config(
    store: Path, *, initials: str = "LM", poste: str = "BUREAU-1", poste_id: str = "poste-a"
) -> PlanningPosteConfig:
    return PlanningPosteConfig(
        local_directory=str(store),
        onedrive_folder=store.name,  # Another folder: another store, as on OneDrive.
        user_initials=initials,
        user_name="Lionel" if initials == "LM" else initials,
        poste_name=poste,
        poste_id=poste_id,
    )


async def local_factory(config: PlanningPosteConfig) -> PlanningStore:
    return LocalPlanningStore(config.local_store_directory())


def make_controller(
    qtbot: Any,
    config: PlanningPosteConfig,
    *,
    factory: Callable[[PlanningPosteConfig], Any] = local_factory,
    hooks: Hooks | None = None,
) -> tuple[PlanningController, PlanningTab, Hooks]:
    tab = PlanningTab()
    qtbot.addWidget(tab)
    tab.resize(1440, 860)
    tab.show()
    qtbot.waitExposed(tab)
    recorder = hooks or Hooks()
    controller = PlanningController(
        tab,
        poste_config=config,
        store_factory=factory,
        hooks=recorder.build(),
        today=lambda: TODAY,
        clock=Clock(),
    )
    return controller, tab, recorder


async def wait_for(
    condition: Callable[[], bool], *, seconds: float = 5.0, poke: Any = None
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while not condition():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        if poke is not None:
            poke()
        await asyncio.sleep(0.05)


async def started(controller: PlanningController) -> PlanningController:
    await controller.start()
    await wait_for(lambda: bool(controller.projects))
    controller.refresh_now()
    return controller


async def close(*controllers: PlanningController) -> None:
    for controller in controllers:
        await controller.aclose()


def snapshot_of(controller: PlanningController) -> Any:
    service = controller.service
    assert service is not None
    return service.snapshot


def bar_keys(tab: PlanningTab) -> set[tuple[str, str]]:
    model = tab.grid.model
    assert model is not None
    return set(model.bars_by_key)


def toast_texts(tab: PlanningTab) -> list[str]:
    return [
        f"{toast.tag_label.text()} {toast.message()}".strip()
        for toast in tab.toasts.toasts()
        if not toast.dismissed_already
    ]


def created_task(controller: PlanningController, before: set[str]) -> PlanningTask:
    snapshot = snapshot_of(controller)
    new = [item for tid, item in snapshot.tasks.items() if tid not in before]
    assert len(new) == 1, new
    return new[0]


# ------------------------------------------------------------- pure parts ----


def test_helpers_name_tasks_and_rows() -> None:
    fab = task("t", "2026-118", "Fabrication garde-corps", None, None, [])
    assert task_name(fab) == "2026-118 - Fabrication garde-corps"
    assert task_name(task("t", "", "", None, None, [])) == "tâche sans titre"
    assert project_from_row(("2026-118", "", "Müller SA", "", "Garde-corps")) is not None
    assert project_from_row(("2026-999", "", "", "", "")) is None
    moved = fab.model_copy(update={"start": at(WEEK, 7), "end": at(WEEK, 9)})
    later = moved.model_copy(update={"start": at(WEEK, 8), "end": at(WEEK, 10)})
    assert remote_verb(None, moved) == "a planifié"
    assert remote_verb(None, fab) == "a ajouté à planifier"
    assert remote_verb(moved, later) == "a déplacé"
    assert remote_verb(moved, fab) == "a renvoyé à planifier"
    assert remote_verb(moved, None) == "a supprimé"


def test_failure_status_tells_permission_sign_in_and_offline_apart() -> None:
    denied = StoreUnavailableError("Autorisation manquante")
    denied.__cause__ = GraphError("403", status_code=403)
    assert failure_status(denied).state == "forbidden"
    assert failure_status(StoreUnavailableError("Pas de réseau")).state == "offline"
    assert failure_status(StoreUnavailableError("autorisation manquante")).state == "forbidden"
    assert failure_status(ValueError("Liste introuvable")).state == "error"
    assert failure_status(denied).read_only


def test_merge_changes_keeps_one_change_per_document() -> None:

    first = task("t", "2026-118", "A", None, None, [])
    second = first.model_copy(update={"title": "B"})
    third = second.model_copy(update={"title": "C"})
    merged = merge_changes(
        (DocumentChange.update(first, second), DocumentChange.update(second, third))
    )
    assert len(merged) == 1
    assert merged[0].before == first
    assert merged[0].after == third


# ------------------------------------------------------------ controller ----


@pytest.mark.asyncio
async def test_start_shows_the_seeded_planning(qtbot: Any, tmp_path: Path, data_dirs: Any) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    keys = bar_keys(tab)
    assert ("t-fab", "mre") in keys
    assert ("t-fab", "jdu") in keys
    assert ("t-livraison", UNASSIGNED_ROW_ID) in keys
    assert tab.backlog.count() == 5
    assert controller.projects["2026-118"].client == "Müller SA"
    assert tab.badge.text() != "Planning local (ce poste)"  # Always a shared planning now.
    assert not controller.read_only
    assert tab.identity_banner.isHidden()
    await close(controller)


@pytest.mark.asyncio
async def test_quick_entry_creates_a_task_on_the_grid(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    before = set(snapshot_of(controller).tasks)
    tab.quick.set_text("118 montage garde-corps KDU lun")
    assert tab.quick.submit()
    new = created_task(controller, before)
    assert new.assignee_ids == ("kdu",)
    assert new.start == at(NEXT_MONDAY, 6, 30)
    assert new.project_number == "2026-118"
    assert new.project_cache.client == "Müller SA"
    assert new.origin == "quick_entry"
    assert new.created_by.initials == "LM"
    service = controller.service
    assert service is not None
    assert service.undo_label is not None
    assert service.undo_label.startswith("Créer 2026-118")
    # The bar is next week: the view goes there and the bar is selected.
    controller.refresh_now()
    assert tab.anchor == NEXT_MONDAY
    assert (new.id, "kdu") in bar_keys(tab)
    assert tab.grid.selected_bars() == [(new.id, "kdu")]
    await close(controller)


@pytest.mark.asyncio
async def test_quick_entry_routes_to_unassigned_backlog_and_absence(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)

    before = set(snapshot_of(controller).tasks)
    tab.quick.set_text("livraison 124 ven matin")
    assert tab.quick.submit()
    delivery = created_task(controller, before)
    assert delivery.assignee_ids == ()
    assert delivery.start == at(date(2026, 10, 23), 6, 30)
    controller.refresh_now()
    assert (delivery.id, UNASSIGNED_ROW_ID) in bar_keys(tab)

    before = set(snapshot_of(controller).tasks)
    tab.quick.set_text("129 fabrication main courante 3j")
    assert tab.quick.submit()
    card = created_task(controller, before)
    assert card.start is None
    assert card.estimate_days == 3
    controller.refresh_now()
    assert card.id in [item.task_id for item in tab.backlog.cards()]
    assert tab.backlog.current_card_id() == card.id

    tab.quick.set_text("/vac YCH 26-30.10")
    assert tab.quick.submit()
    absences = [
        item
        for item in snapshot_of(controller).absences.values()
        if item.resource_id == "ych" and not item.deleted
    ]
    assert len(absences) == 1
    assert absences[0].reason == "vacances"
    assert (absences[0].first_day, absences[0].last_day) == (NEXT_MONDAY, date(2026, 10, 30))
    assert any("Absence enregistrée" in text for text in toast_texts(tab))
    await close(controller)


@pytest.mark.asyncio
async def test_grid_gestures_move_add_and_copy(qtbot: Any, tmp_path: Path, data_dirs: Any) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    service = controller.service
    assert service is not None

    new_start = at(WEEK + timedelta(days=3), 6, 30)
    tab.grid.move_requested.emit("t-debit", "psi", "psi", new_start, "move")
    assert snapshot_of(controller).tasks["t-debit"].start == new_start
    assert service.undo_label == "Déplacer 2026-129 - Débit main courante"

    tab.grid.move_requested.emit("t-debit", "psi", "mre", new_start, "move")
    assert snapshot_of(controller).tasks["t-debit"].assignee_ids == ("mre",)
    assert service.undo_label == "Déplacer 2026-129 - Débit main courante vers MRE"

    tab.grid.move_requested.emit("t-debit", "mre", "jdu", new_start, "add")
    assert snapshot_of(controller).tasks["t-debit"].assignee_ids == ("mre", "jdu")
    assert service.undo_label == "Ajouter JDU à 2026-129 - Débit main courante"

    before = set(snapshot_of(controller).tasks)
    copy_start = at(WEEK + timedelta(days=1), 6, 30)  # another time: a real copy
    tab.grid.move_requested.emit("t-debit", "mre", "psi", copy_start, "copy")
    copy = created_task(controller, before)
    assert copy.assignee_ids == ("psi",)
    assert copy.start == copy_start
    assert snapshot_of(controller).tasks["t-debit"].assignee_ids == ("mre", "jdu")

    # Ctrl+drag onto someone else at the very same time adds that person, no copy.
    before = set(snapshot_of(controller).tasks)
    tab.grid.move_requested.emit("t-debit", "mre", "psi", new_start, "copy")
    assert set(snapshot_of(controller).tasks) == before
    assert snapshot_of(controller).tasks["t-debit"].assignee_ids == ("mre", "jdu", "psi")
    assert service.undo_label == "Ajouter PSI à 2026-129 - Débit main courante"
    await close(controller)


@pytest.mark.asyncio
async def test_resize_shift_reassign_and_card_drop(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    tuesday = WEEK + timedelta(days=1)

    tab.grid.resize_requested.emit("t-debit", "psi", None, at(tuesday, 12))
    assert snapshot_of(controller).tasks["t-debit"].end == at(tuesday, 12)

    tab.grid.shift_requested.emit([("t-debit", "psi")], 60, True)  # noqa: FBT003
    assert snapshot_of(controller).tasks["t-debit"].end == at(tuesday, 14)

    tab.grid.shift_requested.emit([("t-fab", "mre"), ("t-fab", "jdu")], 30, False)  # noqa: FBT003
    assert snapshot_of(controller).tasks["t-fab"].start == at(WEEK, 7)

    tab.grid.reassign_requested.emit("t-debit", "psi", "dco", False)  # noqa: FBT003
    assert snapshot_of(controller).tasks["t-debit"].assignee_ids == ("dco",)

    tab.grid.card_dropped.emit("c-barriere", "kdu", at(NEXT_MONDAY, 6, 30))
    placed = snapshot_of(controller).tasks["c-barriere"]
    assert placed.assignee_ids == ("kdu",)
    assert placed.start == at(NEXT_MONDAY, 6, 30)
    # On the row of a person foreseen, every person foreseen gets it.
    tab.grid.card_dropped.emit("c-metre", "sbe", at(NEXT_MONDAY, 6, 30))
    assert snapshot_of(controller).tasks["c-metre"].assignee_ids == ("sbe",)
    await close(controller)


@pytest.mark.asyncio
async def test_delete_removes_one_person_then_deletes_the_task(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    backlog = {task.id for task in snapshot_of(controller).backlog()}

    tab.grid.delete_requested.emit([("t-fab", "jdu")], False)  # noqa: FBT003
    fab = snapshot_of(controller).tasks["t-fab"]
    assert fab.assignee_ids == ("mre",)
    assert fab.scheduled

    # The last person gone, the task is deleted, not sent back to « À planifier ».
    tab.grid.delete_requested.emit([("t-fab", "mre")], False)  # noqa: FBT003
    assert snapshot_of(controller).tasks["t-fab"].deleted
    assert any(text.endswith("supprimée") for text in toast_texts(tab))
    controller.undo()
    fab = snapshot_of(controller).tasks["t-fab"]
    assert (fab.deleted, fab.scheduled, fab.assignee_ids) == (False, True, ("mre",))

    # Two bars of one task in one press: one command, no conflicting saves.
    tab.grid.delete_requested.emit([("t-pose", "kdu"), ("t-pose", "ych")], False)  # noqa: FBT003
    assert snapshot_of(controller).tasks["t-pose"].deleted

    # « Non affecté » has nobody to remove: the task is deleted.
    tab.grid.delete_requested.emit([("t-livraison", UNASSIGNED_ROW_ID)], False)  # noqa: FBT003
    assert snapshot_of(controller).tasks["t-livraison"].deleted
    assert "2026-121 - Livraison verre supprimée" in toast_texts(tab)
    assert {task.id for task in snapshot_of(controller).backlog()} == backlog
    await close(controller)


@pytest.mark.asyncio
async def test_shift_delete_deletes_for_everybody_and_annuler_restores(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)

    tab.grid.delete_requested.emit([("t-fab", "mre"), ("t-fab", "jdu")], True)  # noqa: FBT003
    assert snapshot_of(controller).tasks["t-fab"].deleted
    toast = next(item for item in tab.toasts.toasts() if "supprimé pour tous" in item.message())
    assert toast.action_button.text() == "Annuler"
    toast.trigger()
    assert not snapshot_of(controller).tasks["t-fab"].deleted
    controller.refresh_now()
    assert ("t-fab", "mre") in bar_keys(tab)
    await close(controller)


@pytest.mark.asyncio
async def test_undo_and_redo_from_the_shortcuts(qtbot: Any, tmp_path: Path, data_dirs: Any) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    original = snapshot_of(controller).tasks["t-debit"].start
    moved = at(WEEK + timedelta(days=3), 6, 30)
    tab.grid.move_requested.emit("t-debit", "psi", "psi", moved, "move")
    controller.refresh_now()
    assert "Déplacer 2026-129" in tab.undo_button.toolTip()

    tab.shortcuts["undo"][0].activated.emit()
    assert snapshot_of(controller).tasks["t-debit"].start == original
    assert any(text.startswith("Annulé : Déplacer") for text in toast_texts(tab))
    controller.refresh_now()
    assert "Déplacer 2026-129" in tab.redo_button.toolTip()

    tab.shortcuts["redo"][1].activated.emit()  # Ctrl+Maj+Z
    assert snapshot_of(controller).tasks["t-debit"].start == moved
    tab.redo_requested.emit()
    assert any("Rien à rétablir" in text for text in toast_texts(tab))
    await close(controller)


@pytest.mark.asyncio
async def test_detail_panel_edits_go_through_commands(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    service = controller.service
    assert service is not None

    tab.grid.detail_requested.emit("t-fab", "mre")
    assert tab.side.face() == "detail"
    assert tab.detail.task_id() == "t-fab"

    tab.detail.field_changed.emit("t-fab", "title", "Fabrication garde-corps inox")
    assert snapshot_of(controller).tasks["t-fab"].title == "Fabrication garde-corps inox"
    assert service.undo_label == "Modifier le titre de 2026-118 - Fabrication garde-corps"

    tab.detail.status_changed.emit("t-fab", "done")
    assert snapshot_of(controller).tasks["t-fab"].status == "done"

    tab.detail.people_changed.emit("t-fab", ["psi"], [])
    assert snapshot_of(controller).tasks["t-fab"].assignee_ids == ("mre", "jdu", "psi")

    # « Détacher » is no longer in the detail: the right click of a bar keeps it.
    menu = controller.bar_menu("t-fab", "mre")
    assert menu is not None
    next(action for action in menu.actions() if action.text() == "Détacher MRE").trigger()
    fab = snapshot_of(controller).tasks["t-fab"]
    assert fab.assignee_ids == ("jdu", "psi")
    assert any(
        item.assignee_ids == ("mre",) and item.id != "t-fab" and item.title == fab.title
        for item in snapshot_of(controller).live_tasks()
    )
    await close(controller)


@pytest.mark.asyncio
async def test_two_postes_see_each_other_and_the_remote_toast(
    qtbot: Any, tmp_path: Path, data_dirs: dict[str, Path]
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    first, first_tab, _first_hooks = make_controller(qtbot, make_config(store))
    await started(first)
    data_dirs["data"] = tmp_path / "data-b"  # the second poste has its own cache
    second, second_tab, second_hooks = make_controller(
        qtbot, make_config(store, initials="PLA", poste="ATELIER-1", poste_id="poste-b")
    )
    await started(second)
    second.set_visible(visible=True, window_active=True)

    new_start = at(WEEK + timedelta(days=3), 6, 30)
    first_tab.grid.move_requested.emit("t-debit", "psi", "psi", new_start, "move")
    await wait_for(
        lambda: snapshot_of(second).tasks["t-debit"].start == new_start,
        poke=lambda: (first.wake(), second.wake()),
    )
    assert snapshot_of(second).tasks["t-debit"].updated_by.label == "LM · BUREAU-1"
    assert "LM · BUREAU-1 a déplacé « 2026-129 - Débit main courante »" in toast_texts(second_tab)

    # A card added by the other poste while the tab is hidden counts in the nav badge.
    second.set_visible(visible=False)
    first_tab.quick.set_text("131 garde-corps balcon 2j")
    assert first_tab.quick.submit()
    await wait_for(
        lambda: bool(second_hooks.badges) and second_hooks.badges[-1] == 1,
        poke=lambda: (first.wake(), second.wake()),
    )
    second.set_visible(visible=True)
    assert second_hooks.badges[-1] == 0
    await close(first, second)


@pytest.mark.asyncio
async def test_a_store_without_permission_is_read_only_from_the_cache(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    config = make_config(store)
    first, _tab, _hooks = make_controller(qtbot, config)
    await started(first)
    await close(first)

    async def denied(_config: PlanningPosteConfig) -> PlanningStore:
        error = StoreUnavailableError("Autorisation manquante pour la liste du planning.")
        error.__cause__ = GraphError("Forbidden", status_code=403)
        raise error

    controller, tab, hooks = make_controller(qtbot, config, factory=denied)
    await started(controller)
    assert controller.sync_status.state == "forbidden"
    assert controller.read_only
    assert tab.badge.text() == "Autorisation Microsoft manquante"
    assert not tab.read_only_banner.isHidden()
    assert "Autorisation manquante" in tab.read_only_banner.message_label.text()
    assert any("Autorisation manquante" in line for line in hooks.logs)
    # Readable from the cache of the previous session.
    assert "t-fab" in snapshot_of(controller).tasks
    controller.refresh_now()
    assert ("t-fab", "mre") in bar_keys(tab)
    # Every edit is refused, with the reason.
    before = set(snapshot_of(controller).tasks)
    tab.quick.set_text("118 montage garde-corps KDU lun")
    assert not tab.quick.submit()
    assert set(snapshot_of(controller).tasks) == before
    tab.grid.move_requested.emit("t-debit", "psi", "psi", at(WEEK, 13), "move")
    assert snapshot_of(controller).tasks["t-debit"].start == at(WEEK + timedelta(days=1), 6, 30)
    assert any("Autorisation manquante" in text for text in toast_texts(tab))
    await close(controller)


@pytest.mark.asyncio
async def test_ensure_backlog_card_is_idempotent(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    created = await controller.ensure_backlog_card(
        "2026-129",
        designation="Main courante inox",
        client="Bonvin & Fils",
        contact="M. Bonvin",
        managed_by="LM",
        released=TODAY,
    )
    assert created
    cards = [
        item
        for item in snapshot_of(controller).live_tasks()
        if item.origin == "sortie_dossier" and item.project_number == "2026-129"
    ]
    assert len(cards) == 1
    assert cards[0].title == "Fabrication – Main courante inox"  # noqa: RUF001
    assert cards[0].project_cache.atelier_release == "2026-10-22"
    assert cards[0].start is None
    again = await controller.ensure_backlog_card(
        "2026-129", designation="Main courante inox", client="Bonvin & Fils"
    )
    assert not again
    assert (
        len(
            [
                item
                for item in snapshot_of(controller).live_tasks()
                if item.origin == "sortie_dossier" and item.project_number == "2026-129"
            ]
        )
        == 1
    )
    # A finished card lets the next release make a new one.
    tab.detail.status_changed.emit(cards[0].id, "done")
    assert await controller.ensure_backlog_card(
        "2026-129", designation="Main courante inox", client="Bonvin", released=TODAY
    )
    titles = sorted(
        item.title
        for item in snapshot_of(controller).live_tasks()
        if item.origin == "sortie_dossier" and item.project_number == "2026-129"
    )
    assert titles[-1] == "Fabrication – Main courante inox (2e sortie 22.10)"  # noqa: RUF001
    await close(controller)


@pytest.mark.asyncio
async def test_no_sortie_card_when_the_shared_setting_is_off(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    documents = [
        item
        if not isinstance(item, PlanningMeta)
        else item.model_copy(update={"auto_card_on_sortie": False})
        for item in seed_documents()
    ]
    await seed_store(store, documents)
    controller, _tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    assert not await controller.ensure_backlog_card("2026-131", designation="X", client="Y")
    await close(controller)
    # Never raises, even closed.
    assert not await controller.ensure_backlog_card("2026-131", designation="X", client="Y")


@pytest.mark.asyncio
async def test_reconfigure_identity_and_store(qtbot: Any, tmp_path: Path, data_dirs: Any) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    unidentified = make_config(store, initials="")
    controller, tab, _hooks = make_controller(qtbot, unidentified)
    await started(controller)
    assert not tab.identity_banner.isHidden()

    identified = make_config(store, initials="PLA", poste="ATELIER-1")
    await controller.reconfigure(identified)
    assert controller.poste_config is identified
    assert tab.identity_banner.isHidden()
    tab.grid.move_requested.emit("t-debit", "psi", "psi", at(WEEK, 13), "move")
    assert snapshot_of(controller).tasks["t-debit"].updated_by.label == "PLA · ATELIER-1"
    service = controller.service

    other = tmp_path / "other-store"
    moved = make_config(other, initials="PLA", poste="ATELIER-1")
    await controller.reconfigure(moved)
    assert controller.service is not service
    assert "t-debit" not in snapshot_of(controller).tasks
    await close(controller)


@pytest.mark.asyncio
async def test_aclose_is_idempotent_and_keeps_the_cache(
    qtbot: Any, tmp_path: Path, data_dirs: dict[str, Path]
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    config = make_config(store)
    controller, tab, hooks = make_controller(qtbot, config)
    await started(controller)
    tab.grid.move_requested.emit("t-debit", "psi", "psi", at(WEEK, 13), "move")
    await controller.aclose()
    await controller.aclose()
    assert controller.service is None
    assert (config.cache_directory() / "planning-cache.json").exists()
    await asyncio.sleep(0)
    assert not [task for task in hooks.tasks if not task.done()]
    reopened = LocalPlanningStore(store)
    stored = await reopened.get("task", "t-debit")
    assert stored is not None
    assert stored.data is not None
    assert stored.data["start"] == "2026-10-19T13:00"
    await reopened.aclose()


@pytest.mark.asyncio
async def test_cell_editor_creates_and_tab_goes_to_the_next_free_half_day(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    friday = WEEK + timedelta(days=4)
    tab.grid.cell_activated.emit("mre", at(WEEK + timedelta(days=3), 6, 30))
    assert tab.editor.is_open()
    assert tab.editor.label_text().startswith("MRE · jeu 22.10 06:30")
    before = set(snapshot_of(controller).tasks)
    tab.editor.bar.set_text("118 finitions")
    assert tab.editor.bar.submit(advance=True)
    new = created_task(controller, before)
    assert new.assignee_ids == ("mre",)
    assert new.start == at(WEEK + timedelta(days=3), 6, 30)
    assert new.end == at(WEEK + timedelta(days=3), 12)
    assert tab.editor.is_open()
    assert tab.editor.label_text().startswith("MRE · jeu 22.10 13:00")
    tab.editor.close_editor()

    # A range drawn over two rows makes one shared task.
    tab.grid.range_selected.emit(["kdu", "lbi"], at(friday, 6, 30), at(friday, 12))
    before = set(snapshot_of(controller).tasks)
    tab.editor.bar.set_text("121 réglage marquise")
    assert tab.editor.bar.submit()
    shared = created_task(controller, before)
    assert shared.assignee_ids == ("kdu", "lbi")
    assert not tab.editor.is_open()
    await close(controller)


def test_next_free_half_day_skips_busy_periods() -> None:

    busy = task("t", "", "x", at(WEEK, 6, 30), at(WEEK, 16, 30), ["mre"])
    snapshot = PlanningSnapshot(tasks={"t": busy}, resources={r.id: r for r in resources()})
    calendar = calendar_resolver(snapshot)("mre")
    cell = next_free_half_day(snapshot, "mre", at(WEEK, 12), calendar)
    assert cell == (at(WEEK + timedelta(days=1), 6, 30), at(WEEK + timedelta(days=1), 12))
    friday = WEEK + timedelta(days=4)
    cell = next_free_half_day(snapshot, "mre", at(friday, 6, 30), calendar)
    assert cell == (at(friday, 6, 30), at(friday, 12))
    assert wall_date(next_free_half_day(snapshot, "mre", at(friday, 12), calendar)[0]) == (  # type: ignore[index]
        NEXT_MONDAY
    )


@pytest.mark.asyncio
async def test_conflict_toast_restores_the_lost_version(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    service = controller.service
    assert service is not None
    lost = snapshot_of(controller).tasks["t-debit"]
    tab.grid.delete_requested.emit([("t-debit", "psi")], True)  # noqa: FBT003
    assert snapshot_of(controller).tasks["t-debit"].deleted
    service.notify(
        PlanningEvent(
            frozenset({("task", "t-debit")}),
            "conflict",
            "Cette tâche a été supprimée sur un autre poste entre-temps : votre "
            "modification n'a pas été enregistrée.",
            restorable=(lost,),
        )
    )
    toast = next(item for item in tab.toasts.toasts() if "supprimée" in item.message())
    assert toast.message().startswith("« 2026-129 - Débit main courante » a été supprimée")
    assert toast.action_button.text() == "Restaurer"
    toast.trigger()
    assert not snapshot_of(controller).tasks["t-debit"].deleted
    assert any("supprimée sur un autre poste" in line for line in hooks.logs)

    # Another poste's change: the author in the tag only, the task by its name.
    other = Author(initials="PLA", name="Pierre Lambiel", poste="ATELIER-1", poste_id="poste-b")
    service.notify(
        PlanningEvent(
            frozenset({("task", "t-fab")}),
            "conflict",
            "PLA · ATELIER-1 a modifié cette tâche entre-temps",
            author=other,
        )
    )
    toast = next(item for item in tab.toasts.toasts() if "entre-temps" in item.message())
    assert toast.tag_label.text() == "PLA · ATELIER-1"
    assert toast.message() == "a modifié « 2026-118 - Fabrication garde-corps » entre-temps"
    await close(controller)


def test_conflict_text_without_author_or_task() -> None:
    event = PlanningEvent(frozenset({("meta", "meta")}), "conflict", "Les réglages ont changé.")
    assert conflict_text(event, PlanningSnapshot()) == ("", "Les réglages ont changé.")
    lost = task("t-x", "2026-118", "Pose", None, None, [])
    event = PlanningEvent(
        frozenset({("task", "t-x")}),
        "conflict",
        "Votre modification est perdue.",
        restorable=(lost,),
    )
    assert conflict_text(event, PlanningSnapshot()) == (
        "",
        "« 2026-118 - Pose » : Votre modification est perdue.",
    )


@pytest.mark.asyncio
async def test_hover_card_menus_and_summary(qtbot: Any, tmp_path: Path, data_dirs: Any) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, _tab, hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    info = controller.hover_info("t-fab")
    assert info is not None
    assert info.number == "2026-118"
    assert info.client == "Müller SA"
    assert info.people == ("MRE", "JDU")
    assert info.duration == "3 j"
    assert len(info.dossier_tasks) == 3
    assert info.author.startswith("Créé par SBE · BUREAU-2")

    menu = controller.bar_menu("t-fab", "mre")
    assert menu is not None
    texts = [action.text() for action in menu.actions()]
    assert "Détacher MRE" in texts
    assert "Supprimer pour tous\tMaj+Suppr" in texts
    open_folder = next(action for action in menu.actions() if action.text() == "Ouvrir le dossier")
    open_folder.trigger()
    assert ("folder", "2026-118") in hooks.opened

    cell = controller.cell_menu("lbi", at(NEXT_MONDAY, 6, 30))
    assert cell is not None
    absence_menu = next(
        child for child in cell.findChildren(QMenu) if child.title() == "Absence ce jour"
    )
    vacation = next(action for action in absence_menu.actions() if action.text() == "Vacances")
    vacation.trigger()
    assert any(
        item.resource_id == "lbi" and item.first_day == NEXT_MONDAY
        for item in snapshot_of(controller).absences.values()
    )
    assert controller.summary_text("t-fab").startswith("2026-118 Müller SA · Fabrication")

    assert controller.availability("lbi", None).text == "absent"
    assert controller.availability("lbi", None).available is False
    assert controller.availability("nfa", None).text.endswith("%")
    await close(controller)


@pytest.mark.asyncio
async def test_team_dialog_opens_applies_and_closes(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, _tab, hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    controller.open_team_dialog()
    await wait_for(lambda: controller.team_dialog is not None)
    dialog = controller.team_dialog
    assert dialog is not None
    assert hooks.members_asked == 1
    assert dialog.snapshot.resources == snapshot_of(controller).resources

    dialog._apply(  # noqa: SLF001 - the builder path the dialog's pages use
        lambda ctx: commands.update_resource(ctx, "mre", role="Chef d'atelier"),
        "Modifier Marc Rey",
    )
    assert snapshot_of(controller).resources["mre"].role == "Chef d'atelier"
    dialog.close()
    await wait_for(lambda: controller.team_dialog is None)
    controller.open_team_dialog()
    assert controller.team_dialog is not None
    assert hooks.members_asked == 1
    await close(controller)
    assert controller.team_dialog is None


@pytest.mark.asyncio
async def test_display_choices_are_kept_for_the_poste(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    config = make_config(store)
    controller, tab, hooks = make_controller(qtbot, config)
    await started(controller)
    tab.set_view("month")
    tab.set_density("compact")
    tab.set_color_by("dossier")
    controller.refresh_now()
    # « Vue à l'ouverture » is a setting: switching the view here leaves it.
    assert config.default_view == "week"
    assert config.density == "compact"
    assert config.color_by == "dossier"
    model = tab.grid.model
    assert model is not None
    assert model.view == "month"
    assert model.color_by == "dossier"
    tab.side.width_changed.emit(360)
    assert config.panel_width == 360
    tab.grid.team_toggled.emit("bureau")
    assert config.collapsed_teams == ["bureau"]
    assert hooks.saved
    assert all(saved is config for saved in hooks.saved)

    # Another « Vue à l'ouverture » chosen in the settings shows at once.
    chosen = config.model_copy(update={"default_view": "day"})
    await controller.reconfigure(chosen)
    assert tab.view == "day"
    tab.set_view("week")
    await controller.reconfigure(chosen.model_copy(update={"density": "large"}))
    assert tab.view == "week"  # unchanged setting: the view switched here stays
    assert tab.density == "large"

    # Folded for lack of room by the tab: not this poste's choice.
    saved = len(hooks.saved)
    tab.resize(1000, 760)
    assert tab.side.is_collapsed()
    assert controller.poste_config.panel_open
    assert len(hooks.saved) == saved
    await close(controller)


# ------------------------------------------------------- review fixes ----


class ClosingStore(LocalPlanningStore):
    """A local store that says whether it was closed."""

    def __init__(self, directory: Path) -> None:
        super().__init__(directory)
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


class HangingStore(ClosingStore):
    """Answers the first read, then hangs on every later one (a network that stalls)."""

    def __init__(self, directory: Path) -> None:
        super().__init__(directory)
        self.reads = 0
        self.hanging = asyncio.Event()

    async def changes(self, cursor: str | None) -> StoreChanges:
        self.reads += 1
        if self.reads > 1:
            self.hanging.set()
            await asyncio.Event().wait()
        return await super().changes(cursor)


def offline() -> Exception:
    return StoreUnavailableError("Planning hors ligne : nouvel essai automatique.")


class Factory:
    """Opens local stores: the first ``failures`` openings fail, each after ``delay``."""

    def __init__(
        self,
        *,
        failures: int = 0,
        delay: float = 0.0,
        error: Callable[[], Exception] = offline,
        store: type[ClosingStore] = ClosingStore,
    ) -> None:
        self.calls = 0
        self.failures = failures
        self.delay = delay
        self.error = error
        self.store = store
        self.opened: list[ClosingStore] = []

    async def __call__(self, config: PlanningPosteConfig) -> PlanningStore:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.calls <= self.failures:
            raise self.error()
        store = self.store(config.local_store_directory())
        self.opened.append(store)
        return store


def pump() -> None:
    QApplication.processEvents()


def running(hooks: Hooks) -> list[asyncio.Task[None]]:
    return [item for item in hooks.tasks if not item.done()]


def sortie_cards(controller: PlanningController, number: str) -> list[PlanningTask]:
    return [
        item
        for item in snapshot_of(controller).live_tasks()
        if item.origin == "sortie_dossier" and item.project_number == number
    ]


@pytest.mark.asyncio
async def test_team_dialog_opened_from_a_modal_window_is_usable(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    controller.open_team_dialog()  # reads the Microsoft 365 members once
    await wait_for(lambda: controller.team_dialog is not None)
    first = controller.team_dialog
    assert first is not None
    first.close()
    await wait_for(lambda: controller.team_dialog is None)

    settings = QDialog(tab)  # what Settings is: a modal dialog run with exec()
    blocked: list[str] = []
    seen: dict[str, Any] = {}

    class BlockedSpy(QObject):
        def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
            if event.type() == QEvent.Type.WindowBlocked:
                blocked.append(type(watched).__name__)
            return False

    spy = BlockedSpy()

    def inside_settings() -> None:
        seen["modal"] = QApplication.activeModalWidget()
        controller.open_team_dialog()  # « Équipe du planning… » of the settings
        dialog = controller.team_dialog
        seen["dialog"] = dialog
        if dialog is not None:
            dialog.installEventFilter(spy)
            seen["parent"] = dialog.parentWidget()
            seen["visible"] = dialog.isVisible()
            dialog.hide()
            dialog.show()  # shown again under the modal window: never blocked
        QTimer.singleShot(50, lambda: settings.done(0))

    QTimer.singleShot(0, inside_settings)
    settings.exec()

    dialog = seen["dialog"]
    assert seen["modal"] is settings
    assert dialog is not None
    assert seen["parent"] is settings
    assert seen["visible"]
    assert blocked == []
    # The settings closed: the roster stays open, on the main window.
    pump()
    assert controller.team_dialog is dialog
    assert dialog.parentWidget() is tab.window()
    assert dialog.isVisible()
    dialog.close()
    await close(controller)


@pytest.mark.asyncio
async def test_a_double_retry_opens_one_store(qtbot: Any, tmp_path: Path, data_dirs: Any) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    factory = Factory(failures=1, delay=0.2)
    controller, tab, hooks = make_controller(qtbot, make_config(store), factory=factory)
    await controller.start()
    assert controller.sync_status.state == "offline"
    assert controller.read_only

    tab.retry_requested.emit()  # a double click on « Réessayer »
    tab.retry_requested.emit()
    await wait_for(lambda: factory.calls == 2 and controller.sync_status.state != "offline")
    await asyncio.sleep(0.3)
    assert factory.calls == 2
    assert len(factory.opened) == 1
    assert not controller.read_only

    await controller.aclose()
    assert all(item.closed for item in factory.opened)
    await asyncio.sleep(0)
    assert not running(hooks)


@pytest.mark.asyncio
async def test_reconfigure_while_the_store_still_opens_keeps_one_session(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    factory = Factory(delay=0.3)
    controller, _tab, hooks = make_controller(qtbot, make_config(store), factory=factory)
    starting = asyncio.ensure_future(controller.start())
    await asyncio.sleep(0.05)
    # OK in the settings, another store, while the planning still opens.
    await controller.reconfigure(make_config(tmp_path / "other-store"))
    await starting
    assert factory.calls == 2
    assert [item.closed for item in factory.opened] == [True, False]
    await controller.aclose()
    assert all(item.closed for item in factory.opened)
    await asyncio.sleep(0)
    assert not running(hooks)


@pytest.mark.asyncio
async def test_a_store_that_could_not_be_opened_is_tried_again(
    qtbot: Any, tmp_path: Path, data_dirs: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    monkeypatch.setattr(planning_module, "RETRY_SECONDS", {"offline": 0.05})
    factory = Factory(failures=1)
    controller, _tab, _hooks = make_controller(qtbot, make_config(store), factory=factory)
    await controller.start()
    assert controller.sync_status.state == "offline"
    # The timer tries again on its own: a poste started offline gets back in sync.
    await wait_for(lambda: controller.sync_status.state != "offline", poke=pump)
    assert factory.calls == 2
    assert not controller.read_only
    await close(controller)


@pytest.mark.asyncio
async def test_showing_the_tab_or_a_new_sign_in_tries_the_store_again(
    qtbot: Any, tmp_path: Path, data_dirs: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    monkeypatch.setattr(planning_module, "RETRY_ON_SHOW_SECONDS", 0.0)

    def denied() -> Exception:
        error = StoreUnavailableError("Autorisation manquante pour la liste du planning.")
        error.__cause__ = GraphError("Forbidden", status_code=403)
        return error

    factory = Factory(failures=2, error=denied)
    controller, _tab, _hooks = make_controller(qtbot, make_config(store), factory=factory)
    await controller.start()
    assert controller.sync_status.state == "forbidden"
    controller.set_visible(visible=False)
    controller.set_visible(visible=True)  # the tab is shown: one attempt at once
    await wait_for(lambda: factory.calls == 2)
    await wait_for(lambda: controller.sync_status.state == "forbidden")
    controller.wake()  # what a new Microsoft sign-in asks for
    await wait_for(lambda: factory.calls == 3 and not controller.read_only)
    assert controller.sync_status.state != "forbidden"
    await close(controller)


@pytest.mark.asyncio
async def test_no_sortie_card_while_the_store_cannot_be_opened(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    factory = Factory(failures=1)
    controller, _tab, hooks = make_controller(qtbot, make_config(store), factory=factory)
    await controller.start()
    assert controller.sync_status.state == "offline"
    created = await controller.ensure_backlog_card(
        "2026-131", designation="Barrière parking", client="Zufferey SA", released=TODAY
    )
    assert not created
    assert not sortie_cards(controller, "2026-131")
    refused = "Planning : carte à planifier non créée pour 2026-131 (planning indisponible"
    assert any(line.startswith(refused) for line in hooks.logs)
    await close(controller)


@pytest.mark.asyncio
async def test_a_sortie_card_waits_for_a_planning_still_opening(
    qtbot: Any, tmp_path: Path, data_dirs: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, _tab, _hooks = make_controller(
        qtbot, make_config(store), factory=Factory(delay=0.4)
    )
    starting = asyncio.ensure_future(controller.start())
    await asyncio.sleep(0.05)
    created = await controller.ensure_backlog_card(
        "2026-131", designation="Barrière parking", client="Zufferey SA", released=TODAY
    )
    assert created
    assert len(sortie_cards(controller, "2026-131")) == 1
    await starting
    await close(controller)

    # Not ready in time: no card, and the log says why.
    monkeypatch.setattr(planning_module, "CARD_WAIT_SECONDS", 0.1)
    late, _late_tab, hooks = make_controller(qtbot, make_config(store), factory=Factory(delay=1.0))
    starting = asyncio.ensure_future(late.start())
    await asyncio.sleep(0.05)
    assert not await late.ensure_backlog_card("2026-124", designation="Portail", client="PPE")
    assert hooks.logs[-1] == (
        "Planning : carte à planifier non créée pour 2026-124 (planning en cours de chargement)."
    )
    await starting
    assert not sortie_cards(late, "2026-124")
    await close(late)


@pytest.mark.asyncio
async def test_aclose_keeps_to_its_budget_when_the_network_hangs(
    qtbot: Any, tmp_path: Path, data_dirs: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    monkeypatch.setattr(planning_module, "STOP_BUDGET_SECONDS", 0.5)
    factory = Factory(store=HangingStore)
    config = make_config(store)
    controller, tab, hooks = make_controller(qtbot, config, factory=factory)
    await started(controller)
    hanging = factory.opened[0]
    assert isinstance(hanging, HangingStore)
    controller.set_visible(visible=True)
    controller.wake()
    await asyncio.wait_for(hanging.hanging.wait(), timeout=5)
    tab.grid.move_requested.emit("t-debit", "psi", "psi", at(WEEK, 13), "move")

    loop = asyncio.get_running_loop()
    began = loop.time()
    await controller.aclose()
    took = loop.time() - began
    assert took < 0.5 + planning_module.CANCEL_GRACE_SECONDS + 0.5
    assert hanging.closed
    await asyncio.sleep(0)
    assert not running(hooks)
    # What could not be sent waits in the cache for the next start.
    assert (config.cache_directory() / "planning-cache.json").exists()


@pytest.mark.asyncio
async def test_the_sync_stays_live_on_a_screen_left_inactive(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, _tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    session = controller._session  # noqa: SLF001 - the engine of the session
    assert session is not None
    engine = session.engine
    assert engine is not None
    controller.set_visible(visible=True, window_active=False)  # a workshop screen
    assert engine.mode == "visible"
    controller.set_visible(visible=False)
    assert engine.mode in {"background", "hidden"}
    await close(controller)


@pytest.mark.asyncio
async def test_the_repertoire_is_not_read_while_hidden_nor_a_missing_year_again(
    qtbot: Any, tmp_path: Path, data_dirs: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    monkeypatch.setattr(planning_module, "PROJECTS_STALE_SECONDS", 0.0)
    reads: list[int] = []

    class MissingPastYear(Hooks):
        async def read_repertoire(self, year: int) -> RepertoireSnapshot:
            reads.append(year)
            if year < TODAY.year:
                raise ProjectCreationError(f"Pas de feuille {year} dans le répertoire.")
            return await super().read_repertoire(year)

    controller, _tab, _hooks = make_controller(qtbot, make_config(store), hooks=MissingPastYear())
    await started(controller)
    timer = controller._projects_timer  # noqa: SLF001 - the 10 min refresh
    controller.set_visible(visible=True)
    assert timer.isActive()
    controller.set_visible(visible=False)
    assert not timer.isActive()
    controller.set_visible(visible=True)  # shown again: read again, without the old year
    await wait_for(lambda: reads.count(TODAY.year) >= 2)
    assert reads.count(TODAY.year - 1) == 1
    await close(controller)


@pytest.mark.asyncio
async def test_week_cells_start_on_their_half_day(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    thursday = WEEK + timedelta(days=3)
    # A double-click 1 h into Thursday morning: the whole half day.
    tab.grid.cell_activated.emit("mre", at(thursday, 7, 30))
    assert tab.editor.label_text() == "MRE · jeu 22.10 06:30 → jeu 22.10 12:00 · ½ j"
    tab.editor.close_editor()
    tab.grid.cell_activated.emit("mre", at(thursday, 14))
    assert tab.editor.label_text().startswith("MRE · jeu 22.10 13:00 → jeu 22.10 16:30")
    tab.editor.close_editor()
    # A letter typed on a cell clicked in the morning.
    tab.set_cell_cursor("nfa", at(WEEK + timedelta(days=1), 8))
    tab.cell_typed.emit("r")
    assert tab.editor.label_text().startswith("NFA · mar 20.10 06:30 → mar 20.10 12:00")
    tab.editor.close_editor()
    # The day view keeps the half hour pointed at.
    tab.set_view("day", anchor=thursday)
    controller.refresh_now()
    tab.grid.cell_activated.emit("mre", at(thursday, 7, 30))
    assert tab.editor.label_text().startswith("MRE · jeu 22.10 07:30 → jeu 22.10 08:30")
    tab.editor.close_editor()
    calendar = calendar_resolver(snapshot_of(controller))("mre")
    assert half_day_start(calendar, at(thursday, 12, 30)) == at(thursday, 13)
    assert half_day_start(calendar, at(thursday, 6)) == at(thursday, 6, 30)
    await close(controller)


@pytest.mark.asyncio
async def test_created_titles_start_with_a_capital_like_the_preview(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    before = set(snapshot_of(controller).tasks)
    tab.quick.set_text("118 pose garde-corps KDU lun")
    assert tab.quick.submit()
    assert created_task(controller, before).title == "Pose garde-corps"
    tab.grid.cell_activated.emit("mre", at(WEEK + timedelta(days=4), 6, 30))
    before = set(snapshot_of(controller).tasks)
    tab.editor.bar.set_text("124 réglage PL")
    assert tab.editor.bar.submit()
    assert created_task(controller, before).title == "Réglage PL"
    assert capitalized("") == ""
    assert capitalized("éclairage LED") == "Éclairage LED"
    await close(controller)


@pytest.mark.asyncio
async def test_hover_card_shows_the_release_of_the_dossier(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, _tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    info = controller.hover_info("t-fab")
    assert info is not None
    assert info.atelier_release == ""
    assert await controller.ensure_backlog_card(
        "2026-118", designation="Garde-corps escalier", client="Müller SA", released=TODAY
    )
    info = controller.hover_info("t-fab")
    assert info is not None
    assert info.atelier_release == "22.10"
    fab = snapshot_of(controller).tasks["t-fab"]
    assert dossier_release(snapshot_of(controller), fab) == "2026-10-22"
    assert fab.project_cache.atelier_release == ""  # shown only, never written
    await close(controller)


# ------------------------------------------------------------ absences ----


@pytest.mark.asyncio
async def test_absences_are_shown_edited_and_deleted_from_the_grid(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    info = controller.absence_hover_info("a-vac")
    assert info is not None
    assert (info.number, info.client, info.title) == ("LBI", "Luca Bianchi", "Vacances")
    assert info.when == "lun 19.10 → ven 23.10"
    assert info.author.startswith("Créé par SBE · BUREAU-2")
    menu = controller.absence_menu("a-vac")
    assert menu is not None
    texts = [action.text() for action in menu.actions()]
    assert texts[:2] == ["Modifier l'absence…", "Supprimer l'absence"]

    # Double-click: « Absence » opens; the new dates and reason are saved.
    tab.grid.absence_activated.emit("a-vac")
    dialog = controller.absence_dialog
    assert dialog is not None
    dialog.reason_combo.setCurrentIndex(dialog.reason_combo.findData("formation"))
    dialog.first_edit.setDate(QDate(2026, 10, 20))
    assert dialog.save()
    saved = snapshot_of(controller).absences["a-vac"]
    assert (saved.reason, saved.first_day, saved.last_day) == (
        "formation",
        date(2026, 10, 20),
        date(2026, 10, 23),
    )
    assert controller.absence_dialog is None
    assert any("Absence modifiée : Formation LBI" in text for text in toast_texts(tab))
    assert controller.service is not None
    assert controller.service.undo_label == "Modifier l'absence de LBI"

    # « Supprimer l'absence », then « Annuler » on the toast brings it back.
    menu = controller.absence_menu("a-vac")
    assert menu is not None
    next(action for action in menu.actions() if action.text() == "Supprimer l'absence").trigger()
    assert snapshot_of(controller).absences["a-vac"].deleted
    toast = next(item for item in tab.toasts.toasts() if "Absence supprimée" in item.message())
    assert toast.action_button.text() == "Annuler"
    toast.trigger()
    restored = snapshot_of(controller).absences["a-vac"]
    assert not restored.deleted
    assert restored.reason == "formation"
    await close(controller)


@pytest.mark.asyncio
async def test_a_recurring_day_off_opens_its_person_in_the_team(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    model = tab.grid.model
    assert model is not None
    block = next(item for item in model.absences_by_row["kro"] if item.recurring)
    info = controller.absence_hover_info(block.absence_id)
    assert info is not None
    assert (info.title, info.type_label, info.when) == (
        "École professionnelle",
        "Jour récurrent",
        "Chaque mercredi",
    )
    menu = controller.absence_menu(block.absence_id)
    assert menu is not None
    assert [action.text() for action in menu.actions()][-1] == "Modifier dans Équipe…"
    tab.grid.absence_activated.emit(block.absence_id)
    assert controller.absence_dialog is None
    await wait_for(lambda: controller.team_dialog is not None)
    dialog = controller.team_dialog
    assert dialog is not None
    assert dialog.pages.currentIndex() == 0
    assert dialog.people_page._selected_id == "kro"  # noqa: SLF001
    # The team page lists the absences and follows the edits made in the grid.
    dialog.show_absences("lbi")
    assert [item.id for item in dialog.absences_page.shown_absences()] == ["a-vac"]
    controller.remove_absence("a-vac")
    controller.refresh_now()
    assert dialog.absences_page.shown_absences() == []
    await close(controller)


@pytest.mark.asyncio
async def test_absences_are_read_only_without_the_store(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    config = make_config(store)
    first, _tab, _hooks = make_controller(qtbot, config)
    await started(first)
    await close(first)

    async def denied(_config: PlanningPosteConfig) -> PlanningStore:
        raise StoreUnavailableError("Autorisation manquante pour la liste du planning.")

    controller, tab, _hooks = make_controller(qtbot, config, factory=denied)
    await started(controller)
    assert controller.read_only
    assert controller.absence_hover_info("a-vac") is not None
    tab.grid.absence_activated.emit("a-vac")
    assert controller.absence_dialog is None
    assert any("Autorisation manquante" in text for text in toast_texts(tab))
    menu = controller.absence_menu("a-vac")
    assert menu is not None
    assert not any(action.isEnabled() for action in menu.actions()[:2])
    controller.remove_absence("a-vac")
    assert not snapshot_of(controller).absences["a-vac"].deleted
    await close(controller)


@pytest.mark.asyncio
async def test_quick_entry_creates_night_work_and_the_detail_puts_it_back(
    qtbot: Any, tmp_path: Path, data_dirs: Any
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    before = set(snapshot_of(controller).tasks)
    tab.quick.set_text("118 aéroport KDU lun 22h-5h")
    assert tab.quick.submit()
    new = created_task(controller, before)
    assert new.off_hours
    assert (new.start, new.end) == (at(NEXT_MONDAY, 22), at(NEXT_MONDAY + timedelta(days=1), 5))
    controller.refresh_now()
    bar = tab.grid.model.bar(new.id, "kdu") if tab.grid.model is not None else None
    assert bar is not None
    assert bar.night
    info = controller.hover_info(new.id)
    assert info is not None
    assert info.duration == "7 h"
    assert info.rest.startswith("Repos : dès lun 26.10 14:00 avant")

    tab.detail.field_changed.emit(new.id, "off_hours", False)  # noqa: FBT003
    back = snapshot_of(controller).tasks[new.id]
    assert not back.off_hours
    assert back.start == at(NEXT_MONDAY + timedelta(days=1), 6, 30)
    await close(controller)
