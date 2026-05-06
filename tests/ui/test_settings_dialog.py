from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QDialog

from projectflow.config import AppConfig
from projectflow.outlook.models import OutlookAccount
from projectflow.ui.dialogs.settings import SettingsDialog


def test_settings_dialog_applies_values(qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    config = AppConfig()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    dialog.racine_edit.setText(str(tmp_path / "clients"))
    dialog.reference_edit.setText(str(tmp_path / "reference"))
    dialog.repertoire_path_edit.setText("Entreprise/Rep.xlsx")
    dialog.outlook_enabled_checkbox.setChecked(True)
    dialog.outlook_account_combo.setEditText("projets@balzmetal.ch")
    dialog.outlook_base_folder_combo.setCurrentIndex(
        dialog.outlook_base_folder_combo.findData("inbox"),
    )

    dialog.apply_to_config(config)

    assert config.paths.racine_projets == tmp_path / "clients"
    assert config.paths.dossier_reference == tmp_path / "reference"
    assert config.paths.repertoire_chantier.display_path == "Entreprise/Rep.xlsx"
    assert config.paths.repertoire_chantier.drive_id == ""
    assert config.paths.repertoire_chantier.item_id == ""
    assert config.outlook.enabled is True
    assert config.outlook.mailbox_email == "projets@balzmetal.ch"
    assert config.outlook.mailbox_store_id == ""
    assert config.outlook.base_folder == "inbox"


def test_settings_dialog_detects_outlook_accounts(qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    config = AppConfig()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(
        "projectflow.ui.dialogs.settings.detect_local_outlook_accounts",
        lambda: [
            OutlookAccount(
                id="store-1",
                display_name="Boite Balz",
                email="lionel@balzmetal.ch",
            ),
        ],
    )

    dialog.outlook_refresh_button.click()
    dialog.outlook_enabled_checkbox.setChecked(True)
    dialog.apply_to_config(config)

    assert dialog.outlook_account_combo.currentText() == "Boite Balz (lionel@balzmetal.ch)"
    assert config.outlook.mailbox_email == "Boite Balz (lionel@balzmetal.ch)"
    assert config.outlook.mailbox_store_id == "store-1"


def test_settings_dialog_tests_selected_outlook_account(qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    config = AppConfig()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    validated: list[tuple[str, str]] = []
    selected_base_folders: list[str] = []

    def fake_information(*_args: object) -> None:
        return None

    monkeypatch.setattr(
        "projectflow.ui.dialogs.settings.validate_local_outlook_account",
        lambda *, store_id, mailbox, base_folder: (
            validated.append((store_id, mailbox)),
            selected_base_folders.append(base_folder),
        ),
    )
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", fake_information)
    dialog.outlook_account_combo.addItem("Boite Balz", "store-1")
    dialog.outlook_account_combo.setCurrentIndex(0)
    dialog.outlook_base_folder_combo.setCurrentIndex(
        dialog.outlook_base_folder_combo.findData("inbox"),
    )

    dialog.outlook_test_button.click()

    assert validated == [("store-1", "Boite Balz")]
    assert selected_base_folders == ["inbox"]
    assert dialog.outlook_enabled_checkbox.isChecked() is True


def test_settings_dialog_refuses_enabled_outlook_without_account(
    qtbot,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    config = AppConfig()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    warnings: list[str] = []
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    dialog.outlook_enabled_checkbox.setChecked(True)

    dialog.accept()

    assert dialog.result() != QDialog.DialogCode.Accepted
    assert warnings == ["Selectionnez un compte Outlook ou desactivez la creation Outlook."]
