"""Short messages stacked at the bottom right of the planning, with an optional action.

« Tâche supprimée · Annuler », « SBE · bureau a planifié 2026-131 »: a toast is a dark
chip (the theme text colour as ground) that disappears after a few seconds, unless the
pointer rests on it. At most three are visible; a fourth pushes the oldest out. Only the
toasts take the mouse: the gaps of the stack let clicks through to the grid.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QEnterEvent, QPainter, QPaintEvent, QRegion, QResizeEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from projectflow.ui.planning.sync_badge import feedback_color
from projectflow.ui.theme import BODY_FAMILY, HEADING_FAMILY, current_theme, theme_notifier

TOAST_TIMEOUT_MS: Final = 4000
HOVER_GRACE_MS: Final = 1500
MAX_TOASTS: Final = 3
TOAST_MAX_WIDTH: Final = 440
TOAST_MARGINS: Final = (12, 7, 8, 7)
TOAST_SPACING: Final = 8
STACK_SPACING: Final = 6
OVERLAY_MARGIN: Final = 16
UNDO_TEXT: Final = "Annuler"


class Toast(QFrame):
    """One message; ``dismissed`` fires once, when it leaves the stack."""

    dismissed = Signal(object)

    def __init__(
        self,
        message: str,
        *,
        action_text: str = "",
        action: Callable[[], object] | None = None,
        author_tag: str = "",
        timeout_ms: int = TOAST_TIMEOUT_MS,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._action = action
        self._done = False
        self._timeout_ms = timeout_ms
        self._remaining_ms = 0
        self.setObjectName("planningToast")
        self.setMaximumWidth(TOAST_MAX_WIDTH)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(*TOAST_MARGINS)
        layout.setSpacing(TOAST_SPACING)
        # Parented at once: a parentless widget made visible would flash as a window.
        self.tag_label = QLabel(author_tag, self)
        self.tag_label.setObjectName("toastTag")
        self.tag_label.setVisible(bool(author_tag))
        self.message_label = QLabel(message, self)
        self.message_label.setObjectName("toastMessage")
        self.message_label.setWordWrap(True)
        self.action_button = QPushButton(action_text or UNDO_TEXT, self)
        self.action_button.setObjectName("toastAction")
        self.action_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.action_button.setVisible(action is not None)
        self.action_button.clicked.connect(self.trigger)
        layout.addWidget(self.tag_label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.message_label, 1)
        layout.addWidget(self.action_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        if timeout_ms > 0:
            self._timer.start(timeout_ms)
        theme_notifier().changed.connect(self._restyle)
        self._restyle()

    def message(self) -> str:
        return self.message_label.text()

    @property
    def dismissed_already(self) -> bool:
        return self._done

    def trigger(self) -> None:
        """Run the action (« Annuler ») once, then remove the toast."""
        action, self._action = self._action, None
        self.dismiss()
        if action is not None:
            action()

    def dismiss(self) -> None:
        if self._done:
            return
        self._done = True
        self._timer.stop()
        self.hide()
        self.dismissed.emit(self)

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        if self._timer.isActive():
            self._remaining_ms = self._timer.remainingTime()
            self._timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        # A sticky toast stays; another one gets back its time, a short grace at least.
        if not self._done and self._timeout_ms > 0:
            self._timer.start(max(self._remaining_ms, HOVER_GRACE_MS))
        super().leaveEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), current_theme().color("text"))
        painter.end()

    def _restyle(self) -> None:
        theme = current_theme()
        ground = theme.tokens["bg"]
        tag = feedback_color("remote").name()
        action = theme.tokens["accent-300"]
        hover = theme.rgba("bg", 0.14)
        self.setStyleSheet(
            f"""
