from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook
from PySide6.QtWidgets import QMessageBox

from projectflow.config import AppConfig
from projectflow.core.fiche_service import FicheService
from projectflow.core.models import ProjectCreationResult, ProjectDeletionResult, ProjectInput
from projectflow.core.numero import parse_project_number
from projectflow.core.repertoire_service import (
    NextAvailableProject,
    RepertoireRow,
    RepertoireSnapshot,
)
from projectflow.ui.controller import ProjectFlowController, _update_prompt_text
from projectflow.ui.creation_tab import CreationFormData
from projectflow.ui.dialogs.quick_create import QuickCreateDialog
from projectflow.ui.main_window import MainWindow
from projectflow.ui.widgets.planner import PlannerTaskFormData


class FakeProjectService:
    def __init__(self) -> None:
        self.created: list[tuple[ProjectInput, bool, bool]] = []
        self.updated: list[ProjectInput] = []
        self.deleted: list[
            tuple[ProjectInput, tuple[ProjectInput, ...], tuple[RepertoireRow, ...]]
        ] = []
        self.creation_result = ProjectCreationResult(
            project_dir_created=True,
            project_dir="C:/tmp/2026-4995",
            fiche_path="C:/tmp/fiche.xlsx",
        )

    async def create_project(
        self,
        project: ProjectInput,
        *,
        force_overwrite: bool = False,
        update_existing_info: bool = True,
    ) -> ProjectCreationResult:
        self.created.append((project, force_overwrite, update_existing_info))
        return self.creation_result

    async def update_project(self, project: ProjectInput) -> ProjectCreationResult:
        self.updated.append(project)
        return ProjectCreationResult(
            project_dir_created=False,
            project_dir="C:/tmp/2026-4995",
            fiche_path="C:/tmp/fiche.xlsx",
        )

    async def delete_project(
        self,
        project: ProjectInput,
        *,
        related_projects: tuple[ProjectInput, ...] | list[ProjectInput],
        repertoire_rows: tuple[RepertoireRow, ...] | list[RepertoireRow],
    ) -> ProjectDeletionResult:
        self.deleted.append((project, tuple(related_projects), tuple(repertoire_rows)))
        return ProjectDeletionResult(
            numbers_released=tuple(row.number for row in repertoire_rows),
            project_path_trashed=True,
            outlook_folders_deleted=1,
            planner_tasks_deleted=len(related_projects),
        )


class FakeRepertoireService:
    def __init__(self) -> None:
        self.updated_rows: list[tuple[int, tuple[Any, ...], tuple[Any, ...]]] = []

    async def next_available(self, *, year: int) -> NextAvailableProject:
        assert year == 2026
        return NextAvailableProject(number=parse_project_number("2026-4995"), row_index=1)

    async def read_snapshot(self, *, year: int) -> RepertoireSnapshot:
        assert year == 2026
        return RepertoireSnapshot(
            year=year,
            rows=(
                RepertoireRow(
                    row_index=0,
                    values=("2026-4994", "", "Balz Metal SA", "Lionel", "Projet"),
                ),
                RepertoireRow(
                    row_index=1,
                    values=("2026-4995", "", "", "", ""),
                ),
            ),
            next_available=NextAvailableProject(
                number=parse_project_number("2026-4995"),
                row_index=1,
            ),
        )

    async def update_editable_row(
        self,
        *,
        year: int,
        row_index: int,
        values: tuple[Any, ...],
        expected_values: tuple[Any, ...],
    ) -> None:
        assert year == 2026
        self.updated_rows.append((row_index, values, expected_values))


class FakeServices:
    def __init__(self, fiche_service: FicheService | None = None) -> None:
        self.project_service = FakeProjectService()
        self.repertoire_service = FakeRepertoireService()
        self.fiche_service = fiche_service or FicheService()

    def fiche(self) -> FicheService:
        return self.fiche_service

    def repertoire(self) -> FakeRepertoireService:
        return self.repertoire_service

    def project(self) -> FakeProjectService:
        return self.project_service


