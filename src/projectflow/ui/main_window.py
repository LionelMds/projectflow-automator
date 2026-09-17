from __future__ import annotations

from datetime import date

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabWidget, QVBoxLayout, QWidget

from projectflow.config import AppConfig
from projectflow.platform.paths import native_path_text
from projectflow.ui.creation_tab import CreationTab
from projectflow.ui.repertoire_tab import RepertoireDossierTab
from projectflow.ui.sortie_tab import SortieDossierTab


class MainWindow(QMainWindow):
    update_confirmed = Signal()
    update_check_requested = Signal()
    settings_requested = Signal()
    hidden_to_background = Signal()

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self._background_mode_enabled = False
        self._quit_requested = False
        self.setWindowTitle("ProjectFlow Automator - Balz Metal Sa")
        self.resize(1120, 760)
        self.setMinimumSize(900, 680)

        central = QWidget()
        tabs = QTabWidget(central)
        self.tabs = tabs
        self.creation_tab = CreationTab()
        years = [str(date.today().year), str(date.today().year + 1)]
        self.creation_tab.year_combo.addItems(years)
        self.sortie_tab = SortieDossierTab()
        self.sortie_tab.year_combo.addItems(years)
        self.repertoire_tab = RepertoireDossierTab()
        self.repertoire_tab.year_combo.addItems(years)
        self.apply_config_labels()
        tabs.addTab(self.creation_tab, "Creation projet")
        tabs.addTab(self.sortie_tab, "Sortie dossier")
        tabs.addTab(self.repertoire_tab, "Repertoire chantier")
        central_layout = QVBoxLayout(central)
        central_layout.addWidget(tabs)
        self.setCentralWidget(central)

        self._build_header()
        self._build_shortcuts()
        self._connect_signals()

    def _build_header(self) -> None:
        header = self.menuBar().addMenu("ProjectFlow")
        settings_action = QAction("Parametres", self)
        update_action = QAction("Rechercher une mise a jour", self)
        about_action = QAction("A propos", self)
        header.addAction(settings_action)
        header.addAction(update_action)
        header.addSeparator()
        header.addAction(about_action)
        settings_action.triggered.connect(self.settings_requested.emit)
        update_action.triggered.connect(self.update_check_requested.emit)
        about_action.triggered.connect(self._show_about)

    def _build_shortcuts(self) -> None:
        open_action = QAction(self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.creation_tab.open_fiche_requested.emit)
        self.addAction(open_action)

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

    def _connect_signals(self) -> None:
        self.creation_tab.update_requested.connect(self._confirm_update)
        self.creation_tab.settings_requested.connect(self.settings_requested.emit)

    def apply_config_labels(self) -> None:
        paths = self._config.paths
        self.creation_tab.racine_label.setText(
            native_path_text(paths.racine_projets) or "Non configure",
        )
        self.creation_tab.reference_label.setText(
            native_path_text(paths.dossier_reference) or "Non configure",
        )
        self.creation_tab.repertoire_label.setText(
            native_path_text(paths.repertoire_chantier.display_path) or "Non configure",
        )
        if getattr(self, "_applied_planner_config", None) != self._config.planner:
            self.creation_tab.apply_planner_config(self._config.planner)
            self._applied_planner_config = self._config.planner.model_copy(deep=True)
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
            "Confirmer la mise a jour",
            "Reecrire la fiche et la ligne du repertoire ?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.update_confirmed.emit()

    def _show_about(self) -> None:
        QMessageBox.about(self, "ProjectFlow Automator", "ProjectFlow Automator - Balz Metal Sa")
