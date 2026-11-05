from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from PySide6.QtCore import QByteArray, QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

from projectflow.core.planning.calendar import WorkCalendar
from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.models import (
    Author,
    PlanningMeta,
    PlanningTask,
    ProjectCache,
    Resource,
)
from projectflow.core.planning.walltime import wall_time
from projectflow.ui.planning import backlog
from projectflow.ui.planning.backlog import (
    BACKLOG_SOURCE,
    DROP_HINT,
    EMPTY_TEXT,
    FILTERED_EMPTY_TEXT,
    READ_ONLY_EMPTY_TEXT,
    BacklogFilters,
    BacklogPanel,
    build_cards,
    card_pixmap,
    deadline_state,
    format_estimate,
    group_cards,
    origin_text,
    relative_time,
)
from projectflow.ui.planning.grid import TASK_MIME, parse_task_mime, task_mime_data
from projectflow.ui.theme import apply_theme, current_theme

TODAY = date(2026, 10, 22)  # a Thursday
NOW = datetime(2026, 10, 22, 8, 15, tzinfo=UTC)
SBE = Author(initials="SBE", name="Sophie Berset", poste="BUREAU-2")
JD = Author(initials="JD", name="Julien Dubois", poste="ATELIER-1")
GE = WorkCalendar(cantons=("GE",))

RESOURCES = {
    resource.id: resource
    for resource in (
        Resource(id="nf", name="Nicolas Favre", initials="NF", team="montage", order=1),
        Resource(id="sg", name="Samuel Gay", initials="SG", team="montage", order=2),
        Resource(id="mr", name="Marc Rey", initials="MR", team="atelier", order=3),
        Resource(id="lb", name="Laura Bron", initials="LB", team="bureau", order=4),
    )
}


def _task(task_id: str, **fields: Any) -> PlanningTask:
    data: dict[str, Any] = {"id": task_id, "created_at": "2026-10-20T07:00:00Z"} | fields
    return PlanningTask.model_validate(data)


def _tasks() -> list[PlanningTask]:
    return [
        _task(
            "escalier",
            backlog_rank="a0",
            project_number="2026-5244",
            project_cache=ProjectCache(
                client="Rossier",
                designation="Escalier extérieur",
                managed_by="LM",
                atelier_release="2026-10-14",
            ),
            title="Escalier ext. - fabrication",
            task_type="soudure",
            estimate_days=3,
            deadline=date(2026, 11, 6),
            origin="sortie_dossier",
            created_at="2026-10-14T09:00:00Z",
        ),
        _task(
            "barriere",
            backlog_rank="a1",
            project_number="2026-5249",
            project_cache=ProjectCache(client="Gillioz", managed_by="LM"),
            title="Barrière parking souterrain",
            task_type="montage",
            estimate_days=2,
            required_people=2,
            assignments={"nf": {}, "sg": {}},
            deadline=date(2026, 10, 28),
            status="provisional",
            created_at="2026-10-22T08:10:00Z",
            created_by=SBE,
            origin="quick_entry",
        ),
        _task(
            "portail",
            backlog_rank="a2",
            project_number="2026-5198-2",
            project_cache=ProjectCache(client="Bétrisey", managed_by="SBE"),
            title="SAV portail - réglage",
            task_type="sav",
            estimate_minutes=240,
            urgent=True,
            deadline=date(2026, 10, 20),
            created_at="2026-10-21T13:05:00Z",
            created_by=JD,
        ),
        _task(
            "rangement",
            backlog_rank="a3",
            title="Rangement stock tubes",
            task_type="interne",
            created_at="2026-10-19T07:00:00Z",
        ),
        _task(
            "garde-corps",
            backlog_rank="a4",
            project_number="2026-98",
            project_cache=ProjectCache(client="Müller SA", managed_by="SBE"),
            title="Pose garde-corps",
            task_type="montage",
            estimate_days=1.5,
            deadline=date(2026, 10, 30),
            created_at="2026-10-21T10:00:00Z",
        ),
        _task(
            "planned",
            project_number="2026-5231",
            title="Fab. garde-corps",
            start=wall_time(date(2026, 10, 19), 6, 30),
            end=wall_time(date(2026, 10, 21), 16, 30),
            assignments={"mr": {}},
        ),
        _task("gone", title="Supprimée", deleted_at="2026-10-21T10:00:00Z"),
    ]


