"""« Équipe du planning »: the shared roster, its teams and the workshop calendar.

The dialog never touches the planning storage. Every edit is a command builder handed
to ``apply`` with a French label (one undo step); the caller builds the
``CommandContext`` (author, time, calendars) and applies the changes, then the pages are
read again from ``snapshot_provider``. Builders read the context's snapshot, not the one
shown, so an edit never reverts what another poste changed meanwhile.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date
from typing import Final, Literal

from PySide6.QtCore import (
    QDate,
    QModelIndex,
    QPersistentModelIndex,
    QPointF,
    QRectF,
    QSignalBlocker,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
    QKeyEvent,
    QPainter,
    QPen,
    QPixmap,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDateEdit,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from projectflow.core.planning.calendar import CANTONS, DAYS_PER_WEEK, WorkWeek
from projectflow.core.planning.changes import DocumentChange, PlanningSnapshot
from projectflow.core.planning.commands import (
    CommandContext,
    PlanningCommandError,
    archive_resource,
    create_resource,
    delete_absence,
    reorder_resource,
    replace_task_types,
    retyped_tasks,
    update_meta,
    update_resource,
)
from projectflow.core.planning.conflicts import NOON_MINUTE
from projectflow.core.planning.models import (
    ABSENCE_LABELS,
    Absence,
    AbsenceReason,
    Closure,
    DayPart,
    RecurringOff,
    Resource,
    ResourceKind,
    TaskType,
    Team,
)
from projectflow.core.planning.quick_entry import fold_text
from projectflow.core.planning.task_types import (
    SIMPLE_TASK_TYPES,
    TYPE_PALETTE,
    dark_variant,
    keywords_text,
    new_type_key,
    next_palette_color,
    parse_keywords,
    simple_mapping,
)
from projectflow.core.planning.walltime import MINUTES_PER_HOUR, format_hhmm
from projectflow.exceptions import ProjectFlowError
from projectflow.ui.dialogs.planning_absence import (
    AbsenceDialog,
    date_edit_style_sheet,
    short_day,
)
from projectflow.ui.theme import current_theme, theme_notifier
from projectflow.ui.widgets.industry import (
    BlueprintFrame,
    button,
    field,
    label,
    rule,
)

CommandBuilder = Callable[[CommandContext], tuple[DocumentChange, ...]]
"""Builds the changes of one edit from the caller's context."""
ApplyCommand = Callable[[CommandBuilder, str], None]
"""Runs a builder on a fresh context and applies its changes under the label."""
PlannerMember = tuple[str, str, str]
"""Microsoft 365 member: id, display name, e-mail."""
TeamPageKey = Literal["people", "absences", "teams", "types", "calendar"]
ReadOnlyReason = Callable[[], str]
"""Why the planning cannot be changed right now, "" when it can."""

DIALOG_MIN_WIDTH: Final = 1100
"""Room for the Personnes form beside the roster without a horizontal scroll bar."""
DIALOG_MIN_HEIGHT: Final = 600
DIALOG_WIDTH: Final = 1180
DIALOG_HEIGHT: Final = 760
NAV_WIDTH: Final = 230
ROSTER_WIDTH: Final = 296
FORM_MAX_WIDTH: Final = 620
PAGE_MAX_WIDTH: Final = 860
INITIALS_EDIT_WIDTH: Final = 110
INITIALS_MAX_LENGTH: Final = 4
ALIAS_EDIT_WIDTH: Final = 170
MENU_ARROW_ROOM: Final = 30
MENU_ARROW_INSET: Final = 10
COMBO_MIN_CHARACTERS: Final = 8
HEADER_ROW_HEIGHT: Final = 36
RESOURCE_ROW_HEIGHT: Final = 44
INITIALS_BOX_WIDTH: Final = 42
INITIALS_BOX_HEIGHT: Final = 26
ROW_PADDING: Final = 12
RAIL_WIDTH: Final = 2
HEADER_TEXT_SIZE: Final = 11
NAME_TEXT_SIZE: Final = 14
DETAIL_TEXT_SIZE: Final = 12
INITIALS_TEXT_SIZE: Final = 13
CAPS_SPACING: Final = 1.1
ARCHIVED_ALPHA: Final = 0.45
DETAIL_SHARE: Final = 0.5
SWATCH_SIZE: Final = 14
TYPES_PAGE_MAX_WIDTH: Final = 980
TYPE_NAME_MIN_WIDTH: Final = 180
"""Wider than the other pages: the keywords of a type make a long line."""
TYPE_DIALOG_WIDTH: Final = 440
WORKDAYS: Final = 5
"""Monday to Friday: Saturday and Sunday are never shown in the planning."""

PAGE_TITLES: Mapping[TeamPageKey, str] = {
    "people": "Personnes",
    "absences": "Absences",
    "teams": "Équipes",
    "types": "Types de tâche",
    "calendar": "Calendrier",
}
WEEKDAY_NAMES: tuple[str, ...] = ("lundi", "mardi", "mercredi", "jeudi", "vendredi")
WEEKDAY_SHORT: tuple[str, ...] = ("lun", "mar", "mer", "jeu", "ven", "sam", "dim")
DAY_PART_LABELS: Mapping[DayPart, str] = {
    "day": "Journée",
    "morning": "Matin",
    "afternoon": "Après-midi",
}
_DAY_PARTS: tuple[DayPart, ...] = ("day", "morning", "afternoon")
_PART_SUFFIXES: Mapping[DayPart, str] = {"day": "", "morning": " matin", "afternoon": " après-midi"}
_REASONS: tuple[AbsenceReason, ...] = (
    "ecole",
    "cie",
    "conge",
    "formation",
    "absent",
    "militaire",
    "recuperation",
    "vacances",
)
CANTON_NAMES: Mapping[str, str] = {"GE": "Genève", "VD": "Vaud"}
DEFAULT_CLOSURE_LABEL: Final = "Fermeture"

_KIND_ROLE = Qt.ItemDataRole.UserRole + 1
_INITIALS_ROLE = Qt.ItemDataRole.UserRole + 2
_DETAIL_ROLE = Qt.ItemDataRole.UserRole + 3
_ARCHIVED_ROLE = Qt.ItemDataRole.UserRole + 4
_COUNT_ROLE = Qt.ItemDataRole.UserRole + 5
_ID_ROLE = Qt.ItemDataRole.UserRole
_ALIAS_RE = re.compile(r"^[a-z0-9_-]*$")
_WORD_SPLIT_RE = re.compile(r"[\s'-]+")
"""Word separators, on text folded first (typographic apostrophes made plain)."""
_SLUG_RE = re.compile(r"[^a-z0-9]+")

_Run = Callable[[CommandBuilder, str, QLabel], bool]


# ---------------------------------------------------------------- helpers ----


def propose_initials(name: str, taken: Iterable[str] = ()) -> str:
    """Propose initials as on the fiches (« Kevin Duc » -> « KD »), avoiding ``taken``.

    When the first letters are taken, more letters of the last then of the first name
    are used (« KDU », « KED »), then a number.
    """
    words = [word for word in _WORD_SPLIT_RE.split(fold_text(name).upper()) if word]
    words = ["".join(char for char in word if char.isalnum()) for word in words]
    words = [word for word in words if word]
    if not words:
        return ""
    used = {_initials_key(item) for item in taken}
    first, last = words[0], words[-1]
    candidates = ["".join(word[0] for word in words)[: INITIALS_MAX_LENGTH - 1]]
    if len(words) == 1:
        candidates += [first[:2], first[:3]]
    else:
        candidates += [first[0] + last[:2], first[:2] + last[0], first[0] + last[:3]]
    for candidate in candidates:
        if candidate and _initials_key(candidate) not in used:
            return candidate
    base = candidates[0]
    number = 2
    while _initials_key(f"{base}{number}") in used:
        number += 1
    return f"{base}{number}"


def work_week_text(week: WorkWeek) -> str:
    """Hours of the week, e.g. « Lun-jeu 06:30-12:00 · 13:00-16:30, ven 06:30-12:00 (41 h 30) »."""
    groups: list[tuple[int, int, tuple[tuple[int, int], ...]]] = []
    for weekday in range(DAYS_PER_WEEK):
        periods = tuple(week.get(weekday, ()))
        if not periods:
            continue
        if groups and groups[-1][1] == weekday - 1 and groups[-1][2] == periods:
            groups[-1] = (groups[-1][0], weekday, periods)
        else:
            groups.append((weekday, weekday, periods))
    parts = []
    for first, last, periods in groups:
        days = WEEKDAY_SHORT[first]
        if last != first:
            days = f"{days}-{WEEKDAY_SHORT[last]}"
        hours = " · ".join(f"{format_hhmm(start)}-{format_hhmm(end)}" for start, end in periods)
        parts.append(f"{days} {hours}")
    total = sum(end - start for periods in week.values() for start, end in periods)
    text = ", ".join(parts) or "aucune heure"
    return f"{text[:1].upper()}{text[1:]} ({hours_text(total)})"


def hours_text(minutes: int) -> str:
    """``2490`` -> « 41 h 30 », ``2520`` -> « 42 h »."""
    hours, rest = divmod(minutes, MINUTES_PER_HOUR)
    return f"{hours} h {rest:02d}" if rest else f"{hours} h"


def recurring_off_text(entry: RecurringOff) -> str:
    """« Mercredi · Journée · École professionnelle »."""
    weekday = _weekday_name(entry.weekday).capitalize()
    reason = ABSENCE_LABELS.get(entry.reason, entry.reason)
    return f"{weekday} · {DAY_PART_LABELS[entry.part]} · {reason}"


def closure_text(closure: Closure) -> str:
    """« Du 21.12.2026 au 01.01.2027 · Fin d'année »."""
    first = closure.first_day.strftime("%d.%m.%Y")
    if closure.last_day == closure.first_day:
        return f"Le {first} · {closure.label}"
    return f"Du {first} au {closure.last_day.strftime('%d.%m.%Y')} · {closure.label}"


def _weekday_name(weekday: int) -> str:
    if 0 <= weekday < len(WEEKDAY_NAMES):
        return WEEKDAY_NAMES[weekday]
    return WEEKDAY_SHORT[weekday % DAYS_PER_WEEK]


def _overlaps(first: RecurringOff, second: RecurringOff) -> bool:
    if first.weekday != second.weekday:
        return False
    return first.part == second.part or "day" in {first.part, second.part}


def _off_clash_text(weekday: int) -> str:
    return f"Le {_weekday_name(weekday)} est déjà indiqué : retirez-le d'abord."


def _sorted_off(entries: Iterable[RecurringOff]) -> tuple[RecurringOff, ...]:
    return tuple(sorted(entries, key=lambda item: (item.weekday, _DAY_PARTS.index(item.part))))


def _has_afternoon(week: WorkWeek, weekday: int) -> bool:
    """Whether ``weekday`` is worked after its midday split, as the planning cuts a day."""
    periods = tuple(week.get(weekday, ()))
    split = periods[0][1] if len(periods) > 1 else NOON_MINUTE
    return any(end > split for _start, end in periods)


def _initials_key(initials: str) -> str:
    # As the quick entry indexes initials: « ŠR » and « sr » are the same person.
    return fold_text(initials.strip())


def _initials_owner(
    snapshot: PlanningSnapshot,
    initials: str,
    *,
    exclude: str | None,
) -> Resource | None:
    """Shown row already using ``initials`` (case and accents ignored), other than ``exclude``."""
    wanted = _initials_key(initials)
    if not wanted:
        return None
    return next(
        (
            row
            for row in snapshot.active_resources()
            if row.id != exclude and _initials_key(row.initials) == wanted
        ),
        None,
    )


def _taken_initials(snapshot: PlanningSnapshot, *, exclude: str | None = None) -> list[str]:
    return [row.initials for row in snapshot.active_resources() if row.id != exclude]


def _sorted_teams(teams: Iterable[Team]) -> list[Team]:
    return sorted(teams, key=lambda team: (team.order, team.name.casefold()))


def _team_name(snapshot: PlanningSnapshot, key: str) -> str:
    team = snapshot.meta.team(key)
    return team.name if team is not None else key


def _slug(text: str) -> str:
    return _SLUG_RE.sub("", fold_text(text))


def _error_label() -> QLabel:
    widget = label("", "small", tone="error", wrap=True)
    widget.hide()
    return widget


def _show_message(widget: QLabel, message: str) -> None:
    widget.setText(message)
    widget.setVisible(bool(message))


def _set_text(edit: QLineEdit, text: str) -> None:
    """Show ``text`` unless the user is typing in ``edit`` right now."""
    if edit.hasFocus() and edit.isModified():
        return
    if edit.text() != text:
        with QSignalBlocker(edit):
            edit.setText(text)
    edit.setModified(False)


def _qdate(day: date) -> QDate:
    return QDate(day.year, day.month, day.day)


def _pydate(day: QDate) -> date:
    return date(day.year(), day.month(), day.day())


def _clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            break
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.deleteLater()
        child = item.layout()
        if child is not None:
            _clear_layout(child)


