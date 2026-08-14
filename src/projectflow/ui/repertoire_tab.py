from __future__ import annotations

from datetime import date
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QRegularExpression,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from projectflow.core.repertoire_service import (
    NextAvailableProject,
    RepertoireRow,
    RepertoireSnapshot,
)

HEADERS = ("No.", "Date", "Client", "Contact", "Designation")
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
        return HEADERS[section] if 0 <= section < len(HEADERS) else None

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
            if (row.row_index, index.column()) in self._dirty_cells:
                return QColor("#C6EFCE")
            if row.row_index == self._next_row_index:
                return QColor("#FFF2CC")
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"Ligne Excel {row.row_index + 1}"
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
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        title = QLabel("Répertoire chantier")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        root.addWidget(title)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Année"))
        self.year_combo = QComboBox()
        self.year_combo.setEditable(True)
        self.year_combo.setMinimumWidth(100)
        controls.addWidget(self.year_combo)
        controls.addWidget(QLabel("Rechercher"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Numéro, client, contact ou désignation")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._filter_rows)
        controls.addWidget(self.search_edit, 1)
        self.load_button = QPushButton("Charger")
        self.load_button.clicked.connect(self.load_requested.emit)
        controls.addWidget(self.load_button)
        self.refresh_button = QPushButton("Actualiser")
        self.refresh_button.clicked.connect(self.load_requested.emit)
        controls.addWidget(self.refresh_button)
        root.addLayout(controls)

        self.status_label = QLabel("Chargez une année pour afficher le répertoire.")
        self.status_label.setWordWrap(True)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        root.addWidget(self.status_label)

        self.table = QTableView()
        self.table.setModel(self._proxy)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.doubleClicked.connect(lambda _index: self.open_project_requested.emit())
        self.table.selectionModel().selectionChanged.connect(
            lambda _selected, _deselected: self._update_action_states()
        )
        root.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.new_project_button = QPushButton("Nouveau projet")
        self.new_project_button.clicked.connect(self.new_project_requested.emit)
        actions.addWidget(self.new_project_button)
        self.open_project_button = QPushButton("Charger le projet")
        self.open_project_button.clicked.connect(self.open_project_requested.emit)
        actions.addWidget(self.open_project_button)
        self.create_subproject_button = QPushButton("Créer sous-projet")
        self.create_subproject_button.clicked.connect(self.create_subproject_requested.emit)
        actions.addWidget(self.create_subproject_button)
        self.duplicate_project_button = QPushButton("Dupliquer")
        self.duplicate_project_button.clicked.connect(self.duplicate_project_requested.emit)
        actions.addWidget(self.duplicate_project_button)
        actions.addStretch(1)
        self.sync_project_button = QPushButton("Mettre à jour le projet")
        self.sync_project_button.setToolTip(
            "Mettre à jour la fiche et les intégrations configurées du projet sélectionné"
        )
        self.sync_project_button.clicked.connect(self.sync_project_requested.emit)
        actions.addWidget(self.sync_project_button)
        self.save_button = QPushButton("Enregistrer la ligne")
        self.save_button.clicked.connect(self._save_selected)
        actions.addWidget(self.save_button)
        self.delete_project_button = QPushButton("Supprimer avec éléments liés")
        self.delete_project_button.setToolTip(
            "Supprimer le projet sélectionné après confirmation et libérer son numéro"
        )
        self.delete_project_button.setStyleSheet("color: #B42318;")
        self.delete_project_button.clicked.connect(self.delete_project_requested.emit)
        actions.addWidget(self.delete_project_button)
        root.addLayout(actions)
        self._update_action_states()

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
        self.save_button.setEnabled(not loading)
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
        for button in (
            self.open_project_button,
            self.create_subproject_button,
            self.duplicate_project_button,
            self.sync_project_button,
            self.delete_project_button,
        ):
            button.setEnabled(occupied)

    def _resize_columns(self) -> None:
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(0, max(110, self.table.columnWidth(0)))
        self.table.setColumnWidth(1, max(110, self.table.columnWidth(1)))
        self.table.setColumnWidth(2, max(170, self.table.columnWidth(2)))
        self.table.setColumnWidth(3, max(170, self.table.columnWidth(3)))
        self.table.setColumnWidth(4, max(360, self.table.columnWidth(4)))

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


def _display_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return str(value)
