"""« Détail »: the face of the side panel that shows and edits one task.

Top to bottom: the project number (« AAAA-XXXX », empty for an internal task) with the
client of the dossier, the sub-designation (the task's own title: left empty, the
dossier's designation names the task), the type, the start and the end, the deadline
with « Urgent » and night work, who does it (everybody of the planning, by team), the
notes and, at the bottom, the status. A fixed footer tells who changed the task last
and holds « Renvoyer à planifier », « Dupliquer » and « Supprimer ».

There is no Save button: a text saves itself when it loses the focus or on Enter, the
notes 800 ms after the last keystroke, a toggle at once. A click on « Début », « Fin »
or « Échéance » unfolds rolls under it, one row at a time; the value goes when they
fold again (Enter, Escape, a click elsewhere or on the row). Each edit leaves as a
signal that the Planning tab turns into a command; the tab then calls ``set_task``
again with the updated task. A field being edited keeps its value when the task is
refreshed meanwhile.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QRectF,
    QRegularExpression,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFocusEvent,
    QFont,
    QIcon,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QRegularExpressionValidator,
    QResizeEvent,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QButtonGroup,
    QCompleter,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QStylePainter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from projectflow.core.numero import canonical_project_number, is_project_number
from projectflow.core.planning.calendar import WorkCalendar, snap
from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.commands import calendar_resolver
from projectflow.core.planning.conflicts import resource_load
from projectflow.core.planning.models import (
    ABSENCE_LABELS,
    STATUS_LABELS,
    Author,
    PlanningDocument,
    PlanningTask,
    Resource,
    TaskStatus,
)
from projectflow.core.planning.night import elapsed_text, rest_text
from projectflow.core.planning.quick_entry import ProjectInfo, format_days, format_duration
from projectflow.core.planning.timescale import WEEKDAY_NAMES, move_preserving_work
from projectflow.core.planning.walltime import WallTime, wall_date
from projectflow.ui.planning.backlog import FlowLayout, relative_time
from projectflow.ui.planning.wheel_picker import (
    DateTimeWheel,
    DateWheel,
    WheelPicker,
    day_text,
    moment_text,
)
from projectflow.ui.theme import (
    HEADING_FAMILY,
    Theme,
    body_font,
    current_theme,
    set_icon,
    theme_notifier,
)
from projectflow.ui.widgets.industry import button, label, rule, set_tone

DetailMode = Literal["empty", "task", "multi"]
DetailAction = Literal["duplicate", "unschedule", "delete"]
PickerName = Literal["start", "end", "deadline"]

MULTI_SELECTION = ""
"""Task id of the signals emitted while several tasks are selected: « every one of them »."""
NOTES_DEBOUNCE_MS = 800
NOTES_HEIGHT = 64
"""About three lines of notes."""
SWATCH_SIZE = 12
FIELD_SPACING = 16
TYPE_COLUMNS = 3
PEOPLE_COLUMNS = 5
PERSON_HEIGHT = 44
"""Initials buttons: about square, an easy target (set through the style sheet)."""
DATE_ROW_HEIGHT = 44
DEADLINE_ROW_HEIGHT = 40
ROW_PADDING = 12
CLEAR_SIZE = 24
"""The cross that clears the deadline, inside its row."""
CHIP_GAP = 6
CHIP_TEXT_MARGIN = 24
"""Padding and border of a chip around its text (see the ``chip`` button style)."""
NUMBER_WIDTH = 176
STATUSES: tuple[TaskStatus, ...] = ("provisional", "confirmed", "done")
EMPTY_TEXT = "Double-cliquez une barre ou une carte pour voir et modifier la tâche."
ABSENT = "absent"
PARTLY_ABSENT = "absent en partie"
NIGHT_TEXT = "Travail de nuit"
NIGHT_TOOLTIP = (
    "Nuit du lundi au jeudi soir : heures exactes de 18:00 à 06:30, durée réelle, "
    "8 h de repos avant et après"
)
URGENT_TEXT = "! Urgent"
MIXED = "—"
NO_DEADLINE = "Aucune"
NUMBER_PLACEHOLDER = "AAAA-XXXX"
NUMBER_TYPING = QRegularExpression(r"\d{0,4}(-\d{0,6}(-[0-9A-Za-z-]{0,10})?)?")
"""What the number field lets type: « 2026-5231 », « 2026-5198-2 », or « 5231 » alone."""
NUMBER_ERROR = "Numéro attendu : AAAA-XXXX (vide pour une tâche interne)"
TITLE_PLACEHOLDER = "Nom libre de la tâche (sinon : désignation du projet)"
INTERNAL_TASK = "Tâche interne"
UNSCHEDULED_TEXT = "Sans date : glisser la carte sur une ligne du planning pour la poser."
END_ERROR = "Fin avant le début : rien n'est enregistré."
OTHER_PEOPLE = "Autres"
"""Group of the people of a task who are no longer rows of the planning."""


# -------------------------------------------------------------- helpers ----


def availability(
    snapshot: PlanningSnapshot,
    resource: Resource,
    task: PlanningTask,
    calendar: WorkCalendar,
) -> str:
    """How free a person is over the days of a scheduled task.

    ``libre``, ``occupé à 62 %``, ``absent`` the whole time, and when away some of the
    days only, ``absent mercredi 21.10`` (one day) or ``absent en partie``.
    """
    if task.start is None or task.end is None:
        return ""
    first, last = wall_date(task.start), wall_date(task.end - 1)
    planned, available = resource_load(snapshot, resource.id, first, last, calendar)
    if resource.id in task.assignments:
        own = (
            task.end - task.start if task.off_hours else calendar.work_between(task.start, task.end)
        )
        planned -= own
    if available <= 0:
        return ABSENT
    days = calendar.days_between(first, last)
    if available < sum(calendar.worked_minutes(day) for day in days):
        away = [
            day
            for day in days
            if resource_load(snapshot, resource.id, day, day, calendar)[1]
            < calendar.worked_minutes(day)
        ]
        if len(away) == 1:
            day = away[0]
            return f"{ABSENT} {WEEKDAY_NAMES[day.weekday()]} {day:%d.%m}"
        return PARTLY_ABSENT
    if planned <= 0:
        return "libre"
    return f"occupé à {round(100 * planned / available)} %"


def absence_reasons(
    snapshot: PlanningSnapshot, resource: Resource, first: date, last: date
) -> list[str]:
    """Reasons a person is away between ``first`` and ``last``: « Vacances », « École … »."""
    reasons = [
        absence.label
        for absence in snapshot.live_absences()
        if absence.resource_id == resource.id
        and absence.first_day <= last
        and absence.last_day >= first
    ]
    span = min((last - first).days + 1, 7)
    weekdays = {(first + timedelta(days=offset)).weekday() for offset in range(span)}
    reasons.extend(
        ABSENCE_LABELS.get(off.reason, off.reason)
        for off in resource.recurring_off
        if off.weekday in weekdays
    )
    return list(dict.fromkeys(reasons))


def person_note(
    snapshot: PlanningSnapshot,
    resource: Resource,
    task: PlanningTask,
    calendar: WorkCalendar,
) -> tuple[bool, str]:
    """Whether a person is away over the task, and what their button says about it.

    « Absent · Vacances », « Absent mercredi 21.10 · École professionnelle », « Libre »,
    « Occupé à 62 % »; nothing for a task without dates.
    """
    if task.start is None or task.end is None:
        return False, ""
    state = availability(snapshot, resource, task, calendar)
    away = state.startswith(ABSENT)
    parts = [state[:1].upper() + state[1:]]
    if away:
        first, last = wall_date(task.start), wall_date(task.end - 1)
        parts.extend(
            reason
            for reason in absence_reasons(snapshot, resource, first, last)
            if reason.casefold() != state.casefold()
        )
    return away, " · ".join(parts)


def author_text(task: PlanningDocument, now: datetime) -> str:
    """« Modifié par JD · BUREAU-2 · il y a 3 min » (or « Créé par … » when never changed)."""
    changed = bool(task.updated_at) and task.updated_at != task.created_at
    author: Author = task.updated_by if changed else task.created_by
    when = relative_time(task.updated_at if changed else task.created_at, now)
    verb = "Modifié" if changed else "Créé"
    if author.label:
        return f"{verb} par {author.label}" + (f" · {when}" if when else "")
    return f"{verb} {when}" if when else ""


def people_count_text(count: int, *, foreseen: bool = False, common: bool = False) -> str:
    """« 2 sélectionnés », « 1 pressenti » (a card), « Aucun en commun » (a selection)."""
    if common:
        return f"{count} en commun" if count else "Aucun en commun"
    word = "pressenti" if foreseen else "sélectionné"
    if not count:
        return f"Aucun {word}"
    return f"{count} {word}{'s' if count > 1 else ''}"


def _popup_visible(completer: QCompleter) -> bool:
    popup = completer.popup()
    return popup is not None and popup.isVisible()


def _swatch(color: str) -> QIcon:
    ratio = 2
    pixmap = QPixmap(SWATCH_SIZE * ratio, SWATCH_SIZE * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(QColor(color))
    painter = QPainter(pixmap)
    painter.setPen(QPen(QColor(color).darker(130)))
    painter.drawRect(QRectF(0, 0, SWATCH_SIZE - 0.5, SWATCH_SIZE - 0.5))
    painter.end()
    return QIcon(pixmap)


def _panel_style(theme: Theme) -> str:
    """Roles of the panel that the application style sheet leaves out (tokens only)."""
    t = theme.tokens
    return f"""
