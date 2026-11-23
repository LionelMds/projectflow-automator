"""« Alerte notification » of the « Détail » panel: the alerts of one task.

Each alert is a chip (« 30 min avant », « lun 23.11 à 07:00 »); a click on a chip removes
it. « + Alerte » offers the usual times before the start in one click and « À un moment
précis… », which unfolds the rolls of a day and a time under the chips. Like the rest of
the panel there is no Save button: each change leaves at once through ``changed``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QHBoxLayout, QMenu, QPushButton, QVBoxLayout, QWidget

from projectflow.core.planning.alerts import BEFORE_PRESETS, lead_text, reminder_text
from projectflow.core.planning.calendar import SNAP_MINUTES
from projectflow.core.planning.models import MAX_REMINDERS, Reminder
from projectflow.core.planning.walltime import MINUTES_PER_HOUR, WallTime, day_start
from projectflow.ui.planning.backlog import FlowLayout
from projectflow.ui.planning.wheel_picker import DateTimeWheel
from projectflow.ui.theme import current_theme
from projectflow.ui.widgets.industry import button, label

CAPTION = "Alerte notification"
ADD_TEXT = "+ Alerte"
MOMENT_TEXT = "À un moment précis…"
REMOVE_MARK = "×"  # noqa: RUF001 - the multiplication sign, which Barlow has
NO_DATE_HINT = "« Avant le début » sonnera une fois la tâche posée sur le planning."
FULL_HINT = f"Au plus {MAX_REMINDERS} alertes par tâche."
DEFAULT_HOUR = 7
CHIP_GAP = 6


def preset_text(minutes: int) -> str:
    """Entry of the « + Alerte » menu: « Au début de la tâche », « 30 min avant »."""
    return "Au début de la tâche" if minutes == 0 else f"{lead_text(minutes)} avant"


class _Frame(QWidget):
    """Hairline frame around the rolls, as around the dates of the panel."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(1, 1, 1, 1)
        self.box.setSpacing(0)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        pen = QPen(current_theme().color("text", 0.30))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.drawRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))
        painter.end()


