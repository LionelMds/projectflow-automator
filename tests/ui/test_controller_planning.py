from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook
from PySide6.QtWidgets import QMessageBox

from projectflow.config import AppConfig
from projectflow.core.fiche_service import FicheService, standard_fiche_path
from projectflow.core.numero import parse_project_number
from projectflow.core.planning.local_store import LocalPlanningStore
from projectflow.core.planning.poste_config import PlanningPosteConfig
from projectflow.core.planning.store import PlanningStore, StoreUnavailableError
from projectflow.core.repertoire_service import (
    NextAvailableProject,
    RepertoireRow,
    RepertoireSnapshot,
)
from projectflow.services import ServiceContainer
from projectflow.ui.controller import ProjectFlowController, planning_reset_notice
from projectflow.ui.dialogs.settings import SettingsDialog
from projectflow.ui.main_window import MainWindow

NUMBER = "2026-4996"


class FakePlanning:
    """Stands for PlanningController: records what the main controller asks of it."""

    def __init__(
        self,
        tab: Any,
        *,
        poste_config: PlanningPosteConfig,
        store_factory: Callable[[PlanningPosteConfig], Any],
        hooks: Any,
        today: Callable[[], date] = date.today,
        clock: Callable[[], str] = str,
    ) -> None:
        self.tab = tab
        self._poste_config = poste_config
        self.store_factory = store_factory
        self.hooks = hooks
        self.started = 0
        self.closed = 0
        self.visibility: list[tuple[bool, bool]] = []
        self.reconfigured: list[PlanningPosteConfig] = []
        self.cards: list[dict[str, Any]] = []
        self.card_result = True
        self.card_error: Exception | None = None
        self.team_opened = 0
        self.woken = 0
        self.events: list[str] = []
        self.store: PlanningStore | None = None

    async def start(self) -> None:
        self.started += 1
        self.store = await self.store_factory(self._poste_config)

    async def aclose(self) -> None:
        self.closed += 1
        self.events.append("planning.aclose")
        await asyncio.sleep(0)

    def set_visible(self, *, visible: bool, window_active: bool = True) -> None:
        self.visibility.append((visible, window_active))

    async def reconfigure(self, poste_config: PlanningPosteConfig) -> None:
        self.reconfigured.append(poste_config)
        self._poste_config = poste_config

    async def ensure_backlog_card(
        self,
        number: str,
        *,
        designation: str,
        client: str,
        contact: str = "",
        managed_by: str = "",
        released: date | None = None,
    ) -> bool:
        self.cards.append(
            {
                "number": number,
                "designation": designation,
                "client": client,
                "contact": contact,
                "managed_by": managed_by,
                "released": released,
            },
        )
        if self.card_error is not None:
            raise self.card_error
        return self.card_result

    def open_team_dialog(self) -> None:
        self.team_opened += 1

    def wake(self) -> None:
        self.woken += 1

    @property
    def poste_config(self) -> PlanningPosteConfig:
        return self._poste_config

    @property
    def service(self) -> None:
        return None

    @property
    def sync_status(self) -> None:
        return None


class FakeRepertoire:
    def __init__(self) -> None:
        self.reads: list[int] = []

    async def read_snapshot(self, *, year: int) -> RepertoireSnapshot:
        self.reads.append(year)
        return RepertoireSnapshot(
            year=year,
            rows=(
                RepertoireRow(
                    row_index=0,
                    values=(f"{year}-4995", "", "", "", ""),
                ),
                RepertoireRow(
                    row_index=1,
                    values=(f"{year}-4996", "", "Rossier SA", "M. Rossier", "Escalier ext."),
                ),
            ),
            next_available=NextAvailableProject(
                number=parse_project_number(f"{year}-4995"),
                row_index=0,
            ),
        )


class FakeServices:
    def __init__(self) -> None:
        self.repertoire_service = FakeRepertoire()
        self.fiche_service = FicheService()

    def fiche(self) -> FicheService:
        return self.fiche_service

    def repertoire(self) -> FakeRepertoire:
        return self.repertoire_service

    def project(self) -> Any:
        raise AssertionError("no project operation expected")


@dataclass
class Setup:
    controller: ProjectFlowController
    window: MainWindow
    config: AppConfig
    planning: FakePlanning
    poste: PlanningPosteConfig
    saved: list[PlanningPosteConfig] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@pytest.fixture
