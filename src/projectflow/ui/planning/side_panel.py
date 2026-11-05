"""Right side panel of the Planning tab: « À planifier » and « Détail » on two faces.

The panel folds into a 32 px rail that reads « À PLANIFIER · 6 » from top to bottom
(Ctrl+B is handled by the tab, which calls ``toggle``). Its left edge is a grip that
resizes it; ``width_changed`` tells the tab the width to keep in the poste settings once
the grip is released. A bar of the grid dropped on the rail or on the « Détail » face
goes back to « À planifier », like a drop on the cards.
"""

from __future__ import annotations

from typing import Literal

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QEnterEvent,
    QFocusEvent,
    QFont,
    QFontMetricsF,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QHBoxLayout,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from projectflow.core.planning.poste_config import (
    DEFAULT_PANEL_WIDTH,
    MAX_PANEL_WIDTH,
    MIN_PANEL_WIDTH,
)
from projectflow.ui.planning.backlog import BacklogPanel, DropOverlay
from projectflow.ui.planning.detail import DetailPanel
from projectflow.ui.planning.grid import parse_task_mime
from projectflow.ui.theme import Theme, body_font, current_theme, theme_notifier

SideFace = Literal["backlog", "detail"]
FACES: tuple[SideFace, ...] = ("backlog", "detail")

RAIL_WIDTH = 32
TAB_HEIGHT = 40
TAB_BORDER = 2
BADGE_PADDING_X = 6
BADGE_HEIGHT = 16
BADGE_GAP = 7
FOLD_BUTTON_WIDTH = 34
CHEVRON_SIZE = 8
CHEVRON_GAP = 4
GRIP_WIDTH = 5
RAIL_TEXT_TOP = 44
RAIL_LETTER_SPACING = 1.4
NEW_DOT_SIZE = 6
PANEL_MIN_WIDTH = max(MIN_PANEL_WIDTH, 260)
PANEL_MAX_WIDTH = min(MAX_PANEL_WIDTH, 520)
"""Widths the grip allows; the poste settings accept a wider range, clamped here."""

BACKLOG_TITLE = "À planifier"
DETAIL_TITLE = "Détail"
FOLD_TOOLTIP = "Replier le panneau (Ctrl+B)"
KEYBOARD_REASONS = (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
UNFOLD_TOOLTIP = "Déplier le panneau (Ctrl+B)"


def clamp_width(width: int) -> int:
    return min(max(width, PANEL_MIN_WIDTH), PANEL_MAX_WIDTH)


def backlog_tab_text(count: int) -> str:
    """``À planifier (6)``: the text of the tab for screen readers and tests."""
    return f"{BACKLOG_TITLE} ({count})"


def rail_text(count: int) -> str:
    """``À PLANIFIER · 6``: the vertical text of the folded panel."""
    return f"{BACKLOG_TITLE.upper()} · {count}"


def _chevron(painter: QPainter, center: QPointF, *, pointing_right: bool, theme: Theme) -> None:
    """Draw a double chevron « » » (or « « ») centred on ``center``."""
    pen = QPen(theme.color("neutral-800"))
    pen.setWidthF(1.4)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    half = CHEVRON_SIZE / 2
    direction = 1 if pointing_right else -1
    for offset in (-CHEVRON_GAP / 2, CHEVRON_GAP / 2):
        x = center.x() + offset - direction * half / 2
        path = QPainterPath()
        path.moveTo(x, center.y() - half)
        path.lineTo(x + direction * half, center.y())
        path.lineTo(x, center.y() + half)
        painter.drawPath(path)


# --------------------------------------------------------------- widgets ----


class _PaintedButton(QAbstractButton):
    """Button drawn by hand, with a hover tint and a focus frame for the keyboard only."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hovered = False
        self._keyboard_focus = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        self._keyboard_focus = event.reason() in KEYBOARD_REASONS
        super().focusInEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        self._keyboard_focus = False
        super().focusOutEvent(event)

    def _paint_focus(self, painter: QPainter, rect: QRectF) -> None:
        if self._keyboard_focus and self.hasFocus():
            pen = QPen(current_theme().color("accent"))
            pen.setWidthF(1.0)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)


class _FaceTab(_PaintedButton):
    """One face of the panel: its title, a count badge, an accent rule when shown."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title = title
        self.count: int | None = None
        self.setCheckable(True)
        self.setText(title)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(TAB_HEIGHT)

    def set_count(self, count: int | None) -> None:
        self.count = count
        text = self.title if count is None else backlog_tab_text(count)
        self.setText(text)
        self.setAccessibleName(text)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        width = QFontMetricsF(self._font()).horizontalAdvance(self.title) + 2 * BADGE_PADDING_X
        if self.count is not None:
            width += BADGE_GAP + self._badge_width()
        return QSize(round(width) + 16, TAB_HEIGHT)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        theme = current_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        if self._hovered and not self.isChecked():
            painter.fillRect(rect, theme.color("text", 0.05))
        font = self._font()
        metrics = QFontMetricsF(font)
        text_width = metrics.horizontalAdvance(self.title)
        badge_width = self._badge_width() if self.count is not None else 0.0
        total = text_width + (BADGE_GAP + badge_width if self.count is not None else 0.0)
        left = rect.center().x() - total / 2
        painter.setFont(font)
        painter.setPen(theme.color("text") if self.isChecked() else theme.color("neutral-800"))
        painter.drawText(
            QRectF(left, rect.top(), text_width + 1, rect.height() - TAB_BORDER),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self.title,
        )
        if self.count is not None:
            badge = QRectF(
                left + text_width + BADGE_GAP,
                rect.center().y() - BADGE_HEIGHT / 2 - TAB_BORDER / 2,
                badge_width,
                BADGE_HEIGHT,
            )
            painter.fillRect(badge, theme.color("accent-100"))
            painter.setFont(body_font(11, QFont.Weight.DemiBold))
            painter.setPen(theme.color("accent-800"))
            painter.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), str(self.count))
        if self.isChecked():
            painter.fillRect(
                QRectF(rect.left(), rect.bottom() - TAB_BORDER, rect.width(), TAB_BORDER),
                theme.color("accent"),
            )
        self._paint_focus(painter, rect.adjusted(1.5, 1.5, -1.5, -TAB_BORDER - 1.5))
        painter.end()

    def _badge_width(self) -> float:
        font = body_font(11, QFont.Weight.DemiBold)
        return QFontMetricsF(font).horizontalAdvance(str(self.count or 0)) + 2 * BADGE_PADDING_X

    @staticmethod
    def _font() -> QFont:
        return body_font(15, QFont.Weight.Medium)