def _window(qtbot: Any, tmp_path: Path) -> tuple[MainWindow, AppConfig, FakeServices]:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    window = MainWindow(config)
    qtbot.addWidget(window)
    services = FakeServices()
    ProjectFlowController(window=window, config=config, services=services)  # type: ignore[arg-type]
    window.creation_tab.set_project_identity(year="2026", project_id="4995")
    window.creation_tab.designation_edit.setText("Escalier")
    return window, config, services


def _completer_values(edit: Any) -> list[str]:
    completer = edit.completer()
    assert completer is not None
    model = completer.model()
    return [str(model.index(row, 0).data()) for row in range(model.rowCount())]


@pytest.fixture(autouse=True)
def project_creation_post_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, list[Any]]:
    calls: dict[str, list[Any]] = {"opened": [], "infos": []}

    def record_opened(path: Path) -> None:
        calls["opened"].append(path)

    def record_information(_parent: object, title: str, text: str) -> None:
        calls["infos"].append((title, text))

    monkeypatch.setattr(
        "projectflow.ui.controller.open_path",
        record_opened,
    )
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.information",
        record_information,
    )
    return calls


@pytest.mark.asyncio
async def test_controller_create_project_reads_form(
    qtbot: Any,
    tmp_path: Path,
    project_creation_post_actions: dict[str, list[Any]],
) -> None:
    window, _config, services = _window(qtbot, tmp_path)
    controller = ProjectFlowController(
        window=window,
        config=_config,
        services=services,  # type: ignore[arg-type]
    )

    await controller.create_project()

    assert str(services.project_service.created[0][0].number) == "2026-4995"
    assert services.project_service.created[0][0].designation == "Escalier"
    assert services.project_service.created[0][1:] == (False, True)
    assert "Projet cree" in window.creation_tab.logs.toPlainText()
    assert project_creation_post_actions["opened"] == [Path("C:/tmp/2026-4995")]
    assert project_creation_post_actions["infos"][0][0] == "Projet cree"


@pytest.mark.asyncio
async def test_controller_create_project_reads_planner_form_options(
    qtbot: Any,
    tmp_path: Path,
    project_creation_post_actions: dict[str, list[Any]],
) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    config.planner.enabled = True
    config.planner.bucket_id = "default-bucket"
    window = MainWindow(config)
    qtbot.addWidget(window)
    services = FakeServices()
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )
    window.creation_tab.set_project_identity(year="2026", project_id="4995")
    window.creation_tab.designation_edit.setText("Escalier")
    window.creation_tab.planner_widget.set_data(
        PlannerTaskFormData(
            enabled=True,
            bucket_id="target-bucket",
            bucket_name="A traiter",
            assignee_ids=("user-a", "user-b"),
            assignee_labels=("A", "B"),
            due_enabled=True,
            due_days=10,
        ),
    )

    await controller.create_project()

    project = services.project_service.created[0][0]
    assert project.planner.enabled is True
    assert project.planner.bucket_id == "target-bucket"
    assert project.planner.assignee_ids == ("user-a", "user-b")
    assert project.planner.due_days == 10
    assert project_creation_post_actions["opened"] == [Path("C:/tmp/2026-4995")]


@pytest.mark.asyncio
async def test_controller_logs_outlook_creation_result(qtbot: Any, tmp_path: Path) -> None:
    window, config, services = _window(qtbot, tmp_path)
    config.outlook.enabled = True
    services.project_service.creation_result = ProjectCreationResult(
        project_dir_created=True,
        project_dir="C:/tmp/2026-4995",
        fiche_path="C:/tmp/fiche.xlsx",
        outlook_folder_created=True,
    )
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )

    await controller.create_project()

    logs = window.creation_tab.logs.toPlainText()
    assert "Dossiers Outlook crees" in logs


@pytest.mark.asyncio
async def test_controller_next_available_prefills_identity(qtbot: Any, tmp_path: Path) -> None:
    window, config, services = _window(qtbot, tmp_path)
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )

    await controller.next_available()

    assert window.creation_tab.project_id_edit.text() == "4995"
    assert window.creation_tab.subproject_edit.text() == ""
    assert _completer_values(window.creation_tab.societe_edit) == ["Balz Metal SA"]