def setup(qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Setup:
    monkeypatch.setattr("projectflow.ui.controller.PlanningController", FakePlanning)
    errors: list[str] = []
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.critical",
        lambda _parent, _title, message, *_args: errors.append(message),
    )
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    window = MainWindow(config)
    qtbot.addWidget(window)
    window.repertoire_tab.set_year(2026)
    poste = PlanningPosteConfig(
        user_initials="LM",
        poste_name="BUREAU-1",
        local_directory=str(tmp_path / "planning-store"),
    )
    saved: list[PlanningPosteConfig] = []

    async def store_factory(poste_config: PlanningPosteConfig) -> PlanningStore:
        return LocalPlanningStore(poste_config.local_store_directory())

    controller = ProjectFlowController(
        window=window,
        config=config,
        services=FakeServices(),  # type: ignore[arg-type]
        planning_config=poste,
        planning_store_factory=store_factory,
        save_planning_config=saved.append,
    )
    planning = controller.planning
    assert isinstance(planning, FakePlanning)
    return Setup(controller, window, config, planning, poste, saved, errors)


async def _drain(controller: ProjectFlowController) -> None:
    """Let the scheduled tasks (planning and others) run to their end."""
    for _ in range(50):
        pending = [
            task
            for task in (
                *controller._planning_tasks,  # noqa: SLF001
                *controller._background_tasks,  # noqa: SLF001
            )
            if not task.done()
        ]
        if not pending:
            return
        await asyncio.wait(pending, timeout=0.1)


def _write_fiche(project_dir: Path, number: str, *, designation: str = "Garde-corps") -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    fiche_path = standard_fiche_path(project_dir, parse_project_number(number))
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet["C3"] = number
    worksheet["D3"] = "Societe : Müller SA"
    worksheet["D4"] = "Contact : M. Müller"
    worksheet["D5"] = f"Projet : {designation}"
    worksheet["D6"] = "Localisation : Carouge"
    worksheet["C6"] = "CF"
    worksheet["E2"] = "fiche d'atelier le"
    workbook.save(fiche_path)
    workbook.close()
    return fiche_path


def test_planning_stays_off_without_a_store_factory(qtbot: Any) -> None:
    window = MainWindow(AppConfig())
    qtbot.addWidget(window)

    controller = ProjectFlowController(
        window=window,
        config=AppConfig(),
        services=FakeServices(),  # type: ignore[arg-type]
    )
    controller.start_planning()

    assert controller.planning is None
    assert window.tabs.count() == 4


@pytest.mark.asyncio
async def test_planning_starts_once_and_follows_the_visible_tab(setup: Setup) -> None:
    controller, window, planning = setup.controller, setup.window, setup.planning
    assert planning.started == 0  # Nothing before the window is shown.

    controller.start_planning()
    controller.start_planning()
    await _drain(controller)

    assert planning.started == 1
    assert isinstance(planning.store, LocalPlanningStore)
    assert planning.visibility[-1][0] is False

    window.show()
    window.tabs.setCurrentWidget(window.planning_tab)
    assert planning.visibility[-1][0] is True

    window.tabs.setCurrentWidget(window.creation_tab)
    assert planning.visibility[-1][0] is False

    window.tabs.setCurrentWidget(window.planning_tab)
    window.hide()
    assert planning.visibility[-1] == (False, False)


@pytest.mark.asyncio
async def test_resolved_sharepoint_ids_are_saved_once(setup: Setup) -> None:
    async def resolving_factory(poste_config: PlanningPosteConfig) -> PlanningStore:
        poste_config.sharepoint_site_id = "site-1"
        poste_config.sharepoint_list_id = "list-1"
        return LocalPlanningStore(poste_config.local_store_directory())

    setup.controller._planning_store_factory = resolving_factory  # noqa: SLF001

    await setup.planning.store_factory(setup.poste)
    await setup.planning.store_factory(setup.poste)

    assert setup.saved == [setup.poste]
    assert setup.poste.sharepoint_list_id == "list-1"


@pytest.mark.asyncio
async def test_hooks_reach_the_application(setup: Setup) -> None:
    hooks, window = setup.planning.hooks, setup.window

    snapshot = await hooks.read_repertoire(2026)
    hooks.set_nav_badge(4)
    hooks.log("+ Planning prêt")
    answer = await hooks.ask(lambda: 42)

    assert snapshot.rows[1].number == NUMBER
    assert window.nav_badge_count(window.planning_index) == 4
    assert "+ Planning prêt" in window.creation_tab.logs.toPlainText()
    assert answer == 42


