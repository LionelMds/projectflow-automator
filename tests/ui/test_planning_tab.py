from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCalendarWidget,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from pytestqt.exceptions import TimeoutError as WaitTimeoutError

from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.layout import UNASSIGNED_ROW_ID
from projectflow.core.planning.models import PlanningMeta, PlanningTask, ProjectCache, Resource
from projectflow.core.planning.sync import SyncStatus
from projectflow.core.planning.walltime import wall_time
from projectflow.ui.planning.grid_model import build_grid_model
from projectflow.ui.planning.planning_tab import (
    DIMMED_OPACITY,
    PlanningTab,
    matches_filter,
    period_text,
    selection_summary,
)
from projectflow.ui.planning.side_panel import RAIL_WIDTH
from projectflow.ui.theme import load_fonts

TODAY = date(2026, 10, 22)
WEEK = date(2026, 10, 19)
STAMP = "2026-10-20T08:00:00Z"


def at(day: date, hour: int, minute: int = 0) -> int:
    return wall_time(day, hour, minute)


def resource(rid: str, name: str, team: str, order: int) -> Resource:
    return Resource.model_validate(
        {"id": rid, "name": name, "initials": rid.upper(), "team": team, "order": order}
    )


def task(  # noqa: PLR0917 - compact rows
    tid: str, number: str, client: str, title: str, people: list[str], day: int
) -> PlanningTask:
    start = at(WEEK + timedelta(days=day), 6, 30)
    return PlanningTask.model_validate(
        {
            "id": tid,
            "project_number": number,
            "project_cache": ProjectCache(client=client).model_dump(),
            "title": title,
            "start": start,
            "end": at(WEEK + timedelta(days=day), 12),
            "assignments": {person: {} for person in people},
            "created_at": STAMP,
            "updated_at": STAMP,
        }
    )


def snapshot() -> PlanningSnapshot:
    people = [
        resource("mre", "Marc Rey", "atelier", 1000),
        resource("kdu", "Karim Dumas", "montage", 1000),
        resource("lbi", "Luca Bianchi", "montage", 2000),
    ]
    tasks = [
        task("t1", "2026-118", "Müller SA", "Fabrication garde-corps", ["mre"], 0),
        task("t2", "2026-121", "Commune de Sion", "Pose marquise", ["kdu", "lbi"], 1),
        task("t3", "2026-124", "PPE Les Vergers", "Livraison portail", [], 2),
    ]
    card = PlanningTask.model_validate(
        {"id": "c1", "project_number": "2026-129", "title": "Main courante", "estimate_days": 2}
    )
    return PlanningSnapshot(
        tasks={item.id: item for item in (*tasks, card)},
        resources={item.id: item for item in people},
        meta=PlanningMeta(),
    )


@pytest.fixture
def tab(qtbot: Any) -> PlanningTab:
    load_fonts()
    widget = PlanningTab()
    qtbot.addWidget(widget)
    widget.resize(1440, 860)
    widget.show()
    qtbot.waitExposed(widget)
    widget.set_today(TODAY)
    return widget


def show_model(tab: PlanningTab) -> None:
    snap = snapshot()
    model = build_grid_model(
        snap,
        view=tab.view,
        anchor=tab.anchor,
        viewport_width=tab.grid.viewport_width(),
        today=TODAY,
        now=at(TODAY, 10),
    )
    tab.set_model(model)
    tab.backlog.set_snapshot(snap, today=TODAY)


class Recorder:
    def __init__(self, *signals: Any) -> None:
        self.calls: list[tuple[Any, ...]] = []
        for signal in signals:
            signal.connect(lambda *args: self.calls.append(args))


# ----------------------------------------------------------------- texts ----


def test_period_texts() -> None:
    assert period_text("week", TODAY) == "Semaine 43 · 19 – 23 oct. 2026"  # noqa: RUF001
    assert period_text("week", date(2026, 10, 1)) == "Semaine 40 · 28 sept. – 2 oct. 2026"  # noqa: RUF001
    assert (
        period_text("week", date(2026, 12, 31)) == "Semaine 53 · 28 déc. 2026 – 1 janv. 2027"  # noqa: RUF001
    )
    assert period_text("day", TODAY) == "Jeudi 22 octobre 2026"
    assert period_text("month", TODAY) == "Octobre 2026"
    assert period_text("week", TODAY, short=True) == "S43 · 19 – 23 oct."  # noqa: RUF001
    assert period_text("week", date(2026, 10, 1), short=True) == "S40 · 28 sept. – 2 oct."  # noqa: RUF001
    assert period_text("day", TODAY, short=True) == "Jeu 22 oct. 2026"
    assert period_text("month", TODAY, short=True) == "Oct. 2026"


