"""The « Planning » tab: toolbar, quick entry, grid, side panel, status bar and overlays.

The tab assembles the planning widgets and keeps what only concerns the display on this
poste: the view and the period shown, the density, the colours, the filter, the shortcuts
and the status bar. It edits nothing: ``PlanningController`` listens to the widgets'
intent signals (and to the tab's own) and runs the commands, then hands the tab a new
``GridModel`` with ``set_model``.

Shortcuts are scoped to the tab (``Qt.WidgetWithChildrenShortcut``) and use the portable
« Ctrl » text, which Qt reads as Cmd on macOS; the grid takes the focus when the tab is
shown with the focus elsewhere (a navigation button), so they work at once. Delete has no
shortcut of its own: the grid and the « À planifier » list handle it, each for what has
the focus.

The side panel sits beside the grid and sets its own width (open, folded into its rail,
resized with its grip). Below ``NARROW_PANEL_WIDTH`` it starts folded, without changing
the choice saved for the poste, and once opened it floats over the grid.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, timedelta
from typing import Final

from PySide6.QtCore import (
    QByteArray,
    QDate,
    QEvent,
    QLocale,
    QObject,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QHideEvent,
    QIcon,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPixmap,
    QResizeEvent,
    QShortcut,
    QShowEvent,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QCalendarWidget,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from projectflow.core.planning.layout import UNASSIGNED_ROW_ID, Density, row_kind
from projectflow.core.planning.quick_entry import EntryContext, fold_text
from projectflow.core.planning.timescale import (
    MONTH_NAMES,
    VIEW_KINDS,
    WEEKDAY_NAMES,
    WEEKDAYS,
    ViewKind,
    iso_week_label,
    shift_view,
    shown_day,
    view_range,
    week_start,
)
from projectflow.core.planning.walltime import WallTime
from projectflow.ui.planning.backlog import BacklogPanel, build_cards
from projectflow.ui.planning.colors import ColorBy
from projectflow.ui.planning.detail import DetailPanel
from projectflow.ui.planning.grid import PlanningGrid
from projectflow.ui.planning.grid_model import (
    MAX_LANE_HEIGHT,
    MAX_ZOOM,
    MIN_LANE_HEIGHT,
    MIN_ZOOM,
    BarData,
    GridModel,
    clamp_lane_height,
    clamp_zoom,
    span_label,
)
from projectflow.ui.planning.hovercard import HoverCard
from projectflow.ui.planning.inline_editor import InlineEditor
from projectflow.ui.planning.quick_entry_bar import QuickEntryBar
from projectflow.ui.planning.side_panel import RAIL_WIDTH, SidePanel
from projectflow.ui.planning.sync_badge import SyncBadge, SyncStatusLike
from projectflow.ui.planning.toast import Toast, ToastOverlay
from projectflow.ui.theme import HEADING_FAMILY, current_theme, icon, theme_notifier
from projectflow.ui.widgets.industry import (
    BlueprintFrame,
    ElidedLabel,
    StatusBanner,
    button,
    label,
    rule,
)

VIEW_LABELS: Final[Mapping[ViewKind, str]] = {"day": "Jour", "week": "Semaine", "month": "Mois"}
VIEW_TOOLTIPS: Final[Mapping[ViewKind, str]] = {
    "day": "Vue jour (Ctrl+1)",
    "week": "Vue semaine (Ctrl+2)",
    "month": "Vue mois (Ctrl+3)",
}
COLOR_LABELS: Final[Mapping[ColorBy, str]] = {"type": "Type", "dossier": "Dossier"}
DENSITY_LABELS: Final[Mapping[str, str]] = {
    "compact": "Compacte",
    "normal": "Normale",
    "large": "Large",
}
DENSITY_HEIGHTS: Final[Mapping[str, int]] = {
    "compact": int(Density.COMPACT),
    "normal": int(Density.NORMAL),
    "large": int(Density.LARGE),
}
ZOOM_STEP: Final = 1.25
"""Ctrl + one notch of the wheel: columns 25 % wider (or narrower)."""
ZOOM_SLIDER_SCALE: Final = 100
ROW_HEIGHT_STEP: Final = 4
"""Ctrl + one notch of the wheel on the names: rows 4 px taller (or lower)."""
ROW_HEIGHT_BUTTON_STEP: Final = 8
"""A click on « Lignes - / + »: from 22 px, the steps go through 38 (Normale) and 54 (Large)."""
SIZE_SLIDER_WIDTH: Final = 160
ZOOM_TOLERANCE: Final = 0.005
MONTH_ABBREVIATIONS: Final = (
    "janv.",
    "févr.",
    "mars",
    "avr.",
    "mai",
    "juin",
    "juil.",
    "août",
    "sept.",
    "oct.",
    "nov.",
    "déc.",
)
EN_DASH: Final = "–"  # noqa: RUF001
CARD_DIMMING: Final = callable(getattr(BacklogPanel, "set_dimmed", None))
"""Whether « À planifier » can dim the cards the filter does not match (its own search
stays the user's: the filter of the toolbar never writes into it)."""
FILTERED: Final = "les barres et les cartes" if CARD_DIMMING else "les barres"
FILTER_PLACEHOLDER: Final = "Filtrer…"
FILTER_TOOLTIP: Final = (
    f"Filtrer par dossier, client, titre ou initiales : atténue {FILTERED} "
    "qui ne correspondent pas (Ctrl+F)"
)
DIMMED_OPACITY: Final = 0.22
NARROW_PANEL_WIDTH: Final = 1200
"""Below this width the side panel starts folded into its rail (not saved as a choice)."""
PERIOD_FONT_PX: Final = 19
TOOLBAR_MARGINS: Final = (16, 8, 16, 6)
TOOLBAR_SPACING: Final = 8
SIDE_GUTTER: Final = 16
STATUS_HEIGHT: Final = 26
FILTER_WIDTH: Final = 200
FILTER_MIN_WIDTH: Final = 110
SQUARE_BUTTON: Final = 32
TOAST_MARGIN: Final = 16
UNDO_TOOLTIP: Final = "Annuler"
REDO_TOOLTIP: Final = "Rétablir"
NOTHING_TO_UNDO: Final = "Rien à annuler"
NOTHING_TO_REDO: Final = "Rien à rétablir"
READ_ONLY_PLACEHOLDER: Final = "Planning en lecture seule : la saisie est désactivée."
IDENTITY_MESSAGE: Final = (
    "Ce poste n'est pas identifié : indiquez vos initiales et le nom du poste pour que vos "
    "modifications du planning soient signées « initiales · poste »."
)
IDENTITY_ACTION: Final = "Identifier ce poste…"
READ_ONLY_TITLE: Final = "Planning en lecture seule"
RETRY_ACTION: Final = "Réessayer"
SAVE_MESSAGE: Final = "Le planning s'enregistre tout seul."
F1_HINT: Final = "F1 aide"
IDLE_HINTS: Final = (
    "Glisser dans le vide : plage puis taper",
    "Double-clic : nouvelle tâche",
    "Ctrl+K saisir",
)
SELECTION_HINTS: Final = (
    "Glisser déplacer",
    "Ctrl+glisser copier",
    "Maj+← → durée",
    "↑↓ réaffecter",
)
READ_ONLY_HINT: Final = "Lecture seule : consultation du planning"

SHORTCUT_SECTIONS: Final[tuple[tuple[str, tuple[tuple[str, str], ...]], ...]] = (
    (
        "Naviguer",
        (
            ("Ctrl+1 / 2 / 3", "Jour, Semaine, Mois"),
            ("Ctrl+T", "Aujourd'hui"),
            ("PgPréc / PgSuiv", "Période précédente, suivante"),
            ("Ctrl+F", f"Filtrer {FILTERED}"),
            ("Ctrl+B", "Replier ou déplier le panneau"),
            ("Ctrl+P", "Imprimer (A3)"),
        ),
    ),
    (
        "Créer",
        (
            ("Ctrl+K", "Saisie rapide"),
            ("Glisser dans le vide", "Plage, puis taper la tâche"),
            ("Double-clic sur une case", "Nouvelle tâche sur la case"),
            ("Lettre sur une case", "Saisie sur la case"),
            ("Maj+Entrée", "Créer et garder le dossier"),
            ("Tab (case)", "Créer et passer à la case libre"),
        ),
    ),
    (
        "Modifier",
        (
            ("Glisser / bord", "Déplacer / étirer"),
            ("Ctrl+glisser", "Copier"),
            ("← → / Maj+← →", "Décaler / durée"),
            ("Entrée", "Détail"),
            ("Suppr", "Retirer la personne / supprimer"),
            ("Maj+Suppr", "Supprimer pour tous"),
            ("Ctrl+Z / Ctrl+Y", "Annuler / rétablir"),
        ),
    ),
    (
        "Affecter",
        (
            ("Maj+glisser", "Ajouter la personne de la ligne"),
            ("↑ ↓", "Réaffecter"),
            ("Maj+↑ ↓", "Ajouter au-dessus / au-dessous"),
            ("Clic droit", "Détacher, retirer, statut, type"),
        ),
    ),
)


# Lucide icons (https://lucide.dev) the theme does not carry, drawn like its own.
TOOL_ICONS: Final[Mapping[str, str]] = {
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "redo": '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    "printer": '<path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 '
    '2h-2"/><path d="M6 9V3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v6"/>'
    '<rect x="6" y="14" width="12" height="8" rx="1"/>',
    "rows": '<path d="M3 6h18"/><path d="M3 12h18"/><path d="M3 18h18"/>',
    "minus": '<path d="M5 12h14"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
}
ICON_SIZE: Final = 16
ICON_RATIO: Final = 2.0


def tool_icon(name: str, role: str = "neutral-800", size: int = ICON_SIZE) -> QIcon:
    """Themed icon of the toolbar, with a dimmed variant for disabled buttons."""
    result = QIcon()
    theme = current_theme()
    for mode, color, opacity in (
        (QIcon.Mode.Normal, theme.color(role).name(), 1.0),
        (QIcon.Mode.Disabled, theme.color("text").name(), 0.3),
    ):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
            f'fill="none" stroke="{color}" stroke-opacity="{opacity}" stroke-width="1.6" '
            f'stroke-linecap="round" stroke-linejoin="round">{TOOL_ICONS[name]}</svg>'
        )
        renderer = QSvgRenderer(QByteArray(svg.encode()))
        image = QImage(
            round(size * ICON_RATIO), round(size * ICON_RATIO), QImage.Format.Format_ARGB32
        )
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter, QRectF(0, 0, image.width(), image.height()))
        painter.end()
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(ICON_RATIO)
        result.addPixmap(pixmap, mode)
    return result


