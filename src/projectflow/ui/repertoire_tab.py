from __future__ import annotations

from datetime import date
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QRectF,
    QRegularExpression,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from projectflow.core.repertoire_service import (
    NextAvailableProject,
    RepertoireRow,
    RepertoireSnapshot,
)
from projectflow.ui.theme import current_theme, icon, theme_notifier
from projectflow.ui.widgets.industry import PrimaryButton, button, field, label

HEADERS = ("No.", "Date", "Client", "Contact", "Désignation")
COLUMN_WIDTHS = (150, 112, 210, 190)
NEXT_AVAILABLE_ROLE = Qt.ItemDataRole.UserRole + 1
_EMPTY_INDEX = QModelIndex()


class RepertoireTableModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self._rows: list[RepertoireRow] = []
        self._original: dict[int, tuple[Any, ...]] = {}
        self._dirty_cells: set[tuple[int, int]] = set()
        self._next_row_index: int | None = None
        self._next_available: NextAvailableProject | None = None

    def rowCount(  # noqa: N802
        self,
        parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX,
    ) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(  # noqa: N802
        self,
        parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX,
    ) -> int:
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        return HEADERS[section].upper() if 0 <= section < len(HEADERS) else None

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        row = self._rows[index.row()]
        value = row.values[index.column()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return _display_value(value)
        if role == Qt.ItemDataRole.BackgroundRole:
            return self._background(row.row_index, index.column())
        if role == NEXT_AVAILABLE_ROLE:
            return row.row_index == self._next_row_index
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"Ligne Excel {row.row_index + 1}"
        return None

    def _background(self, row_index: int, column: int) -> QColor | None:
        theme = current_theme()
        if (row_index, column) in self._dirty_cells:
            return theme.color("accent-200")
        if row_index == self._next_row_index:
            return theme.color("accent-100", 0.7)
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() in range(1, len(HEADERS)):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(  # noqa: N802
        self,
        index: QModelIndex | QPersistentModelIndex,
        value: object,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if (
            not index.isValid()
            or role != Qt.ItemDataRole.EditRole
            or index.column() not in range(1, len(HEADERS))
        ):
            return False
        row = self._rows[index.row()]
        values = list(row.values)
        values[index.column()] = "" if value is None else str(value).strip()
        self._rows[index.row()] = RepertoireRow(row_index=row.row_index, values=tuple(values))
        original = self._original.get(row.row_index, row.values)
        cell_key = (row.row_index, index.column())
        if _display_value(values[index.column()]) == _display_value(original[index.column()]):
            self._dirty_cells.discard(cell_key)
        else:
            self._dirty_cells.add(cell_key)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole])
        return True

    def set_snapshot(self, snapshot: RepertoireSnapshot) -> None:
        self.beginResetModel()
        self._rows = list(snapshot.rows)
        self._original = {row.row_index: row.values for row in self._rows}
        self._dirty_cells.clear()
        self._next_row_index = (
            snapshot.next_available.row_index if snapshot.next_available is not None else None
        )
        self._next_available = snapshot.next_available
        self.endResetModel()

    def dirty_cell_count(self) -> int:
        return len(self._dirty_cells)

    def dirty_row_indexes(self) -> tuple[int, ...]:
        return tuple(sorted({row_index for row_index, _column in self._dirty_cells}))

    def discard_changes(self) -> None:
        if not self._dirty_cells:
            return
        self.beginResetModel()
        self._rows = [
            RepertoireRow(row_index=row.row_index, values=self._original[row.row_index])
            for row in self._rows
        ]
        self._dirty_cells.clear()
        self.endResetModel()

    def row_at(self, source_row: int) -> RepertoireRow | None:
        if not (0 <= source_row < len(self._rows)):
            return None
        return self._rows[source_row]

    def original_values(self, row_index: int) -> tuple[Any, ...] | None:
        return self._original.get(row_index)

    def original_rows(self) -> tuple[RepertoireRow, ...]:
        return tuple(
            RepertoireRow(row_index=row.row_index, values=self._original[row.row_index])
            for row in self._rows
        )

    def next_available(self) -> NextAvailableProject | None:
        return self._next_available

    def mark_saved(self, row_index: int, values: tuple[Any, ...]) -> None:
        for source_row, row in enumerate(self._rows):
            if row.row_index != row_index:
                continue
            updated = RepertoireRow(row_index=row_index, values=values)
            self._rows[source_row] = updated
            self._original[row_index] = values
            self._dirty_cells = {key for key in self._dirty_cells if key[0] != row_index}
            left = self.index(source_row, 0)
            right = self.index(source_row, len(HEADERS) - 1)
            self.dataChanged.emit(left, right)
            return

    def source_index_for_sheet_row(self, row_index: int) -> int | None:
        for source_row, row in enumerate(self._rows):
            if row.row_index == row_index:
                return source_row
        return None


