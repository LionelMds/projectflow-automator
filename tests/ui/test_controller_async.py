from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from projectflow.config import AppConfig
from projectflow.core.fiche_service import FicheService
from projectflow.core.models import ProjectCreationResult, ProjectInput
from projectflow.core.numero import parse_project_number
from projectflow.core.repertoire_service import (
    NextAvailableProject,
    RepertoireRow,
    RepertoireSnapshot,
)
from projectflow.graph.planner import PlannerBucket, PlannerMember
from projectflow.platform.paths import native_path_text
from projectflow.services import ServiceContainer
from projectflow.ui.controller import ProjectFlowController
from projectflow.ui.dialogs.quick_create import QuickCreateDialog
from projectflow.ui.dialogs.settings import SettingsDialog
from projectflow.ui.main_window import MainWindow
from projectflow.ui.widgets.planner import (
    PlannerBucketOption,
    PlannerMemberOption,
    PlannerTaskFormData,
)


class ControlledRepertoire:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self.entered: asyncio.Queue[int] = asyncio.Queue()
        self.releases: list[asyncio.Event] = []

    async def read_snapshot(self, *, year: int) -> RepertoireSnapshot:
        index = len(self.calls)
        self.calls.append(year)
        release = asyncio.Event()
        self.releases.append(release)
        self.entered.put_nowait(index)
        await asyncio.wait_for(release.wait(), timeout=3)
        return RepertoireSnapshot(
            year=year,
            rows=(
                RepertoireRow(
                    row_index=0,
                    values=(f"{year}-4994", "", f"Societe {index}", "Contact", "Projet"),
                ),
                RepertoireRow(row_index=1, values=(f"{year}-4995", "", "", "", "")),
            ),
            next_available=NextAvailableProject(
                number=parse_project_number(f"{year}-4995"),
                row_index=1,
            ),
        )


class ControlledProject:
    def __init__(self) -> None:
        self.calls: list[ProjectInput] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.completed = False
        self.cancelled = False

    async def create_project(
        self,
        project: ProjectInput,
        *,
        force_overwrite: bool = False,
        update_existing_info: bool = True,
    ) -> ProjectCreationResult:
        self.calls.append(project)
        self.entered.set()
        try:
            await asyncio.wait_for(self.release.wait(), timeout=3)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.completed = True
        return ProjectCreationResult(
            project_dir_created=True,
            project_dir="synthetic-project",
            fiche_path=None,
        )


class AsyncTestServices:
    def __init__(self) -> None:
        self.repertoire_service = ControlledRepertoire()
        self.project_service = ControlledProject()

    def fiche(self) -> FicheService:
        return FicheService()

    def repertoire(self) -> ControlledRepertoire:
        return self.repertoire_service

    def project(self) -> ControlledProject:
        return self.project_service


def _controller(
    qtbot: Any,
) -> tuple[ProjectFlowController, MainWindow, AppConfig, AsyncTestServices]:
    config = AppConfig()
    config.planner.enabled = True
    config.planner.plan_id = "saved-plan"
    window = MainWindow(config)
    qtbot.addWidget(window)
    window.creation_tab.set_project_identity(year="2026", project_id="4994")
    window.repertoire_tab.set_year(2026)
    services = AsyncTestServices()
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )
    return controller, window, config, services


def _company_suggestions(window: MainWindow) -> list[str]:
    completer = window.creation_tab.societe_edit.completer()
    assert completer is not None
    model = completer.model()
    return [str(model.index(row, 0).data()) for row in range(model.rowCount())]


@pytest.mark.asyncio
async def test_simultaneous_repertoire_consumers_share_only_the_inflight_read(qtbot) -> None:
    controller, window, _config, services = _controller(qtbot)
    backend = services.repertoire_service
    consumers = [
        asyncio.create_task(controller.next_available()),
        asyncio.create_task(controller.load_repertoire()),
        asyncio.create_task(controller._load_client_suggestions(2026)),  # noqa: SLF001
    ]
    assert await asyncio.wait_for(backend.entered.get(), timeout=2) == 0
    await asyncio.sleep(0)
    assert backend.calls == [2026]
    backend.releases[0].set()
    await asyncio.wait_for(asyncio.gather(*consumers), timeout=2)

    assert window.creation_tab.data().project_id == "4995"
    assert window.repertoire_tab.next_available() is not None
    assert _company_suggestions(window) == ["Societe 0"]

    refresh = asyncio.create_task(controller.next_available())
    assert await asyncio.wait_for(backend.entered.get(), timeout=2) == 1
    assert backend.calls == [2026, 2026]
    backend.releases[1].set()
    await asyncio.wait_for(refresh, timeout=2)
    assert _company_suggestions(window) == ["Societe 1"]


