"""« Zone de notification » of the planning: the bell of the toolbar and its history.

``BellButton`` shows how many alerts are unread. A click opens ``AlertPanel`` under it:
the alerts of this poste, newest first, grouped by day. Each one opens its task, can be
put off (« Reporter ») or removed; an alert put off rings again at its new time. The
panel only shows and asks: the Planning controller owns the ``AlertLog``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from projectflow.core.planning.alert_log import LoggedAlert
from projectflow.core.planning.alerts import moment_text
from projectflow.core.planning.walltime import format_hhmm, minute_of_day, wall_date
from projectflow.ui.theme import (
    BODY_FAMILY,
    HEADING_FAMILY,
    body_font,
    current_theme,
    theme_notifier,
)

PANEL_WIDTH = 400
PANEL_MAX_HEIGHT = 520
CHROME_HEIGHT = 96
"""Title and foot of the panel, around its rows."""
BADGE_SIZE = 15
MAX_BADGE = 9
EMPTY_TEXT = (
    "Aucune alerte pour l'instant.\nAjoutez une « Alerte notification » dans le détail d'une tâche."
)
TITLE = "Notifications"
READ_ALL_TEXT = "Tout marquer comme lu"
CLEAR_TEXT = "Vider l'historique"
OPEN_TEXT = "Ouvrir"
SNOOZE_TEXT = "Reporter"
SNOOZE_TOMORROW = -1
"""Choice « Demain matin » of « Reporter »; the others are minutes."""
SNOOZE_CHOICES: tuple[tuple[str, int], ...] = (
    ("Dans 10 min", 10),
    ("Dans 30 min", 30),
    ("Dans 1 h", 60),
    ("Demain matin", SNOOZE_TOMORROW),
)
MONTHS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)
WEEKDAYS = ("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche")


def day_heading(day: date, today: date) -> str:
    """« Aujourd'hui », « Hier », else « Jeudi 19 novembre »."""
    gap = (today - day).days
    if gap == 0:
        return "Aujourd'hui"
    if gap == 1:
        return "Hier"
    if gap == -1:
        return "Demain"
    return f"{WEEKDAYS[day.weekday()]} {day.day} {MONTHS[day.month - 1]}"


def state_text(entry: LoggedAlert, today: date) -> str:
    """Tell the time of an alert, then « manquée » or « reportée à … »."""
    clock = format_hhmm(minute_of_day(entry.due))
    if entry.snoozed_until is not None:
        return f"{clock} · reportée à {moment_text(entry.snoozed_until, today)}"
    return f"{clock} · manquée" if entry.missed else clock


