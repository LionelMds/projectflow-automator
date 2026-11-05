"""Read-only card shown when the pointer rests on a bar or a card of the planning.

The tab waits ``HOVER_DELAY_MS`` before calling ``show_for``. The card never takes the
focus nor the mouse: it is a frameless tool-tip window drawn as a blueprint object with
corner marks. It shows the dossier (number, client, designation, contact, last release to
the workshop, manager, place), the task, its findings, who changed it last and a 6 px
strip of every task of the dossier in time.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from projectflow.core.planning.walltime import MINUTES_PER_HOUR, WallTime
from projectflow.ui.planning.sync_badge import feedback_color
from projectflow.ui.theme import current_theme, theme_notifier
from projectflow.ui.widgets.industry import MARK_GUTTER, BlueprintFrame, label, rule

HOVER_DELAY_MS: Final = 500
CARD_WIDTH: Final = 320
CARD_PADDING: Final = (14, 12, 14, 12)
CARD_SPACING: Final = 5
CURSOR_OFFSET: Final = QPoint(14, 18)
STRIP_HEIGHT: Final = 6
SEGMENT_GAP: Final = 1.0
MIN_SEGMENT_WIDTH: Final = 2.0
SWATCH_SIZE: Final = 10
SWATCH_GAP: Final = 8
FINDING_MARK: Final = "▲"

StripSegment = tuple[WallTime, WallTime, str]
"""A task of the dossier on the strip: start, end and ``#rrggbb`` colour."""


@dataclass(frozen=True, slots=True)
class HoverInfo:
    """What the card shows; empty fields are left out.

    ``people`` are short labels (initials), ``findings`` plain sentences (conflicts),
    ``author`` the « Modifié par JD · BUREAU-2 il y a 3 min » line. ``dossier_tasks`` are
    all the scheduled tasks of the dossier, ``current`` the one under the pointer, and
    ``dossier_minutes`` their worked minutes for the « 3 tâches · 51 h » total.
    """

    number: str = ""
    designation: str = ""
    client: str = ""
    contact: str = ""
    atelier_release: str = ""
    managed_by: str = ""
    location: str = ""
    title: str = ""
    type_label: str = ""
    type_color: str = ""
    when: str = ""
    duration: str = ""
    rest: str = ""
    """Night work: the rest kept around it (``night.rest_text``)."""
    people: tuple[str, ...] = ()
    status: str = ""
    notes: str = ""
    findings: tuple[str, ...] = ()
    author: str = ""
    dossier_tasks: tuple[StripSegment, ...] = ()
    current: tuple[WallTime, WallTime] | None = None
    dossier_minutes: int = 0


def dossier_summary(count: int, minutes: int) -> str:
    """``3 tâches · 51 h`` (worked hours, the minutes only when there are some)."""
    tasks = f"{count} tâche{'s' if count > 1 else ''}"
    if minutes <= 0:
        return tasks
    hours, rest = divmod(minutes, MINUTES_PER_HOUR)
    if not hours:
        return f"{tasks} · {rest} min"
    return f"{tasks} · {hours} h {rest:02d}" if rest else f"{tasks} · {hours} h"


def meta_line(info: HoverInfo) -> str:
    """« Sorti atelier 14.10 · Géré par LM · Sion, ch. des Vignes »."""
    parts = (
        f"Sorti atelier {info.atelier_release}" if info.atelier_release else "",
        f"Géré par {info.managed_by}" if info.managed_by else "",
        info.location,
    )
    return " · ".join(part for part in parts if part)