@pytest.mark.asyncio
async def test_invalidated_read_cannot_replace_newer_client_suggestions(qtbot) -> None:
    controller, window, _config, services = _controller(qtbot)
    backend = services.repertoire_service
    old_read = asyncio.create_task(controller._load_client_suggestions(2026))  # noqa: SLF001
    assert await asyncio.wait_for(backend.entered.get(), timeout=2) == 0

    controller._invalidate_repertoire_data()  # noqa: SLF001
    current_read = asyncio.create_task(controller._load_client_suggestions(2026))  # noqa: SLF001
    assert await asyncio.wait_for(backend.entered.get(), timeout=2) == 1
    backend.releases[1].set()
    await asyncio.wait_for(current_read, timeout=2)
    assert _company_suggestions(window) == ["Societe 1"]

    backend.releases[0].set()
    await asyncio.wait_for(asyncio.gather(old_read, return_exceptions=True), timeout=2)

    assert backend.calls == [2026, 2026]
    assert _company_suggestions(window) == ["Societe 1"]
    assert controller._client_directories[2026].companies == ("Societe 1",)  # noqa: SLF001


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [False, True])
async def test_main_and_quick_planner_share_request_client_and_cache_empty_results(
    qtbot,
    monkeypatch,
    *,
    empty: bool,
) -> None:
    controller, window, _config, _services = _controller(qtbot)
    quick = QuickCreateDialog(parent=window)
    qtbot.addWidget(quick)
    quick.apply_planner_config(enabled=True, bucket_id="", bucket_name="", due_days=7)
    entered: asyncio.Queue[str] = asyncio.Queue()
    release = asyncio.Event()
    requests: list[tuple[str, str]] = []
    clients: list[object] = []
    closed: list[object] = []

    class PlannerClient:
        def __init__(self) -> None:
            clients.append(self)

        async def list_buckets(self, *, plan_id: str) -> list[PlannerBucket]:
            requests.append(("buckets", plan_id))
            entered.put_nowait("buckets")
            await asyncio.wait_for(release.wait(), timeout=3)
            return [] if empty else [PlannerBucket(id="bucket", name="En cours", plan_id=plan_id)]

        async def list_members(self, *, plan_id: str) -> list[PlannerMember]:
            requests.append(("members", plan_id))
            entered.put_nowait("members")
            await asyncio.wait_for(release.wait(), timeout=3)
            return [] if empty else [PlannerMember(id="member", display_name="Alice", email="")]

        async def aclose(self) -> None:
            closed.append(self)

    monkeypatch.setattr("projectflow.ui.controller._planner_client", PlannerClient)
    targets = [window.creation_tab.planner_widget, quick.planner_widget]
    consumers = [
        asyncio.create_task(controller._load_planner_options(target))  # noqa: SLF001
        for target in targets
    ]
    first = await asyncio.wait_for(entered.get(), timeout=2)
    second = await asyncio.wait_for(entered.get(), timeout=2)
    assert {first, second} == {"buckets", "members"}
    assert len(clients) == 1
    assert len(requests) == 2
    release.set()
    await asyncio.wait_for(asyncio.gather(*consumers), timeout=2)

    assert closed == clients
    for target in targets:
        assert target.bucket_combo.count() == (0 if empty else 1)
        assert set(target._members) == (set() if empty else {"member"})  # noqa: SLF001
        await controller._load_planner_options(target)  # noqa: SLF001
    assert len(clients) == 1
    assert sorted(requests) == [("buckets", "saved-plan"), ("members", "saved-plan")]
    assert closed == clients


@pytest.mark.asyncio
async def test_delayed_planner_options_for_old_plan_are_ignored(qtbot, monkeypatch) -> None:
    controller, window, config, _services = _controller(qtbot)
    entered = asyncio.Event()
    release = asyncio.Event()
    closed: list[bool] = []

    class PlannerClient:
        async def list_buckets(self, *, plan_id: str) -> list[PlannerBucket]:
            entered.set()
            await asyncio.wait_for(release.wait(), timeout=3)
            return [PlannerBucket(id="old-bucket", name="Ancien", plan_id=plan_id)]

        async def list_members(self, *, plan_id: str) -> list[PlannerMember]:
            await asyncio.wait_for(release.wait(), timeout=3)
            return [PlannerMember(id="old-member", display_name="Ancien", email="")]

        async def aclose(self) -> None:
            closed.append(True)

    monkeypatch.setattr("projectflow.ui.controller._planner_client", PlannerClient)
    target = window.creation_tab.planner_widget
    pending = asyncio.create_task(controller._load_planner_options(target))  # noqa: SLF001
    await asyncio.wait_for(entered.wait(), timeout=2)
    config.planner.plan_id = "new-plan"
    release.set()
    await asyncio.wait_for(pending, timeout=2)

    assert target.bucket_combo.findData("old-bucket") == -1
    assert "old-member" not in target._members  # noqa: SLF001
    assert controller._planner_cache_plan != "saved-plan"  # noqa: SLF001
    assert closed == [True]