def _snapshot(tasks: list[PlanningTask] | None = None) -> PlanningSnapshot:
    return PlanningSnapshot(
        tasks={task.id: task for task in tasks or _tasks()},
        resources=RESOURCES,
        meta=PlanningMeta(),
    )


def _ids(cards: list[Any]) -> list[str]:
    return [card.task_id for card in cards]


@pytest.fixture(autouse=True)
def industry_theme(qapp: QApplication) -> Iterator[None]:
    sheet, palette, font = qapp.styleSheet(), qapp.palette(), qapp.font()
    apply_theme(qapp)
    yield
    qapp.setStyleSheet(sheet)
    qapp.setPalette(palette)
    qapp.setFont(font)


def _panel(qtbot: Any, *, snapshot: PlanningSnapshot | None = None) -> BacklogPanel:
    panel = BacklogPanel()
    qtbot.addWidget(panel)
    panel.resize(312, 760)
    panel.set_snapshot(snapshot or _snapshot(), today=TODAY, now=NOW)
    panel.show()
    qtbot.waitExposed(panel)
    return panel


def _bar_mime(task_id: str) -> QMimeData:
    return task_mime_data(task_id, source="grid", resource_id="mr")


# ------------------------------------------------------------- building ----


def test_cards_keep_the_shared_manual_order_and_skip_planned_and_deleted() -> None:
    cards = build_cards(_snapshot(), today=TODAY, now=NOW)

    assert _ids(cards) == ["escalier", "barriere", "portail", "rangement", "garde-corps"]


def test_card_content_follows_the_prototype() -> None:
    cards = {card.task_id: card for card in build_cards(_snapshot(), today=TODAY, now=NOW)}

    escalier = cards["escalier"]
    assert (escalier.number, escalier.client, escalier.duration) == ("2026-5244", "Rossier", "~3 j")
    assert escalier.origin == "auto (sortie dossier)"
    assert [(chip.text, chip.kind) for chip in escalier.chips] == [
        ("avant 06.11", "deadline"),
        ("Sorti 14.10", "released"),
    ]
    barriere = cards["barriere"]
    assert barriere.origin == "par SBE · BUREAU-2 · il y a 5 min"
    assert [(chip.text, chip.kind) for chip in barriere.chips] == [
        ("2 pers.", "people"),
        ("NF", "foreseen"),
        ("SG", "foreseen"),
        ("avant 28.10", "soon"),
    ]
    assert barriere.team == "montage"
    portail = cards["portail"]
    assert portail.duration == "~4 h"
    assert portail.origin == "par JD · ATELIER-1 · hier à " + datetime(
        2026, 10, 21, 13, 5, tzinfo=UTC
    ).astimezone().strftime("%H:%M")
    assert [chip.kind for chip in portail.chips] == ["urgent", "late"]
    assert cards["rangement"].duration == "?"
    assert cards["rangement"].number == ""
    assert cards["garde-corps"].duration == "~1 j ½"


def test_estimate_prefers_working_days_then_hours() -> None:
    assert format_estimate(_task("a", estimate_days=0.5)) == "~½ j"
    assert format_estimate(_task("b", estimate_days=2, estimate_minutes=60)) == "~2 j"
    assert format_estimate(_task("c", estimate_minutes=90)) == "~1 h 30"
    assert format_estimate(_task("d")) == "?"


def test_deadline_turns_amber_within_five_working_days_and_red_once_past() -> None:
    # From Thursday 22.10: Fri, Mon, Tue, Wed, Thu are the five working days left.
    assert deadline_state(date(2026, 10, 22), TODAY, GE) == "soon"
    assert deadline_state(date(2026, 10, 29), TODAY, GE) == "soon"
    assert deadline_state(date(2026, 10, 30), TODAY, GE) == "normal"
    assert deadline_state(date(2026, 10, 21), TODAY, GE) == "late"
    assert deadline_state(None, TODAY, GE) == "none"
    # A weekend does not count, Christmas neither: 29.12 is five working days after 21.12.
    assert deadline_state(date(2026, 12, 29), date(2026, 12, 21), GE) == "soon"
    no_holiday = WorkCalendar(cantons=())
    assert deadline_state(date(2026, 12, 29), date(2026, 12, 21), no_holiday) == "normal"


