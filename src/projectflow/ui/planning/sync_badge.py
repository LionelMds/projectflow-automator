"""Synchronisation state of the planning, as a small tag of the toolbar.

The badge reads a ``SyncStatus`` (or anything with the same fields): green when the last
exchange succeeded (« À jour · il y a 2 s », refreshed every second), amber while changes
are being sent, red when the planning is read-only or cannot reach Microsoft. With the
local store it only says that the planning stays on this poste. A problem state is an
action (sign in again, say): the badge then takes the focus with Tab and Espace or Entrée
emits ``clicked`` like a click.

``feedback_color`` gives the green / amber / red inks and grounds shared by the planning
widgets until the theme carries them as tokens.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Final, Literal, Protocol

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QHideEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QShowEvent,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from projectflow.ui.planning.colors import ok_color, warn_color
from projectflow.ui.theme import body_font, current_theme, theme_notifier

FeedbackTone = Literal["ok", "warn", "err", "neutral", "remote"]

# Grounds (light, dark) of the tones; "remote" marks what another poste did.
_GROUNDS: Final[Mapping[str, tuple[str, str]]] = {
    "ok": ("#e3f1e6", "#1c3324"),
    "warn": ("#fcefd9", "#3a2a12"),
    "err": ("#fde8e5", "#3d1d19"),
    "remote": ("#fcefd9", "#3a2a12"),
}
REMOTE_INK: Final = ("#c07a12", "#e3a447")

BADGE_HEIGHT: Final = 26
BADGE_PADDING: Final = 10
DOT_SIZE: Final = 7
DOT_GAP: Final = 8
TEXT_PX: Final = 12
REFRESH_MS: Final = 1000
FOCUS_RING: Final = 2.0
SECONDS_PER_MINUTE: Final = 60
SECONDS_PER_HOUR: Final = 3600

LOCAL_TEXT: Final = "Planning local (ce poste)"
WAITING_TEXT: Final = "Synchronisation…"
SENDING_TEXT: Final = "Envoi…"
UP_TO_DATE_TEXT: Final = "À jour"
_PROBLEM_TEXTS: Final[Mapping[str, str]] = {
    "offline": "Hors ligne · consultation seule",
    "auth_required": "Reconnexion Microsoft requise",
    "forbidden": "Autorisation Microsoft manquante",
    "error": "Erreur de synchronisation",
}
_DEFAULT_TIPS: Final[Mapping[str, str]] = {
    "ok": "Planning partagé : les modifications des autres postes arrivent en direct.",
    "local": ("Planning enregistré sur ce poste uniquement : les autres postes ne le voient pas."),
    "sending": "Envoi des modifications du planning…",
    "offline": "Planning hors ligne : consultation seule.",
    "auth_required": "Connexion Microsoft requise pour synchroniser le planning.",
    "forbidden": "Accès au planning refusé : autorisation de l'administrateur Microsoft requise.",
    "error": "La synchronisation du planning a rencontré une erreur.",
}


class SyncStatusLike(Protocol):
    """What the badge reads from ``projectflow.core.planning.sync.SyncStatus``."""

    @property
    def state(self) -> str: ...

    @property
    def pending(self) -> int: ...

    @property
    def last_success(self) -> float | None: ...

    @property
    def message(self) -> str: ...


def feedback_color(tone: FeedbackTone, *, background: bool = False) -> QColor:
    """Ink (or ground) of a feedback tone in the current theme."""
    theme = current_theme()
    dark = theme.is_dark
    if tone == "neutral":
        return theme.color("neutral-100" if background else "neutral-600")
    if background:
        return QColor(_GROUNDS[tone][1 if dark else 0])
    inks = {
        "ok": ok_color(dark=dark),
        "warn": warn_color(dark=dark),
        "err": theme.tokens["err"],
        "remote": REMOTE_INK[1 if dark else 0],
    }
    return QColor(inks[tone])


def relative_age(seconds: float) -> str:
    """``2.4`` -> ``il y a 2 s``, ``150`` -> ``il y a 2 min``."""
    elapsed = max(0, int(seconds))
    if elapsed < 1:
        return "à l'instant"
    if elapsed < SECONDS_PER_MINUTE:
        return f"il y a {elapsed} s"
    if elapsed < SECONDS_PER_HOUR:
        return f"il y a {elapsed // SECONDS_PER_MINUTE} min"
    return f"il y a {elapsed // SECONDS_PER_HOUR} h"


def badge_text(
    status: SyncStatusLike | None,
    *,
    local: bool,
    now: float,
) -> tuple[str, FeedbackTone]:
    """Text and tone of the badge for ``status`` at ``now`` (``monotonic()`` time)."""
    state = status.state if status is not None else "ok"
    problem = _PROBLEM_TEXTS.get(state)
    if problem is not None:
        return problem, "err"
    if local:
        return LOCAL_TEXT, "neutral"
    pending = status.pending if status is not None else 0
    if state == "sending" or pending:
        return (f"{SENDING_TEXT} ({pending})" if pending else SENDING_TEXT), "warn"
    if status is None or status.last_success is None:
        return WAITING_TEXT, "neutral"
    return f"{UP_TO_DATE_TEXT} · {relative_age(now - status.last_success)}", "ok"


def badge_tooltip(status: SyncStatusLike | None, *, local: bool) -> str:
    state = status.state if status is not None else "ok"
    if status is not None and status.message:
        return status.message
    if local and state not in _PROBLEM_TEXTS:
        return _DEFAULT_TIPS["local"]
    return _DEFAULT_TIPS.get(state, _DEFAULT_TIPS["ok"])


class SyncBadge(QWidget):
    """Dot and text telling whether the planning is shared, being sent or read-only."""

    _ACTIVATION_KEYS: Final = frozenset({Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter})

    clicked = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        local: bool = False,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(parent)
        self._status: SyncStatusLike | None = None
        self._local = local
        self._monotonic = monotonic
        self._text = ""
        self._tone: FeedbackTone = "neutral"
        self._font = body_font(TEXT_PX)
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(BADGE_HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        theme_notifier().changed.connect(self.update)
        self.refresh()

    # ------------------------------------------------------------- state ----

    def set_status(self, status: SyncStatusLike | None) -> None:
        self._status = status
        self.refresh()

    def set_local(self, *, local: bool) -> None:
        """Say whether the planning lives in the local store of this poste."""
        self._local = local
        self.refresh()

    def status(self) -> SyncStatusLike | None:
        return self._status

    def text(self) -> str:
        return self._text

    def tone(self) -> FeedbackTone:
        return self._tone

    def is_actionable(self) -> bool:
        """Whether a click does something: a problem state (reconnect, retry)."""
        return self._status is not None and self._status.state in _PROBLEM_TEXTS

    def refresh(self) -> None:
        text, tone = badge_text(self._status, local=self._local, now=self._monotonic())
        tooltip = badge_tooltip(self._status, local=self._local)
        self.setToolTip(tooltip)
        actionable = self.is_actionable()
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if actionable else Qt.CursorShape.ArrowCursor
        )
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus if actionable else Qt.FocusPolicy.NoFocus)
        if self.accessibleDescription() != tooltip:
            self.setAccessibleDescription(tooltip)
        if (text, tone) == (self._text, self._tone):
            return
        self.setAccessibleName(f"Synchronisation du planning : {text}")
        resized = len(text) != len(self._text)
        self._text, self._tone = text, tone
        if resized:
            self.updateGeometry()
        self.update()

    # ---------------------------------------------------------------- Qt ----

    def sizeHint(self) -> QSize:  # noqa: N802
        metrics = QFontMetrics(self._font)
        width = 2 * BADGE_PADDING + DOT_SIZE + DOT_GAP + metrics.horizontalAdvance(self._text)
        return QSize(width, BADGE_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        self._timer.start()
        self.refresh()
        super().showEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in self._ACTIVATION_KEYS and self.is_actionable():
            event.accept()
            self.clicked.emit()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        theme = current_theme()
        rect = QRectF(self.rect())
        painter.fillRect(rect, self._ground())
        dot = QRectF(BADGE_PADDING, (rect.height() - DOT_SIZE) / 2, DOT_SIZE, DOT_SIZE)
        painter.fillRect(dot, feedback_color(self._tone))
        painter.setFont(self._font)
        ink = theme.color("err-ink") if self._tone == "err" else theme.color("text")
        if self._tone == "warn":
            ink = feedback_color("warn")
        painter.setPen(ink)
        left = dot.right() + DOT_GAP
        painter.drawText(
            QRectF(left, 0, rect.width() - left - BADGE_PADDING / 2, rect.height()),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self._text,
        )
        if self.hasFocus():
            pen = QPen(theme.color("accent"))
            pen.setWidthF(FOCUS_RING)
            pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            painter.setPen(pen)
            inset = FOCUS_RING / 2
            painter.drawRect(rect.adjusted(inset, inset, -inset, -inset))
        painter.end()

    def _ground(self) -> QColor:
        if self._tone == "ok":
            return current_theme().color("accent-100")
        return feedback_color(self._tone, background=True)