@pytest.mark.asyncio
async def test_controller_prepares_subproject_from_selected_repertoire_row(
    qtbot: Any,
    tmp_path: Path,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )
    snapshot = await services.repertoire_service.read_snapshot(year=2026)
    window.repertoire_tab.set_snapshot(snapshot)
    window.repertoire_tab.table.selectRow(0)

    controller.create_subproject_from_repertoire()

    data = window.creation_tab.data()
    assert data.year == "2026"
    assert data.project_id == "4994"
    assert data.subproject_id == "2"
    assert data.designation == ""
    assert window.tabs.currentWidget() is window.creation_tab


@pytest.mark.asyncio
async def test_controller_duplicates_selected_project_into_next_available_number(
    qtbot: Any,
    tmp_path: Path,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    project_dir = config.paths.racine_projets / "2026" / "2026-4994"
    project_dir.mkdir(parents=True)
    workbook = Workbook()
    worksheet = workbook.active
    worksheet["C3"] = "2026-4994"
    worksheet["D3"] = "Societe : Balz Metal SA"
    worksheet["D4"] = "Contact : Lionel"
    worksheet["D5"] = "Projet : Escalier type"
    workbook.save(project_dir / "2026-4994 - Fiche dossier clients.xlsx")
    workbook.close()
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )
    snapshot = await services.repertoire_service.read_snapshot(year=2026)
    window.repertoire_tab.set_snapshot(snapshot)
    window.repertoire_tab.table.selectRow(0)

    controller.duplicate_project_from_repertoire()

    data = window.creation_tab.data()
    assert data.project_id == "4995"
    assert data.subproject_id == ""
    assert data.societe == "Balz Metal SA"
    assert data.contact == "Lionel"
    assert data.designation == "Escalier type"


@pytest.mark.asyncio
async def test_controller_delete_refreshes_repertoire_and_reports_linked_items(
    qtbot: Any,
    tmp_path: Path,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )
    project = ProjectInput(number=parse_project_number("2026-4994"))
    rows = (
        RepertoireRow(
            row_index=0,
            values=("2026-4994", "14.08.2026", "Balz Metal SA", "Lionel", "Projet"),
        ),
    )

    await controller._delete_project_and_related(  # noqa: SLF001
        project=project,
        related_projects=(project,),
        rows=rows,
    )

    assert services.project_service.deleted == [(project, (project,), rows)]
    assert "numéro(s) libéré(s)" in window.repertoire_tab.status_label.text()


@pytest.mark.asyncio
async def test_controller_repertoire_sync_updates_full_project(
    qtbot: Any,
    tmp_path: Path,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    project_dir.mkdir(parents=True)
    fiche_path = project_dir / "2026-4995 - Fiche dossier clients.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet["C3"] = "2026-4995"
    worksheet["D3"] = "Societe : Ancien client"
    worksheet["D4"] = "Contact : Ancien contact"
    worksheet["D5"] = "Projet : Ancienne designation"
    worksheet["D6"] = "Localisation : Geneve"
    worksheet["C6"] = "LM"
    workbook.save(fiche_path)
    workbook.close()
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )

    values = (
        "2026-4995",
        "15.07.2026",
        "Nouveau client",
        "Nouveau contact",
        "Nouveau projet",
    )
    expected = (
        "2026-4995",
        "14.07.2026",
        "Ancien client",
        "Ancien contact",
        "Ancienne designation",
    )

    await controller._sync_project_from_repertoire(  # noqa: SLF001
        row_index=7,
        values=values,
        expected_values=expected,
    )

    assert services.repertoire_service.updated_rows == [(7, values, expected)]
    assert len(services.project_service.updated) == 1
    project = services.project_service.updated[0]
    assert str(project.number) == "2026-4995"
    assert project.designation == "Nouveau projet"
    assert project.societe == "Nouveau client"
    assert project.contact == "Nouveau contact"
    assert project.localisation == "Geneve"
    assert project.gere_par == "LM"
    assert project.planner.enabled is False


