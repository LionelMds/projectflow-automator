from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTextEdit,
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

    def reset_form_fields(self) -> None:
        for edit in [
            self.project_id_edit,
            self.subproject_edit,
            self.designation_edit,
            self.societe_edit,
            self.contact_edit,
            self.localisation_edit,
            self.gere_par_edit,
        ]:
            edit.clear()

    def _build_ui(self) -> None:
        self.setMinimumWidth(760)
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(20, 16, 20, 16)
        root_layout.setSpacing(14)

        self.config_frame = QFrame()
        config_layout = QFormLayout(self.config_frame)
        _configure_form_layout(config_layout)
        self.racine_label = QLabel("Non configure")
        self.reference_label = QLabel("Non configure")
        self.repertoire_label = QLabel("Non configure")
        for label in [self.racine_label, self.reference_label, self.repertoire_label]:
            _configure_value_label(label)
        settings_button = QPushButton("Parametres")
        settings_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        settings_button.clicked.connect(self.settings_requested.emit)
        config_layout.addRow("Racine projets", self.racine_label)
        config_layout.addRow("Dossier de reference", self.reference_label)
        config_layout.addRow("Repertoire chantier", self.repertoire_label)
        config_layout.addRow("", settings_button)
        root_layout.addWidget(self.config_frame)

        identity_frame = QFrame()
        identity_layout = QFormLayout(identity_frame)
        _configure_form_layout(identity_layout)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.year_combo = QComboBox()
        self.year_combo.setEditable(True)
        _configure_text_control(self.year_combo, min_chars=6)
        self.project_id_edit = QLineEdit()
        self.project_id_edit.setPlaceholderText("4995")
        _configure_text_control(self.project_id_edit, min_chars=12)
        self.subproject_edit = QLineEdit()
        self.subproject_edit.setPlaceholderText("Optionnel")
        _configure_text_control(self.subproject_edit, min_chars=12)
        self.next_button = QPushButton("Suivant disponible")
        self.next_button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.next_button.setText("Suivant disponible")
        self.next_button.clicked.connect(self.next_available_requested.emit)
        row.addWidget(self.year_combo, 1)
        row.addWidget(self.project_id_edit, 3)
        row.addWidget(self.subproject_edit, 1)
        row.addWidget(self.next_button)
        identity_layout.addRow("Annee / ID / Sous-projet", row)
        self.designation_edit = QLineEdit()
        identity_layout.addRow("Designation", self.designation_edit)
        root_layout.addWidget(identity_frame)

        client_frame = QFrame()
        client_layout = QFormLayout(client_frame)
        _configure_form_layout(client_layout)
        self.societe_edit = QLineEdit()
        self.contact_edit = QLineEdit()
        self.localisation_edit = QLineEdit()
        self.gere_par_edit = QLineEdit()
        for edit in [
            self.designation_edit,
            self.societe_edit,
            self.contact_edit,
            self.localisation_edit,
            self.gere_par_edit,
        ]:
            _configure_text_control(edit, min_chars=48)
        client_layout.addRow("Societe", self.societe_edit)
        client_layout.addRow("Contact", self.contact_edit)
        client_layout.addRow("Localisation", self.localisation_edit)
        client_layout.addRow("Gere par", self.gere_par_edit)
        root_layout.addWidget(client_frame)

        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumHeight(120)
        root_layout.addWidget(self.logs)

        actions = QHBoxLayout()
        actions.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.reset_button = QPushButton("Reinitialiser")
        self.load_button = QPushButton("Charger")
        self.open_button = QPushButton("Ouvrir fiche")
        self.create_button = QPushButton("Creer")
        self.update_button = QPushButton("Mettre a jour")
        self.create_button.setDefault(True)
        self.reset_button.clicked.connect(self.reset_form_fields)
        self.load_button.clicked.connect(self.load_requested.emit)
        self.open_button.clicked.connect(self.open_fiche_requested.emit)
        self.create_button.clicked.connect(self.create_requested.emit)
        self.update_button.clicked.connect(self.update_requested.emit)
        actions.addWidget(self.reset_button)
        actions.addWidget(self.load_button)
        actions.addWidget(self.open_button)
        actions.addWidget(self.create_button)
        actions.addWidget(self.update_button)
        root_layout.addLayout(actions)


def _configure_form_layout(layout: QFormLayout) -> None:
    layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
    layout.setHorizontalSpacing(14)
    layout.setVerticalSpacing(10)


def _configure_value_label(label: QLabel) -> None:
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)


def _configure_text_control(widget: QWidget, *, min_chars: int) -> None:
    width = widget.fontMetrics().horizontalAdvance("M" * min_chars) + 24
    widget.setMinimumWidth(width)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
