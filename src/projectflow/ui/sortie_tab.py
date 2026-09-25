from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from projectflow.core.sortie_service import OutputCandidate, OutputInventory, SortieSelection


class SortieDossierTab(QWidget):
    load_requested = Signal()
    create_output_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._inventory = OutputInventory()
        self.is_loading = False
        self._photo_directory: Path | None = None
        self._plan_directory: Path | None = None
        self._build_ui()

    def set_loading(self, *, loading: bool) -> None:
        self.is_loading = loading
        self.load_button.setEnabled(not loading)
        self.load_button.setText("Chargement..." if loading else "Charger")
        self.create_output_button.setEnabled(not loading and bool(self._inventory.fiches))

    def set_project_identity(self, *, year: str, project_id: str) -> None:
        index = self.year_combo.findText(year)
        if index >= 0:
            self.year_combo.setCurrentIndex(index)
        elif year:
            self.year_combo.addItem(year)
            self.year_combo.setCurrentText(year)
        self.project_id_edit.setText(project_id)

    def set_project_directory(self, project_dir: Path) -> None:
        self._photo_directory = project_dir / "photos"
        self._plan_directory = project_dir / "Plans" / "Plan d'exécution"
        self._refresh_browse_buttons()

    def project_identity(self) -> tuple[str, str]:
        return self.year_combo.currentText().strip(), self.project_id_edit.text().strip()

    def set_inventory(self, inventory: OutputInventory) -> None:
        self._inventory = inventory
        # Folders found on disk win over the default names guessed from the project folder.
        if inventory.photo_directory is not None:
            self._photo_directory = inventory.photo_directory
        elif inventory.photos:
            self._photo_directory = inventory.photos[0].path.parent
        if inventory.plan_directory is not None:
            self._plan_directory = inventory.plan_directory
        elif inventory.plans:
            self._plan_directory = inventory.plans[0].path.parent
        self._populate_candidates(self.fiche_list, inventory.fiches, select_first=True)
        self._populate_candidates(self.mesure_list, inventory.mesure_pdfs, select_first=False)
        self.photo_list.clear()
        self.plan_list.clear()
        self._refresh_browse_buttons()
        self._refresh_photo_preview()
        self._refresh_plan_controls()
        self._refresh_status()
        self.create_output_button.setEnabled(bool(inventory.fiches))

    def data(self) -> SortieSelection:
        fiche = self._current_path(self.fiche_list)
        if fiche is None:
            raise ValueError("Selectionnez une fiche dossier avant la sortie.")
        return SortieSelection(
            fiche_path=fiche,
            mesure_pdf_path=self._current_path(self.mesure_list),
            photo_paths=self._all_paths(self.photo_list),
            plan_paths=self._all_paths(self.plan_list),
        )

    def append_log(self, message: str) -> None:
        self.logs.setText(f"{self.logs.text()}\n{message}".strip())

    def _build_ui(self) -> None:
        self.setMinimumWidth(900)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        identity = QGroupBox("Projet a sortir")
        identity_layout = QHBoxLayout(identity)
        self.year_combo = QComboBox()
        self.year_combo.setEditable(True)
        self.project_id_edit = QLineEdit()
        self.project_id_edit.setPlaceholderText("2026-4995")
        self.load_button = QPushButton("Charger")
        self.load_button.clicked.connect(self.load_requested.emit)
        identity_layout.addWidget(QLabel("Annee"))
        identity_layout.addWidget(self.year_combo, 1)
        identity_layout.addWidget(QLabel("Numero"))
        identity_layout.addWidget(self.project_id_edit, 3)
        identity_layout.addWidget(self.load_button)
        root.addWidget(identity)

        files = QHBoxLayout()
        files.addWidget(self._build_fiche_group(), 1)
        files.addWidget(self._build_mesure_group(), 1)
        root.addLayout(files)

        root.addWidget(self._build_photo_group())
        root.addWidget(self._build_plan_group())

        self.logs = QLabel("Chargez un projet pour preparer les documents de sortie.")
        self.logs.setWordWrap(True)
        self.logs.setMinimumHeight(34)
        self.logs.setStyleSheet("QLabel { color: #555; padding: 4px; }")
        root.addWidget(self.logs)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.create_output_button = QPushButton("Creer dossier de sortie")
        self.create_output_button.setDefault(True)
        self.create_output_button.setEnabled(False)
        self.create_output_button.clicked.connect(self.create_output_requested.emit)
        actions.addWidget(self.create_output_button)
        root.addLayout(actions)

    def _build_fiche_group(self) -> QGroupBox:
        group = QGroupBox("1. Fiche dossier (obligatoire)")
        layout = QVBoxLayout(group)
        self.fiche_list = QListWidget()
        self.fiche_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.fiche_list.setMinimumHeight(90)
        layout.addWidget(self.fiche_list)
        return group

    def _build_mesure_group(self) -> QGroupBox:
        group = QGroupBox("2. Prise de cote initiale (PDF)")
        layout = QVBoxLayout(group)
        self.mesure_list = QListWidget()
        self.mesure_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.mesure_list.setMinimumHeight(90)
        layout.addWidget(self.mesure_list)
        return group

    def _build_photo_group(self) -> QGroupBox:
        group = QGroupBox("3. Photos")
        layout = QHBoxLayout(group)
        left = QVBoxLayout()
        self.photo_list = QListWidget()
        self.photo_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.photo_list.setMinimumWidth(320)
        self.photo_list.setMinimumHeight(170)
        self.photo_list.itemSelectionChanged.connect(self._refresh_photo_preview)
        left.addWidget(self.photo_list)
        photo_actions = QHBoxLayout()
        self.photo_browse_button = QPushButton("Parcourir")
        self.photo_remove_button = QPushButton("Retirer")
        self.photo_browse_button.clicked.connect(self._browse_photos)
        self.photo_remove_button.clicked.connect(
            lambda: self._remove_selected(self.photo_list),
        )
        photo_actions.addWidget(self.photo_browse_button)
        photo_actions.addWidget(self.photo_remove_button)
        left.addLayout(photo_actions)
        layout.addLayout(left, 1)

        right = QVBoxLayout()
        right.addWidget(QLabel("Apercu de la photo selectionnee"))
        self.photo_preview = QLabel("Aucune photo selectionnee")
        self.photo_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_preview.setMinimumSize(420, 230)
        self.photo_preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.photo_preview.setStyleSheet("QLabel { border: 1px solid #B0B0B0; background: white; }")
        right.addWidget(self.photo_preview, 1)
        layout.addLayout(right, 2)
        return group

    def _build_plan_group(self) -> QGroupBox:
        group = QGroupBox("4. Plans d'execution (PDF)")
        layout = QVBoxLayout(group)
        self.plan_list = QListWidget()
        self.plan_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.plan_list.setMaximumHeight(105)
        layout.addWidget(self.plan_list)
        plan_actions = QHBoxLayout()
        self.plan_browse_button = QPushButton("Parcourir")
        self.plan_remove_button = QPushButton("Retirer")
        self.plan_browse_button.clicked.connect(self._browse_plans)
        self.plan_remove_button.clicked.connect(
            lambda: self._remove_selected(self.plan_list),
        )
        plan_actions.addWidget(self.plan_browse_button)
        plan_actions.addWidget(self.plan_remove_button)
        plan_actions.addStretch(1)
        layout.addLayout(plan_actions)
        self.plan_list.itemSelectionChanged.connect(self._refresh_plan_controls)
        return group

    def _populate_candidates(
        self,
        widget: QListWidget,
        candidates: tuple[OutputCandidate, ...],
        *,
        select_first: bool,
    ) -> None:
        widget.clear()
        for index, candidate in enumerate(candidates):
            item = QListWidgetItem(_candidate_label(candidate))
            item.setData(Qt.ItemDataRole.UserRole, str(candidate.path))
            widget.addItem(item)
            if select_first and index == 0:
                item.setSelected(True)
                widget.setCurrentItem(item)

    def _browse_photos(self) -> None:
        directory = self._photo_directory or Path.home()
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Selectionner les photos",
            str(directory),
            "Images (*.bmp *.gif *.jpeg *.jpg *.png *.tif *.tiff *.webp)",
        )
        self._add_paths(self.photo_list, paths)
        if paths and self.photo_list.currentRow() < 0:
            self.photo_list.setCurrentRow(0)
        self._refresh_photo_preview()

    def _browse_plans(self) -> None:
        directory = self._plan_directory or Path.home()
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Selectionner les plans",
            str(directory),
            "Documents PDF (*.pdf)",
        )
        self._add_paths(self.plan_list, paths)
        self._refresh_plan_controls()

    def _add_paths(self, widget: QListWidget, paths: list[str]) -> None:
        existing = set(self._all_paths(widget))
        for raw_path in paths:
            path = Path(raw_path).resolve()
            if not path.is_file() or path in existing:
                continue
            item = QListWidgetItem(_path_label(path))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            widget.addItem(item)
            existing.add(path)

    def _remove_selected(self, widget: QListWidget) -> None:
        rows = sorted({index.row() for index in widget.selectedIndexes()}, reverse=True)
        for row in rows:
            widget.takeItem(row)
        if widget is self.photo_list:
            self._refresh_photo_preview()
        else:
            self._refresh_plan_controls()

    def _refresh_browse_buttons(self) -> None:
        self.photo_browse_button.setEnabled(self._photo_directory is not None)
        self.plan_browse_button.setEnabled(self._plan_directory is not None)

    def _refresh_photo_preview(self) -> None:
        self.photo_remove_button.setEnabled(self.photo_list.currentRow() >= 0)
        if self.photo_list.currentRow() < 0:
            self.photo_preview.setText("Aucune photo selectionnee")
            self.photo_preview.setPixmap(QPixmap())
            return
        item = self.photo_list.currentItem()
        path = self._item_path(item)
        if path is None:
            self.photo_preview.setText("Photo introuvable")
            self.photo_preview.setPixmap(QPixmap())
            return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.photo_preview.setText("Apercu indisponible")
            self.photo_preview.setPixmap(QPixmap())
            return
        self.photo_preview.setText("")
        self.photo_preview.setPixmap(
            pixmap.scaled(
                self.photo_preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ),
        )

    def _refresh_plan_controls(self) -> None:
        self.plan_remove_button.setEnabled(bool(self.plan_list.selectedItems()))

    def _refresh_status(self) -> None:
        self.logs.setText(
            f"{len(self._inventory.fiches)} fiche(s), "
            f"{len(self._inventory.mesure_pdfs)} prise(s) de cote, "
            f"{len(self._inventory.photos)} photo(s) disponible(s), "
            f"{len(self._inventory.plans)} plan(s) disponible(s). "
            "Utilisez Parcourir pour les ajouter."
        )

    @staticmethod
    def _all_paths(widget: QListWidget) -> tuple[Path, ...]:
        paths: list[Path] = []
        for index in range(widget.count()):
            item = widget.item(index)
            path = SortieDossierTab._item_path(item)
            if path is not None:
                paths.append(path)
        return tuple(paths)

    @staticmethod
    def _item_path(item: QListWidgetItem) -> Path | None:
        value = item.data(Qt.ItemDataRole.UserRole)
        return Path(value).resolve() if isinstance(value, str) else None

    @staticmethod
    def _current_path(widget: QListWidget) -> Path | None:
        if widget.currentRow() < 0:
            return None
        return SortieDossierTab._item_path(widget.currentItem())


def _candidate_label(candidate: OutputCandidate) -> str:
    return _path_label(
        candidate.path,
        size_bytes=candidate.size_bytes,
        modified=candidate.modified_timestamp,
    )


def _path_label(
    path: Path,
    *,
    size_bytes: int | None = None,
    modified: float | None = None,
) -> str:
    if size_bytes is None or modified is None:
        stat = path.stat()
        size_bytes = stat.st_size
        modified = stat.st_mtime
    modified_text = datetime.fromtimestamp(modified, tz=UTC).strftime("%d.%m.%Y %H:%M")
    size_kb = size_bytes / 1024
    return f"{path.name} - {size_kb:.1f} Ko - {modified_text}"
