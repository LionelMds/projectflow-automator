from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError
from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from projectflow.application_settings import ApplicationSettings
from projectflow.auth.msal_client import PLANNER_GRAPH_SCOPES, MsalAccessTokenProvider
from projectflow.cad.license_storage import SolidWorksLicenseStorage
from projectflow.cad.templates import check_document_manager
from projectflow.config import AppConfig, CadConfig, CadPropertyNames, RepertoireChantierConfig
from projectflow.exceptions import ProjectFlowError
from projectflow.graph.client import GraphClient
from projectflow.graph.planner import GraphPlannerClient
from projectflow.logging import redact_sensitive_links
from projectflow.outlook.local import detect_local_outlook_accounts, validate_local_outlook_account
from projectflow.platform.paths import native_path_text


class LicenseKeyStorage(Protocol):
    def load(self) -> str:
        """Return the stored key, or an empty string."""

    def save(self, value: str) -> bool:
        """Store the key; return False when the credential store refused it."""

    def clear(self) -> bool:
        """Delete the key; return False when it could not be deleted."""


class SettingsDialog(QDialog):
    def __init__(
        self,
        config: AppConfig,
        *,
        parent: QWidget | None = None,
        license_storage: LicenseKeyStorage | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Parametres")
        self._license_storage = license_storage or SolidWorksLicenseStorage()
        self._license_clear_requested = False
        self._reconnect_repertoire = False
        self._microsoft_sign_in_requested = False
        self._planner_task: asyncio.Task[None] | None = None
        self._finished = False
        self._build_ui(config)
        self.finished.connect(self._cancel_planner_action)

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
        config.cad = self._cad_config()

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
        if not self._validate_cad():
            return
        if not self._store_license_key():
            return
        super().accept()

    def _validate_cad(self) -> bool:
        try:
            cad = self._cad_config()
        except ValidationError as exc:
            QMessageBox.warning(self, "Modeles CAO", _validation_message(exc))
            return False
        reference = _optional_path(self.reference_edit.text())
        if reference is not None and any(
            template is not None and _same_or_inside(template, reference)
            for template in (cad.solidworks_template_dir, cad.autocad_template_dir)
        ):
            QMessageBox.warning(
                self,
                "Modeles CAO",
                "Les dossiers modeles CAO doivent etre distincts du dossier de reference, "
                "sinon ils seraient copies a chaque creation de projet.",
            )
            return False
        return True

    def _store_license_key(self) -> bool:
        key = self.solidworks_license_edit.text().strip()
        if key:
            stored = self._license_storage.save(key)
        elif self._license_clear_requested:
            stored = self._license_storage.clear()
        else:
            return True
        if not stored:
            QMessageBox.warning(
                self,
                "Modeles CAO",
                "Le gestionnaire d'identifiants du systeme a refuse la cle Document Manager.",
            )
        return stored

    def _cad_config(self) -> CadConfig:
        return CadConfig(
            solidworks_template_dir=_optional_path(self.cad_solidworks_edit.text()),
            autocad_template_dir=_optional_path(self.cad_autocad_edit.text()),
            destination_subfolder=self.cad_subfolder_edit.text(),
            properties=CadPropertyNames(
                **{
                    field: edit.text() or CadPropertyNames.model_fields[field].default
                    for field, edit in self.cad_property_edits.items()
                },
            ),
        )

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
        root.addWidget(self._cad_group(config))
        root.addWidget(self._microsoft_group())

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

    def _cad_group(self, config: AppConfig) -> QGroupBox:
        group = QGroupBox("Modèles CAO")
        layout = QFormLayout(group)
        self.cad_solidworks_edit = QLineEdit(native_path_text(config.cad.solidworks_template_dir))
        self.cad_solidworks_edit.setPlaceholderText("Dossier distinct du dossier de reference")
        self.cad_autocad_edit = QLineEdit(native_path_text(config.cad.autocad_template_dir))
        self.cad_subfolder_edit = QLineEdit(config.cad.destination_subfolder)
        self.cad_subfolder_edit.setPlaceholderText("Vide = racine du dossier projet")
        self.solidworks_license_edit = QLineEdit()
        self.solidworks_license_edit.setEchoMode(QLineEdit.EchoMode.Password)
        has_key = bool(self._license_storage.load())
        self.solidworks_license_edit.setPlaceholderText(
            "Cle enregistree - laisser vide pour la conserver" if has_key else "Aucune cle",
        )
        self.solidworks_license_clear_button = QPushButton("Effacer la cle")
        self.solidworks_license_clear_button.setEnabled(has_key)
        self.solidworks_license_clear_button.clicked.connect(self._request_license_clear)
        license_row = QWidget()
        license_layout = QHBoxLayout(license_row)
        license_layout.setContentsMargins(0, 0, 0, 0)
        self.solidworks_license_test_button = QPushButton("Tester")
        self.solidworks_license_test_button.setToolTip(
            "Verifie que Document Manager est installe et que la cle ouvre un fichier modele.",
        )
        self.solidworks_license_test_button.clicked.connect(self._test_document_manager)
        license_layout.addWidget(self.solidworks_license_edit, 1)
        license_layout.addWidget(self.solidworks_license_test_button)
        license_layout.addWidget(self.solidworks_license_clear_button)
        layout.addRow(
            "Dossier modele SolidWorks",
            _browse_row(self.cad_solidworks_edit, directory=True),
        )
        layout.addRow("Dossier modele AutoCAD", _browse_row(self.cad_autocad_edit, directory=True))
        layout.addRow("Sous-dossier dans le projet", self.cad_subfolder_edit)
        layout.addRow("Cle Document Manager", license_row)

        self.cad_property_edits: dict[str, QLineEdit] = {}
        properties = QWidget()
        properties_layout = QGridLayout(properties)
        properties_layout.setContentsMargins(0, 0, 0, 0)
        for index, (field, label) in enumerate([
            ("projet", "Projet"),
            ("client", "Client"),
            ("auteur", "Auteur"),
            ("description", "Description"),
            ("revision", "Revision"),
            ("revision_defaut", "Rev. par defaut"),
        ]):
            edit = QLineEdit(getattr(config.cad.properties, field))
            edit.setToolTip(f"Nom exact de la propriete SolidWorks ({label}), accents compris.")
            self.cad_property_edits[field] = edit
            edit.setMinimumWidth(edit.fontMetrics().horizontalAdvance("M" * 10) + 12)
            row, column = divmod(index, 2)
            properties_layout.addWidget(QLabel(label), row, column * 2)
            properties_layout.addWidget(edit, row, column * 2 + 1)
        properties_layout.setColumnStretch(1, 1)
        properties_layout.setColumnStretch(3, 1)
        layout.addRow("Proprietes", properties)
        return group

    def _test_document_manager(self) -> None:
        key = self.solidworks_license_edit.text().strip()
        if not key and not self._license_clear_requested:
            key = self._license_storage.load()
        try:
            message = check_document_manager(
                key,
                _optional_path(self.cad_solidworks_edit.text()),
            )
        except ProjectFlowError as exc:
            self._show_error("Document Manager", str(exc))
            return
        QMessageBox.information(self, "Document Manager", message)

    def _request_license_clear(self) -> None:
        self._license_clear_requested = True
        self.solidworks_license_edit.clear()
        self.solidworks_license_edit.setPlaceholderText("Cle effacee apres enregistrement")
        self.solidworks_license_clear_button.setEnabled(False)

    @property
    def microsoft_sign_in_requested(self) -> bool:
        return self._microsoft_sign_in_requested

    def _microsoft_group(self) -> QGroupBox:
        group = QGroupBox("Compte Microsoft")
        layout = QFormLayout(group)
        self.microsoft_sign_in_button = QPushButton("Se reconnecter au compte Microsoft")
        self.microsoft_sign_in_button.setToolTip(
            "Oublie la connexion Microsoft enregistree sur ce poste. Apres OK, "
            "le navigateur s'ouvre pour choisir le compte et se connecter a nouveau.",
        )
        self.microsoft_sign_in_button.clicked.connect(self._request_microsoft_sign_in)
        layout.addRow("", self.microsoft_sign_in_button)
        return group

    def _request_microsoft_sign_in(self) -> None:
        self._microsoft_sign_in_requested = True
        self.microsoft_sign_in_button.setText("Reconnexion apres enregistrement des parametres")
        self.microsoft_sign_in_button.setEnabled(False)

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
            lambda: self._start_planner_action(
                self._load_planner_plans, self.planner_refresh_button
            ),
        )
        self.planner_test_button = QPushButton("Tester")
        self.planner_test_button.clicked.connect(
            lambda: self._start_planner_action(self._test_planner, self.planner_test_button),
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
            lambda: self._start_planner_action(
                self._load_planner_buckets,
                self.planner_bucket_refresh_button,
            ),
        )
        layout.addWidget(self.planner_bucket_combo, 1)
        layout.addWidget(self.planner_bucket_refresh_button)
        return widget

    def _planner_plan_changed(self) -> None:
        plan_id = self._selected_planner_plan_id()
        if plan_id != self._planner_bucket_plan_id:
            self.planner_bucket_combo.clear()
            self._planner_bucket_plan_id = plan_id

    def _start_planner_action(
        self,
        action: Callable[[], Awaitable[None]],
        button: QPushButton,
    ) -> None:
        if self._finished or self._planner_task is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._show_error("Planner", "Detection indisponible. Rouvrez les parametres.")
            return
        button_text = button.text()
        for control in self._planner_action_buttons():
            control.setEnabled(False)
        button.setText("Chargement...")
        self._planner_task = loop.create_task(self._run_planner_action(action, button, button_text))

    async def _run_planner_action(
        self,
        action: Callable[[], Awaitable[None]],
        button: QPushButton,
        button_text: str,
    ) -> None:
        try:
            await action()
        except Exception as exc:  # noqa: BLE001 - UI task boundary must consume failures.
            if not self._finished:
                self._show_error("Planner", str(exc) or "Operation Planner impossible.")
        finally:
            self._planner_task = None
            if not self._finished:
                button.setText(button_text)
                for control in self._planner_action_buttons():
                    control.setEnabled(True)

    def _planner_action_buttons(self) -> tuple[QPushButton, ...]:
        return (
            self.planner_refresh_button,
            self.planner_bucket_refresh_button,
            self.planner_test_button,
        )

    def _cancel_planner_action(self) -> None:
        self._finished = True
        if self._planner_task is not None and not self._planner_task.cancelling():
            self._planner_task.cancel()

    async def aclose(self) -> None:
        self._cancel_planner_action()
        if self._planner_task is not None:
            await asyncio.gather(self._planner_task, return_exceptions=True)

    def _show_error(self, title: str, message: str) -> None:
        if not self._finished:
            QMessageBox.warning(self, title, redact_sensitive_links(message))

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
            self._show_error("Outlook", str(exc))
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
            self._show_error("Outlook", str(exc))
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
            async with _planner_session() as client:
                plans = await client.list_plans()
                _refresh_combo(
                    self.planner_plan_combo,
                    [(plan.title, plan.id) for plan in plans],
                    selected_id=self._selected_planner_plan_id(),
                )
                self._planner_plan_changed()
                if self._selected_planner_plan_id():
                    await self._refresh_planner_buckets(client)
        except ProjectFlowError as exc:
            self._show_error("Planner", str(exc))

    async def _load_planner_buckets(self) -> None:
        try:
            async with _planner_session() as client:
                await self._refresh_planner_buckets(client)
        except ProjectFlowError as exc:
            self._show_error("Planner", str(exc))

    async def _refresh_planner_buckets(self, client: GraphPlannerClient) -> None:
        plan_id = self._selected_planner_plan_id()
        if not plan_id:
            QMessageBox.warning(self, "Planner", "Selectionnez d'abord un plan Planner.")
            return
        buckets = await client.list_buckets(plan_id=plan_id)
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
            async with _planner_session() as client:
                buckets = await client.list_buckets(plan_id=plan_id)
            bucket_ids = {bucket.id for bucket in buckets}
        except ProjectFlowError as exc:
            self._show_error("Planner", str(exc))
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


def _validation_message(error: ValidationError) -> str:
    messages = [str(item.get("msg", "")).removeprefix("Value error, ") for item in error.errors()]
    return "\n".join(message for message in messages if message) or str(error)


def _same_or_inside(path: Path, parent: Path) -> bool:
    candidate = Path(os.path.normcase(path.absolute()))
    base = Path(os.path.normcase(parent.absolute()))
    return candidate == base or base in candidate.parents


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


@asynccontextmanager
async def _planner_session() -> AsyncIterator[GraphPlannerClient]:
    client = _planner_client()
    try:
        yield client
    finally:
        await client.aclose()


def _planner_client() -> GraphPlannerClient:
    settings = ApplicationSettings.load()
    token_provider = MsalAccessTokenProvider(
        client_id=settings.microsoft_client_id,
        scopes=PLANNER_GRAPH_SCOPES,
    )
    return GraphPlannerClient(
        graph=GraphClient(token_provider=token_provider, request_timeout=60.0)
    )
