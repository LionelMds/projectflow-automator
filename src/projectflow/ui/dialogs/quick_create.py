from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from projectflow.config import CadConfig
from projectflow.core.client_directory import ClientDirectory
from projectflow.ui.creation_tab import CreationFormData
from projectflow.ui.widgets.cad import CadOptionsWidget
from projectflow.ui.widgets.client_autocomplete import ClientAutocomplete
from projectflow.ui.widgets.industry import (
    CheckChip,
    LogoLabel,
    PrimaryButton,
    button,
    field,
    label,
    rule,
)
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
        add_solidworks, add_autocad = self.cad_options.values()
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
            add_solidworks=add_solidworks,
            add_autocad=add_autocad,
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
        self.cad_options.set_values(solidworks=data.add_solidworks, autocad=data.add_autocad)

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
        caption = self._gere_par_field.caption_label  # type: ignore[attr-defined]
        caption.setText(f"Géré par · {initials}" if initials else "Géré par")

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

    def apply_cad_config(self, cad: CadConfig) -> None:
        self.cad_options.apply_config(cad)

    def set_client_directory(self, directory: ClientDirectory) -> None:
        self._client_autocomplete.set_directory(directory)

    def show_and_raise(self) -> None:
        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def _build_ui(self) -> None:
        self.setMinimumWidth(560)
        self.resize(560, 470)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(18, 12, 12, 12)
        header.setSpacing(10)
        header.addWidget(LogoLabel(26))
        header.addWidget(label("Nouveau projet rapide", "h4"), 1)
        self.classic_button = button("Fenêtre complète", "ghost", icon="arrow-right", size="sm")
        self.classic_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.classic_button.setToolTip("Ouvrir la fenêtre complète")
        self.classic_button.clicked.connect(self.classic_requested.emit)
        header.addWidget(self.classic_button)
        layout.addLayout(header)
        layout.addWidget(rule())

        body = QVBoxLayout()
        body.setContentsMargins(18, 16, 18, 16)
        body.setSpacing(12)
        identity = QHBoxLayout()
        identity.setSpacing(8)
        self.year_combo = QComboBox()
        self.year_combo.setEditable(True)
        self.year_combo.addItems([str(date.today().year), str(date.today().year + 1)])
        self.project_id_edit = QLineEdit()
        self.project_id_edit.setPlaceholderText("4995")
        self.subproject_edit = QLineEdit()
        self.subproject_edit.setPlaceholderText("—")
        self.next_available_button = button("Suivant")
        self.next_available_button.setToolTip("Suivant disponible")
        self.next_available_button.clicked.connect(self.next_available_requested.emit)
        year = field("Année", self.year_combo)
        year.setFixedWidth(96)
        subproject = field("Sous-projet", self.subproject_edit)
        subproject.setFixedWidth(90)
        identity.addWidget(year)
        identity.addWidget(field("Numéro", self.project_id_edit), 1)
        identity.addWidget(subproject)
        identity.addWidget(self.next_available_button, 0, Qt.AlignmentFlag.AlignBottom)
        body.addLayout(identity)

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
        # Holds the C9 initials; they show in the "Géré par" caption.
        self.user_initials_edit = QLineEdit()
        self.user_initials_edit.setReadOnly(True)
        self.user_initials_edit.hide()
        body.addWidget(field("Désignation", self.designation_edit))
        client = QHBoxLayout()
        client.setSpacing(8)
        client.addWidget(field("Société", self.societe_edit), 1)
        client.addWidget(field("Contact", self.contact_edit), 1)
        body.addLayout(client)
        place = QHBoxLayout()
        place.setSpacing(8)
        place.addWidget(field("Localisation", self.localisation_edit), 1)
        self._gere_par_field = field("Géré par", self.gere_par_edit)
        self._gere_par_field.setFixedWidth(170)
        place.addWidget(self._gere_par_field)
        body.addLayout(place)

        self.planner_widget = PlannerSelectionWidget()
        self.planner_widget.options_requested.connect(self.planner_options_requested.emit)
        self.cad_options = CadOptionsWidget()
        self.cad_options.hide()
        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.planner_chip = CheckChip("Planner", self.planner_widget.enabled_checkbox)
        self.solidworks_chip = CheckChip("SolidWorks", self.cad_options.solidworks_checkbox)
        self.autocad_chip = CheckChip("AutoCAD", self.cad_options.autocad_checkbox)
        for chip in (self.planner_chip, self.solidworks_chip, self.autocad_chip):
            chips.addWidget(chip)
        chips.addStretch(1)
        body.addLayout(chips)
        # Column, members and due date only matter once a Planner task is requested.
        self.planner_widget.enabled_checkbox.hide()
        self.planner_widget.setVisible(self.planner_widget.enabled_checkbox.isChecked())
        self.planner_widget.enabled_checkbox.toggled.connect(self._show_planner_details)
        body.addWidget(self.planner_widget)
        body.addWidget(self.cad_options)
        body.addStretch(1)
        layout.addLayout(body, 1)
        layout.addWidget(rule())

        footer = QHBoxLayout()
        footer.setContentsMargins(18, 6, 12, 6)
        footer.setSpacing(8)
        footer.addStretch(1)
        cancel_button = button("Annuler")
        cancel_button.clicked.connect(self.reject)
        create_button = PrimaryButton("Créer")
        create_button.setDefault(True)
        create_button.clicked.connect(self.accept)
        footer.addWidget(cancel_button)
        footer.addWidget(create_button)
        layout.addLayout(footer)

    def _show_planner_details(self, checked: bool) -> None:  # noqa: FBT001
        self.planner_widget.setVisible(checked)
        self.adjustSize()
