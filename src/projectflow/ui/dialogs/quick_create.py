from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from projectflow.core.client_directory import ClientDirectory
from projectflow.ui.creation_tab import CreationFormData
from projectflow.ui.widgets.client_autocomplete import ClientAutocomplete
from projectflow.ui.widgets.planner import PlannerSelectionWidget


class QuickCreateDialog(QDialog):
    classic_requested = Signal()
    next_available_requested = Signal()
    planner_options_requested = Signal()
    client_suggestions_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Nouveau projet rapide")
        self.setModal(False)
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
            planner=self.planner_widget.data(),
        )

    def set_data(self, data: CreationFormData) -> None:
        index = self.year_combo.findText(data.year)
        if index >= 0:
            self.year_combo.setCurrentIndex(index)
        elif data.year:
            self.year_combo.addItem(data.year)
            self.year_combo.setCurrentText(data.year)
        self.project_id_edit.setText(data.project_id)
        self.subproject_edit.setText(data.subproject_id)
        self.designation_edit.setText(data.designation)
        self.societe_edit.setText(data.societe)
        self.contact_edit.setText(data.contact)
        self.localisation_edit.setText(data.localisation)
        self.gere_par_edit.setText(data.gere_par)
        self.planner_widget.set_data(data.planner)

    def set_project_identity(
        self,
        *,
        year: str,
        project_id: str,
        subproject_id: str = "",
    ) -> None:
        index = self.year_combo.findText(year)
        if index >= 0:
            self.year_combo.setCurrentIndex(index)
        elif year:
            self.year_combo.addItem(year)
            self.year_combo.setCurrentText(year)
        self.project_id_edit.setText(project_id)
        self.subproject_edit.setText(subproject_id)

    def set_user_initials(self, initials: str) -> None:
        self.user_initials_edit.setText(initials)

    def apply_planner_config(
        self,
        *,
        enabled: bool,
        bucket_id: str,
        bucket_name: str,
        due_days: int,
    ) -> None:
        self.planner_widget.set_config_defaults(
            enabled=enabled,
            bucket_id=bucket_id,
            bucket_name=bucket_name,
            due_days=due_days,
        )

    def set_client_directory(self, directory: ClientDirectory) -> None:
        self._client_autocomplete.set_directory(directory)

    def show_and_raise(self) -> None:
        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def _build_ui(self) -> None:
        self.resize(560, 430)
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        title = QLabel("Nouveau projet rapide")
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.classic_button = QToolButton()
        self.classic_button.setArrowType(Qt.ArrowType.RightArrow)
        self.classic_button.setToolTip("Ouvrir la fenetre complete")
        self.classic_button.clicked.connect(self.classic_requested.emit)
        header.addWidget(title)
        header.addWidget(self.classic_button)
        layout.addLayout(header)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        identity = QHBoxLayout()
        self.year_combo = QComboBox()
        self.year_combo.setEditable(True)
        self.year_combo.addItems([str(date.today().year), str(date.today().year + 1)])
        self.project_id_edit = QLineEdit()
        self.project_id_edit.setPlaceholderText("4995")
        self.subproject_edit = QLineEdit()
        self.subproject_edit.setPlaceholderText("Sous-projet")
        self.next_available_button = QPushButton("Suivant disponible")
        self.next_available_button.clicked.connect(self.next_available_requested.emit)
        identity.addWidget(self.year_combo, 1)
        identity.addWidget(self.project_id_edit, 2)
        identity.addWidget(self.subproject_edit, 1)
        identity.addWidget(self.next_available_button)
        form.addRow("Numero", _wrap_layout(identity))

        self.designation_edit = QLineEdit()
        self.societe_edit = QLineEdit()
        self.contact_edit = QLineEdit()
        self._client_autocomplete = ClientAutocomplete(
            societe_edit=self.societe_edit,
            contact_edit=self.contact_edit,
        )
        self.societe_edit.textEdited.connect(
            lambda _text: self.client_suggestions_requested.emit(),
        )
        self.contact_edit.textEdited.connect(
            lambda _text: self.client_suggestions_requested.emit(),
        )
        self.localisation_edit = QLineEdit()
        self.gere_par_edit = QLineEdit()
        self.user_initials_edit = QLineEdit()
        self.user_initials_edit.setReadOnly(True)
        self.user_initials_edit.setPlaceholderText("Parametres")
        self.user_initials_edit.setToolTip(
            "Initiales de l'utilisateur definies dans les parametres."
        )
        self.user_initials_edit.setFixedWidth(
            self.user_initials_edit.fontMetrics().horizontalAdvance("MMMMMM") + 24,
        )
        form.addRow("Designation", self.designation_edit)
        form.addRow("Societe", self.societe_edit)
        form.addRow("Contact", self.contact_edit)
        form.addRow("Localisation", self.localisation_edit)
        responsibility = QHBoxLayout()
        responsibility.setSpacing(8)
        responsibility.addWidget(self.gere_par_edit, 1)
        responsibility.addWidget(QLabel("Initiales utilisateur"))
        responsibility.addWidget(self.user_initials_edit)
        form.addRow("Géré par", _wrap_layout(responsibility))
        layout.addLayout(form)

        self.planner_widget = PlannerSelectionWidget()
        self.planner_widget.options_requested.connect(self.planner_options_requested.emit)
        layout.addWidget(self.planner_widget)

        buttons = QDialogButtonBox()
        create_button = QPushButton("Creer")
        create_button.setDefault(True)
        buttons.addButton(create_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Annuler", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def _wrap_layout(layout: QHBoxLayout) -> QWidget:
    widget = QWidget()
    widget.setLayout(layout)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return widget
