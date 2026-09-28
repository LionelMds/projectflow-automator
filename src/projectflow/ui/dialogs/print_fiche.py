from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtPrintSupport import QPrinterInfo
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

MAX_COPIES = 20


@dataclass(frozen=True, slots=True)
class PrintFicheOptions:
    printer_name: str
    copies: int = 1


def installed_printers() -> tuple[list[str], str]:
    """Return the installed printers and the system default printer."""
    return list(QPrinterInfo.availablePrinterNames()), QPrinterInfo.defaultPrinterName()


class PrintFicheDialog(QDialog):
    def __init__(
        self,
        *,
        fiche_name: str,
        printers: Sequence[str],
        preferred_printer: str = "",
        default_printer: str = "",
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self.setWindowTitle("Imprimer la fiche")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        fiche_label = QLabel(fiche_name)
        fiche_label.setWordWrap(True)
        self.printer_combo = QComboBox()
        self.printer_combo.addItems(list(printers))
        for candidate in (preferred_printer, default_printer):
            index = self.printer_combo.findText(candidate)
            if candidate and index >= 0:
                self.printer_combo.setCurrentIndex(index)
                break
        self.copies_spin = QSpinBox()
        self.copies_spin.setRange(1, MAX_COPIES)
        self.copies_spin.setValue(1)
        form.addRow("Fiche", fiche_label)
        form.addRow("Imprimante", self.printer_combo)
        form.addRow("Copies", self.copies_spin)
        form.addRow("Format", QLabel("A4, ajusté sur une page"))
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Imprimer")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def options(self) -> PrintFicheOptions:
        return PrintFicheOptions(
            printer_name=self.printer_combo.currentText(),
            copies=self.copies_spin.value(),
        )