def _icon_button(icon: str, tooltip: str) -> QPushButton:
    widget = button("", "icon", icon=icon)
    widget.setToolTip(tooltip)
    widget.setAccessibleName(tooltip)
    return widget


def _scroll(content: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setWidget(content)
    return area


class SegmentedChoice(QWidget):
    """Row of exclusive « seg » buttons, one per key; ``chosen`` fires on a click only."""

    chosen = Signal(str)

    def __init__(
        self,
        options: Iterable[tuple[str, str]],
        current: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(-1)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for key, text in options:
            option = button(text, "seg")
            option.setCheckable(True)
            option.setProperty("choice_key", key)
            self._group.addButton(option)
            layout.addWidget(option)
            self._buttons[key] = option
        layout.addStretch(1)
        self.set_current(current)
        self._group.buttonClicked.connect(self._clicked)

    def current(self) -> str:
        checked = self._group.checkedButton()
        return str(checked.property("choice_key")) if checked is not None else ""

    def set_current(self, key: str) -> None:
        option = self._buttons.get(key)
        if option is not None:
            option.setChecked(True)

    def set_text(self, key: str, text: str) -> None:
        option = self._buttons.get(key)
        if option is not None:
            option.setText(text)

    def option(self, key: str) -> QPushButton:
        return self._buttons[key]

    def _clicked(self, clicked: QAbstractButton) -> None:
        self.chosen.emit(str(clicked.property("choice_key")))


class _RosterDelegate(QStyledItemDelegate):
    """Team headers in caps over a hairline; rows with initials, name and details."""

    def sizeHint(  # noqa: N802
        self,
        option: QStyleOptionViewItem,  # noqa: ARG002 - rows take the list's width
        index: QModelIndex | QPersistentModelIndex,
    ) -> QSize:
        # No width of its own: the list stretches each row to its viewport and the
        # painting elides the name, so a long name never widens the roster.
        height = HEADER_ROW_HEIGHT if index.data(_KIND_ROLE) == "team" else RESOURCE_ROW_HEIGHT
        return QSize(0, height)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect)
        if index.data(_KIND_ROLE) == "team":
            self._paint_team(painter, option, index, rect)
        else:
            self._paint_resource(painter, option, index, rect)
        painter.restore()

    def _paint_team(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
        rect: QRectF,
    ) -> None:
        theme = current_theme()
        font = QFont(option.font)
        font.setPixelSize(HEADER_TEXT_SIZE)
        font.setWeight(QFont.Weight.Medium)
        font.setCapitalization(QFont.Capitalization.AllUppercase)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, CAPS_SPACING)
        painter.setFont(font)
        painter.setPen(theme.color("neutral-800"))
        text_rect = rect.adjusted(ROW_PADDING, 0, -ROW_PADDING, -6)
        bottom_left = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        bottom_right = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        painter.drawText(text_rect, bottom_left, str(index.data(Qt.ItemDataRole.DisplayRole)))
        painter.drawText(text_rect, bottom_right, str(index.data(_COUNT_ROLE) or "0"))
        pen = QPen(theme.color("text", 0.30))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        y = rect.bottom() - 0.5
        painter.drawLine(
            QPointF(rect.left() + ROW_PADDING, y), QPointF(rect.right() - ROW_PADDING, y)
        )

    def _paint_resource(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
        rect: QRectF,
    ) -> None:
        theme = current_theme()
        state = option.state
        selected = bool(state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.fillRect(rect, theme.color("accent-100"))
            painter.fillRect(
                QRectF(rect.left(), rect.top(), RAIL_WIDTH, rect.height()),
                theme.color("accent"),
            )
        elif state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(rect, theme.color("text", 0.04))
        archived = bool(index.data(_ARCHIVED_ROLE))
        alpha = ARCHIVED_ALPHA if archived else 1.0
        box = QRectF(
            rect.left() + ROW_PADDING,
            rect.center().y() - INITIALS_BOX_HEIGHT / 2,
            INITIALS_BOX_WIDTH,
            INITIALS_BOX_HEIGHT,
        )
        painter.fillRect(box, theme.color("accent-200" if selected else "neutral-100", alpha))
        initials_font = QFont(option.font)
        initials_font.setPixelSize(INITIALS_TEXT_SIZE)
        initials_font.setWeight(QFont.Weight.Medium)
        painter.setFont(initials_font)
        painter.setPen(theme.color("neutral-800", alpha))
        painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), str(index.data(_INITIALS_ROLE)))
        left = box.right() + ROW_PADDING
        right = rect.right() - ROW_PADDING
        detail = str(index.data(_DETAIL_ROLE) or "")
        if detail:
            detail_font = QFont(option.font)
            detail_font.setPixelSize(DETAIL_TEXT_SIZE)
            painter.setFont(detail_font)
            painter.setPen(theme.color("neutral-800", alpha))
            metrics = painter.fontMetrics()
            elided = metrics.elidedText(
                detail,
                Qt.TextElideMode.ElideRight,
                int((right - left) * DETAIL_SHARE),
            )
            width = metrics.horizontalAdvance(elided)
            painter.drawText(
                QRectF(right - width, rect.top(), width, rect.height()),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                elided,
            )
            right -= width + ROW_PADDING
        name_font = QFont(option.font)
        name_font.setPixelSize(NAME_TEXT_SIZE)
        painter.setFont(name_font)
        painter.setPen(theme.color("text", alpha))
        name = painter.fontMetrics().elidedText(
            str(index.data(Qt.ItemDataRole.DisplayRole)),
            Qt.TextElideMode.ElideRight,
            max(10, int(right - left)),
        )
        painter.drawText(
            QRectF(left, rect.top(), right - left, rect.height()),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            name,
        )


# ----------------------------------------------------------------- dialog ----