class BellButton(QToolButton):
    """Bell of the toolbar with the count of unread alerts on its corner."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._count = 0
        self.setProperty("variant", "icon")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Notifications")
        self.set_count(0)

    def count(self) -> int:
        return self._count

    def set_count(self, count: int) -> None:
        self._count = max(count, 0)
        if self._count == 0:
            tip = "Notifications : rien de nouveau"
        elif self._count == 1:
            tip = "Notifications : 1 alerte non lue"
        else:
            tip = f"Notifications : {self._count} alertes non lues"
        self.setToolTip(tip)
        self.update()

    def badge_text(self) -> str:
        if self._count == 0:
            return ""
        return str(self._count) if self._count <= MAX_BADGE else f"{MAX_BADGE}+"

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        text = self.badge_text()
        if not text:
            return
        theme = current_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = BADGE_SIZE + (6 if len(text) > 1 else 0)
        rect = QRectF(self.width() - width - 3, 3, width, BADGE_SIZE)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("err"))
        painter.drawRoundedRect(rect, BADGE_SIZE / 2, BADGE_SIZE / 2)
        painter.setPen(QPen(QColor("#ffffff")))
        painter.setFont(body_font(10, QFont.Weight.Bold))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.end()


class AlertRow(QFrame):
    """One alert of the history."""

    open_requested = Signal(str)
    snooze_requested = Signal(str, int)
    remove_requested = Signal(str)

    def __init__(self, entry: LoggedAlert, today: date, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.setObjectName("alertRow")
        self.setProperty("unread", "true" if not entry.read else "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)

        self.title_label = QLabel(entry.title, self)
        self.title_label.setObjectName("alertTitle")
        self.title_label.setWordWrap(True)
        self.state_label = QLabel(state_text(entry, today), self)
        self.state_label.setObjectName("alertState")
        self.body_label = QLabel(entry.body, self)
        self.body_label.setObjectName("alertBody")
        self.body_label.setWordWrap(True)

        self.open_button = QPushButton(OPEN_TEXT, self)
        self.open_button.setObjectName("alertAction")
        self.snooze_button = QPushButton(SNOOZE_TEXT, self)
        self.snooze_button.setObjectName("alertAction")
        self.remove_button = QPushButton("Retirer", self)
        self.remove_button.setObjectName("alertAction")
        self.remove_button.setProperty("quiet", "true")
        for widget in (self.open_button, self.snooze_button, self.remove_button):
            widget.setCursor(Qt.CursorShape.PointingHandCursor)
            widget.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.snooze_menu = QMenu(self.snooze_button)
        for text, minutes in SNOOZE_CHOICES:
            self.snooze_menu.addAction(text).setData(minutes)
        self.snooze_menu.triggered.connect(self._on_snooze)
        self.open_button.clicked.connect(lambda: self.open_requested.emit(entry.key))
        self.snooze_button.clicked.connect(self._show_snooze)
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(entry.key))

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
        head.addWidget(self.title_label, 1)
        head.addWidget(self.state_label, 0, Qt.AlignmentFlag.AlignTop)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 2, 0, 0)
        actions.setSpacing(4)
        actions.addWidget(self.open_button)
        actions.addWidget(self.snooze_button)
        actions.addStretch(1)
        actions.addWidget(self.remove_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 12, 8)
        layout.setSpacing(2)
        layout.addLayout(head)
        layout.addWidget(self.body_label)
        layout.addLayout(actions)

    def snooze(self, minutes: int) -> None:
        self.snooze_requested.emit(self.entry.key, minutes)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.open_requested.emit(self.entry.key)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _show_snooze(self) -> None:
        self.snooze_menu.popup(
            self.snooze_button.mapToGlobal(self.snooze_button.rect().bottomLeft())
        )

    def _on_snooze(self, action: object) -> None:
        data = action.data() if hasattr(action, "data") else None
        if isinstance(data, int):
            self.snooze(data)


class AlertPanel(QFrame):
    """History of the alerts, opened under the bell."""

    open_requested = Signal(str)
    snooze_requested = Signal(str, int)
    remove_requested = Signal(str)
    read_all_requested = Signal()
    clear_requested = Signal()
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("alertPanel")
        self.setFixedWidth(PANEL_WIDTH)
        self._rows: list[AlertRow] = []
        self._headings: list[QLabel] = []

        self.title_label = QLabel(TITLE, self)
        self.title_label.setObjectName("alertPanelTitle")
        self.read_all_button = QPushButton(READ_ALL_TEXT, self)
        self.read_all_button.setObjectName("alertAction")
        self.read_all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.read_all_button.clicked.connect(self.read_all_requested.emit)
        head = QHBoxLayout()
        head.setContentsMargins(14, 12, 10, 10)
        head.addWidget(self.title_label, 1)
        head.addWidget(self.read_all_button)

        self.empty_label = QLabel(EMPTY_TEXT, self)
        self.empty_label.setObjectName("alertEmpty")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._list = QWidget()
        self._list.setObjectName("alertList")
        self._list_layout = QVBoxLayout(self._list)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(0)
        self._list_layout.addStretch(1)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setWidget(self._list)

        self.clear_button = QPushButton(CLEAR_TEXT, self)
        self.clear_button.setObjectName("alertAction")
        self.clear_button.setProperty("quiet", "true")
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_button.clicked.connect(self.clear_requested.emit)
        foot = QHBoxLayout()
        foot.setContentsMargins(10, 6, 10, 8)
        foot.addStretch(1)
        foot.addWidget(self.clear_button)

        layout = QVBoxLayout(self)
        self._layout = layout
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addLayout(head)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.scroll_area, 1)
        layout.addLayout(foot)
        theme_notifier().changed.connect(self._restyle)
        self._restyle()
        self.set_entries((), today=date.today())

    # -- contract ---------------------------------------------------------------

    def rows(self) -> list[AlertRow]:
        return list(self._rows)

    def headings(self) -> list[str]:
        return [heading.text() for heading in self._headings]

    def set_entries(self, entries: Sequence[LoggedAlert], *, today: date) -> None:
        """Show ``entries`` (newest first), a heading over each day."""
        for widget in (*self._rows, *self._headings):
            self._list_layout.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        self._rows = []
        self._headings = []
        shown_day: date | None = None
        position = 0
        for entry in entries:
            day = wall_date(entry.due)
            if day != shown_day:
                shown_day = day
                heading = QLabel(day_heading(day, today).upper(), self._list)
                heading.setObjectName("alertDay")
                self._list_layout.insertWidget(position, heading)
                self._headings.append(heading)
                position += 1
            row = AlertRow(entry, today, self._list)
            row.open_requested.connect(self.open_requested.emit)
            row.snooze_requested.connect(self.snooze_requested.emit)
            row.remove_requested.connect(self.remove_requested.emit)
            self._list_layout.insertWidget(position, row)
            self._rows.append(row)
            position += 1
        empty = not self._rows
        self.empty_label.setVisible(empty)
        self.scroll_area.setVisible(not empty)
        self.clear_button.setVisible(not empty)
        self.read_all_button.setVisible(any(not row.entry.read for row in self._rows))
        self._fit()

    def open_under(self, anchor: QWidget) -> None:
        """Show the panel under ``anchor``, its right edge on the anchor's, inside the screen."""
        self._fit()
        corner = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height() + 4))
        x = corner.x() - self.width()
        y = corner.y()
        screen = anchor.screen()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left() + 4, min(x, area.right() - self.width() - 4))
            y = min(y, area.bottom() - self.height() - 4)
        self.move(x, y)
        self.show()
        self.raise_()

    def hideEvent(self, event: object) -> None:  # noqa: N802
        super().hideEvent(event)  # type: ignore[arg-type]
        self.closed.emit()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(PANEL_WIDTH, self.height())

    # -- internals --------------------------------------------------------------

    def _fit(self) -> None:
        """Give the panel the height of its rows, up to ``PANEL_MAX_HEIGHT``."""
        inner = PANEL_WIDTH - 2
        self._list_layout.activate()
        if self._list_layout.hasHeightForWidth():
            wanted = self._list_layout.totalHeightForWidth(inner)
        else:
            wanted = self._list_layout.totalSizeHint().height()
        self.scroll_area.setFixedHeight(min(wanted, PANEL_MAX_HEIGHT - CHROME_HEIGHT))
        self._layout.activate()
        if self._layout.hasHeightForWidth():
            height = self._layout.totalHeightForWidth(PANEL_WIDTH)
        else:
            height = self._layout.totalSizeHint().height()
        self.setFixedHeight(min(height, PANEL_MAX_HEIGHT))

    def _restyle(self) -> None:
        theme = current_theme()
        t = theme.tokens
        hairline = theme.rgba("text", 0.14)
        self.setStyleSheet(
            f"""
QFrame#alertPanel {{ background: {t["bg"]}; border: 1px solid {theme.rgba("text", 0.30)}; }}
QWidget#alertList {{ background: {t["bg"]}; }}
QLabel#alertPanelTitle {{
  font-family: "{HEADING_FAMILY}"; font-weight: 600; font-size: 20px; color: {t["text"]};
}}
QLabel#alertDay {{
  font-family: "{BODY_FAMILY}"; font-size: 11px; font-weight: 600; letter-spacing: 1px;
  color: {t["neutral-700"]}; background: {t["surface"]}; padding: 5px 14px;
  border-top: 1px solid {hairline}; border-bottom: 1px solid {hairline};
}}
QFrame#alertRow {{
  background: {t["bg"]}; border: 0; border-left: 3px solid transparent;
  border-bottom: 1px solid {hairline};
}}
QFrame#alertRow[unread="true"] {{
  background: {t["accent-100"]}; border-left: 3px solid {t["accent"]};
}}
QFrame#alertRow:hover {{ background: {theme.rgba("accent", 0.10)}; }}
QLabel#alertTitle {{
  font-family: "{BODY_FAMILY}"; font-size: 14px; font-weight: 600; color: {t["text"]};
  background: transparent;
}}
QLabel#alertBody {{
  font-family: "{BODY_FAMILY}"; font-size: 13px; color: {t["neutral-800"]};
  background: transparent;
}}
QLabel#alertState {{
  font-family: "{BODY_FAMILY}"; font-size: 12px; color: {t["neutral-700"]};
  background: transparent;
}}
QLabel#alertEmpty {{
  font-family: "{BODY_FAMILY}"; font-size: 13px; color: {t["neutral-700"]};
  padding: 26px 24px 30px 24px;
}}
QPushButton#alertAction {{
  font-family: "{HEADING_FAMILY}"; font-weight: 600; font-size: 14px; color: {t["accent-700"]};
  background: transparent; border: 0; padding: 3px 8px; min-height: 0;
}}
QPushButton#alertAction:hover {{ background: {theme.rgba("accent", 0.14)}; }}
QPushButton#alertAction[quiet="true"] {{ color: {t["neutral-700"]}; font-weight: 400; }}
"""
        )
