from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QWidget

from projectflow.cad.templates import CadOptionAvailability, template_availability
from projectflow.config import CadConfig

_SOLIDWORKS_HELP = (
    "Copie les fichiers SolidWorks modeles, les renomme avec le numero du projet, "
    "relie l'assemblage aux pieces du projet et renseigne les proprietes."
)
_AUTOCAD_HELP = "Copie le plan AutoCAD modele et le renomme avec le numero du projet."


class CadOptionsWidget(QWidget):
    """Two per-creation options, unchecked by default and never saved in the settings."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        self.solidworks_checkbox = QCheckBox("Ajouter arborescence SolidWorks")
        self.autocad_checkbox = QCheckBox("Ajouter modèle AutoCAD")
        layout.addWidget(self.solidworks_checkbox)
        layout.addWidget(self.autocad_checkbox)
        layout.addStretch(1)
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