class TeamDialog(QDialog):
    """Shared roster of the planning: people and subcontractors, teams, calendar.

    Each edit is applied at once through ``apply`` (no OK button); ``refresh`` reads
    the pages again, e.g. after changes received from another poste.
    """

    def __init__(
        self,
        snapshot_provider: Callable[[], PlanningSnapshot],
        apply: ApplyCommand,
        planner_members: Sequence[PlannerMember] = (),
        parent: QWidget | None = None,
        *,
        read_only_reason: ReadOnlyReason | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Équipe du planning")
        self._snapshot_provider = snapshot_provider
        self._apply = apply
        self._read_only_reason = read_only_reason or (lambda: "")
        snapshot = snapshot_provider()
        self._snapshot = snapshot
        self._shown: PlanningSnapshot | None = None
        self._refreshing = False
        self._refresh_again = False
        self.people_page = _PeoplePage(snapshot, self._run, planner_members)
        self.absences_page = _AbsencesPage(
            snapshot, self._run, self._apply_noted, self._read_only_reason, today
        )
        self.teams_page = _TeamsPage(snapshot, self._run)
        self.types_page = _TypesPage(snapshot, self._run)
        self.calendar_page = _CalendarPage(snapshot, self._run)
        self._build_ui()
        self.refresh()

    @property
    def snapshot(self) -> PlanningSnapshot:
        """The planning as last shown."""
        return self._snapshot

    def refresh(self) -> None:
        """Read the planning again; cheap when only tasks or absences changed."""
        # An edit committed while the pages are rebuilt asks for its own refresh: run it
        # after this one, never inside it, so the pages never end on an older snapshot.
        if self._refreshing:
            self._refresh_again = True
            return
        self._refreshing = True
        try:
            self._refresh_again = True
            while self._refresh_again:
                self._refresh_again = False
                self._show(self._snapshot_provider())
        finally:
            self._refreshing = False

    def _show(self, snapshot: PlanningSnapshot) -> None:
        self._snapshot = snapshot
        shown = self._shown
        if (
            shown is not None
            and snapshot.resources == shown.resources
            and snapshot.meta == shown.meta
            and snapshot.absences == shown.absences
        ):
            # Only tasks changed: the types page counts them.
            if snapshot.tasks is not shown.tasks:
                self._shown = snapshot
                self.types_page.refresh(snapshot)
            return
        self._shown = snapshot
        self.people_page.refresh(snapshot)
        self.absences_page.refresh(snapshot)
        self.teams_page.refresh(snapshot)
        self.types_page.refresh(snapshot)
        self.calendar_page.refresh(snapshot)
        people = [row for row in snapshot.active_resources() if not row.is_subcontractor]
        self._page_meta["people"].setText(str(len(people)))
        self._page_meta["absences"].setText(str(self.absences_page.upcoming_count()))
        self._page_meta["teams"].setText(str(len(snapshot.meta.teams)))
        self._page_meta["types"].setText(str(len(snapshot.meta.task_types)))
        self._page_meta["calendar"].setText(snapshot.meta.default_canton)

    def show_page(self, key: TeamPageKey) -> None:
        keys = list(PAGE_TITLES)
        if key in keys:
            index = keys.index(key)
            self.pages.setCurrentIndex(index)
            page_button = self._page_buttons.button(index)
            if page_button is not None:
                page_button.setChecked(True)

    def select_resource(self, resource_id: str) -> None:
        self.show_page("people")
        self.people_page.select(resource_id)

    def show_absences(self, resource_id: str = "") -> None:
        """Open the « Absences » page, filtered on ``resource_id`` when given."""
        self.show_page("absences")
        self.absences_page.filter_person(resource_id)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        # Enter commits the field being typed and never closes the dialog. With no
        # default button, it reaches here from a focused button, which it presses.
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            focused = self.focusWidget()
            if isinstance(focused, QToolButton) and focused.menu() is not None:
                focused.showMenu()
            elif isinstance(focused, (QPushButton, QToolButton)) and focused.isEnabled():
                focused.animateClick()
            event.accept()
            return
        super().keyPressEvent(event)

    def done(self, result: int) -> None:
        focused = self.focusWidget()
        if isinstance(focused, QLineEdit) and focused.isModified():
            focused.editingFinished.emit()
        super().done(result)

    def _run(self, build: CommandBuilder, text: str, errors: QLabel) -> bool:
        try:
            self._apply(build, text)
        except ProjectFlowError as exc:
            _show_message(errors, str(exc) or "Modification impossible.")
            return False
        _show_message(errors, "")
        self.status_label.setText(f"Enregistré : {text}")
        self.refresh()
        return True

    def _apply_noted(self, build: CommandBuilder, text: str) -> None:
        """``apply`` for the absence dialog: raises when refused, else notes and refreshes."""
        self._apply(build, text)
        self.status_label.setText(f"Enregistré : {text}")
        self.refresh()

    def _build_ui(self) -> None:
        self.setMinimumSize(DIALOG_MIN_WIDTH, DIALOG_MIN_HEIGHT)
        self.resize(DIALOG_WIDTH, DIALOG_HEIGHT)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        body = QHBoxLayout()
        body.setSpacing(0)
        nav = QWidget()
        nav.setFixedWidth(NAV_WIDTH)
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(0, 22, 0, 22)
        nav_layout.setSpacing(0)
        heading = QVBoxLayout()
        heading.setContentsMargins(22, 0, 22, 14)
        heading.setSpacing(2)
        heading.addWidget(label("Planning", "kicker"))
        heading.addWidget(label("Équipe", "h3"))
        nav_layout.addLayout(heading)
        self.pages = QStackedWidget()
        self._page_buttons = QButtonGroup(self)
        self._page_buttons.setExclusive(True)
        self._page_meta: dict[TeamPageKey, QLabel] = {}
        pages: Mapping[TeamPageKey, QWidget] = {
            "people": self.people_page,
            "absences": self.absences_page,
            "teams": _scroll(self.teams_page),
            "types": _scroll(self.types_page),
            "calendar": _scroll(self.calendar_page),
        }
        for index, (key, title) in enumerate(PAGE_TITLES.items()):
            self.pages.addWidget(pages[key])
            nav_button = button("", "side")
            nav_button.setCheckable(True)
            nav_button.setChecked(index == 0)
            nav_button.setMinimumHeight(40)
            row = QHBoxLayout(nav_button)
            row.setContentsMargins(19, 0, 16, 0)
            row.addWidget(QLabel(title))
            row.addStretch(1)
            meta = label("", "small")
            row.addWidget(meta)
            self._page_meta[key] = meta
            self._page_buttons.addButton(nav_button, index)
            nav_layout.addWidget(nav_button)
        self._page_buttons.idClicked.connect(self.pages.setCurrentIndex)
        nav_layout.addStretch(1)
        shared = label(
            "Registre partagé par tous les postes. Chaque modification est enregistrée "
            "tout de suite et peut être annulée (Ctrl+Z) depuis le planning.",
            "small",
            wrap=True,
        )
        shared.setContentsMargins(22, 0, 22, 0)
        nav_layout.addWidget(shared)
        body.addWidget(nav)
        body.addWidget(rule(vertical=True))
        body.addWidget(self.pages, 1)
        root.addLayout(body, 1)
        root.addWidget(rule())
        footer = QHBoxLayout()
        footer.setContentsMargins(26, 8, 20, 8)
        footer.setSpacing(8)
        self.status_label = label("", "small")
        footer.addWidget(self.status_label, 1)
        self.close_button = button("Fermer")
        self.close_button.setAutoDefault(False)
        self.close_button.clicked.connect(self.accept)
        footer.addWidget(self.close_button)
        root.addLayout(footer)
        for child in self.findChildren(QPushButton):
            child.setAutoDefault(False)


# ----------------------------------------------------------------- people ----


class _PeoplePage(QWidget):
    """Roster grouped by team on the left, the selected row's form on the right."""

    def __init__(
        self,
        snapshot: PlanningSnapshot,
        run: _Run,
        planner_members: Sequence[PlannerMember],
    ) -> None:
        super().__init__()
        self._snapshot = snapshot
        self._run = run
        self._members = tuple(planner_members)
        self._mode: Literal["empty", "edit", "new"] = "empty"
        self._selected_id: str | None = None
        self._new_kind: ResourceKind = "person"
        self._new_m365_id = ""
        self._draft_off: list[RecurringOff] = []
        self._initials_touched = False
        self._build_ui()

    # ------------------------------------------------------------ public ----

    def refresh(self, snapshot: PlanningSnapshot) -> None:
        self._snapshot = snapshot
        self._populate_roster()
        self._populate_teams()
        self._populate_members_menu()
        self._sync_off_parts()
        self._load_form()

    def select(self, resource_id: str) -> None:
        resource = self._snapshot.resources.get(resource_id)
        if resource is None or resource.deleted:
            return
        if not resource.active and not self.show_archived_checkbox.isChecked():
            self.show_archived_checkbox.setChecked(True)
        self._commit_typed()
        self._mode = "edit"
        self._selected_id = resource_id
        self._clear_errors()
        self._select_in_list(resource_id, reveal=True)
        self._load_form()

    def start_new(self, kind: ResourceKind = "person", member: PlannerMember | None = None) -> None:
        """Empty form for a new row (prefilled from a Microsoft 365 member)."""
        self._commit_typed()
        self._mode = "new"
        self._new_kind = kind
        self._new_m365_id = member[0] if member is not None else ""
        self._draft_off = []
        self._initials_touched = False
        self._clear_errors()
        self._select_in_list(None)
        name = member[1] if member is not None else ""
        with QSignalBlocker(self.name_edit):
            self.name_edit.setText(name)
        with QSignalBlocker(self.initials_edit):
            self.initials_edit.setText(propose_initials(name, _taken_initials(self._snapshot)))
        self.role_edit.clear()
        default_team = "soustraitants" if kind == "subcontractor" else "atelier"
        self._set_team(default_team if self._snapshot.meta.team(default_team) else "")
        with QSignalBlocker(self.apprentice_checkbox):
            self.apprentice_checkbox.setChecked(False)
        self.canton_choice.set_current("")
        self._load_form()
        self.name_edit.setFocus()

    # ------------------------------------------------------------- build ----

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 26, 34, 20)
        root.setSpacing(14)
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(label("Personnes", "h3"))
        header.addStretch(1)
        self.add_person_button = button("Personne", icon="plus")
        self.add_person_button.setToolTip("Ajouter une personne au planning")
        self.add_person_button.clicked.connect(lambda: self.start_new("person"))
        self.add_subcontractor_button = button("Sous-traitant", icon="plus")
        self.add_subcontractor_button.setToolTip("Ajouter un sous-traitant (une ligne du planning)")
        self.add_subcontractor_button.clicked.connect(lambda: self.start_new("subcontractor"))
        self.members_button = QToolButton()
        self.members_button.setText("Depuis Microsoft 365")
        self.members_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.members_button.setCursor(Qt.CursorShape.PointingHandCursor)
        # The theme reserves room for the arrow of split buttons only, not of menu buttons.
        self.members_button.setStyleSheet(
            f"QToolButton {{ padding-right: {MENU_ARROW_ROOM}px; }}"
            "QToolButton::menu-indicator { subcontrol-origin: padding;"
            f" subcontrol-position: center right; right: {MENU_ARROW_INSET}px; }}",
        )
        self.members_menu = QMenu(self.members_button)
        self.members_button.setMenu(self.members_menu)
        header.addWidget(self.add_person_button)
        header.addWidget(self.add_subcontractor_button)
        header.addWidget(self.members_button)
        root.addLayout(header)
        root.addWidget(
            label(
                "Une ligne du planning par personne ou sous-traitant, sans compte "
                "Microsoft obligatoire. Une personne partie est archivée : ses tâches "
                "passées restent visibles.",
                "muted",
                wrap=True,
            ),
        )
        body = QHBoxLayout()
        body.setSpacing(24)
        roster = QVBoxLayout()
        roster.setSpacing(8)
        frame = BlueprintFrame(padding=(0, 4, 0, 4), spacing=0)
        self.roster_list = QListWidget()
        self.roster_list.setItemDelegate(_RosterDelegate(self.roster_list))
        self.roster_list.setMouseTracking(True)
        self.roster_list.setUniformItemSizes(False)
        self.roster_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.roster_list.currentItemChanged.connect(self._current_changed)
        frame.box.addWidget(self.roster_list)
        frame.setFixedWidth(ROSTER_WIDTH)
        roster.addWidget(frame, 1)
        self.show_archived_checkbox = QCheckBox("Afficher les lignes archivées")
        self.show_archived_checkbox.toggled.connect(lambda _checked: self._populate_roster())
        roster.addWidget(self.show_archived_checkbox)
        body.addLayout(roster)
        self.form_stack = QStackedWidget()
        self.empty_label = label(
            "Sélectionnez une ligne à gauche, ou ajoutez une personne ou un sous-traitant.",
            "muted",
            wrap=True,
        )
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.form_stack.addWidget(self.empty_label)
        self.form_stack.addWidget(_scroll(self._build_form()))
        body.addWidget(self.form_stack, 1)
        root.addLayout(body, 1)

    def _build_form(self) -> QWidget:
        form = QWidget()
        form.setMaximumWidth(FORM_MAX_WIDTH)
        layout = QVBoxLayout(form)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(16)
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        self.form_initials_label = label("", "initials")
        self.form_kicker = label("", "kicker")
        self.form_title = label("", "h4")
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(self.form_kicker)
        titles.addWidget(self.form_title)
        title_row.addWidget(self.form_initials_label, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addLayout(titles)
        title_row.addStretch(1)
        self.kind_tag = label("Sous-traitant", "tag-neutral")
        self.apprentice_tag = label("Apprenti", "tag-accent")
        self.archived_tag = label("Ligne archivée", "tag-outline")
        for tag in (self.kind_tag, self.apprentice_tag, self.archived_tag):
            title_row.addWidget(tag, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(title_row)
        layout.addWidget(rule())

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Prénom Nom")
        self.name_edit.textEdited.connect(self._name_typed)
        self.name_edit.editingFinished.connect(self._commit_name)
        self.name_edit.returnPressed.connect(self._return_pressed)
        self.initials_edit = QLineEdit()
        self.initials_edit.setMaxLength(INITIALS_MAX_LENGTH)
        self.initials_edit.setFixedWidth(INITIALS_EDIT_WIDTH)
        self.initials_edit.setToolTip("Initiales des fiches (C9), uniques dans le planning.")
        self.initials_edit.textEdited.connect(self._initials_typed)
        self.initials_edit.editingFinished.connect(self._commit_initials)
        self.initials_edit.returnPressed.connect(self._return_pressed)
        self.initials_error_label = _error_label()
        initials_field = QWidget()
        initials_column = QVBoxLayout(initials_field)
        initials_column.setContentsMargins(0, 0, 0, 0)
        initials_column.setSpacing(5)
        initials_column.addWidget(label("Initiales", "field"))
        initials_column.addWidget(self.initials_edit)
        names = QGridLayout()
        names.setHorizontalSpacing(16)
        names.setVerticalSpacing(4)
        names.addWidget(field("Nom", self.name_edit), 0, 0)
        names.addWidget(initials_field, 0, 1)
        names.addWidget(self.initials_error_label, 1, 0, 1, 2)
        names.setColumnStretch(0, 1)
        layout.addLayout(names)

        self.team_combo = QComboBox()
        self.team_combo.currentIndexChanged.connect(self._commit_team)
        self.role_edit = QLineEdit()
        self.role_edit.editingFinished.connect(self._commit_role)
        self.role_edit.returnPressed.connect(self._return_pressed)
        self.role_caption = label("Fonction", "field")
        role_column = QVBoxLayout()
        role_column.setSpacing(5)
        role_column.addWidget(self.role_caption)
        role_column.addWidget(self.role_edit)
        details = QHBoxLayout()
        details.setSpacing(16)
        details.addWidget(field("Équipe", self.team_combo), 1)
        details.addLayout(role_column, 1)
        layout.addLayout(details)

        self.canton_choice = SegmentedChoice(
            [
                ("", "Par défaut"),
                *((canton, f"{CANTON_NAMES[canton]} ({canton})") for canton in CANTONS),
            ],
        )
        self.canton_choice.chosen.connect(self._commit_canton)
        layout.addWidget(
            field(
                "Canton des jours fériés",
                self.canton_choice,
                help_text="« Par défaut » suit le canton de l'entreprise (page Calendrier).",
            ),
        )
        self.apprentice_checkbox = QCheckBox("Apprenti")
        self.apprentice_checkbox.toggled.connect(self._commit_apprentice)
        layout.addWidget(self.apprentice_checkbox)
        layout.addWidget(self._build_off_editor())
        self.m365_label = label("", "small", wrap=True)
        layout.addWidget(self.m365_label)
        self.form_error_label = _error_label()
        layout.addWidget(self.form_error_label)
        layout.addWidget(rule())

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.up_button = button("Monter", icon="chevron-up")
        self.up_button.setToolTip("Monter d'une ligne dans l'équipe (ordre partagé)")
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button = button("Descendre", icon="chevron-down")
        self.down_button.setToolTip("Descendre d'une ligne dans l'équipe (ordre partagé)")
        self.down_button.clicked.connect(lambda: self._move(1))
        self.archive_button = button("Archiver", "danger", icon="trash")
        self.archive_button.setToolTip(
            "Retire la ligne du planning ; ses tâches et absences sont conservées.",
        )
        self.archive_button.clicked.connect(self._archive)
        self.reactivate_button = button("Réactiver", icon="refresh")
        self.reactivate_button.clicked.connect(self._reactivate)
        self.cancel_new_button = button("Annuler")
        self.cancel_new_button.clicked.connect(self._cancel_new)
        self.create_button = button("Ajouter", "primary", icon="plus")
        self.create_button.clicked.connect(self._create)
        for widget in (self.up_button, self.down_button):
            actions.addWidget(widget)
        actions.addStretch(1)
        for widget in (
            self.reactivate_button,
            self.archive_button,
            self.cancel_new_button,
            self.create_button,
        ):
            actions.addWidget(widget)
        layout.addLayout(actions)
        layout.addStretch(1)
        return form

    def _build_off_editor(self) -> QWidget:
        self.off_rows = QVBoxLayout()
        self.off_rows.setSpacing(4)
        self.off_empty_label = label("Aucun", "small")
        self.off_weekday_combo = QComboBox()
        for weekday in range(WORKDAYS):
            self.off_weekday_combo.addItem(_weekday_name(weekday).capitalize(), weekday)
        self.off_weekday_combo.currentIndexChanged.connect(self._sync_off_parts)
        self.off_part_combo = QComboBox()
        for part in _DAY_PARTS:
            self.off_part_combo.addItem(DAY_PART_LABELS[part], part)
        self.off_reason_combo = QComboBox()
        self.off_reason_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon,
        )
        self.off_reason_combo.setMinimumContentsLength(COMBO_MIN_CHARACTERS)
        for reason in _REASONS:
            self.off_reason_combo.addItem(ABSENCE_LABELS[reason], reason)
        self.off_add_button = button("Ajouter", icon="plus")
        self.off_add_button.clicked.connect(self._add_off)
        self.off_error_label = _error_label()
        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        add_row.addWidget(self.off_weekday_combo)
        add_row.addWidget(self.off_part_combo)
        add_row.addWidget(self.off_reason_combo, 1)
        add_row.addWidget(self.off_add_button)
        column = QVBoxLayout()
        column.setSpacing(8)
        column.addLayout(self.off_rows)
        column.addWidget(self.off_empty_label)
        column.addLayout(add_row)
        column.addWidget(self.off_error_label)
        return field(
            "Jours non disponibles chaque semaine",
            column,
            help_text="Hachurés sur la ligne chaque semaine, par exemple l'école "
            "professionnelle d'un apprenti le mercredi.",
        )

    # ---------------------------------------------------------- populate ----

    def _populate_roster(self) -> None:
        snapshot = self._snapshot
        groups: dict[str, list[Resource]] = {}
        for row in snapshot.active_resources():
            groups.setdefault(row.team, []).append(row)
        active_counts = {key: len(rows) for key, rows in groups.items()}
        if self.show_archived_checkbox.isChecked():
            archived = sorted(
                (row for row in snapshot.resources.values() if not row.active and not row.deleted),
                key=lambda row: row.name.casefold(),
            )
            for row in archived:
                groups.setdefault(row.team, []).append(row)
        keys = [team.key for team in _sorted_teams(snapshot.meta.teams)]
        keys += sorted(key for key in groups if key not in keys)
        # The list keeps its scroll position: a change received from another poste must
        # not bring the selected row back into view while the user scrolls the list.
        bar = self.roster_list.verticalScrollBar()
        position = bar.value()
        with QSignalBlocker(self.roster_list):
            self.roster_list.clear()
            for key in keys:
                header = QListWidgetItem(_team_name(snapshot, key))
                header.setData(_KIND_ROLE, "team")
                header.setData(_COUNT_ROLE, str(active_counts.get(key, 0)))
                header.setFlags(Qt.ItemFlag.NoItemFlags)
                self.roster_list.addItem(header)
                for row in groups.get(key, []):
                    self.roster_list.addItem(self._roster_item(row))
            self._select_in_list(self._selected_id if self._mode == "edit" else None)
        self.roster_list.doItemsLayout()
        bar.setValue(position)

    def _roster_item(self, row: Resource) -> QListWidgetItem:
        item = QListWidgetItem(row.name)
        item.setData(_ID_ROLE, row.id)
        item.setData(_KIND_ROLE, "resource")
        item.setData(_INITIALS_ROLE, row.initials or "—")
        item.setData(_ARCHIVED_ROLE, not row.active)
        details = [row.role or ("Sous-traitant" if row.is_subcontractor else "")]
        if row.apprentice and "apprenti" not in row.role.casefold():
            details.append("Apprenti")
        if row.canton:
            details.append(row.canton)
        if not row.active:
            details.append("archivée")
        item.setData(_DETAIL_ROLE, " · ".join(part for part in details if part))
        item.setToolTip(row.name)
        return item

    def _select_in_list(self, resource_id: str | None, *, reveal: bool = False) -> None:
        with QSignalBlocker(self.roster_list):
            for index in range(self.roster_list.count()):
                item = self.roster_list.item(index)
                if item is not None and resource_id and item.data(_ID_ROLE) == resource_id:
                    self.roster_list.setCurrentItem(item)
                    if reveal:
                        self.roster_list.scrollToItem(item)
                    return
            self.roster_list.setCurrentRow(-1)
            self.roster_list.clearSelection()

    def _populate_teams(self) -> None:
        current = self.team_combo.currentData()
        teams = _sorted_teams(self._snapshot.meta.teams)
        with QSignalBlocker(self.team_combo):
            self.team_combo.clear()
            for team in teams:
                self.team_combo.addItem(team.name, team.key)
            index = self.team_combo.findData(current)
            self.team_combo.setCurrentIndex(max(index, 0) if teams else -1)

    def _populate_members_menu(self) -> None:
        self.members_menu.clear()
        linked = {
            row.m365_user_id
            for row in self._snapshot.resources.values()
            if row.m365_user_id and not row.deleted
        }
        for member in self._members:
            member_id, name, email = member
            text = f"{name} ({email})" if email else name
            action = QAction(text, self.members_menu)
            if member_id in linked:
                action.setText(f"{text} · déjà dans le planning")
                action.setEnabled(False)
            else:
                action.triggered.connect(
                    lambda _checked=False, chosen=member: self.start_new("person", chosen),
                )
            self.members_menu.addAction(action)
        available = bool(self._members)
        self.members_button.setEnabled(available)
        self.members_button.setToolTip(
            "Préremplit une personne depuis les membres du groupe Microsoft 365."
            if available
            else "Membres Microsoft 365 non chargés (connexion Microsoft et plan Planner requis).",
        )

    def _set_team(self, key: str) -> None:
        with QSignalBlocker(self.team_combo):
            index = self.team_combo.findData(key)
            if index >= 0:
                self.team_combo.setCurrentIndex(index)

    def _selected(self) -> Resource | None:
        if self._mode != "edit" or self._selected_id is None:
            return None
        resource = self._snapshot.resources.get(self._selected_id)
        if resource is None or resource.deleted:
            return None
        return resource

    def _load_form(self) -> None:
        resource = self._selected()
        if self._mode == "edit" and resource is None:
            self._mode = "empty"
            self._selected_id = None
        default_canton = self._snapshot.meta.default_canton
        self.canton_choice.set_text("", f"Par défaut ({default_canton})")
        if self._mode == "empty":
            self.form_stack.setCurrentIndex(0)
            return
        self.form_stack.setCurrentIndex(1)
        creating = self._mode == "new"
        kind = self._new_kind if resource is None else resource.kind_of_resource
        subcontractor = kind == "subcontractor"
        self.role_caption.setText("Activité" if subcontractor else "Fonction")
        self.role_edit.setPlaceholderText(
            "Galvanisation, thermolaquage, transport…"
            if subcontractor
            else "Serrurier, Apprenti 2e…",
        )
        self.apprentice_checkbox.setVisible(not subcontractor)
        if resource is not None:
            _set_text(self.name_edit, resource.name)
            _set_text(self.initials_edit, resource.initials)
            _set_text(self.role_edit, resource.role)
            self._set_team(resource.team)
            self.canton_choice.set_current(resource.canton)
            with QSignalBlocker(self.apprentice_checkbox):
                self.apprentice_checkbox.setChecked(resource.apprentice)
        name = self.name_edit.text().strip()
        if creating:
            self.form_kicker.setText(
                ("Nouveau sous-traitant" if subcontractor else "Nouvelle personne").upper(),
            )
            self.form_title.setText(name or self._new_title())
        else:
            self.form_kicker.setText(
                _team_name(self._snapshot, resource.team).upper() if resource else ""
            )
            self.form_title.setText(resource.name if resource else "")
        initials = self.initials_edit.text().strip().upper()
        self.form_initials_label.setText(initials or "—")
        self.kind_tag.setVisible(subcontractor)
        self.apprentice_tag.setVisible(not subcontractor and self.apprentice_checkbox.isChecked())
        archived = resource is not None and not resource.active
        self.archived_tag.setVisible(archived)
        self._load_off_rows(self._draft_off if resource is None else list(resource.recurring_off))
        self._load_m365(resource)
        neighbours = self._team_neighbours(resource)
        editing = resource is not None and resource.active
        self.up_button.setVisible(editing)
        self.down_button.setVisible(editing)
        self.up_button.setEnabled(neighbours[0] is not None)
        self.down_button.setEnabled(neighbours[1] is not None)
        self.archive_button.setVisible(editing)
        self.reactivate_button.setVisible(archived)
        self.cancel_new_button.setVisible(creating)
        self.create_button.setVisible(creating)

    def _load_off_rows(self, entries: Sequence[RecurringOff]) -> None:
        _clear_layout(self.off_rows)
        for entry in entries:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(label(recurring_off_text(entry)), 1)
            remove = _icon_button("x", "Retirer ce jour")
            remove.setAutoDefault(False)
            remove.clicked.connect(lambda _checked=False, item=entry: self._remove_off(item))
            row.addWidget(remove)
            self.off_rows.addLayout(row)
        self.off_empty_label.setVisible(not entries)

    def _sync_off_parts(self) -> None:
        """Offer « Après-midi » only on a weekday worked in the afternoon (not Friday)."""
        weekday = int(self.off_weekday_combo.currentData())
        allowed = _has_afternoon(self._snapshot.meta.work_week, weekday)
        model = self.off_part_combo.model()
        index = self.off_part_combo.findData("afternoon")
        item = model.item(index) if isinstance(model, QStandardItemModel) else None
        if item is not None:
            item.setEnabled(allowed)
        if not allowed and index == self.off_part_combo.currentIndex():
            self.off_part_combo.setCurrentIndex(self.off_part_combo.findData("day"))

    def _load_m365(self, resource: Resource | None) -> None:
        member_id = resource.m365_user_id if resource is not None else self._new_m365_id
        if not member_id:
            self.m365_label.hide()
            return
        member = next((item for item in self._members if item[0] == member_id), None)
        if member is None:
            self.m365_label.setText("Lié à un compte Microsoft 365.")
        else:
            email = f" ({member[2]})" if member[2] else ""
            self.m365_label.setText(f"Lié au compte Microsoft 365 de {member[1]}{email}.")
        self.m365_label.show()

    def _team_neighbours(self, resource: Resource | None) -> tuple[str | None, str | None]:
        if resource is None or not resource.active:
            return None, None
        ids = [row.id for row in self._snapshot.active_resources() if row.team == resource.team]
        if resource.id not in ids:
            return None, None
        position = ids.index(resource.id)
        above = ids[position - 1] if position > 0 else None
        below = ids[position + 1] if position + 1 < len(ids) else None
        return above, below

    def _clear_errors(self) -> None:
        for widget in (self.initials_error_label, self.form_error_label, self.off_error_label):
            _show_message(widget, "")

    def _commit_typed(self) -> None:
        # Text typed for the row shown belongs to it, before the form shows another one.
        for edit in (self.name_edit, self.initials_edit, self.role_edit):
            if edit.isModified():
                edit.editingFinished.emit()

    # ------------------------------------------------------------ events ----

    def _current_changed(self, current: QListWidgetItem | None, _previous: object) -> None:
        if current is None or current.data(_KIND_ROLE) != "resource":
            return
        self._mode = "edit"
        self._selected_id = str(current.data(_ID_ROLE))
        self._clear_errors()
        self._load_form()

    def _name_typed(self, text: str) -> None:
        if self._mode != "new":
            return
        if not self._initials_touched:
            with QSignalBlocker(self.initials_edit):
                self.initials_edit.setText(propose_initials(text, _taken_initials(self._snapshot)))
        self.form_title.setText(text.strip() or self._new_title())
        self.form_initials_label.setText(self.initials_edit.text() or "—")

    def _new_title(self) -> str:
        return "Sous-traitant" if self._new_kind == "subcontractor" else "Personne"

    def _initials_typed(self, text: str) -> None:
        self._initials_touched = True
        upper = text.upper()
        if upper != text:
            cursor = self.initials_edit.cursorPosition()
            with QSignalBlocker(self.initials_edit):
                self.initials_edit.setText(upper)
            self.initials_edit.setCursorPosition(cursor)
            self.initials_edit.setModified(True)
        self.form_initials_label.setText(upper.strip() or "—")
        _show_message(self.initials_error_label, "")

    def _return_pressed(self) -> None:
        # Enter in a field of a new row adds it, as the « Ajouter » button.
        if self._mode == "new":
            self._create()

    def _apply_or_reload(self, build: CommandBuilder, text: str) -> bool:
        """Apply an edit made with a choice control, shown back as stored when refused."""
        if self._run(build, text, self.form_error_label):
            return True
        self._load_form()
        return False

    def _commit_name(self) -> None:
        self.name_edit.setModified(False)
        resource = self._selected()
        if resource is None:
            return
        name = " ".join(self.name_edit.text().split())
        if name == resource.name:
            return
        if not name:
            _show_message(self.form_error_label, "Le nom est obligatoire.")
            return
        rid = resource.id
        self._run(
            lambda ctx: update_resource(ctx, rid, name=name),
            f"Renommer {resource.name} en {name}",
            self.form_error_label,
        )

    def _commit_initials(self) -> None:
        self.initials_edit.setModified(False)
        resource = self._selected()
        initials = self.initials_edit.text().strip().upper()
        if resource is None:
            if self._mode == "new":
                self._check_initials(initials, exclude=None)
            return
        if initials == resource.initials:
            _show_message(self.initials_error_label, "")
            return
        if not self._check_initials(initials, exclude=resource.id):
            return
        rid = resource.id
        self._run(
            lambda ctx: update_resource(ctx, rid, initials=initials),
            f"Initiales de {resource.name} : {initials}",
            self.initials_error_label,
        )

    def _check_initials(self, initials: str, *, exclude: str | None) -> bool:
        if not initials:
            _show_message(self.initials_error_label, "Les initiales sont obligatoires.")
            return False
        owner = _initials_owner(self._snapshot, initials, exclude=exclude)
        if owner is not None:
            _show_message(
                self.initials_error_label,
                f"« {initials} » est déjà utilisé par {owner.name}. Les initiales "
                "doivent être uniques pour la saisie rapide.",
            )
            return False
        _show_message(self.initials_error_label, "")
        return True

    def _commit_team(self) -> None:
        resource = self._selected()
        key = self.team_combo.currentData()
        if resource is None or not isinstance(key, str) or key == resource.team:
            return
        rid = resource.id
        if self._apply_or_reload(
            lambda ctx: update_resource(ctx, rid, team=key),
            f"Équipe de {resource.name} : {_team_name(self._snapshot, key)}",
        ):
            # The row moved to another group of the roster: keep it in view.
            self._select_in_list(rid, reveal=True)

    def _commit_role(self) -> None:
        self.role_edit.setModified(False)
        resource = self._selected()
        role = " ".join(self.role_edit.text().split())
        if resource is None or role == resource.role:
            return
        rid = resource.id
        self._run(
            lambda ctx: update_resource(ctx, rid, role=role),
            f"Fonction de {resource.name} : {role}"
            if role
            else f"Effacer la fonction de {resource.name}",
            self.form_error_label,
        )

    def _commit_canton(self, canton: str) -> None:
        resource = self._selected()
        if resource is None or canton == resource.canton:
            return
        rid = resource.id
        shown = canton or f"par défaut ({self._snapshot.meta.default_canton})"
        self._apply_or_reload(
            lambda ctx: update_resource(ctx, rid, canton=canton),
            f"Canton de {resource.name} : {shown}",
        )

    def _commit_apprentice(self, checked: bool) -> None:  # noqa: FBT001
        self.apprentice_tag.setVisible(checked)
        resource = self._selected()
        if resource is None or checked == resource.apprentice:
            return
        rid = resource.id
        self._apply_or_reload(
            lambda ctx: update_resource(ctx, rid, apprentice=checked),
            f"Marquer {resource.name} comme apprenti"
            if checked
            else f"{resource.name} n'est plus apprenti",
        )

    def _add_off(self) -> None:
        weekday = int(self.off_weekday_combo.currentData())
        part: DayPart = next(
            (item for item in _DAY_PARTS if item == self.off_part_combo.currentData()), "day"
        )
        reason: AbsenceReason = next(
            (item for item in _REASONS if item == self.off_reason_combo.currentData()),
            "ecole",
        )
        entry = RecurringOff(weekday=weekday, part=part, reason=reason)
        if part == "afternoon" and not _has_afternoon(self._snapshot.meta.work_week, weekday):
            _show_message(
                self.off_error_label,
                f"Le {_weekday_name(weekday)} n'est pas travaillé l'après-midi.",
            )
            return
        resource = self._selected()
        entries = self._draft_off if resource is None else list(resource.recurring_off)
        if any(_overlaps(entry, other) for other in entries):
            _show_message(self.off_error_label, _off_clash_text(weekday))
            return
        _show_message(self.off_error_label, "")
        if resource is None:
            self._draft_off = list(_sorted_off([*entries, entry]))
            self._load_off_rows(self._draft_off)
            return
        rid = resource.id

        def build(ctx: CommandContext) -> tuple[DocumentChange, ...]:
            # The days stored now, which another poste may have changed since shown.
            current = ctx.snapshot.resources.get(rid)
            if current is None:
                return ()
            if any(_overlaps(entry, other) for other in current.recurring_off):
                raise PlanningCommandError(
                    f"Le {_weekday_name(weekday)} est deja indique: retirez-le d'abord."
                )
            updated = _sorted_off((*current.recurring_off, entry))
            return update_resource(ctx, rid, recurring_off=updated)

        self._run(
            build,
            f"{ABSENCE_LABELS[reason]} de {resource.name} le {_weekday_name(weekday)}"
            f"{_PART_SUFFIXES[part]}",
            self.off_error_label,
        )

    def _remove_off(self, entry: RecurringOff) -> None:
        resource = self._selected()
        if resource is None:
            self._draft_off = [item for item in self._draft_off if item != entry]
            self._load_off_rows(self._draft_off)
            return
        rid = resource.id

        def build(ctx: CommandContext) -> tuple[DocumentChange, ...]:
            current = ctx.snapshot.resources.get(rid)
            if current is None or entry not in current.recurring_off:
                return ()
            remaining = tuple(item for item in current.recurring_off if item != entry)
            return update_resource(ctx, rid, recurring_off=remaining)

        self._run(
            build,
            f"Retirer le {_weekday_name(entry.weekday)}{_PART_SUFFIXES[entry.part]} "
            f"de {resource.name}",
            self.off_error_label,
        )

    def _move(self, step: int) -> None:
        resource = self._selected()
        if resource is None:
            return
        rid = resource.id

        def build(ctx: CommandContext) -> tuple[DocumentChange, ...]:
            current = ctx.snapshot.resources.get(rid)
            if current is None:
                return ()
            ids = [row.id for row in ctx.snapshot.active_resources() if row.team == current.team]
            if rid not in ids:
                return ()
            target = ids.index(rid) + step
            if not 0 <= target < len(ids):
                return ()
            if step < 0:
                return reorder_resource(ctx, rid, below_id=ids[target])
            return reorder_resource(ctx, rid, above_id=ids[target])

        verb = "Monter" if step < 0 else "Descendre"
        self._run(build, f"{verb} {resource.name}", self.form_error_label)

    def _archive(self) -> None:
        resource = self._selected()
        if resource is None:
            return
        rid = resource.id
        self._run(
            lambda ctx: archive_resource(ctx, rid),
            f"Archiver {resource.name}",
            self.form_error_label,
        )

    def _reactivate(self) -> None:
        resource = self._selected()
        if resource is None:
            return
        owner = _initials_owner(self._snapshot, resource.initials, exclude=resource.id)
        if owner is not None:
            _show_message(
                self.form_error_label,
                f"« {resource.initials} » est utilisé par {owner.name} : changez d'abord "
                "les initiales de l'une des deux lignes.",
            )
            return
        rid = resource.id
        self._run(
            lambda ctx: update_resource(ctx, rid, active=True),
            f"Réactiver {resource.name}",
            self.form_error_label,
        )

    def _cancel_new(self) -> None:
        self._mode = "empty"
        self._clear_errors()
        self._load_form()

    def _create(self) -> None:
        name = " ".join(self.name_edit.text().split())
        if not name:
            _show_message(self.form_error_label, "Le nom est obligatoire.")
            self.name_edit.setFocus()
            return
        initials = self.initials_edit.text().strip().upper() or propose_initials(
            name,
            _taken_initials(self._snapshot),
        )
        if not self._check_initials(initials, exclude=None):
            self.initials_edit.setFocus()
            return
        team_data = self.team_combo.currentData()
        team = team_data if isinstance(team_data, str) and team_data else None
        kind = self._new_kind
        role = " ".join(self.role_edit.text().split())
        apprentice = kind == "person" and self.apprentice_checkbox.isChecked()
        canton = self.canton_choice.current()
        m365_user_id = self._new_m365_id
        recurring_off = tuple(self._draft_off)
        created: list[str] = []

        def build(ctx: CommandContext) -> tuple[DocumentChange, ...]:
            changes = create_resource(
                ctx,
                name=name,
                initials=initials,
                kind_of_resource=kind,
                team=team,
                role=role,
                apprentice=apprentice,
                canton=canton,
                m365_user_id=m365_user_id,
                recurring_off=recurring_off,
            )
            created.extend(change.doc_id for change in changes)
            return changes

        if not self._run(build, f"Ajouter {name}", self.form_error_label):
            return
        self._draft_off = []
        self._new_m365_id = ""
        if created:
            self.select(created[0])


# ------------------------------------------------------------------ teams ----


class _TeamRow:
    """Widgets of one team of the Équipes page."""

    def __init__(self, team: Team) -> None:
        self.key = team.key
        self.name_edit = QLineEdit(team.name)
        self.name_edit.setAccessibleName(f"Nom de l'équipe {team.name}")
        self.alias_edit = QLineEdit(team.alias)
        self.alias_edit.setPlaceholderText("alias")
        self.alias_edit.setFixedWidth(ALIAS_EDIT_WIDTH)
        self.alias_edit.setAccessibleName(f"Alias de l'équipe {team.name}")
        self.count_label = label("", "small")
        self.up_button = _icon_button("chevron-up", "Monter l'équipe")
        self.down_button = _icon_button("chevron-down", "Descendre l'équipe")
        self.delete_button = _icon_button("trash", "Supprimer l'équipe (vide seulement)")
        for widget in (self.up_button, self.down_button, self.delete_button):
            widget.setAutoDefault(False)

    def widgets(self) -> tuple[QWidget, ...]:
        return (
            self.name_edit,
            self.alias_edit,
            self.count_label,
            self.up_button,
            self.down_button,
            self.delete_button,
        )


class _TeamsPage(QWidget):
    def __init__(self, snapshot: PlanningSnapshot, run: _Run) -> None:
        super().__init__()
        self._snapshot = snapshot
        self._run = run
        self.rows: list[_TeamRow] = []
        self._build_ui()

    def refresh(self, snapshot: PlanningSnapshot) -> None:
        self._snapshot = snapshot
        teams = _sorted_teams(snapshot.meta.teams)
        if [team.key for team in teams] != [row.key for row in self.rows]:
            self._rebuild(teams)
        counts: dict[str, int] = {}
        used: set[str] = set()
        for resource in snapshot.resources.values():
            if resource.deleted:
                continue
            used.add(resource.team)
            if resource.active:
                counts[resource.team] = counts.get(resource.team, 0) + 1
        for position, (row, team) in enumerate(zip(self.rows, teams, strict=True)):
            _set_text(row.name_edit, team.name)
            _set_text(row.alias_edit, team.alias)
            count = counts.get(team.key, 0)
            row.count_label.setText(f"{count} ligne{'s' if count > 1 else ''}")
            row.up_button.setEnabled(position > 0)
            row.down_button.setEnabled(position + 1 < len(teams))
            row.delete_button.setEnabled(team.key not in used)

    def _build_ui(self) -> None:
        content = QVBoxLayout(self)
        content.setContentsMargins(34, 26, 34, 26)
        content.setSpacing(18)
        content.addWidget(label("Équipes", "h3"))
        content.addWidget(
            label(
                "Les groupes de lignes du planning, dans cet ordre. L'alias sert à la saisie "
                "rapide : « +montage » propose les membres de l'équipe.",
                "muted",
                wrap=True,
            ),
        )
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(8)
        self.grid.setColumnStretch(0, 1)
        content.addLayout(self.grid)
        self.error_label = _error_label()
        content.addWidget(self.error_label)
        content.addWidget(rule())
        self.new_team_edit = QLineEdit()
        self.new_team_edit.setPlaceholderText("Nom de la nouvelle équipe")
        self.new_team_edit.returnPressed.connect(self._add)
        self.add_button = button("Ajouter l'équipe", icon="plus")
        self.add_button.clicked.connect(self._add)
        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        add_row.addWidget(self.new_team_edit, 1)
        add_row.addWidget(self.add_button)
        content.addWidget(field("Nouvelle équipe", add_row))
        content.addStretch(1)
        self.setMaximumWidth(PAGE_MAX_WIDTH)

    def _rebuild(self, teams: Sequence[Team]) -> None:
        # Text being typed moves to the new row of its team. The old fields are silenced
        # first: losing the focus as they go would commit the text in mid-refresh.
        typed: dict[tuple[str, int], tuple[str, int]] = {}
        for row in self.rows:
            for position, edit in enumerate((row.name_edit, row.alias_edit)):
                if edit.hasFocus() and edit.isModified():
                    typed[row.key, position] = (edit.text(), edit.cursorPosition())
                edit.blockSignals(True)  # noqa: FBT003
        _clear_layout(self.grid)
        for column, caption in enumerate(("Nom", "Alias de saisie", "Lignes")):
            self.grid.addWidget(label(caption, "field"), 0, column)
        self.rows = []
        for position, team in enumerate(teams, start=1):
            row = _TeamRow(team)
            key = team.key
            row.name_edit.editingFinished.connect(lambda team_key=key: self._rename(team_key))
            row.alias_edit.editingFinished.connect(lambda team_key=key: self._set_alias(team_key))
            row.up_button.clicked.connect(
                lambda _checked=False, team_key=key: self._move(team_key, -1)
            )
            row.down_button.clicked.connect(
                lambda _checked=False, team_key=key: self._move(team_key, 1)
            )
            row.delete_button.clicked.connect(
                lambda _checked=False, team_key=key: self._delete(team_key)
            )
            for column, widget in enumerate(row.widgets()):
                self.grid.addWidget(widget, position, column)
            self.rows.append(row)
            for field_position, edit in enumerate((row.name_edit, row.alias_edit)):
                pending = typed.get((key, field_position))
                if pending is None:
                    continue
                text, cursor = pending
                edit.setText(text)
                edit.setModified(True)
                edit.setCursorPosition(cursor)
                # Shown now, not at the next event loop pass, so that it takes the focus.
                edit.show()
                edit.setFocus()

    def _row(self, key: str) -> _TeamRow | None:
        return next((row for row in self.rows if row.key == key), None)

    def _rename(self, key: str) -> None:
        row = self._row(key)
        team = self._snapshot.meta.team(key)
        if row is None or team is None:
            return
        row.name_edit.setModified(False)
        name = " ".join(row.name_edit.text().split())
        if name == team.name:
            return
        if not name:
            _show_message(self.error_label, "Le nom de l'équipe est obligatoire.")
            return
        if any(
            other.key != key and fold_text(other.name) == fold_text(name)
            for other in self._snapshot.meta.teams
        ):
            _show_message(self.error_label, f"Une équipe s'appelle déjà « {name} ».")
            return
        self._run(
            _team_update(key, name=name),
            f"Renommer l'équipe {team.name} en {name}",
            self.error_label,
        )

    def _set_alias(self, key: str) -> None:
        row = self._row(key)
        team = self._snapshot.meta.team(key)
        if row is None or team is None:
            return
        row.alias_edit.setModified(False)
        alias = fold_text(row.alias_edit.text().strip().lstrip("+@").strip())
        if alias == team.alias:
            _set_text(row.alias_edit, alias)
            return
        if not _ALIAS_RE.match(alias):
            _show_message(
                self.error_label,
                "L'alias ne contient que des lettres et des chiffres, sans espace.",
            )
            return
        clash = next(
            (
                other
                for other in self._snapshot.meta.teams
                if other.key != key and alias and alias in {other.key, fold_text(other.alias)}
            ),
            None,
        )
        if clash is not None:
            _show_message(self.error_label, f"L'alias « {alias} » est déjà pris par {clash.name}.")
            return
        text = (
            f"Alias de l'équipe {team.name} : +{alias}"
            if alias
            else f"Effacer l'alias de {team.name}"
        )
        self._run(_team_update(key, alias=alias), text, self.error_label)

    def _move(self, key: str, step: int) -> None:
        team = self._snapshot.meta.team(key)
        if team is None:
            return

        def build(ctx: CommandContext) -> tuple[DocumentChange, ...]:
            teams = _sorted_teams(ctx.snapshot.meta.teams)
            keys = [item.key for item in teams]
            if key not in keys:
                return ()
            position = keys.index(key)
            target = position + step
            if not 0 <= target < len(teams):
                return ()
            teams[position], teams[target] = teams[target], teams[position]
            renumbered = tuple(
                item.model_copy(update={"order": order}) for order, item in enumerate(teams)
            )
            return update_meta(ctx, teams=renumbered)

        verb = "Monter" if step < 0 else "Descendre"
        self._run(build, f"{verb} l'équipe {team.name}", self.error_label)

    def _delete(self, key: str) -> None:
        team = self._snapshot.meta.team(key)
        if team is None:
            return
        user = next(
            (
                row
                for row in self._snapshot.resources.values()
                if row.team == key and not row.deleted
            ),
            None,
        )
        if user is not None:
            _show_message(self.error_label, f"L'équipe {team.name} contient encore {user.name}.")
            return

        def build(ctx: CommandContext) -> tuple[DocumentChange, ...]:
            remaining = tuple(item for item in ctx.snapshot.meta.teams if item.key != key)
            return update_meta(ctx, teams=remaining)

        self._run(build, f"Supprimer l'équipe {team.name}", self.error_label)

    def _add(self) -> None:
        name = " ".join(self.new_team_edit.text().split())
        if not name:
            _show_message(self.error_label, "Indiquez le nom de la nouvelle équipe.")
            return
        teams = self._snapshot.meta.teams
        if any(fold_text(team.name) == fold_text(name) for team in teams):
            _show_message(self.error_label, f"Une équipe s'appelle déjà « {name} ».")
            return
        base = _slug(name) or "equipe"
        taken = {team.key for team in teams} | {fold_text(team.alias) for team in teams}
        key = base
        number = 2
        while key in taken:
            key = f"{base}{number}"
            number += 1

        def build(ctx: CommandContext) -> tuple[DocumentChange, ...]:
            current = ctx.snapshot.meta.teams
            order = max((team.order for team in current), default=-1) + 1
            added = Team(key=key, name=name, alias=key, order=order)
            return update_meta(ctx, teams=(*current, added))

        if self._run(build, f"Ajouter l'équipe {name}", self.error_label):
            self.new_team_edit.clear()


def _team_update(key: str, **fields: object) -> CommandBuilder:
    def build(ctx: CommandContext) -> tuple[DocumentChange, ...]:
        teams = tuple(
            team.model_copy(update=fields) if team.key == key else team
            for team in ctx.snapshot.meta.teams
        )
        return update_meta(ctx, teams=teams)

    return build


# ------------------------------------------------------------- task types ----


def task_type_counts(snapshot: PlanningSnapshot) -> dict[str, int]:
    """Live tasks of each configured type; a removed type counts as the one shown."""
    counts: dict[str, int] = {}
    meta = snapshot.meta
    for task in snapshot.live_tasks():
        key = meta.task_type(task.task_type).key
        counts[key] = counts.get(key, 0) + 1
    return counts


def _tasks_text(count: int) -> str:
    if not count:
        return "aucune tâche"
    return f"{count} tâche{'s' if count > 1 else ''}"


def _swatch_icon(color: str) -> QIcon:
    ratio = 2
    pixmap = QPixmap(SWATCH_SIZE * ratio, SWATCH_SIZE * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(QColor(color))
    painter = QPainter(pixmap)
    painter.setPen(QPen(QColor(color).darker(130)))
    painter.drawRect(QRectF(0, 0, SWATCH_SIZE - 0.5, SWATCH_SIZE - 0.5))
    painter.end()
    return QIcon(pixmap)


def _type_color(kind: TaskType) -> str:
    return kind.color_dark if current_theme().is_dark else kind.color


class TypeChangeDialog(QDialog):
    """Small confirmation of the « Types de tâche » page, with an optional type choice."""

    def __init__(
        self,
        title: str,
        message: str,
        action: str,
        *,
        choices: Sequence[TaskType] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(TYPE_DIALOG_WIDTH)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(16)
        heading = QVBoxLayout()
        heading.setSpacing(2)
        heading.addWidget(label("Types de tâche", "kicker"))
        heading.addWidget(label(title, "h3"))
        root.addLayout(heading)
        root.addWidget(label(message, wrap=True))
        self.type_combo = QComboBox()
        for kind in choices:
            self.type_combo.addItem(_swatch_icon(_type_color(kind)), kind.label, kind.key)
        if choices:
            root.addWidget(field("Remplacer par", self.type_combo))
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.cancel_button = button("Annuler")
        self.cancel_button.clicked.connect(self.reject)
        self.ok_button = button(action, "primary")
        self.ok_button.clicked.connect(self.accept)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.ok_button)
        root.addLayout(buttons)
        for child in (self.cancel_button, self.ok_button):
            child.setAutoDefault(False)

    def choice(self) -> str:
        data = self.type_combo.currentData()
        return data if isinstance(data, str) else ""


ReplacementAsker = Callable[[TaskType, int, Sequence[TaskType]], str | None]
"""Type taking the tasks of a removed type (type, task count, remaining), None to cancel."""
PresetConfirmer = Callable[[int], bool]
"""Whether to simplify the types, given how many tasks change of type."""
ColorPicker = Callable[[str], str | None]


class _TypeRow:
    """Widgets of one type of the « Types de tâche » page."""

    def __init__(self, kind: TaskType) -> None:
        self.key = kind.key
        self.color_button = QToolButton()
        self.color_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.color_button.setToolTip("Couleur des barres")
        self.color_button.setAccessibleName(f"Couleur du type {kind.label}")
        self.color_button.setIconSize(QSize(SWATCH_SIZE, SWATCH_SIZE))
        self.color_button.setCursor(Qt.CursorShape.PointingHandCursor)
        # The swatch says it opens a palette; the menu arrow would sit over it.
        self.color_button.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0; }")
        self.color_menu = QMenu(self.color_button)
        self.color_button.setMenu(self.color_menu)
        self.name_edit = QLineEdit(kind.label)
        self.name_edit.setAccessibleName(f"Nom du type {kind.label}")
        self.name_edit.setMinimumWidth(TYPE_NAME_MIN_WIDTH)
        self.command_label = label(f"/{kind.key}", "small")
        self.command_label.setToolTip("Commande de saisie rapide")
        self.keywords_edit = QLineEdit(keywords_text(kind.keywords))
        self.keywords_edit.setPlaceholderText("mots-clés, séparés par des virgules")
        self.keywords_edit.setAccessibleName(f"Mots-clés du type {kind.label}")
        self.count_label = label("", "small")
        self.up_button = _icon_button("chevron-up", "Monter le type")
        self.down_button = _icon_button("chevron-down", "Descendre le type")
        self.delete_button = _icon_button("trash", "Supprimer le type")
        for widget in (self.up_button, self.down_button, self.delete_button):
            widget.setAutoDefault(False)

    def edits(self) -> tuple[QLineEdit, QLineEdit]:
        return (self.name_edit, self.keywords_edit)

    def widgets(self) -> tuple[QWidget, ...]:
        return (
            self.color_button,
            self.name_edit,
            self.command_label,
            self.keywords_edit,
            self.count_label,
            self.up_button,
            self.down_button,
            self.delete_button,
        )


class _TypesPage(QWidget):
    """« Types de tâche »: the shared list, its colours and quick-entry keywords."""

    def __init__(self, snapshot: PlanningSnapshot, run: _Run) -> None:
        super().__init__()
        self._snapshot = snapshot
        self._run = run
        self.rows: list[_TypeRow] = []
        self.ask_replacement: ReplacementAsker = self._ask_replacement
        self.confirm_preset: PresetConfirmer = self._confirm_preset
        self.pick_color: ColorPicker = self._pick_color
        self._build_ui()

    def refresh(self, snapshot: PlanningSnapshot) -> None:
        self._snapshot = snapshot
        kinds = snapshot.meta.task_types
        if [kind.key for kind in kinds] != [row.key for row in self.rows]:
            self._rebuild(kinds)
        counts = task_type_counts(snapshot)
        for position, (row, kind) in enumerate(zip(self.rows, kinds, strict=True)):
            _set_text(row.name_edit, kind.label)
            _set_text(row.keywords_edit, keywords_text(kind.keywords))
            row.keywords_edit.setToolTip(keywords_text(kind.keywords))
            for edit in row.edits():
                if not edit.hasFocus():
                    edit.setCursorPosition(0)  # the start of a long text, not its end
            row.color_button.setIcon(_swatch_icon(_type_color(kind)))
            row.count_label.setText(_tasks_text(counts.get(kind.key, 0)))
            row.up_button.setEnabled(position > 0)
            row.down_button.setEnabled(position + 1 < len(kinds))
            row.delete_button.setEnabled(len(kinds) > 1)
            row.delete_button.setToolTip(
                "Supprimer le type" if len(kinds) > 1 else "Il faut garder au moins un type"
            )
        default = snapshot.meta.task_type(snapshot.meta.default_task_type)
        self.default_label.setText(
            f"Une tâche sans mot-clé reconnu prend le type « {default.label} »."
        )

    def _build_ui(self) -> None:
        content = QVBoxLayout(self)
        content.setContentsMargins(34, 26, 34, 26)
        content.setSpacing(18)
        content.addWidget(label("Types de tâche", "h3"))
        content.addWidget(
            label(
                "Les types colorent les barres et classent les cartes « À planifier ». "
                "En saisie rapide, la commande /\u2060montage ou un mot-clé du titre "
                "(«\u00a0soudure garde-corps\u00a0») choisit le type.",
                "muted",
                wrap=True,
            ),
        )
        preset = BlueprintFrame(padding=(16, 12, 16, 12), layout="h", spacing=12)
        preset_text = QVBoxLayout()
        preset_text.setSpacing(2)
        preset_text.addWidget(label("Liste courte", "field"))
        preset_text.addWidget(
            label(
                "Trois types au lieu du détail : Atelier, Montage et Autre. "
                "Les tâches existantes suivent.",
                "small",
                wrap=True,
            ),
        )
        preset.box.addLayout(preset_text, 1)
        self.preset_button = button("Simplifier : Atelier / Montage / Autre")
        self.preset_button.setAutoDefault(False)
        self.preset_button.clicked.connect(self._simplify)
        preset.box.addWidget(self.preset_button, 0, Qt.AlignmentFlag.AlignVCenter)
        content.addWidget(preset)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(8)
        self.grid.setColumnStretch(1, 3)
        self.grid.setColumnStretch(3, 5)
        content.addLayout(self.grid)
        self.default_label = label("", "small", wrap=True)
        content.addWidget(self.default_label)
        self.error_label = _error_label()
        content.addWidget(self.error_label)
        content.addWidget(rule())
        self.new_type_edit = QLineEdit()
        self.new_type_edit.setPlaceholderText("Nom du nouveau type, p. ex. Atelier")
        self.new_type_edit.returnPressed.connect(self._add)
        self.add_button = button("Ajouter le type", icon="plus")
        self.add_button.setAutoDefault(False)
        self.add_button.clicked.connect(self._add)
        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        add_row.addWidget(self.new_type_edit, 1)
        add_row.addWidget(self.add_button)
        content.addWidget(
            field(
                "Nouveau type",
                add_row,
                help_text="Sa commande de saisie rapide reprend son nom (« /atelier »).",
            ),
        )
        content.addStretch(1)
        self.setMaximumWidth(TYPES_PAGE_MAX_WIDTH)

    def _rebuild(self, kinds: Sequence[TaskType]) -> None:
        # As on the Équipes page: typed text follows its type, old fields go silent first.
        typed: dict[tuple[str, int], tuple[str, int]] = {}
        for row in self.rows:
            for position, edit in enumerate(row.edits()):
                if edit.hasFocus() and edit.isModified():
                    typed[row.key, position] = (edit.text(), edit.cursorPosition())
                edit.blockSignals(True)  # noqa: FBT003
        _clear_layout(self.grid)
        captions = ("", "Nom", "Saisie", "Mots-clés", "Tâches")
        for column, caption in enumerate(captions):
            self.grid.addWidget(label(caption, "field"), 0, column)
        self.rows = []
        for position, kind in enumerate(kinds, start=1):
            row = _TypeRow(kind)
            key = kind.key
            self._fill_color_menu(row)
            row.name_edit.editingFinished.connect(lambda type_key=key: self._rename(type_key))
            row.keywords_edit.editingFinished.connect(
                lambda type_key=key: self._set_keywords(type_key)
            )
            row.up_button.clicked.connect(
                lambda _checked=False, type_key=key: self._move(type_key, -1)
            )
            row.down_button.clicked.connect(
                lambda _checked=False, type_key=key: self._move(type_key, 1)
            )
            row.delete_button.clicked.connect(
                lambda _checked=False, type_key=key: self._delete(type_key)
            )
            for column, widget in enumerate(row.widgets()):
                self.grid.addWidget(widget, position, column)
            self.rows.append(row)
            for field_position, edit in enumerate(row.edits()):
                pending = typed.get((key, field_position))
                if pending is None:
                    continue
                text, cursor = pending
                edit.setText(text)
                edit.setModified(True)
                edit.setCursorPosition(cursor)
                edit.show()
                edit.setFocus()

    def _fill_color_menu(self, row: _TypeRow) -> None:
        dark = current_theme().is_dark
        for light, dark_color in TYPE_PALETTE:
            action = row.color_menu.addAction(
                _swatch_icon(dark_color if dark else light), light.upper()
            )
            action.triggered.connect(
                lambda _checked=False, key=row.key, color=light: self.set_color(key, color)
            )
        row.color_menu.addSeparator()
        other = row.color_menu.addAction("Autre couleur…")
        other.triggered.connect(lambda _checked=False, key=row.key: self._other_color(key))

    def row(self, key: str) -> _TypeRow | None:
        return next((row for row in self.rows if row.key == key), None)

    def _kind(self, key: str) -> TaskType | None:
        return next((kind for kind in self._snapshot.meta.task_types if kind.key == key), None)

    def set_color(self, key: str, color: str) -> None:
        """Colour of type ``key`` (``#rrggbb``); the dark-mode one is derived from it."""
        kind = self._kind(key)
        if kind is None:
            return
        light = color.strip().lower()
        if light == kind.color.lower():
            return
        self._run(
            _type_update(key, color=light, color_dark=dark_variant(light)),
            f"Couleur du type {kind.label}",
            self.error_label,
        )

    def _other_color(self, key: str) -> None:
        kind = self._kind(key)
        if kind is None:
            return
        picked = self.pick_color(kind.color)
        if picked:
            self.set_color(key, picked)

    def _pick_color(self, color: str) -> str | None:
        chosen = QColorDialog.getColor(QColor(color), self, "Couleur du type")
        return chosen.name() if chosen.isValid() else None

    def _rename(self, key: str) -> None:
        row = self.row(key)
        kind = self._kind(key)
        if row is None or kind is None:
            return
        row.name_edit.setModified(False)
        name = " ".join(row.name_edit.text().split())
        if name == kind.label:
            return
        if not name:
            _show_message(self.error_label, "Le nom du type est obligatoire.")
            return
        if any(
            other.key != key and fold_text(other.label) == fold_text(name)
            for other in self._snapshot.meta.task_types
        ):
            _show_message(self.error_label, f"Un type s'appelle déjà « {name} ».")
            return
        self._run(
            _type_update(key, label=name),
            f"Renommer le type {kind.label} en {name}",
            self.error_label,
        )

    def _set_keywords(self, key: str) -> None:
        row = self.row(key)
        kind = self._kind(key)
        if row is None or kind is None:
            return
        row.keywords_edit.setModified(False)
        words = parse_keywords(row.keywords_edit.text())
        if words == kind.keywords:
            _set_text(row.keywords_edit, keywords_text(words))
            return
        for other in self._snapshot.meta.task_types:
            if other.key == key:
                continue
            clash = next((word for word in words if word in other.keywords), None)
            if clash is not None:
                _show_message(
                    self.error_label, f"« {clash} » est déjà un mot-clé du type {other.label}."
                )
                return
        self._run(
            _type_update(key, keywords=words),
            f"Mots-clés du type {kind.label}",
            self.error_label,
        )

    def _move(self, key: str, step: int) -> None:
        kind = self._kind(key)
        if kind is None:
            return

        def build(ctx: CommandContext) -> tuple[DocumentChange, ...]:
            kinds = list(ctx.snapshot.meta.task_types)
            keys = [item.key for item in kinds]
            if key not in keys:
                return ()
            position = keys.index(key)
            target = position + step
            if not 0 <= target < len(kinds):
                return ()
            kinds[position], kinds[target] = kinds[target], kinds[position]
            return update_meta(ctx, task_types=tuple(kinds))

        verb = "Monter" if step < 0 else "Descendre"
        self._run(build, f"{verb} le type {kind.label}", self.error_label)

    def _delete(self, key: str) -> None:
        kind = self._kind(key)
        kinds = self._snapshot.meta.task_types
        if kind is None:
            return
        if len(kinds) <= 1:
            _show_message(self.error_label, "Il faut garder au moins un type de tâche.")
            return
        remaining = [item for item in kinds if item.key != key]
        count = task_type_counts(self._snapshot).get(key, 0)
        replacement = remaining[0].key
        if count:
            chosen = self.ask_replacement(kind, count, remaining)
            if not chosen or all(item.key != chosen for item in remaining):
                return
            replacement = chosen
        # Tasks of a type removed earlier show as this one: they follow it too.
        meta = self._snapshot.meta
        mapping = {
            task.task_type: replacement
            for task in self._snapshot.live_tasks()
            if meta.task_type(task.task_type).key == key
        }

        def build(ctx: CommandContext) -> tuple[DocumentChange, ...]:
            left = tuple(item for item in ctx.snapshot.meta.task_types if item.key != key)
            return replace_task_types(ctx, left, mapping)

        text = f"Supprimer le type {kind.label}"
        if count:
            target = next(item for item in remaining if item.key == replacement)
            text += f" ({_tasks_text(count)} en {target.label})"
        self._run(build, text, self.error_label)

    def _ask_replacement(
        self, kind: TaskType, count: int, remaining: Sequence[TaskType]
    ) -> str | None:
        dialog = TypeChangeDialog(
            f"Supprimer « {kind.label} »",
            f"{_tasks_text(count).capitalize()} {'ont' if count > 1 else 'a'} ce type. "
            "Choisissez le type qui les reprend.",
            "Supprimer le type",
            choices=remaining,
            parent=self,
        )
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        choice = dialog.choice()
        dialog.deleteLater()
        return choice if accepted else None

    def _simplify(self) -> None:
        mapping = simple_mapping(
            {kind.key for kind in self._snapshot.meta.task_types}
            | {task.task_type for task in self._snapshot.live_tasks()}
        )
        retyped = retyped_tasks(self._snapshot, SIMPLE_TASK_TYPES, mapping)
        if self._snapshot.meta.task_types == SIMPLE_TASK_TYPES and not retyped:
            _show_message(self.error_label, "Les types sont déjà Atelier, Montage et Autre.")
            return
        if not self.confirm_preset(len(retyped)):
            return

        def build(ctx: CommandContext) -> tuple[DocumentChange, ...]:
            return replace_task_types(ctx, SIMPLE_TASK_TYPES, mapping)

        self._run(build, "Simplifier les types : Atelier / Montage / Autre", self.error_label)

    def _confirm_preset(self, count: int) -> bool:
        changed = (
            f"{_tasks_text(count).capitalize()} {'changent' if count > 1 else 'change'} de type"
            if count
            else "Aucune tâche ne change de type"
        )
        dialog = TypeChangeDialog(
            "Simplifier les types",
            f"La liste devient Atelier, Montage et Autre. {changed} : débit, soudure et "
            "finition passent en Atelier, montage et livraison en Montage, le reste en "
            "Autre. Ctrl+Z dans le planning annule le tout.",
            "Simplifier",
            parent=self,
        )
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        dialog.deleteLater()
        return accepted

    def _add(self) -> None:
        name = " ".join(self.new_type_edit.text().split())
        if not name:
            _show_message(self.error_label, "Indiquez le nom du nouveau type.")
            return
        kinds = self._snapshot.meta.task_types
        if any(fold_text(kind.label) == fold_text(name) for kind in kinds):
            _show_message(self.error_label, f"Un type s'appelle déjà « {name} ».")
            return
        key = new_type_key(name, (kind.key for kind in kinds))
        color, color_dark = next_palette_color(kinds)

        def build(ctx: CommandContext) -> tuple[DocumentChange, ...]:
            current = ctx.snapshot.meta.task_types
            added = TaskType(key=key, label=name, color=color, color_dark=color_dark)
            return update_meta(ctx, task_types=(*current, added))

        if self._run(build, f"Ajouter le type {name}", self.error_label):
            self.new_type_edit.clear()


def _type_update(key: str, **fields: object) -> CommandBuilder:
    def build(ctx: CommandContext) -> tuple[DocumentChange, ...]:
        kinds = tuple(
            kind.model_copy(update=fields) if kind.key == key else kind
            for kind in ctx.snapshot.meta.task_types
        )
        return update_meta(ctx, task_types=kinds)

    return build


# --------------------------------------------------------------- calendar ----


class _CalendarPage(QWidget):
    def __init__(self, snapshot: PlanningSnapshot, run: _Run) -> None:
        super().__init__()
        self._snapshot = snapshot
        self._run = run
        self._build_ui()
        theme_notifier().changed.connect(self._restyle)
        self._restyle()

    def refresh(self, snapshot: PlanningSnapshot) -> None:
        self._snapshot = snapshot
        meta = snapshot.meta
        self.canton_choice.set_current(meta.default_canton)
        self.hours_label.setText(work_week_text(meta.work_week))
        with QSignalBlocker(self.auto_card_checkbox):
            self.auto_card_checkbox.setChecked(meta.auto_card_on_sortie)
        _clear_layout(self.closure_rows)
        closures = sorted(meta.closures, key=lambda item: (item.first_day, item.last_day))
        for closure in closures:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(label(closure_text(closure)), 1)
            remove = _icon_button("x", "Retirer cette fermeture")
            remove.setAutoDefault(False)
            remove.clicked.connect(lambda _checked=False, item=closure: self._remove(item))
            row.addWidget(remove)
            self.closure_rows.addLayout(row)
        self.no_closure_label.setVisible(not closures)

    def _build_ui(self) -> None:
        content = QVBoxLayout(self)
        content.setContentsMargins(34, 26, 34, 26)
        content.setSpacing(20)
        content.addWidget(label("Calendrier", "h3"))
        self.canton_choice = SegmentedChoice(
            (canton, f"{CANTON_NAMES[canton]} ({canton})") for canton in CANTONS
        )
        self.canton_choice.chosen.connect(self._set_canton)
        self.canton_error_label = _error_label()
        canton_column = QVBoxLayout()
        canton_column.setSpacing(6)
        canton_column.addWidget(self.canton_choice)
        canton_column.addWidget(self.canton_error_label)
        content.addWidget(
            field(
                "Canton par défaut",
                canton_column,
                help_text="Jours fériés des lignes sans canton propre. Chaque personne peut "
                "suivre Genève ou Vaud (page Personnes).",
            ),
        )
        hours = BlueprintFrame(padding=(16, 12, 16, 12), layout="h", spacing=12)
        self.hours_label = label("", "field-lg", wrap=True)
        hours.box.addWidget(self.hours_label, 1)
        hours.box.addWidget(label("Lecture seule", "tag-neutral"), 0, Qt.AlignmentFlag.AlignVCenter)
        content.addWidget(
            field(
                "Horaires de l'atelier",
                hours,
                help_text="Samedi et dimanche ne sont jamais travaillés. Les durées en "
                "jours comptent des jours ouvrés.",
            ),
        )
        self.closure_rows = QVBoxLayout()
        self.closure_rows.setSpacing(4)
        self.no_closure_label = label("Aucune fermeture prévue.", "small")
        self.closure_first_edit = self._date_edit()
        self.closure_last_edit = self._date_edit()
        self.closure_first_edit.dateChanged.connect(self._first_changed)
        self.closure_label_edit = QLineEdit()
        self.closure_label_edit.setPlaceholderText("Vacances d'été, fin d'année…")
        self.closure_label_edit.returnPressed.connect(self._add)
        self.closure_add_button = button("Ajouter", icon="plus")
        self.closure_add_button.clicked.connect(self._add)
        self.closure_error_label = _error_label()
        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        add_row.addWidget(label("Du", "small"))
        add_row.addWidget(self.closure_first_edit)
        add_row.addWidget(label("au", "small"))
        add_row.addWidget(self.closure_last_edit)
        add_row.addWidget(self.closure_label_edit, 1)
        add_row.addWidget(self.closure_add_button)
        closures = QVBoxLayout()
        closures.setSpacing(8)
        closures.addLayout(self.closure_rows)
        closures.addWidget(self.no_closure_label)
        closures.addLayout(add_row)
        closures.addWidget(self.closure_error_label)
        content.addWidget(
            field(
                "Fermetures de l'entreprise",
                closures,
                help_text="Jours non travaillés pour tout le monde, en plus des fériés.",
            ),
        )
        self.auto_card_checkbox = QCheckBox(
            "Créer une carte « À planifier » à chaque sortie dossier",
        )
        self.auto_card_checkbox.toggled.connect(self._set_auto_card)
        self.auto_card_error_label = _error_label()
        auto_column = QVBoxLayout()
        auto_column.setSpacing(6)
        auto_column.addWidget(self.auto_card_checkbox)
        auto_column.addWidget(self.auto_card_error_label)
        content.addWidget(field("Sortie dossier", auto_column))
        content.addStretch(1)
        self.setMaximumWidth(PAGE_MAX_WIDTH)

    def _date_edit(self) -> QDateEdit:
        edit = QDateEdit(QDate.currentDate())
        edit.setCalendarPopup(True)
        edit.setDisplayFormat("dd.MM.yyyy")
        return edit

    def _restyle(self) -> None:
        self.setStyleSheet(date_edit_style_sheet())

    def _first_changed(self, first: QDate) -> None:
        if self.closure_last_edit.date() < first:
            self.closure_last_edit.setDate(first)

    def _set_canton(self, canton: str) -> None:
        if canton == self._snapshot.meta.default_canton:
            return
        if not self._run(
            lambda ctx: update_meta(ctx, default_canton=canton),
            f"Canton par défaut : {canton}",
            self.canton_error_label,
        ):
            self.canton_choice.set_current(self._snapshot.meta.default_canton)

    def _set_auto_card(self, checked: bool) -> None:  # noqa: FBT001
        if checked == self._snapshot.meta.auto_card_on_sortie:
            return
        verb = "Activer" if checked else "Désactiver"
        if not self._run(
            lambda ctx: update_meta(ctx, auto_card_on_sortie=checked),
            f"{verb} la carte automatique à la sortie dossier",
            self.auto_card_error_label,
        ):
            with QSignalBlocker(self.auto_card_checkbox):
                self.auto_card_checkbox.setChecked(not checked)

    def _add(self) -> None:
        first = _pydate(self.closure_first_edit.date())
        last = _pydate(self.closure_last_edit.date())
        if last < first:
            _show_message(self.closure_error_label, "La fin de la fermeture précède son début.")
            return
        text = " ".join(self.closure_label_edit.text().split()) or DEFAULT_CLOSURE_LABEL
        closure = Closure(first_day=first, last_day=last, label=text)
        if closure in self._snapshot.meta.closures:
            _show_message(self.closure_error_label, "Cette fermeture est déjà prévue.")
            return

        def build(ctx: CommandContext) -> tuple[DocumentChange, ...]:
            return update_meta(ctx, closures=(*ctx.snapshot.meta.closures, closure))

        if self._run(build, f"Ajouter la fermeture {text}", self.closure_error_label):
            self.closure_label_edit.clear()

    def _remove(self, closure: Closure) -> None:
        def build(ctx: CommandContext) -> tuple[DocumentChange, ...]:
            remaining = tuple(item for item in ctx.snapshot.meta.closures if item != closure)
            return update_meta(ctx, closures=remaining)

        self._run(build, f"Retirer la fermeture {closure.label}", self.closure_error_label)


# --------------------------------------------------------------- absences ----

ABSENCE_COLUMNS: tuple[str, ...] = ("Personne", "Motif", "Du", "Au", "Partie", "Note")
ALL_PEOPLE: Final = "Toutes les personnes"
ABSENCE_ROW_HEIGHT: Final = 34
_ApplyNoted = Callable[[CommandBuilder, str], None]


def absence_order(absences: Iterable[Absence], today: date) -> list[Absence]:
    """Upcoming and current absences first (soonest first), then past ones (latest first)."""
    items = list(absences)
    upcoming = sorted(
        (item for item in items if item.last_day >= today),
        key=lambda item: (item.first_day, item.last_day, item.id),
    )
    past = sorted(
        (item for item in items if item.last_day < today),
        key=lambda item: (item.first_day, item.last_day, item.id),
        reverse=True,
    )
    return upcoming + past


def _absence_day(day: date) -> str:
    """« lun 26.10.2026 »."""
    return f"{short_day(day)}.{day.year}"


class _AbsencesPage(QWidget):
    """Every absence of the planning, upcoming first, with add, edit and delete."""

    def __init__(
        self,
        snapshot: PlanningSnapshot,
        run: _Run,
        apply: _ApplyNoted,
        read_only_reason: ReadOnlyReason,
        today: Callable[[], date],
    ) -> None:
        super().__init__()
        self._snapshot = snapshot
        self._run = run
        self._apply = apply
        self._read_only_reason = read_only_reason
        self._today = today
        self._shown: list[Absence] = []
        self.absence_dialog: AbsenceDialog | None = None
        self._build_ui()

    # ------------------------------------------------------------ public ----

    def refresh(self, snapshot: PlanningSnapshot) -> None:
        self._snapshot = snapshot
        self._populate_people()
        self._populate()

    def upcoming_count(self) -> int:
        today = self._today()
        return sum(1 for item in self._snapshot.live_absences() if item.last_day >= today)

    def shown_absences(self) -> list[Absence]:
        """Absences listed, top to bottom (for tests)."""
        return list(self._shown)

    def filter_person(self, resource_id: str) -> None:
        index = self.person_filter.findData(resource_id)
        self.person_filter.setCurrentIndex(max(index, 0))

    def select_absence(self, absence_id: str) -> None:
        for row, item in enumerate(self._shown):
            if item.id == absence_id:
                self.table.selectRow(row)
                return

    def selected_absence(self) -> Absence | None:
        selection = self.table.selectionModel()
        rows = selection.selectedRows() if selection is not None else []
        if not rows:
            return None
        row = rows[0].row()
        return self._shown[row] if 0 <= row < len(self._shown) else None

    def add(self) -> None:
        resource_id = str(self.person_filter.currentData() or "")
        self._open(None, resource_id=resource_id, first_day=self._today())

    def edit(self) -> None:
        absence = self.selected_absence()
        if absence is not None:
            self._open(absence)

    def delete(self) -> None:
        absence = self.selected_absence()
        if absence is None:
            return
        reason = self._read_only_reason()
        if reason:
            _show_message(self.error_label, reason)
            return
        absence_id = absence.id

        def build(ctx: CommandContext) -> tuple[DocumentChange, ...]:
            return delete_absence(ctx, absence_id)

        resource = self._snapshot.resources.get(absence.resource_id)
        who = resource.short_label if resource is not None else "?"
        self._run(build, f"Supprimer l'absence de {who}", self.error_label)

    # ------------------------------------------------------------- build ----

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 26, 34, 20)
        root.setSpacing(14)
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(label("Absences", "h3"))
        header.addStretch(1)
        self.add_button = button("Absence", icon="plus")
        self.add_button.setToolTip("Ajouter une absence")
        self.add_button.clicked.connect(self.add)
        header.addWidget(self.add_button)
        root.addLayout(header)
        filters = QHBoxLayout()
        filters.setSpacing(12)
        self.person_filter = QComboBox()
        self.person_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon,
        )
        self.person_filter.setMinimumContentsLength(COMBO_MIN_CHARACTERS * 2)
        self.person_filter.currentIndexChanged.connect(self._populate)
        self.past_checkbox = QCheckBox("Afficher les passées")
        self.past_checkbox.toggled.connect(self._populate)
        filters.addWidget(self.person_filter)
        filters.addWidget(self.past_checkbox)
        filters.addStretch(1)
        root.addLayout(filters)
        self.table = QTableWidget(0, len(ABSENCE_COLUMNS))
        self.table.setHorizontalHeaderLabels(list(ABSENCE_COLUMNS))
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        vertical = self.table.verticalHeader()
        vertical.hide()
        vertical.setDefaultSectionSize(ABSENCE_ROW_HEIGHT)
        horizontal = self.table.horizontalHeader()
        horizontal.setStretchLastSection(True)
        horizontal.setHighlightSections(False)
        horizontal.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for column in range(len(ABSENCE_COLUMNS) - 1):
            horizontal.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        horizontal.setSectionResizeMode(len(ABSENCE_COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        self.table.itemDoubleClicked.connect(self._double_clicked)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        root.addWidget(self.table, 1)
        self.empty_label = label("Aucune absence à venir.", "small")
        root.addWidget(self.empty_label)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.edit_button = button("Modifier…")
        self.edit_button.clicked.connect(self.edit)
        self.delete_button = button("Supprimer", "danger", icon="trash")
        self.delete_button.clicked.connect(self.delete)
        actions.addWidget(self.edit_button)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        root.addLayout(actions)
        self.error_label = _error_label()
        root.addWidget(self.error_label)
        root.addWidget(
            label(
                "Les jours non disponibles chaque semaine (école professionnelle…) se "
                "règlent sur la page Personnes.",
                "small",
                wrap=True,
            )
        )
        self._sync_buttons()

    # ---------------------------------------------------------- populate ----

    def _populate_people(self) -> None:
        current = self.person_filter.currentData()
        with QSignalBlocker(self.person_filter):
            self.person_filter.clear()
            self.person_filter.addItem(ALL_PEOPLE, "")
            for row in self._snapshot.active_resources():
                if row.is_subcontractor:
                    continue
                text = f"{row.initials} · {row.name}" if row.initials else row.name
                self.person_filter.addItem(text, row.id)
            self.person_filter.setCurrentIndex(max(self.person_filter.findData(current), 0))

    def _populate(self) -> None:
        selected = self.selected_absence()
        today = self._today()
        person = str(self.person_filter.currentData() or "")
        past = self.past_checkbox.isChecked()
        absences = [
            item
            for item in self._snapshot.live_absences()
            if (not person or item.resource_id == person) and (past or item.last_day >= today)
        ]
        self._shown = absence_order(absences, today)
        muted = current_theme().color("neutral-600")
        with QSignalBlocker(self.table):
            self.table.clearContents()
            self.table.setRowCount(len(self._shown))
            for row, item in enumerate(self._shown):
                for column, text in enumerate(self._row_texts(item)):
                    cell = QTableWidgetItem(text)
                    cell.setData(_ID_ROLE, item.id)
                    if item.last_day < today:
                        cell.setForeground(muted)
                    self.table.setItem(row, column, cell)
        self.empty_label.setText("Aucune absence." if past else "Aucune absence à venir.")
        self.empty_label.setVisible(not self._shown)
        if selected is not None:
            self.select_absence(selected.id)
        self._sync_buttons()

    def _row_texts(self, item: Absence) -> tuple[str, ...]:
        resource = self._snapshot.resources.get(item.resource_id)
        if resource is None:
            who = "?"
        elif resource.initials and resource.initials != resource.name:
            who = f"{resource.initials} · {resource.name}"
        else:
            who = resource.name
        return (
            who,
            item.label,
            _absence_day(item.first_day),
            _absence_day(item.last_day),
            DAY_PART_LABELS[item.part],
            item.note,
        )

    def _double_clicked(self, item: QTableWidgetItem) -> None:
        self.table.selectRow(item.row())
        self.edit()

    def _sync_buttons(self) -> None:
        chosen = self.selected_absence() is not None
        self.edit_button.setEnabled(chosen)
        self.delete_button.setEnabled(chosen)

    # ------------------------------------------------------------ dialog ----

    def _open(
        self, absence: Absence | None, *, resource_id: str = "", first_day: date | None = None
    ) -> None:
        if self.absence_dialog is not None:
            self.absence_dialog.raise_()
            return
        reason = self._read_only_reason()
        if absence is None and reason:
            _show_message(self.error_label, reason)
            return
        _show_message(self.error_label, "")
        dialog = AbsenceDialog(
            self._snapshot,
            self._apply,
            absence=absence,
            resource_id=resource_id,
            first_day=first_day,
            read_only=bool(reason),
            read_only_message=reason,
            parent=self,
        )
        dialog.finished.connect(lambda _result, shown=dialog: self._closed(shown))
        self.absence_dialog = dialog
        dialog.open()

    def _closed(self, dialog: AbsenceDialog) -> None:
        if dialog is self.absence_dialog:
            self.absence_dialog = None
        dialog.deleteLater()