# ---------------------------------------------------------------- layout ----


def test_layout_assembles_the_planning_widgets(tab: PlanningTab) -> None:
    grid_item = tab.content_layout.itemAt(0)
    side_item = tab.content_layout.itemAt(1)
    assert grid_item is not None
    assert side_item is not None
    assert grid_item.widget() is tab.grid
    assert side_item.widget() is tab.side
    assert tab.backlog is tab.side.backlog
    assert tab.detail is tab.side.detail
    assert tab.toasts.parentWidget() is tab
    assert tab.editor.parentWidget() is tab
    assert tab.hover.parentWidget() is tab
    assert not tab.editor.isVisible()
    assert tab.period_button.text().startswith("Semaine 43 · 19 – 23 oct. 2026")  # noqa: RUF001
    assert tab.view_buttons["week"].isChecked()
    assert tab.color_buttons["type"].isChecked()
    assert tab.badge.text() == "Planning local (ce poste)"
    for widget in (tab.quick, tab.status_bar, tab.filter_edit, tab.undo_button, tab.badge):
        assert widget.isVisible()
    # The toolbar sits above the quick entry, which spans the width of the tab.
    assert tab.toolbar.geometry().bottom() < tab.quick.mapTo(tab, tab.quick.rect().topLeft()).y()
    assert tab.quick.width() > tab.width() - 60
    assert tab.identity_banner.isHidden()
    assert tab.read_only_banner.isHidden()


def settle() -> None:
    """Let the layouts run (a panel that sets its own width is laid out on the next pass)."""
    for _ in range(3):
        QApplication.processEvents()


def test_folding_or_narrowing_the_panel_gives_the_room_to_the_grid(tab: PlanningTab) -> None:
    show_model(tab)
    settle()
    content = tab.content.width()
    assert tab.side.width() == tab.side.panel_width()
    assert tab.grid.width() == content - tab.side.width()

    tab.shortcuts["panel"][0].activated.emit()  # Ctrl+B: the rail
    settle()
    assert tab.side.is_collapsed()
    assert tab.side.width() == RAIL_WIDTH
    assert tab.grid.width() == content - RAIL_WIDTH
    tab.side.toggle()
    settle()
    assert tab.grid.width() == content - tab.side.panel_width()

    tab.side.set_panel_width(260)  # what the grip does
    settle()
    assert tab.side.width() == 260
    assert tab.grid.width() == content - 260
    assert tab.side.geometry().right() == tab.content.rect().right()


def test_a_narrow_tab_starts_with_the_rail_and_opens_it_over_the_grid(
    tab: PlanningTab,
) -> None:
    show_model(tab)
    seen: list[tuple[bool, bool]] = []
    tab.side.collapsed_changed.connect(
        lambda collapsed: seen.append((collapsed, tab.panel_adapting))
    )
    tab.resize(1000, 760)
    settle()
    # Folded for lack of room, not by choice: the poste's preference is left alone.
    assert tab.side.is_collapsed()
    assert seen == [(True, True)]
    assert not tab.panel_adapting
    content = tab.content.width()
    assert tab.grid.width() == content - RAIL_WIDTH

    tab.side.set_collapsed(False)  # opened from its rail
    settle()
    assert seen[-1] == (False, False)
    assert tab.content_layout.is_floating_open()
    # Over the grid, which keeps its width beside the rail.
    assert tab.grid.width() == content - RAIL_WIDTH
    assert tab.side.width() == tab.side.panel_width()
    assert tab.side.geometry().left() < tab.grid.geometry().right()
    assert tab.side.geometry().right() == tab.content.rect().right()

    tab.side.set_collapsed(True)
    tab.resize(1440, 860)
    settle()
    # Folded by hand: widening the tab does not open it again.
    assert tab.side.is_collapsed()
    tab.side.set_collapsed(False)
    settle()
    assert not tab.content_layout.is_floating_open()
    assert tab.grid.width() == tab.content.width() - tab.side.width()

    # Folded by the tab only: it opens again once there is room.
    tab.resize(1000, 760)
    settle()
    assert tab.side.is_collapsed()
    tab.resize(1440, 860)
    settle()
    assert not tab.side.is_collapsed()
    assert seen[-1] == (False, True)


