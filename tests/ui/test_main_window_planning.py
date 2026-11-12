from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QIcon, QKeySequence
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QPushButton

from projectflow.config import AppConfig, AppearanceConfig
from projectflow.ui.main_window import NAV_COMPACT_WIDTH, MainWindow
from projectflow.ui.planning.planning_tab import PlanningTab
from projectflow.ui.theme import apply_theme, theme_notifier
from projectflow.ui.tray import ProjectFlowTray


@pytest.fixture(autouse=True, scope="module")
def industry_theme(qapp: QApplication) -> Iterator[None]:
    """Use the real style sheet: the header widths depend on it."""
    # Styling repolishes every live widget: let the windows of earlier tests go first.
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    sheet, palette, font = qapp.styleSheet(), qapp.palette(), qapp.font()
    notifier = theme_notifier()
    blocked = notifier.blockSignals(True)  # noqa: FBT003 - widgets of earlier tests are gone
    try:
        apply_theme(qapp, AppearanceConfig(mode="light"))
    finally:
        notifier.blockSignals(blocked)
    yield
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.setStyleSheet(sheet)
    qapp.setPalette(palette)
    qapp.setFont(font)


def _window(qtbot: Any) -> MainWindow:
    window = MainWindow(AppConfig())
    qtbot.addWidget(window)
    return window


def _nav_texts(window: MainWindow) -> list[str]:
    buttons = window.findChildren(QPushButton)
    return [button.text() for button in buttons if button.property("variant") == "nav"]


def test_planning_is_the_fourth_and_last_tab(qtbot: Any) -> None:
    window = _window(qtbot)

    assert window.tabs.count() == 4
    assert window.tabs.indexOf(window.creation_tab) == 0
    assert window.tabs.indexOf(window.sortie_tab) == 1
    assert window.tabs.indexOf(window.repertoire_tab) == 2
    assert window.planning_index == 3
    assert isinstance(window.planning_tab, PlanningTab)
    assert window.tabs.tabText(3) == "Planning"
    assert _nav_texts(window) == [
        "Création projet",
        "Sortie dossier",
        "Répertoire chantier",
        "Planning",
    ]


def test_nav_button_opens_the_planning_tab(qtbot: Any) -> None:
    window = _window(qtbot)
    nav = [b for b in window.findChildren(QPushButton) if b.property("variant") == "nav"]

    nav[3].click()

    assert window.tabs.currentWidget() is window.planning_tab
    assert nav[3].isChecked()


def test_nav_badge_shows_a_count_and_hides_at_zero(qtbot: Any) -> None:
    window = _window(qtbot)
    window.show()
    qtbot.waitExposed(window)
    badge = window._nav_badges[window.planning_index]  # noqa: SLF001

    window.set_nav_badge(window.planning_index, 3)

    assert window.nav_badge_count(window.planning_index) == 3
    assert badge.isVisible()
    assert badge.text() == "3"
    anchor = badge.parentWidget()
    assert anchor is not None
    # Inside its button, right of the text, which the extra padding keeps clear.
    assert anchor.rect().contains(badge.geometry())
    assert "padding-right" in anchor.styleSheet()

    window.set_nav_badge(window.planning_index, 150)
    assert badge.text() == "99+"

    window.set_nav_badge(window.planning_index, 0)
    assert window.nav_badge_count(window.planning_index) == 0
    assert not badge.isVisible()
    assert anchor.styleSheet() == ""


def test_nav_badge_ignores_unknown_index(qtbot: Any) -> None:
    window = _window(qtbot)

    window.set_nav_badge(42, 5)

    assert window.nav_badge_count(42) == 0


def test_creation_shortcuts_are_disabled_while_planning_is_shown(qtbot: Any) -> None:
    window = _window(qtbot)
    actions = window.creation_actions
    sequences = {action.shortcut().toString() for action in actions}
    assert sequences == {
        QKeySequence(QKeySequence.StandardKey.Open).toString(),
        QKeySequence(QKeySequence.StandardKey.Print).toString(),
        "Ctrl+R",
        "Ctrl+L",
        "Ctrl+S",
    }
    assert all(action.isEnabled() for action in actions)

    window.tabs.setCurrentWidget(window.planning_tab)
    assert not any(action.isEnabled() for action in actions)
    settings = [a for a in window.actions() if a.shortcut().toString() == "Ctrl+,"]
    assert settings
    assert settings[0].isEnabled()

    window.tabs.setCurrentWidget(window.repertoire_tab)
    assert all(action.isEnabled() for action in actions)


def test_ctrl_s_in_planning_never_asks_to_rewrite_the_fiche(qtbot: Any) -> None:
    window = _window(qtbot)
    window.show()
    qtbot.waitExposed(window)
    window.activateWindow()
    update_spy = QSignalSpy(window.creation_tab.update_requested)
    update_action = next(a for a in window.creation_actions if a.shortcut().toString() == "Ctrl+S")

    window.tabs.setCurrentWidget(window.planning_tab)
    update_action.trigger()  # A disabled action does nothing, whatever sends it.
    qtbot.keyClick(window.planning_tab, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    QApplication.processEvents()

    assert update_spy.count() == 0


def test_header_shortens_its_texts_at_the_minimum_width(qtbot: Any) -> None:
    window = _window(qtbot)
    window.set_microsoft_status("connected", "prenom.nom-tres-long@balzmetal.ch")
    window.set_nav_badge(window.planning_index, 12)
    window.resize(1000, 700)
    window.show()
    qtbot.waitExposed(window)
    QApplication.processEvents()

    assert window.width() < NAV_COMPACT_WIDTH
    assert window.compact_header
    assert "Répertoire" in _nav_texts(window)
    assert "Répertoire chantier" not in _nav_texts(window)
    assert window.microsoft_button.text() == "Microsoft"
    assert "prenom.nom-tres-long@balzmetal.ch" in window.microsoft_button.toolTip()
    header = window.centralWidget().layout().itemAt(0).widget()
    assert header.minimumSizeHint().width() <= window.width()

    window.resize(1400, 700)
    QApplication.processEvents()

    assert not window.compact_header
    assert "Répertoire chantier" in _nav_texts(window)
    assert window.microsoft_button.text() == "Microsoft · prenom.nom-tres-long@balzmetal.ch"
    assert header.minimumSizeHint().width() <= window.width()


def test_presence_changes_are_announced(qtbot: Any) -> None:
    window = _window(qtbot)
    spy = QSignalSpy(window.presence_changed)

    window.show()
    qtbot.waitExposed(window)
    window.hide()

    assert spy.count() >= 2


def test_show_planning_tab_raises_the_window_on_the_planning(qtbot: Any) -> None:
    window = _window(qtbot)

    window.show_planning_tab()

    assert window.isVisible()
    assert window.tabs.currentWidget() is window.planning_tab


def test_tray_menu_opens_the_planning(qapp: QApplication) -> None:
    tray = ProjectFlowTray(icon=QIcon(), parent=qapp)
    spy = QSignalSpy(tray.open_planning_requested)
    actions = {action.text(): action for action in tray._menu.actions()}  # noqa: SLF001

    actions["Ouvrir le planning"].trigger()

    assert spy.count() == 1
    tray.deleteLater()
