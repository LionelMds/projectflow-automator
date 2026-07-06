from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook
from PySide6.QtWidgets import QMessageBox

from projectflow.config import AppConfig
from projectflow.core.fiche_service import FicheService
from projectflow.core.models import ProjectCreationResult, ProjectInput
from projectflow.core.numero import parse_project_number
from projectflow.core.repertoire_service import NextAvailableProject
from projectflow.ui.controller import ProjectFlowController, _update_prompt_text
from projectflow.ui.creation_tab import CreationFormData
from projectflow.ui.dialogs.quick_create import QuickCreateDialog
from projectflow.ui.main_window import MainWindow
from projectflow.ui.widgets.planner import PlannerTaskFormData


class FakeProjectService:
    def __init__(self) -> None:
        self.created: list[tuple[ProjectInput, bool, bool]] = []
        self.updated: list[ProjectInput] = []
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


class FakeRepertoireService:
    async def next_available(self, *, year: int) -> NextAvailableProject:
        assert year == 2026
        return NextAvailableProject(number=parse_project_number("2026-4995"), row_index=1)


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


def test_update_prompt_includes_release_notes() -> None:
    message = _update_prompt_text("0.1.8", "- Corrige le repertoire chantier")

    assert "ProjectFlow 0.1.8" in message
    assert "Corrige le repertoire chantier" in message
