from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QMessageBox

from projectflow.config import AppConfig, CadConfig
from projectflow.core.fiche_service import FicheService
from projectflow.core.models import CadFileResult, ProjectCreationResult, ProjectInput
from projectflow.ui.controller import ProjectFlowController
from projectflow.ui.creation_tab import CreationFormData
from projectflow.ui.dialogs.quick_create import QuickCreateDialog
from projectflow.ui.dialogs.settings import SettingsDialog
from projectflow.ui.main_window import MainWindow


class FakeProjectService:
    def __init__(self) -> None:
        self.created: list[tuple[ProjectInput, bool, bool]] = []
        self.updated: list[ProjectInput] = []
        self.creation_result = ProjectCreationResult(
            project_dir_created=True,
            project_dir="C:/tmp/2026-4995",
            fiche_path=None,
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
        return self.creation_result


class FakeServices:
    def __init__(self) -> None:
        self.project_service = FakeProjectService()
        self.fiche_service = FicheService()

    def fiche(self) -> FicheService:
        return self.fiche_service

    def repertoire(self) -> object:
        raise AssertionError("repertoire non attendu")

    def project(self) -> FakeProjectService:
        return self.project_service


def _cad_config(tmp_path: Path) -> CadConfig:
    solidworks = tmp_path / "11-Racine Solidworks"
    autocad = tmp_path / "12-Racine AutoCAD"
    solidworks.mkdir(exist_ok=True)
    autocad.mkdir(exist_ok=True)
    return CadConfig(solidworks_template_dir=solidworks, autocad_template_dir=autocad)


def _window(qtbot: Any, tmp_path: Path, *, cad: CadConfig | None = None) -> MainWindow:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    if cad is not None:
        config.cad = cad
    window = MainWindow(config)
    qtbot.addWidget(window)
    return window


def test_cad_options_are_disabled_with_reason_without_template_folders(
    qtbot: Any,
    tmp_path: Path,
) -> None:
    window = _window(qtbot, tmp_path)
    options = window.creation_tab.cad_options

    assert not options.solidworks_checkbox.isEnabled()
    assert not options.autocad_checkbox.isEnabled()
    assert "non configure" in options.solidworks_checkbox.toolTip()

    window._config.cad = CadConfig(autocad_template_dir=tmp_path / "absent")  # noqa: SLF001
    window.apply_config_labels()

    assert not options.autocad_checkbox.isEnabled()
    assert "introuvable" in options.autocad_checkbox.toolTip()


def test_cad_options_start_unchecked_and_reset_unchecks_them(
    qtbot: Any,
    tmp_path: Path,
) -> None:
    window = _window(qtbot, tmp_path, cad=_cad_config(tmp_path))
    tab = window.creation_tab
    options = tab.cad_options

    assert options.solidworks_checkbox.isEnabled()
    assert options.autocad_checkbox.isEnabled()
    assert not options.solidworks_checkbox.isChecked()
    assert not options.autocad_checkbox.isChecked()
    options.solidworks_checkbox.setChecked(True)
    options.autocad_checkbox.setChecked(True)
    assert (tab.data().add_solidworks, tab.data().add_autocad) == (True, True)

    tab.reset_button.click()

    assert (tab.data().add_solidworks, tab.data().add_autocad) == (False, False)


def test_cad_options_are_never_saved_in_settings(qtbot: Any, tmp_path: Path) -> None:
    window = _window(qtbot, tmp_path, cad=_cad_config(tmp_path))
    window.creation_tab.cad_options.autocad_checkbox.setChecked(True)

    dumped = window._config.model_dump_json()  # noqa: SLF001

    assert "add_autocad" not in dumped
    assert "add_solidworks" not in dumped


def test_quick_dialog_cad_options(qtbot: Any, tmp_path: Path) -> None:
    dialog = QuickCreateDialog()
    qtbot.addWidget(dialog)
    assert not dialog.cad_options.autocad_checkbox.isEnabled()

    dialog.apply_cad_config(_cad_config(tmp_path))
    dialog.cad_options.autocad_checkbox.setChecked(True)

    assert dialog.data().add_autocad is True
    assert dialog.data().add_solidworks is False
    dialog.set_data(CreationFormData("2026", "", "", "", "", "", "", ""))
    assert dialog.data().add_autocad is False


@pytest.fixture
def no_message_boxes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    messages: list[str] = []
    monkeypatch.setattr("projectflow.ui.controller.open_path", lambda _path: None)
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, _title, text: messages.append(text),
    )
    return messages


