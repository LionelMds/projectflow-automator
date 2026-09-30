from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QMessageBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from projectflow import __version__
from projectflow.config import AppConfig
from projectflow.platform.paths import native_path_text
from projectflow.ui.creation_tab import CreationTab
from projectflow.ui.repertoire_tab import RepertoireDossierTab
from projectflow.ui.sortie_tab import SortieDossierTab
from projectflow.ui.theme import set_icon
from projectflow.ui.widgets.industry import DotTag, LogoLabel, button, label, rule

_OUTLOOK_BASE_FOLDERS = {"root": "Racine du compte", "inbox": "Boîte de réception"}


class MainWindow(QMainWindow):
    update_confirmed = Signal()
    update_check_requested = Signal()
    settings_requested = Signal()
    quick_create_requested = Signal()
    hidden_to_background = Signal()

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self._background_mode_enabled = False
        self._quit_requested = False
        self.setWindowTitle("ProjectFlow Automator - Balz Metal Sa")
        self.resize(1280, 820)
        self.setMinimumSize(1000, 680)

        central = QWidget()
        central.setProperty("pfRoot", "true")
        central.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        tabs = QTabWidget(central)
        # The header draws the navigation; the tab widget only stacks the pages.
        tabs.tabBar().hide()
        tabs.setDocumentMode(True)
        self.tabs = tabs
        self.creation_tab = CreationTab()
        years = [str(date.today().year), str(date.today().year + 1)]
        self.creation_tab.year_combo.addItems(years)
        self.sortie_tab = SortieDossierTab()
        self.sortie_tab.year_combo.addItems(years)
        self.repertoire_tab = RepertoireDossierTab()
        self.repertoire_tab.year_combo.addItems(years)
        tabs.addTab(self.creation_tab, "Création projet")
        tabs.addTab(self.sortie_tab, "Sortie dossier")
        tabs.addTab(self.repertoire_tab, "Répertoire chantier")

        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self._build_header())
        central_layout.addWidget(rule())
        central_layout.addWidget(tabs, 1)
        self.setCentralWidget(central)
        self.apply_config_labels()

        self._build_shortcuts()
        self._connect_signals()

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setProperty("role", "header")
        header.setFixedHeight(58)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(28)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        brand.addWidget(LogoLabel(34))
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addStretch(1)
        names.addWidget(label("ProjectFlow", "brand"))
        names.addWidget(label("Balz Métal SA", "small"))
        names.addStretch(1)
        brand.addLayout(names)
        layout.addLayout(brand)

        nav = QHBoxLayout()
        nav.setSpacing(2)
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for index in range(self.tabs.count()):
            nav_button = button(self.tabs.tabText(index), "nav")
            nav_button.setCheckable(True)
            nav_button.setChecked(index == 0)
            self._nav_group.addButton(nav_button, index)
            nav.addWidget(nav_button)
        self._nav_group.idClicked.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self._sync_nav)
        layout.addLayout(nav)
        layout.addStretch(1)

        self.repertoire_tag = DotTag()
        layout.addWidget(self.repertoire_tag, 0, Qt.AlignmentFlag.AlignVCenter)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.quick_button = button("Projet rapide", icon="zap")
        self.quick_button.clicked.connect(self.quick_create_requested.emit)
        actions.addWidget(self.quick_button)
        # One icon for the settings, the update check and the about box: the
        # redesigned window has no menu bar.
        self.settings_button = QToolButton()
        self.settings_button.setProperty("variant", "icon")
        self.settings_button.setToolTip("Paramètres")
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        set_icon(self.settings_button, "settings", size=17)
        menu = QMenu(self.settings_button)
        settings_action = menu.addAction("Paramètres…")
        update_action = menu.addAction("Rechercher une mise à jour")
        menu.addSeparator()
        about_action = menu.addAction("À propos")
        settings_action.triggered.connect(self.settings_requested.emit)
        update_action.triggered.connect(self.update_check_requested.emit)
        about_action.triggered.connect(self._show_about)
        self.settings_button.setMenu(menu)
        actions.addWidget(self.settings_button)
        layout.addLayout(actions)
        return header

    def _sync_nav(self, index: int) -> None:
        nav_button = self._nav_group.button(index)
        if nav_button is not None:
            nav_button.setChecked(True)

    def _build_shortcuts(self) -> None:
        open_action = QAction(self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.creation_tab.open_fiche_requested.emit)
        self.addAction(open_action)

        print_action = QAction(self)
        print_action.setShortcut(QKeySequence.StandardKey.Print)
        print_action.triggered.connect(self.creation_tab.print_fiche_requested.emit)
        self.addAction(print_action)

        open_repertoire_action = QAction(self)
        open_repertoire_action.setShortcut(QKeySequence("Ctrl+R"))
        open_repertoire_action.triggered.connect(self.creation_tab.open_repertoire_requested.emit)
        self.addAction(open_repertoire_action)

        load_action = QAction(self)
        load_action.setShortcut(QKeySequence("Ctrl+L"))
        load_action.triggered.connect(self.creation_tab.load_requested.emit)
        self.addAction(load_action)

        update_action = QAction(self)
        update_action.setShortcut(QKeySequence("Ctrl+S"))
        update_action.triggered.connect(self.creation_tab.update_requested.emit)
        self.addAction(update_action)

        settings_action = QAction(self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self.settings_requested.emit)
        self.addAction(settings_action)

    def _connect_signals(self) -> None:
        self.creation_tab.update_requested.connect(self._confirm_update)
        self.creation_tab.settings_requested.connect(self.settings_requested.emit)

    def apply_config_labels(self) -> None:
        paths = self._config.paths
        self.creation_tab.racine_label.setText(
            native_path_text(paths.racine_projets) or "Non configuré",
        )
        self.creation_tab.reference_label.setText(
            native_path_text(paths.dossier_reference) or "Non configuré",
        )
        self.creation_tab.repertoire_label.setText(
            native_path_text(paths.repertoire_chantier.display_path) or "Non configuré",
        )
        self.repertoire_tag.setText(f"Répertoire · {_repertoire_location(self._config)}")
        workbook = paths.repertoire_chantier.display_path.replace("\\", "/").rsplit("/", 1)[-1]
        self.repertoire_tab.set_source(
            " · ".join(part for part in (workbook, _repertoire_location(self._config)) if part),
        )
        outlook = self._config.outlook
        self.creation_tab.set_outlook_summary(
            enabled=outlook.enabled,
            detail=" · ".join(
                part
                for part in (
                    outlook.mailbox_email,
                    _OUTLOOK_BASE_FOLDERS.get(outlook.base_folder, outlook.base_folder),
                )
                if part
            ),
        )
        if getattr(self, "_applied_planner_config", None) != self._config.planner:
            self.creation_tab.apply_planner_config(self._config.planner)
            self._applied_planner_config = self._config.planner.model_copy(deep=True)
        self.creation_tab.apply_cad_config(self._config.cad)
        self.creation_tab.set_user_initials(self._config.user.initials)

    def set_background_mode_enabled(self, *, enabled: bool) -> None:
        self._background_mode_enabled = enabled

    def show_and_raise(self) -> None:
        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def show_creation_tab(self) -> None:
        self.tabs.setCurrentWidget(self.creation_tab)
        self.show_and_raise()

    def request_quit(self) -> None:
        self._quit_requested = True
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._background_mode_enabled and not self._quit_requested:
            event.ignore()
            self.hide()
            self.hidden_to_background.emit()
            return
        super().closeEvent(event)

    def _confirm_update(self) -> None:
        answer = QMessageBox.question(
            self,
            "Confirmer la mise à jour",
            "Réécrire la fiche et la ligne du répertoire ?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.update_confirmed.emit()

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "ProjectFlow Automator",
            f"ProjectFlow Automator {__version__}\nBalz Métal SA",
        )


def _repertoire_location(config: AppConfig) -> str:
    repertoire = config.paths.repertoire_chantier
    if repertoire.cloud_only or repertoire.drive_id or "://" in repertoire.display_path:
        return "OneDrive"
    if repertoire.display_path:
        return "Local"
    return "Non configuré"
