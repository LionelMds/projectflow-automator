from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QCursor, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


class ProjectFlowTray(QObject):
    show_requested = Signal()
    open_repertoire_requested = Signal()
    open_planning_requested = Signal()
    update_check_requested = Signal()
    reset_requested = Signal()
    quit_requested = Signal()

    def __init__(self, *, icon: QIcon, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tray = QSystemTrayIcon(icon, self)
        self._menu = self._build_menu()
        self._tray.setToolTip("ProjectFlow Automator")
        self._tray.activated.connect(self._on_activated)

    @property
    def is_available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        if self.is_available:
            self._tray.show()

    def show_message(self, title: str, message: str) -> None:
        if self.is_available and self._tray.supportsMessages():
            self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 3500)

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        show_action = QAction("Afficher ProjectFlow", self)
        open_repertoire_action = QAction("Ouvrir repertoire", self)
        open_planning_action = QAction("Ouvrir le planning", self)
        update_action = QAction("Rechercher une mise a jour", self)
        # Reachable even when the window is busy with a broken connection.
        reset_action = QAction("Tout reinitialiser ProjectFlow...", self)
        quit_action = QAction("Quitter ProjectFlow", self)

        show_action.triggered.connect(self.show_requested.emit)
        open_repertoire_action.triggered.connect(self.open_repertoire_requested.emit)
        open_planning_action.triggered.connect(self.open_planning_requested.emit)
        update_action.triggered.connect(self.update_check_requested.emit)
        reset_action.triggered.connect(self.reset_requested.emit)
        quit_action.triggered.connect(self.quit_requested.emit)

        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(open_repertoire_action)
        menu.addAction(open_planning_action)
        menu.addAction(update_action)
        menu.addAction(reset_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        return menu

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            # A single click and a double click both bring the window back.
            self.show_requested.emit()
            return
        if reason == QSystemTrayIcon.ActivationReason.Context:
            self._show_context_menu()

    def _show_context_menu(self) -> None:
        self._menu.popup(QCursor.pos())