@pytest.mark.asyncio
async def test_controller_passes_cad_options_and_unchecks_after_creation(
    qtbot: Any,
    tmp_path: Path,
    no_message_boxes: list[str],
) -> None:
    window = _window(qtbot, tmp_path, cad=_cad_config(tmp_path))
    services = FakeServices()
    services.project_service.creation_result = ProjectCreationResult(
        project_dir_created=True,
        project_dir=str(tmp_path / "2026-4995"),
        fiche_path=None,
        cad_files=(
            CadFileResult(path="C:/p/2026-4995-ENS-100.dwg", status="created"),
            CadFileResult(
                path="C:/p/2026-4995-PRT-100.SLDPRT",
                status="skipped",
                detail="deja present",
            ),
            CadFileResult(path="C:/p/2026-4995-ENS-100.SLDASM", status="error", detail="verrou"),
        ),
        cad_warnings=("Document Manager indisponible",),
    )
    controller = ProjectFlowController(
        window=window,
        config=window._config,  # noqa: SLF001
        services=services,  # type: ignore[arg-type]
    )
    tab = window.creation_tab
    tab.set_project_identity(year="2026", project_id="4995")
    tab.cad_options.solidworks_checkbox.setChecked(True)
    tab.cad_options.autocad_checkbox.setChecked(True)

    await controller.create_project()

    project = services.project_service.created[0][0]
    assert (project.add_solidworks, project.add_autocad) == (True, True)
    assert (tab.data().add_solidworks, tab.data().add_autocad) == (False, False)
    logs = tab.logs.toPlainText()
    assert "+ Fichier CAO cree: 2026-4995-ENS-100.dwg" in logs
    assert "-> Fichier CAO ignore: 2026-4995-PRT-100.SLDPRT (deja present)" in logs
    assert "! Fichier CAO en erreur: 2026-4995-ENS-100.SLDASM - verrou" in logs
    assert "! CAO: Document Manager indisponible" in logs
    assert "1 cree(s), 1 ignore(s), 1 en erreur" in no_message_boxes[0]


@pytest.mark.asyncio
async def test_controller_update_passes_cad_options(
    qtbot: Any,
    tmp_path: Path,
) -> None:
    window = _window(qtbot, tmp_path, cad=_cad_config(tmp_path))
    services = FakeServices()
    controller = ProjectFlowController(
        window=window,
        config=window._config,  # noqa: SLF001
        services=services,  # type: ignore[arg-type]
    )
    tab = window.creation_tab
    tab.set_project_identity(year="2026", project_id="4995", subproject_id="2")
    tab.cad_options.autocad_checkbox.setChecked(True)

    await controller.update_project()

    updated = services.project_service.updated[0]
    assert str(updated.number) == "2026-4995-2"
    assert updated.add_autocad is True
    assert updated.add_solidworks is False
    assert tab.data().add_autocad is False


def test_quick_confirmation_edit_does_not_recheck_cad_options(
    qtbot: Any,
    tmp_path: Path,
) -> None:
    window = _window(qtbot, tmp_path, cad=_cad_config(tmp_path))
    controller = ProjectFlowController(
        window=window,
        config=window._config,  # noqa: SLF001
        services=FakeServices(),  # type: ignore[arg-type]
    )
    data = CreationFormData(
        "2026", "4995", "", "Escalier", "", "", "", "", add_solidworks=True, add_autocad=True
    )
    result = ProjectCreationResult(project_dir_created=True, project_dir="p", fiche_path=None)

    controller._handle_quick_creation_action(  # noqa: SLF001
        "edit",
        result,
        data,
        controller._project_from_data(data),  # noqa: SLF001
    )

    assert window.creation_tab.data().designation == "Escalier"
    assert window.creation_tab.data().add_solidworks is False
    assert window.creation_tab.data().add_autocad is False


def test_settings_dialog_applies_cad_settings_and_stores_key(
    qtbot: Any,
    tmp_path: Path,
    memory_license_keyring: Any,
) -> None:
    config = AppConfig()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    solidworks = (
        tmp_path / "OneDrive - Balz Metal Sa" / "00-Bibliothèque CAO" / "11-Racine Solidworks"
    )
    dialog.cad_solidworks_edit.setText(str(solidworks))
    dialog.cad_autocad_edit.setText(str(tmp_path / "12-Racine AutoCAD"))
    dialog.cad_subfolder_edit.setText("03-CAO")
    dialog.cad_property_edits["client"].setText("Maitre d'ouvrage")
    dialog.solidworks_license_edit.setText("Balz:swdocmgr_general-123")

    dialog.accept()
    dialog.apply_to_config(config)

    assert config.cad.solidworks_template_dir == solidworks
    assert config.cad.autocad_template_dir == tmp_path / "12-Racine AutoCAD"
    assert config.cad.destination_subfolder == "03-CAO"
    assert config.cad.properties.client == "Maitre d'ouvrage"
    assert config.cad.properties.revision == "Révision"
    assert "swdocmgr" not in config.model_dump_json()
    assert list(memory_license_keyring.values.values()) == ["Balz:swdocmgr_general-123"]

    reopened = SettingsDialog(config)
    qtbot.addWidget(reopened)
    assert reopened.solidworks_license_edit.text() == ""
    assert "enregistree" in reopened.solidworks_license_edit.placeholderText()
    reopened.solidworks_license_clear_button.click()
    reopened.accept()
    assert memory_license_keyring.values == {}


def test_settings_dialog_refuses_cad_subfolder_outside_project(
    qtbot: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    dialog = SettingsDialog(AppConfig())
    qtbot.addWidget(dialog)
    dialog.cad_subfolder_edit.setText("../autre projet")

    dialog.accept()

    assert dialog.result() != dialog.DialogCode.Accepted
    assert "relatif" in warnings[0]


def test_settings_dialog_refuses_cad_template_inside_reference_folder(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    dialog = SettingsDialog(AppConfig())
    qtbot.addWidget(dialog)
    dialog.reference_edit.setText(str(tmp_path / "10-Racine"))
    dialog.cad_solidworks_edit.setText(str(tmp_path / "10-Racine" / "SolidWorks"))

    dialog.accept()

    assert dialog.result() != dialog.DialogCode.Accepted
    assert "distincts du dossier de reference" in warnings[0]