@pytest.mark.asyncio
async def test_controller_quick_next_available_prefills_dialog(
    qtbot: Any,
    tmp_path: Path,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )
    dialog = QuickCreateDialog(parent=window)
    qtbot.addWidget(dialog)
    dialog.set_project_identity(year="2026", project_id="", subproject_id="2")

    await controller._quick_next_available(dialog)  # noqa: SLF001

    assert dialog.project_id_edit.text() == "4995"
    assert dialog.subproject_edit.text() == ""
    assert window.creation_tab.project_id_edit.text() == "4995"


@pytest.mark.asyncio
async def test_controller_quick_create_stays_in_quick_flow(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    project_creation_post_actions: dict[str, list[Any]],
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )
    confirmations: list[tuple[ProjectCreationResult, CreationFormData, ProjectInput]] = []
    data = CreationFormData(
        year="2026",
        project_id="4995",
        subproject_id="",
        designation="Escalier rapide",
        societe="Balz",
        contact="Lionel",
        localisation="Geneve",
        gere_par="LM",
    )

    def record_confirmation(
        result: ProjectCreationResult,
        confirmed_data: CreationFormData,
        project: ProjectInput,
    ) -> None:
        confirmations.append((result, confirmed_data, project))

    monkeypatch.setattr(controller, "_show_quick_creation_confirmation", record_confirmation)

    await controller._create_project_from_quick(data)  # noqa: SLF001

    assert str(services.project_service.created[0][0].number) == "2026-4995"
    assert services.project_service.created[0][0].designation == "Escalier rapide"
    assert confirmations[0][1] == data
    assert str(confirmations[0][2].number) == "2026-4995"
    assert project_creation_post_actions["opened"] == []
    assert project_creation_post_actions["infos"] == []
    assert not window.isVisible()


def test_controller_quick_confirmation_modifier_opens_loaded_main_window(
    qtbot: Any,
    tmp_path: Path,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    config.user.initials = "LM"
    window.apply_config_labels()
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )
    data = CreationFormData(
        year="2026",
        project_id="4995",
        subproject_id="",
        designation="Escalier rapide",
        societe="Balz",
        contact="Lionel",
        localisation="Geneve",
        gere_par="LM",
    )
    project = ProjectInput(number=parse_project_number("2026-4995"))

    controller._handle_quick_creation_action(  # noqa: SLF001
        "edit",
        services.project_service.creation_result,
        data,
        project,
    )

    assert window.isVisible()
    assert window.creation_tab.data() == data


def test_controller_quick_confirmation_next_reopens_reset_quick_form(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )
    calls: list[bool] = []

    def record_quick_create(*, reset: bool = False) -> None:
        calls.append(reset)

    monkeypatch.setattr(controller, "show_quick_create", record_quick_create)

    controller._handle_quick_creation_action(  # noqa: SLF001
        "next",
        services.project_service.creation_result,
        CreationFormData(
            year="2026",
            project_id="4995",
            subproject_id="",
            designation="Escalier rapide",
            societe="Balz",
            contact="Lionel",
            localisation="Geneve",
            gere_par="LM",
        ),
        ProjectInput(number=parse_project_number("2026-4995")),
    )

    qtbot.waitUntil(lambda: calls == [True])


def test_controller_reuses_existing_quick_dialog(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )
    dialog = QuickCreateDialog(parent=window)
    qtbot.addWidget(dialog)
    dialog.show()
    controller._quick_dialog = dialog  # noqa: SLF001
    created: list[bool] = []

    def fail_if_created(*_args: object, **_kwargs: object) -> QuickCreateDialog:
        created.append(True)
        return QuickCreateDialog(parent=window)

    monkeypatch.setattr("projectflow.ui.controller.QuickCreateDialog", fail_if_created)

    controller.show_quick_create()

    assert created == []
    assert controller._quick_dialog is dialog  # noqa: SLF001


def test_controller_load_project_reads_existing_fiche(qtbot: Any, tmp_path: Path) -> None:
    window, config, services = _window(qtbot, tmp_path)
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    project_dir.mkdir(parents=True)
    workbook = Workbook()
    worksheet = workbook.active
    worksheet["C3"] = "2026-4995"
    worksheet["D3"] = "Societe : Balz"
    worksheet["D5"] = "Projet : Escalier charge"
    workbook.save(project_dir / "2026-4995 - Fiche dossier clients.xlsx")
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )

    controller.load_project()

    assert window.creation_tab.societe_edit.text() == "Balz"
    assert window.creation_tab.designation_edit.text() == "Escalier charge"