class _FoldButton(_PaintedButton):
    """« » » at the end of the tabs: folds the panel into its rail."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(FOLD_BUTTON_WIDTH, TAB_HEIGHT)
        self.setToolTip(FOLD_TOOLTIP)
        self.setAccessibleName("Replier le panneau")

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        theme = current_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        if self._hovered or self.isDown():
            painter.fillRect(rect, theme.color("text", 0.07))
        _chevron(painter, rect.center(), pointing_right=True, theme=theme)
        self._paint_focus(painter, rect.adjusted(1.5, 1.5, -1.5, -1.5))
        painter.end()


class _Rail(QWidget):
    """Folded panel: « À PLANIFIER · N » written downwards; a click unfolds it."""

    clicked = Signal()
    dropped = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.count = 0
        self.has_new = False
        self._hovered = False
        self._drop_target = False
        self.setFixedWidth(RAIL_WIDTH)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(UNFOLD_TOOLTIP)
        self.setAccessibleName("Déplier le panneau « À planifier »")

    def set_count(self, count: int) -> None:
        self.count = count
        self.setAccessibleDescription(rail_text(count))
        self.update()

    def set_new(self, *, has_new: bool) -> None:
        self.has_new = has_new
        self.update()

    def text(self) -> str:
        return rail_text(self.count)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if parse_task_mime(event.mimeData()) is None:
            event.ignore()
            return
        self._drop_target = True
        self.update()
        event.acceptProposedAction()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        event.acceptProposedAction()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # noqa: N802
        self._drop_target = False
        self.update()
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        payload = parse_task_mime(event.mimeData())
        self._drop_target = False
        self.update()
        if payload is None:
            event.ignore()
            return
        event.acceptProposedAction()
        self.dropped.emit(payload.task_id)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        theme = current_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rect = QRectF(self.rect())
        painter.fillRect(rect, theme.color("bg"))
        if self._drop_target:
            painter.fillRect(rect, theme.color("accent", 0.18))
        elif self._hovered:
            painter.fillRect(rect, theme.color("text", 0.05))
        pen = QPen(theme.color("accent") if self._drop_target else theme.color("text", 0.18))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.drawLine(QPointF(0.5, rect.top()), QPointF(0.5, rect.bottom()))
        _chevron(
            painter,
            QPointF(rect.center().x(), TAB_HEIGHT / 2),
            pointing_right=False,
            theme=theme,
        )
        font = body_font(11, QFont.Weight.DemiBold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, RAIL_LETTER_SPACING)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        painter.save()
        painter.translate(rect.center().x() + metrics.ascent() / 2 - 1, RAIL_TEXT_TOP)
        painter.rotate(90)
        title = f"{BACKLOG_TITLE.upper()} · "
        painter.setPen(theme.color("neutral-800"))
        painter.drawText(QPointF(0, 0), title)
        count_x = metrics.horizontalAdvance(title)
        painter.setPen(theme.color("accent-800"))
        painter.drawText(QPointF(count_x, 0), str(self.count))
        if self.has_new:
            end = count_x + metrics.horizontalAdvance(str(self.count)) + 6
            painter.fillRect(
                QRectF(end, -metrics.ascent() / 2 - NEW_DOT_SIZE / 2, NEW_DOT_SIZE, NEW_DOT_SIZE),
                theme.color("accent"),
            )
        painter.restore()
        painter.end()


class _Grip(QWidget):
    """Left edge of the open panel: drag it to make the panel wider or narrower."""

    pressed = Signal()
    moved = Signal(int)
    released = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._press_x: int | None = None
        self.setFixedWidth(GRIP_WIDTH)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setToolTip("Glisser pour élargir ou rétrécir le panneau")

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_x = round(event.globalPosition().x())
            self.pressed.emit()
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._press_x is None:
            super().mouseMoveEvent(event)
            return
        # Dragging to the left widens the panel, which sits on the right of the tab.
        self.moved.emit(self._press_x - round(event.globalPosition().x()))
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._press_x is not None and event.button() == Qt.MouseButton.LeftButton:
            self._press_x = None
            self.released.emit()
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        theme = current_theme()
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.color("bg"))
        color = theme.color("accent") if self._press_x is not None else theme.color("text", 0.18)
        painter.fillRect(QRectF(0, 0, 1, self.height()), color)
        painter.end()


# ----------------------------------------------------------------- panel ----


class SidePanel(QWidget):
    """Right panel of the Planning tab, with its « À planifier » and « Détail » faces.

    Put it in a layout beside the grid: it sets its own width, open or folded.
    """

    face_changed = Signal(str)
    collapsed_changed = Signal(bool)
    width_changed = Signal(int)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        backlog: BacklogPanel | None = None,
        detail: DetailPanel | None = None,
        width: int = DEFAULT_PANEL_WIDTH,
        collapsed: bool = False,
    ) -> None:
        super().__init__(parent)
        self.backlog = backlog or BacklogPanel()
        self.detail = detail or DetailPanel()
        # The tabs name the faces and lead back to « À planifier ».
        self.backlog.set_title_visible(visible=False)
        self.detail.set_header_visible(visible=False)
        self._face: SideFace = "backlog"
        self._collapsed = False
        self._width = clamp_width(width)
        self._press_width = self._width
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        self.backlog_tab = _FaceTab(BACKLOG_TITLE)
        self.detail_tab = _FaceTab(DETAIL_TITLE)
        self.fold_button = _FoldButton()
        # Bound methods, not lambdas: a lambda holding ``self`` would keep a panel
        # without parent alive for ever.
        self.backlog_tab.clicked.connect(self.show_backlog)
        self.detail_tab.clicked.connect(self._on_detail_tab)
        self.fold_button.clicked.connect(self.toggle)
        tabs = QHBoxLayout()
        tabs.setContentsMargins(0, 0, 0, 0)
        tabs.setSpacing(0)
        tabs.addWidget(self.backlog_tab, 1)
        tabs.addWidget(self.detail_tab, 1)
        tabs.addWidget(self.fold_button)
        self.tabs_box = QWidget()
        self.tabs_box.setLayout(tabs)
        self.tabs_box.setFixedHeight(TAB_HEIGHT)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.backlog)
        self.stack.addWidget(self.detail)

        self.open_box = QWidget()
        column = QVBoxLayout(self.open_box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.tabs_box)
        column.addWidget(self.stack, 1)

        self.grip = _Grip()
        self.grip.pressed.connect(self._on_grip_pressed)
        self.grip.moved.connect(self._on_grip_moved)
        self.grip.released.connect(self._on_grip_released)
        self.rail = _Rail()
        self.rail.clicked.connect(self._on_rail_clicked)
        self.rail.dropped.connect(self.backlog.drop_task)
        self.rail.hide()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.grip)
        root.addWidget(self.open_box, 1)
        root.addWidget(self.rail)

        self.overlay = DropOverlay(self)
        self.overlay.dropped.connect(self.backlog.drop_task)

        self.backlog.count_changed.connect(self._on_count)
        self.detail.back_requested.connect(self.show_backlog)
        theme_notifier().changed.connect(self.update)
        self._on_count(self.backlog.count())
        self._select_tab()
        self.setFixedWidth(self._width)
        if collapsed:
            self.set_collapsed(True)

    # -- public API -----------------------------------------------------------

    def face(self) -> SideFace:
        return self._face

    def set_face(self, face: SideFace) -> None:
        if face not in FACES:
            return
        if face != self._face:
            if self._face == "detail":
                self.detail.flush()
            self._face = face
            self.stack.setCurrentWidget(self.backlog if face == "backlog" else self.detail)
            self.face_changed.emit(face)
        self._select_tab()

    def show_backlog(self) -> None:
        self.set_face("backlog")

    def show_detail(self, *, expand: bool = True) -> None:
        """Show the « Détail » face, unfolding the panel unless ``expand`` is False."""
        self.set_face("detail")
        if expand:
            self.set_collapsed(False)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:  # noqa: FBT001
        """Fold the panel into its 32 px rail, or unfold it at its last width."""
        if collapsed == self._collapsed:
            return
        if collapsed:
            self.detail.flush()
        self._collapsed = collapsed
        self.open_box.setVisible(not collapsed)
        self.grip.setVisible(not collapsed)
        self.rail.setVisible(collapsed)
        self.setFixedWidth(RAIL_WIDTH if collapsed else self._width)
        self.overlay.hide()
        self.collapsed_changed.emit(collapsed)

    def toggle(self) -> None:
        self.set_collapsed(not self._collapsed)

    def panel_width(self) -> int:
        """Width of the open panel (also while folded: the width it unfolds to)."""
        return self._width

    def set_panel_width(self, width: int) -> None:
        """Set the width of the open panel, kept between the limits of the grip."""
        self._width = clamp_width(width)
        if not self._collapsed:
            self.setFixedWidth(self._width)

    def backlog_tab_text(self) -> str:
        return self.backlog_tab.text()

    def rail_text(self) -> str:
        return self.rail.text()

    def set_new_cards(self, *, has_new: bool) -> None:
        """Dot the rail while cards added on another poste were not seen yet."""
        self.rail.set_new(has_new=has_new)

    # -- slots ------------------------------------------------------------------

    def _on_count(self, count: int) -> None:
        self.backlog_tab.set_count(count)
        self.rail.set_count(count)

    def _on_detail_tab(self) -> None:
        self.show_detail(expand=False)

    def _on_rail_clicked(self) -> None:
        self.set_collapsed(False)

    def _select_tab(self) -> None:
        self.backlog_tab.setChecked(self._face == "backlog")
        self.detail_tab.setChecked(self._face == "detail")

    def _on_grip_pressed(self) -> None:
        # Measured from the width at the press: ``set_panel_width`` may have changed it.
        self._press_width = self._width

    def _on_grip_moved(self, delta: int) -> None:
        self.set_panel_width(self._press_width + delta)

    def _on_grip_released(self) -> None:
        if self._width != self._press_width:
            self.width_changed.emit(self._width)
        self._press_width = self._width

    # -- events -----------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        # Only the « Détail » face lands here: the cards and the rail handle their own drops.
        payload = parse_task_mime(event.mimeData())
        if (
            payload is None
            or self._collapsed
            or self._face != "detail"
            or not self.backlog.accepts_task(payload.task_id)
        ):
            event.ignore()
            return
        self.show_drop_overlay()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # noqa: N802
        # The drag may move onto the overlay, or leave or be cancelled before it gets there.
        self.overlay.release_later()
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        payload = parse_task_mime(event.mimeData())
        self.overlay.hide()
        if payload is None:
            event.ignore()
            return
        event.acceptProposedAction()
        self.backlog.drop_task(payload.task_id)

    def show_drop_overlay(self) -> None:
        """Cover the open face with « Déposer ici pour renvoyer dans « À planifier » »."""
        top = self.tabs_box.height()
        self.overlay.setGeometry(self.open_box.x(), top, self.open_box.width(), self.height() - top)
        self.overlay.show()
        self.overlay.raise_()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.overlay.isVisible():
            self.show_drop_overlay()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        theme = current_theme()
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.color("bg"))
        if not self._collapsed:
            pen = QPen(theme.color("text", 0.18))
            pen.setWidthF(1.0)
            painter.setPen(pen)
            bottom = TAB_HEIGHT - 0.5
            painter.drawLine(QPointF(GRIP_WIDTH, bottom), QPointF(self.width(), bottom))
        painter.end()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(RAIL_WIDTH if self._collapsed else self._width, 600)
