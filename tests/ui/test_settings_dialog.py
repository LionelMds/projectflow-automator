from __future__ import annotations

from pathlib import Path

from projectflow.config import AppConfig
from projectflow.ui.dialogs.settings import SettingsDialog


def test_settings_dialog_applies_values(qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    config = AppConfig()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    dialog.racine_edit.setText(str(tmp_path / "clients"))
    dialog.reference_edit.setText(str(tmp_path / "reference"))
    dialog.repertoire_path_edit.setText("Entreprise/Rep.xlsx")
    dialog.drive_id_edit.setText("drive")
    dialog.item_id_edit.setText("item")
    dialog.planner_enabled_checkbox.setChecked(True)
    dialog.plan_id_edit.setText("plan")
    dialog.plan_name_edit.setText("Projets")
    dialog.bucket_id_edit.setText("bucket")
    dialog.bucket_name_edit.setText("Dossiers")
    dialog.due_days_spin.setValue(14)

    dialog.apply_to_config(config)

    assert config.paths.racine_projets == tmp_path / "clients"
    assert config.paths.dossier_reference == tmp_path / "reference"
    assert config.paths.repertoire_chantier.drive_id == "drive"
    assert config.paths.repertoire_chantier.item_id == "item"
    assert config.planner.enabled is True
    assert config.planner.plan_id == "plan"
    assert config.planner.bucket_id == "bucket"
    assert config.planner.due_days == 14
