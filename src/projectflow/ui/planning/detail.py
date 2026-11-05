"""« Détail »: the face of the side panel that shows and edits one task.

There is no Save button: a field saves itself when it loses the focus or on Enter, the
notes 800 ms after the last keystroke. Each edit leaves as a signal that the Planning tab
turns into a command; the tab then calls ``set_task`` again with the updated task. A
field being edited keeps its value when the task is refreshed meanwhile. Start and end
offer only the worked half-hours of their day, so Friday afternoon is never proposed.

The panel scrolls: the wheel only changes a list, a number or a date that has the focus,
and a list stepped through with the arrow keys saves once, when it is left or on Enter.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from PySide6.QtCore import (
    QDate,
    QEvent,
    QLocale,
    QObject,
    QPointF,
    QRect,
    QRectF,
    QSignalBlocker,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFocusEvent,
    QFont,
    QFontMetricsF,
    QIcon,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from projectflow.core.planning.calendar import SNAP_MINUTES, WorkCalendar, snap
from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.commands import calendar_resolver
from projectflow.core.planning.conflicts import Finding, FindingKind, resource_load
from projectflow.core.planning.models import (
    STATUS_LABELS,
    Author,
    PlanningDocument,
    PlanningTask,
    Resource,
    TaskStatus,
)
from projectflow.core.planning.night import elapsed_text, rest_text
from projectflow.core.planning.quick_entry import (
    ProjectInfo,
    fold_text,
    format_days,
    format_duration,
)
from projectflow.core.planning.timescale import WEEKDAY_NAMES, move_preserving_work
from projectflow.core.planning.walltime import (
    MINUTES_PER_DAY,
    MINUTES_PER_HOUR,
    WallTime,
    day_start,
    format_hhmm,
    minute_of_day,
    wall_date,
)
from projectflow.ui.planning.backlog import FlowLayout, relative_time, release_day
from projectflow.ui.theme import Theme, body_font, current_theme, icon_svg, theme_notifier
from projectflow.ui.widgets.industry import IconLabel, button, label, rule, set_tone

DetailMode = Literal["empty", "task", "multi"]
DetailAction = Literal[
    "folder",
    "fiche",
    "repertoire",
    "creation",
    "sortie",
    "duplicate",
    "detach",
    "unschedule",
    "delete",
]

MULTI_SELECTION = ""
"""Task id of the signals emitted while several tasks are selected: « every one of them »."""
NOTES_DEBOUNCE_MS = 800
MAX_REQUIRED_PEOPLE = 50
MAX_SHIFT_DAYS = 60
"""Most working days the « Décaler » field of a selection moves its tasks by at once."""
SHIFT_SPIN_WIDTH = 84
NO_DEADLINE = QDate(2000, 1, 1)
"""Lowest date of the deadline field, shown as « Aucune »."""
DATE_FORMAT = "ddd dd.MM.yyyy"
NOTES_HEIGHT = 78
SWATCH_SIZE = 12
CHIP_HEIGHT = 24
CHIP_PADDING_X = 8
CHIP_CLOSE_WIDTH = 18
CHIP_CROSS_SIZE = 6
TIME_COMBO_WIDTH = 96
REQUIRED_SPIN_WIDTH = 104
DOSSIER_BUTTONS_STYLE = "QPushButton { padding: 3px 8px; min-height: 18px; }"
"""Buttons of the dossier card, tighter than the application's so they fit two rows."""
FIELD_SPACING = 10
STATUSES: tuple[TaskStatus, ...] = ("provisional", "confirmed", "done")
DOSSIER_ACTIONS: tuple[tuple[DetailAction, str, str], ...] = (
    ("folder", "Dossier", "Ouvrir le dossier"),
    ("fiche", "Fiche", "Ouvrir la fiche du dossier"),
    ("repertoire", "Répertoire", "Voir le dossier dans le Répertoire"),
    ("creation", "Création", "Charger le dossier dans Création projet"),
    ("sortie", "Sortie", "Préparer la sortie du dossier"),
)
EMPTY_TEXT = "Double-cliquez une barre ou une carte pour voir et modifier la tâche."
TASK_WIDE_FINDINGS: frozenset[FindingKind] = frozenset({"missing_people", "unknown_project"})
"""Findings of the task itself, repeated on each of its bars: said once, for nobody."""
OFF_HOURS_SUFFIX = " (hors horaire)"
ABSENT = "absent"
PARTLY_ABSENT = "absent en partie"
SHIFT_TOOLTIP = "Décaler toutes les tâches sélectionnées, en jours ouvrés"
ESTIMATE_HINT = "Sans date : glisser la carte sur une ligne du planning pour la poser."
NIGHT_CHECK_TEXT = "Travail de nuit"
NIGHT_CHECK_TOOLTIP = (
    "Nuit du lundi au jeudi soir : heures exactes de 18:00 à 06:30, durée réelle, "
    "8 h de repos avant et après"
)
NIGHT_TIMES = tuple(range(0, MINUTES_PER_DAY, SNAP_MINUTES))
"""Hours offered to night work: every half hour, 00:00 to 23:30."""
ESTIMATE_ERROR = "Durée non comprise : écrire par exemple 2 j, ½ j, 4 h ou 3h30."

_DAYS_RE = re.compile(
    r"^(?P<n>\d+(?:\.\d+)?)?\s*(?P<h1>½)?\s*(?:jours?|journ[ée]es?|jrs?|j)\.?\s*(?P<h2>½)?$"
)
_HOURS_RE = re.compile(r"^(?P<h>\d+(?:\.\d+)?)\s*(?:heures?|hrs?|h)\s*(?P<m>\d{1,2})?$")
_MINUTES_RE = re.compile(r"^(?P<m>\d+)\s*(?:minutes?|min|mn)$")


# -------------------------------------------------------------- helpers ----


def time_choices(calendar: WorkCalendar, day: date, *, end: bool) -> list[int]:
    """Minutes of the day a start (or an end) may take: worked half-hours only.

    Starts run from each period start to half an hour before its end, ends from half an
    hour after its start to its end: Monday 06:30-11:30 and 13:00-16:00 to start, Friday
    06:30-11:30 only. Holidays offer the hours of their weekday.
    """
    minutes: list[int] = []
    for low, high in calendar.periods_of_weekday(day.weekday()):
        first = low + SNAP_MINUTES if end else low
        last = high if end else high - SNAP_MINUTES
        minutes.extend(range(first, last + 1, SNAP_MINUTES))
    return minutes