def test_sorts_by_deadline_dossier_and_date_added() -> None:
    snapshot = _snapshot()

    by_deadline = build_cards(snapshot, today=TODAY, now=NOW, sort="deadline")
    by_dossier = build_cards(snapshot, today=TODAY, now=NOW, sort="dossier")
    by_added = build_cards(snapshot, today=TODAY, now=NOW, sort="added")

    assert _ids(by_deadline) == ["portail", "barriere", "garde-corps", "escalier", "rangement"]
    # Natural order of the numbers (98 before 5198), tasks without dossier last.
    assert _ids(by_dossier) == ["garde-corps", "portail", "escalier", "barriere", "rangement"]
    assert _ids(by_added) == ["barriere", "portail", "garde-corps", "rangement", "escalier"]


def test_search_ignores_accents_and_case_and_needs_every_word() -> None:
    snapshot = _snapshot()

    def found(text: str) -> list[str]:
        filters = BacklogFilters(search=text)
        return _ids(build_cards(snapshot, today=TODAY, now=NOW, filters=filters))

    assert found("betrisey") == ["portail"]
    assert found("REGLAGE") == ["portail"]
    assert found("muller") == ["garde-corps"]
    assert found("Barriere nf") == ["barriere"]
    assert found("5244 rossier") == ["escalier"]
    assert found("escalier exterieur") == ["escalier"]
    assert found("parking rossier") == []
    keyword = build_cards(snapshot, today=TODAY, now=NOW, search="GILLIOZ")
    assert _ids(keyword) == ["barriere"]


def test_filters_by_team_urgency_and_my_dossiers() -> None:
    snapshot = _snapshot()

    def found(filters: BacklogFilters, initials: str = "") -> list[str]:
        cards = build_cards(snapshot, today=TODAY, now=NOW, filters=filters, my_initials=initials)
        return _ids(cards)

    assert found(BacklogFilters(teams=frozenset({"montage"}))) == [
        "barriere",
        "portail",
        "garde-corps",
    ]
    assert found(BacklogFilters(teams=frozenset({"atelier"}))) == ["escalier"]
    assert found(BacklogFilters(urgent_only=True)) == ["portail"]
    assert found(BacklogFilters(mine=True), "sbe") == ["portail", "garde-corps"]
    assert found(BacklogFilters(mine=True)) == []
    assert BacklogFilters().active is False
    assert BacklogFilters(search="  x ").active is True


def test_groups_by_dossier_and_by_type() -> None:
    cards = build_cards(_snapshot(), today=TODAY, now=NOW)

    by_dossier = group_cards(cards, "dossier")
    by_type = group_cards(
        cards, "type", type_order=[kind.key for kind in PlanningMeta().task_types]
    )

    assert [group.label for group in by_dossier] == [
        "2026-98 Müller SA",
        "2026-5198-2 Bétrisey",
        "2026-5244 Rossier",
        "2026-5249 Gillioz",
        "Sans dossier",
    ]
    assert [(group.key, len(group.cards)) for group in by_type] == [
        ("soudure", 1),
        ("montage", 2),
        ("sav", 1),
        ("interne", 1),
    ]


def test_relative_times_and_origin() -> None:
    assert relative_time("2026-10-22T08:14:30Z", NOW) == "à l'instant"
    assert relative_time("2026-10-22T08:10:00Z", NOW) == "il y a 5 min"
    assert relative_time("", NOW) == ""
    assert relative_time("pas une date", NOW) == ""
    assert relative_time("2026-10-01T08:00:00Z", NOW) == "le 01.10.2026"
    assert origin_text(_task("x", created_at="2026-10-22T08:10:00Z"), NOW) == "ajouté il y a 5 min"


def test_task_mime_is_the_one_of_the_grid() -> None:
    # One format for both ends of a drag: the backlog has no copy of the grid's helpers.
    assert not hasattr(backlog, "TASK_MIME")
    assert not hasattr(backlog, "TaskDragPayload")
    payload = parse_task_mime(task_mime_data("t1", source=BACKLOG_SOURCE, resource_id="mr"))
    assert payload is not None
    assert (payload.task_id, payload.source, payload.resource_id) == ("t1", "backlog", "mr")

    plain = QMimeData()
    plain.setData(TASK_MIME, QByteArray(b"t2"))
    other = QMimeData()
    other.setText("t4")
    assert parse_task_mime(plain) == parse_task_mime(task_mime_data("t2", source=""))
    assert parse_task_mime(other) is None
    assert parse_task_mime(None) is None


# ---------------------------------------------------------------- panel ----


