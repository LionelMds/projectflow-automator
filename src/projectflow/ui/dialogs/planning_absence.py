"""« Absence »: see, change or delete one absence of the planning, or record a new one.

Like « Équipe du planning », the dialog never touches the storage: « Enregistrer » and
« Supprimer » hand a command builder to ``apply`` with a French label (one undo step),
which raises ``ProjectFlowError`` when the edit is refused; the message is shown in the
dialog, which stays open. ``outcome`` then tells the caller what was done.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from typing import Final, Literal, get_args

from PySide6.QtCore import QDate, QSignalBlocker, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QButtonGroup,
    QComboBox,
    QDateEdit,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from projectflow.core.planning.calendar import WorkCalendar
from projectflow.core.planning.changes import DocumentChange, PlanningSnapshot
from projectflow.core.planning.commands import (
    CommandContext,
    calendar_resolver,
    create_absence,
    delete_absence,
    update_absence,
)
from projectflow.core.planning.conflicts import NOON_MINUTE
from projectflow.core.planning.models import (
    ABSENCE_LABELS,
    Absence,
    AbsenceReason,
    DayPart,
    Resource,
)
from projectflow.exceptions import ProjectFlowError
from projectflow.ui.planning.detail import author_text
from projectflow.ui.theme import current_theme, theme_notifier
from projectflow.ui.widgets.industry import button, field, label, rule

AbsenceBuilder = Callable[[CommandContext], tuple[DocumentChange, ...]]
ApplyAbsence = Callable[[AbsenceBuilder, str], None]
"""Runs a builder on a fresh context and applies its changes under the label."""
Outcome = Literal["", "created", "saved", "deleted"]

DIALOG_WIDTH: Final = 460
WEEKDAY_SHORT: tuple[str, ...] = ("lun", "mar", "mer", "jeu", "ven", "sam", "dim")
ARROW: Final = "→"
PART_LABELS: Mapping[DayPart, str] = {
    "day": "Journée",
    "morning": "Matin",
    "afternoon": "Après-midi",
}
PART_SUFFIXES: Mapping[DayPart, str] = {"day": "", "morning": "matin", "afternoon": "après-midi"}
DAY_PARTS: tuple[DayPart, ...] = ("day", "morning", "afternoon")
REASONS: tuple[AbsenceReason, ...] = get_args(AbsenceReason)
ORDER_MESSAGE: Final = "La fin de l'absence précède son début."
NO_PERSON_MESSAGE: Final = "Choisissez une personne."

_APP_ARROW_RE = re.compile(r"url\(([^)]*chevron-down\.svg)\)")


# ---------------------------------------------------------------- helpers ----


def short_day(day: date) -> str:
    """``lun 26.10``."""
    return f"{WEEKDAY_SHORT[day.weekday()]} {day:%d.%m}"


def absence_dates_text(first: date, last: date, part: DayPart = "day") -> str:
    """« lun 26.10 → ven 30.10 », « mar 27.10 · matin »."""
    text = short_day(first) if last == first else f"{short_day(first)} {ARROW} {short_day(last)}"
    suffix = PART_SUFFIXES.get(part, "")
    return f"{text} · {suffix}" if suffix else text


def has_afternoon(calendar: WorkCalendar, day: date) -> bool:
    """Whether ``day`` is worked after its midday split (never on Friday at the workshop)."""
    periods = calendar.periods_of_weekday(day.weekday())
    split = periods[0][1] if len(periods) > 1 else NOON_MINUTE
    return any(end > split for _start, end in periods)


def date_edit_style_sheet() -> str:
    """Give date edits the application's field box and the combo boxes' arrow.

    The application style sheet styles combo and spin boxes, not the date fields.
    """
    theme = current_theme()
    app = QApplication.instance()
    sheet = app.styleSheet() if isinstance(app, QApplication) else ""
    arrow = _APP_ARROW_RE.search(sheet)
    arrow_rule = (
        f"QDateEdit::down-arrow {{ image: url({arrow.group(1)}); width: 12px; height: 12px; }}"
        if arrow is not None
        else ""
    )
    return (
        "QDateEdit {"
        f" background: {theme.rgba('surface', 1.0)}; color: {theme.rgba('text', 1.0)};"
        f" border: 1px solid {theme.rgba('text', 0.34)}; border-radius: 0;"
        " padding: 6px 4px 6px 10px; min-height: 22px; font-size: 14px; }"
        f"QDateEdit:focus {{ border-color: {theme.rgba('accent', 1.0)}; }}"
        f"QDateEdit:disabled {{ color: {theme.rgba('text', 0.45)}; }}"
        "QDateEdit::drop-down { border: 0; width: 26px;"
        " subcontrol-origin: padding; subcontrol-position: right; }"
        f"{arrow_rule}"
    )


def _qdate(day: date) -> QDate:
    return QDate(day.year, day.month, day.day)


def _pydate(day: QDate) -> date:
    return date(day.year(), day.month(), day.day())


def _date_edit(day: date) -> QDateEdit:
    edit = QDateEdit(_qdate(day))
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("dd.MM.yyyy")
    return edit


# ----------------------------------------------------------------- dialog ----


class AbsenceDialog(QDialog):
    """One absence: person, reason, first and last day, part of the day, note.

    With ``absence`` the dialog edits it (« Enregistrer », « Supprimer »); without, it
    records a new one for ``resource_id`` from ``first_day``. ``read_only`` shows the
    absence without any edit (``read_only_message`` says why).
    """

    def __init__(
        self,
        snapshot: PlanningSnapshot,
        apply: ApplyAbsence,
        *,
        absence: Absence | None = None,
        resource_id: str = "",
        first_day: date | None = None,
        read_only: bool = False,
        read_only_message: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._snapshot = snapshot
        self._apply = apply
        self._absence = absence
        self._read_only = read_only
        self._calendar_for = calendar_resolver(snapshot)
        self._loading = True
        self.outcome: Outcome = ""
        self.done_label = ""
        """French label of the edit made (« Modifier l'absence de LB »)."""
        self.absence_id = absence.id if absence is not None else ""
        self.setWindowTitle("Absence" if absence is not None else "Nouvelle absence")
        day = first_day or date.today()
        self._build_ui(read_only_message)
        if absence is not None:
            self._load(
                resource_id=absence.resource_id,
                reason=absence.reason,
                first=absence.first_day,
                last=absence.last_day,
                part=absence.part,
                note=absence.note,
            )
        else:
            self._load(
                resource_id=resource_id, reason="absent", first=day, last=day, part="day", note=""
            )
        self._loading = False
        self._sync()
        theme_notifier().changed.connect(self._restyle)
        self._restyle()

    # ------------------------------------------------------------ public ----

    @property
    def editing(self) -> bool:
        return self._absence is not None

    @property
    def read_only(self) -> bool:
        return self._read_only

    def resource_id(self) -> str:
        data = self.person_combo.currentData()
        return str(data) if data else ""

    def reason(self) -> AbsenceReason:
        data = self.reason_combo.currentData()
        return data if data in REASONS else "absent"

    def first_day(self) -> date:
        return _pydate(self.first_edit.date())

    def last_day(self) -> date:
        return _pydate(self.last_edit.date())

    def part(self) -> DayPart:
        checked = self._parts.checkedButton()
        key = str(checked.property("part_key")) if checked is not None else "day"
        return key if key in DAY_PARTS else "day"

    def note(self) -> str:
        return " ".join(self.note_edit.text().split())

    def part_button(self, part: DayPart) -> QPushButton:
        return self._part_buttons[part]

    def set_part(self, part: DayPart) -> None:
        self._part_buttons[part].setChecked(True)

    def problem(self) -> str:
        """Why the absence cannot be saved as typed, or ""."""
        if not self.resource_id():
            return NO_PERSON_MESSAGE
        if self.last_day() < self.first_day():
            return ORDER_MESSAGE
        return ""

    def save(self) -> bool:
        """Apply the typed absence; True when done (the dialog is then accepted)."""
        if self._read_only:
            return False
        problem = self.problem()
        if problem:
            self._show_error(problem)
            return False
        who = self._who(self.resource_id())
        absence = self._absence
        if absence is None:
            values = {
                "resource_id": self.resource_id(),
                "first_day": self.first_day(),
                "last_day": self.last_day(),
                "reason": self.reason(),
                "part": self.part(),
                "note": self.note(),
            }

            def create(ctx: CommandContext) -> tuple[DocumentChange, ...]:
                return create_absence(ctx, **values)  # type: ignore[arg-type]

            return self._finish(create, f"Ajouter l'absence de {who}", "created")
        fields = self._changed_fields(absence)
        if not fields:
            self.accept()
            return True
        absence_id = absence.id

        def update(ctx: CommandContext) -> tuple[DocumentChange, ...]:
            return update_absence(ctx, absence_id, **fields)

        return self._finish(update, f"Modifier l'absence de {who}", "saved")

    def delete(self) -> bool:
        """Delete the absence shown; True when done (the dialog is then accepted)."""
        absence = self._absence
        if absence is None or self._read_only:
            return False
        absence_id = absence.id

        def remove(ctx: CommandContext) -> tuple[DocumentChange, ...]:
            return delete_absence(ctx, absence_id)

        who = self._who(absence.resource_id)
        return self._finish(remove, f"Supprimer l'absence de {who}", "deleted")

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        # Enter saves from any field; a focused button is pressed instead.
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            focused = self.focusWidget()
            if isinstance(focused, QPushButton) and focused.isEnabled():
                focused.animateClick()
            elif self.save_button.isEnabled():
                self.save()
            event.accept()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------- build ----

    def _build_ui(self, read_only_message: str) -> None:
        self.setMinimumWidth(DIALOG_WIDTH)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        content = QVBoxLayout()
        content.setContentsMargins(28, 24, 28, 20)
        content.setSpacing(16)
        heading = QVBoxLayout()
        heading.setSpacing(2)
        heading.addWidget(label("Planning", "kicker"))
        self.title_label = label(
            "Absence" if self._absence is not None else "Nouvelle absence", "h3"
        )
        heading.addWidget(self.title_label)
        content.addLayout(heading)
        self.read_only_label = label(read_only_message or "Lecture seule.", "small", wrap=True)
        self.read_only_label.setProperty("tone", "error")
        self.read_only_label.setVisible(self._read_only)
        content.addWidget(self.read_only_label)

        self.person_combo = QComboBox()
        self.reason_combo = QComboBox()
        for reason in REASONS:
            self.reason_combo.addItem(ABSENCE_LABELS[reason], reason)
        people = QHBoxLayout()
        people.setSpacing(12)
        people.addWidget(field("Personne", self.person_combo), 1)
        people.addWidget(field("Motif", self.reason_combo), 1)
        content.addLayout(people)

        self.first_edit = _date_edit(date.today())
        self.last_edit = _date_edit(date.today())
        days = QHBoxLayout()
        days.setSpacing(12)
        days.addWidget(field("Du", self.first_edit), 1)
        days.addWidget(field("Au (inclus)", self.last_edit), 1)
        content.addLayout(days)

        parts_row = QHBoxLayout()
        parts_row.setContentsMargins(0, 0, 0, 0)
        parts_row.setSpacing(-1)
        self._parts = QButtonGroup(self)
        self._parts.setExclusive(True)
        self._part_buttons: dict[DayPart, QPushButton] = {}
        for part in DAY_PARTS:
            option = button(PART_LABELS[part], "seg")
            option.setCheckable(True)
            option.setProperty("part_key", part)
            option.setAutoDefault(False)
            self._parts.addButton(option)
            parts_row.addWidget(option)
            self._part_buttons[part] = option
        parts_row.addStretch(1)
        self.parts_field = field(
            "Partie de la journée",
            parts_row,
            help_text="Matin ou après-midi pour une absence d'un seul jour "
            "(le vendredi n'a pas d'après-midi).",
        )
        content.addWidget(self.parts_field)

        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("Remplacé par…, à confirmer…")
        content.addWidget(field("Note", self.note_edit))
        self.author_label = label("", "small", wrap=True)
        content.addWidget(self.author_label)
        self.error_label = label("", "small", tone="error", wrap=True)
        self.error_label.hide()
        content.addWidget(self.error_label)
        root.addLayout(content)
        root.addWidget(rule())

        footer = QHBoxLayout()
        footer.setContentsMargins(20, 10, 20, 10)
        footer.setSpacing(8)
        self.delete_button = button("Supprimer", "danger", icon="trash")
        self.delete_button.setVisible(self._absence is not None)
        self.delete_button.clicked.connect(self.delete)
        footer.addWidget(self.delete_button)
        footer.addStretch(1)
        self.cancel_button = button("Fermer" if self._read_only else "Annuler")
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)
        self.save_button = button("Enregistrer", "primary")
        self.save_button.clicked.connect(self.save)
        self.save_button.setVisible(not self._read_only)
        footer.addWidget(self.save_button)
        root.addLayout(footer)
        for child in self.findChildren(QPushButton):
            child.setAutoDefault(False)

        self.person_combo.currentIndexChanged.connect(self._sync)
        self.first_edit.dateChanged.connect(self._first_changed)
        self.last_edit.dateChanged.connect(self._sync)
        self._parts.buttonClicked.connect(self._part_clicked)

    def _load(
        self,
        *,
        resource_id: str,
        reason: str,
        first: date,
        last: date,
        part: DayPart,
        note: str,
    ) -> None:
        rows = [
            row
            for row in self._snapshot.active_resources()
            if not row.is_subcontractor or row.id == resource_id
        ]
        current = self._snapshot.resources.get(resource_id)
        if current is not None and all(row.id != current.id for row in rows):
            rows.append(current)
        with QSignalBlocker(self.person_combo):
            self.person_combo.clear()
            for row in rows:
                self.person_combo.addItem(_person_text(row), row.id)
            index = self.person_combo.findData(resource_id)
            self.person_combo.setCurrentIndex(index)
        with QSignalBlocker(self.reason_combo):
            self.reason_combo.setCurrentIndex(max(self.reason_combo.findData(reason), 0))
        with QSignalBlocker(self.first_edit), QSignalBlocker(self.last_edit):
            self.first_edit.setDate(_qdate(first))
            self.last_edit.setDate(_qdate(last))
        self._part_buttons[part].setChecked(True)
        self.note_edit.setText(note)
        absence = self._absence
        if absence is not None:
            self.author_label.setText(author_text(absence, datetime.now(UTC)))
        self.author_label.setVisible(bool(self.author_label.text()))

    # ------------------------------------------------------------ events ----

    def _first_changed(self, first: QDate) -> None:
        # Moving the first day past the last one carries the last day along.
        if not self._loading and self.last_edit.date() < first:
            with QSignalBlocker(self.last_edit):
                self.last_edit.setDate(first)
        self._sync()

    def _part_clicked(self, _clicked: QAbstractButton) -> None:
        self._sync()

    def _sync(self) -> None:
        """Offer the half days that make sense, enable « Enregistrer », say what is wrong."""
        first, last = self.first_day(), self.last_day()
        single = first == last
        if not single and not self._loading and self.part() != "day":
            self.set_part("day")
        afternoon = has_afternoon(self._calendar_for(self.resource_id() or None), first)
        if single and not afternoon and self.part() == "afternoon":
            self.set_part("day")
        editable = not self._read_only
        for part, option in self._part_buttons.items():
            allowed = part == "day" or (single and (part != "afternoon" or afternoon))
            option.setEnabled(editable and allowed)
        for widget in (
            self.person_combo,
            self.reason_combo,
            self.first_edit,
            self.last_edit,
            self.note_edit,
        ):
            widget.setEnabled(editable)
        self.delete_button.setEnabled(editable)
        problem = self.problem()
        self.save_button.setEnabled(editable and not problem)
        if self._loading:
            return
        self._show_error(problem if problem == ORDER_MESSAGE else "")

    def _changed_fields(self, absence: Absence) -> dict[str, object]:
        typed: dict[str, object] = {
            "resource_id": self.resource_id(),
            "reason": self.reason(),
            "first_day": self.first_day(),
            "last_day": self.last_day(),
            "part": self.part(),
            "note": self.note(),
        }
        return {name: value for name, value in typed.items() if getattr(absence, name) != value}

    def _finish(self, build: AbsenceBuilder, text: str, outcome: Outcome) -> bool:
        try:
            self._apply(build, text)
        except ProjectFlowError as exc:
            self._show_error(str(exc) or "Modification impossible.")
            return False
        self.outcome = outcome
        self.done_label = text
        self.accept()
        return True

    def _who(self, resource_id: str) -> str:
        resource = self._snapshot.resources.get(resource_id)
        return resource.short_label if resource is not None else "?"

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))

    def _restyle(self) -> None:
        self.setStyleSheet(date_edit_style_sheet())


def _person_text(row: Resource) -> str:
    if row.initials and row.initials != row.name:
        return f"{row.initials} · {row.name}"
    return row.name