def parse_estimate(text: str) -> tuple[float, int] | None:
    """Return ``(days, minutes)`` typed as « 2 j », « 1 j ½ », « 4 h », « 3h30 », « 90 min ».

    Days round to the half day, minutes to the half hour; an empty text clears the
    estimate (``(0, 0)``) and an unreadable one gives None.
    """
    value = text.strip().casefold().replace(",", ".").removeprefix("~").strip()
    if not value or value == "?":
        return 0.0, 0
    if match := _DAYS_RE.match(value):
        days = float(match["n"] or 0) + (0.5 if match["h1"] or match["h2"] else 0.0)
        if days <= 0:
            return None
        return max(round(days * 2), 1) / 2, 0
    minutes: int | None = None
    if match := _HOURS_RE.match(value):
        minutes = round(float(match["h"]) * MINUTES_PER_HOUR) + int(match["m"] or 0)
    elif match := _MINUTES_RE.match(value):
        minutes = int(match["m"])
    if minutes is None or minutes <= 0:
        return None
    return 0.0, max(SNAP_MINUTES, -(-minutes // SNAP_MINUTES) * SNAP_MINUTES)


def estimate_text(task: PlanningTask) -> str:
    """Estimate of a task without dates, as typed back in the field (empty when none)."""
    if task.estimate_days > 0:
        return format_days(task.estimate_days)
    if task.estimate_minutes > 0:
        return format_duration(task.estimate_minutes)
    return ""


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


def availability_rank(state: str) -> int:
    """Order of the people offered: free or busy first, then partly away, then away."""
    if state == ABSENT:
        return 2
    return 1 if state.startswith(ABSENT) else 0


def author_text(task: PlanningDocument, now: datetime) -> str:
    """« Modifié par JD · BUREAU-2 · il y a 3 min » (or « Créé par … » when never changed)."""
    changed = bool(task.updated_at) and task.updated_at != task.created_at
    author: Author = task.updated_by if changed else task.created_by
    when = relative_time(task.updated_at if changed else task.created_at, now)
    verb = "Modifié" if changed else "Créé"
    if author.label:
        return f"{verb} par {author.label}" + (f" · {when}" if when else "")
    return f"{verb} {when}" if when else ""


def finding_text(finding: Finding, snapshot: PlanningSnapshot | None) -> str:
    """Message of a finding, led by the initials of the person it concerns."""
    resource = (
        snapshot.resources.get(finding.resource_id)
        if snapshot is not None and finding.resource_id
        else None
    )
    prefix = f"{resource.short_label} · " if resource is not None else ""
    return f"{prefix}{finding.message}"


def unique_findings(findings: Sequence[Finding]) -> list[Finding]:
    """Return the findings of every bar of a task, each said once, task-wide ones rowless."""
    seen: set[tuple[str, str | None, str]] = set()
    result: list[Finding] = []
    for finding in findings:
        row = None if finding.kind in TASK_WIDE_FINDINGS else finding.resource_id
        key = (finding.kind, row, finding.message)
        if key in seen:
            continue
        seen.add(key)
        result.append(finding if row == finding.resource_id else replace(finding, resource_id=row))
    return result


def _popup_visible(completer: QCompleter) -> bool:
    popup = completer.popup()
    return popup is not None and popup.isVisible()


def _qdate(day: date) -> QDate:
    return QDate(day.year, day.month, day.day)


def _pydate(value: QDate) -> date:
    return date(value.year(), value.month(), value.day())


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


def _date_style(theme: Theme) -> str:
    """Style of the date fields, which the application style sheet leaves out."""
    t = theme.tokens
    directory = Path(tempfile.gettempdir()) / "projectflow-planning" / theme.key
    directory.mkdir(parents=True, exist_ok=True)
    arrow = directory / "chevron-down.svg"
    content = icon_svg("chevron-down", t["neutral-800"], 2)
    if not arrow.exists() or arrow.read_text(encoding="utf-8") != content:
        arrow.write_text(content, encoding="utf-8")
    return f"""
QDateEdit {{
  background: {t["surface"]}; color: {t["text"]}; border: 1px solid {theme.rgba("text", 0.34)};
  border-radius: 0; padding: 6px 8px; min-height: 22px; font-size: 14px;
  selection-background-color: {theme.rgba("accent", 0.30)}; selection-color: {t["text"]};
}}
QDateEdit:hover {{ border-color: {theme.rgba("text", 0.5)}; }}
QDateEdit:focus {{ border-color: {t["accent"]}; }}
QDateEdit:read-only, QDateEdit:disabled {{
  background: {t["bg"]}; color: {theme.rgba("text", 0.6)}; border-color: {theme.rgba("text", 0.14)};
}}
QDateEdit::drop-down {{
  border: 0; width: 24px; subcontrol-origin: padding; subcontrol-position: right;
}}
QDateEdit::down-arrow {{ image: url({arrow.as_posix()}); width: 12px; height: 12px; }}
QCalendarWidget QWidget {{ background: {t["surface"]}; color: {t["text"]}; }}
QCalendarWidget QAbstractItemView {{
  selection-background-color: {t["accent"]}; selection-color: {t["bg"]};
}}
"""


# --------------------------------------------------------------- widgets ----


def _wheel_allowed(widget: QWidget, event: QWheelEvent) -> bool:
    """Whether a wheel turn may change ``widget``: only once it has the focus.

    Over a field without the focus the turn is ignored, so it goes up to the scroll area,
    which scrolls the panel instead of retyping or moving the shared task.
    """
    if widget.hasFocus():
        return True
    event.ignore()
    return False


class ChoiceCombo(QComboBox):
    """List that sends a choice once (``chosen``), never while it is stepped through.

    A pick in its open list is sent at once. The arrow keys, and the wheel once it has the
    focus, step through the items: the value goes when the field is left or on Enter.
    """

    chosen = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._in_popup = False
        self._stepped = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.activated.connect(self._on_activated)

    def pending(self) -> bool:
        """Whether an item stepped to with the keys or the wheel is not sent yet."""
        return self._stepped

    def commit(self) -> None:
        """Send the item stepped to, if any."""
        if self._stepped:
            self._stepped = False
            self.chosen.emit(self.currentIndex())

    def discard(self) -> None:
        """Forget the item stepped to (the list now shows another task)."""
        self._stepped = False

    def showPopup(self) -> None:  # noqa: N802
        self._in_popup = True
        super().showPopup()
        if not self.view().isVisible():
            # A native list (macOS) has already been closed and picked from.
            QTimer.singleShot(0, self, self._popup_closed)

    def hidePopup(self) -> None:  # noqa: N802
        super().hidePopup()
        # Qt closes the list, then reports the pick: forget the list once that is done.
        QTimer.singleShot(0, self, self._popup_closed)

    def _popup_closed(self) -> None:
        self._in_popup = False

    def _on_activated(self, index: int) -> None:
        if self._in_popup:
            self._stepped = False
            self.chosen.emit(index)
        else:
            self._stepped = True

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if _wheel_allowed(self, event):
            super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if self._stepped and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.commit()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        super().focusOutEvent(event)
        if event.reason() != Qt.FocusReason.PopupFocusReason:
            self.commit()


class FocusSpinBox(QSpinBox):
    """Number field the wheel changes only once it has the focus."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if _wheel_allowed(self, event):
            super().wheelEvent(event)


class DateField(QDateEdit):
    """Date field with a calendar, changed by the wheel only once it has the focus.

    While it shows its special value (« Aucune »), its calendar opens on ``blank_date``
    and the up arrow gives ``blank_date``, not the day after the lowest date.
    """

    def __init__(self, locale: QLocale, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.blank_date = QDate.currentDate()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCalendarPopup(True)
        self.setDisplayFormat(DATE_FORMAT)
        self.setLocale(locale)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setKeyboardTracking(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        calendar = self.calendarWidget()
        calendar.setLocale(locale)
        calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        calendar.installEventFilter(self)

    def is_blank(self) -> bool:
        """Whether the field shows its special value instead of a date."""
        return bool(self.specialValueText()) and self.date() <= self.minimumDate()

    def stepBy(self, steps: int) -> None:  # noqa: N802
        if steps > 0 and self.is_blank() and not self.isReadOnly():
            self.setDate(self.blank_date)
            return
        super().stepBy(steps)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if _wheel_allowed(self, event):
            super().wheelEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        calendar = self.calendarWidget()
        if watched is calendar and event.type() == QEvent.Type.Show and self.is_blank():
            calendar.setCurrentPage(self.blank_date.year(), self.blank_date.month())
        return super().eventFilter(watched, event)


class PersonChip(QAbstractButton):
    """One assigned (solid) or foreseen (dashed) person, with ✕ to remove them."""

    remove_requested = Signal(str)

    def __init__(
        self,
        resource_id: str,
        text: str,
        tooltip: str,
        *,
        foreseen: bool = False,
        removable: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.resource_id = resource_id
        self.foreseen = foreseen
        self.removable = removable
        self.setText(text)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802
        width = QFontMetricsF(self._font()).horizontalAdvance(self.text()) + 2 * CHIP_PADDING_X
        if self.removable:
            width += CHIP_CLOSE_WIDTH - CHIP_PADDING_X / 2
        return QSize(round(width), CHIP_HEIGHT)

    def close_rect(self) -> QRect:
        if not self.removable:
            return QRect()
        return QRect(self.width() - CHIP_CLOSE_WIDTH, 0, CHIP_CLOSE_WIDTH, self.height())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self.removable
            and event.button() == Qt.MouseButton.LeftButton
            and self.close_rect().contains(event.position().toPoint())
        ):
            event.accept()
            self.remove_requested.emit(self.resource_id)
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if self.removable and event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            event.accept()
            self.remove_requested.emit(self.resource_id)
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        theme = current_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if not self.foreseen:
            painter.fillRect(rect, theme.color("accent-100"))
        pen = QPen(theme.color("accent") if self.hasFocus() else theme.color("neutral-600"))
        pen.setWidthF(1.0)
        if self.foreseen:
            pen.setStyle(Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([2.0, 2.0])
        painter.setPen(pen)
        painter.drawRect(rect)
        painter.setFont(self._font())
        painter.setPen(theme.color("accent-800") if not self.foreseen else theme.color("text"))
        text_rect = QRectF(CHIP_PADDING_X, 0, self.width() - 2 * CHIP_PADDING_X, self.height())
        if self.removable:
            text_rect.setRight(self.close_rect().left() + CHIP_PADDING_X / 2)
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self.text(),
        )
        if self.removable:
            # The body fonts have no « ✕ »: a cross of two strokes.
            center = QRectF(self.close_rect()).center()
            half = CHIP_CROSS_SIZE / 2
            cross = QPen(theme.color("neutral-800"))
            cross.setWidthF(1.3)
            painter.setPen(cross)
            painter.drawLine(
                QPointF(center.x() - half, center.y() - half),
                QPointF(center.x() + half, center.y() + half),
            )
            painter.drawLine(
                QPointF(center.x() - half, center.y() + half),
                QPointF(center.x() + half, center.y() - half),
            )
        painter.end()

    @staticmethod
    def _font() -> QFont:
        return body_font(12, QFont.Weight.DemiBold)


class _DossierCard(QFrame):
    """Dossier of the task as ProjectFlow knows it, with the buttons that open it."""

    action_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(3)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.number_label = label("", "h4")
        self.client_label = label("", "muted")
        self.client_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        head.addWidget(self.number_label, 0, Qt.AlignmentFlag.AlignBaseline)
        head.addWidget(self.client_label, 1, Qt.AlignmentFlag.AlignBaseline)
        layout.addLayout(head)
        self.designation_label = label("", wrap=True)
        self.contact_label = label("", "small", wrap=True)
        self.facts_label = label("", "small", wrap=True)
        layout.addWidget(self.designation_label)
        layout.addWidget(self.contact_label)
        layout.addWidget(self.facts_label)
        self.buttons: dict[str, QPushButton] = {}
        actions_box = QWidget()
        actions_box.setStyleSheet(DOSSIER_BUTTONS_STYLE)
        actions = FlowLayout(actions_box, spacing=4)
        for key, text, tooltip in DOSSIER_ACTIONS:
            action = button(text, size="sm", icon="folder" if key == "folder" else None)
            action.setToolTip(tooltip)
            action.clicked.connect(
                lambda _checked=False, name=key: self.action_requested.emit(name)
            )
            actions.addWidget(action)
            self.buttons[key] = action
        layout.addSpacing(4)
        layout.addWidget(actions_box)
        self.actions_box = actions_box

    def show_dossier(
        self,
        task: PlanningTask,
        project: ProjectInfo | None,
    ) -> None:
        cache = task.project_cache
        number = task.project_number or (project.number if project is not None else "")

        def pick(found: str, cached: str) -> str:
            return found or cached

        client = pick(project.client if project else "", cache.client)
        contact = pick(project.contact if project else "", cache.contact)
        designation = pick(project.designation if project else "", cache.designation)
        managed_by = pick(project.managed_by if project else "", cache.managed_by)
        if not number:
            self.number_label.setText("Sans dossier")
            self.client_label.setText("Tâche interne")
            self.designation_label.setText(
                "Saisir un numéro dans Dossier pour rattacher la tâche à un dossier."
            )
            self.contact_label.hide()
            self.facts_label.hide()
            self.actions_box.hide()
            return
        self.number_label.setText(number)
        self.client_label.setText(client)
        self.designation_label.setText(designation or "Désignation inconnue")
        self.contact_label.setText(f"Contact : {contact}" if contact else "")
        self.contact_label.setVisible(bool(contact))
        released = release_day(cache.atelier_release)
        facts = [
            f"Sorti atelier {released:%d.%m.%Y}" if released is not None else "",
            f"Géré par {managed_by}" if managed_by else "",
            cache.location,
        ]
        self.facts_label.setText(" · ".join(part for part in facts if part))
        self.facts_label.setVisible(any(facts))
        self.actions_box.show()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), current_theme().color("surface"))
        painter.end()


def _caption(text: str) -> QLabel:
    return label(text, "field")


def _field_block(caption: QWidget, *controls: QWidget) -> QWidget:
    block = QWidget()
    layout = QVBoxLayout(block)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(5)
    layout.addWidget(caption)
    for control in controls:
        layout.addWidget(control)
    return block


def _row(*widgets: QWidget, stretch_last: bool = False) -> QWidget:
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    for index, widget in enumerate(widgets):
        layout.addWidget(widget, 1 if stretch_last and index == len(widgets) - 1 else 0)
    return box


# ----------------------------------------------------------------- panel ----


class DetailPanel(QWidget):
    """Face « Détail » of the side panel: one task, several tasks, or nothing."""

    field_changed = Signal(str, str, object)
    people_changed = Signal(str, list, list)
    dates_changed = Signal(str, int, int)
    status_changed = Signal(str, str)
    shift_requested = Signal(str, int)
    """``MULTI_SELECTION`` and the signed working days to move the selected tasks by."""
    action_requested = Signal(str, str)
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mode: DetailMode = "empty"
        self._task: PlanningTask | None = None
        self._snapshot: PlanningSnapshot | None = None
        self._calendar: WorkCalendar | None = None
        self._project: ProjectInfo | None = None
        self._findings: list[Finding] = []
        self._read_only = False
        self._now: datetime | None = None
        self._focus_resource_id: str | None = None
        self._multi_count = 0
        self._common: dict[str, object] = {}
        self._sent: dict[str, object] = {}
        self._loading = False
        self._projects: list[ProjectInfo] = []
        self._project_by_text: dict[str, str] = {}
        self._person_by_text: dict[str, str] = {}
        self._locale = QLocale(QLocale.Language.French, QLocale.Country.Switzerland)
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
        self.dossier_card = _DossierCard()
        self.dossier_card.action_requested.connect(self._request_action)

        self.title_edit = QLineEdit()
        self.title_edit.setAccessibleName("Titre de la tâche")
        self.title_edit.editingFinished.connect(self._commit_title)
        self.title_edit.installEventFilter(self)
        self.dossier_edit = QLineEdit()
        self.dossier_edit.setPlaceholderText("2026-1234 ou vide pour une tâche interne")
        self.dossier_edit.setAccessibleName("Numéro de dossier")
        self.dossier_edit.editingFinished.connect(self._commit_dossier)
        self.dossier_edit.installEventFilter(self)
        self._dossier_completer = QCompleter([], self.dossier_edit)
        self._configure_completer(self._dossier_completer)
        self.dossier_edit.setCompleter(self._dossier_completer)
        self._dossier_completer.activated.connect(self._on_project_chosen)

        self.type_combo = ChoiceCombo()
        self.type_combo.setAccessibleName("Type de tâche")
        self.type_combo.setIconSize(QSize(SWATCH_SIZE, SWATCH_SIZE))
        self.type_combo.chosen.connect(self._commit_type)

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

        self.people_caption = _caption("Affectés")
        self.required_spin = FocusSpinBox()
        self.required_spin.setRange(1, MAX_REQUIRED_PEOPLE)
        self.required_spin.setSuffix(" pers.")
        self.required_spin.setFixedWidth(REQUIRED_SPIN_WIDTH)
        self.required_spin.setAccessibleName("Personnes requises")
        self.required_spin.setToolTip("Personnes requises")
        self.required_spin.editingFinished.connect(self._commit_required)
        self.required_caption = label("Requis", "field")
        people_head = QWidget()
        people_head_layout = QHBoxLayout(people_head)
        people_head_layout.setContentsMargins(0, 0, 0, 0)
        people_head_layout.addWidget(self.people_caption, 1)
        people_head_layout.addWidget(self.required_caption)
        people_head_layout.addWidget(self.required_spin)
        self.people_box = QWidget()
        self.people_flow = FlowLayout(self.people_box, spacing=4)
        self.add_person_button = button("+ ajouter", "chip")
        # In its box from the start: shown before the first task, a widget without parent
        # would open as a tiny window of its own.
        self.add_person_button.setParent(self.people_box)
        self.people_flow.addWidget(self.add_person_button)
        self.add_person_button.setToolTip("Ajouter une personne")
        self.add_person_button.clicked.connect(self._start_adding_person)
        self.person_edit = QLineEdit()
        self.person_edit.setPlaceholderText("Initiales ou nom…")
        self.person_edit.setAccessibleName("Personne à ajouter")
        self.person_edit.returnPressed.connect(self._commit_person_text)
        self.person_edit.installEventFilter(self)
        self.person_edit.hide()
        self._person_completer = QCompleter([], self.person_edit)
        self._configure_completer(self._person_completer)
        self.person_edit.setCompleter(self._person_completer)
        self._person_completer.activated.connect(self._on_person_chosen)

        self.start_date = self._date_edit("Date de début")
        self.start_time = ChoiceCombo()
        self.start_time.setAccessibleName("Heure de début")
        self.start_time.setFixedWidth(TIME_COMBO_WIDTH)
        self.start_time.setPlaceholderText("—")
        self.end_date = self._date_edit("Date de fin")
        self.end_time = ChoiceCombo()
        self.end_time.setAccessibleName("Heure de fin")
        self.end_time.setFixedWidth(TIME_COMBO_WIDTH)
        self.end_time.setPlaceholderText("—")
        self.start_date.dateChanged.connect(self._on_start_date_changed)
        self.end_date.dateChanged.connect(self._on_end_date_changed)
        self.start_date.editingFinished.connect(self._commit_start)
        self.end_date.editingFinished.connect(self._commit_end)
        self.start_time.chosen.connect(self._commit_start)
        self.end_time.chosen.connect(self._commit_end)
        for edit, commit in (
            (self.start_date, self._commit_start),
            (self.end_date, self._commit_end),
        ):
            calendar_widget = edit.calendarWidget()
            calendar_widget.clicked.connect(
                lambda _day, apply=commit: QTimer.singleShot(0, self, apply)
            )
        self.duration_label = label("", "small")
        self.night_check = QCheckBox(NIGHT_CHECK_TEXT)
        self.night_check.setToolTip(NIGHT_CHECK_TOOLTIP)
        self.night_check.clicked.connect(self._commit_night)
        self.rest_label = label("", "small", wrap=True)
        self.estimate_edit = QLineEdit()
        self.estimate_edit.setPlaceholderText("2 j, ½ j, 4 h, 3h30…")
        self.estimate_edit.setAccessibleName("Estimation")
        self.estimate_edit.setToolTip("Durée prévue : en jours ouvrés (2 j) ou en heures (4 h)")
        self.estimate_edit.editingFinished.connect(self._commit_estimate)
        self.estimate_label = label(ESTIMATE_HINT, "small", wrap=True)

        self.deadline_edit = self._date_edit("Échéance")
        self.deadline_edit.setMinimumDate(NO_DEADLINE)
        self.deadline_edit.setSpecialValueText("Aucune")
        self.deadline_edit.editingFinished.connect(self._commit_deadline)
        self.deadline_edit.calendarWidget().clicked.connect(
            lambda _day: QTimer.singleShot(0, self, self._commit_deadline)
        )
        self.clear_deadline_button = button("", "icon", icon="x")
        self.clear_deadline_button.setToolTip("Retirer l'échéance")
        self.clear_deadline_button.setAccessibleName("Retirer l'échéance")
        self.clear_deadline_button.clicked.connect(self._clear_deadline)
        self.urgent_check = QCheckBox("Urgent")
        self.urgent_check.clicked.connect(self._commit_urgent)

        self.shift_spin = FocusSpinBox()
        self.shift_spin.setRange(1, MAX_SHIFT_DAYS)
        self.shift_spin.setSuffix(" j")
        self.shift_spin.setFixedWidth(SHIFT_SPIN_WIDTH)
        self.shift_spin.setAccessibleName("Jours ouvrés à décaler")
        self.shift_spin.setToolTip(SHIFT_TOOLTIP)
        self.earlier_button = button("Plus tôt", size="sm")
        self.earlier_button.setToolTip("Avancer les tâches sélectionnées")
        self.earlier_button.clicked.connect(lambda: self._request_shift(-1))
        self.later_button = button("Plus tard", size="sm")
        self.later_button.setToolTip("Retarder les tâches sélectionnées")
        self.later_button.clicked.connect(lambda: self._request_shift(1))

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

        self.findings_box = QWidget()
        self.findings_layout = QVBoxLayout(self.findings_box)
        self.findings_layout.setContentsMargins(0, 0, 0, 0)
        self.findings_layout.setSpacing(4)
        self.author_label = label("", "small", wrap=True)

        self.duplicate_button = button("Dupliquer", size="sm")
        self.duplicate_button.clicked.connect(lambda: self._request_action("duplicate"))
        self.detach_button = button("Détacher", size="sm")
        self.detach_button.clicked.connect(self._on_detach)
        self.unschedule_button = button("Renvoyer à planifier", size="sm")
        self.unschedule_button.setToolTip(
            "Retirer les dates : la tâche revient dans « À planifier »"
        )
        self.unschedule_button.clicked.connect(lambda: self._request_action("unschedule"))
        self.delete_button = button("Supprimer", "danger", size="sm")
        self.delete_button.setToolTip("Supprimer la tâche pour tous (Maj+Suppr)")
        self.delete_button.clicked.connect(lambda: self._request_action("delete"))
        actions_box = QWidget()
        actions = FlowLayout(actions_box, spacing=6)
        for action in (
            self.duplicate_button,
            self.detach_button,
            self.unschedule_button,
            self.delete_button,
        ):
            actions.addWidget(action)

        self.title_block = _field_block(_caption("Désignation"), self.title_edit)
        self.dossier_block = _field_block(_caption("Dossier"), self.dossier_edit)
        self.type_block = _field_block(_caption("Type"), self.type_combo)
        self.status_block = _field_block(_caption("Statut"), status_box)
        self.people_block = _field_block(people_head, self.people_box, self.person_edit)
        self.start_block = _field_block(
            _caption("Début"), _row(self.start_date, self.start_time, stretch_last=False)
        )
        self.end_block = _field_block(
            _caption("Fin"), _row(self.end_date, self.end_time), self.duration_label
        )
        self.estimate_block = _field_block(
            _caption("Estimation"), self.estimate_edit, self.estimate_label
        )
        self.night_block = _field_block(self.night_check, self.rest_label)
        self.deadline_row = _row(self.deadline_edit, self.clear_deadline_button)
        deadline_line = QWidget()
        deadline_layout = QHBoxLayout(deadline_line)
        deadline_layout.setContentsMargins(0, 0, 0, 0)
        deadline_layout.setSpacing(12)
        deadline_layout.addWidget(self.deadline_row, 1)
        deadline_layout.addWidget(self.urgent_check)
        deadline_layout.addStretch(0)
        self.deadline_caption = _caption("Échéance")
        self.deadline_block = _field_block(self.deadline_caption, deadline_line)
        self.shift_block = _field_block(
            _caption("Décaler en jours ouvrés"),
            _row(self.shift_spin, self.earlier_button, self.later_button),
        )
        self.notes_block = _field_block(_caption("Notes"), self.notes_edit)
        self.findings_block = _field_block(_caption("À vérifier"), self.findings_box)
        self.actions_box = actions_box

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
            self.dossier_card,
            self.title_block,
            self.dossier_block,
            self.type_block,
            self.status_block,
            self.people_block,
            self.start_block,
            self.end_block,
            self.estimate_block,
            self.night_block,
            self.deadline_block,
            self.shift_block,
            self.notes_block,
            self.findings_block,
        ):
            body_layout.addWidget(widget)
        self.actions_rule = rule()
        body_layout.addWidget(self.actions_rule)
        body_layout.addWidget(self.author_label)
        body_layout.addWidget(self.actions_box)
        body_layout.addStretch(1)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setWidget(body)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.header_box)
        root.addWidget(self.scroll_area, 1)

    def _date_edit(self, name: str) -> DateField:
        edit = DateField(self._locale)
        edit.setAccessibleName(name)
        return edit

    @staticmethod
    def _configure_completer(completer: QCompleter) -> None:
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setMaxVisibleItems(8)

    # -- public API -----------------------------------------------------------

    def mode(self) -> DetailMode:
        return self._mode

    def task_id(self) -> str:
        """Id of the task shown, empty when nothing or several tasks are shown."""
        return self._task.id if self._mode == "task" and self._task is not None else ""

    def focus_resource_id(self) -> str | None:
        """Person of the bar the detail was opened from, the one « Détacher » acts on."""
        return self._focus_resource_id

    def is_read_only(self) -> bool:
        return self._read_only

    def set_header_visible(self, *, visible: bool) -> None:
        """Show the « DÉTAIL » row and its back button, or hide it under the tabs."""
        self.header_box.setVisible(visible)

    def set_project_choices(self, projects: Sequence[ProjectInfo]) -> None:
        """Dossiers offered by the completion of the Dossier field."""
        self._projects = list(projects)
        self._project_by_text = {}
        texts: list[str] = []
        for project in self._projects:
            parts = [project.number, project.client, project.designation]
            text = " · ".join(part for part in parts if part)
            self._project_by_text[text] = project.number
            texts.append(text)
        model = self._dossier_completer.model()
        if hasattr(model, "setStringList"):
            model.setStringList(texts)

    def set_task(
        self,
        task: PlanningTask,
        snapshot: PlanningSnapshot,
        *,
        calendar: WorkCalendar,
        project: ProjectInfo | None = None,
        findings: Sequence[Finding] = (),
        read_only: bool = False,
        focus_resource_id: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """Show ``task``; a refresh of the same task keeps what is being typed."""
        same = self._mode == "task" and self._task is not None and self._task.id == task.id
        if not same:
            self.flush()
            self._stop_adding_person()
            self._focus_resource_id = None
        self._mode = "task"
        self._task = task
        self._snapshot = snapshot
        self._calendar = calendar
        self._project = project
        self._findings = unique_findings(findings)
        self._read_only = read_only
        self._now = now
        if focus_resource_id is not None:
            self._focus_resource_id = focus_resource_id
        if self._focus_resource_id not in task.assignments:
            self._focus_resource_id = None
        self._sent = {}
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
        every selected task). Edits are emitted with ``MULTI_SELECTION`` as task id.
        """
        self.flush()
        self._stop_adding_person()
        self._mode = "multi"
        self._task = None
        self._multi_count = count
        self._common = dict(common or {})
        if snapshot is not None:
            self._snapshot = snapshot
        self._read_only = read_only
        self._focus_resource_id = None
        self._sent = {}
        self._populate_multi()

    def clear(self) -> None:
        self.flush()
        self._stop_adding_person()
        self._mode = "empty"
        self._task = None
        self._focus_resource_id = None
        self._sent = {}
        self._show_blocks(set())
        self.empty_label.show()

    def flush(self) -> None:
        """Save at once what is being edited: texts, lists stepped through, waiting notes."""
        if self._mode == "multi":
            self.type_combo.commit()
        if self._mode != "task" or self._task is None:
            self._notes_timer.stop()
            for combo in (self.type_combo, self.start_time, self.end_time):
                combo.discard()
            return
        if self.title_edit.isModified():
            self._commit_title()
        if self.dossier_edit.isModified():
            self._commit_dossier()
        if self.estimate_edit.isModified():
            self._commit_estimate()
        for combo in (self.type_combo, self.start_time, self.end_time):
            combo.commit()
        if self._notes_timer.isActive():
            self._notes_timer.stop()
            self._commit_notes()

    # -- populating -----------------------------------------------------------

    def _show_blocks(self, visible: set[QWidget]) -> None:
        for widget in (
            self.empty_label,
            self.read_only_label,
            self.multi_label,
            self.multi_hint,
            self.dossier_card,
            self.title_block,
            self.dossier_block,
            self.type_block,
            self.status_block,
            self.people_block,
            self.start_block,
            self.end_block,
            self.estimate_block,
            self.night_block,
            self.deadline_block,
            self.shift_block,
            self.notes_block,
            self.findings_block,
            self.actions_rule,
            self.author_label,
            self.actions_box,
        ):
            widget.setVisible(widget in visible)

    def _populate_task(self, *, keep_edits: bool) -> None:
        task = self._task
        snapshot = self._snapshot
        if task is None or snapshot is None:
            return
        self._loading = True
        try:
            visible: set[QWidget] = {
                self.dossier_card,
                self.title_block,
                self.dossier_block,
                self.type_block,
                self.status_block,
                self.people_block,
                self.night_block,
                self.deadline_block,
                self.notes_block,
                self.actions_rule,
                self.author_label,
                self.actions_box,
            }
            visible |= (
                {self.start_block, self.end_block} if task.scheduled else {self.estimate_block}
            )
            if self._findings:
                visible.add(self.findings_block)
            if self._read_only:
                visible.add(self.read_only_label)
            self._show_blocks(visible)
            self.dossier_card.show_dossier(task, self._project)
            # Filled with the dossier's designation while the task has none of its own.
            self._set_line(
                self.title_edit, task.title or self._dossier_designation(task), keep=keep_edits
            )
            self.title_edit.setPlaceholderText("Désignation de la tâche")
            self._set_line(self.dossier_edit, task.project_number, keep=keep_edits)
            self._fill_types(task.task_type)
            self._check_status(task.status)
            self._fill_people(task)
            if not (keep_edits and self.required_spin.hasFocus()):
                self.required_spin.setValue(task.required_people)
            self._fill_dates(task, keep=keep_edits)
            self.night_check.setChecked(task.off_hours)
            night = task.off_hours and task.start is not None and task.end is not None
            self.rest_label.setVisible(night)
            if night and task.start is not None and task.end is not None:
                self.rest_label.setText(rest_text(task.start, task.end))
            if not (keep_edits and self.deadline_edit.hasFocus()):
                self._set_deadline(task.deadline)
            self.deadline_caption.setText("Échéance")
            self.deadline_row.show()
            self.urgent_check.setTristate(False)
            self.urgent_check.setChecked(task.urgent)
            typing_notes = self.notes_edit.hasFocus() or self._notes_timer.isActive()
            if not (keep_edits and typing_notes) and self.notes_edit.toPlainText() != task.notes:
                self.notes_edit.setPlainText(task.notes)
            self._fill_findings()
            self.author_label.setText(author_text(task, self._now or datetime.now(UTC)))
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
                self.status_block,
                self.people_block,
                self.deadline_block,
                self.shift_block,
                self.actions_rule,
                self.actions_box,
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
            status = self._common.get("status")
            self._check_status(status if isinstance(status, str) else None)
            self.people_caption.setText("Personnes communes")
            self.required_caption.hide()
            self.required_spin.hide()
            ids = self._common.get("resource_ids", ())
            people = [str(item) for item in ids] if isinstance(ids, (list, tuple)) else []
            self._fill_chips(people, foreseen=False)
            self._fill_person_choices(None)
            self.deadline_caption.setText("Priorité")
            self.deadline_row.hide()
            urgent = self._common.get("urgent")
            self.urgent_check.setTristate(not isinstance(urgent, bool))
            if isinstance(urgent, bool):
                self.urgent_check.setChecked(urgent)
            else:
                self.urgent_check.setCheckState(Qt.CheckState.PartiallyChecked)
            self.detach_button.hide()
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

    def _fill_types(self, selected: str | None) -> None:
        snapshot = self._snapshot
        theme = current_theme()
        if self.type_combo.pending():
            # Stepped to with the keys, not saved yet: keep it through a refresh.
            stepped = self.type_combo.currentData()
            selected = stepped if isinstance(stepped, str) else selected
        self.type_combo.clear()
        kinds = list(snapshot.meta.task_types) if snapshot is not None else []
        if snapshot is not None and selected and all(kind.key != selected for kind in kinds):
            # A type removed from the shared list shows as the one its bar takes.
            selected = snapshot.meta.task_type(selected).key
        for kind in kinds:
            color = kind.color_dark if theme.is_dark else kind.color
            self.type_combo.addItem(_swatch(color), kind.label, kind.key)
        self.type_combo.setPlaceholderText("Plusieurs types")
        self.type_combo.setCurrentIndex(self.type_combo.findData(selected) if selected else -1)

    def _check_status(self, status: str | None) -> None:
        self.status_buttons.setExclusive(False)
        for choice in self.status_buttons.buttons():
            choice.setChecked(choice.property("status") == status)
        self.status_buttons.setExclusive(True)

    def _fill_people(self, task: PlanningTask) -> None:
        count = len(task.assignments)
        if task.scheduled:
            caption = "Affectés"
            if task.required_people > count:
                caption += f" · {count} sur {task.required_people}"
        else:
            caption = "Pressentis"
        self.people_caption.setText(caption)
        self.required_caption.show()
        self.required_spin.show()
        self._fill_chips(list(task.assignments), foreseen=not task.scheduled)
        self._fill_person_choices(task)

    def _fill_chips(self, resource_ids: Sequence[str], *, foreseen: bool) -> None:
        snapshot = self._snapshot
        self.people_flow.removeWidget(self.add_person_button)
        self.people_flow.clear()
        removable = not self._read_only
        for resource_id in resource_ids:
            resource = snapshot.resources.get(resource_id) if snapshot is not None else None
            text = resource.short_label if resource is not None else "?"
            name = resource.name if resource is not None else "Personne inconnue"
            chip = PersonChip(
                resource_id,
                text,
                f"Retirer {name}" if removable else name,
                foreseen=foreseen,
                removable=removable,
            )
            chip.remove_requested.connect(self._remove_person)
            self.people_flow.addWidget(chip)
        self.people_flow.addWidget(self.add_person_button)
        # While a person is being typed, the field stands in for « + ajouter ».
        self.add_person_button.setVisible(not self._read_only and self.person_edit.isHidden())
        self.people_box.updateGeometry()

    def chips(self) -> list[PersonChip]:
        """People chips shown, in order."""
        items = (self.people_flow.itemAt(index) for index in range(self.people_flow.count()))
        return [
            widget
            for item in items
            if item is not None and isinstance(widget := item.widget(), PersonChip)
        ]

    def _fill_person_choices(self, task: PlanningTask | None) -> None:
        snapshot = self._snapshot
        self._person_by_text = {}
        if snapshot is None:
            return
        calendar_for = calendar_resolver(snapshot)
        taken = set(task.assignments) if task is not None else set()
        entries: list[tuple[int, str, str, str]] = []
        for resource in snapshot.active_resources():
            if resource.id in taken:
                continue
            team = snapshot.meta.team(resource.team)
            state = (
                availability(snapshot, resource, task, calendar_for(resource.id))
                if task is not None
                else ""
            )
            parts = [resource.name, team.name if team is not None else "", state]
            text = f"{resource.initials or '?'}  " + " · ".join(part for part in parts if part)
            rank = availability_rank(state)
            entries.append((rank, resource.name.casefold(), text, resource.id))
        entries.sort()
        model = self._person_completer.model()
        if hasattr(model, "setStringList"):
            model.setStringList([text for _rank, _name, text, _rid in entries])
        self._person_by_text = {text: rid for _rank, _name, text, rid in entries}

    def person_choices(self) -> list[str]:
        """Texts offered by « + ajouter »: people available first, then partly or all away."""
        return list(self._person_by_text)

    def _fill_dates(self, task: PlanningTask, *, keep: bool) -> None:
        """Show the dates of ``task``; with ``keep``, a start or an end being edited stays."""
        calendar = self._calendar
        if task.start is None or task.end is None or calendar is None:
            self._show_estimate_error(error=False)
            if not (self.estimate_edit.hasFocus() and self.estimate_edit.isModified()):
                self.estimate_edit.setText(estimate_text(task))
                self.estimate_edit.setModified(False)
            return
        start_day, end_day = wall_date(task.start), wall_date(task.end)
        if not (keep and (self.start_date.hasFocus() or self.start_time.pending())):
            self.start_time.discard()
            self.start_date.setDate(_qdate(start_day))
            self._fill_times(self.start_time, start_day, minute_of_day(task.start), end=False)
        if not (keep and (self.end_date.hasFocus() or self.end_time.pending())):
            self.end_time.discard()
            self.end_date.setDate(_qdate(end_day))
            self._fill_times(self.end_time, end_day, minute_of_day(task.end), end=True)
        if task.off_hours:
            self.duration_label.setText(f"Durée : {elapsed_text(task.end - task.start)} (nuit)")
            return
        worked = calendar.work_between(task.start, task.end)
        days = calendar.whole_days(task.start, task.end)
        duration = format_duration(worked)
        if days is not None:
            duration = f"{format_days(days)} · {duration}"
        self.duration_label.setText(f"Durée : {duration}")

    def _fill_times(self, combo: QComboBox, day: date, selected: int | None, *, end: bool) -> None:
        choices = self._time_choices(day, end=end)
        blocker = QSignalBlocker(combo)
        combo.clear()
        for minute in choices:
            combo.addItem(format_hhmm(minute), minute)
        if selected is not None and selected not in choices:
            # A time outside the hours (an older task): shown as it is, never offered.
            position = sum(1 for minute in choices if minute < selected)
            combo.insertItem(position, format_hhmm(selected) + OFF_HOURS_SUFFIX, selected)
        if selected is not None:
            combo.setCurrentIndex(combo.findData(selected))
        del blocker

    def _set_deadline(self, deadline: date | None) -> None:
        self.deadline_edit.setDate(_qdate(deadline) if deadline is not None else NO_DEADLINE)
        self.deadline_edit.blank_date = _qdate(self._deadline_start())
        self.clear_deadline_button.setVisible(deadline is not None and not self._read_only)

    def _deadline_start(self) -> date:
        """Day a deadline is picked from: the last day of the task, else today."""
        task = self._task
        if task is not None and task.end is not None:
            return wall_date(task.end - 1)
        return (self._now or datetime.now(UTC)).astimezone().date()

    def _fill_findings(self) -> None:
        while self.findings_layout.count():
            item = self.findings_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        for finding in self._findings:
            line = QWidget()
            layout = QHBoxLayout(line)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)
            layout.addWidget(IconLabel("alert", "err", 14), 0, Qt.AlignmentFlag.AlignTop)
            text = label(finding_text(finding, self._snapshot), "small", tone="error", wrap=True)
            text.setToolTip(finding.label)
            layout.addWidget(text, 1)
            self.findings_layout.addWidget(line)

    def _fill_actions(self, task: PlanningTask) -> None:
        several = len(task.assignments) > 1
        self.detach_button.setVisible(several)
        focus = self._focus_resource_id
        snapshot = self._snapshot
        resource = snapshot.resources.get(focus) if focus and snapshot is not None else None
        self.detach_button.setText(
            f"Détacher {resource.short_label}" if resource is not None else "Détacher…"
        )
        self.detach_button.setToolTip("La part de cette personne devient une tâche indépendante")
        self.unschedule_button.setVisible(task.scheduled)
        self.duplicate_button.show()

    def _apply_read_only(self) -> None:
        editable = not self._read_only
        for edit in (self.title_edit, self.dossier_edit, self.person_edit, self.estimate_edit):
            edit.setReadOnly(not editable)
        self.notes_edit.setReadOnly(not editable)
        for date_edit in (self.start_date, self.end_date, self.deadline_edit):
            date_edit.setReadOnly(not editable)
        self.required_spin.setReadOnly(not editable)
        for widget in (
            self.type_combo,
            self.start_time,
            self.end_time,
            self.urgent_check,
            self.night_check,
            self.shift_spin,
            self.earlier_button,
            self.later_button,
            self.duplicate_button,
            self.detach_button,
            self.unschedule_button,
            self.delete_button,
            *self.status_buttons.buttons(),
        ):
            widget.setEnabled(editable)
        if not editable:
            self.person_edit.hide()
            self.add_person_button.hide()

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
        if self._mode != "task":
            return
        text = self.title_edit.text().strip()
        task = self._task
        if task is not None and not task.title and text == self._dossier_designation(task):
            return  # Still the dossier's designation: nothing typed, nothing to save.
        self._commit_value("title", text)

    def _commit_dossier(self) -> None:
        if self._mode == "task":
            self._commit_value("project_number", self._resolve_project(self.dossier_edit.text()))

    def _resolve_project(self, text: str) -> str:
        """Return the number typed or chosen; ``5231`` completes to the dossier ``…-5231``."""
        value = text.strip()
        if value in self._project_by_text:
            return self._project_by_text[value]
        if value.isdigit():
            matches = [p.number for p in self._projects if p.number.endswith(f"-{value}")]
            if len(matches) == 1:
                return matches[0]
        return value

    def _on_project_chosen(self, text: str) -> None:
        number = self._project_by_text.get(text)
        if number is None:
            return
        self.dossier_edit.setText(number)
        self.dossier_edit.setModified(True)
        self._commit_dossier()

    def _commit_type(self, index: int) -> None:
        key = self.type_combo.itemData(index)
        if isinstance(key, str):
            self._commit_value("task_type", key)

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
        self.status_changed.emit(target, status)

    def _commit_required(self) -> None:
        if self._mode == "task":
            self._commit_value("required_people", self.required_spin.value())

    def _commit_urgent(self, checked: bool) -> None:  # noqa: FBT001
        if self._mode == "multi" and self.urgent_check.isTristate():
            self.urgent_check.setTristate(False)
            self.urgent_check.setChecked(checked)
        self._commit_value("urgent", bool(checked))

    def _commit_night(self, checked: bool) -> None:  # noqa: FBT001
        if self._mode == "task":
            self._commit_value("off_hours", bool(checked))

    def _time_choices(self, day: date, *, end: bool) -> list[int]:
        """Hours offered: worked half-hours, every half hour of the day for night work."""
        task = self._task
        if task is not None and task.off_hours:
            return list(NIGHT_TIMES)
        calendar = self._calendar
        return time_choices(calendar, day, end=end) if calendar is not None else []

    def _commit_deadline(self) -> None:
        if self._mode != "task":
            return
        value = self.deadline_edit.date()
        deadline = None if value <= NO_DEADLINE else _pydate(value)
        self.clear_deadline_button.setVisible(deadline is not None and not self._read_only)
        self._commit_value("deadline", deadline)

    def _clear_deadline(self) -> None:
        self.deadline_edit.setDate(NO_DEADLINE)
        self._commit_deadline()

    def _on_notes_edited(self) -> None:
        if not self._loading and self._mode == "task" and not self._read_only:
            self._notes_timer.start()

    def _commit_notes(self) -> None:
        if self._mode == "task":
            self._commit_value("notes", self.notes_edit.toPlainText())

    def _commit_estimate(self) -> None:
        task = self._task
        if self._mode != "task" or task is None or task.scheduled or self._target() is None:
            return
        parsed = parse_estimate(self.estimate_edit.text())
        if parsed is None:
            self._show_estimate_error(error=True)
            return
        self._show_estimate_error(error=False)
        days, minutes = parsed
        # Days win over minutes: typing hours clears the days first, typing days is enough.
        if days > 0 or not minutes:
            self._commit_value("estimate_days", days)
        if not days:
            if self._sent.get("estimate_days", task.estimate_days):
                self._commit_value("estimate_days", 0.0)
            self._commit_value("estimate_minutes", minutes)
        shown = format_days(days) if days else format_duration(minutes) if minutes else ""
        self.estimate_edit.setText(shown)
        self.estimate_edit.setModified(False)

    def _show_estimate_error(self, *, error: bool) -> None:
        self.estimate_label.setText(ESTIMATE_ERROR if error else ESTIMATE_HINT)
        set_tone(self.estimate_label, "error" if error else None)

    def _on_start_date_changed(self, value: QDate) -> None:
        if self._loading:
            return
        self._fill_times(
            self.start_time, _pydate(value), self._closest(self.start_time, end=False), end=False
        )

    def _on_end_date_changed(self, value: QDate) -> None:
        if self._loading:
            return
        self._fill_times(
            self.end_time, _pydate(value), self._closest(self.end_time, end=True), end=True
        )

    def _closest(self, combo: QComboBox, *, end: bool) -> int | None:
        """Time kept when the day changes: the same, or the nearest worked one of that day."""
        current = combo.currentData()
        calendar = self._calendar
        if not isinstance(current, int) or calendar is None:
            return None
        edit = self.end_date if end else self.start_date
        choices = self._time_choices(_pydate(edit.date()), end=end)
        if not choices or current in choices:
            return current if choices else None
        return min(choices, key=lambda minute: (abs(minute - current), minute))

    @staticmethod
    def _picked(edit: QDateEdit, combo: QComboBox, *, end: bool) -> WallTime:
        day = _pydate(edit.date())
        minute = combo.currentData()
        if isinstance(minute, int):
            return day_start(day) + minute
        # A day without hours (weekend): the calendar moves it to the nearest worked time.
        return day_start(day) + (MINUTES_PER_DAY if end else 0)

    def _commit_start(self) -> None:
        task, calendar = self._task, self._calendar
        target = self._target()
        if target is None or self._mode != "task" or task is None or calendar is None:
            return
        if task.start is None or task.end is None:
            return
        picked = self._picked(self.start_date, self.start_time, end=False)
        if task.off_hours:
            # Night work keeps its exact times and its real length.
            wanted = snap(picked)
            if wanted == self._sent.get("start", task.start):
                return
            start, end = wanted, wanted + (task.end - task.start)
            self._sent["start"], self._sent["end"] = start, end
            self.dates_changed.emit(target, start, end)
            return
        wanted = calendar.working_start(picked)
        if wanted == self._sent.get("start", task.start):
            return
        start, end = move_preserving_work(task.start, task.end, wanted, calendar)
        self._sent["start"], self._sent["end"] = start, end
        self.dates_changed.emit(target, start, end)

    def _commit_end(self) -> None:
        task, calendar = self._task, self._calendar
        target = self._target()
        if target is None or self._mode != "task" or task is None or calendar is None:
            return
        if task.start is None or task.end is None:
            return
        picked = self._picked(self.end_date, self.end_time, end=True)
        start = self._sent.get("start", task.start)
        if not isinstance(start, int):
            return
        if task.off_hours:
            wanted = max(snap(picked), start + SNAP_MINUTES)
        else:
            wanted = calendar.working_end(picked)
        if wanted <= start:
            wanted = calendar.add_work(start, SNAP_MINUTES)
        if wanted == self._sent.get("end", task.end):
            return
        self._sent["end"] = wanted
        self.dates_changed.emit(target, start, wanted)

    def _request_shift(self, direction: int) -> None:
        """Move every selected task by the working days of the field, later or earlier."""
        target = self._target()
        if target is None or self._mode != "multi":
            return
        self.shift_requested.emit(target, direction * self.shift_spin.value())

    # -- people -----------------------------------------------------------------

    def _start_adding_person(self) -> None:
        if self._read_only:
            return
        self.add_person_button.hide()
        self.person_edit.clear()
        self.person_edit.show()
        self.person_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self._person_completer.setCompletionPrefix("")
        self._person_completer.complete()

    def _stop_adding_person(self) -> None:
        self.person_edit.hide()
        self.person_edit.clear()
        self.add_person_button.setVisible(not self._read_only)

    def _on_person_chosen(self, text: str) -> None:
        resource_id = self._person_by_text.get(text)
        if resource_id is not None:
            self._add_person(resource_id)
        QTimer.singleShot(0, self, self._stop_adding_person)

    def _commit_person_text(self) -> None:
        text = self.person_edit.text().strip()
        resource_id = self._person_by_text.get(text) or self._find_person(text)
        if resource_id:
            self._add_person(resource_id)
            self._stop_adding_person()

    def _find_person(self, text: str) -> str:
        """Person typed by initials (any case), or by a name only one person matches."""
        snapshot = self._snapshot
        wanted = fold_text(text).strip()
        if snapshot is None or not wanted:
            return ""
        people = [
            resource
            for resource in snapshot.active_resources()
            if resource.id in self._person_by_text.values()
        ]
        exact = [person for person in people if fold_text(person.initials) == wanted]
        if len(exact) == 1:
            return exact[0].id
        named = [person for person in people if wanted in fold_text(person.name)]
        return named[0].id if len(named) == 1 else ""

    def _add_person(self, resource_id: str) -> None:
        target = self._target()
        if target is None:
            return
        task = self._task
        if self._mode == "task" and task is not None and resource_id in task.assignments:
            return
        self.people_changed.emit(target, [resource_id], [])

    def _remove_person(self, resource_id: str) -> None:
        target = self._target()
        if target is not None:
            self.people_changed.emit(target, [], [resource_id])

    # -- actions ----------------------------------------------------------------

    def _request_action(self, action: str) -> None:
        if self._mode == "task" and self._task is not None:
            target = self._task.id
        elif self._mode == "multi":
            target = MULTI_SELECTION
        else:
            return
        navigation = {key for key, _text, _tip in DOSSIER_ACTIONS}
        if self._read_only and action not in navigation:
            return
        self.flush()
        self.action_requested.emit(target, action)

    def _on_detach(self) -> None:
        task = self._task
        if task is None or self._read_only:
            return
        if self._focus_resource_id in task.assignments:
            self._request_action("detach")
            return
        menu = self.detach_menu()
        menu.exec(self.detach_button.mapToGlobal(self.detach_button.rect().bottomLeft()))
        menu.deleteLater()

    def detach_menu(self) -> QMenu:
        """Menu of the people of the task, when the detail was not opened from a bar."""
        menu = QMenu(self)
        task, snapshot = self._task, self._snapshot
        if task is None or snapshot is None:
            return menu
        for resource_id in task.assignments:
            resource = snapshot.resources.get(resource_id)
            name = resource.name if resource is not None else resource_id
            action = menu.addAction(f"Détacher {name}")
            action.triggered.connect(
                lambda _checked=False, rid=resource_id: self._detach_person(rid)
            )
        return menu

    def _detach_person(self, resource_id: str) -> None:
        self._focus_resource_id = resource_id
        self._request_action("detach")

    # -- events -----------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.FocusOut:
            if watched is self.title_edit:
                self._commit_title()
            elif watched is self.dossier_edit and not _popup_visible(self._dossier_completer):
                self._commit_dossier()
            elif watched is self.notes_edit and self._notes_timer.isActive():
                self._notes_timer.stop()
                self._commit_notes()
            elif watched is self.person_edit and not _popup_visible(self._person_completer):
                QTimer.singleShot(0, self, self._stop_adding_person)
        elif (
            event.type() == QEvent.Type.KeyPress
            and watched is self.person_edit
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Escape
        ):
            self._stop_adding_person()
            return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.flush()
            self.back_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _on_theme(self) -> None:
        self.setStyleSheet(_date_style(current_theme()))
        if self._mode == "task" and self._task is not None:
            self._loading = True
            try:
                self._fill_types(self._task.task_type)
            finally:
                self._loading = False
        elif self._mode == "multi":
            self._loading = True
            try:
                common_type = self._common.get("task_type")
                self._fill_types(common_type if isinstance(common_type, str) else None)
            finally:
                self._loading = False

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), current_theme().color("bg"))
        painter.end()