class AlertEditor(QWidget):
    """Chips of the alerts of a task, with what adds one."""

    changed = Signal(object)
    """The new alerts, as a tuple of ``Reminder``."""
    moment_opened = Signal()
    """The rolls of « À un moment précis » were unfolded (the panel scrolls to them)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._reminders: tuple[Reminder, ...] = ()
        self._start: WallTime | None = None
        self._today: date | None = None
        self._editable = True
        self._chips: list[QPushButton] = []

        self.caption = label(CAPTION, "field")
        self.chips_box = QWidget()
        self._flow = FlowLayout(self.chips_box, spacing=CHIP_GAP)
        self.add_button = button(ADD_TEXT, "chip", icon="bell")
        self.add_button.setProperty("panel", "true")
        self.add_button.setAccessibleName("Ajouter une alerte")
        self.add_button.setToolTip("Prévenir avant le début de la tâche, ou à un moment précis")
        self.menu = QMenu(self.add_button)
        for minutes in BEFORE_PRESETS:
            action = self.menu.addAction(preset_text(minutes))
            action.setData(minutes)
        self.menu.addSeparator()
        self.moment_action = self.menu.addAction(MOMENT_TEXT)
        self.menu.triggered.connect(self._on_menu)
        self.add_button.clicked.connect(self._show_menu)
        self._flow.addWidget(self.add_button)

        self.picker = DateTimeWheel()
        self.picker.setAccessibleName("Alerte : jour, heure, minutes")
        self.picker.finished.connect(self._on_picker_finished)
        self.confirm_button = button("Ajouter l'alerte", "primary", size="sm")
        self.cancel_button = button("Annuler", "ghost", size="sm")
        self.confirm_button.clicked.connect(self.confirm_moment)
        self.cancel_button.clicked.connect(self.close_moment)
        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(8, 6, 8, 8)
        actions_layout.setSpacing(8)
        actions_layout.addStretch(1)
        actions_layout.addWidget(self.cancel_button)
        actions_layout.addWidget(self.confirm_button)
        self.moment_box = _Frame()
        self.moment_box.box.addWidget(self.picker)
        self.moment_box.box.addWidget(actions)
        self.moment_box.hide()

        self.hint_label = label("", "small", wrap=True)
        self.hint_label.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        for widget in (self.caption, self.chips_box, self.moment_box, self.hint_label):
            layout.addWidget(widget)

    # -- contract ---------------------------------------------------------------

    def reminders(self) -> tuple[Reminder, ...]:
        return self._reminders

    def chip_texts(self) -> list[str]:
        return [chip.text() for chip in self._chips]

    def moment_open(self) -> bool:
        return not self.moment_box.isHidden()

    def set_reminders(
        self,
        reminders: Sequence[Reminder],
        *,
        start: WallTime | None,
        today: date | None = None,
    ) -> None:
        """Show the alerts of a task that starts at ``start`` (None: not on the planning)."""
        self._reminders = tuple(reminders)
        self._start = start
        self._today = today
        self._rebuild()

    def set_editable(self, *, editable: bool) -> None:
        self._editable = editable
        if not editable:
            self.close_moment()
        self._rebuild()

    def add_before(self, minutes: int) -> None:
        self._add(Reminder(kind="before", minutes=minutes))

    def open_moment(self) -> None:
        """Unfold the rolls on a likely moment: an hour before the start, else 07:00."""
        if not self._editable or self._full():
            return
        today = self._today or date.today()
        if self._start is not None:
            moment = self._start - MINUTES_PER_HOUR
        else:
            moment = day_start(today) + DEFAULT_HOUR * MINUTES_PER_HOUR
        self.picker.set_moment(moment - moment % SNAP_MINUTES, today=today)
        self.moment_box.show()
        self.picker.setFocus(Qt.FocusReason.OtherFocusReason)
        self.moment_opened.emit()

    def confirm_moment(self) -> None:
        if not self.moment_open():
            return
        moment = self.picker.moment()
        self.close_moment()
        self._add(Reminder(kind="at", at=moment))

    def close_moment(self) -> None:
        self.moment_box.hide()

    def remove(self, key: str) -> None:
        if not self._editable:
            return
        kept = tuple(reminder for reminder in self._reminders if reminder.key != key)
        if kept != self._reminders:
            self._commit(kept)

    # -- internals --------------------------------------------------------------

    def _full(self) -> bool:
        return len(self._reminders) >= MAX_REMINDERS

    def _add(self, reminder: Reminder) -> None:
        if not self._editable or self._full():
            return
        if any(existing.key == reminder.key for existing in self._reminders):
            return
        self._commit((*self._reminders, reminder))

    def _commit(self, reminders: tuple[Reminder, ...]) -> None:
        self._reminders = reminders
        self._rebuild()
        self.changed.emit(reminders)

    def _rebuild(self) -> None:
        for chip in self._chips:
            self._flow.removeWidget(chip)
            chip.hide()
            chip.deleteLater()
        self._chips = []
        self._flow.removeWidget(self.add_button)
        for reminder in self._reminders:
            text = reminder_text(reminder, self._today)
            chip = button(f"{text}  {REMOVE_MARK}" if self._editable else text, "chip")
            chip.setParent(self.chips_box)
            chip.setProperty("panel", "true")
            chip.setCheckable(True)
            chip.setChecked(True)
            chip.setEnabled(self._editable)
            chip.setAccessibleName(f"Alerte {text}")
            chip.setToolTip("Cliquer pour retirer cette alerte")
            chip.clicked.connect(lambda _checked=False, key=reminder.key: self.remove(key))
            self._flow.addWidget(chip)
            chip.show()
            self._chips.append(chip)
        self._flow.addWidget(self.add_button)
        self.add_button.setVisible(self._editable)
        self.add_button.setEnabled(not self._full())
        waiting = self._start is None and any(
            reminder.kind == "before" for reminder in self._reminders
        )
        hint = NO_DATE_HINT if waiting else FULL_HINT if self._full() and self._editable else ""
        self.hint_label.setText(hint)
        self.hint_label.setVisible(bool(hint))
        self._flow.invalidate()
        self.chips_box.updateGeometry()

    def _show_menu(self) -> None:
        self.menu.popup(self.add_button.mapToGlobal(self.add_button.rect().bottomLeft()))

    def _on_menu(self, action: object) -> None:
        if action is self.moment_action:
            self.open_moment()
            return
        data = action.data() if hasattr(action, "data") else None
        if isinstance(data, int):
            self.add_before(data)

    def _on_picker_finished(self, accepted: bool) -> None:  # noqa: FBT001
        if accepted:
            self.confirm_moment()
        else:
            self.close_moment()