QLabel#toastMessage {{
  color: {ground}; background: transparent; font-family: "{BODY_FAMILY}"; font-size: 13px;
}}
QLabel#toastTag {{
  color: {ground}; background: {tag}; font-family: "{BODY_FAMILY}"; font-size: 11px;
  font-weight: 600; padding: 1px 5px;
}}
QPushButton#toastAction {{
  color: {action}; background: transparent; border: 0; padding: 2px 6px; min-height: 0;
  font-family: "{HEADING_FAMILY}"; font-weight: 600; font-size: 14px;
}}
QPushButton#toastAction:hover {{ background: {hover}; }}
"""
        )
        self._fit_message()

    def _fit_message(self) -> None:
        """Keep a message that fits the toast on one line; wrap only longer ones."""
        for widget in (self.message_label, self.tag_label, self.action_button):
            widget.ensurePolished()
        left, _top, right, _bottom = TOAST_MARGINS
        used = left + right
        for widget in (self.tag_label, self.action_button):
            if not widget.isHidden():
                used += widget.sizeHint().width() + TOAST_SPACING
        natural = self.message_label.fontMetrics().horizontalAdvance(self.message_label.text())
        self.message_label.setMinimumWidth(min(natural + 1, TOAST_MAX_WIDTH - used))


class ToastOverlay(QWidget):
    """Stack of toasts kept at the bottom right of ``parent``, newest at the bottom."""

    def __init__(
        self,
        parent: QWidget,
        *,
        margin_right: int = OVERLAY_MARGIN,
        margin_bottom: int = OVERLAY_MARGIN,
    ) -> None:
        super().__init__(parent)
        self._margin_right = margin_right
        self._margin_bottom = margin_bottom
        self._toasts: list[Toast] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(STACK_SPACING)
        self._layout = layout
        parent.installEventFilter(self)
        self.hide()

    def show_toast(
        self,
        message: str,
        *,
        action_text: str = "",
        action: Callable[[], object] | None = None,
        author_tag: str = "",
        timeout_ms: int = TOAST_TIMEOUT_MS,
    ) -> Toast:
        """Show ``message``; with ``action`` a button (« Annuler » by default) runs it."""
        toast = Toast(
            message,
            action_text=action_text,
            action=action,
            author_tag=author_tag,
            timeout_ms=timeout_ms,
            parent=self,
        )
        toast.dismissed.connect(self._remove)
        self._toasts.append(toast)
        self._layout.addWidget(toast, 0, Qt.AlignmentFlag.AlignRight)
        while len(self._toasts) > MAX_TOASTS:
            self._toasts[0].dismiss()
        # Shown now, not when the layout gets to it: the stack is sized right away.
        toast.show()
        self.show()
        self._reposition()
        return toast

    def toasts(self) -> tuple[Toast, ...]:
        return tuple(self._toasts)

    def clear(self) -> None:
        for toast in list(self._toasts):
            toast.dismiss()

    def set_margins(self, *, right: int, bottom: int) -> None:
        """Keep the stack ``right`` px from the right edge (e.g. left of a side panel)."""
        self._margin_right, self._margin_bottom = right, bottom
        self._reposition()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._reposition()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_mask()

    def _remove(self, toast: object) -> None:
        if not isinstance(toast, Toast) or toast not in self._toasts:
            return
        self._toasts.remove(toast)
        self._layout.removeWidget(toast)
        toast.deleteLater()
        if self._toasts:
            self._reposition()
        else:
            self.hide()

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None or not self._toasts:
            return
        self._layout.activate()
        self.adjustSize()
        x = max(0, parent.width() - self.width() - self._margin_right)
        y = max(0, parent.height() - self.height() - self._margin_bottom)
        self.move(x, y)
        self._update_mask()
        self.raise_()

    def _update_mask(self) -> None:
        """Take the mouse over the toasts only, not over the gaps between them."""
        region = QRegion()
        for toast in self._toasts:
            if not toast.isHidden():
                region = region.united(QRegion(toast.geometry()))
        if region.isEmpty():
            self.clearMask()
        else:
            self.setMask(region)
