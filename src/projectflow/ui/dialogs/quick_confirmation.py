from __future__ import annotations

from typing import Literal

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

QuickConfirmationAction = Literal["edit", "next"]


class QuickCreationConfirmationDialog(QDialog):
    open_fiche_requested = Signal()
    open_repertoire_requested = Signal()

    def __init__(
        self,
        *,
        title: str,
        message: str,
        project_dir: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._selected_action: QuickConfirmationAction | None = None
        self.setWindowTitle(title)
        self._build_ui(message=message, project_dir=project_dir)

    def selected_action(self) -> QuickConfirmationAction | None:
        return self._selected_action

    def _build_ui(self, *, message: str, project_dir: str) -> None:
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        message_label = QLabel(message)
        message_label.setWordWrap(True)
        layout.addWidget(message_label)

        path_label = QLabel(f"Dossier:\n{project_dir}")
        path_label.setWordWrap(True)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(path_label)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(separator)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.open_fiche_button = QPushButton("Ouvrir fiche")
        self.open_repertoire_button = QPushButton("Ouvrir repertoire")
        self.edit_button = QPushButton("Modifier")
        self.next_button = QPushButton("Suivant")
        self.next_button.setDefault(True)

        self.open_fiche_button.clicked.connect(self.open_fiche_requested.emit)
        self.open_repertoire_button.clicked.connect(self.open_repertoire_requested.emit)
        self.edit_button.clicked.connect(lambda: self._choose("edit"))
        self.next_button.clicked.connect(lambda: self._choose("next"))

        buttons.addWidget(self.open_fiche_button)
        buttons.addWidget(self.open_repertoire_button)
        buttons.addStretch(1)
        buttons.addWidget(self.edit_button)
        buttons.addWidget(self.next_button)
        layout.addLayout(buttons)

    def _choose(self, action: QuickConfirmationAction) -> None:
        self._selected_action = action
        self.accept()
