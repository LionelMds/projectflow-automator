from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from projectflow.config import AppConfig


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Parametres")
        self._build_ui(config)

    def apply_to_config(self, config: AppConfig) -> None:
        config.paths.racine_projets = _optional_path(self.racine_edit.text())
        config.paths.dossier_reference = _optional_path(self.reference_edit.text())
        config.paths.repertoire_chantier.display_path = self.repertoire_path_edit.text().strip()
        config.paths.repertoire_chantier.drive_id = self.drive_id_edit.text().strip()
        config.paths.repertoire_chantier.item_id = self.item_id_edit.text().strip()

        config.planner.enabled = self.planner_enabled_checkbox.isChecked()
        config.planner.plan_id = self.plan_id_edit.text().strip()
        config.planner.plan_name = self.plan_name_edit.text().strip()
        config.planner.bucket_id = self.bucket_id_edit.text().strip()
        config.planner.bucket_name = self.bucket_name_edit.text().strip()
        config.planner.due_days = self.due_days_spin.value()

    def _build_ui(self, config: AppConfig) -> None:
        root = QVBoxLayout(self)
        root.addWidget(self._paths_group(config))
        root.addWidget(self._planner_group(config))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _paths_group(self, config: AppConfig) -> QGroupBox:
        group = QGroupBox("Chemins")
        layout = QFormLayout(group)
        self.racine_edit = QLineEdit(str(config.paths.racine_projets or ""))
        self.reference_edit = QLineEdit(str(config.paths.dossier_reference or ""))
        self.repertoire_path_edit = QLineEdit(config.paths.repertoire_chantier.display_path)
        self.drive_id_edit = QLineEdit(config.paths.repertoire_chantier.drive_id)
        self.item_id_edit = QLineEdit(config.paths.repertoire_chantier.item_id)
        layout.addRow("Racine projets", _browse_row(self.racine_edit, directory=True))
        layout.addRow("Dossier de reference", _browse_row(self.reference_edit, directory=True))
        layout.addRow(
            "Repertoire chantier",
            _browse_row(self.repertoire_path_edit, directory=False),
        )
        layout.addRow("Drive ID", self.drive_id_edit)
        layout.addRow("Item ID", self.item_id_edit)
        return group

    def _planner_group(self, config: AppConfig) -> QGroupBox:
        group = QGroupBox("Planner")
        layout = QFormLayout(group)
        self.planner_enabled_checkbox = QCheckBox("Creer une tache Planner")
        self.planner_enabled_checkbox.setChecked(config.planner.enabled)
        self.plan_id_edit = QLineEdit(config.planner.plan_id)
        self.plan_name_edit = QLineEdit(config.planner.plan_name)
        self.bucket_id_edit = QLineEdit(config.planner.bucket_id)
        self.bucket_name_edit = QLineEdit(config.planner.bucket_name)
        self.due_days_spin = QSpinBox()
        self.due_days_spin.setRange(0, 365)
        self.due_days_spin.setValue(config.planner.due_days)
        layout.addRow("", self.planner_enabled_checkbox)
        layout.addRow("Plan ID", self.plan_id_edit)
        layout.addRow("Plan", self.plan_name_edit)
        layout.addRow("Bucket ID", self.bucket_id_edit)
        layout.addRow("Bucket", self.bucket_name_edit)
        layout.addRow("Echeance (jours)", self.due_days_spin)
        return group


def _browse_row(edit: QLineEdit, *, directory: bool) -> QWidget:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    button = QPushButton("Parcourir")

    def browse() -> None:
        if directory:
            selected = QFileDialog.getExistingDirectory(widget, "Selectionner")
        else:
            selected, _ = QFileDialog.getOpenFileName(
                widget,
                "Selectionner",
                filter="Excel (*.xlsx)",
            )
        if selected:
            edit.setText(selected)

    button.clicked.connect(browse)
    layout.addWidget(edit)
    layout.addWidget(button)
    return widget


def _optional_path(value: str) -> Path | None:
    stripped = value.strip()
    if not stripped:
        return None
    return Path(stripped).expanduser()
