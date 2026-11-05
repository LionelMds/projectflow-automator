from __future__ import annotations

from datetime import date
from typing import Final, Literal

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QHideEvent, QKeySequence, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QLayout,
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
from projectflow.ui.planning.planning_tab import PlanningTab
from projectflow.ui.repertoire_tab import RepertoireDossierTab
from projectflow.ui.sortie_tab import SortieDossierTab
from projectflow.ui.theme import current_theme, set_icon, theme_notifier
from projectflow.ui.widgets.industry import DotTag, LogoLabel, button, label, rule, set_variant

_OUTLOOK_BASE_FOLDERS = {"root": "Racine du compte", "inbox": "Boîte de réception"}
MicrosoftState = Literal["hidden", "checking", "connected", "warning", "disconnected"]
MICROSOFT_SIGN_IN_LABEL = "Se connecter à Microsoft"
PLANNING_TAB_TEXT: Final = "Planning"
QUICK_CREATE_LABEL: Final = "Projet rapide"
# Below this window width the header shortens its texts to stay on one line down to the
# minimum width (1000 px): nav « Répertoire », icon-only « Projet rapide », no account.
NAV_COMPACT_WIDTH: Final = 1100
NAV_COMPACT_LABELS: Final = {"Répertoire chantier": "Répertoire"}
HEADER_SPACING: Final = 28
HEADER_COMPACT_SPACING: Final = 16
NAV_PADDING: Final = 14
"""Horizontal padding of a navigation button (``QPushButton[variant="nav"]``)."""
NAV_BADGE_HEIGHT: Final = 16
NAV_BADGE_TOP: Final = 9
NAV_BADGE_GAP: Final = 4
NAV_BADGE_TEXT_MARGIN: Final = 10
NAV_BADGE_MAX: Final = 99


