"""Building blocks of the Industry look: blueprint frames, labels, buttons, banners."""

from __future__ import annotations

from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPointF,
    QRectF,
    QSignalBlocker,
    QSize,
    Qt,
)
from PySide6.QtGui import QFont, QPainter, QPaintEvent, QPen, QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QListView,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from projectflow.ui.theme import current_theme, icon_pixmap, logo_pixmap, set_icon, theme_notifier

MARK_GUTTER = 6
"""Space kept around a blueprint object so its corner marks can overhang the frame."""

_SPACED_ROLES = {"kicker": 1.1, "caps": 0.9, "num": 1.1}


def label(
    text: str = "",
    role: str | None = None,
    *,
    tone: str | None = None,
    wrap: bool = False,
    selectable: bool = False,
) -> QLabel:
    widget = QLabel(text.upper() if role in {"kicker", "caps"} else text)
    if role:
        widget.setProperty("role", role)
    if tone:
        widget.setProperty("tone", tone)
    if role in _SPACED_ROLES:
        font = widget.font()
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, _SPACED_ROLES[role])
        widget.setFont(font)
    widget.setWordWrap(wrap)
    if selectable:
        widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def set_role(widget: QWidget, role: str) -> None:
    widget.setProperty("role", role)
    _repolish(widget)


def set_tone(widget: QWidget, tone: str | None) -> None:
    widget.setProperty("tone", tone or "")
    _repolish(widget)


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def button(
    text: str = "",
    variant: str | None = None,
    *,
    icon: str | None = None,
    icon_color: str | None = None,
    size: str | None = None,
) -> QPushButton:
    widget = QPushButton(text)
    if variant:
        widget.setProperty("variant", variant)
    if size:
        widget.setProperty("size", size)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon:
        default_color = {"ghost": "accent-700", "danger": "err", "primary": "bg"}.get(
            variant or "",
            "text",
        )
        set_icon(widget, icon, icon_color or default_color)
    return widget


def rule(*, vertical: bool = False) -> QFrame:
    line = QFrame()
    line.setProperty("role", "vrule" if vertical else "rule")
    line.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    return line


def paint_corner_marks(painter: QPainter, rect: QRectF, color_role: str = "neutral-800") -> None:
    """Draw the four "+" registration marks centred on the corners of ``rect``."""
    pen = QPen(current_theme().color(color_role))
    pen.setWidthF(1.0)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    painter.setPen(pen)
    arm = 5.5
    for corner in (rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()):
        painter.drawLine(
            QPointF(corner.x() - arm, corner.y()), QPointF(corner.x() + arm, corner.y())
        )
        painter.drawLine(
            QPointF(corner.x(), corner.y() - arm), QPointF(corner.x(), corner.y() + arm)
        )


class BlueprintFrame(QFrame):
    """A square, hairline-bordered wireframe object with corner marks.

    The frame reserves a small gutter on every side so the marks can sit over
    its corners, like the Industry mockups, without being clipped.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        padding: tuple[int, int, int, int] = (20, 18, 20, 20),
        fill: str | None = None,
        border: str | None = None,
        marks: bool = True,
        layout: str = "v",
        spacing: int = 12,
    ) -> None:
        super().__init__(parent)
        self._fill = fill
        self._border = border
        self._marks = marks
        g = MARK_GUTTER
        left, top, right, bottom = padding
        box: QBoxLayout = QVBoxLayout(self) if layout == "v" else QHBoxLayout(self)
        box.setContentsMargins(g + left, g + top, g + right, g + bottom)
        box.setSpacing(spacing)
        self.box = box

    def set_fill(self, fill: str | None, border: str | None = None, *, marks: bool = True) -> None:
        self._fill = fill
        self._border = border
        self._marks = marks
        self.update()

    def frame_rect(self) -> QRectF:
        g = MARK_GUTTER
        return QRectF(self.rect()).adjusted(g + 0.5, g + 0.5, -g - 0.5, -g - 0.5)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        theme = current_theme()
        rect = self.frame_rect()
        if self._fill:
            fill = theme.color(self._fill, 0.06) if self._fill == "err" else theme.color(self._fill)
            painter.fillRect(rect, fill)
        border = theme.color(self._border) if self._border else theme.color("text", 0.30)
        pen = QPen(border)
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.drawRect(rect)
        if self._marks:
            paint_corner_marks(painter, rect)
        painter.end()
        super().paintEvent(event)


class PrimaryButton(QPushButton):
    """The one solid object on the board: accent fill, square corners, corner marks."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("variant", "primary")
        self.setProperty("blueprint", "true")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        g = MARK_GUTTER
        paint_corner_marks(painter, QRectF(self.rect()).adjusted(g, g, -g - 1, -g - 1))
        painter.end()


