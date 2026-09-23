from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from projectflow.auth.browser_sign_in import open_sign_in_page


class MicrosoftSignInDialog(QDialog):
    def __init__(
        self,
        sign_in_url: str,
        cancel: Callable[[], None],
        *,
        open_url: Callable[[str], bool] = open_sign_in_page,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._sign_in_url = sign_in_url
        self._cancel = cancel
        self._open_url = open_url
        self._finished_by_sign_in = False
        self.setWindowTitle("Connexion Microsoft")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        message = QLabel(
            "ProjectFlow attend votre connexion Microsoft.\n\n"
            "La page de connexion a ete ouverte dans le navigateur. Si elle n'apparait pas, "
            "cliquez sur « Ouvrir la page de connexion », ou copiez le lien et collez-le "
            "dans Edge ou Chrome.\n\n"
            "Cette fenetre se ferme automatiquement une fois la connexion terminee.",
        )
        message.setWordWrap(True)
        layout.addWidget(message)
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.open_button = QPushButton("Ouvrir la page de connexion")
        self.open_button.setDefault(True)
        self.open_button.clicked.connect(self._open_page)
        self.copy_button = QPushButton("Copier le lien")
        self.copy_button.clicked.connect(self._copy_link)
        self.cancel_button = QPushButton("Annuler")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.copy_button)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)
        self.setMinimumWidth(460)

    def close_after_sign_in(self) -> None:
        self._finished_by_sign_in = True
        self.accept()

    def reject(self) -> None:
        if not self._finished_by_sign_in:
            self._finished_by_sign_in = True
            self._cancel()
        super().reject()

    def _open_page(self) -> None:
        if self._open_url(self._sign_in_url):
            self.status_label.setText("Page de connexion ouverte dans le navigateur.")
        else:
            self.status_label.setText("Navigateur introuvable : copiez le lien.")

    def _copy_link(self) -> None:
        QApplication.clipboard().setText(self._sign_in_url)
        self.status_label.setText("Lien copie : collez-le dans la barre d'adresse du navigateur.")


class MicrosoftSignInPrompt(QObject):
    """Bridges sign-in notifications from worker threads to dialogs on the UI thread."""

    _started = Signal(str, object)
    _finished = Signal(str)

    def __init__(self, parent_window: QWidget | None = None) -> None:
        super().__init__()
        self._parent_window = parent_window
        self._dialogs: dict[str, MicrosoftSignInDialog] = {}
        self._started.connect(self._show, Qt.ConnectionType.QueuedConnection)
        self._finished.connect(self._close, Qt.ConnectionType.QueuedConnection)

    def sign_in_started(self, sign_in_url: str, cancel: Callable[[], None]) -> None:
        self._started.emit(sign_in_url, cancel)

    def sign_in_finished(self, sign_in_url: str) -> None:
        self._finished.emit(sign_in_url)

    def open_dialogs(self) -> list[MicrosoftSignInDialog]:
        return list(self._dialogs.values())

    def _show(self, sign_in_url: str, cancel: Callable[[], None]) -> None:
        dialog = MicrosoftSignInDialog(sign_in_url, cancel, parent=self._parent_window)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.finished.connect(lambda _result: self._dialogs.pop(sign_in_url, None))
        self._dialogs[sign_in_url] = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _close(self, sign_in_url: str) -> None:
        dialog = self._dialogs.pop(sign_in_url, None)
        if dialog is not None:
            dialog.close_after_sign_in()
