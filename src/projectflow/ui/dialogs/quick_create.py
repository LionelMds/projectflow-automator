from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from projectflow.ui.creation_tab import CreationFormData


class QuickCreateDialog(QDialog):
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

    def _build_ui(self) -> None:
        self.resize(460, 280)
        layout = QVBoxLayout(self)
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
        identity.addWidget(self.year_combo, 1)
        identity.addWidget(self.project_id_edit, 2)
        identity.addWidget(self.subproject_edit, 1)
        form.addRow("Numero", _wrap_layout(identity))

        self.designation_edit = QLineEdit()
        self.societe_edit = QLineEdit()
        self.contact_edit = QLineEdit()
        self.localisation_edit = QLineEdit()
        self.gere_par_edit = QLineEdit()
        form.addRow("Designation", self.designation_edit)
        form.addRow("Societe", self.societe_edit)
        form.addRow("Contact", self.contact_edit)
        form.addRow("Localisation", self.localisation_edit)
        form.addRow("Gere par", self.gere_par_edit)
        layout.addLayout(form)

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
