"""Compact quick entry floating over a grid cell or a range drawn over several rows.

The editor is a ``QuickEntryBar`` in compact mode inside a blueprint frame: the cell
gives the people and the dates (``EntryContext.cell_*``), the typed text the rest.
Entrée emits ``submitted(entry, False)``, Tab ``submitted(entry, True)`` so that the tab
moves the cell cursor on to the next free half-day; the editor then closes and gives the
focus back to the widget that had it (the grid). Échap emits ``cancelled("escape")`` and
gives the focus back too; the focus going to another widget emits ``cancelled("focus")``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Literal

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from projectflow.core.planning.quick_entry import EntryContext, ParsedEntry, ProjectInfo
from projectflow.ui.planning.quick_entry_bar import AvailabilityProvider, QuickEntryBar
from projectflow.ui.widgets.industry import MARK_GUTTER, BlueprintFrame, ElidedLabel

CancelReason = Literal["escape", "focus"]

INLINE_MIN_WIDTH: Final = 340
INLINE_PADDING: Final = (6, 4, 6, 6)
INLINE_SPACING: Final = 4
CREATE_HINT: Final = "Entrée crée · Tab crée et continue"
COMMON_HINT: Final = "Entrée crée une tâche commune"
CANCEL_HINT: Final = "Échap annule"


class InlineEditor(BlueprintFrame):
    """Floating editor of the planning grid, opened with ``open``.

    With a parent the editor is a child of that widget (the grid viewport, say) and is
    placed by mapping the global rectangle into it; without parent it is a frameless tool
    window. A ``submitted`` handler that cannot create the task calls ``refuse()``: the
    editor stays open with the text.
    """

    submitted = Signal(object, bool)
    cancelled = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, padding=INLINE_PADDING, fill="bg", spacing=INLINE_SPACING)
        if parent is None:
            self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._context: EntryContext | None = None
        self._open = False
        self._opened = 0
        self._submitting = False
        self._refused = False
        self._return_focus: QWidget | None = None
        self._bar = QuickEntryBar(self, compact=True)
        self._bar.entry_submitted.connect(self._on_entered)
        self._bar.entry_submitted_next.connect(self._on_tabbed)
        self._bar.cancelled.connect(self._on_escape)
        self._label = ElidedLabel("", Qt.TextElideMode.ElideRight)
        self._label.setProperty("role", "small")
        self._hint = QLabel()
        self._hint.setProperty("role", "small")
        self._hint.setWordWrap(True)
        self.box.addWidget(self._bar)
        self.box.addWidget(self._label)
        self.box.addWidget(self._hint)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.focusChanged.connect(self._on_focus_changed)
        self.hide()

    @property
    def bar(self) -> QuickEntryBar:
        return self._bar

    @property
    def context(self) -> EntryContext | None:
        return self._context

    def is_open(self) -> bool:
        return self._open

    def label_text(self) -> str:
        """Return the cell label given to ``open``."""
        return self._label.full_text()

    def hint_text(self) -> str:
        return self._hint.text()

    def set_projects(self, projects: Sequence[ProjectInfo]) -> None:
        self._bar.set_projects(projects)

    def set_availability_provider(self, provider: AvailabilityProvider | None) -> None:
        self._bar.set_availability_provider(provider)

    def open(
        self,
        at_global_rect: QRect,
        context: EntryContext,
        *,
        label: str,
        prefill: str = "",
    ) -> None:
        """Show the editor over ``at_global_rect`` (screen coordinates of the cell).

        ``label`` names the cell, e.g. « LBI, YCH · jeu 22.10 06:30 → ven 23.10 12:00 »;
        ``prefill`` is the character typed on the cell that opened the editor.
        """
        focus = QApplication.focusWidget()
        if focus is not None and focus is not self and not self.isAncestorOf(focus):
            self._return_focus = focus
        self._context = context
        self._opened += 1
        self._bar.set_context_provider(self._current_context)
        hint = COMMON_HINT if len(context.cell_resource_ids) > 1 else CREATE_HINT
        self._label.setText(label)
        self._label.setVisible(bool(label))
        self._hint.setText(f"{hint} · {CANCEL_HINT}")
        self._bar.clear()
        self._open = True
        self._place(at_global_rect)
        self.show()
        self.raise_()
        if self.isWindow():
            self.activateWindow()
        self._bar.focus_entry(prefill)

    def close_editor(self) -> None:
        """Hide the editor without emitting anything."""
        self._open = False
        self.hide()

    def refuse(self) -> None:
        """Keep the editor open with its text: the task could not be created.

        Called from a ``submitted`` handler (read-only planning, refused command).
        """
        if self._submitting:
            self._refused = True
        self._bar.refuse()

    # ---------------------------------------------------------- geometry ----

    def _place(self, rect: QRect) -> None:
        # The field sits over the cell: the frame's gutter and padding, then the field's.
        left, top, right, _bottom = INLINE_PADDING
        inset = QPoint(2 * MARK_GUTTER + left, 2 * MARK_GUTTER + top)
        width = max(INLINE_MIN_WIDTH, rect.width() + 4 * MARK_GUTTER + left + right)
        self.resize(width, self._height_for(width))
        self._move_inside(rect.topLeft() - inset)

    def _height_for(self, width: int) -> int:
        layout = self.layout()
        if layout is not None and layout.hasHeightForWidth():
            return layout.totalHeightForWidth(width)
        return self.sizeHint().height()

    def _move_inside(self, global_top_left: QPoint) -> None:
        parent = self.parentWidget()
        if parent is not None and not self.isWindow():
            point = parent.mapFromGlobal(global_top_left)
            area = parent.rect()
        else:
            point = global_top_left
            screen = QGuiApplication.screenAt(global_top_left) or QGuiApplication.primaryScreen()
            area = screen.availableGeometry() if screen is not None else QRect(point, self.size())
        x = max(area.left(), min(point.x(), area.right() + 1 - self.width()))
        y = max(area.top(), min(point.y(), area.bottom() + 1 - self.height()))
        self.move(x, y)

    def event(self, event: QEvent) -> bool:
        handled = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest and self._open:
            height = self._height_for(self.width())
            if height != self.height():
                anchor = self.mapToGlobal(QPoint(0, 0))
                self.resize(self.width(), height)
                self._move_inside(anchor)
        return handled

    # ------------------------------------------------------------ events ----

    def _current_context(self) -> EntryContext:
        if self._context is None:
            raise RuntimeError("Editeur de case ouvert sans contexte")
        return self._context

    def _on_entered(self, entry: object, _keep_project: object) -> None:
        self._submit(entry, advance=False)

    def _on_tabbed(self, entry: object) -> None:
        self._submit(entry, advance=True)

    def _submit(self, entry: object, *, advance: bool) -> None:
        if not self._open or not isinstance(entry, ParsedEntry):
            return
        opened = self._opened
        self._submitting, self._refused = True, False
        try:
            self.submitted.emit(entry, advance)
        finally:
            self._submitting = False
        # Refused, or the handler already opened the editor on another cell.
        if self._refused or opened != self._opened:
            self._refused = False
            return
        self.close_editor()
        self._give_focus_back()

    def _on_escape(self) -> None:
        self._cancel("escape")

    def _cancel(self, reason: CancelReason) -> None:
        if not self._open:
            return
        self.close_editor()
        if reason == "escape":
            self._give_focus_back()  # before the signal: a handler may still move it
        self.cancelled.emit(reason)

    def _give_focus_back(self) -> None:
        widget, self._return_focus = self._return_focus, None
        if widget is None:
            return
        try:
            visible = widget.isVisible()
        except RuntimeError:  # deleted while the editor was open
            return
        if not visible:
            return
        if self.isWindow():
            widget.window().activateWindow()
        widget.setFocus(Qt.FocusReason.OtherFocusReason)

    def _on_focus_changed(self, _old: QWidget | None, new: QWidget | None) -> None:
        # Leaving the application (Alt+Tab) keeps the editor: the focus comes back to it.
        # A submitted handler may move the focus (to the grid): the editor closes after it.
        if (
            not self._open
            or self._submitting
            or new is None
            or new is self
            or self.isAncestorOf(new)
        ):
            return
        self._return_focus = None  # the focus stays where the user put it
        self._cancel("focus")