QLineEdit[role="project-number"] {{
  font-family: "{HEADING_FAMILY}"; font-weight: 700; font-size: 30px; color: {t["text"]};
  background: transparent; border: 0; border-bottom: 2px solid {t["accent"]};
  padding: 0 0 2px 0; min-height: 0;
}}
QLineEdit[role="project-number"]:read-only {{
  background: transparent; border-bottom-color: {theme.rgba("text", 0.30)};
}}
QLineEdit[role="project-number"][tone="error"] {{
  color: {t["err-ink"]}; border-bottom-color: {t["err"]};
}}
QLineEdit[role="subtitle"] {{ font-size: 16px; font-weight: 600; }}
QPushButton[variant="chip"][panel="true"] {{ min-height: 22px; }}
QPushButton[variant="chip"][tone="error"]:checked {{
  color: {t["err-ink"]}; background: {theme.rgba("err", 0.08)}; border-color: {t["err"]};
}}
QPushButton[variant="chip"][panel="true"][role="person"] {{
  font-family: "{HEADING_FAMILY}"; font-weight: 600; font-size: 15px; padding: 0 2px;
  min-height: {PERSON_HEIGHT - 2}px; max-height: {PERSON_HEIGHT - 2}px;
}}
QPushButton[variant="chip"][panel="true"][role="row-chip"] {{
  min-height: {DEADLINE_ROW_HEIGHT - 10}px; max-height: {DEADLINE_ROW_HEIGHT - 10}px;
}}
QPushButton[role="person"][absent="true"] {{ color: {t["neutral-600"]}; }}
QPushButton[role="person"][absent="true"]:checked {{ color: {t["accent-800"]}; }}
QToolButton[role="row-clear"] {{
  border: 0; background: transparent; padding: 0;
  min-width: {CLEAR_SIZE}px; max-width: {CLEAR_SIZE}px;
  min-height: {CLEAR_SIZE}px; max-height: {CLEAR_SIZE}px;
}}
QToolButton[role="row-clear"]:hover {{ background: {theme.rgba("text", 0.07)}; }}
QWidget[role="detail-footer"] QPushButton {{ min-height: 22px; }}
"""


# --------------------------------------------------------------- widgets ----


class FitButton(QPushButton):
    """Chip sharing a row equally with others: a text too long is cut with « … ».

    With ``share=False`` it keeps the width of its whole text instead (a wrapping row).
    """

    def __init__(self, text: str, parent: QWidget | None = None, *, share: bool = True) -> None:
        super().__init__(text, parent)
        self._full_text = text
        self._tooltip = ""
        self._share = share
        self.setProperty("variant", "chip")
        self.setProperty("panel", "true")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        horizontal = QSizePolicy.Policy.Ignored if share else QSizePolicy.Policy.Fixed
        self.setSizePolicy(horizontal, QSizePolicy.Policy.Fixed)

    def full_text(self) -> str:
        return self._full_text

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self._fit()

    def set_tooltip(self, text: str) -> None:
        self._tooltip = text
        self._fit()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        margin = CHIP_TEXT_MARGIN + (self.iconSize().width() + 6 if not self.icon().isNull() else 0)
        shown = self._full_text
        if self._share:
            shown = self.fontMetrics().elidedText(
                self._full_text, Qt.TextElideMode.ElideRight, max(self.width() - margin, 0)
            )
        if shown != self.text():
            self.setText(shown)
        cut = shown != self._full_text
        self.setToolTip(self._tooltip or (self._full_text if cut else ""))


class PersonToggle(FitButton):
    """Initials of one person to tick; hatched while they are away over the task."""

    def __init__(self, resource_id: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.resource_id = resource_id
        self.absent = False
        self.setProperty("role", "person")
        self.setProperty("absent", "false")

    def set_absent(self, *, absent: bool) -> None:
        if absent == self.absent:
            return
        self.absent = absent
        self.setProperty("absent", "true" if absent else "false")
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QStylePainter(self)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        painter.drawControl(QStyle.ControlElement.CE_PushButtonBevel, option)
        if self.absent:
            hatch = QBrush(current_theme().color("text", 0.16), Qt.BrushStyle.BDiagPattern)
            painter.fillRect(self.rect().adjusted(1, 1, -1, -1), hatch)
        painter.drawControl(QStyle.ControlElement.CE_PushButtonLabel, option)
        painter.end()


class DateRow(QAbstractButton):
    """« Début ……… lun 16 nov · 07:00 »: a row that unfolds its rolls when clicked.

    The value turns accent while the rolls are open, error when it cannot be saved.
    """

    def __init__(
        self,
        caption: str,
        *,
        height: int = DATE_ROW_HEIGHT,
        framed: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setText(caption)
        self._value = ""
        self._open = False
        self._error = False
        self._framed = framed
        self._trailing: QWidget | None = None
        self.setFixedHeight(height)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(caption)

    def value(self) -> str:
        return self._value

    def set_value(self, text: str) -> None:
        if text != self._value:
            self._value = text
            self.setAccessibleDescription(text)
            self.update()

    def is_open(self) -> bool:
        return self._open

    def set_open(self, *, open_: bool) -> None:
        if open_ != self._open:
            self._open = open_
            self.update()

    def has_error(self) -> bool:
        return self._error

    def set_error(self, *, error: bool) -> None:
        if error != self._error:
            self._error = error
            self.update()

    def set_trailing(self, widget: QWidget) -> None:
        """Put a small control inside the row, on its right (the cross of the deadline)."""
        widget.setParent(self)
        self._trailing = widget
        self._place_trailing()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(160, self.height())

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place_trailing()

    def _place_trailing(self) -> None:
        widget = self._trailing
        if widget is not None:
            size = widget.sizeHint().expandedTo(QSize(CLEAR_SIZE, CLEAR_SIZE))
            widget.setGeometry(
                self.width() - ROW_PADDING // 2 - size.width(),
                (self.height() - size.height()) // 2,
                size.width(),
                size.height(),
            )

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            self.click()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        theme = current_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rect = QRectF(self.rect())
        enabled = self.isEnabled()
        if enabled and self.underMouse():
            painter.fillRect(rect, theme.color("text", 0.05))
        focused = self.hasFocus() and enabled
        if self._framed or focused:
            border = rect.adjusted(0.5, 0.5, -0.5, -0.5)
            color = theme.color("accent") if focused or self._open else theme.color("text", 0.34)
            pen = QPen(color)
            pen.setWidthF(1.0)
            painter.setPen(pen)
            painter.drawRect(border)
        inner = rect.adjusted(ROW_PADDING, 0, -ROW_PADDING, 0)
        trailing = self._trailing
        if trailing is not None and trailing.isVisible():
            inner.setRight(trailing.x() - ROW_PADDING // 2)
        painter.setFont(body_font(14))
        painter.setPen(theme.color("neutral-800") if enabled else theme.color("text", 0.45))
        left = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        painter.drawText(inner, int(left), self.text())
        caption_width = painter.fontMetrics().horizontalAdvance(self.text()) + ROW_PADDING
        painter.setFont(body_font(16, QFont.Weight.DemiBold))
        if not enabled:
            color = theme.color("text", 0.45)
        elif self._error:
            color = theme.color("err-ink")
        elif self._open:
            color = theme.color("accent")
        else:
            color = theme.color("text")
        painter.setPen(color)
        value = painter.fontMetrics().elidedText(
            self._value, Qt.TextElideMode.ElideRight, max(int(inner.width() - caption_width), 0)
        )
        right = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight
        painter.drawText(inner, int(right), value)
        painter.end()


class _Box(QWidget):
    """Hairline frame around rows and their rolls (a wireframe object, no fill)."""

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


def _tone(widget: QWidget, tone: str | None) -> None:
    """Set the tone of ``widget``, restyling it only when the tone changes."""
    if (widget.property("tone") or "") != (tone or ""):
        set_tone(widget, tone)


def _caption(text: str) -> QLabel:
    return label(text, "field")


def _stack(*widgets: QWidget, spacing: int = 5) -> QWidget:
    block = QWidget()
    layout = QVBoxLayout(block)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for widget in widgets:
        layout.addWidget(widget)
    return block


def _head(left: QWidget, right: QWidget) -> QWidget:
    """Put something on the right of a caption (« QUI ……… 2 sélectionnés »)."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    layout.addWidget(left, 0, Qt.AlignmentFlag.AlignBottom)
    layout.addStretch(1)
    layout.addWidget(right, 0, Qt.AlignmentFlag.AlignBottom)
    return row