def test_panel_counts_cards_and_filters_from_its_header(qtbot: Any) -> None:
    panel = _panel(qtbot)

    assert panel.title_label.text() == "À PLANIFIER · 5"
    assert panel.count() == 5
    with qtbot.waitSignal(panel.filters_changed):
        panel.search_edit.setText("bétrisey")
    assert _ids(panel.cards()) == ["portail"]
    assert panel.title_label.text() == "À PLANIFIER · 1 SUR 5"

    panel.search_edit.clear()
    with qtbot.waitSignal(panel.filters_changed):
        panel.urgent_chip.click()
    assert _ids(panel.cards()) == ["portail"]
    panel.urgent_chip.click()

    assert panel.mine_chip.isEnabled() is False
    panel.set_my_initials("sbe")
    panel.mine_chip.click()
    assert _ids(panel.cards()) == ["portail", "garde-corps"]
    panel.mine_chip.click()

    team_chip = panel._team_chips["atelier"]  # noqa: SLF001
    team_chip.click()
    assert _ids(panel.cards()) == ["escalier"]
    assert panel.filters().teams == frozenset({"atelier"})


def test_sort_and_group_combos(qtbot: Any) -> None:
    panel = _panel(qtbot)

    with qtbot.waitSignal(panel.filters_changed):
        panel.sort_combo.setCurrentIndex(panel.sort_combo.findData("deadline"))
        panel.sort_combo.activated.emit(panel.sort_combo.currentIndex())
    assert panel.sort() == "deadline"
    assert _ids(panel.cards())[0] == "portail"

    panel.set_group("dossier")
    assert panel.group_combo.currentData() == "dossier"
    assert panel.list_view.model().rowCount() == 10  # five headers, five cards


def test_plus_button_asks_for_the_quick_entry(qtbot: Any) -> None:
    panel = _panel(qtbot)

    with qtbot.waitSignal(panel.new_entry_requested):
        panel.new_button.click()


def _card_center(panel: BacklogPanel, task_id: str) -> QPoint:
    rect = panel.card_rect(task_id)
    assert not rect.isEmpty()
    return rect.center()


def test_double_click_and_enter_open_the_detail(qtbot: Any) -> None:
    panel = _panel(qtbot)
    viewport = panel.list_view.viewport()

    with qtbot.waitSignal(panel.card_activated) as blocker:
        qtbot.mouseDClick(viewport, Qt.MouseButton.LeftButton, pos=_card_center(panel, "barriere"))
    assert blocker.args == ["barriere"]

    assert panel.select_card("portail")
    panel.list_view.setFocus()
    with qtbot.waitSignal(panel.card_activated) as blocker:
        qtbot.keyClick(panel.list_view, Qt.Key.Key_Return)
    assert blocker.args == ["portail"]


def test_context_menu_actions(qtbot: Any) -> None:
    panel = _panel(qtbot)

    menu = panel.build_menu("barriere")
    actions = [action for action in menu.actions() if not action.isSeparator()]
    # The keys of the list follow a tab: the menu shows them right-aligned.
    assert [action.text() for action in actions] == [
        "Détail	Entrée",
        "Dupliquer",
        "Ouvrir le dossier",
        "Supprimer	Suppr",
    ]
    with qtbot.waitSignal(panel.card_action) as blocker:
        actions[1].trigger()
    assert blocker.args == ["barriere", "duplicate"]
    folder = next(a for a in panel.build_menu("rangement").actions() if a.data() == "folder")
    assert folder.isEnabled() is False

    assert panel.select_card("portail")
    panel.list_view.setFocus()
    with qtbot.waitSignal(panel.card_action) as blocker:
        qtbot.keyClick(panel.list_view, Qt.Key.Key_Delete)
    assert blocker.args == ["portail", "delete"]


