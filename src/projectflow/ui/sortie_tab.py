from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from projectflow.core.sortie_service import OutputCandidate, OutputInventory, SortieSelection
from projectflow.ui.widgets.industry import (
    META_ROLE,
    BlueprintFrame,
    FileItemDelegate,
    PrimaryButton,
    button,
    field,
    label,
    rule,
    section_heading,
)


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
        self.load_button.setText("Chargement…" if loading else "Charger")
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
        self.pages.setCurrentIndex(1)
        year, project_id = self.project_identity()
        number = project_id if "-" in project_id or not year else f"{year}-{project_id}"
        self.title_label.setText(number or "Projet chargé")

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
        # The footer shows the latest messages only; the full history is in the log file.
        lines = [*self.logs.text().splitlines(), message]
        self.logs.setText("\n".join(lines[-3:]).strip())

    def _build_ui(self) -> None:
        self.setMinimumWidth(900)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(20, 16, 20, 16)
        body.setSpacing(10)
        body.addLayout(self._build_identity())

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_empty_state())
        loaded = QWidget()
        grid = QGridLayout(loaded)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        grid.addWidget(self._build_fiche_group(), 0, 0)
        grid.addWidget(self._build_mesure_group(), 0, 1)
        grid.addWidget(self._build_photo_group(), 1, 0, 1, 2)
        grid.addWidget(self._build_plan_group(), 2, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(3, 1)
        self.pages.addWidget(loaded)
        self.pages.currentChanged.connect(self._fit_pages)
        self._fit_pages(0)
        body.addWidget(self.pages, 1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        root.addWidget(rule())

        footer = QWidget()
        footer.setProperty("role", "footer")
        actions = QHBoxLayout(footer)
        actions.setContentsMargins(26, 6, 20, 6)
        actions.setSpacing(12)
        self.logs = label("Chargez un projet pour préparer les documents de sortie.", "muted")
        self.logs.setWordWrap(True)
        self.logs.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        actions.addWidget(self.logs, 1)
        self.create_output_button = PrimaryButton("Créer dossier de sortie")
        self.create_output_button.setDefault(True)
        self.create_output_button.setEnabled(False)
        self.create_output_button.clicked.connect(self.create_output_requested.emit)
        actions.addWidget(self.create_output_button)
        root.addWidget(footer)

    def _build_identity(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(6, 0, 6, 4)
        row.setSpacing(16)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label("Sortie dossier", "kicker"))
        self.title_label = label("Projet à sortir", "h2")
        titles.addWidget(self.title_label)
        row.addLayout(titles, 1)
        self.year_combo = QComboBox()
        self.year_combo.setEditable(True)
        self.project_id_edit = QLineEdit()
        self.project_id_edit.setPlaceholderText("2026-4995")
        self.load_button = button("Charger")
        self.load_button.clicked.connect(self.load_requested.emit)
        year = field("Année", self.year_combo)
        year.setFixedWidth(112)
        number = field("Numéro", self.project_id_edit)
        number.setFixedWidth(200)
        row.addWidget(year, 0, Qt.AlignmentFlag.AlignBottom)
        row.addWidget(number, 0, Qt.AlignmentFlag.AlignBottom)
        row.addWidget(self.load_button, 0, Qt.AlignmentFlag.AlignBottom)
        return row

    def _build_empty_state(self) -> BlueprintFrame:
        frame = BlueprintFrame()
        frame.setMinimumHeight(420)
        frame.box.addStretch(1)
        steps = QHBoxLayout()
        steps.setSpacing(-1)
        steps.addStretch(1)
        for number in range(1, 5):
            cell = label(str(number), "step-cell")
            cell.setFixedSize(57, 57)
            cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
            steps.addWidget(cell)
        steps.addStretch(1)
        frame.box.addLayout(steps)
        title = label("Chargez un projet", "h4")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        frame.box.addSpacing(8)
        frame.box.addWidget(title)
        text = label(
            "Fiche dossier, prise de cote, photos et plans d'exécution seront détectés "
            "dans le dossier projet et réunis dans un dossier de sortie.",
            "muted",
            wrap=True,
        )
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text.setFixedWidth(440)
        centered = QHBoxLayout()
        centered.addStretch(1)
        centered.addWidget(text)
        centered.addStretch(1)
        frame.box.addLayout(centered)
        frame.box.addStretch(1)
        return frame

    def _build_fiche_group(self) -> BlueprintFrame:
        group = BlueprintFrame(padding=(16, 14, 16, 16), spacing=8)
        group.box.addWidget(
            section_heading(
                "01", "Fiche dossier", size="h5", trailing=label("Obligatoire", "tag-outline")
            ),
        )
        self.fiche_list = QListWidget()
        self.fiche_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        _configure_file_list(self.fiche_list, FileItemDelegate(self.fiche_list, radio=True))
        self.fiche_list.setMinimumHeight(104)
        self.fiche_list.setMaximumHeight(140)
        group.box.addWidget(self.fiche_list)
        return group

    def _build_mesure_group(self) -> BlueprintFrame:
        group = BlueprintFrame(padding=(16, 14, 16, 16), spacing=8)
        group.box.addWidget(
            section_heading(
                "02",
                "Prise de cote initiale",
                size="h5",
                trailing=label("PDF · facultatif", "tag-neutral"),
            ),
        )
        self.mesure_list = QListWidget()
        self.mesure_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        _configure_file_list(self.mesure_list, FileItemDelegate(self.mesure_list, radio=True))
        self.mesure_list.setMinimumHeight(104)
        self.mesure_list.setMaximumHeight(140)
        group.box.addWidget(self.mesure_list)
        return group

    def _build_photo_group(self) -> BlueprintFrame:
        group = BlueprintFrame(padding=(0, 0, 0, 0), layout="h", spacing=0)
        left_widget = QWidget()
        left_widget.setFixedWidth(340)
        left = QVBoxLayout(left_widget)
        left.setContentsMargins(16, 14, 16, 16)
        left.setSpacing(8)
        self.photo_count_label = label("", "small")
        left.addWidget(section_heading("03", "Photos", size="h5", trailing=self.photo_count_label))
        self.photo_list = QListWidget()
        self.photo_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.photo_list.setProperty("role", "rail")
        _configure_file_list(self.photo_list, FileItemDelegate(self.photo_list, style="rail"))
        self.photo_list.setSpacing(0)
        self.photo_list.setMinimumHeight(170)
        self.photo_list.itemSelectionChanged.connect(self._refresh_photo_preview)
        left.addWidget(self.photo_list, 1)
        photo_actions = QHBoxLayout()
        photo_actions.setSpacing(8)
        self.photo_browse_button = button("Parcourir")
        self.photo_remove_button = button("Retirer")
        self.photo_browse_button.clicked.connect(self._browse_photos)
        self.photo_remove_button.clicked.connect(
            lambda: self._remove_selected(self.photo_list),
        )
        photo_actions.addWidget(self.photo_browse_button)
        photo_actions.addWidget(self.photo_remove_button)
        photo_actions.addStretch(1)
        left.addLayout(photo_actions)
        group.box.addWidget(left_widget)
        group.box.addWidget(rule(vertical=True))

        right_widget = QWidget()
        right = QVBoxLayout(right_widget)
        right.setContentsMargins(16, 14, 16, 16)
        right.setSpacing(8)
        self.photo_caption = label("Aperçu", "caps")
        right.addWidget(self.photo_caption)
        preview_frame = BlueprintFrame(padding=(0, 0, 0, 0), spacing=0)
        self.photo_preview = QLabel("Aucune photo sélectionnée")
        self.photo_preview.setProperty("role", "muted")
        self.photo_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_preview.setMinimumSize(420, 230)
        self.photo_preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        preview_frame.box.addWidget(self.photo_preview, 1)
        right.addWidget(preview_frame, 1)
        group.box.addWidget(right_widget, 1)
        return group

    def _build_plan_group(self) -> BlueprintFrame:
        group = BlueprintFrame(padding=(16, 14, 16, 16), spacing=8)
        self.plan_list = QListWidget()
        self.plan_browse_button = button("Parcourir", size="sm")
        self.plan_remove_button = button("Retirer", size="sm")
        self.plan_browse_button.clicked.connect(self._browse_plans)
        self.plan_remove_button.clicked.connect(
            lambda: self._remove_selected(self.plan_list),
        )
        heading = QHBoxLayout()
        heading.setSpacing(10)
        heading.addWidget(section_heading("04", "Plans d'exécution", size="h5"))
        heading.addWidget(label("PDF", "tag-neutral"), 0, Qt.AlignmentFlag.AlignVCenter)
        heading.addStretch(1)
        heading.addWidget(self.plan_browse_button)
        heading.addWidget(self.plan_remove_button)
        group.box.addLayout(heading)
        self.plan_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        _configure_file_list(self.plan_list, FileItemDelegate(self.plan_list, columns=3))
        self.plan_list.setFlow(QListView.Flow.LeftToRight)
        self.plan_list.setWrapping(True)
        self.plan_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.plan_list.setMinimumHeight(66)
        self.plan_list.setMaximumHeight(105)
        group.box.addWidget(self.plan_list)
        self.plan_list.itemSelectionChanged.connect(self._refresh_plan_controls)
        return group

    def _fit_pages(self, current: int) -> None:
        # A stacked widget is as tall as its tallest page; only the shown one counts here.
        for index in range(self.pages.count()):
            page = self.pages.widget(index)
            if page is None:
                continue
            policy = (
                QSizePolicy.Policy.Preferred if index == current else QSizePolicy.Policy.Ignored
            )
            page.setSizePolicy(policy, policy)
        self.pages.adjustSize()

    def _populate_candidates(
        self,
        widget: QListWidget,
        candidates: tuple[OutputCandidate, ...],
        *,
        select_first: bool,
    ) -> None:
        widget.clear()
        for index, candidate in enumerate(candidates):
            item = QListWidgetItem(candidate.path.name)
            item.setData(META_ROLE, _candidate_meta(candidate))
            item.setToolTip(str(candidate.path))
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
            item = QListWidgetItem(path.name)
            item.setData(META_ROLE, _path_meta(path))
            item.setToolTip(str(path))
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
        count = self.photo_list.count()
        self.photo_count_label.setText(
            f"{count} ajoutée{'s' if count > 1 else ''}" if count else "Aucune",
        )
        current = self.photo_list.currentItem()
        self.photo_caption.setText(
            f"APERÇU · {current.text()}" if current is not None else "APERÇU",
        )
        if self.photo_list.currentRow() < 0:
            self.photo_preview.setText("Aucune photo sélectionnée")
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
            self.photo_preview.setText("Aperçu indisponible")
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


def _configure_file_list(widget: QListWidget, delegate: FileItemDelegate) -> None:
    widget.setItemDelegate(delegate)
    widget.setSpacing(3)
    widget.setMouseTracking(True)
    widget.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)


def _candidate_meta(candidate: OutputCandidate) -> str:
    return _path_meta(
        candidate.path,
        size_bytes=candidate.size_bytes,
        modified=candidate.modified_timestamp,
    )


def _path_meta(
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
    size_kb = f"{size_bytes / 1024:,.1f}".replace(",", "\u202f").replace(".", ",")
    return f"{size_kb} Ko · {modified_text}"