class DossierStrip(QWidget):
    """The tasks of a dossier side by side in time, as thin coloured blocks."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._segments: tuple[StripSegment, ...] = ()
        self._current: tuple[WallTime, WallTime] | None = None
        self.setFixedHeight(STRIP_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_segments(
        self,
        segments: Sequence[StripSegment],
        current: tuple[WallTime, WallTime] | None = None,
    ) -> None:
        self._segments = tuple(segment for segment in segments if segment[1] > segment[0])
        self._current = current
        self.update()

    def segments(self) -> tuple[StripSegment, ...]:
        return self._segments

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(CARD_WIDTH, STRIP_HEIGHT)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        theme = current_theme()
        rect = QRectF(self.rect())
        painter.fillRect(rect, theme.color("neutral-200"))
        if self._segments:
            first = min(start for start, _end, _color in self._segments)
            last = max(end for _start, end, _color in self._segments)
            scale = rect.width() / max(1, last - first)
            for start, end, color in self._segments:
                block = QRectF(rect.left() + (start - first) * scale, rect.top(), 0, rect.height())
                # A hairline of ground between tasks that follow each other.
                block.setWidth(max(MIN_SEGMENT_WIDTH, (end - start) * scale - SEGMENT_GAP))
                painter.fillRect(block, QColor(color))
            if self._current is not None:
                start, end = self._current
                pen = QPen(theme.color("text"))
                pen.setWidthF(1.0)
                painter.setPen(pen)
                left = rect.left() + (start - first) * scale
                width = max(MIN_SEGMENT_WIDTH, (end - start) * scale - SEGMENT_GAP)
                painter.drawRect(
                    QRectF(left, rect.top(), width, rect.height()).adjusted(0.5, 0.5, -0.5, -0.5)
                )
        painter.end()


class _Swatch(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._color = ""
        self.setFixedSize(SWATCH_SIZE, SWATCH_SIZE)

    def set_color(self, color: str) -> None:
        self._color = color
        self.setVisible(bool(color))
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        if not self._color:
            return
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self._color))
        painter.end()


class HoverCard(BlueprintFrame):
    """Dossier and task details next to the pointer; ``hide()`` removes it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, padding=CARD_PADDING, fill="bg", spacing=CARD_SPACING)
        self.setWindowFlags(
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedWidth(CARD_WIDTH + 2 * MARK_GUTTER)
        self._info: HoverInfo | None = None

        self.number_label = label("", "h4")
        self.client_label = label("", "muted")
        self.client_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        header = QHBoxLayout()
        header.setSpacing(SWATCH_GAP)
        header.addWidget(self.number_label, 0, Qt.AlignmentFlag.AlignBaseline)
        header.addWidget(self.client_label, 1, Qt.AlignmentFlag.AlignBaseline)
        self.designation_label = label("", wrap=True)
        self.contact_label = label("", "small", wrap=True)
        self.meta_label = label("", "small", wrap=True)
        self.task_rule = rule()
        self.swatch = _Swatch()
        self.title_label = label("", "field-lg", wrap=True)
        title_row = QHBoxLayout()
        title_row.setSpacing(SWATCH_GAP)
        title_row.addWidget(self.swatch, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addWidget(self.title_label, 1)
        self.type_label = label("", "small", wrap=True)
        self.when_label = label("", wrap=True)
        self.people_label = label("", "small", wrap=True)
        self.notes_label = label("", "small", wrap=True)
        self.findings_label = label("", "small", wrap=True)
        self.strip = DossierStrip()
        self.summary_label = label("", "small")
        self.author_label = label("", "small", wrap=True)

        self.box.addLayout(header)
        dossier_rows: tuple[QWidget, ...] = (
            self.designation_label,
            self.contact_label,
            self.meta_label,
            self.task_rule,
        )
        for row in dossier_rows:
            self.box.addWidget(row)
        self.box.addLayout(title_row)
        task_rows: tuple[QWidget, ...] = (
            self.type_label,
            self.when_label,
            self.people_label,
            self.notes_label,
            self.findings_label,
            self.strip,
            self.summary_label,
            self.author_label,
        )
        for row in task_rows:
            self.box.addWidget(row)
        theme_notifier().changed.connect(self._restyle)
        self.hide()

    @property
    def info(self) -> HoverInfo | None:
        return self._info

    def show_for(self, info: HoverInfo, global_pos: QPoint) -> None:
        """Fill the card with ``info`` and show it below and right of ``global_pos``."""
        self._info = info
        self._populate(info)
        self._fit_height()
        self.move(self._position(global_pos))
        self.show()
        self.raise_()

    def texts(self) -> list[str]:
        """Visible texts, top to bottom (for tests and accessibility)."""
        return [
            widget.text()
            for widget in self.findChildren(QLabel)
            if not widget.isHidden() and widget.text()
        ]

    # --------------------------------------------------------- populate ----

    def _populate(self, info: HoverInfo) -> None:
        self._set(self.number_label, info.number)
        self._set(self.client_label, info.client)
        self._set(self.designation_label, info.designation)
        self._set(self.contact_label, info.contact)
        self._set(self.meta_label, meta_line(info))
        self.swatch.set_color(info.type_color)
        self._set(self.title_label, info.title)
        self.task_rule.setVisible(bool(info.title or info.when))
        status = " · ".join(part for part in (info.type_label, info.status) if part)
        self._set(self.type_label, status)
        when = " · ".join(part for part in (info.when, info.duration) if part)
        self._set(self.when_label, "\n".join(part for part in (when, info.rest) if part))
        self._set(self.people_label, ", ".join(info.people))
        self._set(self.notes_label, info.notes)
        findings = "\n".join(f"{FINDING_MARK} {finding}" for finding in info.findings)
        self._set(self.findings_label, findings)
        self.strip.set_segments(info.dossier_tasks, info.current)
        self.strip.setVisible(bool(info.dossier_tasks))
        summary = dossier_summary(len(info.dossier_tasks), info.dossier_minutes)
        self._set(self.summary_label, summary if info.dossier_tasks else "")
        self._set(self.author_label, info.author)
        self._restyle()

    def _fit_height(self) -> None:
        """Make the card as high as its wrapped texts at its fixed width.

        ``adjustSize`` measures them at their natural, wider width and cuts the last rows.
        """
        self.ensurePolished()
        layout = self.layout()
        if layout is None:
            self.adjustSize()
            return
        layout.invalidate()
        # The minimum height of the previous content would keep a shorter card tall.
        layout.activate()
        height = (
            layout.totalHeightForWidth(self.width())
            if layout.hasHeightForWidth()
            else layout.totalSizeHint().height()
        )
        self.resize(self.width(), max(height, layout.totalMinimumSize().height()))

    @staticmethod
    def _set(widget: QLabel, text: str) -> None:
        widget.setText(text)
        widget.setVisible(bool(text))

    def _restyle(self) -> None:
        ink = feedback_color("warn").name()
        self.findings_label.setStyleSheet(f"QLabel {{ color: {ink}; }}")
        self.update()

    def _position(self, global_pos: QPoint) -> QPoint:
        target = global_pos + CURSOR_OFFSET
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        area: QRect = screen.availableGeometry()
        size = self.size()
        x, y = target.x(), target.y()
        if x + size.width() > area.right():
            x = global_pos.x() - CURSOR_OFFSET.x() - size.width()
        if y + size.height() > area.bottom():
            y = global_pos.y() - CURSOR_OFFSET.y() - size.height()
        return QPoint(max(area.left(), x), max(area.top(), y))
