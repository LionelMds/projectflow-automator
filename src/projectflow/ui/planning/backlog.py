"""« À planifier »: the shared backlog of tasks without dates, shown as cards.

``build_cards`` turns a snapshot into the cards to show: sorted, filtered and searched
without accents, with the deadline in amber within five working days and in red once
past. The panel paints them like the magnets of the workshop board. A card leaves the
panel as a drag with the task mime type of the grid (``grid.task_mime_data``), and a bar
of the grid dropped on the panel goes back to « À planifier ». Nothing is edited here:
every gesture leaves as a signal that the Planning tab turns into a command.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from PySide6.QtCore import (
    QAbstractListModel,
    QElapsedTimer,
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPoint,
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
    QAction,
    QColor,
    QContextMenuEvent,
    QDrag,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QFocusEvent,
    QFont,
    QFontMetricsF,
    QHideEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QListView,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from projectflow.core.planning.calendar import WorkCalendar
from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.commands import calendar_resolver
from projectflow.core.planning.models import PlanningTask, TaskStatus, TaskType
from projectflow.core.planning.poste_config import BacklogSort
from projectflow.core.planning.quick_entry import fold_text, format_days, format_duration
from projectflow.ui.planning.colors import ColorBy, dossier_colors
from projectflow.ui.planning.colors import warn_color as warn_hex
from projectflow.ui.planning.grid import parse_task_mime, task_mime_data
from projectflow.ui.theme import Theme, body_font, current_theme, heading_font, icon, theme_notifier
from projectflow.ui.widgets.industry import button, label, set_role

BACKLOG_SOURCE = "backlog"
"""``source`` of the task drags that leave « À planifier » (``grid.task_mime_data``)."""

BacklogGroup = Literal["none", "dossier", "type"]
ChipKind = Literal["urgent", "people", "foreseen", "deadline", "soon", "late", "released"]
DeadlineState = Literal["none", "normal", "soon", "late"]
CardAction = Literal["detail", "duplicate", "folder", "delete"]

SORT_LABELS: dict[BacklogSort, str] = {
    "manual": "Manuel",
    "deadline": "Échéance",
    "dossier": "Dossier",
    "added": "Date d'ajout",
}
GROUP_LABELS: dict[BacklogGroup, str] = {
    "none": "Aucun",
    "dossier": "Par dossier",
    "type": "Par type",
}
CARD_ACTION_LABELS: dict[CardAction, str] = {
    "detail": "Détail",
    "duplicate": "Dupliquer",
    "folder": "Ouvrir le dossier",
    "delete": "Supprimer",
}
CARD_ACTION_HINTS: dict[CardAction, str] = {"detail": "Entrée", "delete": "Suppr"}
"""Keys of the card list that do the same, shown in the context menu."""
DROP_HINT = "Déposer ici pour renvoyer dans « À planifier »"
EMPTY_TEXT = "Rien à planifier. Tapez une tâche sans date dans la saisie rapide (Ctrl+K)."
READ_ONLY_EMPTY_TEXT = "Rien à planifier. Planning en lecture seule : aucune carte à ajouter."
FILTERED_EMPTY_TEXT = "Aucune carte ne correspond aux filtres."
NO_DOSSIER_LABEL = "Sans dossier"

DEADLINE_SOON_WORKDAYS = 5
"""A deadline turns amber when at most this many working days are left."""
FLASH_MS = 600
FLASH_TICK_MS = 30
REFRESH_MS = 60_000
"""Period of the refresh of « il y a 5 min » texts."""
SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60

CARD_HEIGHT = 84
CARD_SPACING = 6
HEADER_ROW_HEIGHT = 28
TYPE_STRIP_WIDTH = 4
CARD_PADDING_X = 10
CARD_PADDING_Y = 7
LINE_HEIGHT = 18
CHIP_HEIGHT = 17
CHIP_PADDING_X = 5
CHIP_GAP = 4
ORIGIN_HEIGHT = 14
BADGE_PADDING_X = 6
NEW_DOT_SIZE = 6
HANDLE_DOT_SIZE = 2
HANDLE_DOT_GAP = 3
DROP_LINE_WIDTH = 2
FOCUS_FRAME_WIDTH = 2
EMPTY_TEXT_MARGIN = 18
OVERLAY_TEXT_MARGIN = 30
OVERLAY_BORDER_WIDTH = 2
CHEVRON_SIZE = 7
DRAG_OPACITY = 0.92
DRAGGED_CARD_OPACITY = 0.4
DONE_OPACITY = 0.55
CHIP_SPACING = 4
MENU_DOT_SIZE = 2.4
CAPS_SPACING = 0.9
MENU_DOT_GAP = 5
CHECK_SIZE = 10
COMPACT_CHIPS_STYLE = (
    'QPushButton[variant="chip"] { font-size: 12px; padding: 1px 8px; min-height: 20px; }'
)
"""Filter chips smaller than the application's, like the prototype's."""
WARN_FILL_ALPHA = 0.14

KEYBOARD_FOCUS_REASONS = frozenset(
    {
        Qt.FocusReason.TabFocusReason,
        Qt.FocusReason.BacktabFocusReason,
        Qt.FocusReason.ShortcutFocusReason,
        Qt.FocusReason.OtherFocusReason,
    }
)
"""Ways the card list gets the focus that frame its current row (not a click)."""
HEADER_TOGGLE_KEYS = frozenset({Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter})

_NUMBER_RE = re.compile(r"\d+")
_EMPTY_INDEX = QModelIndex()


# ------------------------------------------------------------------ data ----


@dataclass(frozen=True, slots=True)
class BacklogFilters:
    """Filters of the panel; empty ``teams`` means every team."""

    teams: frozenset[str] = frozenset()
    urgent_only: bool = False
    mine: bool = False
    """Only the dossiers managed by the initials of this poste (« Mes dossiers »)."""
    search: str = ""

    @property
    def active(self) -> bool:
        return bool(self.teams or self.urgent_only or self.mine or self.search.strip())


@dataclass(frozen=True, slots=True)
class CardChip:
    text: str
    kind: ChipKind
    tooltip: str = ""


@dataclass(frozen=True, slots=True)
class CardData:
    """Everything a card shows, computed once from the task and the snapshot."""

    task_id: str
    number: str
    client: str
    title: str
    duration: str
    task_type: str
    type_label: str
    color: str
    color_dark: str
    team: str
    status: TaskStatus
    chips: tuple[CardChip, ...]
    origin: str
    deadline: date | None
    deadline_state: DeadlineState
    urgent: bool
    managed_by: str
    foreseen: tuple[str, ...]
    tooltip: str
    position: int
    """Place in the shared manual order."""
    created_at: str


@dataclass(frozen=True, slots=True)
class CardGroup:
    key: str
    label: str
    cards: tuple[CardData, ...]


# ------------------------------------------------------------- helpers ----


def warn_color(theme: Theme, *, fill: bool = False) -> QColor:
    """Amber of deadlines close at hand (the theme has no amber token), or its light ground."""
    color = QColor(warn_hex(dark=theme.is_dark))
    if fill:
        color.setAlphaF(WARN_FILL_ALPHA)
    return color


def type_color(task_type: TaskType, theme: Theme) -> QColor:
    return QColor(task_type.color_dark if theme.is_dark else task_type.color)


def dossier_color(number: str, theme: Theme) -> QColor:
    """Tint of a dossier, the same as its bars in the grid (sub-projects share it)."""
    return QColor(dossier_colors(number).stripe_for(dark=theme.is_dark))


def format_estimate(task: PlanningTask) -> str:
    """Estimate of a card: days when typed in days, else hours, ``?`` when unknown."""
    if task.estimate_days > 0:
        return f"~{format_days(task.estimate_days)}"
    if task.estimate_minutes > 0:
        return f"~{format_duration(task.estimate_minutes)}"
    return "?"


def short_day(day: date, *, today: date | None = None) -> str:
    """``06.11``, with the year when it differs from today's."""
    if today is not None and day.year != today.year:
        return f"{day.day:02d}.{day.month:02d}.{day.year}"
    return f"{day.day:02d}.{day.month:02d}"


