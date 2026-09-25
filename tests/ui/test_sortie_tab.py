from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QFileDialog

from projectflow.core.sortie_service import OutputInventory
from projectflow.ui.sortie_tab import SortieDossierTab


def test_plan_dialog_opens_in_existing_accented_folder(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tab = SortieDossierTab()
    qtbot.addWidget(tab)
    plans = tmp_path / "Plans" / "Plan d'exécution"
    photos = tmp_path / "Photos"
    opened: list[str] = []

    def record_dialog(_parent: object, _title: str, directory: str, _filter: str) -> Any:
        opened.append(directory)
        return [], ""

    monkeypatch.setattr(QFileDialog, "getOpenFileNames", record_dialog)
    tab.set_project_directory(tmp_path)
    tab.set_inventory(OutputInventory(plan_directory=plans, photo_directory=photos))

    tab.plan_browse_button.click()
    tab.photo_browse_button.click()

    assert opened == [str(plans), str(photos)]


def test_plan_dialog_defaults_to_accented_name_without_inventory_folder(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tab = SortieDossierTab()
    qtbot.addWidget(tab)
    opened: list[str] = []
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileNames",
        lambda _parent, _title, directory, _filter: (opened.append(directory), ([], ""))[1],
    )
    tab.set_project_directory(tmp_path)
    tab.set_inventory(OutputInventory())

    tab.plan_browse_button.click()

    assert opened == [str(tmp_path / "Plans" / "Plan d'exécution")]
