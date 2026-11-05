"""Frozen row column of the Planning grid: « Non affecté », team headers, people.

``paint_row_column`` draws the rows between ``y0`` and ``y0 + height`` of the body; the
widget follows the body's vertical scroll bar and the PDF export calls the same function
for every page. The « Non affecté » row stays pinned at the top, as in the body.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QLineF, QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from projectflow.core.planning.layout import UNASSIGNED_ROW_ID, RowLayout
from projectflow.ui.planning.grid_items import make_pen
from projectflow.ui.planning.grid_model import GridModel, RowLabel, format_hours
from projectflow.ui.theme import Theme, body_font, current_theme, heading_font

ROW_COLUMN_WIDTH = 200
MIN_ROW_COLUMN_WIDTH = 160
MAX_ROW_COLUMN_WIDTH = 280
RESIZE_GRIP = 4
LEFT_PAD = 14.0
RIGHT_PAD = 10.0
TEAM_LEFT = 12.0
CHEVRON = 4.0
CHEVRON_ROOM = 14.0
INITIALS_WIDTH = 34.0
INITIALS_HEIGHT = 24.0
COMPACT_INITIALS_WIDTH = 30.0
COMPACT_INITIALS_HEIGHT = 18.0
GAP = 9.0
TAG_GAP = 6.0
TAG_PADDING = 3.0
TAG_HEIGHT = 13.0
GAUGE_WIDTH = 28.0
"""Room of the load gauge: « 100 % » fits, and so does a full name in the 200 px column."""
GAUGE_GAP = 6.0
GAUGE_HEIGHT = 3.0
GAUGE_TEXT_GAP = 3.0
TWO_LINE_MIN_HEIGHT = 32
LINE_GAP = 1.0
PIN_SHADOW = 3.0
FOCUS_RULE = 2.0
"""Accent rule along the right edge of the column and its corner while the body has focus."""
PERCENT = 100
OVERLOAD = 1.0

APPRENTICE_TAG = "APP."
ARCHIVED_TAG = "ARCHIVÉ"
SUBCONTRACTOR_ROLE = "Sous-traitant"
CORNER_TITLE = "Équipe"


# --------------------------------------------------------------- styles ----


class RowFonts:
    """Fonts and metrics of the row column (built once per paint)."""

    __slots__ = (
        "caps",
        "caps_metrics",
        "initials",
        "name",
        "name_metrics",
        "role",
        "role_metrics",
        "small",
        "small_metrics",
        "title",
        "title_metrics",
    )

    def __init__(self, *, spaced: bool = True) -> None:
        self.caps = body_font(11, QFont.Weight.DemiBold)
        self.title = body_font(12, QFont.Weight.DemiBold)
        if spaced:
            self.caps.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.9)
            self.title.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.4)
        self.caps_metrics = QFontMetricsF(self.caps)
        self.title_metrics = QFontMetricsF(self.title)
        self.initials = heading_font(13)
        self.name = body_font(13, QFont.Weight.Medium)
        self.name_metrics = QFontMetricsF(self.name)
        self.role = body_font(11)
        self.role_metrics = QFontMetricsF(self.role)
        self.small = body_font(10, QFont.Weight.DemiBold)
        self.small_metrics = QFontMetricsF(self.small)


def load_text(label: RowLabel) -> str:
    """``77 %``, or empty when the row has no worked time in the range."""
    load = label.load
    return "" if load is None else f"{round(load * PERCENT)} %"


def row_tooltip(label: RowLabel) -> str:
    """Name, role and load of a row, for the tooltip of the row column."""
    if label.kind == "team":
        return f"{label.title} · {label.count} personne{'s' if label.count > 1 else ''}"
    if label.kind == "unassigned":
        return f"{label.title} : tâches datées sans personne ({label.count})"
    lines = [label.title]
    role = label.subtitle or (SUBCONTRACTOR_ROLE if label.subcontractor else "")
    if role:
        lines.append(role)
    if label.archived:
        lines.append("Archivé : la ligne revient tant qu'une tâche lui est affectée")
    if label.absent_all_range:
        lines.append("Absent sur toute la période affichée")
    elif label.load is not None:
        lines.append(
            f"Charge : {format_hours(label.planned_minutes)} sur "
            f"{format_hours(label.available_minutes)} ({load_text(label)})"
        )
    return "\n".join(lines)


# ------------------------------------------------------------- painting ----


def _paint_chevron(painter: QPainter, center: QPointF, color: QColor, *, collapsed: bool) -> None:
    path = QPainterPath()
    x, y = center.x(), center.y()
    if collapsed:
        path.moveTo(x - CHEVRON / 2, y - CHEVRON)
        path.lineTo(x + CHEVRON / 2, y)
        path.lineTo(x - CHEVRON / 2, y + CHEVRON)
    else:
        path.moveTo(x - CHEVRON, y - CHEVRON / 2)
        path.lineTo(x, y + CHEVRON / 2)
        path.lineTo(x + CHEVRON, y - CHEVRON / 2)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, on=True)
    painter.strokePath(path, make_pen(color, 1.5))
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, on=False)


def _paint_team(
    painter: QPainter, label: RowLabel, rect: QRectF, theme: Theme, fonts: RowFonts
) -> None:
    painter.fillRect(rect, theme.color("surface"))
    center = QPointF(rect.left() + TEAM_LEFT, rect.center().y())
    _paint_chevron(painter, center, theme.color("neutral-800"), collapsed=label.collapsed)
    left = rect.left() + TEAM_LEFT + CHEVRON_ROOM
    title = label.title.upper()
    count = f" · {label.count}"
    room = rect.right() - RIGHT_PAD - left - fonts.caps_metrics.horizontalAdvance(count)
    shown = fonts.caps_metrics.elidedText(title, Qt.TextElideMode.ElideRight, max(room, 0.0))
    painter.setFont(fonts.caps)
    painter.setPen(theme.color("neutral-800"))
    text_rect = QRectF(left, rect.top(), rect.right() - left, rect.height())
    painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter), shown)
    painter.setPen(theme.color("neutral-600"))
    offset = fonts.caps_metrics.horizontalAdvance(shown)
    painter.drawText(text_rect.adjusted(offset, 0, 0, 0), int(Qt.AlignmentFlag.AlignVCenter), count)


def _paint_unassigned(
    painter: QPainter, label: RowLabel, rect: QRectF, theme: Theme, fonts: RowFonts
) -> None:
    painter.fillRect(rect, theme.color("surface"))
    left = rect.left() + LEFT_PAD
    title = label.title.upper() + (f" · {label.count}" if label.count else "")
    two_lines = rect.height() >= TWO_LINE_MIN_HEIGHT and label.subtitle
    painter.setFont(fonts.title)
    painter.setPen(theme.color("neutral-800"))
    if not two_lines:
        text_rect = QRectF(left, rect.top(), rect.width() - LEFT_PAD, rect.height())
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter), title)
        return
    block = fonts.title_metrics.height() + fonts.role_metrics.height() + LINE_GAP
    top = rect.center().y() - block / 2
    painter.drawText(QPointF(left, top + fonts.title_metrics.ascent()), title)
    painter.setFont(fonts.role)
    painter.setPen(theme.color("neutral-700"))
    baseline = top + fonts.title_metrics.height() + LINE_GAP + fonts.role_metrics.ascent()
    painter.drawText(QPointF(left, baseline), label.subtitle)


def _paint_initials(
    painter: QPainter, label: RowLabel, box: QRectF, theme: Theme, fonts: RowFonts
) -> None:
    if label.subcontractor:
        painter.setPen(make_pen(theme.color("text", 0.35), 1.0, Qt.PenStyle.DashLine))
    else:
        painter.fillRect(box, theme.color("neutral-100"))
        painter.setPen(make_pen(theme.color("text", 0.16)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(box.adjusted(0.5, 0.5, -0.5, -0.5))
    painter.setFont(fonts.initials)
    painter.setPen(theme.color("neutral-700" if label.archived else "neutral-900"))
    painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), label.initials)


def _paint_tag(
    painter: QPainter, text: str, x: float, baseline: float, *, color: QColor, fonts: RowFonts
) -> float:
    """Small capital tag (« APP. », « VD ») after a text; gives its right end."""
    width = fonts.small_metrics.horizontalAdvance(text) + 2 * TAG_PADDING
    top = baseline - fonts.small_metrics.ascent() - (TAG_HEIGHT - fonts.small_metrics.height()) / 2
    box = QRectF(x, round(top), width, TAG_HEIGHT)
    painter.setPen(make_pen(color))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(box.adjusted(0.5, 0.5, -0.5, -0.5))
    painter.setFont(fonts.small)
    painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), text)
    return box.right()


def _paint_gauge(
    painter: QPainter,
    label: RowLabel,
    right: float,
    center: float,
    *,
    theme: Theme,
    fonts: RowFonts,
) -> None:
    load = label.load
    if load is None:
        return
    over = load > OVERLOAD
    text = load_text(label)
    left = right - GAUGE_WIDTH
    block = fonts.small_metrics.height() + GAUGE_TEXT_GAP + GAUGE_HEIGHT
    top = center - block / 2
    painter.setFont(fonts.small)
    painter.setPen(theme.color("err" if over else "neutral-700"))
    text_rect = QRectF(left, top, GAUGE_WIDTH, fonts.small_metrics.height())
    painter.drawText(
        text_rect, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), text
    )
    gauge = QRectF(
        left, round(top + fonts.small_metrics.height() + GAUGE_TEXT_GAP), GAUGE_WIDTH, GAUGE_HEIGHT
    )
    painter.fillRect(gauge, theme.color("neutral-200"))
    filled = QRectF(gauge.left(), gauge.top(), GAUGE_WIDTH * min(load, OVERLOAD), GAUGE_HEIGHT)
    painter.fillRect(filled, theme.color("err" if over else "accent-400"))


@dataclass(frozen=True, slots=True)
class ResourceTexts:
    """What a person's row label shows in its room: name, role line and small tags.

    The tags (« APP. », a canton other than the planning's) follow the name when the
    whole name still fits, else they go to the role line: a name is never cut for them.
    """

    name: str
    role: str
    tags: tuple[str, ...]
    tags_on_role: bool


def resource_tags(label: RowLabel, default_canton: str = "") -> tuple[str, ...]:
    """« APP. » for an apprentice, the canton when it is not the planning's."""
    tags: list[str] = []
    if label.apprentice:
        tags.append(APPRENTICE_TAG)
    if label.canton and default_canton and label.canton != default_canton:
        tags.append(label.canton)
    return tuple(tags)


def resource_texts(
    label: RowLabel,
    room: float,
    fonts: RowFonts,
    *,
    two_lines: bool,
    default_canton: str = "",
) -> ResourceTexts:
    """Name, role and tags of a person's label, elided to ``room`` pixels."""
    tags = resource_tags(label, default_canton)
    tag_room = sum(_tag_room(text, fonts) for text in tags)
    role = label.subtitle or (SUBCONTRACTOR_ROLE if label.subcontractor else "")
    sub = " · ".join(
        part for part in (role, ARCHIVED_TAG.lower() if label.archived else "") if part
    )
    name_width = fonts.name_metrics.horizontalAdvance(label.title)
    tags_on_role = two_lines and bool(tags) and name_width + tag_room > room
    name_room = room if tags_on_role else room - tag_room
    name = fonts.name_metrics.elidedText(
        label.title, Qt.TextElideMode.ElideRight, max(name_room, 0.0)
    )
    role_room = room - tag_room if tags_on_role else room
    shown = (
        fonts.role_metrics.elidedText(sub, Qt.TextElideMode.ElideRight, max(role_room, 0.0))
        if two_lines
        else ""
    )
    return ResourceTexts(name=name, role=shown, tags=tags, tags_on_role=tags_on_role)


def label_room(column_width: float, *, compact: bool, gauge: bool) -> float:
    """Width left to a person's name and role in a column of ``column_width``."""
    box_width = COMPACT_INITIALS_WIDTH if compact else INITIALS_WIDTH
    gauge_room = GAUGE_WIDTH + GAUGE_GAP if gauge and not compact else 0.0
    return column_width - LEFT_PAD - box_width - GAP - RIGHT_PAD - gauge_room


def _tag_room(text: str, fonts: RowFonts) -> float:
    return fonts.small_metrics.horizontalAdvance(text) + 2 * TAG_PADDING + TAG_GAP


def _tag_color(text: str, theme: Theme) -> QColor:
    return theme.color("accent-700" if text == APPRENTICE_TAG else "neutral-700")


def _paint_resource(
    painter: QPainter,
    label: RowLabel,
    rect: QRectF,
    *,
    lane_height: float,
    theme: Theme,
    fonts: RowFonts,
    default_canton: str = "",
) -> None:
    compact = lane_height < TWO_LINE_MIN_HEIGHT
    center = rect.top() + min(rect.height(), lane_height) / 2
    box_width = COMPACT_INITIALS_WIDTH if compact else INITIALS_WIDTH
    box_height = COMPACT_INITIALS_HEIGHT if compact else INITIALS_HEIGHT
    box = QRectF(rect.left() + LEFT_PAD, round(center - box_height / 2), box_width, box_height)
    _paint_initials(painter, label, box, theme, fonts)
    gauge_right = rect.right() - RIGHT_PAD
    if not compact:
        _paint_gauge(painter, label, gauge_right, center, theme=theme, fonts=fonts)
    left = box.right() + GAP
    right = left + label_room(rect.width(), compact=compact, gauge=label.load is not None)
    role = label.subtitle or (SUBCONTRACTOR_ROLE if label.subcontractor else "")
    two_lines = not compact and bool(role or label.archived)
    block = fonts.name_metrics.height() + (
        fonts.role_metrics.height() + LINE_GAP if two_lines else 0
    )
    name_baseline = center - block / 2 + fonts.name_metrics.ascent()
    texts = resource_texts(
        label, right - left, fonts, two_lines=two_lines, default_canton=default_canton
    )
    painter.setFont(fonts.name)
    painter.setPen(theme.color("neutral-700" if label.archived else "text"))
    painter.drawText(QPointF(left, name_baseline), texts.name)
    if not texts.tags_on_role:
        x = left + fonts.name_metrics.horizontalAdvance(texts.name)
        for text in texts.tags:
            color = _tag_color(text, theme)
            x = _paint_tag(painter, text, x + TAG_GAP, name_baseline, color=color, fonts=fonts)
    if not two_lines:
        return
    painter.setFont(fonts.role)
    painter.setPen(theme.color("neutral-700"))
    baseline = name_baseline + fonts.name_metrics.descent() + LINE_GAP + fonts.role_metrics.ascent()
    painter.drawText(QPointF(left, baseline), texts.role)
    if texts.tags_on_role:
        x = left + fonts.role_metrics.horizontalAdvance(texts.role)
        for text in texts.tags:
            color = _tag_color(text, theme)
            x = _paint_tag(painter, text, x + TAG_GAP, baseline, color=color, fonts=fonts)


def paint_row(
    painter: QPainter,
    model: GridModel,
    row: RowLayout,
    rect: QRectF,
    *,
    theme: Theme,
    fonts: RowFonts,
) -> None:
    """Paint the label of ``row`` in ``rect`` (the row's place in the column)."""
    label = model.label(row.resource_id)
    if label is None:
        return
    if label.kind == "team":
        _paint_team(painter, label, rect, theme, fonts)
    elif label.kind == "unassigned":
        _paint_unassigned(painter, label, rect, theme, fonts)
    else:
        painter.fillRect(rect, theme.color("bg"))
        _paint_resource(
            painter,
            label,
            rect,
            lane_height=row.lane_height,
            theme=theme,
            fonts=fonts,
            default_canton=model.default_canton,
        )
    strong = row.kind != "resource"
    painter.setPen(make_pen(theme.color("text", 0.22 if strong else 0.08)))
    painter.drawLine(QLineF(rect.left(), rect.bottom() - 0.5, rect.right(), rect.bottom() - 0.5))


def paint_row_column(
    painter: QPainter,
    model: GridModel,
    theme: Theme,
    *,
    y0: float,
    height: float,
    width: float,
    hovered: str | None = None,
    pinned: bool = True,
    spaced: bool = True,
) -> None:
    """Paint the rows of the body between ``y0`` and ``y0 + height`` at painter y 0.

    With ``pinned`` the « Non affecté » row stays at the top once ``y0`` scrolled;
    ``spaced`` False drops the letter spacing of the capitals (PDF).
    """
    fonts = RowFonts(spaced=spaced)
    painter.save()
    painter.setClipRect(QRectF(0, 0, width, height))
    painter.fillRect(QRectF(0, 0, width, height), theme.color("bg"))
    for row in model.rows.visible(y0, y0 + height):
        rect = QRectF(0, row.top - y0, width, row.height)
        paint_row(painter, model, row, rect, theme=theme, fonts=fonts)
        if row.resource_id == hovered and row.kind == "resource":
            painter.fillRect(rect.adjusted(0, 0, 0, -1), theme.color("accent", 0.06))
    pinned_row = model.rows.get(UNASSIGNED_ROW_ID)
    if pinned and y0 > 0 and pinned_row is not None:
        rect = QRectF(0, 0, width, pinned_row.height)
        paint_row(painter, model, pinned_row, rect, theme=theme, fonts=fonts)
        _paint_pin_shadow(painter, rect, theme)
    painter.restore()
    painter.setPen(make_pen(theme.color("text", 0.30)))
    painter.drawLine(QLineF(width - 0.5, 0, width - 0.5, height))


def _paint_pin_shadow(painter: QPainter, rect: QRectF, theme: Theme) -> None:
    shadow = QLinearGradient(0, rect.bottom(), 0, rect.bottom() + PIN_SHADOW)
    color = theme.color("text", 0.10)
    clear = QColor(color)
    clear.setAlphaF(0.0)
    shadow.setColorAt(0.0, color)
    shadow.setColorAt(1.0, clear)
    painter.fillRect(QRectF(rect.left(), rect.bottom(), rect.width(), PIN_SHADOW), shadow)


def paint_focus_rule(painter: QPainter, widget: QWidget, theme: Theme) -> None:
    """Accent rule on the right edge of ``widget``, next to the focused body."""
    area = QRectF(widget.width() - FOCUS_RULE, 0, FOCUS_RULE, widget.height())
    painter.fillRect(area, theme.color("accent"))


# --------------------------------------------------------------- widgets ----


class RowColumn(QWidget):
    """Row labels left of the body, following its vertical scroll bar.

    A click on a team header toggles it (``team_toggled``); a right click on a row asks
    for its menu (``row_menu_requested``); the wheel scrolls the body
    (``scroll_requested`` with the pixel delta). The right edge can be dragged between
    160 and 280 px (``width_changed``).
    """

    team_toggled = Signal(str)
    row_clicked = Signal(str, object)
    row_menu_requested = Signal(str, QPoint)
    scroll_requested = Signal(int)
    width_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model: GridModel | None = None
        self.y0 = 0.0
        self.hovered: str | None = None
        self.focused = False
        self._resizing = False
        self.setFixedWidth(ROW_COLUMN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMouseTracking(True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(ROW_COLUMN_WIDTH, 200)

    def set_model(self, model: GridModel | None) -> None:
        self.model = model
        self.update()

    def set_offset(self, y0: float) -> None:
        if y0 != self.y0:
            self.y0 = y0
            self.update()

    def set_column_width(self, width: int) -> int:
        width = min(max(width, MIN_ROW_COLUMN_WIDTH), MAX_ROW_COLUMN_WIDTH)
        if width != self.width():
            self.setFixedWidth(width)
            self.width_changed.emit(width)
        return width

    def set_hovered(self, row_id: str | None) -> None:
        if row_id != self.hovered:
            self.hovered = row_id
            self.update()

    def set_focused(self, focused: bool) -> None:  # noqa: FBT001
        """Show the accent rule while the body of the grid has the keyboard focus."""
        if focused != self.focused:
            self.focused = focused
            self.update()

    def row_at(self, y: float) -> RowLayout | None:
        """Row under the widget ``y``, the pinned « Non affecté » first."""
        if self.model is None:
            return None
        pinned = self.model.rows.get(UNASSIGNED_ROW_ID)
        if self.y0 > 0 and pinned is not None and 0 <= y < pinned.height:
            return pinned
        return self.model.rows.row_at(y + self.y0)

    # Events -----------------------------------------------------------

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        theme = current_theme()
        if self.model is None:
            painter.fillRect(self.rect(), theme.color("bg"))
        else:
            paint_row_column(
                painter,
                self.model,
                theme,
                y0=self.y0,
                height=self.height(),
                width=self.width(),
                hovered=self.hovered,
            )
        if self.focused:
            paint_focus_rule(painter, self, theme)
        painter.end()

    def _on_grip(self, x: float) -> bool:
        return x >= self.width() - RESIZE_GRIP

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        position = event.position()
        if self._resizing:
            self.set_column_width(round(position.x()))
            return
        grip = self._on_grip(position.x())
        row = self.row_at(position.y())
        self.setCursor(
            Qt.CursorShape.SizeHorCursor
            if grip
            else Qt.CursorShape.PointingHandCursor
            if row is not None and row.kind == "team"
            else Qt.CursorShape.ArrowCursor
        )
        label = self.model.label(row.resource_id) if self.model and row else None
        self.setToolTip(row_tooltip(label) if label is not None else "")
        self.set_hovered(row.resource_id if row is not None else None)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self.set_hovered(None)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        position = event.position()
        if self._on_grip(position.x()):
            self._resizing = True
            event.accept()
            return
        row = self.row_at(position.y())
        if row is None:
            return
        if row.kind == "team":
            self.team_toggled.emit(row.team_key)
        else:
            self.row_clicked.emit(row.resource_id, event.modifiers())
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._resizing = False
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802
        row = self.row_at(event.pos().y())
        if row is None:
            return
        self.row_menu_requested.emit(row.resource_id, event.globalPos())
        event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        delta = event.pixelDelta().y() or round(event.angleDelta().y() / 120 * 3 * 20)
        if delta:
            self.scroll_requested.emit(-delta)
        event.accept()


class RowCorner(QWidget):
    """Top-left corner above the row column: « ÉQUIPE · 14 »."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.count = 0
        self.focused = False
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    def set_count(self, count: int) -> None:
        if count != self.count:
            self.count = count
            self.update()

    def set_focused(self, focused: bool) -> None:  # noqa: FBT001
        if focused != self.focused:
            self.focused = focused
            self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        theme = current_theme()
        painter.fillRect(self.rect(), theme.color("bg"))
        fonts = RowFonts()
        painter.setFont(fonts.caps)
        painter.setPen(theme.color("neutral-800"))
        rect = QRectF(LEFT_PAD, 0, self.width() - LEFT_PAD, self.height())
        painter.drawText(
            rect,
            int(Qt.AlignmentFlag.AlignVCenter),
            f"{CORNER_TITLE.upper()} · {self.count}",
        )
        painter.setPen(make_pen(theme.color("text", 0.30)))
        right, bottom = self.width() - 0.5, self.height() - 0.5
        painter.drawLine(QLineF(right, 0, right, self.height()))
        painter.drawLine(QLineF(0, bottom, self.width(), bottom))
        if self.focused:
            paint_focus_rule(painter, self, theme)
        painter.end()