def _clear_layout(layout: QVBoxLayout | QGridLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget() if item is not None else None
        if widget is not None:
            widget.hide()
            widget.deleteLater()


# ----------------------------------------------------------------- panel ----


class DetailPanel(QWidget):
    """Face « Détail » of the side panel: one task, several tasks, or nothing."""

    field_changed = Signal(str, str, object)
    people_changed = Signal(str, list, list)
    dates_changed = Signal(str, int, int)
    status_changed = Signal(str, str)
    action_requested = Signal(str, str)
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mode: DetailMode = "empty"
        self._task: PlanningTask | None = None
        self._snapshot: PlanningSnapshot | None = None
        self._calendar: WorkCalendar | None = None
        self._project: ProjectInfo | None = None
        self._read_only = False
        self._now: datetime | None = None
        self._multi_count = 0
        self._common: dict[str, object] = {}
        self._sent: dict[str, object] = {}
        self._loading = False
        self._projects: list[ProjectInfo] = []
        self._project_by_text: dict[str, str] = {}
        self._open: PickerName | None = None
        self._rolled = False
        self._watching_clicks = False
        self._type_signature: tuple[object, ...] = ()
        self._type_keys: list[str] = []
        self._people_signature: tuple[object, ...] = ()
        self._person_buttons: dict[str, PersonToggle] = {}
        self._build()
        theme_notifier().changed.connect(self._on_theme)
        self._on_theme()
        self.clear()

    # -- construction ---------------------------------------------------------

    def _build(self) -> None:
        self.back_button = button("‹ À planifier", "ghost", size="sm")  # noqa: RUF001
        self.back_button.setToolTip("Revenir à « À planifier » (Échap)")
        self.back_button.clicked.connect(self.back_requested)
        self.header_box = QWidget()
        header = QHBoxLayout(self.header_box)
        header.setContentsMargins(14, 10, 10, 6)
        header.addWidget(label("Détail", "caps"), 1)
        header.addWidget(self.back_button)

        self.empty_label = label(EMPTY_TEXT, "muted", wrap=True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.multi_label = label("", "h5", wrap=True)
        self.multi_hint = label("", "small", wrap=True)
        self.read_only_label = label(
            "Planning en lecture seule sur ce poste : rien ne peut être modifié.",
            "small",
            tone="error",
            wrap=True,
        )

        self._build_number()
        self._build_title()
        self._build_types()
        self._build_dates()
        self._build_deadline()
        self._build_people()

        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText("Matériel, accès chantier, grue…")
        self.notes_edit.setFixedHeight(NOTES_HEIGHT)
        self.notes_edit.setTabChangesFocus(True)
        self.notes_edit.setAccessibleName("Notes")
        self.notes_edit.textChanged.connect(self._on_notes_edited)
        self.notes_edit.installEventFilter(self)
        self._notes_timer = QTimer(self)
        self._notes_timer.setSingleShot(True)
        self._notes_timer.setInterval(NOTES_DEBOUNCE_MS)
        self._notes_timer.timeout.connect(self._commit_notes)
        self.notes_block = _stack(_caption("Notes"), self.notes_edit)

        self.status_buttons = QButtonGroup(self)
        self.status_buttons.setExclusive(True)
        status_box = QWidget()
        status_layout = QHBoxLayout(status_box)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(0)
        for index, status in enumerate(STATUSES):
            choice = button(STATUS_LABELS[status], "seg")
            choice.setCheckable(True)
            choice.setProperty("status", status)
            self.status_buttons.addButton(choice, index)
            status_layout.addWidget(choice, 1)
        self.status_buttons.idClicked.connect(self._commit_status)
        self.status_mixed = label("", "small")
        self.status_block = _stack(_head(_caption("Statut"), self.status_mixed), status_box)

        body = QWidget()
        body.setProperty("pfRoot", "true")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(14, 12, 14, 16)
        body_layout.setSpacing(FIELD_SPACING)
        for widget in (
            self.empty_label,
            self.read_only_label,
            self.multi_label,
            self.multi_hint,
            self.number_block,
            self.title_block,
            self.type_block,
            self.dates_block,
            self.deadline_block,
            self.night_block,
            self.people_block,
            self.notes_block,
        ):
            body_layout.addWidget(widget)
        # The status stays at the bottom of the content, however short the task.
        body_layout.addStretch(1)
        body_layout.addWidget(self.status_block)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setWidget(body)

        self._build_footer()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.header_box)
        root.addWidget(self.scroll_area, 1)
        root.addWidget(self.footer)

    def _build_number(self) -> None:
        self.number_edit = QLineEdit()
        self.number_edit.setProperty("role", "project-number")
        self.number_edit.setPlaceholderText(NUMBER_PLACEHOLDER)
        self.number_edit.setAccessibleName("Numéro de projet")
        self.number_edit.setValidator(QRegularExpressionValidator(NUMBER_TYPING, self.number_edit))
        self.number_edit.setFixedWidth(NUMBER_WIDTH)
        self.number_edit.editingFinished.connect(self._commit_number)
        self.number_edit.installEventFilter(self)
        # The list shows « 2026-5231 · client · désignation » and writes the number alone.
        self._number_model = QStandardItemModel(self)
        self._number_completer = QCompleter(self._number_model, self.number_edit)
        self._number_completer.setCompletionRole(Qt.ItemDataRole.UserRole)
        self._number_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._number_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._number_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._number_completer.setMaxVisibleItems(8)
        self.number_edit.setCompleter(self._number_completer)
        self._number_completer.activated.connect(self._on_project_chosen)
        self.client_label = label("", "muted")
        self.client_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        self.client_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        line = QWidget()
        layout = QHBoxLayout(line)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.number_edit, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addWidget(self.client_label, 1, Qt.AlignmentFlag.AlignBottom)
        self.number_block = _stack(_caption("N° de projet"), line, spacing=2)

    def _build_title(self) -> None:
        self.title_edit = QLineEdit()
        self.title_edit.setProperty("role", "subtitle")
        self.title_edit.setPlaceholderText(TITLE_PLACEHOLDER)
        self.title_edit.setToolTip(TITLE_PLACEHOLDER)
        self.title_edit.setAccessibleName("Sous-désignation")
        self.title_edit.editingFinished.connect(self._commit_title)
        self.title_edit.installEventFilter(self)
        self.project_label = label("", "small", wrap=True)
        self.title_block = _stack(_caption("Sous-désignation"), self.title_edit, self.project_label)

    def _build_types(self) -> None:
        self.type_buttons = QButtonGroup(self)
        self.type_buttons.setExclusive(True)
        self.type_buttons.idClicked.connect(self._commit_type)
        self.type_box = QWidget()
        self._type_holder = QVBoxLayout(self.type_box)
        self._type_holder.setContentsMargins(0, 0, 0, 0)
        self.type_mixed = label("", "small")
        self.type_block = _stack(_head(_caption("Type"), self.type_mixed), self.type_box)

    def _build_dates(self) -> None:
        self.start_row = DateRow("Début")
        self.end_row = DateRow("Fin")
        self.start_picker = DateTimeWheel()
        self.start_picker.setAccessibleName("Début : jour, heure, minutes")
        self.end_picker = DateTimeWheel()
        self.end_picker.setAccessibleName("Fin : jour, heure, minutes")
        self.dates_box = _Box()
        for widget in (self.start_row, self.start_picker, rule(), self.end_row, self.end_picker):
            self.dates_box.box.addWidget(widget)
        self.duration_label = label("", "small")
        self.duration_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.unscheduled_label = label(UNSCHEDULED_TEXT, "small", wrap=True)
        self.dates_block = _stack(self.dates_box, self.duration_label, self.unscheduled_label)
        self.start_row.clicked.connect(lambda: self._toggle_picker("start"))
        self.end_row.clicked.connect(lambda: self._toggle_picker("end"))

    def _build_deadline(self) -> None:
        self.deadline_row = DateRow("Échéance", height=DEADLINE_ROW_HEIGHT, framed=True)
        self.deadline_row.clicked.connect(lambda: self._toggle_picker("deadline"))
        self.clear_deadline_button = QToolButton()
        self.clear_deadline_button.setProperty("role", "row-clear")
        self.clear_deadline_button.setCursor(Qt.CursorShape.PointingHandCursor)
        set_icon(self.clear_deadline_button, "x", "neutral-800", 14)
        self.clear_deadline_button.setToolTip("Retirer l'échéance")
        self.clear_deadline_button.setAccessibleName("Retirer l'échéance")
        self.clear_deadline_button.clicked.connect(self._clear_deadline)
        self.deadline_row.set_trailing(self.clear_deadline_button)
        self.urgent_button = button(URGENT_TEXT, "chip")
        self.urgent_button.setCheckable(True)
        self.urgent_button.setProperty("panel", "true")
        self.urgent_button.setProperty("tone", "error")
        self.urgent_button.setProperty("role", "row-chip")
        self.urgent_button.setAccessibleName("Urgent")
        self.urgent_button.clicked.connect(self._commit_urgent)
        line = QWidget()
        layout = QHBoxLayout(line)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(CHIP_GAP)
        layout.addWidget(self.deadline_row, 1)
        layout.addWidget(self.urgent_button)
        self.deadline_picker = DateWheel()
        self.deadline_picker.setAccessibleName("Échéance : jour")
        self.deadline_picker_box = _Box()
        self.deadline_picker_box.box.addWidget(self.deadline_picker)
        self.deadline_block = _stack(line, self.deadline_picker_box, spacing=0)

        self.night_button = button(NIGHT_TEXT, "chip", icon="moon")
        self.night_button.setCheckable(True)
        self.night_button.setProperty("panel", "true")
        self.night_button.setToolTip(NIGHT_TOOLTIP)
        self.night_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.night_button.clicked.connect(self._commit_night)
        self.rest_label = label("", "small", wrap=True)
        self.night_block = _stack(self.night_button, self.rest_label)

        for picker in (self.start_picker, self.end_picker, self.deadline_picker):
            picker.changed.connect(self._on_picker_rolled)
            picker.finished.connect(self._on_picker_finished)
            picker.installEventFilter(self)
        self.start_picker.hide()
        self.end_picker.hide()
        self.deadline_picker_box.hide()

    def _build_people(self) -> None:
        self.people_count = label("", "small")
        self.people_box = QWidget()
        self.people_layout = QVBoxLayout(self.people_box)
        self.people_layout.setContentsMargins(0, 0, 0, 0)
        self.people_layout.setSpacing(4)
        self.people_block = _stack(
            _head(label("Qui", "caps"), self.people_count), self.people_box, spacing=8
        )

    def _build_footer(self) -> None:
        self.author_label = label("", "small", wrap=True)
        self.unschedule_button = button("Renvoyer à planifier", size="sm")
        self.unschedule_button.setToolTip(
            "Retirer les dates : la tâche revient dans « À planifier »"
        )
        self.unschedule_button.clicked.connect(lambda: self._request_action("unschedule"))
        self.duplicate_button = button("Dupliquer", size="sm")
        self.duplicate_button.clicked.connect(lambda: self._request_action("duplicate"))
        self.delete_button = button("Supprimer", "danger", size="sm")
        self.delete_button.setToolTip("Supprimer la tâche pour tous (Maj+Suppr)")
        self.delete_button.clicked.connect(lambda: self._request_action("delete"))
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)
        for action in (self.unschedule_button, self.duplicate_button):
            actions.addWidget(action)
        actions.addStretch(1)
        actions.addWidget(self.delete_button)
        self.footer = QWidget()
        self.footer.setProperty("role", "detail-footer")
        layout = QVBoxLayout(self.footer)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(rule())
        inner = QVBoxLayout()
        inner.setContentsMargins(14, 8, 14, 10)
        inner.setSpacing(6)
        inner.addWidget(self.author_label)
        inner.addLayout(actions)
        layout.addLayout(inner)

    # -- public API -----------------------------------------------------------

    def mode(self) -> DetailMode:
        return self._mode

    def task_id(self) -> str:
        """Id of the task shown, empty when nothing or several tasks are shown."""
        return self._task.id if self._mode == "task" and self._task is not None else ""

    def is_read_only(self) -> bool:
        return self._read_only

    def open_picker(self) -> PickerName | None:
        """Return the rolls unfolded: « start », « end », « deadline », or None."""
        return self._open

    def type_chips(self) -> list[FitButton]:
        """Type toggles, in the order of the shared list."""
        return [chip for chip in self.type_buttons.buttons() if isinstance(chip, FitButton)]

    def person_buttons(self) -> list[PersonToggle]:
        """Return the initials of everybody of the planning, team after team."""
        return list(self._person_buttons.values())

    def person_button(self, resource_id: str) -> PersonToggle | None:
        return self._person_buttons.get(resource_id)

    def team_titles(self) -> list[str]:
        """Names of the teams shown over the initials, in order."""
        titles: list[str] = []
        for index in range(self.people_layout.count()):
            item = self.people_layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if isinstance(widget, QLabel):
                titles.append(widget.text())
        return titles

    def set_header_visible(self, *, visible: bool) -> None:
        """Show the « DÉTAIL » row and its back button, or hide it under the tabs."""
        self.header_box.setVisible(visible)

    def set_project_choices(self, projects: Sequence[ProjectInfo]) -> None:
        """Dossiers offered by the completion of the project number."""
        self._projects = list(projects)
        self._project_by_text = {}
        self._number_model.clear()
        for project in self._projects:
            parts = [project.number, project.client, project.designation]
            text = " · ".join(part for part in parts if part)
            self._project_by_text[text] = project.number
            self._project_by_text[project.number] = project.number
            item = QStandardItem(text)
            item.setData(project.number, Qt.ItemDataRole.UserRole)
            item.setEditable(False)
            self._number_model.appendRow(item)

    def set_task(
        self,
        task: PlanningTask,
        snapshot: PlanningSnapshot,
        *,
        calendar: WorkCalendar,
        project: ProjectInfo | None = None,
        read_only: bool = False,
        now: datetime | None = None,
    ) -> None:
        """Show ``task``; a refresh of the same task keeps what is being typed or rolled."""
        same = self._mode == "task" and self._task is not None and self._task.id == task.id
        if not same:
            self.flush()
        self._mode = "task"
        self._task = task
        self._snapshot = snapshot
        self._calendar = calendar
        self._project = project
        self._read_only = read_only
        self._now = now
        self._sent = {}
        if read_only:
            self._close_picker(commit=False)
        self._populate_task(keep_edits=same)

    def set_multi(
        self,
        count: int,
        common: Mapping[str, object] | None = None,
        *,
        snapshot: PlanningSnapshot | None = None,
        read_only: bool = False,
    ) -> None:
        """Show a selection of ``count`` tasks; ``common`` holds the values they share.

        Known keys: ``task_type``, ``status``, ``urgent`` and ``resource_ids`` (people on
        every selected task). Edits are emitted with ``MULTI_SELECTION`` as task id; a
        value the tasks do not share shows « — ».
        """
        self.flush()
        self._mode = "multi"
        self._task = None
        self._multi_count = count
        self._common = dict(common or {})
        if snapshot is not None:
            self._snapshot = snapshot
        self._read_only = read_only
        self._sent = {}
        self._populate_multi()

    def clear(self) -> None:
        self.flush()
        self._mode = "empty"
        self._task = None
        self._sent = {}
        self._show_blocks(set())
        self.empty_label.show()

    def flush(self) -> None:
        """Save at once what is being edited: texts, rolls left open, waiting notes."""
        if self._mode != "task" or self._task is None:
            self._notes_timer.stop()
            self._close_picker(commit=False)
            return
        self._close_picker()
        if self.title_edit.isModified():
            self._commit_title()
        if self.number_edit.isModified():
            self._commit_number()
        if self._notes_timer.isActive():
            self._notes_timer.stop()
            self._commit_notes()

    # -- populating -----------------------------------------------------------

    def _blocks(self) -> tuple[QWidget, ...]:
        return (
            self.empty_label,
            self.read_only_label,
            self.multi_label,
            self.multi_hint,
            self.number_block,
            self.title_block,
            self.type_block,
            self.dates_block,
            self.deadline_block,
            self.night_block,
            self.people_block,
            self.notes_block,
            self.status_block,
            self.footer,
        )

    def _show_blocks(self, visible: set[QWidget]) -> None:
        for widget in self._blocks():
            widget.setVisible(widget in visible)

    def _populate_task(self, *, keep_edits: bool) -> None:
        task = self._task
        snapshot = self._snapshot
        if task is None or snapshot is None:
            return
        self._loading = True
        try:
            visible: set[QWidget] = {
                self.number_block,
                self.title_block,
                self.type_block,
                self.dates_block,
                self.deadline_block,
                self.night_block,
                self.people_block,
                self.notes_block,
                self.status_block,
                self.footer,
            }
            if self._read_only:
                visible.add(self.read_only_label)
            self._show_blocks(visible)
            self._fill_number(task, keep=keep_edits)
            self._set_line(self.title_edit, task.title, keep=keep_edits)
            designation = self._dossier_designation(task)
            self.project_label.setText(f"Projet : {designation}")
            self.project_label.setVisible(bool(designation))
            self._fill_types(task.task_type)
            self.type_mixed.clear()
            self._fill_dates(task)
            self.deadline_row.show()
            self._fill_deadline(task.deadline)
            self.urgent_button.setText(URGENT_TEXT)
            self.urgent_button.setChecked(task.urgent)
            self.night_button.setChecked(task.off_hours)
            night = task.off_hours and task.start is not None and task.end is not None
            self.rest_label.setVisible(night)
            if night and task.start is not None and task.end is not None:
                self.rest_label.setText(rest_text(task.start, task.end))
            self._fill_people(
                list(task.assignments),
                task,
                count_text=people_count_text(len(task.assignments), foreseen=not task.scheduled),
            )
            typing_notes = self.notes_edit.hasFocus() or self._notes_timer.isActive()
            if not (keep_edits and typing_notes) and self.notes_edit.toPlainText() != task.notes:
                self.notes_edit.setPlainText(task.notes)
            self._check_status(task.status)
            self.status_mixed.clear()
            self.author_label.setText(author_text(task, self._now or datetime.now(UTC)))
            self.author_label.show()
            self._fill_actions(task)
            self._apply_read_only()
        finally:
            self._loading = False

    def _populate_multi(self) -> None:
        self._loading = True
        try:
            visible: set[QWidget] = {
                self.multi_label,
                self.multi_hint,
                self.type_block,
                self.deadline_block,
                self.people_block,
                self.status_block,
                self.footer,
            }
            if self._read_only:
                visible.add(self.read_only_label)
            self._show_blocks(visible)
            self.multi_label.setText(f"{self._multi_count} tâches sélectionnées")
            self.multi_hint.setText(
                f"Les changements s'appliquent aux {self._multi_count} tâches à la fois."
            )
            common_type = self._common.get("task_type")
            self._fill_types(common_type if isinstance(common_type, str) else None)
            self.type_mixed.setText("" if isinstance(common_type, str) else MIXED)
            self.deadline_row.hide()
            self.clear_deadline_button.hide()
            urgent = self._common.get("urgent")
            self.urgent_button.setChecked(urgent is True)
            self.urgent_button.setText(
                URGENT_TEXT if isinstance(urgent, bool) else f"{URGENT_TEXT} {MIXED}"
            )
            ids = self._common.get("resource_ids", ())
            people = [str(item) for item in ids] if isinstance(ids, (list, tuple)) else []
            self._fill_people(people, None, count_text=people_count_text(len(people), common=True))
            status = self._common.get("status")
            self._check_status(status if isinstance(status, str) else None)
            self.status_mixed.setText("" if isinstance(status, str) else MIXED)
            self.author_label.hide()
            self.unschedule_button.show()
            self.duplicate_button.show()
            self._apply_read_only()
        finally:
            self._loading = False

    @staticmethod
    def _set_line(edit: QLineEdit, text: str, *, keep: bool) -> None:
        if keep and edit.hasFocus() and edit.isModified():
            return
        if edit.text() != text:
            edit.setText(text)
        edit.setModified(False)

    def _today(self) -> date:
        return (self._now or datetime.now(UTC)).astimezone().date()

    def _fill_number(self, task: PlanningTask, *, keep: bool) -> None:
        editing = keep and self.number_edit.hasFocus() and self.number_edit.isModified()
        self._set_line(self.number_edit, task.project_number, keep=keep)
        if not editing:
            self._show_number_error(error=False)
        project = self._project
        client = (project.client if project is not None else "") or task.project_cache.client
        self.client_label.setText(client if task.project_number else INTERNAL_TASK)

    def _fill_types(self, selected: str | None) -> None:
        snapshot = self._snapshot
        theme = current_theme()
        kinds = list(snapshot.meta.task_types) if snapshot is not None else []
        if snapshot is not None and selected and all(kind.key != selected for kind in kinds):
            # A type removed from the shared list shows as the one its bar takes.
            selected = snapshot.meta.task_type(selected).key
        signature = (theme.key, *((k.key, k.label, k.color, k.color_dark) for k in kinds))
        if signature != self._type_signature:
            # Rebuilt only when the shared list (or the theme) changes: a refresh caused by
            # a click on a type must not delete the chip being clicked.
            self._type_signature = signature
            for chip in self.type_buttons.buttons():
                self.type_buttons.removeButton(chip)
            _clear_layout(self._type_holder)
            # Up to three types share one row; a longer shared list wraps, never cut.
            share = len(kinds) <= TYPE_COLUMNS
            holder = QWidget()
            grid: QGridLayout | None = None
            flow: FlowLayout | None = None
            if share:
                grid = QGridLayout(holder)
                grid.setContentsMargins(0, 0, 0, 0)
                grid.setSpacing(CHIP_GAP)
                for column in range(TYPE_COLUMNS):
                    grid.setColumnStretch(column, 1)
            else:
                flow = FlowLayout(holder, spacing=CHIP_GAP)
            for index, kind in enumerate(kinds):
                chip = FitButton(kind.label, share=share)
                chip.setProperty("key", kind.key)
                chip.setIcon(_swatch(kind.color_dark if theme.is_dark else kind.color))
                chip.setIconSize(QSize(SWATCH_SIZE, SWATCH_SIZE))
                chip.setAccessibleName(f"Type {kind.label}")
                chip.setEnabled(not self._read_only)
                self.type_buttons.addButton(chip, index)
                if grid is not None:
                    grid.addWidget(chip, 0, index)
                elif flow is not None:
                    flow.addWidget(chip)
            self._type_holder.addWidget(holder)
            self._type_keys = [kind.key for kind in kinds]
        self.type_buttons.setExclusive(False)
        for chip in self.type_buttons.buttons():
            chip.setChecked(chip.property("key") == selected)
        self.type_buttons.setExclusive(True)

    def _check_status(self, status: str | None) -> None:
        self.status_buttons.setExclusive(False)
        for choice in self.status_buttons.buttons():
            choice.setChecked(choice.property("status") == status)
        self.status_buttons.setExclusive(True)

    def _fill_dates(self, task: PlanningTask) -> None:
        """Show the dates of ``task``, or the rolls being turned when they are open."""
        scheduled = task.start is not None and task.end is not None and self._calendar is not None
        self.dates_box.setVisible(scheduled)
        self.duration_label.setVisible(scheduled)
        self.unscheduled_label.setVisible(not scheduled)
        if not scheduled or task.start is None or task.end is None:
            if self._open in ("start", "end"):
                self._close_picker(commit=False)
            return
        if self._open in ("start", "end"):
            self._preview_dates()
            return
        today = self._today()
        self.start_row.set_value(moment_text(task.start, today))
        self.end_row.set_value(moment_text(task.end, today))
        self.end_row.set_error(error=False)
        self._show_duration(task.start, task.end)

    def _show_duration(self, start: WallTime, end: WallTime) -> None:
        task, calendar = self._task, self._calendar
        _tone(self.duration_label, None)
        if task is None or calendar is None:
            self.duration_label.clear()
            return
        if task.off_hours:
            self.duration_label.setText(f"Durée : {elapsed_text(end - start)} (nuit)")
            return
        worked = calendar.work_between(start, end)
        days = calendar.whole_days(start, end)
        duration = format_duration(worked)
        if days is not None:
            duration = f"{format_days(days)} · {duration}"
        self.duration_label.setText(f"Durée : {duration}")

    def _fill_deadline(self, deadline: date | None) -> None:
        if self._open == "deadline":
            return
        self.deadline_row.set_value(day_text(deadline, self._today()) if deadline else NO_DEADLINE)
        self.clear_deadline_button.setVisible(deadline is not None and not self._read_only)
        self.deadline_row.update()

    def _deadline_start(self) -> date:
        """Day a deadline is picked from: the last day of the task, else today."""
        task = self._task
        if task is not None and task.end is not None:
            return wall_date(task.end - 1)
        return self._today()

    def _people_groups(self, assigned: Sequence[str]) -> list[tuple[str, list[Resource]]]:
        """Everybody of the planning by team, in the order of the teams; then the others."""
        snapshot = self._snapshot
        if snapshot is None:
            return []
        groups: list[tuple[str, list[Resource]]] = []
        current: str | None = None
        for resource in snapshot.active_resources():
            if resource.team != current or not groups:
                current = resource.team
                team = snapshot.meta.team(resource.team)
                title = team.name if team is not None else resource.team or OTHER_PEOPLE
                groups.append((title, []))
            groups[-1][1].append(resource)
        shown = {resource.id for _title, members in groups for resource in members}
        others = [
            snapshot.resources.get(rid) or Resource(id=rid, name="Personne inconnue", initials="?")
            for rid in assigned
            if rid not in shown
        ]
        if others:
            groups.append((OTHER_PEOPLE, others))
        return groups

    def _fill_people(
        self, assigned: Sequence[str], task: PlanningTask | None, *, count_text: str
    ) -> None:
        snapshot = self._snapshot
        groups = self._people_groups(assigned)
        signature = tuple(
            (title, tuple((r.id, r.short_label, r.name) for r in members))
            for title, members in groups
        )
        if signature != self._people_signature:
            # Rebuilt only when the people change: a refresh caused by a click on someone
            # must not delete the button being clicked.
            self._people_signature = signature
            self._person_buttons = {}
            _clear_layout(self.people_layout)
            for title, members in groups:
                self.people_layout.addWidget(label(title, "small"))
                grid_box = QWidget()
                grid = QGridLayout(grid_box)
                grid.setContentsMargins(0, 0, 0, 6)
                grid.setSpacing(CHIP_GAP)
                for column in range(PEOPLE_COLUMNS):
                    grid.setColumnStretch(column, 1)
                for index, resource in enumerate(members):
                    toggle = PersonToggle(resource.id, resource.short_label or "?")
                    toggle.setAccessibleName(resource.name)
                    toggle.setEnabled(not self._read_only)
                    toggle.clicked.connect(
                        lambda checked=False, rid=resource.id: self._on_person_clicked(
                            rid, checked=bool(checked)
                        )
                    )
                    grid.addWidget(toggle, index // PEOPLE_COLUMNS, index % PEOPLE_COLUMNS)
                    self._person_buttons[resource.id] = toggle
                self.people_layout.addWidget(grid_box)
        chosen = set(assigned)
        calendar_for = calendar_resolver(snapshot) if snapshot is not None else None
        for resource_id, toggle in self._person_buttons.items():
            person = snapshot.resources.get(resource_id) if snapshot is not None else None
            name = person.name if person is not None else "Personne inconnue"
            away, note = False, ""
            if snapshot is not None and calendar_for is not None and person and task:
                away, note = person_note(snapshot, person, task, calendar_for(resource_id))
            toggle.setChecked(resource_id in chosen)
            toggle.set_absent(absent=away)
            toggle.set_tooltip(f"{name}\n{note}" if note else name)
        self.people_count.setText(count_text)

    def _fill_actions(self, task: PlanningTask) -> None:
        self.unschedule_button.setVisible(task.scheduled)
        self.duplicate_button.show()

    def _apply_read_only(self) -> None:
        editable = not self._read_only
        for edit in (self.number_edit, self.title_edit):
            edit.setReadOnly(not editable)
        self.notes_edit.setReadOnly(not editable)
        for widget in (
            self.start_row,
            self.end_row,
            self.deadline_row,
            self.urgent_button,
            self.night_button,
            self.duplicate_button,
            self.unschedule_button,
            self.delete_button,
            *self.type_buttons.buttons(),
            *self.status_buttons.buttons(),
            *self._person_buttons.values(),
        ):
            widget.setEnabled(editable)
        if not editable:
            self.clear_deadline_button.hide()

    # -- rolls ------------------------------------------------------------------

    def _picker(self, name: PickerName) -> WheelPicker:
        return {
            "start": self.start_picker,
            "end": self.end_picker,
            "deadline": self.deadline_picker,
        }[name]

    def _picker_widget(self, name: PickerName) -> QWidget:
        return self.deadline_picker_box if name == "deadline" else self._picker(name)

    def _row(self, name: PickerName) -> DateRow:
        return {"start": self.start_row, "end": self.end_row, "deadline": self.deadline_row}[name]

    def _toggle_picker(self, name: PickerName) -> None:
        if self._open == name:
            self._close_picker()
            return
        self._close_picker()
        self._open_picker(name)

    def _open_picker(self, name: PickerName) -> None:
        task = self._task
        if self._mode != "task" or task is None or self._read_only:
            return
        today = self._today()
        if name == "start":
            if task.start is None:
                return
            self.start_picker.set_moment(task.start, today=today)
        elif name == "end":
            if task.end is None:
                return
            self.end_picker.set_moment(task.end, today=today)
        else:
            self.deadline_picker.set_day(task.deadline or self._deadline_start(), today=today)
        self._open = name
        self._rolled = False
        widget = self._picker_widget(name)
        widget.show()
        self._row(name).set_open(open_=True)
        self._picker(name).setFocus(Qt.FocusReason.OtherFocusReason)
        self._watch_clicks(watch=True)
        self._preview()
        QTimer.singleShot(0, self, self._show_open_picker)

    def _show_open_picker(self) -> None:
        if self._open is not None:
            self.scroll_area.ensureWidgetVisible(self._picker_widget(self._open), 0, 0)

    def _close_picker(self, *, commit: bool = True, accepted: bool = False) -> None:
        """Fold the rolls; with ``commit``, save their value (an end before the start: not)."""
        name = self._open
        if name is None:
            return
        self._open = None
        self._watch_clicks(watch=False)
        self._picker_widget(name).hide()
        self._row(name).set_open(open_=False)
        rolled, self._rolled = self._rolled, False
        task = self._task
        if commit and self._mode == "task" and task is not None:
            if name == "start":
                self._commit_start()
            elif name == "end":
                self._commit_end()
            elif rolled or accepted or task.deadline is not None:
                # From « Aucune », folding the rolls untouched sets no deadline.
                self._commit_deadline()
        task = self._task
        if self._mode == "task" and task is not None:
            self._fill_dates(task)
            self._fill_deadline(task.deadline)

    def _watch_clicks(self, *, watch: bool) -> None:
        """Watch every click of the application while rolls are open (« clic ailleurs »)."""
        app = QApplication.instance()
        if app is None or watch == self._watching_clicks:
            return
        self._watching_clicks = watch
        if watch:
            app.installEventFilter(self)
        else:
            app.removeEventFilter(self)

    def _on_picker_rolled(self) -> None:
        self._rolled = True
        self._preview()

    def _on_picker_finished(self, accepted: bool) -> None:  # noqa: FBT001
        name = self._open
        if name is None:
            return
        self._close_picker(accepted=accepted)
        self._row(name).setFocus(Qt.FocusReason.OtherFocusReason)

    def _preview(self) -> None:
        """Show on the rows what the open rolls would save."""
        if self._open in ("start", "end"):
            self._preview_dates()
        elif self._open == "deadline":
            day = self.deadline_picker.day()
            self.deadline_row.set_value(day_text(day, self._today()) if day else NO_DEADLINE)

    def _preview_dates(self) -> None:
        task = self._task
        if task is None or task.start is None or task.end is None or self._calendar is None:
            return
        today = self._today()
        if self._open == "start":
            start, end = self._planned_start(self.start_picker.moment())
            self.start_row.set_value(moment_text(start, today))
            self.end_row.set_value(moment_text(end, today))
            self.end_row.set_error(error=False)
            self._show_duration(start, end)
            return
        start = self._sent_start()
        picked = self.end_picker.moment()
        planned = self._planned_end(picked, start)
        self.start_row.set_value(moment_text(start, today))
        if planned is None:
            self.end_row.set_value(moment_text(picked, today))
            self.end_row.set_error(error=True)
            self.duration_label.setText(END_ERROR)
            _tone(self.duration_label, "error")
            return
        self.end_row.set_value(moment_text(planned, today))
        self.end_row.set_error(error=False)
        self._show_duration(start, planned)

    def _sent_start(self) -> WallTime:
        task = self._task
        start = self._sent.get("start", task.start if task is not None else None)
        return start if isinstance(start, int) else 0

    def _planned_start(self, picked: WallTime) -> tuple[WallTime, WallTime]:
        """Start and end the task takes when its start is moved to ``picked``.

        Night work keeps its exact times and its real length; day work starts at the
        first worked half hour and keeps its worked time.
        """
        task, calendar = self._task, self._calendar
        if task is None or task.start is None or task.end is None or calendar is None:
            return picked, picked
        if task.off_hours:
            wanted = snap(picked)
            return wanted, wanted + (task.end - task.start)
        return move_preserving_work(task.start, task.end, calendar.working_start(picked), calendar)

    def _planned_end(self, picked: WallTime, start: WallTime) -> WallTime | None:
        """End the task takes for ``picked`` (the last worked time); None before the start."""
        task, calendar = self._task, self._calendar
        if task is None or calendar is None or picked <= start:
            return None
        wanted = snap(picked) if task.off_hours else calendar.working_end(picked)
        return wanted if wanted > start else None

    # -- commits ----------------------------------------------------------------

    def _target(self) -> str | None:
        """Task id the edits go to: the task, ``MULTI_SELECTION``, or None (no edit)."""
        if self._loading or self._read_only:
            return None
        if self._mode == "task" and self._task is not None:
            return self._task.id
        if self._mode == "multi":
            return MULTI_SELECTION
        return None

    def _commit_value(self, name: str, value: object) -> None:
        target = self._target()
        if target is None:
            return
        task = self._task
        if self._mode == "task" and task is not None:
            current = self._sent.get(name, getattr(task, name))
            if value == current:
                return
            self._sent[name] = value
        self.field_changed.emit(target, name, value)

    def _dossier_designation(self, task: PlanningTask) -> str:
        project = self._project
        designation = project.designation if project is not None else ""
        return (designation or task.project_cache.designation).strip()

    def _commit_title(self) -> None:
        if self._mode == "task":
            self._commit_value("title", self.title_edit.text().strip())
            self.title_edit.setModified(False)

    def _commit_number(self) -> None:
        if self._mode != "task" or self._target() is None:
            return
        number = self._resolve_project(self.number_edit.text())
        if number and not is_project_number(number):
            self._show_number_error(error=True)
            return
        self._show_number_error(error=False)
        if self.number_edit.text() != number:
            self.number_edit.setText(number)
        self.number_edit.setModified(False)
        self._commit_value("project_number", number)

    def _show_number_error(self, *, error: bool) -> None:
        _tone(self.number_edit, "error" if error else None)
        self.number_edit.setToolTip(NUMBER_ERROR if error else "")

    def _resolve_project(self, text: str) -> str:
        """Return the number typed or chosen; ``5231`` completes to the dossier ``…-5231``."""
        value = text.strip()
        if value in self._project_by_text:
            return self._project_by_text[value]
        if value.isdigit():
            matches = [p.number for p in self._projects if p.number.endswith(f"-{value}")]
            if len(matches) == 1:
                return matches[0]
        return canonical_project_number(value)

    def _on_project_chosen(self, text: str) -> None:
        number = self._project_by_text.get(text)
        if number is None:
            return
        self.number_edit.setText(number)
        self.number_edit.setModified(True)
        self._commit_number()

    def _commit_type(self, index: int) -> None:
        if 0 <= index < len(self._type_keys):
            self._commit_value("task_type", self._type_keys[index])
            if self._mode == "multi":
                self.type_mixed.clear()

    def _commit_status(self, index: int) -> None:
        target = self._target()
        if target is None or not 0 <= index < len(STATUSES):
            return
        status = STATUSES[index]
        task = self._task
        if self._mode == "task" and task is not None:
            if self._sent.get("status", task.status) == status:
                return
            self._sent["status"] = status
        if self._mode == "multi":
            self.status_mixed.clear()
        self.status_changed.emit(target, status)

    def _commit_urgent(self, checked: bool) -> None:  # noqa: FBT001
        if self._mode == "multi":
            self.urgent_button.setText(URGENT_TEXT)
        self._commit_value("urgent", bool(checked))

    def _commit_night(self, checked: bool) -> None:  # noqa: FBT001
        if self._mode == "task":
            self._commit_value("off_hours", bool(checked))

    def _commit_start(self) -> None:
        task = self._task
        target = self._target()
        if target is None or self._mode != "task" or task is None or self._calendar is None:
            return
        if task.start is None or task.end is None:
            return
        start, end = self._planned_start(self.start_picker.moment())
        if (start, end) == (self._sent.get("start", task.start), self._sent.get("end", task.end)):
            return
        self._sent["start"], self._sent["end"] = start, end
        self.dates_changed.emit(target, start, end)

    def _commit_end(self) -> None:
        task = self._task
        target = self._target()
        if target is None or self._mode != "task" or task is None or self._calendar is None:
            return
        if task.start is None or task.end is None:
            return
        start = self._sent_start()
        end = self._planned_end(self.end_picker.moment(), start)
        if end is None or end == self._sent.get("end", task.end):
            return  # An end before the start is never saved.
        self._sent["end"] = end
        self.dates_changed.emit(target, start, end)

    def _commit_deadline(self) -> None:
        if self._mode == "task":
            self._commit_value("deadline", self.deadline_picker.day())

    def _clear_deadline(self) -> None:
        if self._open == "deadline":
            self._close_picker(commit=False)
        self._commit_value("deadline", None)
        self._fill_deadline(None)

    def _on_notes_edited(self) -> None:
        if not self._loading and self._mode == "task" and not self._read_only:
            self._notes_timer.start()

    def _commit_notes(self) -> None:
        if self._mode == "task":
            self._commit_value("notes", self.notes_edit.toPlainText())

    # -- people -----------------------------------------------------------------

    def _on_person_clicked(self, resource_id: str, *, checked: bool) -> None:
        target = self._target()
        toggle = self._person_buttons.get(resource_id)
        if target is None:
            if toggle is not None:
                toggle.setChecked(not checked)
            return
        task = self._task
        mode_task = self._mode == "task" and task is not None
        if mode_task and task is not None and checked == (resource_id in task.assignments):
            return
        if checked:
            self.people_changed.emit(target, [resource_id], [])
        else:
            self.people_changed.emit(target, [], [resource_id])
        if self._mode == "multi":
            count = sum(1 for button in self._person_buttons.values() if button.isChecked())
            self.people_count.setText(people_count_text(count, common=True))

    # -- actions ----------------------------------------------------------------

    def _request_action(self, action: str) -> None:
        if self._mode == "task" and self._task is not None:
            target = self._task.id
        elif self._mode == "multi":
            target = MULTI_SELECTION
        else:
            return
        if self._read_only:
            return
        self.flush()
        self.action_requested.emit(target, action)

    # -- events -----------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            if self._open is not None and isinstance(event, QMouseEvent):
                self._close_if_outside(event.globalPosition().toPoint())
            return False
        if kind == QEvent.Type.FocusOut and isinstance(event, QFocusEvent):
            if watched is self.title_edit:
                self._commit_title()
            elif watched is self.number_edit and not _popup_visible(self._number_completer):
                self._commit_number()
            elif watched is self.notes_edit and self._notes_timer.isActive():
                self._notes_timer.stop()
                self._commit_notes()
            elif (
                self._open is not None
                and watched is self._picker(self._open)
                and event.reason()
                in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
            ):
                self._close_picker()
        return super().eventFilter(watched, event)

    def _close_if_outside(self, point: QPoint) -> None:
        """Fold the rolls when the press is neither on them nor on their row."""
        name = self._open
        if name is None:
            return
        for widget in (self._picker_widget(name), self._row(name)):
            if widget.isVisible() and widget.rect().contains(widget.mapFromGlobal(point)):
                return
        self._close_picker()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.flush()
            self.back_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _on_theme(self) -> None:
        self.setStyleSheet(_panel_style(current_theme()))
        selected: str | None = None
        if self._mode == "task" and self._task is not None:
            selected = self._task.task_type
        elif self._mode == "multi":
            common_type = self._common.get("task_type")
            selected = common_type if isinstance(common_type, str) else None
        self._loading = True
        try:
            self._fill_types(selected)
        finally:
            self._loading = False

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), current_theme().color("bg"))
        painter.end()
