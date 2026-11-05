from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final, Protocol, TypeVar

from pydantic import ValidationError
from PySide6.QtCore import QRectF, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QFont, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from projectflow import __version__
from projectflow.application_settings import ApplicationSettings
from projectflow.auth.msal_client import PLANNER_GRAPH_SCOPES, MsalAccessTokenProvider
from projectflow.cad.license_storage import SolidWorksLicenseStorage
from projectflow.cad.solidworks_app import SolidWorksReferenceReplacer
from projectflow.cad.templates import check_document_manager
from projectflow.config import (
    AppConfig,
    AppearanceConfig,
    CadConfig,
    CadPropertyNames,
    RepertoireChantierConfig,
)
from projectflow.core.planning.local_store import has_documents
from projectflow.core.planning.poste_config import (
    PlanningBackendName,
    PlanningColorBy,
    PlanningDensity,
    PlanningPosteConfig,
    PlanningView,
    default_poste_name,
)
from projectflow.exceptions import GraphError, ProjectFlowError
from projectflow.graph.client import GraphClient
from projectflow.graph.drive_planning_store import DEFAULT_ONEDRIVE_FOLDER, folder_segments
from projectflow.graph.planner import (
    GraphPlannerClient,
    is_planner_link,
    planner_plan_id_from_text,
)
from projectflow.graph.planning_store import DEFAULT_PLANNING_LIST_NAME
from projectflow.logging import redact_sensitive_links
from projectflow.outlook.local import detect_local_outlook_accounts, validate_local_outlook_account
from projectflow.platform.paths import native_path_text
from projectflow.ui.dialogs.planning_team import (
    INITIALS_EDIT_WIDTH,
    INITIALS_MAX_LENGTH,
    PlannerMember,
    SegmentedChoice,
    propose_initials,
)
from projectflow.ui.modal import ask_outside_task, show_outside_task
from projectflow.ui.theme import (
    HEADING_FAMILY,
    MODES,
    PALETTES,
    apply_theme,
    build_theme,
    current_theme,
)
from projectflow.ui.widgets.industry import (
    BlueprintFrame,
    PrimaryButton,
    StatusBanner,
    button,
    field,
    label,
    paint_corner_marks,
    rule,
)
from projectflow.ui.widgets.planner import (
    PlannerMemberOption,
    choose_planner_members,
    default_assignees,
)

MICROSOFT_SIGN_IN_TEXT = "Se reconnecter au compte Microsoft"
PLANNING_VIEWS: tuple[tuple[PlanningView, str], ...] = (
    ("day", "Jour"),
    ("week", "Semaine"),
    ("month", "Mois"),
)
PLANNING_DENSITIES: tuple[tuple[PlanningDensity, str], ...] = (
    ("compact", "Compacte"),
    ("normal", "Normale"),
    ("large", "Large"),
)
PLANNING_STORAGE_NAMES: Final[dict[PlanningBackendName, str]] = {
    "local": "Local",
    "onedrive": "OneDrive",
    "sharepoint": "SharePoint",
}
PLANNING_COLORS: tuple[tuple[PlanningColorBy, str], ...] = (
    ("type", "Par type de tâche"),
    ("dossier", "Par dossier"),
)
SHAREPOINT_CONSENT_TEXT: Final = (
    "Le planning partagé sur SharePoint demande l'autorisation Microsoft "
    "Sites.ReadWrite.All, accordée par un administrateur du compte Microsoft 365 : ce "
    "consentement administrateur est en attente. Le planning local de ce poste n'est pas "
    "copié automatiquement vers SharePoint."
)
ONEDRIVE_NOTE_TEXT: Final = (
    "Tous les postes connectés au même compte Microsoft voient ce planning. "
    "Aucune autorisation supplémentaire."
)
PLANNING_COPY_TEXT: Final = "Copier le planning local vers OneDrive…"
PLANNING_COPY_RUNNING_TEXT: Final = "Copie en cours…"
FOLDER_STRIP: Final = " /\\"
SHAREPOINT_URL_PREFIX: Final = "https://"

_Choice = TypeVar("_Choice", bound=str)


class LicenseKeyStorage(Protocol):
    def load(self) -> str:
        """Return the stored key, or an empty string."""

    def save(self, value: str) -> bool:
        """Store the key; return False when the credential store refused it."""

    def clear(self) -> bool:
        """Delete the key; return False when it could not be deleted."""


