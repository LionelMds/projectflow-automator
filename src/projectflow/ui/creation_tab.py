from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True, slots=True)
class CreationFormData:
    year: str
    project_id: str
    subproject_id: str
    designation: str
    societe: str
    contact: str
    localisation: str
    gere_par: str
    planner_enabled: bool
    due_days: int


class CreationTab(QWidget):
    create_requested = Signal()
    update_requested = Signal()
    load_requested = Signal()
    open_fiche_requested = Signal()
    next_available_requested = Signal()
    settings_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._build_ui()

    def data(self) -> CreationFormData:
        return CreationFormData(
            year=self.year_combo.currentText().strip(),
            project_id=self.project_id_edit.text().strip(),
            subproject_id=self.subproject_edit.text().strip(),
            designation=self.designation_edit.text().strip(),
            societe=self.societe_edit.text().strip(),
            contact=self.contact_edit.text().strip(),
            localisation=self.localisation_edit.text().strip(),
            gere_par=self.gere_par_edit.text().strip(),
            planner_enabled=self.planner_checkbox.isChecked(),
            due_days=self.due_days_spin.value(),
        )

    def set_project_identity(self, *, year: str, project_id: str, subproject_id: str = "") -> None:
        index = self.year_combo.findText(year)
        if index >= 0:
            self.year_combo.setCurrentIndex(index)
        else:
            self.year_combo.addItem(year)
            self.year_combo.setCurrentText(year)
        self.project_id_edit.setText(project_id)
        self.subproject_edit.setText(subproject_id)

    def append_log(self, message: str) -> None:
        self.logs.append(message)

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(20, 16, 20, 16)
        root_layout.setSpacing(14)

        self.config_frame = QFrame()
        config_layout = QFormLayout(self.config_frame)
        self.racine_label = QLabel("Non configure")
        self.reference_label = QLabel("Non configure")
        self.repertoire_label = QLabel("Non configure")
        settings_button = QPushButton("Parametres")
        settings_button.clicked.connect(self.settings_requested.emit)
        config_layout.addRow("Racine projets", self.racine_label)
        config_layout.addRow("Dossier de reference", self.reference_label)
        config_layout.addRow("Repertoire chantier", self.repertoire_label)
        config_layout.addRow("", settings_button)
        root_layout.addWidget(self.config_frame)

        identity_frame = QFrame()
        identity_layout = QFormLayout(identity_frame)
        row = QHBoxLayout()
        self.year_combo = QComboBox()
        self.year_combo.setEditable(True)
        self.project_id_edit = QLineEdit()
        self.project_id_edit.setPlaceholderText("4995")
        self.subproject_edit = QLineEdit()
        self.subproject_edit.setPlaceholderText("Optionnel")
        self.next_button = QToolButton()
        self.next_button.setText("Suivant disponible")
        self.next_button.clicked.connect(self.next_available_requested.emit)
        row.addWidget(self.year_combo, 1)
        row.addWidget(self.project_id_edit, 2)
        row.addWidget(self.subproject_edit, 1)
        row.addWidget(self.next_button)
        identity_layout.addRow("Annee / ID / Sous-projet", row)
        self.designation_edit = QLineEdit()
        identity_layout.addRow("Designation", self.designation_edit)
        root_layout.addWidget(identity_frame)

        client_frame = QFrame()
        client_layout = QFormLayout(client_frame)
        self.societe_edit = QLineEdit()
        self.contact_edit = QLineEdit()
        self.localisation_edit = QLineEdit()
        self.gere_par_edit = QLineEdit()
        client_layout.addRow("Societe", self.societe_edit)
        client_layout.addRow("Contact", self.contact_edit)
        client_layout.addRow("Localisation", self.localisation_edit)
        client_layout.addRow("Gere par", self.gere_par_edit)
        root_layout.addWidget(client_frame)

        planner_frame = QFrame()
        planner_layout = QFormLayout(planner_frame)
        self.planner_checkbox = QCheckBox("Creer une tache Planner")
        self.plan_label = QLabel("Aucun plan selectionne")
        self.bucket_label = QLabel("Aucun bucket selectionne")
        self.due_days_spin = QSpinBox()
        self.due_days_spin.setRange(0, 365)
        self.due_days_spin.setValue(7)
        planner_layout.addRow("", self.planner_checkbox)
        planner_layout.addRow("Plan", self.plan_label)
        planner_layout.addRow("Bucket", self.bucket_label)
        planner_layout.addRow("Echeance (jours)", self.due_days_spin)
        root_layout.addWidget(planner_frame)

        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumHeight(120)
        root_layout.addWidget(self.logs)

        actions = QHBoxLayout()
        actions.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.load_button = QPushButton("Charger")
        self.open_button = QPushButton("Ouvrir fiche")
        self.create_button = QPushButton("Creer")
        self.update_button = QPushButton("Mettre a jour")
        self.create_button.setDefault(True)
        self.load_button.clicked.connect(self.load_requested.emit)
        self.open_button.clicked.connect(self.open_fiche_requested.emit)
        self.create_button.clicked.connect(self.create_requested.emit)
        self.update_button.clicked.connect(self.update_requested.emit)
        actions.addWidget(self.load_button)
        actions.addWidget(self.open_button)
        actions.addWidget(self.create_button)
        actions.addWidget(self.update_button)
        root_layout.addLayout(actions)