def test_the_grid_takes_the_focus_when_the_tab_is_shown(qtbot: Any) -> None:
    load_fonts()
    window = QWidget()
    qtbot.addWidget(window)
    nav = QPushButton("Planning", window)
    pages = QStackedWidget(window)
    other = QLineEdit()
    planning = PlanningTab()
    pages.addWidget(other)
    pages.addWidget(planning)
    layout = QVBoxLayout(window)
    layout.addWidget(nav)
    layout.addWidget(pages)
    window.resize(1440, 900)
    window.show()
    qtbot.waitExposed(window)
    nav.setFocus()
    assert window.focusWidget() is nav
    pages.setCurrentWidget(planning)  # what a click on the navigation button does
    assert window.focusWidget() is planning.grid.view
    planning.quick.line_edit.setFocus()
    planning.show()  # shown again (already visible): the focus is left where it is
    assert window.focusWidget() is planning.quick.line_edit
    pages.setCurrentWidget(other)
    other.setFocus()
    pages.setCurrentWidget(planning)
    assert window.focusWidget() is planning.grid.view


def test_toolbar_hides_optional_controls_when_narrow(tab: PlanningTab) -> None:
    tab.resize(2400, 860)
    assert tab.color_box.isVisible()
    assert tab.print_button.isVisible()
    assert tab.team_button.isVisible()
    tab.resize(640, 700)
    assert not tab.color_box.isVisible()
    assert not tab.print_button.isVisible()
    assert tab.period_button.text() == "S43 · 19 – 23 oct."  # noqa: RUF001
    assert tab.period_button.isVisible()
    assert tab.badge.isVisible()
    assert tab.filter_edit.isVisible()
    # Still reachable from the display menu.
    texts = [action.text() for action in tab.display_menu.actions()]
    assert "Dossier" in texts
    assert any(text.startswith("Imprimer") for text in texts)
    assert "Équipe…" in texts
    tab.resize(2400, 860)
    assert tab.color_box.isVisible()


# ------------------------------------------------------------- shortcuts ----


def test_shortcuts_are_scoped_to_the_tab_and_leave_delete_alone(tab: PlanningTab) -> None:
    sequences: list[str] = []
    for shortcuts in tab.shortcuts.values():
        for shortcut in shortcuts:
            assert shortcut.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut
            sequences.append(shortcut.key().toString(QKeySequence.SequenceFormat.PortableText))
    assert "Ctrl+K" in sequences
    assert "Ctrl+Shift+Z" in sequences
    assert "PgUp" in sequences
    assert "F1" in sequences
    assert not {"Del", "Backspace", "Shift+Del"} & set(sequences)


def test_shortcuts_dispatch(tab: PlanningTab) -> None:
    recorder = Recorder(
        tab.undo_requested,
        tab.redo_requested,
        tab.print_requested,
        tab.save_requested,
    )
    views = Recorder(tab.view_changed)

    tab.shortcuts["month"][0].activated.emit()
    assert tab.view == "month"
    assert tab.anchor == date(2026, 10, 1)
    tab.shortcuts["day"][0].activated.emit()
    assert tab.view == "day"
    assert tab.anchor == TODAY
    tab.shortcuts["next"][0].activated.emit()
    assert tab.anchor == date(2026, 10, 23)
    tab.shortcuts["next"][0].activated.emit()
    assert tab.anchor == date(2026, 10, 26)  # the weekend is skipped
    tab.shortcuts["week"][0].activated.emit()
    assert tab.view == "week"
    assert tab.anchor == date(2026, 10, 26)
    tab.shortcuts["previous"][0].activated.emit()
    tab.shortcuts["previous"][0].activated.emit()
    assert tab.anchor == date(2026, 10, 12)
    tab.shortcuts["today"][0].activated.emit()
    assert tab.anchor == WEEK
    assert len(views.calls) == 8

    for name in ("undo", "redo", "print", "save"):
        tab.shortcuts[name][0].activated.emit()
    tab.shortcuts["redo"][1].activated.emit()
    assert len(recorder.calls) == 5

    tab.shortcuts["panel"][0].activated.emit()
    assert tab.side.is_collapsed()
    tab.shortcuts["panel"][0].activated.emit()
    assert not tab.side.is_collapsed()

    tab.shortcuts["help"][0].activated.emit()
    assert tab.shortcuts_visible()
    assert any("Ctrl+K" in text or "Cmd+K" in text for text in tab.sheet.texts())
    tab.shortcuts["help"][0].activated.emit()
    assert not tab.shortcuts_visible()