class SettingsDialog(QDialog):
    # Signing in again acts at once: it is not a setting waiting for OK.
    microsoft_sign_in_requested = Signal()
    reset_requested = Signal()
    # The shared roster is edited in its own dialog, applied at once, never on OK.
    planning_team_requested = Signal()
    # Copies this poste's local planning into the OneDrive folder given, at once.
    planning_copy_requested = Signal(str)

    def __init__(
        self,
        config: AppConfig,
        *,
        parent: QWidget | None = None,
        license_storage: LicenseKeyStorage | None = None,
        planning_config: PlanningPosteConfig | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Paramètres")
        self._license_storage = license_storage or SolidWorksLicenseStorage()
        self._license_clear_requested = False
        self._reconnect_repertoire = False
        self._planner_task: asyncio.Task[None] | None = None
        self._cad_test_task: asyncio.Task[None] | None = None
        self._finished = False
        self._planning_members: list[PlannerMember] = []
        self._build_ui(config, planning_config)
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
        plan_text = self.planner_plan_combo.currentText().strip()
        config.planner.plan_name = (
            config.planner.plan_id if is_planner_link(plan_text) else plan_text
        )
        config.planner.bucket_id = self._selected_planner_bucket_id()
        config.planner.bucket_name = self.planner_bucket_combo.currentText().strip()
        config.planner.due_days = self.planner_due_days_spin.value()
        config.planner.default_assignee_ids = [member.id for member in self._planner_members]
        config.planner.default_assignee_names = [member.label for member in self._planner_members]
        config.cad = self._cad_config()
        config.appearance = self.selected_appearance()

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
        if not (self._validate_planning() and self._validate_cad()):
            return
        if not self._store_license_key():
            return
        super().accept()

    def _validate_planning(self) -> bool:
        if not self._planning_available:
            return True
        if self.planning_onedrive_radio.isChecked():
            if self.planning_onedrive_edit.text().strip(FOLDER_STRIP):
                return True
            QMessageBox.warning(
                self,
                "Planning",
                "Indiquez le nom du dossier OneDrive du planning ou choisissez le stockage local.",
            )
            return False
        if not self.planning_sharepoint_radio.isChecked():
            return True
        site = self.planning_site_edit.text().strip()
        if not site:
            message = (
                "Indiquez l'adresse du site SharePoint du planning ou choisissez le stockage local."
            )
        elif not site.lower().startswith(SHAREPOINT_URL_PREFIX):
            message = "L'adresse du site SharePoint commence par https://."
        else:
            return True
        QMessageBox.warning(self, "Planning", message)
        return False

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

    def _build_ui(self, config: AppConfig, planning_config: PlanningPosteConfig | None) -> None:
        self.setMinimumSize(900, 600)
        self.resize(1080, 760)
        self._initial_appearance = config.appearance.model_copy()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        body = QHBoxLayout()
        body.setSpacing(0)

        user_group = QGroupBox("Utilisateur")
        user_layout = QFormLayout(user_group)
        self.user_initials_edit = QLineEdit(config.user.initials)
        self.user_initials_edit.setMaximumWidth(240)
        user_layout.addRow("Initiales utilisateur", self.user_initials_edit)
        user_layout.addRow(
            "",
            label(
                "Inscrites en C9 de chaque fiche. Le responsable saisi dans « Géré par » "
                "reste en C6.",
                "muted",
                wrap=True,
            ),
        )
        sections = [
            ("user", "Utilisateur", user_group),
            ("paths", "Chemins", self._paths_group(config)),
            ("outlook", "Outlook", self._outlook_group(config)),
            ("planner", "Microsoft Planner", self._planner_group(config)),
            ("cad", "Modèles CAO", self._cad_group(config)),
            ("planning", "Planning", self._planning_group(config, planning_config)),
            ("ms", "Compte Microsoft", self._microsoft_group()),
            ("appearance", "Apparence", self._appearance_group(config)),
        ]
        self._section_keys = tuple(key for key, _title, _group in sections)

        nav = QWidget()
        nav.setFixedWidth(230)
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(0, 22, 0, 22)
        nav_layout.setSpacing(0)
        heading = QVBoxLayout()
        heading.setContentsMargins(22, 0, 22, 14)
        heading.setSpacing(2)
        heading.addWidget(label("Configuration", "kicker"))
        heading.addWidget(label("Paramètres", "h3"))
        nav_layout.addLayout(heading)
        self.pages = QStackedWidget()
        self._section_buttons = QButtonGroup(self)
        self._section_buttons.setExclusive(True)
        self._section_meta: dict[str, QLabel] = {}
        for index, (key, title, group) in enumerate(sections):
            self.pages.addWidget(_settings_page(title, group))
            nav_button = button("", "side")
            nav_button.setCheckable(True)
            nav_button.setChecked(index == 0)
            row = QHBoxLayout(nav_button)
            row.setContentsMargins(19, 0, 16, 0)
            row.addWidget(QLabel(title))
            row.addStretch(1)
            meta = label("", "small")
            row.addWidget(meta)
            self._section_meta[key] = meta
            nav_button.setMinimumHeight(40)
            self._section_buttons.addButton(nav_button, index)
            nav_layout.addWidget(nav_button)
        self._section_buttons.idClicked.connect(self.pages.setCurrentIndex)
        nav_layout.addStretch(1)
        version = label(f"ProjectFlow {__version__}", "small")
        version.setContentsMargins(22, 0, 22, 0)
        nav_layout.addWidget(version)
        body.addWidget(nav)
        body.addWidget(rule(vertical=True))
        body.addWidget(self.pages, 1)
        root.addLayout(body, 1)
        root.addWidget(rule())

        footer = QHBoxLayout()
        footer.setContentsMargins(26, 6, 20, 6)
        footer.setSpacing(8)
        footer.addStretch(1)
        cancel_button = button("Annuler")
        cancel_button.clicked.connect(self.reject)
        save_button = PrimaryButton("Enregistrer")
        save_button.setDefault(True)
        save_button.clicked.connect(self.accept)
        footer.addWidget(cancel_button)
        footer.addWidget(save_button)
        root.addLayout(footer)

        self.user_initials_edit.textChanged.connect(self._refresh_section_meta)
        self.outlook_enabled_checkbox.toggled.connect(self._refresh_section_meta)
        self.planner_enabled_checkbox.toggled.connect(self._refresh_section_meta)
        self.planning_initials_edit.textChanged.connect(self._refresh_section_meta)
        self.planning_sharepoint_radio.toggled.connect(self._refresh_section_meta)
        self.planning_onedrive_radio.toggled.connect(self._refresh_section_meta)
        self._refresh_section_meta()

    def show_section(self, key: str) -> None:
        if key in self._section_keys:
            index = self._section_keys.index(key)
            self.pages.setCurrentIndex(index)
            section_button = self._section_buttons.button(index)
            if section_button is not None:
                section_button.setChecked(True)

    def _refresh_section_meta(self) -> None:
        self._section_meta["user"].setText(self.user_initials_edit.text().strip().upper())
        self._section_meta["outlook"].setText(
            "Actif" if self.outlook_enabled_checkbox.isChecked() else "Inactif",
        )
        self._section_meta["planner"].setText(
            "Actif" if self.planner_enabled_checkbox.isChecked() else "Inactif",
        )
        storage = PLANNING_STORAGE_NAMES[self.selected_planning_backend()]
        initials = self.planning_initials_edit.text().strip().upper()
        self._section_meta["planning"].setText(
            " · ".join(part for part in (initials, storage) if part)
            if self._planning_available
            else "",
        )
        self._section_meta["appearance"].setText(
            f"{MODES[self._appearance_mode]} · {PALETTES[self._appearance_palette][0]}",
        )

    # ------------------------------------------------------------- planning ----

    def apply_to_planning_config(self, config: PlanningPosteConfig) -> None:
        """Write this poste's planning settings into ``config`` (the roster is shared)."""
        config.user_initials = self.planning_initials_edit.text()
        config.user_name = " ".join(self.planning_name_edit.text().split())
        config.user_m365_id = self._planning_linked_id()
        config.poste_name = self.planning_poste_edit.text()
        site = self.planning_site_edit.text().strip()
        list_name = self.planning_list_edit.text().strip() or DEFAULT_PLANNING_LIST_NAME
        # Identifiers resolved for another site or list must be looked up again. SharePoint
        # ignores the case and a closing slash, so the same site keeps them.
        if _sharepoint_key(site) != _sharepoint_key(config.sharepoint_site_url):
            config.sharepoint_site_id = ""
            config.sharepoint_list_id = ""
        elif _sharepoint_key(list_name) != _sharepoint_key(config.sharepoint_list_name):
            config.sharepoint_list_id = ""
        config.sharepoint_site_url = site
        config.sharepoint_list_name = list_name
        config.backend = self.selected_planning_backend()
        config.onedrive_folder = self.planning_onedrive_edit.text()
        config.default_view = _choice(
            PLANNING_VIEWS,
            self.planning_view_choice.current(),
            config.default_view,
        )
        config.density = _choice(
            PLANNING_DENSITIES,
            self.planning_density_choice.current(),
            config.density,
        )
        config.color_by = _choice(
            PLANNING_COLORS,
            self.planning_color_choice.current(),
            config.color_by,
        )

    def set_planning_members(self, members: Sequence[PlannerMember]) -> None:
        """Offer the Microsoft 365 members (id, name, e-mail) in « Je suis… »."""
        self._planning_members = list(members)
        combo = self.planning_member_combo
        with QSignalBlocker(combo):
            combo.clear()
            for member_id, name, email in self._planning_members:
                combo.addItem(f"{name} ({email})" if email else name, member_id)
            linked = self._planning_linked_id()
            combo.setCurrentIndex(combo.findData(linked) if linked else -1)
        combo.setEnabled(self._planning_available and bool(self._planning_members))
        combo.setPlaceholderText(
            "Choisir dans la liste Microsoft 365"
            if self._planning_members
            else "Membres Microsoft 365 non chargés",
        )

    def _planning_group(
        self,
        config: AppConfig,
        planning: PlanningPosteConfig | None,
    ) -> QGroupBox:
        group = QGroupBox("Planning")
        layout = QVBoxLayout(group)
        layout.setSpacing(16)
        self._planning_available = planning is not None
        current = planning if planning is not None else PlanningPosteConfig()
        # The Microsoft 365 account and the name it goes with: the link holds while the
        # name field shows that name, so a typo corrected keeps it.
        self._planning_link = (current.user_m365_id, current.user_name)
        if planning is None:
            layout.addWidget(
                label("Le planning n'est pas disponible sur ce poste.", "muted", wrap=True),
            )
        controls = QWidget()
        column = QVBoxLayout(controls)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(16)
        self._planning_identity(column, config, current)
        self._planning_storage(column, current)
        column.addWidget(label("Équipe du planning", "h5"))
        team = BlueprintFrame(padding=(16, 12, 16, 12), layout="h", spacing=14)
        team.box.addWidget(
            label(
                "Personnes, sous-traitants, équipes et fermetures : registre partagé par "
                "tous les postes, modifié tout de suite.",
                wrap=True,
            ),
            1,
        )
        self.planning_team_button = button("Équipe du planning…", icon="settings")
        self.planning_team_button.clicked.connect(self.planning_team_requested.emit)
        team.box.addWidget(self.planning_team_button, 0, Qt.AlignmentFlag.AlignVCenter)
        column.addWidget(team)
        column.addWidget(label("Affichage par défaut", "h5"))
        self.planning_view_choice = SegmentedChoice(PLANNING_VIEWS, current.default_view)
        self.planning_density_choice = SegmentedChoice(PLANNING_DENSITIES, current.density)
        self.planning_color_choice = SegmentedChoice(PLANNING_COLORS, current.color_by)
        column.addWidget(field("Vue à l'ouverture", self.planning_view_choice))
        column.addWidget(field("Densité des lignes", self.planning_density_choice))
        column.addWidget(
            field(
                "Couleur des barres",
                self.planning_color_choice,
                help_text="Propre à ce poste, comme la vue et la densité.",
            ),
        )
        layout.addWidget(controls)
        controls.setEnabled(self._planning_available)
        self._planning_storage_changed()
        return group

    def _planning_identity(
        self,
        column: QVBoxLayout,
        config: AppConfig,
        current: PlanningPosteConfig,
    ) -> None:
        column.addWidget(label("Identité de ce poste", "h5"))
        column.addWidget(
            label(
                "Le compte Microsoft peut être partagé : chaque modification du planning est "
                "signée des initiales et du nom du poste, par exemple « LM · BUREAU-LM ».",
                "muted",
                wrap=True,
            ),
        )
        self.planning_member_combo = QComboBox()
        self.planning_member_combo.setPlaceholderText("Membres Microsoft 365 non chargés")
        self.planning_member_combo.setEnabled(False)
        self.planning_member_combo.currentIndexChanged.connect(self._planning_member_chosen)
        column.addWidget(
            field(
                "Je suis…",
                self.planning_member_combo,
                help_text="Remplit le nom et propose des initiales, qui restent modifiables.",
            ),
        )
        self.planning_initials_edit = QLineEdit(
            current.user_initials or config.user.initials.strip().upper(),
        )
        self.planning_initials_edit.setMaxLength(INITIALS_MAX_LENGTH)
        self.planning_initials_edit.setFixedWidth(INITIALS_EDIT_WIDTH)
        self.planning_initials_edit.setPlaceholderText("LM")
        self.planning_name_edit = QLineEdit(current.user_name)
        self.planning_name_edit.setPlaceholderText("Prénom Nom")
        self.planning_name_edit.textEdited.connect(self._planning_name_edited)
        identity = QHBoxLayout()
        identity.setSpacing(16)
        identity.addWidget(field("Initiales", self.planning_initials_edit))
        identity.addWidget(field("Nom", self.planning_name_edit), 1)
        column.addLayout(identity)
        self.planning_poste_edit = QLineEdit(current.poste_name)
        self.planning_poste_edit.setPlaceholderText(default_poste_name())
        column.addWidget(
            field(
                "Nom du poste",
                self.planning_poste_edit,
                help_text="Par défaut, le nom de l'ordinateur ; par exemple « BUREAU-LM » "
                "ou « ATELIER-1 ».",
            ),
        )

    def _planning_storage(self, column: QVBoxLayout, current: PlanningPosteConfig) -> None:
        column.addWidget(label("Stockage du planning", "h5"))
        self.planning_local_radio = QRadioButton("Local (ce poste uniquement)")
        self.planning_onedrive_radio = QRadioButton("OneDrive (partagé, recommandé)")
        self.planning_sharepoint_radio = QRadioButton(
            "SharePoint (autorisation administrateur requise)",
        )
        self._planning_storage_buttons = QButtonGroup(self)
        for radio in (
            self.planning_local_radio,
            self.planning_onedrive_radio,
            self.planning_sharepoint_radio,
        ):
            self._planning_storage_buttons.addButton(radio)
        self.planning_onedrive_radio.setChecked(current.backend == "onedrive")
        self.planning_sharepoint_radio.setChecked(current.backend == "sharepoint")
        self.planning_local_radio.setChecked(current.backend == "local")
        self._planning_local_directory = current.local_store_directory()
        column.addWidget(self.planning_local_radio)
        local_path = label(
            f"Dossier : {native_path_text(self._planning_local_directory)}",
            "small",
            wrap=True,
            selectable=True,
        )
        local_path.setContentsMargins(26, 0, 0, 0)
        column.addWidget(local_path)

        column.addWidget(self.planning_onedrive_radio)
        self.planning_onedrive_edit = QLineEdit(current.onedrive_folder)
        self.planning_onedrive_edit.setPlaceholderText(DEFAULT_ONEDRIVE_FOLDER)
        self.planning_onedrive_fields = QWidget()
        onedrive_fields = QVBoxLayout(self.planning_onedrive_fields)
        onedrive_fields.setContentsMargins(26, 0, 0, 0)
        onedrive_fields.setSpacing(8)
        onedrive_fields.addWidget(
            field(
                "Dossier OneDrive",
                self.planning_onedrive_edit,
                help_text="À la racine du OneDrive du compte connecté ; créé s'il n'existe pas.",
            ),
        )
        self.planning_onedrive_note = label(ONEDRIVE_NOTE_TEXT, "muted", wrap=True)
        onedrive_fields.addWidget(self.planning_onedrive_note)
        copy_row = QHBoxLayout()
        copy_row.setSpacing(12)
        self.planning_copy_button = button(PLANNING_COPY_TEXT)
        self.planning_copy_button.setToolTip(
            "Ajoute au dossier OneDrive les éléments du planning local de ce poste qu'il ne "
            "contient pas encore ; rien n'y est remplacé.",
        )
        self.planning_copy_button.clicked.connect(self._request_planning_copy)
        copy_row.addWidget(self.planning_copy_button)
        copy_row.addStretch(1)
        onedrive_fields.addLayout(copy_row)
        self.planning_copy_status = label("", "small", wrap=True)
        self.planning_copy_status.setVisible(False)
        onedrive_fields.addWidget(self.planning_copy_status)
        column.addWidget(self.planning_onedrive_fields)
        self._planning_has_local = has_documents(self._planning_local_directory)
        self._planning_copying = False

        column.addWidget(self.planning_sharepoint_radio)
        self.planning_site_edit = QLineEdit(current.sharepoint_site_url)
        self.planning_site_edit.setPlaceholderText("https://balzmetal.sharepoint.com/sites/…")
        self.planning_list_edit = QLineEdit(current.sharepoint_list_name)
        self.planning_list_edit.setPlaceholderText(DEFAULT_PLANNING_LIST_NAME)
        self.planning_sharepoint_fields = QWidget()
        sharepoint_fields = QVBoxLayout(self.planning_sharepoint_fields)
        sharepoint_fields.setContentsMargins(26, 0, 0, 0)
        sharepoint_fields.setSpacing(12)
        sharepoint_fields.addWidget(field("Site SharePoint", self.planning_site_edit))
        sharepoint_fields.addWidget(
            field(
                "Liste du planning",
                self.planning_list_edit,
                help_text="Créée sur le site au premier envoi si elle n'existe pas.",
            ),
        )
        column.addWidget(self.planning_sharepoint_fields)
        self.planning_storage_banner = StatusBanner()
        column.addWidget(self.planning_storage_banner)
        for radio in (self.planning_onedrive_radio, self.planning_sharepoint_radio):
            radio.toggled.connect(self._planning_storage_changed)
        self.planning_onedrive_edit.textChanged.connect(self._refresh_planning_copy)

    def selected_planning_backend(self) -> PlanningBackendName:
        if self.planning_onedrive_radio.isChecked():
            return "onedrive"
        if self.planning_sharepoint_radio.isChecked():
            return "sharepoint"
        return "local"

    def _planning_storage_changed(self) -> None:
        sharepoint = self.planning_sharepoint_radio.isChecked()
        self.planning_sharepoint_fields.setVisible(sharepoint)
        self.planning_onedrive_fields.setVisible(self.planning_onedrive_radio.isChecked())
        if sharepoint:
            self.planning_storage_banner.show_success(SHAREPOINT_CONSENT_TEXT)
            # A notice, not a success: the warning sign keeps the banner's accent tint.
            self.planning_storage_banner.icon_label.set_icon("alert", "accent-800")
        else:
            self.planning_storage_banner.clear()
        self._refresh_planning_copy()

    def _refresh_planning_copy(self) -> None:
        folder = self.planning_onedrive_edit.text().strip(FOLDER_STRIP)
        self.planning_copy_button.setEnabled(
            self._planning_available
            and self._planning_has_local
            and bool(folder)
            and not self._planning_copying,
        )

    def _request_planning_copy(self) -> None:
        if not self.planning_copy_button.isEnabled():
            return
        self._planning_copying = True
        self.planning_copy_button.setText(PLANNING_COPY_RUNNING_TEXT)
        self._refresh_planning_copy()
        self.planning_copy_status.setVisible(False)
        self.planning_copy_requested.emit(self.planning_onedrive_folder())

    def planning_onedrive_folder(self) -> str:
        """Return the OneDrive folder typed, as the planning settings keep it."""
        return "/".join(folder_segments(self.planning_onedrive_edit.text()))

    def planning_copy_finished(self, message: str, *, copied: bool) -> None:
        """Show how the copy went; the button can then be used again."""
        self._planning_copying = False
        self.planning_copy_button.setText(PLANNING_COPY_TEXT)
        self._refresh_planning_copy()
        text = message
        if copied and self.selected_planning_backend() == "onedrive":
            text = f"{message} Cliquez sur « Enregistrer » pour utiliser le planning OneDrive."
        self.planning_copy_status.setText(text)
        self.planning_copy_status.setVisible(bool(text))

    def _planning_member_chosen(self, index: int) -> None:
        if not 0 <= index < len(self._planning_members):
            return
        member_id, name, _email = self._planning_members[index]
        self._planning_link = (member_id, name)
        self.planning_name_edit.setText(name)
        self.planning_initials_edit.setText(propose_initials(name))

    def _planning_linked_id(self) -> str:
        """Microsoft 365 id of this poste's user, or "" once another name is typed."""
        member_id, name = self._planning_link
        if not member_id or _person_key(self.planning_name_edit.text()) != _person_key(name):
            return ""
        return member_id

    def _planning_name_edited(self, _text: str) -> None:
        # Another name means another person: the member chosen no longer applies.
        combo = self.planning_member_combo
        linked = self._planning_linked_id()
        with QSignalBlocker(combo):
            combo.setCurrentIndex(combo.findData(linked) if linked else -1)

    def _appearance_group(self, config: AppConfig) -> QGroupBox:
        group = QGroupBox("Apparence")
        layout = QVBoxLayout(group)
        layout.setSpacing(18)
        self._appearance_mode = config.appearance.mode
        self._appearance_palette = config.appearance.palette

        self.appearance_mode_buttons = QButtonGroup(self)
        self.appearance_mode_buttons.setExclusive(True)
        modes = QHBoxLayout()
        modes.setSpacing(-1)
        for key, text in MODES.items():
            mode_button = button(text, "seg")
            mode_button.setCheckable(True)
            mode_button.setChecked(key == self._appearance_mode)
            mode_button.setProperty("appearance_key", key)
            self.appearance_mode_buttons.addButton(mode_button)
            modes.addWidget(mode_button)
        modes.addStretch(1)
        self.appearance_mode_buttons.buttonClicked.connect(self._appearance_mode_clicked)
        layout.addWidget(
            field(
                "Mode",
                modes,
                help_text="« Système » suit le réglage clair / sombre de Windows ou macOS.",
            ),
        )

        self.appearance_palette_buttons = QButtonGroup(self)
        self.appearance_palette_buttons.setExclusive(True)
        palettes = QHBoxLayout()
        palettes.setSpacing(10)
        for key in PALETTES:
            swatch = PaletteSwatch(key)
            swatch.setChecked(key == self._appearance_palette)
            self.appearance_palette_buttons.addButton(swatch)
            palettes.addWidget(swatch)
        palettes.addStretch(1)
        self.appearance_palette_buttons.buttonClicked.connect(self._appearance_palette_clicked)
        layout.addWidget(
            field(
                "Palette de neutres",
                palettes,
                help_text="Teinte des fonds, bordures et textes secondaires. "
                "L'accent acier reste identique.",
            ),
        )
        layout.addWidget(
            label(
                "L'aperçu s'applique immédiatement ; Annuler rétablit l'apparence précédente.",
                "muted",
            ),
        )
        return group

    def _appearance_mode_clicked(self, clicked: QAbstractButton) -> None:
        key = str(clicked.property("appearance_key"))
        if key in MODES:
            self._appearance_mode = key  # type: ignore[assignment]
            self._preview_appearance()

    def _appearance_palette_clicked(self, clicked: QAbstractButton) -> None:
        if isinstance(clicked, PaletteSwatch):
            self._appearance_palette = clicked.palette_key  # type: ignore[assignment]
            self._preview_appearance()

    def selected_appearance(self) -> AppearanceConfig:
        return AppearanceConfig(mode=self._appearance_mode, palette=self._appearance_palette)

    def _preview_appearance(self) -> None:
        self._refresh_section_meta()
        app = QApplication.instance()
        if isinstance(app, QApplication) and app.styleSheet():
            apply_theme(app, self.selected_appearance())

    def reject(self) -> None:
        app = QApplication.instance()
        if (
            isinstance(app, QApplication)
            and app.styleSheet()
            and self.selected_appearance() != self._initial_appearance
        ):
            apply_theme(app, self._initial_appearance)
        super().reject()

    def _paths_group(self, config: AppConfig) -> QGroupBox:
        group = QGroupBox("Chemins")
        layout = QVBoxLayout(group)
        layout.setSpacing(16)
        self.racine_edit = QLineEdit(native_path_text(config.paths.racine_projets))
        self.reference_edit = QLineEdit(native_path_text(config.paths.dossier_reference))
        self.repertoire_path_edit = QLineEdit(
            native_path_text(config.paths.repertoire_chantier.display_path),
        )
        self.repertoire_path_edit.setPlaceholderText("Chemin Excel ou lien OneDrive / SharePoint")
        layout.addWidget(
            field(
                "Racine projets",
                _browse_row(self.racine_edit, directory=True),
                help_text="Les dossiers projet sont créés sous Racine\\Année.",
            ),
        )
        layout.addWidget(
            field(
                "Dossier de référence",
                _browse_row(self.reference_edit, directory=True),
                help_text="Copié sans rien écraser à chaque création.",
            ),
        )
        layout.addWidget(
            field(
                "Répertoire chantier",
                _browse_row(self.repertoire_path_edit, directory=False),
                help_text="Chemin Excel ou lien OneDrive / SharePoint — "
                "utilisé pour les écritures.",
            ),
        )
        self.repertoire_open_path_edit = QLineEdit(
            native_path_text(config.paths.repertoire_chantier.open_path),
        )
        self.repertoire_open_path_edit.setPlaceholderText("Facultatif : fichier Excel sur ce poste")
        self.repertoire_open_path_edit.setToolTip(
            "Fichier synchronise a ouvrir dans Excel. "
            "Le repertoire chantier ci-dessus reste utilise pour les modifications partagees.",
        )
        layout.addWidget(
            field(
                "Fichier synchronisé pour ouverture Excel",
                _browse_row(self.repertoire_open_path_edit, directory=False),
                help_text="Ouvert par « Ouvrir répertoire » ; les modifications passent par "
                "le lien cloud.",
            ),
        )
        self.repertoire_cloud_checkbox = QCheckBox("Répertoire partagé OneDrive / SharePoint")
        self.repertoire_cloud_checkbox.setChecked(
            config.paths.repertoire_chantier.cloud_only
            or config.paths.repertoire_chantier.is_configured,
        )
        self.repertoire_cloud_checkbox.setToolTip(
            "Impose la connexion cloud meme si le dossier synchronise n'est pas reconnu. "
            "Les dossiers OneDrive et SharePoint detectes utilisent toujours le cloud.",
        )
        self.repertoire_reconnect_button = button("Reconnecter")
        self.repertoire_reconnect_button.setToolTip("Reconnecter à OneDrive / SharePoint")
        self.repertoire_reconnect_button.clicked.connect(self._request_repertoire_reconnection)
        cloud_tag = label("Écriture cloud active", "tag-accent")
        self.repertoire_cloud_checkbox.toggled.connect(cloud_tag.setVisible)
        cloud_tag.setVisible(self.repertoire_cloud_checkbox.isChecked())
        cloud = BlueprintFrame(padding=(16, 12, 16, 12), layout="h", spacing=14)
        cloud.box.addWidget(self.repertoire_cloud_checkbox, 1)
        cloud.box.addWidget(cloud_tag)
        cloud.box.addWidget(self.repertoire_reconnect_button)
        layout.addWidget(cloud)
        return group

    def _cad_group(self, config: AppConfig) -> QGroupBox:
        group = QGroupBox("Modèles CAO")
        layout = QFormLayout(group)
        self.cad_solidworks_edit = QLineEdit(native_path_text(config.cad.solidworks_template_dir))
        self.cad_solidworks_edit.setPlaceholderText("Dossier distinct du dossier de reference")
        self.cad_autocad_edit = QLineEdit(native_path_text(config.cad.autocad_template_dir))
        self.cad_subfolder_edit = QLineEdit(config.cad.destination_subfolder.replace("/", os.sep))
        self.cad_subfolder_edit.setPlaceholderText("Vide = racine du dossier projet")
        self.solidworks_license_edit = QLineEdit()
        self.solidworks_license_edit.setEchoMode(QLineEdit.EchoMode.Password)
        has_key = bool(self._license_storage.load())
        self.solidworks_license_edit.setPlaceholderText(
            "Cle enregistree - laisser vide pour la conserver" if has_key else "Aucune cle",
        )
        self.solidworks_license_clear_button = QPushButton("Effacer la clé")
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
            "Dossier modèle SolidWorks",
            _browse_row(self.cad_solidworks_edit, directory=True),
        )
        layout.addRow("Dossier modèle AutoCAD", _browse_row(self.cad_autocad_edit, directory=True))
        layout.addRow("Sous-dossier dans le projet", self.cad_subfolder_edit)
        layout.addRow("Clé Document Manager", license_row)

        self.cad_property_edits: dict[str, QLineEdit] = {}
        properties = QWidget()
        properties_layout = QGridLayout(properties)
        properties_layout.setContentsMargins(0, 0, 0, 0)
        for index, (property_field, caption) in enumerate(
            [
                ("projet", "Projet"),
                ("client", "Client"),
                ("auteur", "Auteur"),
                ("description", "Description"),
                ("revision", "Révision"),
                ("revision_defaut", "Rév. par défaut"),
            ]
        ):
            edit = QLineEdit(getattr(config.cad.properties, property_field))
            edit.setToolTip(f"Nom exact de la propriete SolidWorks ({caption}), accents compris.")
            self.cad_property_edits[property_field] = edit
            edit.setMinimumWidth(edit.fontMetrics().horizontalAdvance("M" * 10) + 12)
            row, column = divmod(index, 2)
            properties_layout.addWidget(QLabel(caption), row, column * 2)
            properties_layout.addWidget(edit, row, column * 2 + 1)
        properties_layout.setColumnStretch(1, 1)
        properties_layout.setColumnStretch(3, 1)
        layout.addRow("Propriétés SolidWorks", properties)
        return group

    def _test_document_manager(self) -> None:
        if self._finished or self._cad_test_task is not None:
            return
        key = self.solidworks_license_edit.text().strip()
        if not key and not self._license_clear_requested:
            key = self._license_storage.load()
        template_dir = _optional_path(self.cad_solidworks_edit.text())
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._show_document_manager_result(*_document_manager_check(key, template_dir))
            return
        # The test copies the templates and drives COM: keep the window responsive.
        self.solidworks_license_test_button.setEnabled(False)
        self.solidworks_license_test_button.setText("Test en cours...")
        self._cad_test_task = loop.create_task(self._run_document_manager_test(key, template_dir))

    async def _run_document_manager_test(self, key: str, template_dir: Path | None) -> None:
        try:
            succeeded, message = await asyncio.to_thread(
                _document_manager_check,
                key,
                template_dir,
            )
        finally:
            self._cad_test_task = None
            if not self._finished:
                self.solidworks_license_test_button.setText("Tester")
                self.solidworks_license_test_button.setEnabled(True)
        if not self._finished:
            self._show_document_manager_result(succeeded, message)

    def _show_document_manager_result(self, succeeded: bool, message: str) -> None:  # noqa: FBT001
        if succeeded:
            self._show_dialog(lambda: QMessageBox.information(self, "Document Manager", message))
        else:
            self._show_error("Document Manager", message)

    def _request_license_clear(self) -> None:
        self._license_clear_requested = True
        self.solidworks_license_edit.clear()
        self.solidworks_license_edit.setPlaceholderText("Cle effacee apres enregistrement")
        self.solidworks_license_clear_button.setEnabled(False)

    def _microsoft_group(self) -> QGroupBox:
        group = QGroupBox("Compte Microsoft")
        layout = QFormLayout(group)
        self.microsoft_sign_in_button = QPushButton(MICROSOFT_SIGN_IN_TEXT)
        self.microsoft_sign_in_button.setToolTip(
            "Ouvre tout de suite la connexion Microsoft dans le navigateur pour choisir "
            "le compte. Le compte actuel reste utilise si la connexion est annulee.",
        )
        self.microsoft_sign_in_button.clicked.connect(self._request_microsoft_sign_in)
        self.microsoft_status_label = label("", "muted", wrap=True)
        self.reset_button = button("Tout réinitialiser ProjectFlow…", "danger")
        self.reset_button.setToolTip(
            "Efface la connexion Microsoft, tous les parametres et la cle Document Manager, "
            "puis redemarre ProjectFlow comme a la premiere installation.",
        )
        self.reset_button.clicked.connect(self.reset_requested.emit)
        layout.addRow("", self.microsoft_sign_in_button)
        layout.addRow("", self.microsoft_status_label)
        layout.addRow("", self.reset_button)
        return group

    def _request_microsoft_sign_in(self) -> None:
        self.microsoft_sign_in_button.setText("Connexion en cours...")
        self.microsoft_sign_in_button.setEnabled(False)
        self.microsoft_status_label.setText(
            "Choisissez le compte dans la page Microsoft ouverte dans le navigateur.",
        )
        self.microsoft_sign_in_requested.emit()

    def microsoft_sign_in_finished(self, message: str) -> None:
        """Show the outcome; nothing needs saving afterwards."""
        self.microsoft_sign_in_button.setText(MICROSOFT_SIGN_IN_TEXT)
        self.microsoft_sign_in_button.setEnabled(True)
        self.microsoft_status_label.setText(redact_sensitive_links(message))

    def _request_repertoire_reconnection(self) -> None:
        self._reconnect_repertoire = True
        self.repertoire_cloud_checkbox.setChecked(True)
        self.repertoire_reconnect_button.setText("Reconnexion apres enregistrement des parametres")
        self.repertoire_reconnect_button.setEnabled(False)

    def _planner_group(self, config: AppConfig) -> QGroupBox:
        group = QGroupBox("Microsoft Planner")
        layout = QFormLayout(group)
        self.planner_enabled_checkbox = QCheckBox("Créer une tâche Planner")
        self.planner_enabled_checkbox.setChecked(config.planner.enabled)
        self.planner_plan_combo = QComboBox()
        self.planner_plan_combo.setEditable(True)
        self.planner_plan_combo.setPlaceholderText("plan Planner ou lien du plan")
        self.planner_plan_combo.setToolTip(
            "Si le plan n'est pas detecte, ouvrez-le dans Planner (navigateur), "
            "copiez l'adresse de la page, collez-la ici puis cliquez sur Detecter.",
        )
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
        self._planner_members = default_assignees(config.planner)
        layout.addRow("", self.planner_enabled_checkbox)
        layout.addRow("Plan", self._planner_plan_row())
        layout.addRow("Colonne", self._planner_bucket_row())
        layout.addRow("Membres par défaut", self._planner_members_row())
        layout.addRow("Échéance par défaut", self.planner_due_days_spin)
        return group

    def _planner_members_row(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        self.planner_members_label = QLabel()
        self.planner_members_label.setToolTip(
            "Assignes a chaque nouvelle tache Planner. Sans choix, la tache est assignee "
            "au compte Microsoft connecte, qui peut etre partage entre plusieurs postes.",
        )
        self.planner_members_button = QPushButton("Choisir")
        self.planner_members_button.clicked.connect(
            lambda: self._start_planner_action(
                self._choose_planner_members,
                self.planner_members_button,
            ),
        )
        layout.addWidget(self.planner_members_label, 1)
        layout.addWidget(self.planner_members_button)
        self._refresh_planner_members_label()
        return widget

    def _refresh_planner_members_label(self) -> None:
        names = ", ".join(member.label for member in self._planner_members)
        self.planner_members_label.setText(names or "Compte Microsoft connecté")

    async def _choose_planner_members(self) -> None:
        plan_id = self._selected_planner_plan_id()
        if not plan_id:
            self._show_error("Planner", "Selectionnez d'abord un plan Planner.")
            return
        async with _planner_session() as client:
            members = await client.list_members(plan_id=plan_id)
        options = [PlannerMemberOption(id=member.id, label=member.label) for member in members]
        chosen = await ask_outside_task(
            lambda: choose_planner_members(
                self,
                options,
                [member.id for member in self._planner_members],
            ),
            skip=lambda: self._finished,
        )
        if chosen is None or plan_id != self._selected_planner_plan_id():
            return
        self._planner_members = tuple(option for option in options if option.id in chosen)
        self._refresh_planner_members_label()

    def _planner_plan_row(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        self.planner_refresh_button = QPushButton("Détecter")
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
        self.planner_bucket_refresh_button = QPushButton("Détecter colonnes")
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
            # Members belong to the plan's group: another plan has other members.
            self._planner_members = ()
            self._refresh_planner_members_label()

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
            self.planner_members_button,
        )

    def _cancel_planner_action(self) -> None:
        self._finished = True
        if self._planner_task is not None and not self._planner_task.cancelling():
            self._planner_task.cancel()

    async def aclose(self) -> None:
        self._cancel_planner_action()
        # A running Document Manager test cannot be interrupted: let its thread finish.
        pending = [task for task in (self._planner_task, self._cad_test_task) if task is not None]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _show_error(self, title: str, message: str) -> None:
        text = redact_sensitive_links(message)
        self._show_dialog(lambda: QMessageBox.warning(self, title, text))

    def _show_dialog(self, show: Callable[[], object]) -> None:
        # Never inside a task step: the modal loop would stall every other task.
        show_outside_task(show, skip=lambda: self._finished)

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
        self.outlook_base_folder_combo.addItem("Boîte de réception", "inbox")
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
        self.outlook_refresh_button = QPushButton("Détecter")
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
            plan_text = self.planner_plan_combo.currentText().strip()
            linked_plan_id = ""
            if is_planner_link(plan_text):
                linked_plan_id = planner_plan_id_from_text(plan_text)
            async with _planner_session() as client:
                if linked_plan_id:
                    linked_plan = await client.get_plan(plan_id=linked_plan_id)
                    try:
                        plans = await client.list_plans()
                    except GraphError:
                        plans = []
                    if all(plan.id != linked_plan.id for plan in plans):
                        plans = [linked_plan, *plans]
                    selected_id = linked_plan.id
                else:
                    plans = await client.list_plans()
                    selected_id = self._selected_planner_plan_id()
                _refresh_combo(
                    self.planner_plan_combo,
                    [(plan.title, plan.id) for plan in plans],
                    selected_id=selected_id,
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
            self._show_error("Planner", "Selectionnez d'abord un plan Planner.")
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
            self._show_error("Planner", "Selectionnez un plan et une colonne Planner.")
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
            self._show_error("Planner", "La colonne selectionnee est introuvable.")
            return
        self.planner_enabled_checkbox.setChecked(True)
        message = "Plan et colonne Planner accessibles."
        self._show_dialog(lambda: QMessageBox.information(self, "Planner", message))

    def _selected_planner_plan_id(self) -> str:
        selected = _selected_combo_id(self.planner_plan_combo)
        if not is_planner_link(selected):
            return selected
        try:
            return planner_plan_id_from_text(selected)
        except ProjectFlowError:
            return ""

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


def _document_manager_check(key: str, template_dir: Path | None) -> tuple[bool, str]:
    """Run the Document Manager test; the result is shown by the dialog thread."""
    try:
        return True, check_document_manager(
            key,
            template_dir,
            reference_replacer=SolidWorksReferenceReplacer(),
        )
    except ProjectFlowError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001 - the test reports any failure to the user
        return False, f"{type(exc).__name__}: {exc}"


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


def _choice(options: Sequence[tuple[_Choice, str]], key: str, default: _Choice) -> _Choice:
    """Return the option whose value is ``key``, else ``default``."""
    return next((value for value, _text in options if value == key), default)


def _sharepoint_key(text: str) -> str:
    # As the planning configuration compares a site address or a list name.
    return text.strip().rstrip("/").casefold()


def _person_key(name: str) -> str:
    return " ".join(name.split()).casefold()


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


class PaletteSwatch(QPushButton):
    """Checkable card previewing one neutral palette in the current mode."""

    def __init__(self, palette_key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.palette_key = palette_key
        self.setCheckable(True)
        self.setProperty("variant", "swatch")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(132, 78)
        self.setToolTip(PALETTES[palette_key][0])

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        theme = current_theme()
        preview = build_theme(theme.mode, self.palette_key)
        painter = QPainter(self)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.fillRect(rect, preview.color("bg"))
        band = QRectF(rect.left() + 10, rect.top() + 10, rect.width() - 20, 22)
        painter.fillRect(band, preview.color("surface"))
        for step, offset in (("neutral-400", 0), ("neutral-600", 18), ("neutral-800", 36)):
            painter.fillRect(
                QRectF(band.left() + 6 + offset, band.top() + 6, 12, 10), preview.color(step)
            )
        painter.fillRect(QRectF(band.right() - 26, band.top() + 6, 20, 10), preview.color("accent"))
        font = QFont(HEADING_FAMILY)
        font.setPixelSize(15)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(preview.color("text"))
        painter.drawText(
            QRectF(rect.left() + 10, band.bottom() + 4, rect.width() - 20, 30),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            PALETTES[self.palette_key][0],
        )
        checked = self.isChecked()
        pen = QPen(theme.color("accent") if checked else theme.color("text", 0.30))
        pen.setWidthF(2.0 if checked else 1.0)
        painter.setPen(pen)
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5) if checked else rect)
        if checked:
            paint_corner_marks(painter, rect.adjusted(5, 5, -5, -5))
        painter.end()


def _settings_page(title: str, group: QGroupBox) -> QScrollArea:
    """Lay a settings group out as a page: heading, then captions stacked over fields."""
    group.setTitle("")
    group.setFlat(True)
    form = group.layout()
    if isinstance(form, QFormLayout):
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(8)
        for row in range(form.rowCount()):
            item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            caption = item.widget() if item is not None else None
            if isinstance(caption, QLabel):
                if caption.text():
                    caption.setProperty("role", "field")
                else:
                    caption.hide()
    else:
        layout = group.layout()
        if layout is not None:
            layout.setContentsMargins(0, 0, 0, 0)
    content = QWidget()
    content.setMaximumWidth(820)
    column = QVBoxLayout(content)
    column.setContentsMargins(34, 26, 34, 26)
    column.setSpacing(18)
    column.addWidget(label(title, "h3"))
    column.addWidget(group)
    column.addStretch(1)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setWidget(content)
    return scroll