def test_controller_load_project_reads_fiche_from_numbered_subfolder(
    qtbot: Any,
    tmp_path: Path,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    window.creation_tab.set_project_identity(year="2026", project_id="5093")
    project_dir = config.paths.racine_projets / "2026" / "2026-5093"
    nested_dir = project_dir / "2026-5093"
    nested_dir.mkdir(parents=True)
    workbook = Workbook()
    worksheet = workbook.active
    worksheet["C3"] = "2026-5093"
    worksheet["D3"] = "Societe : Client niche"
    worksheet["D5"] = "Projet : Fiche rangee en sous-dossier"
    workbook.save(nested_dir / "2026-5093 - Fiche dossier clients.xlsx")
    workbook.close()
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )

    controller.load_project()

    assert window.creation_tab.societe_edit.text() == "Client niche"
    assert window.creation_tab.designation_edit.text() == "Fiche rangee en sous-dossier"


def test_controller_load_subproject_reads_fiche_from_subproject_subfolder(
    qtbot: Any,
    tmp_path: Path,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    window.creation_tab.set_project_identity(year="2026", project_id="5093", subproject_id="2")
    project_dir = config.paths.racine_projets / "2026" / "2026-5093"
    nested_dir = project_dir / "2026-5093-2"
    nested_dir.mkdir(parents=True)
    workbook = Workbook()
    worksheet = workbook.active
    worksheet["C3"] = "2026-5093-2"
    worksheet["D3"] = "Societe : Sous client"
    worksheet["D5"] = "Projet : Sous-projet charge"
    workbook.save(nested_dir / "2026-5093-2 - Fiche dossier clients.xlsx")
    workbook.close()
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )

    controller.load_project()

    assert window.creation_tab.societe_edit.text() == "Sous client"
    assert window.creation_tab.designation_edit.text() == "Sous-projet charge"


def test_controller_load_project_does_not_rename_fiche(
    qtbot: Any,
    tmp_path: Path,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    project_dir.mkdir(parents=True)
    source_path = project_dir / "ancienne fiche.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet["C3"] = "2026-4995"
    worksheet["D5"] = "Projet : Charge sans renommer"
    workbook.save(source_path)
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )

    controller.load_project()

    assert window.creation_tab.designation_edit.text() == "Charge sans renommer"
    assert source_path.exists()
    assert not (project_dir / "2026-4995 - Fiche dossier clients.xlsx").exists()


@pytest.mark.asyncio
async def test_controller_recreate_existing_project_without_changes_skips_info_update(
    qtbot: Any,
    tmp_path: Path,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    project_dir.mkdir(parents=True)
    workbook = Workbook()
    worksheet = workbook.active
    worksheet["C3"] = "2026-4995"
    worksheet["D5"] = "Projet : Escalier"
    workbook.save(project_dir / "2026-4995 - Fiche dossier clients.xlsx")
    services.project_service.creation_result = ProjectCreationResult(
        project_dir_created=False,
        project_dir=str(project_dir),
        fiche_path=str(project_dir / "2026-4995 - Fiche dossier clients.xlsx"),
    )
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )

    await controller.create_project()

    assert services.project_service.created[0][1:] == (False, False)
    assert "Informations existantes conservees" in window.creation_tab.logs.toPlainText()


@pytest.mark.asyncio
async def test_controller_recreate_existing_project_confirms_changed_information(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    project_dir.mkdir(parents=True)
    workbook = Workbook()
    worksheet = workbook.active
    worksheet["C3"] = "2026-4995"
    worksheet["D5"] = "Projet : Ancien"
    workbook.save(project_dir / "2026-4995 - Fiche dossier clients.xlsx")
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )

    await controller.create_project()

    assert services.project_service.created[0][1:] == (True, True)


