from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QListWidget, QVBoxLayout

from projectflow.core.fiche_service import FicheCandidate


class FicheSelectionDialog(QDialog):
    def __init__(self, candidates: list[FicheCandidate], *, parent: object | None = None) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self.setWindowTitle("Selectionner une fiche")
        self._candidates = candidates
        layout = QVBoxLayout(self)
        self.list_widget = QListWidget()
        for candidate in candidates:
            self.list_widget.addItem(_candidate_label(candidate))
        if candidates:
            self.list_widget.setCurrentRow(0)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(self.list_widget)
        layout.addWidget(buttons)

    def selected_path(self) -> Path | None:
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self._candidates):
            return None
        return self._candidates[row].path


def _candidate_label(candidate: FicheCandidate) -> str:
    modified = datetime.fromtimestamp(
        candidate.modified_timestamp,
        tz=UTC,
    ).strftime("%Y-%m-%d %H:%M")
    size_kb = candidate.size_bytes / 1024
    return f"{candidate.path.name} - {size_kb:.1f} KB - {modified}"
