from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QVBoxLayout, QWidget

from projectflow.cad.templates import CadOptionAvailability, template_availability
from projectflow.config import CadConfig
from projectflow.ui.widgets.industry import label

_SOLIDWORKS_HELP = (
    "Copie les fichiers SolidWorks modeles, les renomme avec le numero du projet, "
    "relie l'assemblage aux pieces du projet et renseigne les proprietes."
)
_AUTOCAD_HELP = "Copie le plan AutoCAD modele et le renomme avec le numero du projet."
_SOLIDWORKS_DETAIL = (
    "Pièces renommées au numéro du projet, assemblage relié, propriétés renseignées"
)


class CadOptionsWidget(QWidget):
    """Two per-creation options, unchecked by default and never saved in the settings."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.solidworks_checkbox = QCheckBox("Ajouter arborescence SolidWorks")
        self.autocad_checkbox = QCheckBox("Ajouter modèle AutoCAD")
        self.solidworks_detail = label(_SOLIDWORKS_DETAIL, "small", wrap=True)
        self.autocad_detail = label("Plan modèle copié dans le dossier projet et renommé", "small")
        layout.addLayout(_option(self.solidworks_checkbox, self.solidworks_detail))
        layout.addLayout(_option(self.autocad_checkbox, self.autocad_detail))
        self.set_availability(
            solidworks=template_availability(None, "SolidWorks"),
            autocad=template_availability(None, "AutoCAD"),
        )

    def values(self) -> tuple[bool, bool]:
        return (
            self.solidworks_checkbox.isEnabled() and self.solidworks_checkbox.isChecked(),
            self.autocad_checkbox.isEnabled() and self.autocad_checkbox.isChecked(),
        )

    def set_values(self, *, solidworks: bool, autocad: bool) -> None:
        self.solidworks_checkbox.setChecked(solidworks and self.solidworks_checkbox.isEnabled())
        self.autocad_checkbox.setChecked(autocad and self.autocad_checkbox.isEnabled())

    def reset(self) -> None:
        self.set_values(solidworks=False, autocad=False)

    def apply_config(self, config: CadConfig) -> None:
        subfolder = config.destination_subfolder.replace("/", "\\")
        self.autocad_detail.setText(
            f"Plan modèle copié dans {subfolder}\\ et renommé"
            if subfolder
            else "Plan modèle copié à la racine du projet et renommé",
        )
        self.set_availability(
            solidworks=template_availability(config.solidworks_template_dir, "SolidWorks"),
            autocad=template_availability(config.autocad_template_dir, "AutoCAD"),
        )

    def set_availability(
        self,
        *,
        solidworks: CadOptionAvailability,
        autocad: CadOptionAvailability,
    ) -> None:
        _apply_availability(self.solidworks_checkbox, solidworks, _SOLIDWORKS_HELP)
        _apply_availability(self.autocad_checkbox, autocad, _AUTOCAD_HELP)
        self.solidworks_detail.setEnabled(solidworks.available)
        self.autocad_detail.setEnabled(autocad.available)


def _option(checkbox: QCheckBox, detail: QWidget) -> QVBoxLayout:
    column = QVBoxLayout()
    column.setSpacing(0)
    column.addWidget(checkbox)
    indent = QHBoxLayout()
    indent.setContentsMargins(26, 0, 0, 0)
    indent.addWidget(detail)
    column.addLayout(indent)
    return column


def _apply_availability(
    checkbox: QCheckBox,
    availability: CadOptionAvailability,
    help_text: str,
) -> None:
    checkbox.setEnabled(availability.available)
    if not availability.available:
        checkbox.setChecked(False)
        checkbox.setToolTip(availability.reason)
        return
    checkbox.setToolTip(f"{help_text}\n{availability.reason}")