@pytest.mark.asyncio
async def test_double_creation_runs_one_mutation_and_restores_action_buttons(
    qtbot,
    monkeypatch,
    tmp_path: Path,
) -> None:
    controller, window, config, services = _controller(qtbot)
    config.paths.racine_projets = tmp_path / "clients"
    project = services.project_service
    confirmations: list[str] = []
    monkeypatch.setattr("projectflow.ui.controller.open_path", lambda _path: None)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.information",
        lambda _parent, title, _text: confirmations.append(title),
    )

    first = asyncio.create_task(controller.create_project())
    await asyncio.wait_for(project.entered.wait(), timeout=2)
    buttons = (
        window.creation_tab.create_button,
        window.creation_tab.update_button,
        window.creation_tab.reset_button,
        window.creation_tab.load_button,
        window.creation_tab.open_button,
    )
    assert all(not button.isEnabled() for button in buttons)
    assert window.creation_tab.create_button.text() == "Operation en cours..."
    fiche_calls: list[bool] = []

    def forbidden_fiche_access() -> FicheService:
        fiche_calls.append(True)
        pytest.fail("Le service fiche ne doit pas etre appele pendant une creation.")

    monkeypatch.setattr(services, "fiche", forbidden_fiche_access)
    controller.load_project()
    controller.open_fiche()
    assert not fiche_calls
    await controller.create_project()
    assert len(project.calls) == 1
    project.release.set()
    await asyncio.wait_for(first, timeout=2)

    assert project.completed
    assert not project.cancelled
    assert all(button.isEnabled() for button in buttons)
    assert window.creation_tab.create_button.text() == "Creer"
    assert confirmations == ["Projet cree"]


@pytest.mark.asyncio
async def test_controller_close_waits_for_active_mutation_without_confirmation(
    qtbot,
    monkeypatch,
    tmp_path: Path,
) -> None:
    controller, _window, config, services = _controller(qtbot)
    config.paths.racine_projets = tmp_path / "clients"
    project = services.project_service
    confirmations: list[str] = []
    opened: list[Path] = []
    monkeypatch.setattr("projectflow.ui.controller.open_path", opened.append)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.information",
        lambda _parent, title, _text: confirmations.append(title),
    )
    controller._schedule_task(controller.create_project())  # noqa: SLF001
    await asyncio.wait_for(project.entered.wait(), timeout=2)
    mutation = next(iter(controller._background_tasks))  # noqa: SLF001

    closing = asyncio.create_task(controller.aclose())
    await asyncio.sleep(0)
    assert not closing.done()
    assert not project.completed
    assert not project.cancelled
    project.release.set()
    await asyncio.wait_for(asyncio.gather(closing, mutation), timeout=2)

    assert project.completed
    assert not project.cancelled
    assert not confirmations
    assert not opened
    assert not controller._background_tasks  # noqa: SLF001


@pytest.mark.asyncio
async def test_scheduled_unexpected_failure_is_reported_and_task_is_removed(
    qtbot, monkeypatch
) -> None:
    controller, _window, _config, _services = _controller(qtbot)
    errors: list[str] = []
    monkeypatch.setattr(controller, "_error", errors.append)

    async def fail() -> None:
        await asyncio.sleep(0)
        raise RuntimeError("Unexpected synthetic failure")

    controller._schedule_task(fail())  # noqa: SLF001
    task = next(iter(controller._background_tasks))  # noqa: SLF001
    await asyncio.gather(task, return_exceptions=True)

    assert len(errors) == 1
    assert "Unexpected synthetic failure" in errors[0]
    assert not controller._background_tasks  # noqa: SLF001


@pytest.mark.asyncio
async def test_closed_quick_dialog_does_not_apply_delayed_number_to_main_form(qtbot) -> None:
    controller, window, _config, services = _controller(qtbot)
    quick = QuickCreateDialog(parent=window)
    qtbot.addWidget(quick)
    quick.set_data(window.creation_tab.data())
    controller._quick_dialog = quick  # noqa: SLF001
    backend = services.repertoire_service
    pending = asyncio.create_task(controller._quick_next_available(quick))  # noqa: SLF001
    assert await asyncio.wait_for(backend.entered.get(), timeout=2) == 0

    quick.reject()
    controller._quick_dialog = None  # noqa: SLF001
    backend.releases[0].set()
    await asyncio.wait_for(pending, timeout=2)

    assert window.creation_tab.data().project_id == "4994"
    assert quick.data().project_id == "4994"
    assert _company_suggestions(window) == []


