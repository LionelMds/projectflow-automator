from __future__ import annotations

from datetime import date

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QMessageBox, QWidget

from projectflow.config import AppConfig
from projectflow.ui.creation_tab import CreationTab


class MainWindow(QMainWindow):
    update_confirmed = Signal()
    update_check_requested = Signal()
    settings_requested = Signal()

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self.setWindowTitle("ProjectFlow Automator - Balz Metal Sa")
        self.resize(1120, 760)
        self.setMinimumSize(900, 680)

        central = QWidget()
        layout = QHBoxLayout(central)
        self.creation_tab = CreationTab()
        self.creation_tab.year_combo.addItems([str(date.today().year), str(date.today().year + 1)])
        self.apply_config_labels()
        layout.addWidget(self.creation_tab)
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
        self.creation_tab.racine_label.setText(str(paths.racine_projets or "Non configure"))
        self.creation_tab.reference_label.setText(str(paths.dossier_reference or "Non configure"))
        self.creation_tab.repertoire_label.setText(
            paths.repertoire_chantier.display_path or "Non configure",
        )

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