def test_controller_open_fiche_uses_default_app(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    project_dir.mkdir(parents=True)
    workbook = Workbook()
    workbook.save(project_dir / "2026-4995 - Fiche dossier clients.xlsx")
    opened: list[Path] = []
    monkeypatch.setattr(
        "projectflow.ui.controller.open_file_default_app",
        lambda path: opened.append(path) is None or True,
    )
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )

    controller.open_fiche()

    assert opened == [project_dir / "2026-4995 - Fiche dossier clients.xlsx"]


def test_controller_open_folder_uses_project_directory(
    qtbot: Any,
    tmp_path: Path,
    project_creation_post_actions: dict[str, list[Any]],
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    project_dir.mkdir(parents=True)
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )

    controller.open_folder()

    assert project_creation_post_actions["opened"] == [project_dir]
    assert "Dossier projet ouvert" in window.creation_tab.logs.toPlainText()


def test_controller_open_folder_for_subproject_uses_parent_project_directory(
    qtbot: Any,
    tmp_path: Path,
    project_creation_post_actions: dict[str, list[Any]],
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    window.creation_tab.set_project_identity(year="2026", project_id="4995", subproject_id="2")
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    project_dir.mkdir(parents=True)
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )

    controller.open_folder()

    assert project_creation_post_actions["opened"] == [project_dir]


def test_controller_open_repertoire_uses_default_app(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    repertoire_path = tmp_path / "repertoire.xlsx"
    workbook = Workbook()
    workbook.save(repertoire_path)
    config.paths.repertoire_chantier.display_path = str(repertoire_path)
    opened: list[Path] = []
    monkeypatch.setattr(
        "projectflow.ui.controller.open_file_default_app",
        lambda path: opened.append(path) is None or True,
    )
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )

    controller.open_repertoire()

    assert opened == [repertoire_path]
    assert "Repertoire ouvert" in window.creation_tab.logs.toPlainText()


@pytest.mark.asyncio
async def test_controller_open_repertoire_accepts_cloud_link(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    url = "https://balz.sharepoint.com/:x:/s/site/abc"
    config.paths.repertoire_chantier.display_path = url
    document_url = "https://balz.sharepoint.com/sites/site/Documents/repertoire.xlsx"

    async def resolve(_config: object) -> str:
        # A concurrent repertoire read can initialize these without changing
        # the file selected by the user. Opening must still finish.
        config.paths.repertoire_chantier.drive_id = "resolved-drive"
        config.paths.repertoire_chantier.item_id = "resolved-item"
        return document_url

    monkeypatch.setattr("projectflow.ui.controller.resolve_repertoire_open_url", resolve)
    opened: list[str] = []
    monkeypatch.setattr(
        "projectflow.ui.controller.open_excel_uri",
        lambda target: opened.append(target) is None,
    )
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )
    controller.open_repertoire()
    await asyncio.gather(*controller._background_tasks)  # noqa: SLF001
    assert opened == ["ms-excel:ofe|u|" + document_url]


def test_open_repertoire_prefers_separate_sync_file_without_changing_cloud_target(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    cloud = config.paths.repertoire_chantier
    cloud.display_path = "https://balz.sharepoint.com/:x:/s/site/secret"
    cloud.drive_id, cloud.item_id = "original-drive", "original-item"
    local = tmp_path / "OneDrive" / "repertoire.xlsx"
    local.parent.mkdir()
    local.touch()
    cloud.open_path = str(local)
    before = cloud.model_dump()
    opened: list[Path] = []
    monkeypatch.setattr(
        "projectflow.ui.controller.open_file_default_app",
        lambda path: opened.append(path) is None,
    )
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )
    controller.open_repertoire()
    assert opened == [local]
    assert cloud.model_dump() == before
    assert not controller._background_tasks  # noqa: SLF001


@pytest.mark.asyncio
async def test_open_repertoire_ignores_outdated_cloud_resolution(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    config.paths.repertoire_chantier.display_path = "https://1drv.ms/x/old"
    opened: list[object] = []

    async def resolve(_config: object) -> str:
        config.paths.repertoire_chantier.display_path = "https://1drv.ms/x/new"
        return "https://balz.sharepoint.com/Documents/old.xlsx"

    monkeypatch.setattr("projectflow.ui.controller.resolve_repertoire_open_url", resolve)
    monkeypatch.setattr(
        "projectflow.ui.controller.open_excel_uri",
        lambda target: opened.append(target) is None,
    )
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )
    controller.open_repertoire()
    await asyncio.gather(*controller._background_tasks)  # noqa: SLF001
    assert not opened