def native_keys(text: str) -> str:
    """Key names as the platform shows them: Cmd for Ctrl on macOS."""
    return text.replace("Ctrl", "Cmd") if sys.platform == "darwin" else text


def _short_day(day: date) -> str:
    return f"{day.day} {MONTH_ABBREVIATIONS[day.month - 1]}"


def period_text(view: ViewKind, anchor: date, *, short: bool = False) -> str:
    """« Semaine 44 · 26 - 30 oct. 2026 » (en dash), « Jeudi 22 octobre 2026 », « Octobre 2026 ».

    ``short`` gives « S44 · 26 - 30 oct. », « Jeu 22 oct. 2026 », « Oct. 2026 » for a
    narrow toolbar.
    """
    if view == "day":
        name = WEEKDAY_NAMES[anchor.weekday()].capitalize()
        if short:
            return f"{name[:3]} {_short_day(anchor)} {anchor.year}"
        return f"{name} {anchor.day} {MONTH_NAMES[anchor.month - 1]} {anchor.year}"
    if view == "month":
        if short:
            return f"{MONTH_ABBREVIATIONS[anchor.month - 1].capitalize()} {anchor.year}"
        return f"{MONTH_NAMES[anchor.month - 1].capitalize()} {anchor.year}"
    first = week_start(anchor)
    last = first + timedelta(days=max(WEEKDAYS))
    if short:
        start = str(first.day) if first.month == last.month else _short_day(first)
        return f"{iso_week_label(first, short=True)} · {start} {EN_DASH} {_short_day(last)}"
    if first.year != last.year:
        span = f"{_short_day(first)} {first.year} {EN_DASH} {_short_day(last)} {last.year}"
    elif first.month != last.month:
        span = f"{_short_day(first)} {EN_DASH} {_short_day(last)} {last.year}"
    else:
        span = f"{first.day} {EN_DASH} {_short_day(last)} {last.year}"
    return f"{iso_week_label(first)} · {span}"


def bar_search_text(bar: BarData, model: GridModel | None = None) -> str:
    """Folded text a bar matches against the filter: dossier, client, title, people."""
    parts = [bar.project_number, bar.number, bar.client, bar.title, bar.type_label, *bar.people]
    task = model.task(bar.task_id) if model is not None else None
    if task is not None and model is not None:
        parts.append(task.project_cache.designation)
        for resource_id in task.assignments:
            resource = model.snapshot.resources.get(resource_id)
            if resource is not None:
                parts.append(resource.name)
    return fold_text(" ".join(part for part in parts if part))


def matches_filter(bar: BarData, query: str, model: GridModel | None = None) -> bool:
    """Whether every word of ``query`` (accents and case ignored) is found in the bar."""
    words = fold_text(query).split()
    if not words:
        return True
    text = bar_search_text(bar, model)
    return all(word in text for word in words)


def selection_summary(model: GridModel | None, bars: Sequence[tuple[str, str]]) -> str:
    """« Sélection : 118 Pose garde-corps · KDU, LBI · lun 26.10 06:30 → … · 2 j »."""
    if model is None or not bars:
        return ""
    tasks = {task_id for task_id, _row in bars}
    if len(bars) > 1:
        count = f"{len(bars)} barres"
        return f"Sélection : {count}" + (f" ({len(tasks)} tâches)" if len(tasks) > 1 else "")
    bar = model.bar(*bars[0])
    if bar is None:
        return ""
    head = " ".join(part for part in (bar.number, bar.title) if part)
    people = ", ".join(bar.people) or "Non affecté"
    calendar = model.task_calendar(bar.task_id)
    return f"Sélection : {head} · {people} · {span_label(calendar, bar.start, bar.end)}"


# ---------------------------------------------------------------- widgets ----


class _NoticeBanner(StatusBanner):
    """A ``StatusBanner`` for a notice that is not an error: accent ground, alert icon."""

    def show_notice(self, message: str) -> None:
        self._show("notice", message)
        self.icon_label.set_icon("alert", "accent-700")
        self.set_fill("accent-100")


CALENDAR_MIN_SIZE: Final = QSize(400, 280)
MAX_ISO_WEEKS: Final = 53
LAST_WEEKS: Final = 52
MIN_YEAR: Final = 2000
MAX_YEAR: Final = 2100
JANUARY: Final = 1
DECEMBER: Final = 12