def _drop(widget: QWidget, mime: QMimeData, pos: QPoint) -> QDropEvent:
    """Deliver a drop to ``widget`` (a list view takes viewport coordinates).

    The handler is called directly: Qt only routes a drop to the widget of the drag.
    """
    event = QDropEvent(
        QPointF(pos),
        Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.dropEvent(event)
    return event


def _drag_enter(widget: QWidget, mime: QMimeData, pos: QPoint) -> QDragEnterEvent:
    event = QDragEnterEvent(
        pos,
        Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.dragEnterEvent(event)
    return event


def test_dragging_a_card_inside_the_list_reorders_it(qtbot: Any) -> None:
    panel = _panel(qtbot)
    below_portail = _card_center(panel, "rangement") - QPoint(0, 30)

    with qtbot.waitSignal(panel.reorder_requested) as blocker:
        _drop(panel.list_view, task_mime_data("escalier"), below_portail)
    assert blocker.args == ["escalier", "portail", "rangement"]

    top = _card_center(panel, "escalier") - QPoint(0, 30)
    with qtbot.waitSignal(panel.reorder_requested) as blocker:
        _drop(panel.list_view, task_mime_data("garde-corps"), top)
    assert blocker.args == ["garde-corps", "", "escalier"]

    with qtbot.assertNotEmitted(panel.reorder_requested):
        _drop(panel.list_view, task_mime_data("escalier"), top)


def test_no_manual_reorder_outside_the_manual_sort(qtbot: Any) -> None:
    panel = _panel(qtbot)
    panel.set_sort("deadline")

    with qtbot.assertNotEmitted(panel.reorder_requested):
        _drop(panel.list_view, task_mime_data("escalier"), QPoint(20, 5))


def test_dropping_a_bar_of_the_grid_sends_it_back(qtbot: Any) -> None:
    panel = _panel(qtbot)

    enter = _drag_enter(panel.list_view, _bar_mime("planned"), QPoint(40, 40))
    assert enter.isAccepted()
    assert panel.overlay.isVisible()
    assert panel.overlay.geometry() == panel.rect()
    assert DROP_HINT == "Déposer ici pour renvoyer dans « À planifier »"

    with qtbot.waitSignal(panel.unschedule_requested) as blocker:
        _drop(panel.overlay, _bar_mime("planned"), QPoint(40, 40))
    assert blocker.args == ["planned"]
    assert not panel.overlay.isVisible()

    with qtbot.waitSignal(panel.unschedule_requested) as blocker:
        _drop(panel.list_view, _bar_mime("planned"), QPoint(40, 40))
    assert blocker.args == ["planned"]


def test_read_only_backlog_refuses_drops_and_edits(qtbot: Any) -> None:
    panel = _panel(qtbot)
    panel.set_read_only(True)

    enter = _drag_enter(panel.list_view, _bar_mime("planned"), QPoint(40, 40))
    assert not enter.isAccepted()
    with qtbot.assertNotEmitted(panel.unschedule_requested):
        _drop(panel.list_view, _bar_mime("planned"), QPoint(40, 40))
        panel.drop_task("planned")
    assert panel.new_button.isEnabled() is False
    assert all(
        not action.isEnabled()
        for action in panel.build_menu("barriere").actions()
        if action.data() in {"duplicate", "delete"}
    )


def test_empty_read_only_backlog_does_not_invite_to_type_a_card(qtbot: Any) -> None:
    planned = [task for task in _tasks() if task.scheduled]
    assert planned
    panel = _panel(qtbot, snapshot=_snapshot(planned))
    assert panel.list_view.empty_text == EMPTY_TEXT

    panel.set_read_only(True)

    assert panel.list_view.empty_text == READ_ONLY_EMPTY_TEXT
    assert "Ctrl+K" not in READ_ONLY_EMPTY_TEXT
    panel.set_read_only(False)
    assert panel.list_view.empty_text == EMPTY_TEXT
    panel.set_snapshot(_snapshot(), today=TODAY, now=NOW)
    panel.set_read_only(True)
    panel.set_filters(BacklogFilters(search="aucune carte ne contient ce texte"))
    assert panel.list_view.empty_text == FILTERED_EMPTY_TEXT


def test_new_cards_flash_and_keep_their_dot_until_hovered(qtbot: Any) -> None:
    panel = _panel(qtbot)

    panel.mark_new(["barriere", "unknown"])
    assert "barriere" in panel.new_ids()
    qtbot.waitUntil(lambda: not panel._flash_starts, timeout=2000)  # noqa: SLF001
    assert "barriere" in panel.new_ids()

    qtbot.mouseMove(panel.list_view.viewport(), _card_center(panel, "barriere"))
    qtbot.waitUntil(lambda: "barriere" not in panel.new_ids(), timeout=1000)
    panel.set_snapshot(_snapshot(), today=TODAY, now=NOW)
    assert "unknown" not in panel.new_ids()


def test_count_changed_follows_the_snapshot(qtbot: Any) -> None:
    panel = _panel(qtbot)
    tasks = [task for task in _tasks() if task.id != "portail"]

    with qtbot.waitSignal(panel.count_changed) as blocker:
        panel.set_snapshot(_snapshot(tasks), today=TODAY, now=NOW)
    assert blocker.args == [4]


def test_card_pixmap_is_the_card_width(qtbot: Any) -> None:
    card = build_cards(_snapshot(), today=TODAY, now=NOW)[1]

    pixmap = card_pixmap(card, 280)

    assert pixmap.deviceIndependentSize().width() == 280
    assert not pixmap.toImage().isNull()


def test_drop_highlight_goes_when_the_drag_leaves_before_reaching_it(qtbot: Any) -> None:
    panel = _panel(qtbot)

    # The drag leaves (or Escape cancels it) before one more move reaches the highlight.
    _drag_enter(panel.list_view, _bar_mime("planned"), QPoint(40, 40))
    assert panel.overlay.isVisible()
    panel.list_view.dragLeaveEvent(QDragLeaveEvent())
    qtbot.waitUntil(lambda: not panel.overlay.isVisible(), timeout=1000)
    card = panel.list_view.viewport().mapTo(panel, _card_center(panel, "barriere"))
    assert panel.childAt(card) is not panel.overlay

    _drag_enter(panel, _bar_mime("planned"), QPoint(40, 10))
    assert panel.overlay.isVisible()
    panel.dragLeaveEvent(QDragLeaveEvent())
    qtbot.waitUntil(lambda: not panel.overlay.isVisible(), timeout=1000)

    # The usual way: the next move goes to the highlight itself, which stays.
    _drag_enter(panel.list_view, _bar_mime("planned"), QPoint(40, 40))
    panel.list_view.dragLeaveEvent(QDragLeaveEvent())
    _drag_enter(panel.overlay, _bar_mime("planned"), QPoint(40, 40))
    qtbot.wait(30)
    assert panel.overlay.isVisible()
    assert panel.overlay.has_drag()


class _Host(QWidget):
    """The panel below a field, to reach the list with Tab like on the tab."""

    def __init__(self) -> None:
        super().__init__()
        self.before = QLineEdit()
        self.panel = BacklogPanel()
        layout = QVBoxLayout(self)
        layout.addWidget(self.before)
        layout.addWidget(self.panel)


def _hosted_panel(qtbot: Any) -> _Host:
    host = _Host()
    qtbot.addWidget(host)
    host.resize(320, 800)
    host.panel.set_snapshot(_snapshot(), today=TODAY, now=NOW)
    host.show()
    qtbot.waitExposed(host)
    host.activateWindow()
    # Focus given before the window is active would arrive as « window activated ».
    qtbot.waitUntil(lambda: QApplication.activeWindow() is host)
    return host


def _right_edge(panel: BacklogPanel, task_id: str) -> str:
    """Colour just inside the right border of a card, where the focus frame runs."""
    rect = panel.card_rect(task_id)
    image = panel.list_view.viewport().grab().toImage()
    ratio = image.devicePixelRatio()
    x, y = round((rect.right() - 1) * ratio), round(rect.center().y() * ratio)
    return QColor(image.pixelColor(x, y)).name()


def test_keyboard_focus_frames_the_card_enter_opens(qtbot: Any) -> None:
    host = _hosted_panel(qtbot)
    panel = host.panel
    view = panel.list_view
    accent = current_theme().color("accent").name()

    view.setFocus(Qt.FocusReason.TabFocusReason)
    qtbot.waitUntil(view.hasFocus)
    assert view.currentIndex().row() == 0
    assert _right_edge(panel, "escalier") == accent
    assert _right_edge(panel, "barriere") != accent
    with qtbot.waitSignal(panel.card_activated) as blocker:
        qtbot.keyClick(view, Qt.Key.Key_Return)
    assert blocker.args == ["escalier"]
    qtbot.keyClick(view, Qt.Key.Key_Down)
    assert _right_edge(panel, "barriere") == accent

    # A click selects without the keyboard frame.
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=_card_center(panel, "portail"))
    assert panel.current_card_id() == "portail"
    assert _right_edge(panel, "portail") != accent


def test_space_or_enter_on_a_group_header_folds_it(qtbot: Any) -> None:
    panel = _panel(qtbot)
    panel.set_group("dossier")
    view = panel.list_view
    model = view.model()
    rows = model.rowCount()

    view.setCurrentIndex(model.index(0, 0))  # « 2026-98 Müller SA (1) »
    qtbot.keyClick(view, Qt.Key.Key_Space)
    assert model.rowCount() == rows - 1
    assert view.currentIndex().row() == 0  # the header stays current
    qtbot.keyClick(view, Qt.Key.Key_Return)
    assert model.rowCount() == rows
    assert view.currentIndex().row() == 0
