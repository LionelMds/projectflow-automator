from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from PySide6.QtCore import QSignalBlocker
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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from projectflow.application_settings import ApplicationSettings
from projectflow.auth.msal_client import PLANNER_GRAPH_SCOPES, MsalAccessTokenProvider
from projectflow.config import AppConfig, RepertoireChantierConfig
from projectflow.exceptions import ProjectFlowError
from projectflow.graph.client import GraphClient
from projectflow.graph.planner import GraphPlannerClient
from projectflow.outlook.local import detect_local_outlook_accounts, validate_local_outlook_account
from projectflow.platform.paths import native_path_text


class SettingsDialog(QDialog):
    def __init__(
        self,
        config: AppConfig,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Parametres")
        self._reconnect_repertoire = False
        self._build_ui(config)

    def apply_to_config(self, config: AppConfig) -> None:
        config.user.initials = self.user_initials_edit.text()
        config.paths.racine_projets = _optional_path(self.racine_edit.text())
        config.paths.dossier_reference = _optional_path(self.reference_edit.text())
        repertoire_path = native_path_text(self.repertoire_path_edit.text())
        previous_repertoire = config.paths.repertoire_chantier
        keep_target = (
            repertoire_path == previous_repertoire.display_path and not self._reconnect_repertoire
        )
        # An in-flight resolver keeps the old object; it must not restore stale
        # cloud identifiers into the configuration accepted by this dialog.
        config.paths.repertoire_chantier = RepertoireChantierConfig(
            display_path=repertoire_path,
            open_path=native_path_text(self.repertoire_open_path_edit.text()),
            drive_id=previous_repertoire.drive_id if keep_target else "",
            item_id=previous_repertoire.item_id if keep_target else "",
            cloud_only=self.repertoire_cloud_checkbox.isChecked(),
        )

        config.outlook.enabled = self.outlook_enabled_checkbox.isChecked()
        config.outlook.mailbox_email = self.outlook_account_combo.currentText().strip()
        config.outlook.mailbox_store_id = self._selected_outlook_store_id()
        config.outlook.base_folder = self._selected_outlook_base_folder()

        config.planner.enabled = self.planner_enabled_checkbox.isChecked()
        config.planner.plan_id = self._selected_planner_plan_id()
        config.planner.plan_name = self.planner_plan_combo.currentText().strip()
        config.planner.bucket_id = self._selected_planner_bucket_id()
        config.planner.bucket_name = self.planner_bucket_combo.currentText().strip()
        config.planner.due_days = self.planner_due_days_spin.value()

    def accept(self) -> None:
        if "://" in self.repertoire_open_path_edit.text():
            QMessageBox.warning(
                self,
                "Repertoire chantier",
                "Selectionnez un fichier synchronise sur ce poste pour l'ouverture Excel. "
                "Les liens OneDrive / SharePoint vont dans le champ Repertoire chantier.",
            )
            return
        account_text = self.outlook_account_combo.currentText().strip()
        if self.outlook_enabled_checkbox.isChecked() and not account_text:
            QMessageBox.warning(
                self,
                "Outlook",
                "Selectionnez un compte Outlook ou desactivez la creation Outlook.",
            )
            return
        if self.planner_enabled_checkbox.isChecked() and not self._selected_planner_plan_id():
            QMessageBox.warning(
                self,
                "Planner",
                "Selectionnez un plan Planner ou desactivez la creation Planner.",
            )
            return
        if self.planner_enabled_checkbox.isChecked() and not self._selected_planner_bucket_id():
            QMessageBox.warning(
                self,
                "Planner",
                "Selectionnez une colonne Planner ou desactivez la creation Planner.",
            )
            return
        super().accept()

    def _build_ui(self, config: AppConfig) -> None:
        root = QVBoxLayout(self)
        user_group = QGroupBox("Utilisateur")
        user_layout = QFormLayout(user_group)
        self.user_initials_edit = QLineEdit(config.user.initials)
        user_layout.addRow("Initiales utilisateur", self.user_initials_edit)
        root.addWidget(user_group)
        root.addWidget(self._paths_group(config))
        root.addWidget(self._outlook_group(config))
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
        self.racine_edit = QLineEdit(native_path_text(config.paths.racine_projets))
        self.reference_edit = QLineEdit(native_path_text(config.paths.dossier_reference))
        self.repertoire_path_edit = QLineEdit(
            native_path_text(config.paths.repertoire_chantier.display_path),
        )
        self.repertoire_path_edit.setPlaceholderText("Chemin Excel ou lien OneDrive / SharePoint")
        layout.addRow("Racine projets", _browse_row(self.racine_edit, directory=True))
        layout.addRow("Dossier de reference", _browse_row(self.reference_edit, directory=True))
        layout.addRow(
            "Repertoire chantier",
            _browse_row(self.repertoire_path_edit, directory=False),
        )
        self.repertoire_open_path_edit = QLineEdit(
            native_path_text(config.paths.repertoire_chantier.open_path),
        )
        self.repertoire_open_path_edit.setPlaceholderText("Facultatif : fichier Excel sur ce poste")
        self.repertoire_open_path_edit.setToolTip(
            "Fichier synchronise a ouvrir dans Excel. "
            "Le repertoire chantier ci-dessus reste utilise pour les modifications partagees.",
        )
        layout.addRow(
            "Fichier synchronise pour ouverture Excel",
            _browse_row(self.repertoire_open_path_edit, directory=False),
        )
        self.repertoire_cloud_checkbox = QCheckBox("Repertoire partage OneDrive / SharePoint")
        self.repertoire_cloud_checkbox.setChecked(
            config.paths.repertoire_chantier.cloud_only
            or config.paths.repertoire_chantier.is_configured,
        )
        self.repertoire_cloud_checkbox.setToolTip(
            "Impose la connexion cloud meme si le dossier synchronise n'est pas reconnu. "
            "Les dossiers OneDrive et SharePoint detectes utilisent toujours le cloud.",
        )
        layout.addRow("", self.repertoire_cloud_checkbox)
        self.repertoire_reconnect_button = QPushButton("Reconnecter a OneDrive / SharePoint")
        self.repertoire_reconnect_button.clicked.connect(self._request_repertoire_reconnection)
        layout.addRow("", self.repertoire_reconnect_button)
        return group

    def _request_repertoire_reconnection(self) -> None:
        self._reconnect_repertoire = True
        self.repertoire_cloud_checkbox.setChecked(True)
        self.repertoire_reconnect_button.setText("Reconnexion apres enregistrement des parametres")
        self.repertoire_reconnect_button.setEnabled(False)

    def _planner_group(self, config: AppConfig) -> QGroupBox:
        group = QGroupBox("Microsoft Planner")
        layout = QFormLayout(group)
        self.planner_enabled_checkbox = QCheckBox("Creer une tache Planner")
        self.planner_enabled_checkbox.setChecked(config.planner.enabled)
        self.planner_plan_combo = QComboBox()
        self.planner_plan_combo.setEditable(True)
        self.planner_plan_combo.setPlaceholderText("plan Planner")
        if config.planner.plan_id or config.planner.plan_name:
            self.planner_plan_combo.addItem(
                config.planner.plan_name or config.planner.plan_id,
                config.planner.plan_id,
            )
            self.planner_plan_combo.setCurrentIndex(0)
        self.planner_bucket_combo = QComboBox()
        self.planner_bucket_combo.setEditable(True)
        self.planner_bucket_combo.setPlaceholderText("colonne Planner")
        if config.planner.bucket_id or config.planner.bucket_name:
            self.planner_bucket_combo.addItem(
                config.planner.bucket_name or config.planner.bucket_id,
                config.planner.bucket_id,
            )
            self.planner_bucket_combo.setCurrentIndex(0)
        self._planner_bucket_plan_id = self._selected_planner_plan_id()
        self.planner_due_days_spin = QSpinBox()
        self.planner_due_days_spin.setRange(0, 365)
        self.planner_due_days_spin.setValue(config.planner.due_days)
        self.planner_due_days_spin.setSuffix(" jours")
        layout.addRow("", self.planner_enabled_checkbox)
        layout.addRow("Plan", self._planner_plan_row())
        layout.addRow("Colonne", self._planner_bucket_row())
        layout.addRow("Echeance", self.planner_due_days_spin)
        return group

    def _planner_plan_row(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        self.planner_refresh_button = QPushButton("Detecter")
        self.planner_refresh_button.clicked.connect(
            lambda: asyncio.create_task(self._load_planner_plans()),
        )
        self.planner_test_button = QPushButton("Tester")
        self.planner_test_button.clicked.connect(
            lambda: asyncio.create_task(self._test_planner()),
        )
        self.planner_plan_combo.currentIndexChanged.connect(self._planner_plan_changed)
        self.planner_plan_combo.currentTextChanged.connect(self._planner_plan_changed)
        layout.addWidget(self.planner_plan_combo, 1)
        layout.addWidget(self.planner_refresh_button)
        layout.addWidget(self.planner_test_button)
        return widget

    def _planner_bucket_row(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        self.planner_bucket_refresh_button = QPushButton("Detecter colonnes")
        self.planner_bucket_refresh_button.clicked.connect(
            lambda: asyncio.create_task(self._load_planner_buckets()),
        )
        layout.addWidget(self.planner_bucket_combo, 1)
        layout.addWidget(self.planner_bucket_refresh_button)
        return widget

    def _planner_plan_changed(self) -> None:
        plan_id = self._selected_planner_plan_id()
        if plan_id != self._planner_bucket_plan_id:
            self.planner_bucket_combo.clear()
            self._planner_bucket_plan_id = plan_id

    def _outlook_group(self, config: AppConfig) -> QGroupBox:
        group = QGroupBox(_mail_group_title())
        layout = QFormLayout(group)
        self.outlook_enabled_checkbox = QCheckBox(_mail_checkbox_text())
        self.outlook_enabled_checkbox.setChecked(config.outlook.enabled)
        self.outlook_account_combo = QComboBox()
        self.outlook_account_combo.setEditable(True)
        self.outlook_account_combo.setPlaceholderText(_mail_account_placeholder())
        if config.outlook.mailbox_email or config.outlook.mailbox_store_id:
            label = config.outlook.mailbox_email or "Compte Outlook configure"
            self.outlook_account_combo.addItem(label, config.outlook.mailbox_store_id)
            self.outlook_account_combo.setCurrentIndex(0)
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
        _refresh_combo(
            self.outlook_account_combo,
            [(account.label, account.id) for account in accounts],
            selected_id=self._selected_outlook_store_id(),
        )

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

    async def _load_planner_plans(self) -> None:
        try:
            plans = await _planner_client().list_plans()
        except ProjectFlowError as exc:
            QMessageBox.warning(self, "Planner", str(exc))
            return
        _refresh_combo(
            self.planner_plan_combo,
            [(plan.title, plan.id) for plan in plans],
            selected_id=self._selected_planner_plan_id(),
        )
        self._planner_plan_changed()
        if self._selected_planner_plan_id():
            await self._load_planner_buckets()

    async def _load_planner_buckets(self) -> None:
        plan_id = self._selected_planner_plan_id()
        if not plan_id:
            QMessageBox.warning(self, "Planner", "Selectionnez d'abord un plan Planner.")
            return
        try:
            buckets = await _planner_client().list_buckets(plan_id=plan_id)
        except ProjectFlowError as exc:
            QMessageBox.warning(self, "Planner", str(exc))
            return
        if plan_id != self._selected_planner_plan_id():
            return
        _refresh_combo(
            self.planner_bucket_combo,
            [(bucket.name, bucket.id) for bucket in buckets],
            selected_id=self._selected_planner_bucket_id(),
        )

    async def _test_planner(self) -> None:
        plan_id = self._selected_planner_plan_id()
        bucket_id = self._selected_planner_bucket_id()
        if not plan_id or not bucket_id:
            QMessageBox.warning(self, "Planner", "Selectionnez un plan et une colonne Planner.")
            return
        try:
            buckets = await _planner_client().list_buckets(plan_id=plan_id)
            bucket_ids = {bucket.id for bucket in buckets}
        except ProjectFlowError as exc:
            QMessageBox.warning(self, "Planner", str(exc))
            return
        if (plan_id, bucket_id) != (
            self._selected_planner_plan_id(),
            self._selected_planner_bucket_id(),
        ):
            return
        if bucket_id not in bucket_ids:
            QMessageBox.warning(self, "Planner", "La colonne selectionnee est introuvable.")
            return
        self.planner_enabled_checkbox.setChecked(True)
        QMessageBox.information(self, "Planner", "Plan et colonne Planner accessibles.")

    def _selected_planner_plan_id(self) -> str:
        return _selected_combo_id(self.planner_plan_combo)

    def _selected_planner_bucket_id(self) -> str:
        return _selected_combo_id(self.planner_bucket_combo)


def _refresh_combo(
    combo: QComboBox,
    items: list[tuple[str, str]],
    *,
    selected_id: str,
) -> None:
    selected_text = combo.currentText().strip()
    with QSignalBlocker(combo):
        combo.clear()
        for label, item_id in items:
            combo.addItem(label, item_id)
        index = combo.findData(selected_id) if selected_id else -1
        if index >= 0:
            combo.setCurrentIndex(index)
        elif selected_id or selected_text:
            combo.addItem(selected_text or selected_id, selected_id)
            combo.setCurrentIndex(combo.count() - 1)
        elif combo.count():
            combo.setCurrentIndex(0)


def _browse_row(edit: QLineEdit, *, directory: bool) -> QWidget:
    widget = QWidget()
    layout = QHBoxLayout(widget)
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
            edit.setText(native_path_text(selected))

    button.clicked.connect(browse)
    layout.addWidget(edit, 1)
    layout.addWidget(button)
    return widget


def _optional_path(value: str) -> Path | None:
    stripped = value.strip()
    if not stripped:
        return None
    return Path(stripped).expanduser()


def _selected_combo_id(combo: QComboBox) -> str:
    index = combo.currentIndex()
    if index < 0:
        return combo.currentText().strip()
    data = combo.currentData()
    if isinstance(data, str):
        current_text = combo.currentText().strip()
        if current_text == combo.itemText(index):
            return data.strip()
    return combo.currentText().strip()


def _mail_group_title() -> str:
    return "Mail macOS" if sys.platform == "darwin" else "Outlook"


def _mail_checkbox_text() -> str:
    if sys.platform == "darwin":
        return "Creer les dossiers Mail"
    return "Creer les dossiers Outlook"


def _mail_account_placeholder() -> str:
    if sys.platform == "darwin":
        return "compte Mail local"
    return "compte Outlook local"


def _planner_client() -> GraphPlannerClient:
    settings = ApplicationSettings.load()
    token_provider = MsalAccessTokenProvider(
        client_id=settings.microsoft_client_id,
        scopes=PLANNER_GRAPH_SCOPES,
    )
    return GraphPlannerClient(graph=GraphClient(token_provider=token_provider))
