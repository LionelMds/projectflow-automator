"""Window « Détail » of the Planning tab: the task chosen, beside the planning.

It is a window of its own, not modal: it floats over the main window, can be moved to
another screen, and keeps following the bar or the card selected while it stays open.
The planning stays usable behind it. Its place and size are kept for the session.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QCloseEvent, QGuiApplication, QHideEvent, QShowEvent
from PySide6.QtWidgets import QVBoxLayout, QWidget

from projectflow.ui.planning.detail import DetailPanel

WINDOW_TITLE = "Détail"
DEFAULT_SIZE = QSize(1040, 640)
MINIMUM_SIZE = QSize(960, 480)
SCREEN_MARGIN = 24


def window_title(heading: str) -> str:
    """« 2026-5194 - Fabrication — Détail », or « Détail » alone when nothing is shown."""
    return f"{heading} — {WINDOW_TITLE}" if heading else WINDOW_TITLE


class DetailWindow(QWidget):
    """Top-level window holding the ``DetailPanel``; ``opened_changed`` follows its show."""

    opened_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        # A dialog of the tab's window: over it, never behind, and closed with it.
        super().__init__(parent, Qt.WindowType.Dialog)
        self.setObjectName("planningDetailWindow")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)  # noqa: FBT003
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.panel = DetailPanel(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.panel)
        self.setMinimumSize(MINIMUM_SIZE)
        self.resize(DEFAULT_SIZE)
        self._placed = False
        self.panel.back_requested.connect(self.close)
        self.panel.shown_changed.connect(self._sync_title)
        self._sync_title()

    def is_open(self) -> bool:
        return self.isVisible()

    def open_window(self) -> None:
        """Show the window (centred on the planning the first time) and bring it forward."""
        if not self._placed:
            self._place()
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _place(self) -> None:
        self._placed = True
        parent = self.parentWidget()
        host = parent.window() if parent is not None else None
        screen = host.screen() if host is not None else QGuiApplication.primaryScreen()
        area = screen.availableGeometry().adjusted(
            SCREEN_MARGIN, SCREEN_MARGIN, -SCREEN_MARGIN, -SCREEN_MARGIN
        )
        size = self.size().boundedTo(area.size()).expandedTo(self.minimumSize())
        self.resize(size)
        center = host.frameGeometry().center() if host is not None else area.center()
        corner = center - QPoint(size.width() // 2, size.height() // 2)
        x = min(max(corner.x(), area.left()), max(area.right() - size.width(), area.left()))
        y = min(max(corner.y(), area.top()), max(area.bottom() - size.height(), area.top()))
        self.move(x, y)

    def _sync_title(self) -> None:
        self.setWindowTitle(window_title(self.panel.heading()))

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if not event.spontaneous():
            self.opened_changed.emit(True)  # noqa: FBT003

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        # What is being typed or rolled is saved before the window goes.
        self.panel.flush()
        super().closeEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        super().hideEvent(event)
        if not event.spontaneous():
            self.opened_changed.emit(False)  # noqa: FBT003