def deadline_state(deadline: date | None, today: date, calendar: WorkCalendar) -> DeadlineState:
    """``late`` once past, ``soon`` with at most five working days left, else ``normal``."""
    if deadline is None:
        return "none"
    if deadline < today:
        return "late"
    left = len(calendar.days_between(today + timedelta(days=1), deadline))
    return "soon" if left <= DEADLINE_SOON_WORKDAYS else "normal"


def parse_stamp(stamp: str) -> datetime | None:
    """UTC time of a document stamp (``2026-10-22T08:15:00Z``), None when unreadable."""
    text = stamp.strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def relative_time(stamp: str, now: datetime) -> str:
    """``à l'instant``, ``il y a 5 min``, ``il y a 3 h``, ``hier à 14:05``, ``le 14.10.2026``."""
    moment = parse_stamp(stamp)
    if moment is None:
        return ""
    seconds = (now - moment).total_seconds()
    if seconds < SECONDS_PER_MINUTE:
        return "à l'instant"
    minutes = int(seconds // SECONDS_PER_MINUTE)
    if minutes < MINUTES_PER_HOUR:
        return f"il y a {minutes} min"
    local, local_now = moment.astimezone(), now.astimezone()
    if local.date() == local_now.date():
        return f"il y a {minutes // MINUTES_PER_HOUR} h"
    if local.date() == local_now.date() - timedelta(days=1):
        return f"hier à {local:%H:%M}"
    return f"le {local:%d.%m.%Y}"


def origin_text(task: PlanningTask, now: datetime) -> str:
    """``auto (sortie dossier)`` or ``par SBE · BUREAU-2 · il y a 5 min``."""
    if task.origin == "sortie_dossier":
        return "auto (sortie dossier)"
    when = relative_time(task.created_at, now)
    who = task.created_by.label
    if who:
        return f"par {who} · {when}" if when else f"par {who}"
    return f"ajouté {when}" if when else ""


def release_day(text: str) -> date | None:
    """Date of the last release to the workshop, written ``2026-10-14`` or ``14.10.2026``."""
    value = text.strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        pass
    parts = value.split(".")
    if len(parts) == len(("day", "month", "year")) and all(part.isdigit() for part in parts):
        try:
            return date(int(parts[2]), int(parts[1]), int(parts[0]))
        except ValueError:
            return None
    return None


def title_text(visible: int, total: int) -> str:
    """``À PLANIFIER · 6``, or ``À PLANIFIER · 2 SUR 6`` while filters hide cards."""
    return f"À PLANIFIER · {visible} SUR {total}" if visible != total else f"À PLANIFIER · {total}"


def summary_text(visible: int, total: int) -> str:
    """``Partagé en direct · 6 cartes``, or ``2 cartes sur 6 · filtres actifs``."""
    noun = "carte" if visible <= 1 else "cartes"
    if visible != total:
        return f"{visible} {noun} sur {total} · filtres actifs"
    return f"Partagé en direct · {total} {noun}"


def number_key(number: str) -> tuple[int, ...]:
    """Natural order of dossier numbers: ``2026-98`` before ``2026-121``."""
    return tuple(int(part) for part in _NUMBER_RE.findall(number))


# ------------------------------------------------------------ building ----


def build_cards(
    snapshot: PlanningSnapshot,
    *,
    today: date,
    sort: BacklogSort = "manual",
    filters: BacklogFilters | None = None,
    search: str | None = None,
    my_initials: str = "",
    now: datetime | None = None,
    calendar: WorkCalendar | None = None,
) -> list[CardData]:
    """Cards of « À planifier » to show, filtered and in the order of ``sort``.

    The search (``search``, else the one of ``filters``) ignores case and accents and
    needs every typed word somewhere in the card (number, client, title, designation,
    type, people foreseen, notes). « Mes dossiers » compares the manager of the dossier
    with ``my_initials``. Deadlines count working days on ``calendar`` (the shared
    default canton when None).
    """
    active = filters or BacklogFilters()
    moment = now or datetime.now(UTC)
    work = calendar or calendar_resolver(snapshot)(None)
    words = fold_text(active.search if search is None else search).split()
    mine = fold_text(my_initials.strip())
    cards: list[CardData] = []
    for position, task in enumerate(snapshot.backlog()):
        card = _card(snapshot, task, position=position, today=today, now=moment, calendar=work)
        if active.teams and card.team not in active.teams:
            continue
        if active.urgent_only and not task.urgent:
            continue
        if active.mine and (not mine or fold_text(card.managed_by.strip()) != mine):
            continue
        if words and not _matches(snapshot, task, card, words):
            continue
        cards.append(card)
    return sort_cards(cards, sort)


def sort_cards(cards: Sequence[CardData], sort: BacklogSort) -> list[CardData]:
    if sort == "deadline":
        return sorted(
            cards,
            key=lambda card: (card.deadline is None, card.deadline or date.max, card.position),
        )
    if sort == "dossier":
        return sorted(
            cards,
            key=lambda card: (not card.number, number_key(card.number), card.position),
        )
    if sort == "added":
        # Newest first; stable, so equal stamps keep the manual order.
        return sorted(cards, key=lambda card: card.created_at, reverse=True)
    return sorted(cards, key=lambda card: card.position)


def group_cards(
    cards: Sequence[CardData],
    group: BacklogGroup,
    *,
    type_order: Sequence[str] = (),
) -> list[CardGroup]:
    """Cards split by dossier or by type, each group keeping the order of ``cards``."""
    if group == "none":
        return [CardGroup(key="", label="", cards=tuple(cards))]
    buckets: dict[str, list[CardData]] = {}
    labels: dict[str, str] = {}
    for card in cards:
        if group == "dossier":
            key = card.number
            text = " ".join(part for part in (card.number, card.client) if part)
            labels.setdefault(key, text if key else NO_DOSSIER_LABEL)
        else:
            key = card.task_type
            labels.setdefault(key, card.type_label)
        buckets.setdefault(key, []).append(card)
    keys = list(buckets)
    if group == "dossier":
        keys.sort(key=lambda key: (not key, number_key(key)))
    else:
        ranks = {key: index for index, key in enumerate(type_order)}
        keys.sort(key=lambda key: ranks.get(key, len(ranks)))
    return [CardGroup(key=key, label=labels[key], cards=tuple(buckets[key])) for key in keys]


def _card(
    snapshot: PlanningSnapshot,
    task: PlanningTask,
    *,
    position: int,
    today: date,
    now: datetime,
    calendar: WorkCalendar,
) -> CardData:
    kind = snapshot.meta.task_type(task.task_type)
    cache = task.project_cache
    foreseen = [snapshot.resources.get(resource_id) for resource_id in task.assignments]
    team = kind.team or next((person.team for person in foreseen if person is not None), "")
    state = deadline_state(task.deadline, today, calendar)
    chips: list[CardChip] = []
    if task.urgent:
        chips.append(CardChip("Urgent", "urgent", "Tâche urgente"))
    if task.required_people > 1:
        chips.append(CardChip(f"{task.required_people} pers.", "people", "Personnes requises"))
    for resource_id, person in zip(task.assignments, foreseen, strict=True):
        text = person.short_label if person is not None else "?"
        name = person.name if person is not None else resource_id
        chips.append(CardChip(text, "foreseen", f"Pressenti : {name}"))
    if task.deadline is not None:
        chip_kind: ChipKind = (
            "late" if state == "late" else "soon" if state == "soon" else "deadline"
        )
        chips.append(
            CardChip(
                f"avant {short_day(task.deadline, today=today)}",
                chip_kind,
                f"Échéance : {task.deadline:%d.%m.%Y}",
            )
        )
    released = release_day(cache.atelier_release)
    if released is not None:
        chips.append(
            CardChip(
                f"Sorti {short_day(released, today=today)}",
                "released",
                f"Sortie atelier le {released:%d.%m.%Y}",
            )
        )
    names = [person.name for person in foreseen if person is not None]
    tooltip_lines = [
        " · ".join(part for part in (task.project_number, cache.client) if part),
        task.display_title,
        cache.designation if cache.designation != task.display_title else "",
        f"Type : {kind.label}",
        f"Pressentis : {', '.join(names)}" if names else "",
        f"Notes : {task.notes}" if task.notes else "",
    ]
    return CardData(
        task_id=task.id,
        number=task.project_number,
        client=cache.client,
        title=task.display_title,
        duration=format_estimate(task),
        task_type=kind.key,
        type_label=kind.label,
        color=kind.color,
        color_dark=kind.color_dark,
        team=team,
        status=task.status,
        chips=tuple(chips),
        origin=origin_text(task, now),
        deadline=task.deadline,
        deadline_state=state,
        urgent=task.urgent,
        managed_by=cache.managed_by,
        foreseen=tuple(task.assignments),
        tooltip="\n".join(line for line in tooltip_lines if line),
        position=position,
        created_at=task.created_at,
    )


def _matches(
    snapshot: PlanningSnapshot,
    task: PlanningTask,
    card: CardData,
    words: Sequence[str],
) -> bool:
    cache = task.project_cache
    people = [snapshot.resources.get(resource_id) for resource_id in task.assignments]
    parts = [
        card.number,
        card.client,
        card.title,
        task.title,
        cache.designation,
        cache.contact,
        cache.managed_by,
        card.type_label,
        task.notes,
        *(f"{person.initials} {person.name}" for person in people if person is not None),
    ]
    haystack = fold_text(" ".join(parts))
    return all(word in haystack for word in words)


# --------------------------------------------------------------- layout ----


class FlowLayout(QLayout):
    """Lays its items out in rows and wraps to a new row when the width is used up."""

    def __init__(self, parent: QWidget | None = None, *, spacing: int = CHIP_SPACING) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._gap = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize(0, 0)
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def clear(self) -> None:
        """Remove and delete every widget of the layout."""
        while self._items:
            item = self._items.pop()
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

    def _arrange(self, rect: QRect, *, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, line = area.x(), area.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            if x > area.x() and x + hint.width() > area.right() + 1:
                x = area.x()
                y += line + self._gap
                line = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._gap
            line = max(line, hint.height())
        return y + line - rect.y() + margins.bottom()


# -------------------------------------------------------------- painting ----


@dataclass(slots=True)
class _ViewState:
    """What the delegate needs besides the card: hover, novelty, flashes, colours."""

    color_by: ColorBy = "type"
    hover_row: int = -1
    reorder: bool = True
    read_only: bool = False
    focus_visible: bool = False
    """Frame the current row: the list got the focus from the keyboard, not a click."""
    dragging_id: str = ""
    backlog_ids: frozenset[str] = frozenset()
    """Every task of « À planifier », shown or filtered out."""
    new_ids: set[str] = field(default_factory=set)
    flashes: dict[str, float] = field(default_factory=dict)

    def color_of(self, card: CardData, theme: Theme) -> QColor:
        if self.color_by == "dossier" and card.number:
            return dossier_color(card.number, theme)
        return QColor(card.color_dark if theme.is_dark else card.color)


def _chip_colors(
    kind: ChipKind,
    theme: Theme,
) -> tuple[QColor, QColor | None, QColor | None, bool]:
    """``(ink, border, fill, dashed)`` of a chip."""
    if kind == "soon":
        ink = warn_color(theme)
        return ink, ink, warn_color(theme, fill=True), False
    if kind in {"late", "urgent"}:
        ink = theme.color("err-ink") if kind == "late" else theme.color("err")
        return ink, theme.color("err"), theme.color("err", 0.10), False
    if kind == "foreseen":
        return theme.color("neutral-800"), theme.color("neutral-600"), None, True
    if kind == "released":
        return theme.color("neutral-800"), None, theme.color("text", 0.07), False
    return theme.color("neutral-800"), theme.color("text", 0.26), None, False


def _paint_chips(
    painter: QPainter,
    chips: Sequence[CardChip],
    rect: QRectF,
    theme: Theme,
) -> None:
    font = body_font(11, QFont.Weight.Medium)
    metrics = QFontMetricsF(font)
    painter.setFont(font)
    x = rect.left()
    for index, chip in enumerate(chips):
        width = metrics.horizontalAdvance(chip.text) + 2 * CHIP_PADDING_X
        if x + width > rect.right():
            more = f"+{len(chips) - index}"
            more_width = metrics.horizontalAdvance(more) + 2 * CHIP_PADDING_X
            if x + more_width <= rect.right():
                painter.setPen(theme.color("neutral-700"))
                painter.drawText(
                    QRectF(x, rect.top(), more_width, rect.height()),
                    int(Qt.AlignmentFlag.AlignCenter),
                    more,
                )
            return
        box = QRectF(x, rect.top(), width, rect.height()).adjusted(0.5, 0.5, -0.5, -0.5)
        ink, border, fill, dashed = _chip_colors(chip.kind, theme)
        if fill is not None:
            painter.fillRect(box, fill)
        if border is not None:
            pen = QPen(border)
            pen.setWidthF(1.0)
            if dashed:
                pen.setStyle(Qt.PenStyle.CustomDashLine)
                pen.setDashPattern([2.0, 2.0])
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(box)
        painter.setPen(ink)
        painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), chip.text)
        x += width + CHIP_GAP