class RepertoireDossierTab(QWidget):
    load_requested = Signal()
    save_requested = Signal(int, object, object)
    sync_project_requested = Signal()
    new_project_requested = Signal()
    open_project_requested = Signal()
    create_subproject_requested = Signal()
    duplicate_project_requested = Signal()
    delete_project_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._model = RepertoireTableModel()
        self._proxy = QSortFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy.setFilterKeyColumn(-1)
        self._loading = False
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top = QVBoxLayout()
        top.setContentsMargins(26, 22, 26, 12)
        top.setSpacing(12)
        heading = QHBoxLayout()
        heading.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.source_label = label("Répertoire chantier · Classeur Excel", "kicker")
        titles.addWidget(self.source_label)
        titles.addWidget(label("Répertoire chantier", "h2"))
        heading.addLayout(titles, 1)
        self.year_combo = QComboBox()
        self.year_combo.setEditable(True)
        year = field("Année", self.year_combo)
        year.setFixedWidth(112)
        heading.addWidget(year, 0, Qt.AlignmentFlag.AlignBottom)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Numéro, client, contact ou désignation")
        self.search_edit.setClearButtonEnabled(True)
        self._search_action = self.search_edit.addAction(
            icon("search", "neutral-700"),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        theme_notifier().changed.connect(
            lambda: self._search_action.setIcon(icon("search", "neutral-700")),
        )
        self.search_edit.textChanged.connect(self._filter_rows)
        search = field("Rechercher", self.search_edit)
        search.setFixedWidth(340)
        heading.addWidget(search, 0, Qt.AlignmentFlag.AlignBottom)
        # A first load and a refresh do the same read; the header only shows "Actualiser".
        self.load_button = button("Charger")
        self.load_button.clicked.connect(self.load_requested.emit)
        self.load_button.hide()
        self.refresh_button = button("Actualiser", icon="refresh")
        self.refresh_button.clicked.connect(self.load_requested.emit)
        heading.addWidget(self.load_button, 0, Qt.AlignmentFlag.AlignBottom)
        heading.addWidget(self.refresh_button, 0, Qt.AlignmentFlag.AlignBottom)
        top.addLayout(heading)

        status = QHBoxLayout()
        status.setSpacing(14)
        self.status_label = label("Chargez une année pour afficher le répertoire.", "muted")
        self.status_label.setWordWrap(True)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        status.addWidget(self.status_label, 1)
        status.addLayout(_legend("swatch-next", "Prochaine disponible"))
        status.addLayout(_legend("swatch-dirty", "Modifié, non enregistré"))
        top.addLayout(status)
        root.addLayout(top)

        self.table = QTableView()
        self.table.setModel(self._proxy)
        self.table.setItemDelegate(RepertoireRowDelegate(self.table))
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setSortingEnabled(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.doubleClicked.connect(lambda _index: self.open_project_requested.emit())
        self.table.selectionModel().selectionChanged.connect(
            lambda _selected, _deselected: self._update_action_states()
        )
        table_row = QHBoxLayout()
        table_row.setContentsMargins(26, 0, 26, 0)
        table_row.addWidget(self.table)
        root.addLayout(table_row, 1)

        footer = QWidget()
        footer.setProperty("role", "footer")
        actions = QHBoxLayout(footer)
        actions.setContentsMargins(26, 6, 20, 6)
        actions.setSpacing(8)
        self.new_project_button = button("Nouveau projet", icon="plus")
        self.new_project_button.clicked.connect(self.new_project_requested.emit)
        actions.addWidget(self.new_project_button)
        self.open_project_button = button("Charger le projet")
        self.open_project_button.clicked.connect(self.open_project_requested.emit)
        actions.addWidget(self.open_project_button)
        self.create_subproject_button = button("Créer sous-projet")
        self.create_subproject_button.clicked.connect(self.create_subproject_requested.emit)
        actions.addWidget(self.create_subproject_button)
        self.duplicate_project_button = button("Dupliquer")
        self.duplicate_project_button.clicked.connect(self.duplicate_project_requested.emit)
        actions.addWidget(self.duplicate_project_button)
        self.delete_project_button = button("Supprimer avec éléments liés", "danger", icon="trash")
        self.delete_project_button.setToolTip(
            "Supprimer le projet sélectionné après confirmation et libérer son numéro"
        )
        self.delete_project_button.clicked.connect(self.delete_project_requested.emit)
        actions.addWidget(self.delete_project_button)
        actions.addStretch(1)
        self.dirty_label = label("", "accent-text")
        actions.addWidget(self.dirty_label)
        self.discard_button = button("Annuler", "ghost")
        self.discard_button.setToolTip("Rétablir les valeurs lues dans le répertoire")
        self.discard_button.clicked.connect(self.discard_changes)
        actions.addWidget(self.discard_button)
        self.sync_project_button = button("Mettre à jour le projet")
        self.sync_project_button.setToolTip(
            "Mettre à jour la fiche et les intégrations configurées du projet sélectionné"
        )
        self.sync_project_button.clicked.connect(self.sync_project_requested.emit)
        actions.addWidget(self.sync_project_button)
        self.save_button = PrimaryButton("Enregistrer la ligne")
        self.save_button.clicked.connect(self._save_selected)
        actions.addWidget(self.save_button)
        root.addWidget(footer)

        self._model.dataChanged.connect(lambda *_args: self._refresh_dirty_state())
        self._model.modelReset.connect(self._refresh_dirty_state)
        self._update_action_states()
        self._refresh_dirty_state()

    def set_source(self, text: str) -> None:
        self.source_label.setText(text.upper())

    def discard_changes(self) -> None:
        self._model.discard_changes()
        self._update_action_states()

    def _refresh_dirty_state(self) -> None:
        count = self._model.dirty_cell_count()
        rows = self._model.dirty_row_indexes()
        numbers = []
        for row_index in rows:
            source_row = self._model.source_index_for_sheet_row(row_index)
            row = self._model.row_at(source_row) if source_row is not None else None
            if row is not None:
                numbers.append(_display_value(row.values[0]))
        plural = "s" if count > 1 else ""
        self.dirty_label.setText(
            f"{count} cellule{plural} modifiée{plural} · {', '.join(numbers)}" if count else "",
        )
        self.dirty_label.setVisible(bool(count))
        self.discard_button.setVisible(bool(count))
        self.save_button.setEnabled(bool(count) and not self._loading)

    def set_snapshot(self, snapshot: RepertoireSnapshot) -> None:
        self._model.set_snapshot(snapshot)
        self._resize_columns()
        self._position_near_next_available(snapshot)
        self._update_action_states()
        if snapshot.next_available is None:
            self.status_label.setText(
                f"{len(snapshot.rows)} lignes chargées. Aucune ligne disponible détectée."
            )
        else:
            row_number = snapshot.next_available.row_index + 1
            self.status_label.setText(
                f"{len(snapshot.rows)} lignes chargées. "
                f"Prochaine ligne disponible : {snapshot.next_available.number} "
                f"(ligne Excel {row_number}). Les colonnes A à E sont modifiables."
            )

    def set_loading(self, *, loading: bool) -> None:
        self._loading = loading
        self.load_button.setEnabled(not loading)
        self.refresh_button.setEnabled(not loading)
        self.save_button.setEnabled(not loading and self._model.dirty_cell_count() > 0)
        self.sync_project_button.setEnabled(not loading)
        self.open_project_button.setEnabled(not loading and self._selected_project_is_occupied())
        self.create_subproject_button.setEnabled(
            not loading and self._selected_project_is_occupied()
        )
        self.duplicate_project_button.setEnabled(
            not loading and self._selected_project_is_occupied()
        )
        self.delete_project_button.setEnabled(not loading and self._selected_project_is_occupied())
        if loading:
            self.status_label.setText("Chargement du répertoire en cours…")

    def set_error(self, message: str) -> None:
        self.status_label.setText(f"Erreur : {message}")

    def set_status_message(self, message: str) -> None:
        self.status_label.setText(message)

    def year(self) -> int:
        return int(self.year_combo.currentText().strip())

    def set_year(self, year: int) -> None:
        text = str(year)
        index = self.year_combo.findText(text)
        if index < 0:
            self.year_combo.addItem(text)
        self.year_combo.setCurrentText(text)

    def selected_row_payload(self) -> tuple[int, tuple[Any, ...], tuple[Any, ...]] | None:
        proxy_index = self.table.currentIndex()
        if not proxy_index.isValid():
            return None
        source_index = self._proxy.mapToSource(proxy_index)
        row = self._model.row_at(source_index.row())
        if row is None:
            return None
        original = self._model.original_values(row.row_index)
        if original is None:
            return None
        return row.row_index, row.values, original

    def mark_saved(self, row_index: int, values: tuple[Any, ...]) -> None:
        self._model.mark_saved(row_index, values)

    def original_rows(self) -> tuple[RepertoireRow, ...]:
        return self._model.original_rows()

    def next_available(self) -> NextAvailableProject | None:
        return self._model.next_available()

    def _filter_rows(self, text: str) -> None:
        self._proxy.setFilterRegularExpression(QRegularExpression.escape(text.strip()))

    def _save_selected(self) -> None:
        payload = self.selected_row_payload()
        if payload is not None:
            row_index, values, original = payload
            self.save_requested.emit(row_index, values, original)

    def _selected_project_is_occupied(self) -> bool:
        payload = self.selected_row_payload()
        if payload is None:
            return False
        _row_index, values, _original = payload
        return any(_display_value(value).strip() for value in values[1:])

    def _update_action_states(self) -> None:
        occupied = self._selected_project_is_occupied() and not self._loading
        for action in (
            self.open_project_button,
            self.create_subproject_button,
            self.duplicate_project_button,
            self.sync_project_button,
            self.delete_project_button,
        ):
            action.setEnabled(occupied)

    def _resize_columns(self) -> None:
        for column, width in enumerate(COLUMN_WIDTHS):
            self.table.setColumnWidth(column, width)

    def _position_near_next_available(self, snapshot: RepertoireSnapshot) -> None:
        next_project = snapshot.next_available
        if next_project is None:
            if self._proxy.rowCount() > 0:
                self.table.scrollTo(self._proxy.index(0, 0), QTableView.ScrollHint.PositionAtTop)
            return
        source_next = self._model.source_index_for_sheet_row(next_project.row_index)
        if source_next is None:
            return
        source_start = max(0, source_next - 4)
        proxy_start = self._proxy.mapFromSource(self._model.index(source_start, 0))
        proxy_next = self._proxy.mapFromSource(self._model.index(source_next, 0))
        if proxy_start.isValid():
            self.table.scrollTo(proxy_start, QTableView.ScrollHint.PositionAtTop)
        if proxy_next.isValid():
            self.table.selectRow(proxy_next.row())


class RepertoireRowDelegate(QStyledItemDelegate):
    """Selected row bar and the "Disponible" tag on the next free number."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        theme = current_theme()
        rect = QRectF(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.save()
        background = index.data(Qt.ItemDataRole.BackgroundRole)
        if selected:
            painter.fillRect(rect, theme.color("accent-200"))
        elif isinstance(background, QColor):
            painter.fillRect(rect, background)
        if selected and isinstance(background, QColor) and background == theme.color("accent-200"):
            painter.fillRect(rect, theme.color("accent-300", 0.6))
        painter.setPen(theme.color("text", 0.08))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if selected and index.column() == 0:
            painter.fillRect(
                QRectF(rect.left(), rect.top(), 3, rect.height()), theme.color("accent")
            )
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        font = QFont(option.font)
        font.setPixelSize(14)
        if index.column() == 0:
            font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(theme.color("text"))
        text_rect = rect.adjusted(10, 0, -10, 0)
        elided = painter.fontMetrics().elidedText(
            text,
            Qt.TextElideMode.ElideRight,
            int(text_rect.width()),
        )
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            elided,
        )
        if index.column() == 0 and index.data(NEXT_AVAILABLE_ROLE):
            tag_font = QFont(font)
            tag_font.setPixelSize(10)
            tag_font.setWeight(QFont.Weight.Normal)
            left = text_rect.left() + painter.fontMetrics().horizontalAdvance(text) + 8
            painter.setFont(tag_font)
            tag_width = painter.fontMetrics().horizontalAdvance("Disponible") + 12
            tag = QRectF(left, rect.center().y() - 8, tag_width, 16)
            painter.setPen(theme.color("accent"))
            painter.drawRect(tag.adjusted(0.5, 0.5, -0.5, -0.5))
            painter.drawText(tag, int(Qt.AlignmentFlag.AlignCenter), "Disponible")
        painter.restore()


def _legend(role: str, text: str) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(6)
    swatch = QFrame()
    swatch.setProperty("role", role)
    swatch.setFixedSize(10, 10)
    swatch.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    row.addWidget(swatch)
    row.addWidget(label(text, "muted"))
    return row


def _display_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return str(value)