def test_keyboard_shortcuts_reach_the_tab(qtbot: Any, tab: PlanningTab) -> None:
    tab.activateWindow()
    try:
        qtbot.waitUntil(tab.isActiveWindow, timeout=2000)
    except WaitTimeoutError:
        pytest.skip("Ce système de fenêtres ne rend pas l'onglet actif : raccourcis inactifs.")
    tab.grid.view.setFocus()
    qtbot.waitUntil(tab.grid.view.hasFocus, timeout=2000)
    undo = Recorder(tab.undo_requested)
    qtbot.keyClick(tab.grid.view, Qt.Key.Key_3, Qt.KeyboardModifier.ControlModifier)
    assert tab.view == "month"
    qtbot.keyClick(tab.grid.view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert undo.calls == [()]
    qtbot.keyClick(tab.grid.view, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(tab.quick.line_edit.hasFocus, timeout=2000)
    qtbot.keyClick(tab.quick.line_edit, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(tab.filter_edit.hasFocus, timeout=2000)
    qtbot.keyClick(tab.filter_edit, Qt.Key.Key_F1)
    assert tab.shortcuts_visible()
    qtbot.keyClick(tab.sheet, Qt.Key.Key_Escape)
    assert not tab.shortcuts_visible()


def test_a_letter_typed_on_the_grid_asks_for_the_cell_editor(qtbot: Any, tab: PlanningTab) -> None:
    show_model(tab)
    typed = Recorder(tab.cell_typed)
    qtbot.keyClick(
        tab.grid.view,
        Qt.Key.Key_A,
        Qt.KeyboardModifier.NoModifier,
    )
    assert typed.calls == [("a",)]
    qtbot.keyClick(tab.grid.view, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert typed.calls == [("a",)]
    tab.set_read_only(True, "Hors ligne")  # noqa: FBT003
    qtbot.keyClick(tab.grid.view, Qt.Key.Key_B)
    assert typed.calls == [("a",)]


# ---------------------------------------------------------------- filter ----


def test_filter_dims_bars_without_accents_and_leaves_the_card_search_alone(
    tab: PlanningTab,
) -> None:
    show_model(tab)
    model = tab.grid.model
    assert model is not None
    bar = model.bar("t1", "mre")
    assert bar is not None
    assert matches_filter(bar, "muller", model)
    assert matches_filter(bar, "MÜLLER garde", model)
    assert matches_filter(bar, "marc", model)
    assert not matches_filter(bar, "sion", model)
    assert tab.filter_edit.placeholderText() == "Filtrer…"
    assert "dossier, client, titre ou initiales" in tab.filter_edit.toolTip()

    tab.backlog.search_edit.setText("main")  # the panel's own search
    changes = Recorder(tab.filter_changed)
    tab.filter_edit.setText("muller")
    assert changes.calls == [("muller",)]
    assert tab.dimmed_bars()
    assert ("t1", "mre") not in tab.dimmed_bars()
    assert ("t2", "kdu") in tab.dimmed_bars()
    item = tab.grid.scene.items_by_key[("t3", UNASSIGNED_ROW_ID)]
    assert item.opacity() == pytest.approx(DIMMED_OPACITY)
    # « À planifier » keeps what was typed in it; the filter only names the cards to dim.
    assert tab.backlog.search_edit.text() == "main"
    assert tab.backlog.filters().search == "main"
    assert tab.unmatched_cards() == frozenset({"c1"})
    # A new model keeps the filter.
    show_model(tab)
    assert ("t2", "kdu") in tab.dimmed_bars()
    tab.filter_edit.clear()
    assert not tab.dimmed_bars()
    assert tab.unmatched_cards() == frozenset()
    assert tab.backlog.search_edit.text() == "main"


# ---------------------------------------------------------- feedback ----


def test_identity_and_read_only_banners(tab: PlanningTab) -> None:
    settings = Recorder(tab.settings_requested)
    retry = Recorder(tab.retry_requested)
    tab.set_identity(identified=False)
    assert not tab.identity_banner.isHidden()
    assert "pas identifié" in tab.identity_banner.message_label.text()
    tab.identity_button.click()
    assert settings.calls == [()]
    tab.set_identity(identified=True, label_text="LM · BUREAU-1")
    assert tab.identity_banner.isHidden()
    assert tab.identity_label.text() == "Poste : LM · BUREAU-1"

    tab.set_read_only(True, "Autorisation Microsoft manquante")  # noqa: FBT003
    assert not tab.read_only_banner.isHidden()
    assert "Autorisation Microsoft manquante" in tab.read_only_banner.message_label.text()
    assert tab.quick.line_edit.isReadOnly()
    assert tab.grid.read_only
    tab.retry_button.click()
    assert retry.calls == [()]
    assert "Lecture seule" in tab.status_texts()[1]
    tab.set_read_only(False)
    assert tab.read_only_banner.isHidden()
    assert not tab.quick.line_edit.isReadOnly()


def test_sync_badge_and_undo_tooltips(tab: PlanningTab) -> None:
    tab.set_sync_status(SyncStatus("forbidden", None, 0, "Autorisation manquante"), local=False)
    assert tab.badge.text() == "Autorisation Microsoft manquante"
    retry = Recorder(tab.retry_requested)
    tab.badge.clicked.emit()
    assert retry.calls == [()]
    tab.set_undo_labels("Déplacer 2026-118 Pose", None)
    assert tab.undo_button.isEnabled()
    assert "Annuler : Déplacer 2026-118 Pose" in tab.undo_button.toolTip()
    assert not tab.redo_button.isEnabled()
    assert tab.redo_button.toolTip() == "Rien à rétablir"


def test_status_bar_summarises_the_selection(tab: PlanningTab) -> None:
    show_model(tab)
    selection, hints = tab.status_texts()
    assert "Semaine 43" in selection
    assert "Ctrl+K saisir" in hints or "Cmd+K saisir" in hints
    tab.grid.select([("t2", "kdu")])
    selection, hints = tab.status_texts()
    assert selection.startswith("Sélection : 121 Pose marquise · KDU, LBI · mar 20.10 06:30")
    assert "Suppr retirer KDU" in hints
    assert selection_summary(tab.grid.model, [("t1", "mre"), ("t2", "kdu")]) == (
        "Sélection : 2 barres (2 tâches)"
    )


def test_toasts_stay_above_the_editor(qtbot: Any, tab: PlanningTab) -> None:
    show_model(tab)
    toast = tab.show_toast("Test", action_text="Annuler", action=lambda: None)
    assert toast.isVisible()
    assert tab.toasts.isVisible()
    tab.side.set_collapsed(True)
    right = tab.width() - tab.toasts.geometry().right()
    assert right < 80
    QApplication.processEvents()


def test_period_label_opens_a_calendar_with_week_numbers(tab: PlanningTab) -> None:
    tab.period_button.click()
    popup = tab.calendar_popup
    assert popup.isVisible()
    calendar = popup.calendar
    assert calendar.verticalHeaderFormat() == QCalendarWidget.VerticalHeaderFormat.ISOWeekNumbers
    assert calendar.firstDayOfWeek() == Qt.DayOfWeek.Monday
    views = Recorder(tab.view_changed)
    popup.day_chosen.emit(date(2026, 11, 4))
    assert tab.anchor == date(2026, 11, 2)
    assert views.calls == [()]
    assert tab.period_button.text().startswith("Semaine 45")


def test_calendar_jumps_to_a_week_number(tab: PlanningTab) -> None:
    tab.period_button.click()
    popup = tab.calendar_popup
    assert (popup.week_spin.value(), popup.year_spin.value()) == (
        tab.anchor.isocalendar()[1],
        tab.anchor.isocalendar()[0],
    )
    popup.week_spin.setValue(50)
    popup.year_spin.setValue(2026)
    popup._go_to_week()  # noqa: SLF001
    assert tab.anchor == date(2026, 12, 7)
    assert not popup.isVisible()
    # A week 53 that the year lacks shows its last week instead.
    popup._choose_week(2025, 53)  # noqa: SLF001
    assert tab.anchor == date(2025, 12, 22)


def test_calendar_cells_are_not_padded_into_ellipses(tab: PlanningTab) -> None:
    sheet = tab.calendar_popup.calendar.styleSheet()
    assert "padding: 0" in sheet
    assert tab.calendar_popup.calendar.minimumWidth() >= 400


def test_cell_cursor_follows_the_last_empty_cell_or_the_current_bar(
    qtbot: Any, tab: PlanningTab
) -> None:
    show_model(tab)
    model = tab.grid.model
    assert model is not None
    row = model.rows.get("mre")
    assert row is not None
    friday = WEEK + timedelta(days=4)
    point = tab.grid.view.mapFromScene(
        model.timescale.x_of(at(friday, 9)), row.top + row.height / 2
    )
    qtbot.mouseClick(tab.grid.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    cell = tab.cell_cursor()
    assert cell is not None
    assert cell[0] == "mre"
    assert friday == date.fromordinal(cell[1] // 1440)
    tab.grid.select([("t2", "kdu")])
    assert tab.cell_cursor() == ("kdu", at(WEEK + timedelta(days=1), 12))