def section_heading(
    number: str,
    title: str,
    *,
    size: str = "h4",
    trailing: QWidget | None = None,
) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    if number:
        num = label(number, "num")
        layout.addWidget(num, 0, Qt.AlignmentFlag.AlignBaseline)
    layout.addWidget(label(title, size), 0, Qt.AlignmentFlag.AlignBaseline)
    layout.addStretch(1)
    if trailing is not None:
        layout.addWidget(trailing, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


def field(caption: str, control: QWidget | QLayout, *, help_text: str = "") -> QWidget:
    """Stack a caption over its control, as the mockups' ``.field`` does."""
    wrapper = QWidget()
    layout = QVBoxLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(5)
    caption_label = label(caption, "field")
    layout.addWidget(caption_label)
    if isinstance(control, QLayout):
        layout.addLayout(control)
    else:
        layout.addWidget(control)
    if help_text:
        layout.addWidget(label(help_text, "small", wrap=True))
    wrapper.caption_label = caption_label  # type: ignore[attr-defined]
    return wrapper


def hbox(*widgets: QWidget | int | None, spacing: int = 8, margins: int = 0) -> QHBoxLayout:
    """Row layout; an int adds a stretch with that factor, None is skipped."""
    layout = QHBoxLayout()
    layout.setContentsMargins(margins, margins, margins, margins)
    layout.setSpacing(spacing)
    for item in widgets:
        if item is None:
            continue
        if isinstance(item, int):
            layout.addStretch(item)
        else:
            layout.addWidget(item)
    return layout


class DotTag(QWidget):
    """Accent tag led by a small square status dot."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "tagbox")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 3, 10, 3)
        layout.setSpacing(6)
        dot = QFrame()
        dot.setProperty("role", "dot")
        dot.setFixedSize(6, 6)
        dot.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.text_label = label(text, "tag-text")
        layout.addWidget(dot)
        layout.addWidget(self.text_label)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def setText(self, text: str) -> None:  # noqa: N802
        self.text_label.setText(text)

    def text(self) -> str:
        return self.text_label.text()


class LogoLabel(QLabel):
    """The ProjectFlow mark, re-inked whenever the theme changes."""

    def __init__(self, size: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self._refresh()
        theme_notifier().changed.connect(self._refresh)

    def _refresh(self) -> None:
        self.setPixmap(logo_pixmap(self._size))


class IconLabel(QLabel):
    def __init__(
        self, name: str, color: str, size: int = 18, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._name = name
        self._color = color
        self._size = size
        self.setFixedSize(size, size)
        self._refresh()
        theme_notifier().changed.connect(self._refresh)

    def set_icon(self, name: str, color: str) -> None:
        self._name = name
        self._color = color
        self._refresh()

    def _refresh(self) -> None:
        self.setPixmap(icon_pixmap(self._name, current_theme().color(self._color), self._size))


class Spinner(QWidget):
    """Small circular activity indicator (the mockups' ``pfspin``)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(QSize(14, 14))
        self._angle = 0
        self._timer_id = 0

    def showEvent(self, event: object) -> None:  # noqa: N802
        if not self._timer_id:
            self._timer_id = self.startTimer(40)
        super().showEvent(event)  # type: ignore[arg-type]

    def hideEvent(self, event: object) -> None:  # noqa: N802
        if self._timer_id:
            self.killTimer(self._timer_id)
            self._timer_id = 0
        super().hideEvent(event)  # type: ignore[arg-type]

    def timerEvent(self, _event: object) -> None:  # noqa: N802
        self._angle = (self._angle + 18) % 360
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        theme = current_theme()
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        pen = QPen(theme.color("accent-300"))
        pen.setWidthF(1.5)
        painter.setPen(pen)
        painter.drawEllipse(rect)
        pen.setColor(theme.color("accent"))
        painter.setPen(pen)
        painter.drawArc(rect, -self._angle * 16, 90 * 16)
        painter.end()


class StatusBanner(BlueprintFrame):
    """Operation feedback strip: progress, success or error, with optional actions."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, padding=(16, 10, 16, 10), layout="h", spacing=14)
        self.kind = ""
        self.spinner = Spinner()
        self.icon_label = IconLabel("check-circle", "accent-700", 20)
        body = QVBoxLayout()
        body.setSpacing(6)
        self.message_label = label("", wrap=True, selectable=True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        body.addWidget(self.message_label)
        body.addWidget(self.progress)
        self.hint_label = label("", "small")
        self.box.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        self.box.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)
        self.box.addLayout(body, 1)
        self.box.addWidget(self.hint_label)
        self.action_row = QHBoxLayout()
        self.action_row.setSpacing(8)
        self.box.addLayout(self.action_row)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.hide()

    def add_action(self, text: str, kinds: tuple[str, ...]) -> QPushButton:
        action = button(text)
        action.setProperty("banner_kinds", ",".join(kinds))
        self.action_row.addWidget(action)
        return action

    def show_progress(self, message: str, hint: str = "") -> None:
        self._show("progress", message, hint)

    def show_success(self, message: str) -> None:
        self._show("success", message)

    def show_error(self, message: str) -> None:
        self._show("error", message)

    def clear(self) -> None:
        self.kind = ""
        self.hide()

    def _show(self, kind: str, message: str, hint: str = "") -> None:
        self.kind = kind
        self.message_label.setText(message)
        self.hint_label.setText(hint)
        self.hint_label.setVisible(bool(hint))
        self.spinner.setVisible(kind == "progress")
        self.progress.setVisible(kind == "progress")
        self.icon_label.setVisible(kind != "progress")
        if kind == "success":
            self.icon_label.set_icon("check-circle", "accent-700")
            self.set_fill("accent-100")
            set_tone(self.message_label, "success")
        elif kind == "error":
            self.icon_label.set_icon("alert", "err")
            self.set_fill("err", "err", marks=False)
            set_tone(self.message_label, "error")
        else:
            self.set_fill(None)
            set_tone(self.message_label, None)
        for index in range(self.action_row.count()):
            item = self.action_row.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                kinds = str(widget.property("banner_kinds") or "").split(",")
                widget.setVisible(kind in kinds)
        self.show()


class ElidedLabel(QLabel):
    """Single-line label that elides its text and shows the full value as a tooltip."""

    def __init__(
        self,
        text: str = "",
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle,
    ) -> None:
        super().__init__()
        self._full_text = ""
        self._mode = mode
        self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setText(text)

    def full_text(self) -> str:
        return self._full_text

    def setText(self, text: str) -> None:  # noqa: N802
        self._full_text = text
        self.setToolTip(text)
        self._refresh_elided_text()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        self._refresh_elided_text()
        super().resizeEvent(event)

    def _refresh_elided_text(self) -> None:
        width = max(40, self.width() - 4)
        super().setText(self.fontMetrics().elidedText(self._full_text, self._mode, width))


META_ROLE = Qt.ItemDataRole.UserRole + 1
"""Item data role holding the secondary text (size, date) of a file row."""


class FileItemDelegate(QStyledItemDelegate):
    """Paint file rows as the mockups do: name on the left, size and date on the right.

    ``style`` is ``"card"`` (framed row, optional radio dot) or ``"rail"``
    (accent bar on the left of the selected row). ``columns`` lays a wrapping
    list out as a grid of equal cells.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        style: str = "card",
        radio: bool = False,
        columns: int = 1,
    ) -> None:
        super().__init__(parent)
        self._style = style
        self._radio = radio
        self._columns = columns

    def sizeHint(  # noqa: N802
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> QSize:
        height = 40 if self._style == "card" else 34
        width = super().sizeHint(option, index).width()
        view = option.widget
        if self._columns > 1 and isinstance(view, QListView):
            spacing = view.spacing()
            available = view.viewport().width() - spacing * 2 * self._columns - 4
            width = max(120, available // self._columns)
        return QSize(width, height)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        theme = current_theme()
        rect = QRectF(option.rect).adjusted(0.5, 0.5, -0.5, -0.5)
        state = option.state
        selected = bool(state & QStyle.StateFlag.State_Selected)
        hovered = bool(state & QStyle.StateFlag.State_MouseOver)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected:
            painter.fillRect(rect, theme.color("accent-100"))
        elif hovered:
            painter.fillRect(rect, theme.color("text", 0.04))
        if self._style == "card":
            pen = QPen(theme.color("accent") if selected else theme.color("text", 0.30))
            painter.setPen(pen)
            painter.drawRect(rect)
        elif selected:
            painter.fillRect(
                QRectF(rect.left(), rect.top(), 2, rect.height()), theme.color("accent")
            )
        left = rect.left() + 10
        if self._radio:
            dot = QRectF(left, rect.center().y() - 7, 14, 14)
            pen = QPen(theme.color("accent"))
            pen.setWidthF(1.5)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(dot)
            if selected:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(theme.color("accent"))
                painter.drawEllipse(dot.adjusted(3.5, 3.5, -3.5, -3.5))
            left = dot.right() + 10
        meta = str(index.data(META_ROLE) or "")
        name = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        right = rect.right() - 10
        base_font = option.font
        if meta:
            meta_font = QFont(base_font)
            meta_font.setPixelSize(12)
            painter.setFont(meta_font)
            painter.setPen(theme.color("neutral-800"))
            meta_width = painter.fontMetrics().horizontalAdvance(meta)
            if meta_width < (right - left) * 0.55:
                painter.drawText(
                    QRectF(right - meta_width, rect.top(), meta_width, rect.height()),
                    int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                    meta,
                )
                right -= meta_width + 12
        name_font = QFont(base_font)
        name_font.setPixelSize(14)
        painter.setFont(name_font)
        painter.setPen(theme.color("text"))
        elided = painter.fontMetrics().elidedText(
            name,
            Qt.TextElideMode.ElideRight,
            max(10, int(right - left)),
        )
        painter.drawText(
            QRectF(left, rect.top(), right - left, rect.height()),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            elided,
        )
        painter.restore()


class CheckChip(QPushButton):
    """A compact toggle mirroring a (hidden) checkbox, including its enabled state."""

    def __init__(self, text: str, checkbox: QCheckBox, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self._checkbox = checkbox
        self.setProperty("variant", "chip")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(self._push)
        checkbox.toggled.connect(self._pull)
        checkbox.installEventFilter(self)
        self._pull()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._checkbox and event.type() == QEvent.Type.EnabledChange:
            self._pull()
        return super().eventFilter(watched, event)

    def _push(self, checked: bool) -> None:  # noqa: FBT001
        if self._checkbox.isChecked() != checked:
            self._checkbox.setChecked(checked)
        self._refresh_text()

    def _pull(self) -> None:
        self.setEnabled(self._checkbox.isEnabled())
        self.setToolTip(self._checkbox.toolTip())
        blocker = QSignalBlocker(self)
        self.setChecked(self._checkbox.isChecked())
        del blocker
        self._refresh_text()

    def _refresh_text(self) -> None:
        self.setText(f"{'✓' if self.isChecked() else '+'}  {self._text}")
