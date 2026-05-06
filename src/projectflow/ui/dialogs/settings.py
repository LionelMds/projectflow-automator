from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from projectflow.config import AppConfig
from projectflow.exceptions import ProjectFlowError
from projectflow.outlook.local import detect_local_outlook_accounts, validate_local_outlook_account


class SettingsDialog(QDialog):
    def __init__(
        self,
        config: AppConfig,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Parametres")
        self._build_ui(config)

    def apply_to_config(self, config: AppConfig) -> None:
        config.paths.racine_projets = _optional_path(self.racine_edit.text())
        config.paths.dossier_reference = _optional_path(self.reference_edit.text())
        config.paths.repertoire_chantier.display_path = self.repertoire_path_edit.text().strip()
        config.paths.repertoire_chantier.drive_id = ""
        config.paths.repertoire_chantier.item_id = ""

        config.outlook.enabled = self.outlook_enabled_checkbox.isChecked()
        config.outlook.mailbox_email = self.outlook_account_combo.currentText().strip()
        config.outlook.mailbox_store_id = self._selected_outlook_store_id()
        config.outlook.base_folder = self._selected_outlook_base_folder()

    def accept(self) -> None:
        account_text = self.outlook_account_combo.currentText().strip()
        if self.outlook_enabled_checkbox.isChecked() and not account_text:
            QMessageBox.warning(
                self,
                "Outlook",
                "Selectionnez un compte Outlook ou desactivez la creation Outlook.",
            )
            return
        super().accept()

    def _build_ui(self, config: AppConfig) -> None:
        root = QVBoxLayout(self)
        root.addWidget(self._paths_group(config))
        root.addWidget(self._outlook_group(config))

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
        layout.addRow("Racine projets", _browse_row(self.racine_edit, directory=True))
        layout.addRow("Dossier de reference", _browse_row(self.reference_edit, directory=True))
        layout.addRow(
            "Repertoire chantier",
            _browse_row(self.repertoire_path_edit, directory=False),
        )
        return group

    def _outlook_group(self, config: AppConfig) -> QGroupBox:
        group = QGroupBox("Outlook")
        layout = QFormLayout(group)
        self.outlook_enabled_checkbox = QCheckBox("Creer les dossiers Outlook")
        self.outlook_enabled_checkbox.setChecked(config.outlook.enabled)
        self.outlook_account_combo = QComboBox()
        self.outlook_account_combo.setEditable(True)
        self.outlook_account_combo.setPlaceholderText("compte Outlook local")
        if config.outlook.mailbox_email or config.outlook.mailbox_store_id:
            label = config.outlook.mailbox_email or "Compte Outlook configure"
            self.outlook_account_combo.addItem(label, config.outlook.mailbox_store_id)
        self.outlook_base_folder_combo = QComboBox()
        self.outlook_base_folder_combo.addItem("Racine du compte", "root")
        self.outlook_base_folder_combo.addItem("Boite de reception", "inbox")
        base_index = self.outlook_base_folder_combo.findData(config.outlook.target_base_folder)
        self.outlook_base_folder_combo.setCurrentIndex(max(0, base_index))
        layout.addRow("", self.outlook_enabled_checkbox)
        layout.addRow("Compte", self._outlook_selector_row())
        layout.addRow("Emplacement", self.outlook_base_folder_combo)
        return group

    def _outlook_selector_row(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        self.outlook_refresh_button = QPushButton("Detecter")
        self.outlook_refresh_button.clicked.connect(self._load_outlook_accounts)
        self.outlook_test_button = QPushButton("Tester")
        self.outlook_test_button.clicked.connect(self._test_outlook_account)
        layout.addWidget(self.outlook_account_combo, 1)
        layout.addWidget(self.outlook_refresh_button)
        layout.addWidget(self.outlook_test_button)
        return widget

    def _load_outlook_accounts(self) -> None:
        try:
            accounts = detect_local_outlook_accounts()
        except ProjectFlowError as exc:
            QMessageBox.warning(self, "Outlook", str(exc))
            return
        current_store_id = self._selected_outlook_store_id()
        self.outlook_account_combo.clear()
        for account in accounts:
            self.outlook_account_combo.addItem(account.label, account.id)
        if current_store_id:
            index = self.outlook_account_combo.findData(current_store_id)
            if index >= 0:
                self.outlook_account_combo.setCurrentIndex(index)
        elif self.outlook_account_combo.count():
            self.outlook_account_combo.setCurrentIndex(0)

    def _test_outlook_account(self) -> None:
        try:
            validate_local_outlook_account(
                store_id=self._selected_outlook_store_id(),
                mailbox=self.outlook_account_combo.currentText().strip(),
                base_folder=self._selected_outlook_base_folder(),
            )
        except ProjectFlowError as exc:
            QMessageBox.warning(self, "Outlook", str(exc))
            return
        self.outlook_enabled_checkbox.setChecked(True)
        QMessageBox.information(self, "Outlook", "Compte Outlook accessible.")

    def _selected_outlook_store_id(self) -> str:
        index = self.outlook_account_combo.currentIndex()
        if index < 0:
            return ""
        data = self.outlook_account_combo.currentData()
        if not isinstance(data, str):
            return ""
        current_text = self.outlook_account_combo.currentText().strip()
        if current_text != self.outlook_account_combo.itemText(index):
            return ""
        return data.strip()

    def _selected_outlook_base_folder(self) -> str:
        data = self.outlook_base_folder_combo.currentData()
        if isinstance(data, str):
            return data
        return "root"


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
