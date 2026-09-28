from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook
from PySide6.QtWidgets import QDialog

from projectflow.config import AppConfig
from projectflow.core.fiche_service import FicheService
from projectflow.core.models import ProjectCreationResult, ProjectInput
from projectflow.exceptions import PrintError
from projectflow.ui.controller import ProjectFlowController
from projectflow.ui.dialogs.print_fiche import PrintFicheDialog, PrintFicheOptions
from projectflow.ui.main_window import MainWindow

FICHE_NAME = "2026-4995 - Fiche dossier clients.xlsx"


class FakeProjectService:
    def __init__(self, fiche_path: Path) -> None:
        self.result = ProjectCreationResult(
            project_dir_created=True,
            project_dir=str(fiche_path.parent),
            fiche_path=str(fiche_path),
        )
        self.created: list[ProjectInput] = []
        self.updated: list[ProjectInput] = []

    async def create_project(
        self,
        project: ProjectInput,
        *,
        force_overwrite: bool = False,
        update_existing_info: bool = True,
    ) -> ProjectCreationResult:
        self.created.append(project)
        return self.result

    async def update_project(self, project: ProjectInput) -> ProjectCreationResult:
        self.updated.append(project)
        return self.result


class FakeServices:
    def __init__(self, fiche_path: Path) -> None:
        self.project_service = FakeProjectService(fiche_path)
        self.fiche_service = FicheService()

    def fiche(self) -> FicheService:
        return self.fiche_service

    def repertoire(self) -> object:
        raise AssertionError("Le repertoire n'est pas utilise par l'impression.")

    def project(self) -> FakeProjectService:
        return self.project_service


class PrintRecorder:
    def __init__(self) -> None:
        self.printed: list[tuple[Path, str, int]] = []
        self.error: Exception | None = None

    def __call__(self, path: Path, *, printer_name: str, copies: int = 1) -> None:
        if self.error is not None:
            raise self.error
        self.printed.append((path, printer_name, copies))


@pytest.fixture
def printer(monkeypatch: pytest.MonkeyPatch) -> PrintRecorder:
    recorder = PrintRecorder()
    monkeypatch.setattr("projectflow.ui.controller.print_workbook_a4", recorder)
    monkeypatch.setattr("projectflow.ui.controller.open_path", lambda _path: None)
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", lambda *_args: None)
    return recorder


@pytest.fixture
def errors(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    messages: list[str] = []
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.critical",
        lambda _parent, _title, text: messages.append(text),
    )
    return messages


def _save_fiche(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    workbook.save(path)
    workbook.close()
    return path


def _setup(
    qtbot: Any,
    tmp_path: Path,
    *,
    print_after_save: bool = False,
) -> tuple[ProjectFlowController, MainWindow, AppConfig, FakeServices, list[bool]]:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    config.printing.print_fiche_on_save = print_after_save
    window = MainWindow(config)
    qtbot.addWidget(window)
    # The fake service reports a fiche outside the projects root: creating a
    # project whose folder already exists would ask a blocking question.
    services = FakeServices(_save_fiche(tmp_path / "resultat" / FICHE_NAME))
    saves: list[bool] = []
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,  # type: ignore[arg-type]
        save_config=lambda: saves.append(True),
    )
    window.creation_tab.set_project_identity(year="2026", project_id="4995")
    window.creation_tab.designation_edit.setText("Escalier")
    return controller, window, config, services, saves


def _choose_printer(
    monkeypatch: pytest.MonkeyPatch,
    controller: ProjectFlowController,
    options: PrintFicheOptions | None,
) -> list[Path]:
    asked: list[Path] = []

    def ask(fiche_path: Path) -> PrintFicheOptions | None:
        asked.append(fiche_path)
        return options

    monkeypatch.setattr(controller, "_ask_print_options", ask)
    return asked


@pytest.mark.asyncio
async def test_create_prints_saved_fiche_when_option_is_checked(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    printer: PrintRecorder,
) -> None:
    controller, window, _config, services, _saves = _setup(
        qtbot,
        tmp_path,
        print_after_save=True,
    )
    asked = _choose_printer(monkeypatch, controller, PrintFicheOptions("Xerox Bac2", copies=2))

    await controller.create_project()

    fiche_path = Path(services.project_service.result.fiche_path or "")
    assert window.creation_tab.print_after_save_checkbox.isChecked()
    assert asked == [fiche_path]
    assert printer.printed == [(fiche_path, "Xerox Bac2", 2)]
    assert "Fiche envoyée à l'imprimante Xerox Bac2" in window.creation_tab.logs.toPlainText()


