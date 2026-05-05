from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from projectflow.config import AppConfig, RepertoireChantierConfig, UserConfig
from projectflow.exceptions import ProjectFlowError
from projectflow.platform.paths import detect_onedrive_balz_root


class UserProfileLike(Protocol):
    def to_config(self) -> UserConfig:
        """Return the user config representation."""


class PlannerPlanLike(Protocol):
    @property
    def id(self) -> str:
        """Planner plan id."""

    @property
    def title(self) -> str:
        """Planner plan title."""


class PlannerBucketLike(Protocol):
    @property
    def id(self) -> str:
        """Planner bucket id."""

    @property
    def name(self) -> str:
        """Planner bucket name."""

    @property
    def plan_id(self) -> str:
        """Parent plan id."""


class OnboardingMicrosoftService(Protocol):
    def connect_user(self) -> UserProfileLike:
        """Authenticate and return a profile object exposing to_config()."""

    def resolve_repertoire(self, display_path: str) -> RepertoireChantierConfig:
        """Resolve a OneDrive display path to Graph drive/item ids."""

    def list_plans(self) -> Sequence[PlannerPlanLike]:
        """Return Planner plans accessible to the signed-in user."""

    def list_buckets(self, plan_id: str) -> Sequence[PlannerBucketLike]:
        """Return buckets for a Planner plan."""


class OnboardingWizard(QWizard):
    def __init__(
        self,
        config: AppConfig,
        *,
        microsoft_service: OnboardingMicrosoftService | None = None,
    ) -> None:
        super().__init__()
        self.config = config.model_copy(deep=True)
        self._microsoft_service = microsoft_service
        self.demo_requested = False
        self.setWindowTitle("Bienvenue dans ProjectFlow")
        self.addPage(WelcomePage())
        self.microsoft_page = MicrosoftPage(self.config, microsoft_service=microsoft_service)
        self.microsoft_page.demo_requested.connect(self._request_demo)
        self.paths_page = PathsPage(self.config)
        self.planner_page = PlannerPage(self.config, microsoft_service=microsoft_service)
        self.addPage(self.microsoft_page)
        self.addPage(self.paths_page)
        self.addPage(self.planner_page)

    def accept(self) -> None:
        try:
            self.paths_page.apply_to_config()
            if self._microsoft_service is not None:
                self.config.paths.repertoire_chantier = (
                    self._microsoft_service.resolve_repertoire(
                        self.config.paths.repertoire_chantier.display_path,
                    )
                )
            self.planner_page.apply_to_config()
        except ProjectFlowError as exc:
            QMessageBox.critical(self, "Configuration incomplete", str(exc))
            return
        super().accept()

    def _request_demo(self) -> None:
        self.demo_requested = True
        super().accept()


class WelcomePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Bienvenue")
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "ProjectFlow cree les dossiers, fiches, entrees Outlook, "
                "lignes de repertoire et taches Planner depuis un seul formulaire.",
            ),
        )


class MicrosoftPage(QWizardPage):
    demo_requested = Signal()

    def __init__(
        self,
        config: AppConfig,
        *,
        microsoft_service: OnboardingMicrosoftService | None,
    ) -> None:
        super().__init__()
        self._config = config
        self._microsoft_service = microsoft_service
        self.setTitle("Connexion Microsoft")
        layout = QVBoxLayout(self)
        self.status_label = QLabel("Non connecte")
        self.login_button = QPushButton("Se connecter a Microsoft")
        self.login_button.clicked.connect(self._connect)
        self.demo_button = QPushButton("Continuer en mode demo")
        self.demo_button.clicked.connect(self.demo_requested.emit)
        if microsoft_service is None:
            self.status_label.setText(
                "Client ID Microsoft non configure. "
                "Le mode demo permet de tester l'application localement.",
            )
            self.login_button.setEnabled(False)
        else:
            self.demo_button.setVisible(False)
        layout.addWidget(self.status_label)
        layout.addWidget(self.login_button)
        layout.addWidget(self.demo_button)

    def _connect(self) -> None:
        if self._microsoft_service is None:
            return
        try:
            profile = self._microsoft_service.connect_user()
            user_config = profile.to_config()
        except ProjectFlowError as exc:
            QMessageBox.critical(self, "Connexion impossible", str(exc))
            return
        self._config.user = user_config
        self.status_label.setText(f"Connecte - {user_config.email}")
        self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802
        return bool(self._config.user.user_id)