@pytest.mark.parametrize("change", ["profile", "plan", "defaults"])
def test_settings_preserve_unrelated_state_and_apply_changed_planner_to_both_forms(
    qtbot,
    monkeypatch,
    change: str,
) -> None:
    config = AppConfig()
    config.planner.enabled = True
    config.planner.plan_id = "saved-plan"
    config.planner.plan_name = "Plan configure"
    config.planner.bucket_id = "default-bucket"
    config.planner.bucket_name = "Colonne configuree"
    window = MainWindow(config)
    qtbot.addWidget(window)
    window.creation_tab.set_project_identity(year="2026", project_id="4994")
    backend = ControlledRepertoire()
    services = ServiceContainer(config, repertoire_service=backend)  # type: ignore[arg-type]
    planner_service = object()
    services.planner_service = planner_service  # type: ignore[assignment]
    controller = ProjectFlowController(window=window, config=config, services=services)
    quick = QuickCreateDialog(parent=window)
    qtbot.addWidget(quick)
    quick.apply_planner_config(
        enabled=True,
        bucket_id=config.planner.bucket_id,
        bucket_name=config.planner.bucket_name,
        due_days=config.planner.due_days,
    )
    quick.set_data(window.creation_tab.data())
    controller._quick_dialog = quick  # noqa: SLF001
    buckets = [PlannerBucketOption(id="chosen-bucket", name="Choix formulaire")]
    members = [PlannerMemberOption(id="chosen-member", label="Alice")]
    chosen = PlannerTaskFormData(
        enabled=True,
        bucket_id="chosen-bucket",
        bucket_name="Choix formulaire",
        assignee_ids=("chosen-member",),
        assignee_labels=("Alice",),
        due_enabled=True,
        due_days=19,
    )
    targets = [window.creation_tab.planner_widget, quick.planner_widget]
    for target in targets:
        target.set_options(buckets=buckets, members=members)
        target.set_data(chosen)
    controller._planner_cache_plan = "saved-plan"  # noqa: SLF001
    controller._planner_bucket_options = buckets  # noqa: SLF001
    controller._planner_member_options = members  # noqa: SLF001
    controller._remember_client_directory(  # noqa: SLF001
        2026,
        [RepertoireRow(row_index=0, values=("2026-4994", "", "Societe gardee", "Alice", ""))],
    )
    directory = controller._client_directories[2026]  # noqa: SLF001

    class AcceptedSettings(SettingsDialog):
        def exec(self) -> int:
            self.user_initials_edit.setText(" cd ")
            self.repertoire_open_path_edit.setText("C:/Synthetic/Repertoire.xlsx")
            if change == "plan":
                self.planner_plan_combo.addItem("Nouveau plan", "new-plan")
                self.planner_plan_combo.setCurrentIndex(1)
            if change != "profile":
                self.planner_bucket_combo.clear()
                self.planner_bucket_combo.addItem("Nouveau defaut", "new-default")
                self.planner_bucket_combo.setCurrentIndex(0)
                self.planner_due_days_spin.setValue(9)
            return self.DialogCode.Accepted

    monkeypatch.setattr("projectflow.ui.controller.SettingsDialog", AcceptedSettings)

    controller.open_settings()

    assert config.user.initials == "CD"
    assert config.paths.repertoire_chantier.open_path == native_path_text(
        "C:/Synthetic/Repertoire.xlsx",
    )
    assert window.creation_tab.gere_par_edit.text() == "CD"
    assert quick.gere_par_edit.text() == "CD"
    assert services.repertoire_service is backend
    assert controller._client_directories[2026] is directory  # noqa: SLF001
    assert _company_suggestions(window) == ["Societe gardee"]
    for target in targets:
        if change == "profile":
            assert target.data() == chosen
        else:
            assert target.data().bucket_id == "new-default"
            assert target.data().due_days == 9
            assert target.data().assignee_ids == ()
    if change == "plan":
        assert services.planner_service is None
        assert controller._planner_generation == 1  # noqa: SLF001
        assert controller._planner_cache_plan == ""  # noqa: SLF001
        assert not controller._planner_bucket_options  # noqa: SLF001
        assert not controller._planner_member_options  # noqa: SLF001
    else:
        assert services.planner_service is planner_service
        assert controller._planner_generation == 0  # noqa: SLF001
        assert controller._planner_cache_plan == "saved-plan"  # noqa: SLF001
        assert controller._planner_bucket_options == buckets  # noqa: SLF001
        assert controller._planner_member_options == members  # noqa: SLF001
