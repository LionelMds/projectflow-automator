from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QCursor, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


class ProjectFlowTray(QObject):
    show_requested = Signal()
    quick_create_requested = Signal()
    open_repertoire_requested = Signal()
    update_check_requested = Signal()
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
        quick_create_action = QAction("Nouveau projet rapide", self)
        open_repertoire_action = QAction("Ouvrir repertoire", self)
        update_action = QAction("Rechercher une mise a jour", self)
        quit_action = QAction("Quitter ProjectFlow", self)

        show_action.triggered.connect(self.show_requested.emit)
        quick_create_action.triggered.connect(self.quick_create_requested.emit)
        open_repertoire_action.triggered.connect(self.open_repertoire_requested.emit)
        update_action.triggered.connect(self.update_check_requested.emit)
        quit_action.triggered.connect(self.quit_requested.emit)

        menu.addAction(show_action)
        menu.addAction(quick_create_action)
        menu.addSeparator()
        menu.addAction(open_repertoire_action)
        menu.addAction(update_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        return menu

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.quick_create_requested.emit()
            return
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_requested.emit()
            return
        if reason == QSystemTrayIcon.ActivationReason.Context:
            self._show_context_menu()

    def _show_context_menu(self) -> None:
        self._menu.popup(QCursor.pos())