class PathsPage(QWizardPage):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self.setTitle("Configuration des chemins")
        onedrive_root = detect_onedrive_balz_root()
        default_clients = onedrive_root / "Clients" if onedrive_root else Path.home()
        default_reference = (
            onedrive_root / "Modeles" / "10-Racine" if onedrive_root else Path.home()
        )

        layout = QFormLayout(self)
        self.racine_edit = QLineEdit(str(config.paths.racine_projets or default_clients))
        self.reference_edit = QLineEdit(str(config.paths.dossier_reference or default_reference))
        self.repertoire_edit = QLineEdit(config.paths.repertoire_chantier.display_path)
        layout.addRow("Racine projets", _browse_row(self.racine_edit, directory=True))
        layout.addRow("Dossier de reference", _browse_row(self.reference_edit, directory=True))
        layout.addRow("Repertoire chantier", _browse_row(self.repertoire_edit, directory=False))

    def apply_to_config(self) -> None:
        self._config.paths.racine_projets = Path(self.racine_edit.text()).expanduser()
        self._config.paths.dossier_reference = Path(self.reference_edit.text()).expanduser()
        self._config.paths.repertoire_chantier.display_path = self.repertoire_edit.text().strip()


class PlannerPage(QWizardPage):
    def __init__(
        self,
        config: AppConfig,
        *,
        microsoft_service: OnboardingMicrosoftService | None,
    ) -> None:
        super().__init__()
        self._config = config
        self._microsoft_service = microsoft_service
        self.setTitle("Configuration Planner")
        layout = QFormLayout(self)
        self.enabled_checkbox = QCheckBox("Creer une tache Planner")
        self.enabled_checkbox.setChecked(config.planner.enabled)
        self.plan_combo = QComboBox()
        self.bucket_combo = QComboBox()
        self.due_days_spin = QSpinBox()
        self.due_days_spin.setRange(0, 365)
        self.due_days_spin.setValue(config.planner.due_days)
        self.refresh_button = QPushButton("Charger les plans")
        self.refresh_button.clicked.connect(self.load_plans)
        self.plan_combo.currentIndexChanged.connect(self._load_buckets_for_current_plan)
        if config.planner.plan_id:
            self.plan_combo.addItem(config.planner.plan_name, config.planner.plan_id)
        if config.planner.bucket_id:
            self.bucket_combo.addItem(config.planner.bucket_name, config.planner.bucket_id)
        if microsoft_service is None:
            self.refresh_button.setEnabled(False)
            self.plan_combo.setEditable(True)
            self.bucket_combo.setEditable(True)
        layout.addRow("", self.enabled_checkbox)
        layout.addRow("Plan", self.plan_combo)
        layout.addRow("Bucket", self.bucket_combo)
        layout.addRow("Echeance (jours)", self.due_days_spin)
        layout.addRow("", self.refresh_button)

    def load_plans(self) -> None:
        if self._microsoft_service is None:
            return
        try:
            plans = self._microsoft_service.list_plans()
        except ProjectFlowError as exc:
            QMessageBox.critical(self, "Planner", str(exc))
            return
        current_plan_id = self.current_plan_id()
        self.plan_combo.clear()
        for plan in plans:
            self.plan_combo.addItem(plan.title, plan.id)
        if current_plan_id:
            index = self.plan_combo.findData(current_plan_id)
            if index >= 0:
                self.plan_combo.setCurrentIndex(index)
        self._load_buckets_for_current_plan()

    def apply_to_config(self) -> None:
        self._config.planner.enabled = self.enabled_checkbox.isChecked()
        self._config.planner.plan_id = self.current_plan_id()
        self._config.planner.plan_name = self.plan_combo.currentText().strip()
        self._config.planner.bucket_id = self.current_bucket_id()
        self._config.planner.bucket_name = self.bucket_combo.currentText().strip()
        self._config.planner.due_days = self.due_days_spin.value()

    def current_plan_id(self) -> str:
        data = self.plan_combo.currentData()
        return data if isinstance(data, str) else self.plan_combo.currentText().strip()

    def current_bucket_id(self) -> str:
        data = self.bucket_combo.currentData()
        return data if isinstance(data, str) else self.bucket_combo.currentText().strip()

    def _load_buckets_for_current_plan(self) -> None:
        if self._microsoft_service is None:
            return
        plan_id = self.current_plan_id()
        if not plan_id:
            self.bucket_combo.clear()
            return
        try:
            buckets = self._microsoft_service.list_buckets(plan_id)
        except ProjectFlowError as exc:
            QMessageBox.critical(self, "Planner", str(exc))
            return
        current_bucket_id = self.current_bucket_id()
        self.bucket_combo.clear()
        for bucket in buckets:
            self.bucket_combo.addItem(bucket.name, bucket.id)
        if current_bucket_id:
            index = self.bucket_combo.findData(current_bucket_id)
            if index >= 0:
                self.bucket_combo.setCurrentIndex(index)


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
