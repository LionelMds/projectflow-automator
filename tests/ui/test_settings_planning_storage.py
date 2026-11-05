"""Settings, Planning section: the three storages and the copy of the local planning."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QDialog

from projectflow.config import AppConfig
from projectflow.core.planning.local_store import LocalPlanningStore
from projectflow.core.planning.poste_config import PlanningPosteConfig
from projectflow.ui.dialogs.settings import ONEDRIVE_NOTE_TEXT, SettingsDialog


def _dialog(qtbot: Any, planning: PlanningPosteConfig | None) -> SettingsDialog:
    dialog = SettingsDialog(AppConfig(), planning_config=planning)
    qtbot.addWidget(dialog)
    return dialog


def _poste(tmp_path: Path, **fields: Any) -> PlanningPosteConfig:
    return PlanningPosteConfig(local_directory=str(tmp_path / "local"), **fields)


async def _local_planning(tmp_path: Path) -> None:
    store = LocalPlanningStore(tmp_path / "local")
    await store.create("task", "a", {"id": "a", "title": "Pose"})


def test_storage_offers_local_onedrive_and_sharepoint(qtbot: Any, tmp_path: Path) -> None:
    dialog = _dialog(qtbot, _poste(tmp_path))

    assert dialog.planning_local_radio.text() == "Local (ce poste uniquement)"
    assert dialog.planning_onedrive_radio.text() == "OneDrive (partagé, recommandé)"
    assert (
        dialog.planning_sharepoint_radio.text()
        == "SharePoint (autorisation administrateur requise)"
    )
    assert dialog.planning_local_radio.isChecked()
    assert dialog.planning_onedrive_fields.isHidden()
    assert dialog.planning_sharepoint_fields.isHidden()


def test_onedrive_shows_its_folder_and_note_without_consent_banner(
    qtbot: Any,
    tmp_path: Path,
) -> None:
    dialog = _dialog(qtbot, _poste(tmp_path))

    dialog.planning_onedrive_radio.click()

    assert not dialog.planning_onedrive_fields.isHidden()
    assert dialog.planning_sharepoint_fields.isHidden()
    assert dialog.planning_storage_banner.isHidden()
    assert dialog.planning_onedrive_edit.text() == "ProjectFlow Planning"
    assert dialog.planning_onedrive_note.text() == ONEDRIVE_NOTE_TEXT
    assert "Aucune autorisation supplémentaire" in ONEDRIVE_NOTE_TEXT
    assert dialog._section_meta["planning"].text() == "OneDrive"  # noqa: SLF001

    dialog.planning_sharepoint_radio.click()
    assert dialog.planning_onedrive_fields.isHidden()
    assert not dialog.planning_storage_banner.isHidden()
    assert "Sites.ReadWrite.All" in dialog.planning_storage_banner.message_label.text()


def test_onedrive_poste_opens_on_onedrive(qtbot: Any, tmp_path: Path) -> None:
    dialog = _dialog(qtbot, _poste(tmp_path, backend="onedrive", onedrive_folder="Atelier/Plan"))

    assert dialog.planning_onedrive_radio.isChecked()
    assert not dialog.planning_onedrive_fields.isHidden()
    assert dialog.planning_onedrive_edit.text() == "Atelier/Plan"


def test_apply_to_planning_config_writes_the_onedrive_choice(qtbot: Any, tmp_path: Path) -> None:
    planning = _poste(tmp_path, onedrive_drive_id="b!drive")
    dialog = _dialog(qtbot, planning)

    dialog.planning_onedrive_radio.click()
    dialog.planning_onedrive_edit.setText(" /Planning atelier/ ")
    dialog.apply_to_planning_config(planning)

    assert planning.backend == "onedrive"
    assert planning.onedrive_folder == "Planning atelier"
    assert planning.onedrive_drive_id == "b!drive"
    assert planning.cache_key().startswith("onedrive-")

    dialog.planning_local_radio.click()
    dialog.apply_to_planning_config(planning)
    assert planning.backend == "local"
    assert planning.onedrive_folder == "Planning atelier"


def test_onedrive_without_folder_is_refused(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _dialog(qtbot, _poste(tmp_path))
    warnings: list[str] = []
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    dialog.planning_onedrive_radio.click()
    dialog.planning_onedrive_edit.setText(" / ")

    dialog.accept()

    assert dialog.result() != QDialog.DialogCode.Accepted
    assert warnings == [
        "Indiquez le nom du dossier OneDrive du planning ou choisissez le stockage local.",
    ]


def test_copy_button_needs_a_local_planning(qtbot: Any, tmp_path: Path) -> None:
    dialog = _dialog(qtbot, _poste(tmp_path))
    dialog.planning_onedrive_radio.click()

    assert dialog.planning_copy_button.text() == "Copier le planning local vers OneDrive…"
    assert not dialog.planning_copy_button.isEnabled()


def test_copy_button_asks_for_the_copy_and_shows_its_outcome(
    qtbot: Any,
    tmp_path: Path,
) -> None:
    asyncio.run(_local_planning(tmp_path))
    dialog = _dialog(qtbot, _poste(tmp_path))
    dialog.planning_onedrive_radio.click()
    requested: list[str] = []
    dialog.planning_copy_requested.connect(requested.append)
    assert dialog.planning_copy_button.isEnabled()

    dialog.planning_onedrive_edit.setText(" Planning atelier/ ")
    dialog.planning_copy_button.click()

    assert requested == ["Planning atelier"]
    assert not dialog.planning_copy_button.isEnabled()
    dialog.planning_copy_button.click()
    assert requested == ["Planning atelier"]

    dialog.planning_copy_finished("Copie terminée : 1 élément(s) copiés.", copied=True)

    assert dialog.planning_copy_button.isEnabled()
    assert dialog.planning_copy_button.text() == "Copier le planning local vers OneDrive…"
    assert not dialog.planning_copy_status.isHidden()
    assert "Enregistrer" in dialog.planning_copy_status.text()

    dialog.planning_copy_finished("Copie impossible : hors ligne", copied=False)
    assert dialog.planning_copy_status.text() == "Copie impossible : hors ligne"


def test_copy_button_is_off_without_planning(qtbot: Any) -> None:
    dialog = _dialog(qtbot, None)

    assert not dialog.planning_copy_button.isEnabled()