def paint_card(
    painter: QPainter,
    rect: QRectF,
    card: CardData,
    *,
    theme: Theme,
    color: QColor,
    hovered: bool = False,
    selected: bool = False,
    new: bool = False,
    flash: float = 0.0,
    handle: bool = False,
    menu: bool = False,
    focused: bool = False,
) -> None:
    """Draw one card in ``rect`` (the card itself, without the spacing below it)."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    if card.status == "done":
        painter.setOpacity(painter.opacity() * DONE_OPACITY)
    strip = _paint_card_frame(
        painter, rect, card, theme=theme, color=color, hovered=hovered, selected=selected
    )
    if flash > 0:
        painter.fillRect(rect, theme.color("accent-200", min(flash, 1.0)))
    left = rect.left() + TYPE_STRIP_WIDTH + CARD_PADDING_X
    width = rect.right() - CARD_PADDING_X - left
    line1 = QRectF(left, rect.top() + CARD_PADDING_Y, width, LINE_HEIGHT)
    line2 = QRectF(left, line1.bottom() + 2, width, LINE_HEIGHT)
    line3 = QRectF(left, line2.bottom() + 2, width, CHIP_HEIGHT)
    line4 = QRectF(left, line3.bottom() + 3, width, ORIGIN_HEIGHT)
    _paint_first_line(painter, line1, card, theme, new=new)
    _paint_title_line(painter, line2, card, theme, menu_rect=card_menu_rect(rect) if menu else None)
    _paint_chips(painter, card.chips, line3, theme)
    if card.origin:
        origin_font = body_font(11)
        painter.setFont(origin_font)
        painter.setPen(theme.color("neutral-700"))
        painter.drawText(
            line4,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            QFontMetricsF(origin_font).elidedText(
                card.origin, Qt.TextElideMode.ElideRight, line4.width()
            ),
        )
    if handle:
        _paint_handle(painter, strip, rect, theme)
    if focused:
        _paint_focus_frame(painter, rect, theme)
    painter.restore()


def _paint_focus_frame(painter: QPainter, rect: QRectF, theme: Theme) -> None:
    """Accent frame of the row the keyboard is on (Enter opens it)."""
    pen = QPen(theme.color("accent"))
    pen.setWidthF(FOCUS_FRAME_WIDTH)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    painter.save()
    painter.setOpacity(1.0)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    inset = FOCUS_FRAME_WIDTH / 2
    painter.drawRect(rect.adjusted(inset, inset, -inset, -inset))
    painter.restore()


def _paint_card_frame(
    painter: QPainter,
    rect: QRectF,
    card: CardData,
    *,
    theme: Theme,
    color: QColor,
    hovered: bool,
    selected: bool,
) -> QRectF:
    """Paint the ground, the border (dashed when provisional) and the type stripe."""
    painter.fillRect(rect, theme.color("accent-100") if selected else theme.color("surface"))
    border = theme.color("accent") if selected else theme.color("text", 0.30 if hovered else 0.14)
    pen = QPen(border)
    pen.setWidthF(1.0)
    if card.status == "provisional":
        pen.setStyle(Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([3.0, 2.0])
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(rect)
    strip = QRectF(rect.left(), rect.top(), TYPE_STRIP_WIDTH, rect.height())
    strip_color = QColor(color)
    if card.status == "provisional":
        strip_color.setAlphaF(0.55)
    painter.fillRect(strip, strip_color)
    return strip


def _paint_first_line(
    painter: QPainter,
    line: QRectF,
    card: CardData,
    theme: Theme,
    *,
    new: bool,
) -> None:
    """Paint the dossier number, the client, the « nouveau » mark and the estimate."""
    left = line.left()
    badge_font = body_font(12, QFont.Weight.DemiBold)
    badge_width = QFontMetricsF(badge_font).horizontalAdvance(card.duration) + 2 * BADGE_PADDING_X
    badge = QRectF(line.right() - badge_width, line.top() + 1, badge_width, LINE_HEIGHT - 2)
    painter.fillRect(badge, theme.color("text", 0.08))
    painter.setFont(badge_font)
    painter.setPen(theme.color("text") if card.duration != "?" else theme.color("neutral-700"))
    painter.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), card.duration)
    text_right = badge.left() - 8
    if new:
        text_right = _paint_new_mark(painter, line, text_right, theme)
    number_font = heading_font(15)
    number_metrics = QFontMetricsF(number_font)
    room = max(0.0, text_right - left)
    number_text = number_metrics.elidedText(
        card.number or card.type_label, Qt.TextElideMode.ElideRight, room
    )
    painter.setFont(number_font)
    painter.setPen(theme.color("text"))
    painter.drawText(
        QRectF(left, line.top(), room, LINE_HEIGHT),
        int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
        number_text,
    )
    client_left = left + number_metrics.horizontalAdvance(number_text) + 7
    if card.client and client_left < text_right:
        client_font = body_font(12)
        painter.setFont(client_font)
        painter.setPen(theme.color("neutral-800"))
        painter.drawText(
            QRectF(client_left, line.top(), text_right - client_left, LINE_HEIGHT),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            QFontMetricsF(client_font).elidedText(
                card.client, Qt.TextElideMode.ElideRight, text_right - client_left
            ),
        )


def _paint_new_mark(painter: QPainter, line: QRectF, right: float, theme: Theme) -> float:
    """Paint the accent dot and « NOUVEAU » ending at ``right``; return what is left."""
    font = body_font(10, QFont.Weight.DemiBold)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
    text = "NOUVEAU"
    width = QFontMetricsF(font).horizontalAdvance(text)
    left = right - width
    painter.setFont(font)
    painter.setPen(theme.color("accent-800"))
    painter.drawText(
        QRectF(left, line.top(), width, LINE_HEIGHT),
        int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
        text,
    )
    dot = QRectF(
        left - NEW_DOT_SIZE - 4, line.center().y() - NEW_DOT_SIZE / 2, NEW_DOT_SIZE, NEW_DOT_SIZE
    )
    painter.fillRect(dot, theme.color("accent"))
    return dot.left() - 6


def _paint_title_line(
    painter: QPainter,
    line: QRectF,
    card: CardData,
    theme: Theme,
    *,
    menu_rect: QRectF | None,
) -> None:
    right = line.right()
    if menu_rect is not None:
        # The body fonts have no « ⋯ »: three dots drawn as squares.
        color = theme.color("neutral-800")
        center = menu_rect.center()
        for step in (-1, 0, 1):
            painter.fillRect(
                QRectF(
                    center.x() + step * MENU_DOT_GAP - MENU_DOT_SIZE / 2,
                    center.y() - MENU_DOT_SIZE / 2,
                    MENU_DOT_SIZE,
                    MENU_DOT_SIZE,
                ),
                color,
            )
        right = menu_rect.left() - 4
    left = line.left()
    if card.status == "done":
        _paint_check(painter, QPointF(left + CHECK_SIZE / 2, line.center().y()), theme)
        left += CHECK_SIZE + 4
    font = body_font(13, QFont.Weight.Medium)
    painter.setFont(font)
    painter.setPen(theme.color("text"))
    painter.drawText(
        QRectF(left, line.top(), right - left, LINE_HEIGHT),
        int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
        QFontMetricsF(font).elidedText(card.title, Qt.TextElideMode.ElideRight, right - left),
    )


def _paint_check(painter: QPainter, center: QPointF, theme: Theme) -> None:
    """Draw the tick of a finished task (the body fonts have no « ✓ »)."""
    half = CHECK_SIZE / 2
    path = QPainterPath()
    path.moveTo(center.x() - half, center.y())
    path.lineTo(center.x() - half / 3, center.y() + half * 0.7)
    path.lineTo(center.x() + half, center.y() - half * 0.7)
    pen = QPen(theme.color("text"))
    pen.setWidthF(1.6)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)


def _paint_handle(painter: QPainter, strip: QRectF, rect: QRectF, theme: Theme) -> None:
    """Paint the « ⋮⋮ » grip of a card that can be reordered."""
    color = theme.color("neutral-600")
    x0 = strip.right() + HANDLE_DOT_GAP
    y0 = rect.center().y() - HANDLE_DOT_GAP * 2
    for column in range(2):
        for row in range(3):
            painter.fillRect(
                QRectF(
                    x0 + column * HANDLE_DOT_GAP,
                    y0 + row * (HANDLE_DOT_GAP + 1),
                    HANDLE_DOT_SIZE,
                    HANDLE_DOT_SIZE,
                ),
                color,
            )


def card_menu_rect(card_rect: QRectF) -> QRectF:
    """Where the « ⋯ » menu of a hovered card sits (right end of the title line)."""
    top = card_rect.top() + CARD_PADDING_Y + LINE_HEIGHT + 2
    return QRectF(card_rect.right() - CARD_PADDING_X - 20, top, 20, LINE_HEIGHT)


def card_pixmap(card: CardData, width: int, *, color_by: ColorBy = "type") -> QPixmap:
    """Picture of a card, used as the drag pixmap."""
    theme = current_theme()
    ratio = 2.0
    pixmap = QPixmap(round(width * ratio), round(CARD_HEIGHT * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setOpacity(DRAG_OPACITY)
    paint_card(
        painter,
        QRectF(0.5, 0.5, width - 1, CARD_HEIGHT - 1),
        card,
        theme=theme,
        color=_ViewState(color_by=color_by).color_of(card, theme),
        selected=True,
    )
    painter.end()
    return pixmap


def _paint_group_header(
    painter: QPainter,
    rect: QRectF,
    row: _Row,
    theme: Theme,
    *,
    focused: bool = False,
) -> None:
    if focused:
        _paint_focus_frame(painter, rect.adjusted(0.5, 0.5, -0.5, -1.5), theme)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = theme.color("neutral-800")
    chevron = QPainterPath()
    cx, cy = rect.left() + CHEVRON_SIZE / 2 + 2, rect.center().y()
    half = CHEVRON_SIZE / 2
    if row.collapsed:
        chevron.moveTo(cx - half / 2, cy - half)
        chevron.lineTo(cx + half / 2, cy)
        chevron.lineTo(cx - half / 2, cy + half)
    else:
        chevron.moveTo(cx - half, cy - half / 2)
        chevron.lineTo(cx, cy + half / 2)
        chevron.lineTo(cx + half, cy - half / 2)
    pen = QPen(color)
    pen.setWidthF(1.3)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(chevron)
    font = body_font(11, QFont.Weight.DemiBold)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.9)
    painter.setFont(font)
    text_rect = rect.adjusted(CHEVRON_SIZE + 10, 0, 0, -2)
    painter.drawText(
        text_rect,
        int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
        QFontMetricsF(font).elidedText(
            f"{row.label.upper()} ({row.count})", Qt.TextElideMode.ElideRight, text_rect.width()
        ),
    )
    rule_pen = QPen(theme.color("text", 0.18))
    rule_pen.setWidthF(1.0)
    painter.setPen(rule_pen)
    painter.drawLine(
        QPointF(rect.left(), rect.bottom() - 0.5), QPointF(rect.right(), rect.bottom() - 0.5)
    )
    painter.restore()


# ---------------------------------------------------------- model/view ----


@dataclass(frozen=True, slots=True)
class _Row:
    card: CardData | None = None
    group_key: str = ""
    label: str = ""
    count: int = 0
    collapsed: bool = False


class _CardModel(QAbstractListModel):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.rows: list[_Row] = []

    def set_rows(self, rows: list[_Row]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def row_at(self, row: int) -> _Row | None:
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def row_of(self, task_id: str) -> int:
        return next(
            (
                index
                for index, row in enumerate(self.rows)
                if row.card is not None and row.card.task_id == task_id
            ),
            -1,
        )

    def header_row(self, group_key: str) -> int:
        return next(
            (
                index
                for index, row in enumerate(self.rows)
                if row.card is None and row.group_key == group_key
            ),
            -1,
        )

    def card_ids(self) -> list[str]:
        return [row.card.task_id for row in self.rows if row.card is not None]

    def rowCount(  # noqa: N802
        self,
        parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX,
    ) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        row = self.row_at(index.row()) if index.isValid() else None
        if row is None:
            return None
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            if row.card is None:
                return f"{row.label} ({row.count})"
            return " · ".join(
                part for part in (row.card.number, row.card.client, row.card.title) if part
            )
        if role == Qt.ItemDataRole.ToolTipRole and row.card is not None:
            return row.card.tooltip
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        row = self.row_at(index.row()) if index.isValid() else None
        if row is None:
            return Qt.ItemFlag.NoItemFlags
        if row.card is None:
            return Qt.ItemFlag.ItemIsEnabled
        return (
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
        )


class _CardDelegate(QStyledItemDelegate):
    def __init__(self, model: _CardModel, state: _ViewState, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._model = model
        self._state = state

    def sizeHint(  # noqa: N802
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> QSize:
        row = self._model.row_at(index.row())
        width = option.rect.width()
        if row is not None and row.card is None:
            return QSize(width, HEADER_ROW_HEIGHT)
        return QSize(width, CARD_HEIGHT + CARD_SPACING)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        row = self._model.row_at(index.row())
        if row is None:
            return
        theme = current_theme()
        rect = QRectF(option.rect)
        state = self._state
        focused = state.focus_visible and bool(option.state & QStyle.StateFlag.State_HasFocus)
        if row.card is None:
            _paint_group_header(painter, rect, row, theme, focused=focused)
            return
        card = row.card
        hovered = index.row() == state.hover_row
        painter.save()
        if card.task_id == state.dragging_id:
            painter.setOpacity(DRAGGED_CARD_OPACITY)
        paint_card(
            painter,
            card_rect(rect),
            card,
            theme=theme,
            color=state.color_of(card, theme),
            hovered=hovered,
            selected=bool(option.state & QStyle.StateFlag.State_Selected),
            new=card.task_id in state.new_ids,
            flash=state.flashes.get(card.task_id, 0.0),
            handle=hovered and state.reorder and not state.read_only,
            menu=hovered,
            focused=focused,
        )
        painter.restore()


def card_rect(item_rect: QRectF) -> QRectF:
    """Return the card inside its list item, which also holds the spacing below the card."""
    return QRectF(
        item_rect.left(), item_rect.top(), item_rect.width(), item_rect.height() - CARD_SPACING
    ).adjusted(0.5, 0.5, -0.5, -0.5)


class _CardList(QListView):
    """List of cards: drags out, internal reordering, drops from the grid, keyboard."""

    card_activated = Signal(str)
    card_menu = Signal(str, QPoint)
    card_hovered = Signal(str)
    delete_pressed = Signal(str)
    header_toggled = Signal(str)
    reorder_dropped = Signal(str, str, str)
    foreign_entered = Signal()
    foreign_dropped = Signal(str)
    drag_left = Signal()

    def __init__(self, model: _CardModel, state: _ViewState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = model
        self._state = state
        self._drop_row = -1
        self._press_pos: QPoint | None = None
        self.empty_text = EMPTY_TEXT
        self.setModel(model)
        self.setItemDelegate(_CardDelegate(model, state, self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(False)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAutoFillBackground(False)
        self.viewport().setAutoFillBackground(False)
        self.verticalScrollBar().setSingleStep(CARD_HEIGHT // 3)

    @property
    def drop_row(self) -> int:
        return self._drop_row

    def card_id_at(self, pos: QPoint) -> str:
        row = self._model.row_at(self.indexAt(pos).row())
        return row.card.task_id if row is not None and row.card is not None else ""

    def current_card_id(self) -> str:
        row = self._model.row_at(self.currentIndex().row())
        return row.card.task_id if row is not None and row.card is not None else ""

    def current_group_key(self) -> str | None:
        """Group of the current row when it is a group header, else None."""
        row = self._model.row_at(self.currentIndex().row())
        return row.group_key if row is not None and row.card is None else None

    # -- drag out -----------------------------------------------------------

    def startDrag(self, _supported: Qt.DropAction) -> None:  # noqa: N802
        index = self.currentIndex()
        row = self._model.row_at(index.row())
        if row is None or row.card is None or self._state.read_only:
            return
        rect = self.visualRect(index)
        drag = QDrag(self)
        drag.setMimeData(task_mime_data(row.card.task_id, source=BACKLOG_SOURCE))
        drag.setPixmap(card_pixmap(row.card, rect.width(), color_by=self._state.color_by))
        press = self._press_pos or rect.topLeft() + QPoint(24, 18)
        drag.setHotSpot(press - rect.topLeft())
        self._state.dragging_id = row.card.task_id
        self.viewport().update()
        try:
            drag.exec(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction, Qt.DropAction.MoveAction)
        finally:
            self._state.dragging_id = ""
            self._set_drop_row(-1)

    # -- drops --------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        self._drag_over(event, entering=True)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        self._drag_over(event, entering=False)

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # noqa: N802
        self._set_drop_row(-1)
        self.drag_left.emit()
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        payload = parse_task_mime(event.mimeData())
        self._set_drop_row(-1)
        if payload is None or self._state.read_only:
            event.ignore()
            return
        if self.is_own(payload.task_id):
            drop_row = self.drop_row_at(event.position().toPoint())
            moved = self.reorder_target(payload.task_id, drop_row) if self._state.reorder else None
            if moved is not None:
                self.reorder_dropped.emit(payload.task_id, *moved)
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            return
        self.foreign_dropped.emit(payload.task_id)
        event.acceptProposedAction()

    def is_own(self, task_id: str) -> bool:
        """Whether ``task_id`` is one of the cards: its drop reorders instead of unplanning."""
        return task_id in self._state.backlog_ids

    def _drag_over(self, event: QDragMoveEvent, *, entering: bool) -> None:
        payload = parse_task_mime(event.mimeData())
        if payload is None or self._state.read_only:
            event.ignore()
            return
        if self.is_own(payload.task_id):
            if not self._state.reorder:
                event.ignore()
                return
            self._set_drop_row(self.drop_row_at(event.position().toPoint()))
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            return
        if entering:
            self.foreign_entered.emit()
        event.acceptProposedAction()

    def drop_row_at(self, pos: QPoint) -> int:
        """Return where a card dropped at ``pos`` (viewport coordinates) is inserted."""
        index = self.indexAt(pos)
        if not index.isValid():
            count = self._model.rowCount()
            if not count:
                return 0
            first = self.visualRect(self._model.index(0, 0))
            return 0 if pos.y() < first.top() else count
        rect = self.visualRect(index)
        middle = rect.top() + (rect.height() - CARD_SPACING) / 2
        return index.row() + (1 if pos.y() > middle else 0)

    def reorder_target(self, task_id: str, drop_row: int) -> tuple[str, str] | None:
        """``(above_id, below_id)`` of a card dropped at ``drop_row``; None if it stays."""
        ids = self._model.card_ids()
        if task_id not in ids or drop_row < 0:
            return None
        current = ids.index(task_id)
        if drop_row in (current, current + 1):
            return None
        others = [card_id for card_id in ids if card_id != task_id]
        position = drop_row if drop_row <= current else drop_row - 1
        above = others[position - 1] if position > 0 else ""
        below = others[position] if position < len(others) else ""
        return above, below

    def _set_drop_row(self, row: int) -> None:
        if row != self._drop_row:
            self._drop_row = row
            self.viewport().update()

    # -- mouse and keyboard ------------------------------------------------

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._set_hover(self.indexAt(event.position().toPoint()).row())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self._set_hover(-1)
        super().leaveEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        reason = event.reason()
        if reason == Qt.FocusReason.MouseFocusReason:
            self._show_focus(visible=False)
        elif reason in KEYBOARD_FOCUS_REASONS:
            self._show_focus(visible=True)
        super().focusInEvent(event)

    def _show_focus(self, *, visible: bool) -> None:
        if visible != self._state.focus_visible:
            self._state.focus_visible = visible
            self.viewport().update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position().toPoint()
        self._press_pos = pos
        self._show_focus(visible=False)
        index = self.indexAt(pos)
        row = self._model.row_at(index.row())
        if row is not None and row.card is None:
            if event.button() == Qt.MouseButton.LeftButton:
                self.header_toggled.emit(row.group_key)
            event.accept()
            return
        if (
            row is not None
            and row.card is not None
            and event.button() == Qt.MouseButton.LeftButton
            and card_menu_rect(card_rect(QRectF(self.visualRect(index)))).contains(QPointF(pos))
        ):
            self.setCurrentIndex(index)
            self.card_menu.emit(row.card.task_id, self.viewport().mapToGlobal(pos))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        task_id = self.card_id_at(event.position().toPoint())
        if task_id and event.button() == Qt.MouseButton.LeftButton:
            self.card_activated.emit(task_id)
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        task_id = self.current_card_id()
        key = event.key()
        self._show_focus(visible=True)
        group = self.current_group_key()
        if group is not None and key in HEADER_TOGGLE_KEYS:
            self.header_toggled.emit(group)
            event.accept()
            return
        if task_id and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.card_activated.emit(task_id)
            event.accept()
            return
        if task_id and key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if not self._state.read_only:
                self.delete_pressed.emit(task_id)
            event.accept()
            return
        if task_id and key == Qt.Key.Key_Menu:
            rect = self.visualRect(self.currentIndex())
            self.card_menu.emit(task_id, self.viewport().mapToGlobal(rect.center()))
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802
        pos = self.viewport().mapFromGlobal(event.globalPos())
        index = self.indexAt(pos)
        row = self._model.row_at(index.row())
        if row is None or row.card is None:
            event.ignore()
            return
        self.setCurrentIndex(index)
        self.card_menu.emit(row.card.task_id, event.globalPos())
        event.accept()

    def _set_hover(self, row: int) -> None:
        if row == self._state.hover_row:
            return
        self._state.hover_row = row
        self.viewport().update()
        entry = self._model.row_at(row)
        if entry is not None and entry.card is not None:
            self.card_hovered.emit(entry.card.task_id)

    # -- painting -----------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        theme = current_theme()
        painter = QPainter(self.viewport())
        if not self._model.rowCount():
            painter.setFont(body_font(13))
            painter.setPen(theme.color("neutral-700"))
            painter.drawText(
                QRectF(self.viewport().rect()).adjusted(
                    EMPTY_TEXT_MARGIN, EMPTY_TEXT_MARGIN, -EMPTY_TEXT_MARGIN, -EMPTY_TEXT_MARGIN
                ),
                int(
                    Qt.AlignmentFlag.AlignTop
                    | Qt.AlignmentFlag.AlignHCenter
                    | Qt.TextFlag.TextWordWrap
                ),
                self.empty_text,
            )
        if self._drop_row >= 0:
            y = self._drop_line_y(self._drop_row)
            pen = QPen(theme.color("accent"))
            pen.setWidthF(DROP_LINE_WIDTH)
            painter.setPen(pen)
            width = self.viewport().width()
            painter.drawLine(QPointF(0, y), QPointF(width, y))
            painter.drawLine(QPointF(0.5, y - 4), QPointF(0.5, y + 4))
            painter.drawLine(QPointF(width - 0.5, y - 4), QPointF(width - 0.5, y + 4))
        painter.end()

    def _drop_line_y(self, row: int) -> float:
        count = self._model.rowCount()
        if not count:
            return 1.0
        if row < count:
            top = self.visualRect(self._model.index(row, 0)).top()
            return max(1.0, top - CARD_SPACING / 2)
        bottom = self.visualRect(self._model.index(count - 1, 0)).bottom()
        return bottom - CARD_SPACING / 2


class DropOverlay(QWidget):
    """Full-panel highlight shown while a bar of the grid is dragged over the panel.

    Once shown, it takes the drag itself, over fields that would refuse it. The widget
    that showed it calls ``release_later`` when the drag leaves it: the overlay then hides
    unless the drag has moved onto it meanwhile, so a drag that leaves or is cancelled
    first never leaves it over the cards.
    """

    dropped = Signal(str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._has_drag = False
        self.setAcceptDrops(True)
        self.hide()

    def has_drag(self) -> bool:
        """Whether the drag is over the overlay itself."""
        return self._has_drag

    def release_later(self) -> None:
        """Hide once the events of the moment are handled, unless the drag came onto it."""
        QTimer.singleShot(0, self, self._release)

    def _release(self) -> None:
        if not self._has_drag:
            self.hide()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if parse_task_mime(event.mimeData()) is None:
            event.ignore()
            return
        self._has_drag = True
        event.acceptProposedAction()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        event.acceptProposedAction()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # noqa: N802
        self._has_drag = False
        self.hide()
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        payload = parse_task_mime(event.mimeData())
        self._has_drag = False
        self.hide()
        if payload is None:
            event.ignore()
            return
        event.acceptProposedAction()
        self.dropped.emit(payload.task_id)

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        self._has_drag = False
        super().hideEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        theme = current_theme()
        painter = QPainter(self)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.fillRect(rect, theme.color("bg", 0.80))
        painter.fillRect(rect, theme.color("accent", 0.14))
        pen = QPen(theme.color("accent"))
        pen.setWidthF(OVERLAY_BORDER_WIDTH)
        pen.setStyle(Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([3.0, 2.0])
        painter.setPen(pen)
        painter.drawRect(rect)
        painter.setFont(heading_font(17))
        painter.setPen(theme.color("accent-800"))
        painter.drawText(
            rect.adjusted(
                OVERLAY_TEXT_MARGIN, OVERLAY_TEXT_MARGIN, -OVERLAY_TEXT_MARGIN, -OVERLAY_TEXT_MARGIN
            ),
            int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
            DROP_HINT,
        )
        painter.end()


# ----------------------------------------------------------------- panel ----


class BacklogPanel(QWidget):
    """Face « À planifier » of the side panel."""

    card_activated = Signal(str)
    card_action = Signal(str, str)
    reorder_requested = Signal(str, str, str)
    unschedule_requested = Signal(str)
    new_entry_requested = Signal()
    filters_changed = Signal()
    count_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._snapshot: PlanningSnapshot | None = None
        self._today: date | None = None
        self._now: datetime | None = None
        self._my_initials = ""
        self._sort: BacklogSort = "manual"
        self._group: BacklogGroup = "none"
        self._filters = BacklogFilters()
        self._collapsed: set[str] = set()
        self._cards: list[CardData] = []
        self._backlog_ids: frozenset[str] = frozenset()
        self._total = -1
        self._read_only = False
        self._title_visible = True
        self._team_keys: tuple[tuple[str, str], ...] = ()
        self._team_chips: dict[str, QPushButton] = {}
        self._clock = QElapsedTimer()
        self._clock.start()
        self._flash_starts: dict[str, int] = {}
        self._state = _ViewState()
        self._model = _CardModel(self)

        self.title_label = label("", "caps")
        self.new_button = button("", "icon", icon="plus")
        self.new_button.setToolTip("Nouvelle tâche à planifier (Ctrl+K)")
        self.new_button.setAccessibleName("Nouvelle tâche à planifier")
        self.new_button.clicked.connect(self.new_entry_requested)

        self.sort_combo = QComboBox()
        for sort_key, text in SORT_LABELS.items():
            self.sort_combo.addItem(text, sort_key)
        self.sort_combo.setAccessibleName("Tri des cartes")
        self.sort_combo.activated.connect(self._on_sort_chosen)
        self.group_combo = QComboBox()
        for group_key, text in GROUP_LABELS.items():
            self.group_combo.addItem(text, group_key)
        self.group_combo.setAccessibleName("Regroupement des cartes")
        self.group_combo.activated.connect(self._on_group_chosen)

        self._chips_box = QWidget()
        self._chips_box.setStyleSheet(COMPACT_CHIPS_STYLE)
        self._chips = FlowLayout(self._chips_box, spacing=CHIP_SPACING)
        self.urgent_chip = self._chip("Urgent", "Seulement les tâches urgentes")
        self.mine_chip = self._chip("Mes dossiers", "Seulement les dossiers que je gère")
        self.urgent_chip.toggled.connect(self._on_filter_chip)
        self.mine_chip.toggled.connect(self._on_filter_chip)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Rechercher : dossier, client, texte…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setAcceptDrops(False)
        self.search_edit.setAccessibleName("Rechercher dans les cartes")
        self._search_action = self.search_edit.addAction(
            icon("search", "neutral-700"), QLineEdit.ActionPosition.LeadingPosition
        )
        self.search_edit.textChanged.connect(self._on_search)

        self.list_view = _CardList(self._model, self._state, self)
        self.list_view.card_activated.connect(self.card_activated)
        self.list_view.card_menu.connect(self._open_menu)
        self.list_view.card_hovered.connect(self._on_card_hovered)
        self.list_view.delete_pressed.connect(self._on_delete_pressed)
        self.list_view.header_toggled.connect(self._toggle_group)
        self.list_view.reorder_dropped.connect(self.reorder_requested)
        self.list_view.foreign_entered.connect(self._on_foreign_drag)
        self.list_view.foreign_dropped.connect(self.drop_task)
        self.list_view.drag_left.connect(self._on_drag_left)

        header = QVBoxLayout()
        header.setContentsMargins(14, 10, 14, 8)
        header.setSpacing(8)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(self.title_label, 1)
        title_row.addWidget(self.new_button)
        header.addLayout(title_row)
        sort_row = QHBoxLayout()
        sort_row.setSpacing(6)
        sort_row.addWidget(label("Tri", "field"))
        sort_row.addWidget(self.sort_combo, 1)
        sort_row.addSpacing(6)
        sort_row.addWidget(label("Grouper", "field"))
        sort_row.addWidget(self.group_combo, 1)
        header.addLayout(sort_row)
        header.addWidget(self._chips_box)
        header.addWidget(self.search_edit)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addLayout(header)
        list_box = QVBoxLayout()
        list_box.setContentsMargins(14, 2, 10, 0)
        list_box.addWidget(self.list_view, 1)
        root.addLayout(list_box, 1)

        self.overlay = DropOverlay(self)
        self.overlay.dropped.connect(self.drop_task)

        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(FLASH_TICK_MS)
        self._flash_timer.timeout.connect(self._tick_flash)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(REFRESH_MS)
        self._refresh_timer.timeout.connect(self._refresh_times)
        self._refresh_timer.start()
        theme_notifier().changed.connect(self._on_theme)
        self.set_my_initials("")
        self._rebuild_team_chips(())
        self._rebuild()

    # -- public API -----------------------------------------------------------

    def set_snapshot(
        self,
        snapshot: PlanningSnapshot,
        *,
        today: date | None = None,
        now: datetime | None = None,
    ) -> None:
        """Show the backlog of ``snapshot``; ``today``/``now`` fix the clock (tests)."""
        self._snapshot = snapshot
        if today is not None:
            self._today = today
        if now is not None:
            self._now = now
        teams = tuple(
            (team.key, team.name) for team in sorted(snapshot.meta.teams, key=lambda t: t.order)
        )
        if teams != self._team_keys:
            self._rebuild_team_chips(teams)
        self._rebuild()

    def set_title_visible(self, *, visible: bool) -> None:
        """Show « À PLANIFIER · N », or a plain summary where a tab already names the panel."""
        self._title_visible = visible
        font = self.title_label.font()
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, CAPS_SPACING if visible else 0)
        self.title_label.setFont(font)
        set_role(self.title_label, "caps" if visible else "small")
        self._rebuild()

    def set_my_initials(self, initials: str) -> None:
        """Set the initials of the person at this poste, for « Mes dossiers »."""
        self._my_initials = initials.strip()
        self.mine_chip.setEnabled(bool(self._my_initials))
        self.mine_chip.setToolTip(
            "Seulement les dossiers que je gère"
            if self._my_initials
            else "Indiquez vos initiales dans les réglages du planning"
        )
        if self._filters.mine:
            self._rebuild()

    def set_color_by(self, mode: ColorBy) -> None:
        self._state.color_by = mode
        self.list_view.viewport().update()

    def set_read_only(self, read_only: bool) -> None:  # noqa: FBT001
        """Read-only planning: no drag, no drop, no reordering, no new card."""
        self._read_only = read_only
        self._state.read_only = read_only
        self.new_button.setEnabled(not read_only)
        self.list_view.setDragEnabled(not read_only)
        self._update_reorder()
        self._update_empty_text()
        self.list_view.viewport().update()

    def mark_new(self, task_ids: Iterable[str]) -> None:
        """Flash the cards added on another poste and dot them until they are hovered."""
        now = self._clock.elapsed()
        for task_id in task_ids:
            self._state.new_ids.add(task_id)
            self._flash_starts[task_id] = now
            self._state.flashes[task_id] = 1.0
        if self._flash_starts and not self._flash_timer.isActive():
            self._flash_timer.start()
        self.list_view.viewport().update()

    def new_ids(self) -> frozenset[str]:
        return frozenset(self._state.new_ids)

    def accepts_task(self, task_id: str) -> bool:
        """Whether a task dragged here may be sent back (not read-only, not already here)."""
        return not self._read_only and task_id not in self._backlog_ids

    def sort(self) -> BacklogSort:
        return self._sort

    def set_sort(self, sort: BacklogSort) -> None:
        if sort not in SORT_LABELS or sort == self._sort:
            return
        self._sort = sort
        self._select_combo(self.sort_combo, sort)
        self._rebuild()
        self.filters_changed.emit()

    def group(self) -> BacklogGroup:
        return self._group

    def set_group(self, group: BacklogGroup) -> None:
        if group not in GROUP_LABELS or group == self._group:
            return
        self._group = group
        self._select_combo(self.group_combo, group)
        self._rebuild()
        self.filters_changed.emit()

    def filters(self) -> BacklogFilters:
        return self._filters

    def set_filters(self, filters: BacklogFilters) -> None:
        if filters == self._filters:
            return
        self._filters = filters
        self._sync_filter_widgets()
        self._rebuild()
        self.filters_changed.emit()

    def cards(self) -> list[CardData]:
        """Cards shown, in their display order (collapsed groups included)."""
        return list(self._cards)

    def count(self) -> int:
        """Cards in « À planifier », whatever the filters."""
        return max(self._total, 0)

    def current_card_id(self) -> str:
        return self.list_view.current_card_id()

    def card_rect(self, task_id: str) -> QRect:
        """Where the card of ``task_id`` sits in the list viewport (empty when not shown)."""
        row = self._model.row_of(task_id)
        if row < 0:
            return QRect()
        return self.list_view.visualRect(self._model.index(row, 0))

    def select_card(self, task_id: str) -> bool:
        row = self._model.row_of(task_id)
        if row < 0:
            return False
        index = self._model.index(row, 0)
        self.list_view.setCurrentIndex(index)
        self.list_view.scrollTo(index)
        return True

    def build_menu(self, task_id: str) -> QMenu:
        """Context menu of a card; each action emits ``card_action``."""
        menu = QMenu(self)
        card = next((card for card in self._cards if card.task_id == task_id), None)
        for key, text in CARD_ACTION_LABELS.items():
            if key == "delete":
                menu.addSeparator()
            # The hint follows a tab, which the menu shows right-aligned like a shortcut:
            # QKeySequence has no French key names without the Qt translations.
            hint = CARD_ACTION_HINTS.get(key)
            action = QAction(f"{text}\t{hint}" if hint else text, menu)
            action.setData(key)
            if key in {"duplicate", "delete"} and self._read_only:
                action.setEnabled(False)
            if key == "folder" and (card is None or not card.number):
                action.setEnabled(False)
            action.triggered.connect(
                lambda _checked=False, name=key: self.card_action.emit(task_id, name)
            )
            menu.addAction(action)
        return menu

    # -- building -------------------------------------------------------------

    def _rebuild(self) -> None:
        snapshot = self._snapshot
        current = self.list_view.current_card_id()
        current_group = self.list_view.current_group_key()
        scroll = self.list_view.verticalScrollBar().value()
        if snapshot is None:
            cards: list[CardData] = []
            total = 0
            type_order: list[str] = []
            self._backlog_ids = frozenset()
        else:
            backlog = snapshot.backlog()
            total = len(backlog)
            self._backlog_ids = frozenset(task.id for task in backlog)
            cards = build_cards(
                snapshot,
                today=self._today or date.today(),
                sort=self._sort,
                filters=self._filters,
                my_initials=self._my_initials,
                now=self._now,
            )
            type_order = [kind.key for kind in snapshot.meta.task_types]
        self._cards = cards
        rows: list[_Row] = []
        for group in group_cards(cards, self._group, type_order=type_order):
            if self._group != "none":
                collapsed = group.key in self._collapsed
                rows.append(
                    _Row(
                        group_key=group.key,
                        label=group.label,
                        count=len(group.cards),
                        collapsed=collapsed,
                    )
                )
                if collapsed:
                    continue
            rows.extend(_Row(card=card, group_key=group.key) for card in group.cards)
        self._state.hover_row = -1
        self._state.backlog_ids = self._backlog_ids
        self._state.new_ids &= set(self._backlog_ids)
        self._model.set_rows(rows)
        self._update_empty_text()
        row = self._model.row_of(current) if current else -1
        if row < 0 and current_group is not None:
            # A group folded or unfolded from the keyboard keeps its header current.
            row = self._model.header_row(current_group)
        if row >= 0:
            self.list_view.setCurrentIndex(self._model.index(row, 0))
        self.list_view.verticalScrollBar().setValue(scroll)
        self._update_reorder()
        self.title_label.setText(
            title_text(len(cards), total)
            if self._title_visible
            else summary_text(len(cards), total)
        )
        if total != self._total:
            self._total = total
            self.count_changed.emit(total)

    def _update_empty_text(self) -> None:
        """Say why the list is empty: filters, nothing yet, or no way to add a card."""
        if self._backlog_ids:
            self.list_view.empty_text = FILTERED_EMPTY_TEXT
        else:
            self.list_view.empty_text = READ_ONLY_EMPTY_TEXT if self._read_only else EMPTY_TEXT

    def _update_reorder(self) -> None:
        self._state.reorder = (
            self._sort == "manual" and self._group == "none" and not self._read_only
        )

    def _chip(self, text: str, tooltip: str) -> QPushButton:
        chip = button(text, "chip")
        chip.setCheckable(True)
        chip.setToolTip(tooltip)
        return chip

    def _rebuild_team_chips(self, teams: tuple[tuple[str, str], ...]) -> None:
        self._team_keys = teams
        for chip in self._team_chips.values():
            self._chips.removeWidget(chip)
            chip.deleteLater()
        self._chips.removeWidget(self.urgent_chip)
        self._chips.removeWidget(self.mine_chip)
        self._team_chips = {}
        for key, name in teams:
            chip = self._chip(name, f"Seulement les tâches de l'équipe {name}")
            chip.setChecked(key in self._filters.teams)
            chip.toggled.connect(self._on_filter_chip)
            self._team_chips[key] = chip
            self._chips.addWidget(chip)
        self._chips.addWidget(self.urgent_chip)
        self._chips.addWidget(self.mine_chip)
        self._chips_box.updateGeometry()

    def _sync_filter_widgets(self) -> None:
        for key, chip in self._team_chips.items():
            blocker = QSignalBlocker(chip)
            chip.setChecked(key in self._filters.teams)
            del blocker
        for chip, checked in (
            (self.urgent_chip, self._filters.urgent_only),
            (self.mine_chip, self._filters.mine),
        ):
            blocker = QSignalBlocker(chip)
            chip.setChecked(checked)
            del blocker
        if self.search_edit.text() != self._filters.search:
            blocker = QSignalBlocker(self.search_edit)
            self.search_edit.setText(self._filters.search)
            del blocker

    @staticmethod
    def _select_combo(combo: QComboBox, key: str) -> None:
        index = combo.findData(key)
        if index >= 0 and index != combo.currentIndex():
            blocker = QSignalBlocker(combo)
            combo.setCurrentIndex(index)
            del blocker

    # -- slots ---------------------------------------------------------------

    def _on_sort_chosen(self, index: int) -> None:
        key = self.sort_combo.itemData(index)
        if isinstance(key, str) and key in SORT_LABELS:
            self.set_sort(key)

    def _on_group_chosen(self, index: int) -> None:
        key = self.group_combo.itemData(index)
        if isinstance(key, str) and key in GROUP_LABELS:
            self.set_group(key)

    def _on_filter_chip(self) -> None:
        teams = frozenset(key for key, chip in self._team_chips.items() if chip.isChecked())
        filters = BacklogFilters(
            teams=teams,
            urgent_only=self.urgent_chip.isChecked(),
            mine=self.mine_chip.isChecked(),
            search=self._filters.search,
        )
        if filters != self._filters:
            self._filters = filters
            self._rebuild()
            self.filters_changed.emit()

    def _on_search(self, text: str) -> None:
        filters = BacklogFilters(
            teams=self._filters.teams,
            urgent_only=self._filters.urgent_only,
            mine=self._filters.mine,
            search=text,
        )
        if filters != self._filters:
            self._filters = filters
            self._rebuild()
            self.filters_changed.emit()

    def _toggle_group(self, key: str) -> None:
        if key in self._collapsed:
            self._collapsed.discard(key)
        else:
            self._collapsed.add(key)
        self._rebuild()

    def _open_menu(self, task_id: str, global_pos: QPoint) -> None:
        menu = self.build_menu(task_id)
        menu.exec(global_pos)
        menu.deleteLater()

    def _on_delete_pressed(self, task_id: str) -> None:
        self.card_action.emit(task_id, "delete")

    def _on_card_hovered(self, task_id: str) -> None:
        if task_id in self._state.new_ids:
            self._state.new_ids.discard(task_id)
            self.list_view.viewport().update()

    def _on_foreign_drag(self) -> None:
        if not self._read_only:
            self.show_drop_overlay()

    def _on_drag_left(self) -> None:
        # The drag may be moving onto the overlay, or leaving or cancelled before that.
        self.overlay.release_later()

    def drop_task(self, task_id: str) -> None:
        """Send back to « À planifier » a task dropped here (or on the rail of the panel)."""
        self.overlay.hide()
        if self._read_only or task_id in self._backlog_ids:
            return
        self.unschedule_requested.emit(task_id)

    def show_drop_overlay(self) -> None:
        self.overlay.setGeometry(self.rect())
        self.overlay.show()
        self.overlay.raise_()

    def _tick_flash(self) -> None:
        now = self._clock.elapsed()
        for task_id, start in list(self._flash_starts.items()):
            level = 1.0 - (now - start) / FLASH_MS
            if level <= 0:
                del self._flash_starts[task_id]
                self._state.flashes.pop(task_id, None)
            else:
                self._state.flashes[task_id] = level
        if not self._flash_starts:
            self._flash_timer.stop()
        self.list_view.viewport().update()

    def _refresh_times(self) -> None:
        if self._snapshot is not None and self.isVisible() and self._now is None:
            self._rebuild()

    def _on_theme(self) -> None:
        self._search_action.setIcon(icon("search", "neutral-700"))
        self.update()
        self.list_view.viewport().update()

    # -- events ----------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        payload = parse_task_mime(event.mimeData())
        if payload is None or not self.accepts_task(payload.task_id):
            event.ignore()
            return
        self.show_drop_overlay()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # noqa: N802
        self._on_drag_left()
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        payload = parse_task_mime(event.mimeData())
        self.overlay.hide()
        if payload is None or not self.accepts_task(payload.task_id):
            event.ignore()
            return
        event.acceptProposedAction()
        self.drop_task(payload.task_id)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.overlay.setGeometry(self.rect())

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), current_theme().color("bg"))
        painter.end()
