from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from projectflow.config import AppConfig
from projectflow.platform.paths import detect_onedrive_balz_root


class OnboardingWizard(QWizard):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config.model_copy(deep=True)
        self.setWindowTitle("Bienvenue dans ProjectFlow")
        self.addPage(WelcomePage())
        self.paths_page = PathsPage(self.config)
        self.addPage(self.paths_page)

    def accept(self) -> None:
        self.paths_page.apply_to_config()
        super().accept()


class WelcomePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Bienvenue")
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "ProjectFlow cree les dossiers, fiches et lignes de repertoire "
                "depuis un seul formulaire.",
            ),
        )


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
        self._config.paths.repertoire_chantier.drive_id = ""
        self._config.paths.repertoire_chantier.item_id = ""


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