class CalendarPopup(QFrame):
    """Month calendar with ISO week numbers, opened from the period label.

    A day or a week number can be clicked; « Semaine » jumps to any ISO week.
    """

    day_chosen = Signal(object)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setFrameShape(QFrame.Shape.Box)
        self.calendar = QCalendarWidget(self)
        self.calendar.setLocale(QLocale(QLocale.Language.French, QLocale.Country.Switzerland))
        self.calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.ISOWeekNumbers)
        self.calendar.setGridVisible(True)
        # The app style pads table cells by 8 px: the narrow day cells then read « … ».
        self.calendar.setStyleSheet(
            "QCalendarWidget QTableView::item { padding: 0; border: 0; }"
            "QCalendarWidget QTableView { font-size: 13px; }",
        )
        self.calendar.setMinimumSize(CALENDAR_MIN_SIZE)
        self.calendar.clicked.connect(self._on_clicked)
        self.calendar.activated.connect(self._on_clicked)
        self._view = self.calendar.findChild(QTableView)
        if self._view is not None:
            self._view.viewport().installEventFilter(self)
        week_row = QHBoxLayout()
        week_row.setSpacing(6)
        week_row.addWidget(QLabel("Semaine"))
        self.week_spin = QSpinBox()
        self.week_spin.setRange(1, MAX_ISO_WEEKS)
        self.year_spin = QSpinBox()
        self.year_spin.setRange(MIN_YEAR, MAX_YEAR)
        self.week_spin.setToolTip("Numéro de semaine ISO")
        go = button("Afficher", size="sm")
        go.clicked.connect(self._go_to_week)
        for spin in (self.week_spin, self.year_spin):
            spin.lineEdit().returnPressed.connect(self._go_to_week)
        week_row.addWidget(self.week_spin)
        week_row.addWidget(self.year_spin)
        week_row.addStretch(1)
        week_row.addWidget(go)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(self.calendar)
        layout.addLayout(week_row)

    def open_at(self, global_pos: QPoint, day: date) -> None:
        self.calendar.setSelectedDate(QDate(day.year, day.month, day.day))
        year, week, _weekday = day.isocalendar()
        self.year_spin.setValue(year)
        self.week_spin.setValue(week)
        self.adjustSize()
        self.move(global_pos)
        self.show()
        self.calendar.setFocus(Qt.FocusReason.PopupFocusReason)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        view = self._view
        if (
            view is not None
            and watched is view.viewport()
            and event.type() == QEvent.Type.MouseButtonRelease
            and isinstance(event, QMouseEvent)
        ):
            index = view.indexAt(event.position().toPoint())
            if index.isValid() and index.column() == 0 and index.row() > 0:
                week = _week_number(index.data())
                if week is not None:
                    self._choose_week(self._year_of_week(week), week)
                    return True
        return super().eventFilter(watched, event)

    def _year_of_week(self, week: int) -> int:
        """Year of a week number clicked in the shown month (week 1 in December...)."""
        year, month = self.calendar.yearShown(), self.calendar.monthShown()
        if week == 1 and month == DECEMBER:
            return year + 1
        if week >= LAST_WEEKS and month == JANUARY:
            return year - 1
        return year

    def _go_to_week(self) -> None:
        self._choose_week(self.year_spin.value(), self.week_spin.value())

    def _choose_week(self, year: int, week: int) -> None:
        try:
            monday = date.fromisocalendar(year, week, 1)
        except ValueError:
            # Week 53 does not exist every year: the last week of that year instead.
            monday = date.fromisocalendar(year, date(year, 12, 28).isocalendar()[1], 1)
        self.hide()
        self.day_chosen.emit(monday)

    def _on_clicked(self, value: QDate) -> None:
        self.hide()
        self.day_chosen.emit(date(value.year(), value.month(), value.day()))