@pytest.mark.asyncio
async def test_a_failing_planning_task_is_logged_never_shown(setup: Setup) -> None:
    async def failing() -> None:
        raise RuntimeError("liste illisible")

    setup.planning.hooks.schedule(failing())
    await _drain(setup.controller)

    assert setup.errors == []
    assert "! Planning : liste illisible" in setup.window.creation_tab.logs.toPlainText()


def test_open_project_dir_hook_opens_the_folder_of_a_number(
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[Path] = []
    monkeypatch.setattr("projectflow.ui.controller.open_path", opened.append)
    project_dir = setup.config.paths.racine_projets / "2026" / NUMBER  # type: ignore[operator]
    project_dir.mkdir(parents=True)

    setup.planning.hooks.open_project_dir(NUMBER)
    setup.planning.hooks.open_project_dir("2026-4997")

    assert opened == [project_dir]
    assert len(setup.errors) == 1
    assert "introuvable" in setup.errors[0]


def test_open_fiche_hook_opens_the_standard_fiche(
    qtbot: Any,
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = setup.config.paths.racine_projets / "2026" / NUMBER  # type: ignore[operator]
    fiche_path = _write_fiche(project_dir, NUMBER)
    opened: list[Path] = []
    monkeypatch.setattr(
        "projectflow.ui.controller.open_file_default_app",
        lambda path: opened.append(path) or True,
    )

    setup.planning.hooks.open_fiche(NUMBER)

    # From the Qt loop: a fiche choice dialog would never block a planning task.
    assert opened == []
    qtbot.waitUntil(lambda: opened == [fiche_path])


@pytest.mark.asyncio
async def test_show_in_repertoire_hook_selects_the_dossier_row(setup: Setup) -> None:
    window = setup.window

    setup.planning.hooks.show_in_repertoire(NUMBER)
    await _drain(setup.controller)

    assert window.tabs.currentWidget() is window.repertoire_tab
    payload = window.repertoire_tab.selected_row_payload()
    assert payload is not None
    assert payload[1][0] == NUMBER
    services = setup.controller._services  # noqa: SLF001
    assert services.repertoire().reads == [2026]  # type: ignore[attr-defined]


def test_load_in_creation_hook_fills_the_creation_form(qtbot: Any, setup: Setup) -> None:
    project_dir = setup.config.paths.racine_projets / "2026" / NUMBER  # type: ignore[operator]
    _write_fiche(project_dir, NUMBER, designation="Garde-corps escalier")

    setup.window.tabs.setCurrentWidget(setup.window.planning_tab)

    setup.planning.hooks.load_in_creation(NUMBER)

    tab = setup.window.creation_tab
    qtbot.waitUntil(lambda: tab.designation_edit.text() == "Garde-corps escalier")
    assert setup.window.tabs.currentWidget() is tab
    assert tab.project_id_edit.text() == "4996"
    assert tab.societe_edit.text() == "Müller SA"


@pytest.mark.asyncio
async def test_prepare_sortie_hook_fills_and_loads_the_sortie_tab(
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads: list[tuple[str, str]] = []

    async def load() -> None:
        loads.append(setup.window.sortie_tab.project_identity())

    monkeypatch.setattr(setup.controller, "load_sortie_dossier", load)

    setup.planning.hooks.prepare_sortie(NUMBER)
    await _drain(setup.controller)

    assert setup.window.tabs.currentWidget() is setup.window.sortie_tab
    assert loads == [("2026", NUMBER)]


def test_open_settings_hook_shows_the_planning_section(
    qtbot: Any,
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, bool]] = []

    class InspectedSettings(SettingsDialog):
        def exec(self) -> int:
            page = self.pages.currentWidget()
            seen.append(
                (
                    self.planning_initials_edit.text(),
                    page.isAncestorOf(self.planning_initials_edit),
                ),
            )
            return self.DialogCode.Rejected

    monkeypatch.setattr("projectflow.ui.controller.SettingsDialog", InspectedSettings)

    setup.planning.hooks.open_settings("planning")

    qtbot.waitUntil(lambda: bool(seen))
    assert seen == [("LM", True)]
    assert setup.planning.reconfigured == []


@pytest.mark.asyncio
async def test_settings_ok_reconfigures_the_planning_then_saves_it(
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AcceptedSettings(SettingsDialog):
        def exec(self) -> int:
            self.planning_initials_edit.setText("zz")
            self.planning_poste_edit.setText("ATELIER-2")
            return self.DialogCode.Accepted

    monkeypatch.setattr("projectflow.ui.controller.SettingsDialog", AcceptedSettings)

    setup.controller.open_settings()
    await _drain(setup.controller)

    assert len(setup.planning.reconfigured) == 1
    applied = setup.planning.reconfigured[0]
    assert applied is not setup.poste  # A copy: the planning compares old and new.
    assert (applied.user_initials, applied.poste_name) == ("ZZ", "ATELIER-2")
    assert applied.poste_id == setup.poste.poste_id
    assert setup.saved == [applied]


def test_settings_team_button_opens_the_team_dialog(
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TeamThenCancel(SettingsDialog):
        def exec(self) -> int:
            self.planning_team_requested.emit()
            return self.DialogCode.Rejected

    monkeypatch.setattr("projectflow.ui.controller.SettingsDialog", TeamThenCancel)

    setup.controller.open_settings()

    assert setup.planning.team_opened == 1


@pytest.mark.asyncio
async def test_planner_members_reach_the_settings_and_are_read_once(
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup.config.planner.enabled = True
    setup.config.planner.plan_id = "plan-1"
    fetched: list[str] = []

    async def fetch(plan_id: str) -> tuple[tuple[str, str, str], ...]:
        fetched.append(plan_id)
        return (("user-1", "Marc Rey", "marc.rey@example.com"),)

    monkeypatch.setattr("projectflow.ui.controller._fetch_planner_members", fetch)
    opened: list[SettingsDialog] = []

    class ModelessSettings(SettingsDialog):
        def exec(self) -> int:
            opened.append(self)
            self.show()
            return self.DialogCode.Rejected

    monkeypatch.setattr("projectflow.ui.controller.SettingsDialog", ModelessSettings)

    setup.controller.open_settings()
    await _drain(setup.controller)
    members = await setup.planning.hooks.planner_members()

    assert opened[0].planning_member_combo.count() == 1
    assert opened[0].planning_member_combo.itemData(0) == "user-1"
    assert members == (("user-1", "Marc Rey", "marc.rey@example.com"),)
    assert fetched == ["plan-1"]
    opened[0].close()


@pytest.mark.asyncio
async def test_planner_members_are_empty_without_planner(setup: Setup) -> None:
    setup.config.planner.enabled = False

    assert await setup.planning.hooks.planner_members() == ()


async def _create_sortie(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    project_dir = setup.config.paths.racine_projets / "2026" / NUMBER  # type: ignore[operator]
    _write_fiche(project_dir, NUMBER, designation="Garde-corps balcons")
    setup.window.sortie_tab.set_project_identity(year="2026", project_id="4996")
    monkeypatch.setattr("projectflow.ui.controller.open_path", lambda _path: None)
    scheduled_before_question: list[bool] = []

    def question(*_args: object, **_kwargs: object) -> QMessageBox.StandardButton:
        # The card is on its way before the modal question waits for an answer.
        scheduled_before_question.append(bool(setup.controller._planning_tasks))  # noqa: SLF001
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.question", question)
    await setup.controller.load_sortie_dossier()
    await setup.controller.create_sortie_dossier()
    await _drain(setup.controller)
    return scheduled_before_question


@pytest.mark.asyncio
async def test_sortie_dossier_adds_a_backlog_card(
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notices: list[tuple[str, str]] = []
    setup.controller.set_notifier(lambda title, message: notices.append((title, message)))

    scheduled_before_question = await _create_sortie(setup, monkeypatch)

    assert scheduled_before_question == [True]
    assert setup.planning.cards == [
        {
            "number": NUMBER,
            "designation": "Garde-corps balcons",
            "client": "Müller SA",
            "contact": "M. Müller",
            "managed_by": "CF",
            "released": date.today(),
        },
    ]
    logs = setup.window.sortie_tab.logs.text()
    assert f"+ Planning : {NUMBER} ajouté à planifier" in logs
    assert "Dossier de sortie ouvert" in logs
    assert notices == [("Planning", f"{NUMBER} ajouté à planifier")]


@pytest.mark.asyncio
async def test_existing_card_is_not_announced(
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup.planning.card_result = False

    await _create_sortie(setup, monkeypatch)

    assert len(setup.planning.cards) == 1
    assert "ajouté à planifier" not in setup.window.sortie_tab.logs.text()


@pytest.mark.asyncio
async def test_a_planning_failure_never_breaks_the_sortie(
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup.planning.card_error = RuntimeError("planning injoignable")

    await _create_sortie(setup, monkeypatch)

    assert setup.errors == []
    assert "Dossier de sortie ouvert" in setup.window.sortie_tab.logs.text()
    assert "! Planning : planning injoignable" in setup.window.creation_tab.logs.toPlainText()
    project_dir = setup.config.paths.racine_projets / "2026" / NUMBER  # type: ignore[operator]
    assert any((project_dir / "Sorties dossier").iterdir())


@pytest.mark.asyncio
async def test_sortie_card_details_fall_back_on_the_repertoire(setup: Setup) -> None:
    details = await setup.controller._sortie_card_details(  # noqa: SLF001
        parse_project_number(NUMBER),
        Path("missing-fiche.xlsx"),
    )

    assert (details.societe, details.contact, details.designation) == (
        "Rossier SA",
        "M. Rossier",
        "Escalier ext.",
    )


@dataclass(slots=False)
class RecordingContainer(ServiceContainer):
    events: list[str] = field(default_factory=list)

    async def close(self) -> None:
        self.events.append("services.close")
        await super().close()


@pytest.mark.asyncio
async def test_aclose_lets_the_planning_flush_before_services_close(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("projectflow.ui.controller.PlanningController", FakePlanning)
    config = AppConfig()
    window = MainWindow(config)
    qtbot.addWidget(window)
    services = RecordingContainer(config)

    async def store_factory(poste_config: PlanningPosteConfig) -> PlanningStore:
        return LocalPlanningStore(poste_config.local_store_directory())

    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,
        planning_config=PlanningPosteConfig(local_directory=str(tmp_path / "store")),
        planning_store_factory=store_factory,
    )
    planning = controller.planning
    assert isinstance(planning, FakePlanning)
    planning.events = services.events
    engine_running = asyncio.Event()

    async def engine() -> None:
        engine_running.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            services.events.append("engine.cancelled")
            raise

    async def other() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            services.events.append("other.cancelled")
            raise

    planning.hooks.schedule(engine())
    controller._schedule_task(other())  # noqa: SLF001
    await engine_running.wait()

    await controller.aclose()
    await controller.aclose()

    assert services.events[:4] == [
        "other.cancelled",
        "planning.aclose",
        "engine.cancelled",
        "services.close",
    ]
    assert planning.closed == 2  # Idempotent on the planning side.
    planning.hooks.schedule(engine())  # Refused once closing: closed, never run.
    assert not controller._planning_tasks  # noqa: SLF001


@pytest.mark.asyncio
async def test_real_planning_receives_the_sortie_card_and_flushes_on_close(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "projectflow.core.planning.poste_config.planning_cache_directory",
        lambda: tmp_path / "cache",
    )
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    window = MainWindow(config)
    qtbot.addWidget(window)
    poste = PlanningPosteConfig(
        user_initials="LM",
        poste_name="BUREAU-1",
        local_directory=str(tmp_path / "store"),
    )
    saved: list[PlanningPosteConfig] = []

    async def store_factory(poste_config: PlanningPosteConfig) -> PlanningStore:
        return LocalPlanningStore(poste_config.local_store_directory())

    controller = ProjectFlowController(
        window=window,
        config=config,
        services=FakeServices(),  # type: ignore[arg-type]
        planning_config=poste,
        planning_store_factory=store_factory,
        save_planning_config=saved.append,
    )
    planning = controller.planning
    assert planning is not None
    project_dir = tmp_path / "clients" / "2026" / NUMBER
    fiche_path = _write_fiche(project_dir, NUMBER, designation="Garde-corps balcons")

    controller.start_planning()
    for _ in range(100):
        if planning.service is not None and not planning.read_only:
            break
        await asyncio.sleep(0.05)
    assert planning.service is not None
    await controller._add_sortie_card(parse_project_number(NUMBER), fiche_path)  # noqa: SLF001
    await controller._add_sortie_card(parse_project_number(NUMBER), fiche_path)  # noqa: SLF001

    cards = [t for t in planning.service.snapshot.backlog() if t.project_number == NUMBER]
    assert len(cards) == 1
    assert cards[0].origin == "sortie_dossier"
    assert cards[0].title.startswith("Fabrication")
    assert cards[0].project_cache.client == "Müller SA"
    assert "ajouté à planifier" in window.sortie_tab.logs.text()

    await controller.aclose()

    stored = await LocalPlanningStore(tmp_path / "store").changes(None)
    assert any(
        d.kind == "task" and d.data is not None and d.data.get("project_number") == NUMBER
        for d in stored.documents
    )
    assert (tmp_path / "cache").exists()


LOST = "sera definitivement perdu"
KEPT = "Le planning enregistre sur ce poste (taches, personnes, absences) est conserve."


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stand for what « Tout réinitialiser » empties, with the default local store inside."""
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr("projectflow.core.planning.poste_config.data_dir", lambda: root)
    return root


def _default_store_with_tasks(data_root: Path) -> Path:
    store = PlanningPosteConfig().local_store_directory()
    assert store.is_relative_to(data_root)  # Kept by the reset although inside it.
    store.mkdir(parents=True)
    (store / "tasks.json").write_text("{}", encoding="utf-8")
    return store


def test_reset_says_the_former_local_planning_is_kept(data_root: Path) -> None:
    config = PlanningPosteConfig(user_initials="LM")
    assert planning_reset_notice(config) == (
        "- les reglages du planning de ce poste et sa copie locale ;\n",
        " Le planning partage sur OneDrive n'est pas touche.",
    )

    _default_store_with_tasks(data_root)
    erased, kept = planning_reset_notice(config)

    assert LOST not in erased
    assert kept.endswith(f" {KEPT}")


@pytest.mark.parametrize("former_local_planning", [False, True])
def test_reset_of_a_sharepoint_poste_leaves_the_shared_planning(
    data_root: Path,
    former_local_planning: bool,  # noqa: FBT001
) -> None:
    if former_local_planning:
        _default_store_with_tasks(data_root)

    erased, kept = planning_reset_notice(PlanningPosteConfig(backend="sharepoint"))

    assert LOST not in erased
    assert "copie locale" in erased
    assert "SharePoint n'est pas touche" in kept
    assert (KEPT in kept) is former_local_planning


def test_reset_without_planning_says_a_planning_on_disk_is_kept(data_root: Path) -> None:
    assert KEPT not in planning_reset_notice(None)[1]

    _default_store_with_tasks(data_root)

    assert LOST not in planning_reset_notice(None)[0]
    assert KEPT in planning_reset_notice(None)[1]


def test_reset_confirmation_says_the_local_planning_is_kept(
    setup: Setup,
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup.planning._poste_config = PlanningPosteConfig(user_initials="LM")  # noqa: SLF001
    _default_store_with_tasks(data_root)
    shown: list[str] = []

    def warning(_parent: Any, _title: str, text: str, *_args: Any) -> QMessageBox.StandardButton:
        shown.append(text)
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr("projectflow.ui.controller.QMessageBox.warning", warning)

    setup.controller.request_reset()

    assert len(shown) == 1
    assert LOST not in shown[0]
    assert KEPT in shown[0]
    assert "la connexion Microsoft" in shown[0]
    assert not setup.controller.reset_requested


# ------------------------------------------------------ copy to OneDrive ----


def _copy_setup(setup: Setup, tmp_path: Path) -> list[PlanningPosteConfig]:
    """Use a second local folder as « OneDrive »; return the configs opened."""
    opened: list[PlanningPosteConfig] = []

    async def factory(poste_config: PlanningPosteConfig) -> PlanningStore:
        opened.append(poste_config)
        if poste_config.backend == "onedrive":
            return LocalPlanningStore(tmp_path / "onedrive" / poste_config.onedrive_folder)
        return LocalPlanningStore(poste_config.local_store_directory())

    setup.controller._planning_store_factory = factory  # noqa: SLF001
    return opened


class VisibleDialog:
    """Stands for the settings dialog: records the copy's outcome."""

    def __init__(self) -> None:
        self.finished: list[tuple[str, bool]] = []

    def isVisible(self) -> bool:  # noqa: N802 - Qt's name
        return True

    def planning_copy_finished(self, message: str, *, copied: bool) -> None:
        self.finished.append((message, copied))


@pytest.mark.asyncio
async def test_copy_to_onedrive_asks_then_copies_and_wakes_the_onedrive_planning(
    setup: Setup,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local = LocalPlanningStore(setup.poste.local_store_directory())
    await local.create("task", "a", {"id": "a", "title": "Pose"})
    await local.create("task", "b", {"id": "b", "title": "Soudure"})
    target = LocalPlanningStore(tmp_path / "onedrive" / "Planning")
    await target.create("task", "b", {"id": "b", "title": "Version OneDrive"})
    opened = _copy_setup(setup, tmp_path)
    questions: list[str] = []

    def question(_parent: Any, _title: str, text: str, *_args: Any) -> Any:
        questions.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.question", question)
    setup.poste.backend = "onedrive"
    setup.poste.onedrive_folder = "Planning"
    dialog = VisibleDialog()

    await setup.controller._copy_planning_to_onedrive(dialog, "Planning")  # type: ignore[arg-type]  # noqa: SLF001

    assert [config.backend for config in opened] == ["onedrive"]
    assert all(config is not setup.poste for config in opened)
    assert len(questions) == 1
    assert "1 élément(s)" in questions[0]
    assert "Planning" in questions[0]
    changes = await target.changes(None)
    titles = {doc.doc_id: doc.data["title"] for doc in changes.documents if doc.data}
    assert titles == {"a": "Pose", "b": "Version OneDrive"}
    assert dialog.finished == [
        ("Copie terminée : 1 élément(s) copiés, 1 déjà présent(s), 0 en échec.", True),
    ]
    assert setup.planning.woken == 1
    assert setup.saved == []  # The poste's settings are never changed by the copy.


@pytest.mark.asyncio
async def test_declined_copy_writes_nothing(
    setup: Setup,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local = LocalPlanningStore(setup.poste.local_store_directory())
    await local.create("task", "a", {"id": "a", "title": "Pose"})
    _copy_setup(setup, tmp_path)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        lambda *_args: QMessageBox.StandardButton.No,
    )
    dialog = VisibleDialog()

    await setup.controller._copy_planning_to_onedrive(dialog, "Planning")  # type: ignore[arg-type]  # noqa: SLF001

    target = LocalPlanningStore(tmp_path / "onedrive" / "Planning")
    assert (await target.changes(None)).documents == ()
    assert dialog.finished == [("Copie annulée.", False)]
    assert setup.planning.woken == 0


@pytest.mark.asyncio
async def test_failed_copy_is_reported_in_the_dialog(setup: Setup) -> None:
    async def unavailable(poste_config: PlanningPosteConfig) -> PlanningStore:
        if poste_config.backend == "onedrive":
            raise StoreUnavailableError("Planning OneDrive injoignable pour le moment.")
        return LocalPlanningStore(poste_config.local_store_directory())

    setup.controller._planning_store_factory = unavailable  # noqa: SLF001
    dialog = VisibleDialog()

    await setup.controller._copy_planning_to_onedrive(dialog, "Planning")  # type: ignore[arg-type]  # noqa: SLF001

    assert dialog.finished == [
        ("Copie impossible : Planning OneDrive injoignable pour le moment.", False),
    ]


def test_settings_copy_button_reaches_the_controller(
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    class CopyThenCancel(SettingsDialog):
        def exec(self) -> int:
            self.planning_copy_requested.emit("Planning atelier")
            return self.DialogCode.Rejected

    async def copy(_dialog: Any, folder: str) -> None:
        requested.append(folder)

    monkeypatch.setattr("projectflow.ui.controller.SettingsDialog", CopyThenCancel)
    monkeypatch.setattr(setup.controller, "_copy_planning_to_onedrive", copy)
    scheduled: list[Any] = []
    monkeypatch.setattr(setup.controller, "_schedule_planning", scheduled.append)

    setup.controller.open_settings()

    assert len(scheduled) == 1
    with pytest.raises(StopIteration):
        scheduled[0].send(None)
    assert requested == ["Planning atelier"]


def test_reset_of_a_onedrive_poste_leaves_the_shared_planning(data_root: Path) -> None:
    _erased, kept = planning_reset_notice(PlanningPosteConfig(backend="onedrive"))

    assert "Le planning partage sur OneDrive n'est pas touche." in kept