@pytest.mark.asyncio
async def test_create_and_update_do_not_print_when_option_is_unchecked(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    printer: PrintRecorder,
) -> None:
    controller, _window, _config, services, _saves = _setup(qtbot, tmp_path)
    asked = _choose_printer(monkeypatch, controller, PrintFicheOptions("Xerox Bac2"))

    await controller.create_project()
    await controller.update_project()

    assert len(services.project_service.created) == 1
    assert len(services.project_service.updated) == 1
    assert asked == []
    assert printer.printed == []


@pytest.mark.asyncio
async def test_update_prints_saved_fiche_when_option_is_checked(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    printer: PrintRecorder,
) -> None:
    controller, window, _config, _services, _saves = _setup(qtbot, tmp_path)
    window.creation_tab.print_after_save_checkbox.setChecked(True)
    _choose_printer(monkeypatch, controller, PrintFicheOptions("Xerox Bac1"))

    await controller.update_project()

    assert [printed[1] for printed in printer.printed] == ["Xerox Bac1"]


def test_print_option_is_remembered(qtbot: Any, tmp_path: Path) -> None:
    _controller, window, config, _services, saves = _setup(qtbot, tmp_path)

    window.creation_tab.print_after_save_checkbox.setChecked(True)

    assert config.printing.print_fiche_on_save is True
    assert saves == [True]


@pytest.mark.asyncio
async def test_print_menu_prints_form_fiche_and_remembers_printer(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    printer: PrintRecorder,
) -> None:
    controller, window, config, _services, saves = _setup(qtbot, tmp_path)
    fiche_path = _save_fiche(config.paths.racine_projets / "2026" / "2026-4995" / FICHE_NAME)
    config.printing.printer_name = "Xerox Bac2"
    shown: list[tuple[str, str]] = []

    def accept(dialog: PrintFicheDialog) -> int:
        shown.append((dialog.printer_combo.currentText(), dialog.windowTitle()))
        dialog.printer_combo.setCurrentText("Xerox Bac3")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(
        "projectflow.ui.controller.installed_printers",
        lambda: (["Xerox Bac1", "Xerox Bac2", "Xerox Bac3"], "Xerox Bac1"),
    )
    monkeypatch.setattr(PrintFicheDialog, "exec", accept)

    window.creation_tab.print_fiche_action.trigger()
    await asyncio.gather(*controller._background_tasks)  # noqa: SLF001

    assert shown == [("Xerox Bac2", "Imprimer la fiche")]
    assert printer.printed == [(fiche_path, "Xerox Bac3", 1)]
    assert config.printing.printer_name == "Xerox Bac3"
    assert saves


@pytest.mark.asyncio
async def test_print_cancelled_or_failed_is_reported(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    printer: PrintRecorder,
    errors: list[str],
) -> None:
    controller, window, _config, _services, _saves = _setup(qtbot, tmp_path)
    fiche_path = tmp_path / "resultat" / FICHE_NAME
    _choose_printer(monkeypatch, controller, None)

    await controller._print_fiche(fiche_path)  # noqa: SLF001

    assert "Impression de la fiche annulée" in window.creation_tab.logs.toPlainText()
    printer.error = PrintError("Excel ne trouve pas l'imprimante Xerox Bac9.")
    _choose_printer(monkeypatch, controller, PrintFicheOptions("Xerox Bac9"))

    await controller._print_fiche(fiche_path)  # noqa: SLF001

    assert printer.printed == []
    assert errors == ["Fiche non imprimée: Excel ne trouve pas l'imprimante Xerox Bac9."]


@pytest.mark.asyncio
async def test_print_reports_missing_printers(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    printer: PrintRecorder,
    errors: list[str],
) -> None:
    controller, _window, _config, _services, _saves = _setup(qtbot, tmp_path)
    monkeypatch.setattr("projectflow.ui.controller.installed_printers", lambda: ([], ""))

    await controller._print_fiche(tmp_path / FICHE_NAME)  # noqa: SLF001

    assert errors == ["Aucune imprimante n'est installée sur ce poste."]
    assert printer.printed == []


def test_print_dialog_prefers_last_printer_then_system_default(qtbot: Any) -> None:
    printers = ["Xerox Bac1", "Xerox Bac2"]
    remembered = PrintFicheDialog(
        fiche_name=FICHE_NAME,
        printers=printers,
        preferred_printer="Xerox Bac2",
        default_printer="Xerox Bac1",
    )
    fallback = PrintFicheDialog(
        fiche_name=FICHE_NAME,
        printers=printers,
        preferred_printer="Ancienne imprimante",
        default_printer="Xerox Bac2",
    )
    qtbot.addWidget(remembered)
    qtbot.addWidget(fallback)
    remembered.copies_spin.setValue(3)

    assert remembered.options() == PrintFicheOptions("Xerox Bac2", copies=3)
    assert fallback.options() == PrintFicheOptions("Xerox Bac2", copies=1)