class MainWindow(QMainWindow):
    update_confirmed = Signal()
    update_check_requested = Signal()
    settings_requested = Signal()
    quick_create_requested = Signal()
    hidden_to_background = Signal()
    microsoft_sign_in_requested = Signal()
    connection_check_requested = Signal()
    reset_requested = Signal()
    # Shown, hidden, minimised, activated or deactivated: what the planning sync follows.
    presence_changed = Signal()

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self._background_mode_enabled = False
        self._quit_requested = False
        self._microsoft_state: MicrosoftState = "hidden"
        self._microsoft_detail = ""
        self._compact_header = False
        self._header: QWidget | None = None
        self._header_layout: QHBoxLayout | None = None
        self._nav_badges: dict[int, NavBadge] = {}
        self._creation_actions: list[QAction] = []
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
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
        self.planning_tab = PlanningTab()
        tabs.addTab(self.creation_tab, "Création projet")
        tabs.addTab(self.sortie_tab, "Sortie dossier")
        tabs.addTab(self.repertoire_tab, "Répertoire chantier")
        # Last: the indexes of the other tabs do not move.
        tabs.addTab(self.planning_tab, PLANNING_TAB_TEXT)

        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        self._header = self._build_header()
        central_layout.addWidget(self._header)
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
        layout.setSpacing(HEADER_SPACING)
        self._header_layout = layout

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
        for index in range(self.tabs.count()):
            nav_button = button(self.tabs.tabText(index), "nav")
            nav_button.setCheckable(True)
            nav_button.setChecked(index == 0)
            self._nav_group.addButton(nav_button, index)
            self._nav_badges[index] = NavBadge(nav_button)
            nav.addWidget(nav_button)
        self._nav_group.idClicked.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self._sync_nav)
        layout.addLayout(nav)
        layout.addStretch(1)

        # Connection state checked at startup: one click signs in when it is broken.
        self.microsoft_button = button("", "ghost", size="sm")
        self.microsoft_button.clicked.connect(self._microsoft_clicked)
        self._microsoft_menu = QMenu(self.microsoft_button)
        sign_in_action = self._microsoft_menu.addAction("Se connecter / changer de compte")
        check_action = self._microsoft_menu.addAction("Vérifier la connexion")
        self._microsoft_menu.addSeparator()
        reset_from_status = self._microsoft_menu.addAction("Tout réinitialiser ProjectFlow…")
        sign_in_action.triggered.connect(self.microsoft_sign_in_requested.emit)
        check_action.triggered.connect(self.connection_check_requested.emit)
        reset_from_status.triggered.connect(self.reset_requested.emit)
        self.microsoft_button.hide()
        layout.addWidget(self.microsoft_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.repertoire_tag = DotTag()
        layout.addWidget(self.repertoire_tag, 0, Qt.AlignmentFlag.AlignVCenter)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.quick_button = button(QUICK_CREATE_LABEL, icon="zap")
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
        reset_action = menu.addAction("Tout réinitialiser ProjectFlow…")
        about_action = menu.addAction("À propos")
        settings_action.triggered.connect(self.settings_requested.emit)
        update_action.triggered.connect(self.update_check_requested.emit)
        reset_action.triggered.connect(self.reset_requested.emit)
        about_action.triggered.connect(self._show_about)
        self.settings_button.setMenu(menu)
        actions.addWidget(self.settings_button)
        layout.addLayout(actions)
        return header

    @property
    def microsoft_state(self) -> MicrosoftState:
        return self._microsoft_state

    def set_microsoft_status(self, state: MicrosoftState, detail: str = "") -> None:
        self._microsoft_state = state
        self._microsoft_detail = detail
        self.microsoft_button.setVisible(state != "hidden")
        self._refresh_microsoft_text()
        broken = state in {"warning", "disconnected"}
        set_variant(self.microsoft_button, "danger" if broken else "ghost")
        self._apply_header_mode()

    def _refresh_microsoft_text(self) -> None:
        state, detail = self._microsoft_state, self._microsoft_detail
        texts = {
            "checking": "Microsoft · vérification…",
            "connected": f"Microsoft · {detail}",
            "warning": f"Microsoft · {detail}",
            "disconnected": MICROSOFT_SIGN_IN_LABEL,
        }
        tooltip = detail if state == "disconnected" else "Connexion Microsoft de ProjectFlow"
        if self._compact_header and state in {"checking", "connected"}:
            # The account moves to the tooltip: the four tabs keep their room.
            texts = {"checking": "Microsoft…", "connected": "Microsoft"}
            tooltip = f"Microsoft · {detail}" if detail else tooltip
        self.microsoft_button.setText(texts.get(state, ""))
        self.microsoft_button.setToolTip(tooltip)

    def _microsoft_clicked(self) -> None:
        if self._microsoft_state in {"warning", "disconnected"}:
            self.microsoft_sign_in_requested.emit()
            return
        self._microsoft_menu.popup(
            self.microsoft_button.mapToGlobal(QPoint(0, self.microsoft_button.height())),
        )

    def _sync_nav(self, index: int) -> None:
        nav_button = self._nav_group.button(index)
        if nav_button is not None:
            nav_button.setChecked(True)

    @property
    def planning_index(self) -> int:
        return self.tabs.indexOf(self.planning_tab)

    def set_nav_badge(self, index: int, count: int) -> None:
        """Show ``count`` beside the navigation button ``index``; nothing at 0."""
        badge = self._nav_badges.get(index)
        if badge is not None:
            badge.set_count(count)
            self._apply_header_mode()

    def nav_badge_count(self, index: int) -> int:
        badge = self._nav_badges.get(index)
        return badge.count if badge is not None else 0

    def show_planning_tab(self) -> None:
        self.tabs.setCurrentWidget(self.planning_tab)
        self.show_and_raise()

    def _build_shortcuts(self) -> None:
        # The creation form's shortcuts: the planning tab uses Ctrl+S, Ctrl+P... itself.
        for sequence, signal in (
            (QKeySequence(QKeySequence.StandardKey.Open), self.creation_tab.open_fiche_requested),
            (QKeySequence(QKeySequence.StandardKey.Print), self.creation_tab.print_fiche_requested),
            (QKeySequence("Ctrl+R"), self.creation_tab.open_repertoire_requested),
            (QKeySequence("Ctrl+L"), self.creation_tab.load_requested),
            (QKeySequence("Ctrl+S"), self.creation_tab.update_requested),
        ):
            action = QAction(self)
            action.setShortcut(sequence)
            action.triggered.connect(signal.emit)
            self.addAction(action)
            self._creation_actions.append(action)

        settings_action = QAction(self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self.settings_requested.emit)
        self.addAction(settings_action)
        self.tabs.currentChanged.connect(self._update_shortcuts)
        self._update_shortcuts(self.tabs.currentIndex())

    @property
    def creation_actions(self) -> tuple[QAction, ...]:
        """Window-wide shortcuts of the creation form (Ctrl+O, P, R, L and S)."""
        return tuple(self._creation_actions)

    def _update_shortcuts(self, index: int) -> None:
        # Ctrl+S in the planning must never launch « Réécrire la fiche ».
        enabled = self.tabs.widget(index) is not self.planning_tab
        for action in self._creation_actions:
            action.setEnabled(enabled)

    @property
    def compact_header(self) -> bool:
        return self._compact_header

    def _apply_header_mode(self) -> None:
        """Shorten the header texts below 1100 px, or sooner when a long account needs it.

        Decided on screen only (shown, resized): a hidden window keeps its texts.
        """
        if self._header is None or not self.isVisible():
            return
        compact = self.width() < max(NAV_COMPACT_WIDTH, self._full_header_width())
        if compact != self._compact_header:
            self._set_header_texts(compact=compact)

    def _full_header_width(self) -> int:
        if self._header is None:
            return 0
        if not self._compact_header:
            return self._header.minimumSizeHint().width()
        # Measured with the full texts, then back before anything is painted.
        self._set_header_texts(compact=False)
        width = self._header.minimumSizeHint().width()
        self._set_header_texts(compact=True)
        return width

    def _set_header_texts(self, *, compact: bool) -> None:
        self._compact_header = compact
        if self._header_layout is not None:
            self._header_layout.setSpacing(HEADER_COMPACT_SPACING if compact else HEADER_SPACING)
        self.quick_button.setText("" if compact else QUICK_CREATE_LABEL)
        self.quick_button.setToolTip(QUICK_CREATE_LABEL if compact else "")
        self._apply_nav_labels()
        self._refresh_microsoft_text()
        self._refresh_repertoire_tag()
        if self._header_layout is not None:
            # Nested layouts keep their sizes until the next layout pass otherwise.
            _invalidate_layout(self._header_layout)

    def _refresh_repertoire_tag(self) -> None:
        location = _repertoire_location(self._config)
        self.repertoire_tag.setText(
            location if self._compact_header else f"Répertoire · {location}",
        )
        self.repertoire_tag.setToolTip(f"Répertoire · {location}" if self._compact_header else "")

    def _apply_nav_labels(self) -> None:
        compact = self._compact_header
        for index in range(self.tabs.count()):
            nav_button = self._nav_group.button(index)
            full = self.tabs.tabText(index)
            text = NAV_COMPACT_LABELS.get(full, full) if compact else full
            if nav_button.text() != text:
                nav_button.setText(text)
                nav_button.setToolTip(full if text != full else "")

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_header_mode()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._apply_header_mode()
        self.presence_changed.emit()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        super().hideEvent(event)
        self.presence_changed.emit()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in {QEvent.Type.ActivationChange, QEvent.Type.WindowStateChange}:
            self.presence_changed.emit()

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
        self._refresh_repertoire_tag()
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
        self._apply_header_mode()

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


class NavBadge(QLabel):
    """Small accent count at the top right of a navigation button, hidden at 0."""

    def __init__(self, anchor: QAbstractButton) -> None:
        super().__init__(anchor)
        self._anchor = anchor
        self._count = 0
        self.setObjectName("navBadge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedHeight(NAV_BADGE_HEIGHT)
        anchor.installEventFilter(self)
        # A bound method: Qt disconnects it with the badge, a lambda would outlive it.
        theme_notifier().changed.connect(self._restyle)
        self._restyle()
        self.hide()

    @property
    def count(self) -> int:
        return self._count

    def set_count(self, count: int) -> None:
        self._count = max(0, count)
        visible = self._count > 0
        self.setText(str(self._count) if self._count <= NAV_BADGE_MAX else f"{NAV_BADGE_MAX}+")
        self.ensurePolished()
        width = max(
            NAV_BADGE_HEIGHT,
            self.fontMetrics().horizontalAdvance(self.text()) + NAV_BADGE_TEXT_MARGIN,
        )
        self.setFixedWidth(width)
        # The button grows by the badge so that the badge never covers its text.
        self._anchor.setStyleSheet(
            f"QPushButton {{ padding-right: {NAV_PADDING + NAV_BADGE_GAP + width}px; }}"
            if visible
            else "",
        )
        self._anchor.setAccessibleDescription(f"{self._count} nouveauté(s)" if visible else "")
        self.setVisible(visible)
        self._place()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._anchor and event.type() == QEvent.Type.Resize:
            self._place()
        return False

    def _place(self) -> None:
        self.move(self._anchor.width() - NAV_PADDING - self.width(), NAV_BADGE_TOP)
        self.raise_()

    def _restyle(self) -> None:
        theme = current_theme()
        self.setStyleSheet(
            "QLabel#navBadge {"
            f" background: {theme.tokens['accent']}; color: {theme.tokens['bg']};"
            f" border-radius: {NAV_BADGE_HEIGHT // 2}px; font-size: 11px; font-weight: 600;"
            " padding: 0 4px; }",
        )


def _invalidate_layout(layout: QLayout) -> None:
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item is None:
            continue
        child = item.layout()
        if child is not None:
            _invalidate_layout(child)
        item.invalidate()
    layout.invalidate()


def _repertoire_location(config: AppConfig) -> str:
    repertoire = config.paths.repertoire_chantier
    if repertoire.cloud_only or repertoire.drive_id or "://" in repertoire.display_path:
        return "OneDrive"
    if repertoire.display_path:
        return "Local"
    return "Non configuré"