@pytest.mark.asyncio
async def test_open_repertoire_reports_missing_excel_handler(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    config.paths.repertoire_chantier.display_path = "https://1drv.ms/x/original"

    async def resolve(_config: object) -> str:
        return "https://balz.sharepoint.com/Documents/original.xlsx"

    monkeypatch.setattr("projectflow.ui.controller.resolve_repertoire_open_url", resolve)
    monkeypatch.setattr("projectflow.ui.controller.open_excel_uri", lambda _url: False)
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.critical", lambda *_args: None)
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )
    controller.open_repertoire()
    await asyncio.gather(*controller._background_tasks)  # noqa: SLF001
    assert "Impossible de lancer Excel" in window.creation_tab.logs.toPlainText()


def test_configured_initials_survive_form_reset_and_quick_dialog(
    qtbot: Any,
    tmp_path: Path,
) -> None:
    window, config, _services = _window(qtbot, tmp_path)
    config.user.initials = " ab "
    window.apply_config_labels()
    tab = window.creation_tab
    tab.reset_form_fields()
    assert tab.gere_par_edit.isReadOnly()
    assert tab.data().gere_par == "AB"
    dialog = QuickCreateDialog(parent=window)
    qtbot.addWidget(dialog)
    dialog.set_user_initials(config.user.initials)
    data = replace(tab.data(), gere_par="OTHER")
    dialog.set_data(data)
    assert dialog.data().gere_par == "AB"


def test_controller_open_repertoire_reports_missing_path(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.critical",
        lambda *_args, **_kwargs: None,
    )
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )

    controller.open_repertoire()

    assert "Repertoire chantier non configure" in window.creation_tab.logs.toPlainText()


@pytest.mark.asyncio
async def test_controller_load_sortie_dossier_repertories_project_files(
    qtbot: Any,
    tmp_path: Path,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    (project_dir / "photos").mkdir(parents=True)
    (project_dir / "Plans" / "Plan d'execution").mkdir(parents=True)
    (project_dir / "fiche.xlsx").touch()
    (project_dir / "cote.pdf").touch()
    (project_dir / "photos" / "photo.jpg").touch()
    (project_dir / "Plans" / "Plan d'execution" / "plan.pdf").touch()
    window.sortie_tab.set_project_identity(year="2026", project_id="4995")
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )

    await controller.load_sortie_dossier()

    assert window.sortie_tab.fiche_list.count() == 1
    assert window.sortie_tab.mesure_list.count() == 1
    assert window.sortie_tab.photo_list.count() == 0
    assert window.sortie_tab.plan_list.count() == 0
    assert window.sortie_tab.photo_browse_button.isEnabled()
    assert window.sortie_tab.plan_browse_button.isEnabled()
    assert "Projet charge" in window.sortie_tab.logs.text()


@pytest.mark.asyncio
async def test_controller_create_sortie_dossier_logs_success(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    project_dir.mkdir(parents=True)
    fiche = project_dir / "fiche.xlsx"
    workbook = Workbook()
    workbook.active["E2"] = "fiche d'atelier le"
    workbook.save(fiche)
    workbook.close()
    window.sortie_tab.set_project_identity(year="2026", project_id="4995")
    opened: list[Path] = []
    monkeypatch.setattr("projectflow.ui.controller.open_path", opened.append)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
    )

    await controller.load_sortie_dossier()
    await controller.create_sortie_dossier()

    assert "Dossier de sortie cree" in window.sortie_tab.logs.text()
    output_dir = next((project_dir / "Sorties dossier").iterdir())
    assert opened == [output_dir]
    assert "Dossier de sortie ouvert" in window.sortie_tab.logs.text()


def test_update_prompt_includes_release_notes() -> None:
    message = _update_prompt_text("0.1.8", "- Corrige le repertoire chantier")

    assert "ProjectFlow 0.1.8" in message
    assert "Corrige le repertoire chantier" in message
