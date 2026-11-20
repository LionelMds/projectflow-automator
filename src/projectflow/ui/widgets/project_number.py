"""The dossier number field: « 20YY- » written in front, the number typed after it.

Only « XXXX » is typed: the year is looked up from the number (répertoire, project
folders) and replaces « 20YY ». Typing or pasting « 2025-4493 » forces the year;
Backspace at the start of the field, or emptying it, gives the year back to the lookup.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeyEvent, QResizeEvent
from PySide6.QtWidgets import QLabel, QLineEdit, QWidget

from projectflow.core.numero import ProjectNumber, parse_project_number
from projectflow.core.project_years import split_typed_number
from projectflow.exceptions import ProjectNumberError
from projectflow.ui.theme import current_theme, theme_notifier

YearLookup = Callable[[str], int | None]
"""Year of a number « 5102 », or None when nothing tells it."""

UNKNOWN_YEAR = "20YY-"
_MIN_ID_DIGITS = 3
# Border and left padding of the line edits in the theme (QLineEdit { padding: 6px 10px }).
_PREFIX_X = 11


class ProjectNumberEdit(QLineEdit):
    """One field for a dossier number, its year filled in from the number."""

    number_changed = Signal()
    year_lookup_requested = Signal(str)
    """No year found for this number yet: the répertoire can be read, then ``refresh_year``."""

    def __init__(self, parent: QWidget | None = None, *, allow_suffix: bool = True) -> None:
        super().__init__(parent)
        self._allow_suffix = allow_suffix
        self._lookup: YearLookup | None = None
        self._forced_year: int | None = None
        self._found_year: int | None = None
        self._found_for = ""
        self._updating = False
        self._asked: set[str] = set()
        self._prefix = QLabel(UNKNOWN_YEAR, self)
        self._prefix.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._prefix.setObjectName("projectNumberPrefix")
        self.setPlaceholderText("XXXX")
        self.setToolTip(
            "Tapez le numéro du dossier : l'année est retrouvée automatiquement. "
            "Tapez 2025-4493 pour imposer une autre année.",
        )
        self.textEdited.connect(self._on_edited)
        self.textChanged.connect(self._on_text_changed)
        theme_notifier().changed.connect(self._refresh_prefix)
        self._refresh_prefix()

    # -- reading -------------------------------------------------------------

    def year(self) -> int | None:
        """Year typed, else the one found; None while unknown."""
        return self._forced_year if self._forced_year is not None else self._found_year

    def year_is_forced(self) -> bool:
        return self._forced_year is not None

    def typed_text(self) -> str:
        """Return what is typed after the year: « 5102 », « 5102-2 »."""
        return self.text().strip()

    def project_id(self) -> str:
        return split_typed_number(self.typed_text()).project_id

    def subproject_id(self) -> str:
        return split_typed_number(self.typed_text()).subproject_id or ""

    def number_text(self) -> str:
        """« 2026-5102 » once the year is known, else what was typed, else ""."""
        typed = self.typed_text()
        year = self.year()
        if not typed or year is None:
            return typed
        return f"{year}-{typed}"

    def number(self) -> ProjectNumber:
        """Return the dossier number; raise ``ProjectNumberError`` while incomplete."""
        typed = self.typed_text()
        if not typed:
            raise ProjectNumberError("Saisissez un numéro de dossier (20YY-XXXX).")
        if self.year() is None:
            raise ProjectNumberError(
                f"Année du dossier {self.project_id() or typed} introuvable : tapez le numéro "
                "avec son année (20YY-XXXX).",
            )
        return parse_project_number(self.number_text())

    # -- writing -------------------------------------------------------------

    def set_year_lookup(self, lookup: YearLookup | None) -> None:
        self._lookup = lookup
        self._asked.clear()
        self._refresh_year()

    def set_number(self, text: str, *, force_year: bool = False) -> None:
        """Show ``text`` (« 2026-5102 », « 5102 »); its year is kept only when forced.

        A full number coming from the répertoire or a fiche is exact: its year is
        shown as found, and stays when the number is then retyped.
        """
        typed = split_typed_number(text)
        self._forced_year = typed.year if force_year else None
        self._found_year = None if force_year else typed.year
        self._found_for = typed.project_id
        self._updating = True
        try:
            super().setText(self._rest_text(text, typed.year))
        finally:
            self._updating = False
        self._refresh_year()
        self.number_changed.emit()

    def setText(self, text: str | None) -> None:  # noqa: N802 - Qt name, as set_number
        """Show ``text`` as ``set_number`` does: a full number puts its year in front."""
        self.set_number(text or "")

    def set_year(self, year: int | None) -> None:
        """Impose ``year`` (None gives it back to the lookup)."""
        self._forced_year = year
        self._refresh_year()

    def refresh_year(self) -> None:
        """Look the year up again (the répertoire was read meanwhile)."""
        self._asked.clear()
        self._refresh_year()

    def clear(self) -> None:
        self._forced_year = None
        self._found_year = None
        self._found_for = ""
        super().clear()

    # -- events --------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if (
            event.key() == Qt.Key.Key_Backspace
            and self.cursorPosition() == 0
            and not self.hasSelectedText()
            and self._forced_year is not None
        ):
            self._forced_year = None
            self._refresh_year()
            return
        super().keyPressEvent(event)

    def event(self, event: QEvent) -> bool:
        if event.type() in {QEvent.Type.FontChange, QEvent.Type.StyleChange}:
            self._place_prefix()
        return super().event(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._place_prefix()

    # -- internals -----------------------------------------------------------

    def _on_text_changed(self, _text: str) -> None:
        if not self._updating:
            self._refresh_year()

    def _on_edited(self, text: str) -> None:
        typed = split_typed_number(text)
        if typed.year is not None:
            # « 2025-4493 » typed or pasted: the year moves in front, the number stays.
            self._forced_year = typed.year
            rest = self._rest_text(text, typed.year)
            self._updating = True
            try:
                super().setText(rest)
            finally:
                self._updating = False
            self.setCursorPosition(len(rest))
        elif not text.strip():
            self._forced_year = None
        self._refresh_year()
        self.number_changed.emit()

    def _rest_text(self, text: str, year: int | None) -> str:
        typed = split_typed_number(text)
        if year is None or typed.year is None:
            rest = text.strip()
        else:
            rest = typed.project_id
            if typed.subproject_id:
                rest = f"{rest}-{typed.subproject_id}"
        if not self._allow_suffix:
            rest = rest.split("-", 1)[0]
        return rest

    def _refresh_year(self) -> None:
        project_id = self.project_id()
        before = self._found_year
        if project_id != self._found_for:
            # Another number: the year found for the previous one no longer holds.
            self._found_for = project_id
            self._found_year = None
        if self._forced_year is None and len(project_id) >= _MIN_ID_DIGITS and project_id.isdigit():
            found = self._lookup(project_id) if self._lookup is not None else None
            if found is not None:
                self._found_year = found
            elif self._found_year is None and project_id not in self._asked:
                self._asked.add(project_id)
                self.year_lookup_requested.emit(project_id)
        if self._found_year != before:
            self.number_changed.emit()
        self._refresh_prefix()

    def _refresh_prefix(self) -> None:
        year = self.year()
        tokens = current_theme().tokens
        self._prefix.setText(f"{year}-" if year is not None else UNKNOWN_YEAR)
        color = tokens["text"] if year is not None else tokens["neutral-700"]
        weight = "600" if self._forced_year is not None else "400"
        self._prefix.setStyleSheet(
            f"color: {color}; font-weight: {weight}; background: transparent;"
        )
        if self._forced_year is not None:
            self._prefix.setToolTip(
                "Année imposée : Retour arrière au début du champ pour la retirer."
            )
        elif year is not None:
            self._prefix.setToolTip("Année retrouvée d'après le numéro.")
        else:
            self._prefix.setToolTip("")
        self._place_prefix()

    def _place_prefix(self) -> None:
        self._prefix.setFont(self.font())
        self._prefix.adjustSize()
        width = self._prefix.sizeHint().width()
        # QLineEdit keeps 2 px before its text: the number follows the year closely.
        self.setTextMargins(max(width - 2, 0), 0, 0, 0)
        self._prefix.move(_PREFIX_X, (self.height() - self._prefix.height()) // 2)
