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
from projectflow.ui.controller import ProjectFlowController
from projectflow.ui.main_window import MainWindow


class FakeProjectService:
    def __init__(self) -> None:
        self.created: list[ProjectInput] = []
        self.updated: list[ProjectInput] = []

    async def create_project(self, project: ProjectInput) -> ProjectCreationResult:
        self.created.append(project)
        return ProjectCreationResult(
            project_dir_created=True,
            project_dir="C:/tmp/2026-4995",
            fiche_path="C:/tmp/fiche.xlsx",
        )

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
        self.signed_out = False

    def fiche(self) -> FicheService:
        return self.fiche_service

    def repertoire(self) -> FakeRepertoireService:
        return self.repertoire_service

    def project(self) -> FakeProjectService:
        return self.project_service

    def sign_out(self) -> None:
        self.signed_out = True


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


@pytest.mark.asyncio
async def test_controller_create_project_reads_form(qtbot: Any, tmp_path: Path) -> None:
    window, _config, services = _window(qtbot, tmp_path)
    controller = ProjectFlowController(
        window=window,
        config=_config,
        services=services,  # type: ignore[arg-type]
    )

    await controller.create_project()

    assert str(services.project_service.created[0].number) == "2026-4995"
    assert services.project_service.created[0].designation == "Escalier"
    assert "Projet cree" in window.creation_tab.logs.toPlainText()


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


def test_controller_sign_out_clears_user(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, config, services = _window(qtbot, tmp_path)
    config.user.user_id = "user"
    config.user.email = "lionel@balzmetal.ch"
    saved = False

    def save() -> None:
        nonlocal saved
        saved = True

    def confirm_sign_out(
        parent: object,
        title: str,
        text: str,
    ) -> QMessageBox.StandardButton:
        del parent, title, text
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr("projectflow.ui.controller.QMessageBox.question", confirm_sign_out)
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
        save_config=save,
    )

    controller.sign_out()

    assert services.signed_out is True
    assert config.user.user_id == ""
    assert config.user.email == ""
    assert saved is True