def _week_number(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except ValueError:
        return None


class ShortcutSheet(BlueprintFrame):
    """F1 overlay: the shortcuts of the planning in four columns (Naviguer, Créer…)."""

    closed = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, padding=(24, 18, 24, 18), fill="bg", spacing=14)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        head = QHBoxLayout()
        head.addWidget(label("Raccourcis du planning", "h4"), 1)
        self.close_button = button("Fermer", size="sm")
        self.close_button.setToolTip("Fermer l'aide (Échap ou F1)")
        self.close_button.clicked.connect(self.close_sheet)
        head.addWidget(self.close_button)
        self.box.addLayout(head)
        columns = QGridLayout()
        columns.setHorizontalSpacing(28)
        columns.setVerticalSpacing(4)
        for column, (title, rows) in enumerate(SHORTCUT_SECTIONS):
            columns.addWidget(label(title, "caps"), 0, column * 2, 1, 2)
            for line, (keys, text) in enumerate(rows, start=1):
                key_label = label(native_keys(keys), "field")
                key_label.setStyleSheet(f'QLabel {{ font-family: "{HEADING_FAMILY}"; }}')
                columns.addWidget(key_label, line, column * 2)
                columns.addWidget(label(text, "small"), line, column * 2 + 1)
        self.box.addLayout(columns)
        self.box.addWidget(
            label(
                "Les raccourcis agissent dans l'onglet Planning seulement. Maj veut toujours "
                "dire « ajouter », Ctrl « copier ».",
                "small",
                wrap=True,
            )
        )
        self.hide()

    def texts(self) -> list[str]:
        return [child.text() for child in self.findChildren(QLabel) if child.text()]

    def open_sheet(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.adjustSize()
            width = min(self.sizeHint().width(), parent.width() - 2 * SIDE_GUTTER)
            self.resize(max(width, 200), self.sizeHint().height())
            self.move(
                max(0, (parent.width() - self.width()) // 2),
                max(0, (parent.height() - self.height()) // 3),
            )
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def close_sheet(self) -> None:
        if self.isVisible():
            self.hide()
            self.closed.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in {Qt.Key.Key_Escape, Qt.Key.Key_F1}:
            self.close_sheet()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.close_sheet()
        event.accept()


def _segmented(
    options: Iterable[tuple[str, str, str]], current: str
) -> tuple[QWidget, QButtonGroup, dict[str, QPushButton]]:
    """Row of « seg » buttons, one checked: ``options`` are ``(key, text, tooltip)``."""
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    group = QButtonGroup(box)
    group.setExclusive(True)
    buttons: dict[str, QPushButton] = {}
    for index, (key, text, tooltip) in enumerate(options):
        choice = button(text, "seg", size="sm")
        choice.setCheckable(True)
        choice.setChecked(key == current)
        choice.setToolTip(tooltip)
        choice.setProperty("key", key)
        group.addButton(choice, index)
        layout.addWidget(choice)
        buttons[key] = choice
    return box, group, buttons


class PanelLayout(QLayout):
    """The grid, then the side panel at the width the panel sets itself.

    With ``floating`` (a narrow tab), the open panel lies over the right of the grid,
    which keeps the width it has beside the folded rail instead of being pushed.
    """

    def __init__(self, parent: QWidget, grid: QWidget, side: SidePanel) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._side = side
        self._floating = False
        self.setContentsMargins(0, 0, 0, 0)
        self.addWidget(grid)
        self.addWidget(side)

    @property
    def floating(self) -> bool:
        return self._floating

    def set_floating(self, *, floating: bool) -> None:
        if floating != self._floating:
            self._floating = floating
            self.invalidate()

    def is_floating_open(self) -> bool:
        """Whether the open panel lies over the grid right now."""
        return self._floating and not self._side.is_collapsed()

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation.Horizontal | Qt.Orientation.Vertical

    def sizeHint(self) -> QSize:  # noqa: N802
        return self._total(lambda item: item.sizeHint())

    def minimumSize(self) -> QSize:  # noqa: N802
        return self._total(lambda item: item.minimumSize())

    def _total(self, size_of: Callable[[QLayoutItem], QSize]) -> QSize:
        width = height = 0
        for item in self._items:
            size = size_of(item)
            width += size.width()
            height = max(height, size.height())
        return QSize(width, height)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        if len(self._items) != len(("grid", "side")):
            return
        grid, side = self._items
        panel = self._side.minimumWidth()  # the panel's fixed width, folded or open
        reserved = RAIL_WIDTH if self.is_floating_open() else panel
        grid.setGeometry(QRect(rect.x(), rect.y(), max(rect.width() - reserved, 0), rect.height()))
        side.setGeometry(QRect(rect.x() + rect.width() - panel, rect.y(), panel, rect.height()))
        if self.is_floating_open():
            self._side.raise_()


# ------------------------------------------------------------------- tab ----


class PlanningTab(QWidget):
    """The fourth tab of the main window; ``PlanningController`` drives it."""

    view_changed = Signal()
    """The view, the period, the density or the colours changed: build a new model."""
    filter_changed = Signal(str)
    undo_requested = Signal()
    redo_requested = Signal()
    print_requested = Signal()
    pdf_requested = Signal()
    team_requested = Signal()
    settings_requested = Signal()
    """« Identifier ce poste… »: open the Planning section of the settings."""
    retry_requested = Signal()
    save_requested = Signal()
    cell_typed = Signal(str)
    """A character typed on the grid: open the cell editor at ``cell_cursor()``."""
    visibility_changed = Signal()
    """Shown, hidden, or its window activated or deactivated."""
    density_chosen = Signal(str)
    display_size_changed = Signal()
    """The row height or the column width of a view changed: keep it for this poste."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("planningTab")
        self._today = date.today()
        self._view: ViewKind = "week"
        self._anchor = self._today
        self._density = "normal"
        self._row_heights: dict[ViewKind, int] = {}
        self._zooms: dict[ViewKind, float] = {}
        self._color_by: ColorBy = "type"
        self._filter = ""
        self._read_only = False
        self._cell: tuple[str, WallTime] | None = None
        self._fitting = False
        self._adapting = False
        self._narrow = False
        self._auto_folded = False
        self.shortcuts: dict[str, list[QShortcut]] = {}

        self.grid = PlanningGrid()
        self.side = SidePanel()
        self.quick = QuickEntryBar()
        self._quick_placeholder = self.quick.line_edit.placeholderText()
        self._build_toolbar()
        self._build_banners()
        self._build_status_bar()

        # Beside the grid, at the width it sets itself (no splitter: it would keep the
        # old width of a panel folded or narrowed, leaving a blank strip).
        self.content = QWidget()
        self.content_layout = PanelLayout(self.content, self.grid, self.side)

        quick_row = QHBoxLayout()
        quick_row.setContentsMargins(SIDE_GUTTER, 0, SIDE_GUTTER, 8)
        quick_row.addWidget(self.quick)
        banners = QVBoxLayout()
        banners.setContentsMargins(SIDE_GUTTER - 6, 0, SIDE_GUTTER - 6, 0)
        banners.setSpacing(4)
        banners.addWidget(self.identity_banner)
        banners.addWidget(self.read_only_banner)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.toolbar)
        root.addLayout(quick_row)
        root.addLayout(banners)
        root.addWidget(rule())
        root.addWidget(self.content, 1)
        root.addWidget(rule())
        root.addWidget(self.status_bar)

        self.toasts = ToastOverlay(self, margin_right=TOAST_MARGIN, margin_bottom=STATUS_HEIGHT)
        self.hover = HoverCard(self)
        self.editor = InlineEditor(self)
        self.calendar_popup = CalendarPopup(self)
        self.calendar_popup.day_chosen.connect(self.set_anchor)
        self.calendar_popup.hide()
        self.sheet = ShortcutSheet(self)
        self.sheet.closed.connect(self._focus_grid)

        self._build_shortcuts()
        self._wire()
        self._sync_toolbar()
        self._update_status()

    # -- construction -----------------------------------------------------

    @property
    def backlog(self) -> BacklogPanel:
        return self.side.backlog

    @property
    def detail(self) -> DetailPanel:
        return self.side.detail

    def _build_toolbar(self) -> None:
        self.toolbar = QWidget()
        self.toolbar.setObjectName("planningToolbar")
        # Never wider than the tab: the optional controls go first (``_fit_toolbar``).
        self.toolbar.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self.toolbar)
        layout.setContentsMargins(*TOOLBAR_MARGINS)
        layout.setSpacing(TOOLBAR_SPACING)
        self._toolbar_layout = layout

        self.prev_button = button("", size="sm")
        self.prev_button.setToolTip("Période précédente (PgPréc)")
        self.prev_button.setAccessibleName("Période précédente")
        self.prev_button.setFixedWidth(SQUARE_BUTTON)
        self.today_button = button("Aujourd'hui", size="sm")
        self.today_button.setToolTip(native_keys("Revenir à aujourd'hui (Ctrl+T)"))
        self.next_button = button("", size="sm")
        self.next_button.setToolTip("Période suivante (PgSuiv)")
        self.next_button.setAccessibleName("Période suivante")
        self.next_button.setFixedWidth(SQUARE_BUTTON)

        self.period_button = QPushButton()
        self.period_button.setObjectName("planningPeriod")
        self.period_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.period_button.setToolTip("Choisir une date (calendrier avec numéros de semaine)")
        self.period_button.setAccessibleName("Période affichée")
        self.period_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.period_button.setStyleSheet(
            f'QPushButton#planningPeriod {{ font-family: "{HEADING_FAMILY}"; '
            f"font-size: {PERIOD_FONT_PX}px; font-weight: 600; border: 0; "
            "background: transparent; padding: 0 6px; text-align: left; min-height: 0; }"
        )

        self.view_box, self.view_group, self.view_buttons = _segmented(
            ((key, VIEW_LABELS[key], native_keys(VIEW_TOOLTIPS[key])) for key in VIEW_KINDS),
            self._view,
        )
        self.color_box = QWidget()
        color_layout = QHBoxLayout(self.color_box)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.setSpacing(6)
        color_layout.addWidget(label("Couleur", "caps"))
        colors, self.color_group, self.color_buttons = _segmented(
            (
                ("type", COLOR_LABELS["type"], "Colorer les barres par type de tâche"),
                ("dossier", COLOR_LABELS["dossier"], "Colorer les barres par dossier"),
            ),
            self._color_by,
        )
        color_layout.addWidget(colors)

        self.lines_box = QWidget()
        lines_layout = QHBoxLayout(self.lines_box)
        lines_layout.setContentsMargins(0, 0, 0, 0)
        lines_layout.setSpacing(4)
        lines_layout.addWidget(label("Lignes", "caps"))
        self.lower_rows_button = button("", size="sm")
        self.lower_rows_button.setAccessibleName("Lignes moins hautes")
        self.lower_rows_button.setFixedWidth(SQUARE_BUTTON)
        self.taller_rows_button = button("", size="sm")
        self.taller_rows_button.setAccessibleName("Lignes plus hautes")
        self.taller_rows_button.setFixedWidth(SQUARE_BUTTON)
        lines_layout.addWidget(self.lower_rows_button)
        lines_layout.addWidget(self.taller_rows_button)

        self.display_button = QToolButton()
        self.display_button.setProperty("variant", "icon")
        self.display_button.setToolTip("Densité des lignes et affichage")
        self.display_button.setAccessibleName("Densité et affichage")
        self.display_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.display_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.display_button.setFixedWidth(SQUARE_BUTTON + 4)
        self.display_menu = QMenu(self.display_button)
        self.density_actions: dict[str, QAction] = {}
        density_group = QActionGroup(self.display_menu)
        self.display_menu.addSection("Densité")
        for key, text in DENSITY_LABELS.items():
            action = self.display_menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(key == self._density)
            action.setData(key)
            density_group.addAction(action)
            self.density_actions[key] = action
        density_group.triggered.connect(self._on_density_action)
        self.display_menu.addSection("Taille")
        self.size_action = QWidgetAction(self.display_menu)
        self.size_action.setDefaultWidget(self._build_size_controls())
        self.display_menu.addAction(self.size_action)
        self.display_menu.addSection("Couleur des barres")
        self.color_actions: dict[str, QAction] = {}
        color_group = QActionGroup(self.display_menu)
        for key, text in COLOR_LABELS.items():
            action = self.display_menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(key == self._color_by)
            action.setData(key)
            color_group.addAction(action)
            self.color_actions[key] = action
        color_group.triggered.connect(self._on_color_action)
        self.display_menu.addSeparator()
        self.menu_print_action = self.display_menu.addAction(native_keys("Imprimer…\tCtrl+P"))
        self.menu_pdf_action = self.display_menu.addAction("Exporter en PDF (A3)…")
        self.menu_team_action = self.display_menu.addAction("Équipe…")
        self.menu_help_action = self.display_menu.addAction("Aide-mémoire des raccourcis\tF1")
        self.display_button.setMenu(self.display_menu)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(FILTER_PLACEHOLDER)
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setAccessibleName("Filtrer le planning")
        self.filter_edit.setToolTip(native_keys(FILTER_TOOLTIP))
        self.filter_edit.addAction(
            icon("search", "neutral-700"), QLineEdit.ActionPosition.LeadingPosition
        )
        self.filter_edit.setMinimumWidth(FILTER_MIN_WIDTH)
        self.filter_edit.setMaximumWidth(FILTER_WIDTH + 80)
        self.filter_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.undo_button = button("", size="sm")
        self.undo_button.setAccessibleName(UNDO_TOOLTIP)
        self.undo_button.setFixedWidth(SQUARE_BUTTON)
        self.redo_button = button("", size="sm")
        self.redo_button.setAccessibleName(REDO_TOOLTIP)
        self.redo_button.setFixedWidth(SQUARE_BUTTON)

        self.print_button = QToolButton()
        self.print_button.setProperty("variant", "icon")
        self.print_button.setAccessibleName("Imprimer")
        self.print_button.setToolTip(native_keys("Imprimer le planning affiché en A3 (Ctrl+P)"))
        self.print_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.print_button.setCursor(Qt.CursorShape.PointingHandCursor)
        print_menu = QMenu(self.print_button)
        self.print_action = print_menu.addAction("Imprimer…")
        self.pdf_action = print_menu.addAction("Exporter en PDF (A3)…")
        self.print_button.setMenu(print_menu)

        self.team_button = button("Équipe…", size="sm")
        self.team_button.setToolTip("Personnes, sous-traitants, équipes et calendrier partagés")

        self.badge = SyncBadge(local=True)

        for widget in (self.prev_button, self.today_button, self.next_button):
            layout.addWidget(widget)
        layout.addWidget(self.period_button)
        layout.addWidget(self.view_box)
        layout.addStretch(1)
        layout.addWidget(self.lines_box)
        layout.addWidget(self.color_box)
        layout.addWidget(self.display_button)
        layout.addWidget(self.filter_edit, 4)
        layout.addWidget(self.undo_button)
        layout.addWidget(self.redo_button)
        layout.addWidget(self.print_button)
        layout.addWidget(self.team_button)
        layout.addWidget(self.badge)
        # Hidden first, then the next ones, when the tab is too narrow: each is also
        # reachable from the display menu.
        self._optional = (self.color_box, self.print_button, self.team_button, self.lines_box)
        self._icon_buttons: tuple[tuple[QAbstractButton, str], ...] = (
            (self.prev_button, "chevron-left"),
            (self.next_button, "chevron-right"),
            (self.lower_rows_button, "minus"),
            (self.taller_rows_button, "plus"),
            (self.undo_button, "undo"),
            (self.redo_button, "redo"),
            (self.print_button, "printer"),
            (self.display_button, "rows"),
        )
        self.toolbar.setStyleSheet(
            '#planningToolbar QPushButton[variant="seg"] { padding: 4px 11px; }'
        )
        self._refresh_icons()

    def _build_banners(self) -> None:
        self.identity_banner = _NoticeBanner()
        self.identity_button = self.identity_banner.add_action(IDENTITY_ACTION, ("notice",))
        self.read_only_banner = StatusBanner()
        self.retry_button = self.read_only_banner.add_action(RETRY_ACTION, ("error",))

    def _build_status_bar(self) -> None:
        self.status_bar = QWidget()
        self.status_bar.setFixedHeight(STATUS_HEIGHT)
        layout = QHBoxLayout(self.status_bar)
        layout.setContentsMargins(SIDE_GUTTER, 0, SIDE_GUTTER, 0)
        layout.setSpacing(16)
        self.selection_label = ElidedLabel("", Qt.TextElideMode.ElideRight)
        self.selection_label.setProperty("role", "small")
        self.hints_label = ElidedLabel("", Qt.TextElideMode.ElideLeft)
        self.hints_label.setProperty("role", "small")
        self.hints_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.identity_label = label("", "small")
        self.identity_label.setToolTip("Les modifications de ce poste sont signées ainsi")
        self.identity_label.hide()
        layout.addWidget(self.selection_label, 1)
        layout.addWidget(self.hints_label, 1)
        layout.addWidget(self.identity_label)

    def _build_shortcuts(self) -> None:
        bindings: tuple[tuple[str, tuple[str, ...], Callable[[], object]], ...] = (
            ("quick_entry", ("Ctrl+K",), self.focus_quick_entry),
            ("undo", ("Ctrl+Z",), self.undo_requested.emit),
            ("redo", ("Ctrl+Y", "Ctrl+Shift+Z"), self.redo_requested.emit),
            ("day", ("Ctrl+1",), lambda: self.set_view("day")),
            ("week", ("Ctrl+2",), lambda: self.set_view("week")),
            ("month", ("Ctrl+3",), lambda: self.set_view("month")),
            ("today", ("Ctrl+T",), self.go_today),
            ("previous", ("PgUp",), lambda: self.step(-1)),
            ("next", ("PgDown",), lambda: self.step(1)),
            ("panel", ("Ctrl+B",), self.side.toggle),
            ("print", ("Ctrl+P",), self.print_requested.emit),
            ("filter", ("Ctrl+F",), self.focus_filter),
            ("help", ("F1",), self.toggle_shortcuts),
            ("save", ("Ctrl+S",), self.save_requested.emit),
        )
        for name, sequences, slot in bindings:
            created: list[QShortcut] = []
            for sequence in sequences:
                shortcut = QShortcut(QKeySequence(sequence), self)
                shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                shortcut.activated.connect(slot)
                created.append(shortcut)
            self.shortcuts[name] = created

    def _wire(self) -> None:
        self.prev_button.clicked.connect(lambda: self.step(-1))
        self.next_button.clicked.connect(lambda: self.step(1))
        self.today_button.clicked.connect(self.go_today)
        self.period_button.clicked.connect(self.open_calendar)
        self.lower_rows_button.clicked.connect(lambda: self.step_row_height(-1))
        self.taller_rows_button.clicked.connect(lambda: self.step_row_height(1))
        self.view_group.idClicked.connect(self._on_view_clicked)
        self.color_group.idClicked.connect(self._on_color_clicked)
        self.filter_edit.textChanged.connect(self.set_filter)
        self.undo_button.clicked.connect(self.undo_requested)
        self.redo_button.clicked.connect(self.redo_requested)
        self.print_button.clicked.connect(self.print_requested)
        self.print_action.triggered.connect(self.print_requested)
        self.pdf_action.triggered.connect(self.pdf_requested)
        self.menu_print_action.triggered.connect(self.print_requested)
        self.menu_pdf_action.triggered.connect(self.pdf_requested)
        self.menu_team_action.triggered.connect(self.team_requested)
        self.menu_help_action.triggered.connect(self.show_shortcuts)
        self.team_button.clicked.connect(self.team_requested)
        self.identity_button.clicked.connect(self.settings_requested)
        self.retry_button.clicked.connect(self.retry_requested)
        self.badge.clicked.connect(self._on_badge_clicked)
        self.grid.selection_changed.connect(self._update_status)
        self.grid.day_clicked.connect(lambda day: self.set_view("day", anchor=day))
        self.grid.week_clicked.connect(lambda day: self.set_view("week", anchor=day))
        self.grid.zoom_requested.connect(self._on_zoom_wheel)
        self.grid.row_height_requested.connect(self._on_row_height_wheel)
        self.grid.period_step_requested.connect(self._step_from_edge)
        self.side.width_changed.connect(self._place_toasts)
        self.side.collapsed_changed.connect(self._on_panel_collapsed)
        self.backlog.set_color_by(self._color_by)
        self.grid.view.installEventFilter(self)
        self.grid.view.viewport().installEventFilter(self)
        theme_notifier().changed.connect(self._refresh_icons)

    # -- view state -------------------------------------------------------

    @property
    def view(self) -> ViewKind:
        return self._view

    @property
    def anchor(self) -> date:
        return self._anchor

    @property
    def today(self) -> date:
        return self._today

    @property
    def density(self) -> str:
        return self._density

    @property
    def row_height(self) -> int:
        """Height of a lane in the current view: chosen, else from the density."""
        chosen = self._row_heights.get(self._view)
        if chosen:
            return clamp_lane_height(chosen)
        return DENSITY_HEIGHTS.get(self._density, int(Density.NORMAL))

    @property
    def zoom(self) -> float:
        """Width of the columns of the current view against the width that fits (1)."""
        return clamp_zoom(self._zooms.get(self._view, MIN_ZOOM))

    def row_heights(self) -> dict[str, int]:
        return {view: clamp_lane_height(height) for view, height in self._row_heights.items()}

    def zooms(self) -> dict[str, float]:
        return {view: clamp_zoom(zoom) for view, zoom in self._zooms.items()}

    def set_row_height(self, height: int) -> None:
        """Rows of the current view ``height`` pixels a lane (kept per view)."""
        height = clamp_lane_height(height)
        if height == self.row_height:
            return
        self._row_heights[self._view] = height
        self._sync_size_controls()
        self.display_size_changed.emit()
        self.view_changed.emit()

    def step_row_height(self, steps: int) -> None:
        """Taller (``steps`` > 0) or lower rows in the current view, a button step each."""
        self.set_row_height(self.row_height + ROW_HEIGHT_BUTTON_STEP * steps)

    def set_zoom(self, zoom: float, *, viewport_x: float | None = None) -> None:
        """Columns of the current view ``zoom`` times their fitted width.

        The instant under ``viewport_x`` (the middle by default) stays where it is.
        """
        zoom = round(clamp_zoom(zoom), 2)
        if abs(zoom - self.zoom) < ZOOM_TOLERANCE:
            return
        self.grid.keep_point(
            viewport_x if viewport_x is not None else self.grid.viewport_width() / 2
        )
        if zoom <= MIN_ZOOM:
            self._zooms.pop(self._view, None)
        else:
            self._zooms[self._view] = zoom
        self._sync_size_controls()
        self.display_size_changed.emit()
        self.view_changed.emit()

    def _build_size_controls(self) -> QWidget:
        box = QWidget()
        layout = QGridLayout(box)
        layout.setContentsMargins(12, 4, 12, 8)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(6)
        self.row_height_slider = QSlider(Qt.Orientation.Horizontal)
        self.row_height_slider.setRange(MIN_LANE_HEIGHT, MAX_LANE_HEIGHT)
        self.row_height_slider.setSingleStep(2)
        self.row_height_slider.setPageStep(8)
        self.row_height_slider.setAccessibleName("Hauteur des lignes")
        self.row_height_slider.setToolTip(
            native_keys("Hauteur des lignes de cette vue (aussi Ctrl + molette sur les noms)")
        )
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(
            round(MIN_ZOOM * ZOOM_SLIDER_SCALE), round(MAX_ZOOM * ZOOM_SLIDER_SCALE)
        )
        self.zoom_slider.setSingleStep(5)
        self.zoom_slider.setPageStep(25)
        self.zoom_slider.setAccessibleName("Largeur des colonnes")
        self.zoom_slider.setToolTip(
            native_keys(
                "Largeur des colonnes de cette vue (aussi Ctrl + molette sur le planning) ; "
                "au-delà de 100 %, le planning défile de côté (Maj + molette)"
            )
        )
        for slider in (self.row_height_slider, self.zoom_slider):
            slider.setMinimumWidth(SIZE_SLIDER_WIDTH)
        self.row_height_value = QLabel()
        self.zoom_value = QLabel()
        self.fit_button = QPushButton("Ajuster")
        self.fit_button.setToolTip("Toutes les colonnes de la période à l'écran (100 %)")
        self.fit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(QLabel("Lignes"), 0, 0)
        layout.addWidget(self.row_height_slider, 0, 1)
        layout.addWidget(self.row_height_value, 0, 2)
        layout.addWidget(QLabel("Colonnes"), 1, 0)
        layout.addWidget(self.zoom_slider, 1, 1)
        layout.addWidget(self.zoom_value, 1, 2)
        layout.addWidget(self.fit_button, 1, 3)
        self.row_height_slider.valueChanged.connect(self.set_row_height)
        self.zoom_slider.valueChanged.connect(self._on_zoom_slider)
        self.fit_button.clicked.connect(self.fit_columns)
        return box

    def fit_columns(self) -> None:
        """Back to every column of the period on screen."""
        self.set_zoom(MIN_ZOOM)

    def _on_zoom_slider(self, value: int) -> None:
        self.set_zoom(value / ZOOM_SLIDER_SCALE)

    def _sync_size_controls(self) -> None:
        if not hasattr(self, "row_height_slider"):
            return
        height, zoom = self.row_height, self.zoom
        for slider, value in (
            (self.row_height_slider, height),
            (self.zoom_slider, round(zoom * ZOOM_SLIDER_SCALE)),
        ):
            blocked = slider.blockSignals(True)  # noqa: FBT003 - Qt's API
            slider.setValue(value)
            slider.blockSignals(blocked)
        self.row_height_value.setText(f"{height} px")
        self.zoom_value.setText(f"{round(zoom * ZOOM_SLIDER_SCALE)} %")
        self.fit_button.setEnabled(zoom > MIN_ZOOM)
        self.lower_rows_button.setEnabled(height > MIN_LANE_HEIGHT)
        self.taller_rows_button.setEnabled(height < MAX_LANE_HEIGHT)
        wheel = native_keys("aussi Ctrl + molette sur les noms")
        self.lower_rows_button.setToolTip(f"Lignes moins hautes : {height} px ({wheel})")
        self.taller_rows_button.setToolTip(f"Lignes plus hautes : {height} px ({wheel})")

    def _on_zoom_wheel(self, notches: int, viewport_x: float) -> None:
        self.set_zoom(self.zoom * ZOOM_STEP**notches, viewport_x=viewport_x)

    def _on_row_height_wheel(self, notches: int) -> None:
        self.set_row_height(self.row_height + ROW_HEIGHT_STEP * notches)

    def _step_from_edge(self, step: int) -> None:
        """Show the period past the end the sideways wheel went beyond."""
        self.grid.land_on_edge(step)
        self.step(step)

    @property
    def color_by(self) -> ColorBy:
        return self._color_by

    @property
    def filter_text(self) -> str:
        return self._filter

    @property
    def read_only(self) -> bool:
        return self._read_only

    def period_range(self) -> tuple[date, date]:
        """First and last day of the period shown (Monday to Friday for a week)."""
        first, last = view_range(self._anchor, self._view, WEEKDAYS)
        if self._view == "week":
            last = first + timedelta(days=max(WEEKDAYS))
        return first, last

    def apply_preferences(
        self,
        *,
        view: ViewKind | None = None,
        density: str | None = None,
        color_by: ColorBy | None = None,
        row_heights: Mapping[str, int] | None = None,
        zooms: Mapping[str, float] | None = None,
    ) -> None:
        """Show the preferences of this poste without emitting ``view_changed``."""
        if row_heights is not None:
            self._row_heights = {
                view: clamp_lane_height(height)
                for view in VIEW_KINDS
                if (height := row_heights.get(view))
            }
        if zooms is not None:
            self._zooms = {
                view: clamp_zoom(zoom)
                for view in VIEW_KINDS
                if (zoom := zooms.get(view)) and zoom > MIN_ZOOM
            }
        if view is not None and view in VIEW_KINDS:
            self._view = view
            self._anchor = self._normalized(self._anchor)
        if density is not None and density in DENSITY_LABELS:
            self._density = density
        if color_by is not None and color_by in COLOR_LABELS:
            self._color_by = color_by
            self.backlog.set_color_by(color_by)
        self._sync_toolbar()

    def set_today(self, today: date, *, show: bool = True) -> None:
        """Give the date of today (injected clock); ``show`` brings the period back to it."""
        self._today = today
        if show:
            self._anchor = self._normalized(today)
            self._sync_toolbar()

    def set_view(self, view: ViewKind, *, anchor: date | None = None) -> None:
        """Switch to ``view``; the day kept on screen is ``anchor``, else today if shown."""
        if view not in VIEW_KINDS:
            return
        if anchor is None:
            first, last = self.period_range()
            anchor = self._today if first <= self._today <= last else self._anchor
        self._change(view, anchor)

    def set_anchor(self, day: object) -> None:
        if isinstance(day, date):
            self._change(self._view, day)

    def step(self, steps: int) -> None:
        """Previous or next period (a working day in the day view)."""
        self._change(self._view, shift_view(self._anchor, self._view, steps, WEEKDAYS))

    def go_today(self) -> None:
        self._change(self._view, self._today)

    def set_density(self, density: str) -> None:
        chosen = bool(self._row_heights)
        if density in DENSITY_LABELS and (density != self._density or chosen):
            self._density = density
            # A density sets the rows of every view again, past a height chosen.
            self._row_heights.clear()
            self._sync_toolbar()
            if chosen:
                self.display_size_changed.emit()
            self.density_chosen.emit(density)
            self.view_changed.emit()

    def set_color_by(self, color_by: str) -> None:
        if color_by in COLOR_LABELS and color_by != self._color_by:
            self._color_by = "dossier" if color_by == "dossier" else "type"
            self.backlog.set_color_by(self._color_by)
            self._sync_toolbar()
            self.view_changed.emit()

    def _normalized(self, day: date) -> date:
        if self._view == "week":
            return week_start(day)
        if self._view == "day":
            return shown_day(day, WEEKDAYS)
        return day.replace(day=1)

    def _change(self, view: ViewKind, anchor: date) -> None:
        old = (self._view, self._anchor)
        self._view = view
        self._anchor = self._normalized(anchor)
        self._sync_toolbar()
        if (self._view, self._anchor) != old:
            self.view_changed.emit()

    def _sync_toolbar(self) -> None:
        for key, choice in self.view_buttons.items():
            choice.setChecked(key == self._view)
        for key, choice in self.color_buttons.items():
            choice.setChecked(key == self._color_by)
        for key, action in self.density_actions.items():
            action.setChecked(key == self._density)
        for key, action in self.color_actions.items():
            action.setChecked(key == self._color_by)
        self._sync_size_controls()
        self._fit_toolbar()

    def _on_view_clicked(self, index: int) -> None:
        self.set_view(VIEW_KINDS[index])

    def _on_color_clicked(self, index: int) -> None:
        self.set_color_by(tuple(COLOR_LABELS)[index])

    def _on_density_action(self, action: QAction) -> None:
        self.set_density(str(action.data()))

    def _on_color_action(self, action: QAction) -> None:
        self.set_color_by(str(action.data()))

    # -- model and filter ---------------------------------------------------

    def set_model(self, model: GridModel) -> None:
        """Show ``model`` in the grid; the filter and the status bar follow it."""
        self.grid.set_model(model)
        self.apply_filter()
        self._update_status()

    def set_filter(self, text: str) -> None:
        if text == self._filter:
            return
        self._filter = text
        if self.filter_edit.text() != text:
            self.filter_edit.setText(text)
        self.apply_filter()
        self.filter_changed.emit(text)

    def apply_filter(self) -> None:
        """Dim the bars, and the cards when « À planifier » can, that do not match.

        The search field of « À planifier » stays the user's: it is never written here.
        """
        model = self.grid.model
        for item in self.grid.scene.items_by_key.values():
            matched = model is None or matches_filter(item.bar, self._filter, model)
            item.setOpacity(1.0 if matched else DIMMED_OPACITY)
        dim_cards = getattr(self.backlog, "set_dimmed", None)
        if callable(dim_cards):
            dim_cards(self.unmatched_cards())

    def dimmed_bars(self) -> list[tuple[str, str]]:
        return [key for key, item in self.grid.scene.items_by_key.items() if item.opacity() < 1.0]

    def unmatched_cards(self) -> frozenset[str]:
        """Cards of « À planifier » the filter does not match (none without a filter)."""
        model = self.grid.model
        if model is None or not fold_text(self._filter).split():
            return frozenset()
        snapshot = model.snapshot
        matched = {
            card.task_id for card in build_cards(snapshot, today=self._today, search=self._filter)
        }
        return frozenset(task.id for task in snapshot.backlog() if task.id not in matched)

    # -- controller feedback ------------------------------------------------

    def set_undo_labels(self, undo: str | None, redo: str | None) -> None:
        undo_keys, redo_keys = native_keys("Ctrl+Z"), native_keys("Ctrl+Y")
        self.undo_button.setEnabled(bool(undo) and not self._read_only)
        self.redo_button.setEnabled(bool(redo) and not self._read_only)
        self.undo_button.setToolTip(
            f"{UNDO_TOOLTIP} : {undo} ({undo_keys})" if undo else NOTHING_TO_UNDO
        )
        self.redo_button.setToolTip(
            f"{REDO_TOOLTIP} : {redo} ({redo_keys})" if redo else NOTHING_TO_REDO
        )

    def set_sync_status(self, status: SyncStatusLike | None, *, local: bool) -> None:
        self.badge.set_local(local=local)
        self.badge.set_status(status)
        self._fit_toolbar()

    def set_read_only(self, read_only: bool, message: str = "") -> None:  # noqa: FBT001
        """Block the edits (offline, missing permission…), with the reason in a banner."""
        self._read_only = read_only
        self.grid.set_read_only(read_only, reason=message)
        self.backlog.set_read_only(read_only)
        self.quick.line_edit.setReadOnly(read_only)
        self.quick.line_edit.setPlaceholderText(
            READ_ONLY_PLACEHOLDER if read_only else self._quick_placeholder
        )
        if read_only:
            self.editor.close_editor()
            self.read_only_banner.show_error(
                f"{READ_ONLY_TITLE} : {message}" if message else READ_ONLY_TITLE
            )
        else:
            self.read_only_banner.clear()
        self._update_status()

    def set_identity(self, *, identified: bool, label_text: str = "") -> None:
        """Show who signs the changes of this poste, and a banner while nobody said so."""
        if identified:
            self.identity_banner.clear()
        else:
            self.identity_banner.show_notice(IDENTITY_MESSAGE)
        self.identity_label.setText(f"Poste : {label_text}" if label_text else "")
        self.identity_label.setVisible(bool(label_text))

    def show_toast(
        self,
        message: str,
        *,
        action_text: str = "",
        action: Callable[[], object] | None = None,
        author_tag: str = "",
        timeout_ms: int | None = None,
    ) -> Toast:
        options: dict[str, object] = {}
        if timeout_ms is not None:
            options["timeout_ms"] = timeout_ms
        toast = self.toasts.show_toast(
            message,
            action_text=action_text,
            action=action,
            author_tag=author_tag,
            **options,  # type: ignore[arg-type]
        )
        self.toasts.raise_()
        return toast

    def open_editor(
        self, rect: QRect, context: EntryContext, *, label_text: str, prefill: str = ""
    ) -> None:
        """Open the cell editor over ``rect`` (global coordinates), toasts kept above it."""
        self.hover.hide()
        self.editor.open(rect, context, label=label_text, prefill=prefill)
        self.toasts.raise_()

    def cell_rect(self, row_ids: Sequence[str], start: WallTime, end: WallTime) -> QRect:
        """Global rectangle of a span over ``row_ids`` in the grid (empty when not shown)."""
        model = self.grid.model
        rows = [row for row in row_ids if model is not None and model.rows.get(row)]
        if model is None or not rows:
            return QRect()
        top = self.grid.scene.span_rect(start, end, rows[0])
        bottom = self.grid.scene.span_rect(start, end, rows[-1])
        area = top.united(bottom)
        view = self.grid.view
        top_left = view.mapFromScene(area.topLeft())
        bottom_right = view.mapFromScene(area.bottomRight())
        local = QRect(top_left, bottom_right).normalized()
        visible = view.viewport().rect()
        local = QRect(
            max(local.left(), visible.left()),
            max(local.top(), visible.top()),
            max(min(local.width(), visible.width()), 40),
            max(local.height(), 20),
        )
        return QRect(view.viewport().mapToGlobal(local.topLeft()), local.size())

    def cell_cursor(self) -> tuple[str, WallTime] | None:
        """Cell last clicked in the empty grid, or the end of the current bar."""
        current = self.grid.current
        model = self.grid.model
        if current is not None and model is not None:
            bar = model.bar(*current)
            if bar is not None:
                return bar.row_id, bar.end
        return self._cell

    def set_cell_cursor(self, row_id: str, start: WallTime) -> None:
        self._cell = (row_id, start)

    def focus_quick_entry(self) -> None:
        if self.editor.is_open():
            self.editor.close_editor()
        self.quick.focus_entry()

    def focus_filter(self) -> None:
        self.filter_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.filter_edit.selectAll()

    def open_calendar(self) -> None:
        position = self.period_button.mapToGlobal(QPoint(0, self.period_button.height() + 2))
        self.calendar_popup.open_at(position, self._anchor)

    def show_shortcuts(self) -> None:
        self.hover.hide()
        self.sheet.open_sheet()

    def toggle_shortcuts(self) -> None:
        if self.sheet.isVisible():
            self.sheet.close_sheet()
        else:
            self.show_shortcuts()

    def shortcuts_visible(self) -> bool:
        return self.sheet.isVisible()

    def _focus_grid(self) -> None:
        self.grid.view.setFocus(Qt.FocusReason.OtherFocusReason)

    def _refresh_icons(self) -> None:
        for widget, name in self._icon_buttons:
            widget.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
            widget.setIcon(tool_icon(name))
        self.period_button.setIconSize(QSize(ICON_SIZE - 2, ICON_SIZE - 2))
        self.period_button.setIcon(tool_icon("chevron-down", "text", ICON_SIZE - 2))

    def _on_badge_clicked(self) -> None:
        if self.badge.is_actionable():
            self.retry_requested.emit()

    # -- status bar ---------------------------------------------------------

    def _update_status(self, *_args: object) -> None:
        bars = self.grid.selected_bars()
        summary = selection_summary(self.grid.model, bars)
        self.selection_label.setText(summary or self._period_summary())
        if self._read_only:
            hints = [READ_ONLY_HINT]
        elif bars:
            people = self._removed_people(bars)
            if self._deletes_all(bars):
                hints = [*SELECTION_HINTS, "Suppr supprimer"]
            else:
                hints = [*SELECTION_HINTS, f"Suppr retirer {people}" if people else "Suppr retirer"]
                hints.append("Maj+Suppr supprimer")
        else:
            hints = list(IDLE_HINTS)
        self.hints_label.setText(native_keys(" · ".join([*hints, F1_HINT])))

    def _period_summary(self) -> str:
        model = self.grid.model
        if model is None:
            return ""
        people = sum(1 for row in model.rows if row.kind == "resource")
        return (
            f"{period_text(self._view, self._anchor)} · {people} lignes · {len(model.bars)} barres"
        )

    def _deletes_all(self, bars: Sequence[tuple[str, str]]) -> bool:
        """Whether Suppr deletes every task of ``bars`` (nobody left on any of them)."""
        model = self.grid.model
        if model is None:
            return False
        selected: dict[str, set[str]] = {}
        for task_id, row_id in bars:
            selected.setdefault(task_id, set()).add(row_id)
        for task_id, rows in selected.items():
            task = model.task(task_id)
            if task is not None and set(task.assignments) - rows and UNASSIGNED_ROW_ID not in rows:
                return False
        return True

    def _removed_people(self, bars: Sequence[tuple[str, str]]) -> str:
        names = [self.grid.row_short(row_id) for _task, row_id in bars if row_id]
        unique = list(dict.fromkeys(name for name in names if name))
        return ", ".join(unique[:3]) + ("…" if len(unique) > 3 else "")  # noqa: PLR2004

    def status_texts(self) -> tuple[str, str]:
        return self.selection_label.full_text(), self.hints_label.full_text()

    # -- layout -------------------------------------------------------------

    def _toolbar_width(self) -> int:
        layout = self._toolbar_layout
        left, _top, right, _bottom = TOOLBAR_MARGINS
        widths: list[int] = []
        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is None or widget.isHidden():
                continue
            if widget is self.filter_edit:
                widths.append(FILTER_MIN_WIDTH)
            else:
                widths.append(widget.sizeHint().width())
        return left + right + sum(widths) + TOOLBAR_SPACING * max(len(widths) - 1, 0)

    def _fit_toolbar(self) -> None:
        """Hide the optional controls, one after the other, while the toolbar is too wide."""
        if self._fitting or not hasattr(self, "_optional"):
            return
        self._fitting = True
        try:
            available = self.width()
            self.period_button.setText(period_text(self._view, self._anchor))
            for widget in self._optional:
                widget.setHidden(False)
            for widget in self._optional:
                if self._toolbar_width() <= available:
                    break
                widget.setHidden(True)
            if self._toolbar_width() > available:
                self.period_button.setText(period_text(self._view, self._anchor, short=True))
            self.period_button.setToolTip(
                f"{period_text(self._view, self._anchor)} : choisir une date "
                "(calendrier avec numéros de semaine)"
            )
        finally:
            self._fitting = False

    def _place_toasts(self, *_args: object) -> None:
        # The width the panel set itself: its geometry follows at the next layout pass.
        right = (self.side.minimumWidth() if self.side.isVisible() else 0) + TOAST_MARGIN
        self.toasts.set_margins(right=right, bottom=STATUS_HEIGHT + 10)

    @property
    def panel_adapting(self) -> bool:
        """True while the tab folds or unfolds the panel for its width, not by choice."""
        return self._adapting

    def _on_panel_collapsed(self, _collapsed: bool) -> None:  # noqa: FBT001
        if not self._adapting:
            self._auto_folded = False  # folded or opened by hand: the tab leaves it so
        self._place_toasts()

    def _set_panel(self, *, collapsed: bool) -> None:
        self._adapting = True
        try:
            self.side.set_collapsed(collapsed)
        finally:
            self._adapting = False

    def _adapt_panel(self) -> None:
        """Below ``NARROW_PANEL_WIDTH``: the panel starts as a rail and opens over the grid.

        Only on screen, where the width is the real one; the poste's choice is not changed.
        """
        if not self.isVisible():
            return
        narrow = self.width() < NARROW_PANEL_WIDTH
        self.content_layout.set_floating(floating=narrow)
        if narrow and not self._narrow:
            if not self.side.is_collapsed():
                self._set_panel(collapsed=True)
                self._auto_folded = True
        elif not narrow and self._narrow:
            if self._auto_folded and self.side.is_collapsed():
                self._set_panel(collapsed=False)
            self._auto_folded = False
        self._narrow = narrow

    def _focus_inside(self) -> None:
        """Give the grid the focus when it is elsewhere, on a navigation button say.

        The shortcuts of the tab only work from inside it.
        """
        focus = self.window().focusWidget()
        if focus is None or (focus is not self and not self.isAncestorOf(focus)):
            self._focus_grid()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._adapt_panel()
        self._fit_toolbar()
        self._place_toasts()
        if self.sheet.isVisible():
            self.sheet.open_sheet()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._adapt_panel()
        self._fit_toolbar()
        self._place_toasts()
        if not event.spontaneous():
            self._focus_inside()
        self.visibility_changed.emit()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        self.hover.hide()
        self.calendar_popup.hide()
        super().hideEvent(event)
        self.visibility_changed.emit()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange:
            if not self.isActiveWindow():
                self.hover.hide()
            self.visibility_changed.emit()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.grid.view and event.type() == QEvent.Type.KeyPress:
            return isinstance(event, QKeyEvent) and self._typed_on_grid(event)
        if (
            watched is self.grid.view.viewport()
            and event.type() == QEvent.Type.MouseButtonPress
            and isinstance(event, QMouseEvent)
        ):
            self._remember_cell(event)
        elif watched is self.grid.view.viewport() and event.type() == QEvent.Type.Leave:
            self.hover.hide()
        return super().eventFilter(watched, event)

    def _typed_on_grid(self, event: QKeyEvent) -> bool:
        """Open the cell editor with a letter or a digit typed on the grid."""
        text = event.text()
        commanded = event.modifiers() & (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier
        )
        if (
            commanded
            or not text
            or not text.isprintable()
            or text.isspace()
            or self._read_only
            or self.grid.gestures.active
        ):
            return False
        self.cell_typed.emit(text)
        return True

    def _remember_cell(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = self.grid.view.mapToScene(event.position().toPoint())
        if self.grid.scene.bar_at(point) is not None:
            return
        row = self.grid.scene.row_at(point.y())
        if row is None or row_kind(row.resource_id) == "team":
            return
        self._cell = (row.resource_id, self.grid.snapped_start(row.resource_id, point.x()))


__all__ = [
    "DIMMED_OPACITY",
    "CalendarPopup",
    "PanelLayout",
    "PlanningTab",
    "ShortcutSheet",
    "matches_filter",
    "native_keys",
    "period_text",
    "selection_summary",
]
