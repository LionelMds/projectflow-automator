from __future__ import annotations

import gc
import os
import weakref
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QDate, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QVBoxLayout, QWidget

from projectflow.config import AppearanceConfig
from projectflow.core.planning.calendar import WorkCalendar
from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.conflicts import Finding
from projectflow.core.planning.models import (
    Absence,
    Author,
    PlanningMeta,
    PlanningTask,
    ProjectCache,
    Resource,
)
from projectflow.core.planning.quick_entry import ProjectInfo
from projectflow.core.planning.walltime import format_hhmm, wall_time
from projectflow.ui.planning.detail import (
    MULTI_SELECTION,
    DetailPanel,
    PersonChip,
    author_text,
    availability,
    parse_estimate,
    time_choices,
    unique_findings,
)
from projectflow.ui.planning.grid import task_mime_data
from projectflow.ui.planning.side_panel import RAIL_WIDTH, SidePanel
from projectflow.ui.theme import apply_theme

TODAY = date(2026, 10, 22)  # a Thursday
NOW = datetime(2026, 10, 22, 8, 15, tzinfo=UTC)
GE = WorkCalendar(cantons=("GE",))
SBE = Author(initials="SBE", name="Sophie Berset", poste="BUREAU-2")
JD = Author(initials="JD", name="Julien Dubois", poste="ATELIER-1")
SCREENSHOT_DIR = os.environ.get("PLANNING_UI_SCREENSHOT_DIR", "")

MONDAY = date(2026, 10, 19)
TUESDAY = date(2026, 10, 20)
WEDNESDAY = date(2026, 10, 21)
THURSDAY = date(2026, 10, 22)
FRIDAY = date(2026, 10, 23)
SATURDAY = date(2026, 10, 24)

RESOURCES = {
    resource.id: resource
    for resource in (
        Resource(id="nf", name="Nicolas Favre", initials="NF", team="montage", order=1),
        Resource(id="sg", name="Samuel Gay", initials="SG", team="montage", order=2),
        Resource(id="mr", name="Marc Rey", initials="MR", team="atelier", order=3),
        Resource(id="jd", name="Julien Dubois", initials="JD", team="atelier", order=4),
        Resource(id="lb", name="Laura Bron", initials="LB", team="bureau", order=5),
        Resource(id="old", name="Olivier Tissot", initials="OT", team="atelier", active=False),
    )
}
PROJECTS = (
    ProjectInfo("2026-5231", "Müller SA", "M. Müller", "Garde-corps escalier ext.", "LM"),
    ProjectInfo("2026-5244", "Rossier", "", "Escalier extérieur", "LM"),
    ProjectInfo("2026-5249", "Gillioz", "", "Barrière parking", "SBE"),
)
CACHE = ProjectCache(
    client="Müller SA",
    contact="M. Müller",
    designation="Garde-corps escalier ext.",
    managed_by="LM",
    atelier_release="2026-10-14",
    location="Sion",
)


def _task(task_id: str, **fields: Any) -> PlanningTask:
    data: dict[str, Any] = {"id": task_id, "created_at": "2026-10-15T07:00:00Z"} | fields
    return PlanningTask.model_validate(data)


FAB = _task(
    "fab",
    project_number="2026-5231",
    project_cache=CACHE,
    title="Fab. garde-corps",
    task_type="soudure",
    start=wall_time(MONDAY, 6, 30),
    end=wall_time(WEDNESDAY, 16, 30),
    assignments={"mr": {}, "jd": {}},
    required_people=3,
    status="provisional",
    deadline=date(2026, 11, 6),
    notes="Prendre le poste TIG mobile",
    created_by=SBE,
    updated_at="2026-10-22T08:12:00Z",
    updated_by=JD,
)
CARD = _task(
    "card",
    project_number="2026-5249",
    project_cache=ProjectCache(client="Gillioz", designation="Barrière parking"),
    title="Barrière parking souterrain",
    task_type="montage",
    estimate_days=2,
    required_people=2,
    assignments={"nf": {}},
    created_by=SBE,
)
OTHER = _task(
    "other",
    title="Pose marquise",
    task_type="montage",
    start=wall_time(MONDAY, 6, 30),
    end=wall_time(TUESDAY, 16, 30),
    assignments={"nf": {}},
)
LB_OFF = Absence(id="off", resource_id="lb", first_day=MONDAY, last_day=WEDNESDAY)
FINDINGS = [
    Finding("missing_people", "fab", "mr", "1 personne manquante sur 3 requises"),
    Finding("missing_people", "fab", "jd", "1 personne manquante sur 3 requises"),
    Finding("absence", "fab", "jd", "Absent mercredi 21.10"),
]


def _snapshot(*tasks: PlanningTask) -> PlanningSnapshot:
    chosen = tasks or (FAB, CARD, OTHER)
    return PlanningSnapshot(
        tasks={task.id: task for task in chosen},
        resources=RESOURCES,
        absences={LB_OFF.id: LB_OFF},
        meta=PlanningMeta(),
    )


@pytest.fixture(autouse=True)
def industry_theme(qapp: QApplication) -> Iterator[None]:
    sheet, palette, font = qapp.styleSheet(), qapp.palette(), qapp.font()
    apply_theme(qapp, AppearanceConfig(mode="light"))
    yield
    apply_theme(qapp, AppearanceConfig(mode="light"))
    qapp.setStyleSheet(sheet)
    qapp.setPalette(palette)
    qapp.setFont(font)


class _Host(QWidget):
    """The detail panel with a field to move the focus to."""

    def __init__(self) -> None:
        super().__init__()
        self.detail = DetailPanel()
        self.other = QLineEdit()
        layout = QVBoxLayout(self)
        layout.addWidget(self.detail, 1)
        layout.addWidget(self.other)


def _detail(
    qtbot: Any,
    task: PlanningTask = FAB,
    *,
    read_only: bool = False,
    focus_resource_id: str | None = None,
) -> _Host:
    host = _Host()
    qtbot.addWidget(host)
    host.resize(330, 1200)
    host.detail.set_project_choices(PROJECTS)
    host.detail.set_task(
        task,
        _snapshot(),
        calendar=GE,
        project=PROJECTS[0] if task.project_number == "2026-5231" else None,
        findings=FINDINGS if task.id == "fab" else [],
        read_only=read_only,
        focus_resource_id=focus_resource_id,
        now=NOW,
    )
    host.show()
    qtbot.waitExposed(host)
    host.activateWindow()
    return host


def _focus(qtbot: Any, widget: QWidget) -> None:
    widget.setFocus(Qt.FocusReason.OtherFocusReason)
    qtbot.waitUntil(widget.hasFocus)


def _texts(combo: QComboBox) -> list[str]:
    return [combo.itemText(index) for index in range(combo.count())]


def _choose(qtbot: Any, combo: QComboBox, text: str) -> None:
    """Pick ``text`` in the open list of ``combo``, as a click or Enter in the list does."""
    index = combo.findText(text)
    assert index >= 0, (text, _texts(combo))
    combo.showPopup()
    view = combo.view()
    qtbot.waitUntil(view.isVisible)
    view.setCurrentIndex(combo.model().index(index, 0))
    qtbot.keyClick(view, Qt.Key.Key_Return)
    assert combo.currentIndex() == index


def _wheel(widget: QWidget, delta: int = -120) -> QWheelEvent:
    center = QPointF(widget.rect().center())
    event = QWheelEvent(
        center,
        QPointF(widget.mapToGlobal(center.toPoint())),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,  # noqa: FBT003
    )
    QApplication.sendEvent(widget, event)
    return event


# ------------------------------------------------------------- helpers ----


def test_time_choices_offer_worked_half_hours_only() -> None:
    def hours(day: date, *, end: bool) -> list[str]:
        return [format_hhmm(minute) for minute in time_choices(GE, day, end=end)]

    monday_starts = hours(MONDAY, end=False)
    assert monday_starts[0] == "06:30"
    assert "11:30" in monday_starts
    assert "12:00" not in monday_starts
    assert "12:30" not in monday_starts
    assert monday_starts[-1] == "16:00"
    monday_ends = hours(MONDAY, end=True)
    assert (monday_ends[0], monday_ends[-1]) == ("07:00", "16:30")
    assert "12:00" in monday_ends
    assert "13:00" not in monday_ends
    assert len(monday_starts) == len(monday_ends) == 18  # 9 h of work in half hours
    # Friday afternoon is never offered.
    assert hours(FRIDAY, end=False) == [format_hhmm(m) for m in range(390, 691, 30)]
    assert hours(FRIDAY, end=True)[-1] == "12:00"
    assert hours(SATURDAY, end=False) == []


def test_parse_estimate_reads_days_and_hours() -> None:
    assert parse_estimate("2 j") == (2.0, 0)
    assert parse_estimate("1,5 j") == (1.5, 0)
    assert parse_estimate("1 j ½") == (1.5, 0)
    assert parse_estimate("½ j") == (0.5, 0)
    assert parse_estimate("3 jours") == (3.0, 0)
    assert parse_estimate("4 h") == (0.0, 240)
    assert parse_estimate("3h30") == (0.0, 210)
    assert parse_estimate("3 h 15") == (0.0, 210)
    assert parse_estimate("90 min") == (0.0, 90)
    assert parse_estimate("~2 j") == (2.0, 0)
    assert parse_estimate("") == (0.0, 0)
    assert parse_estimate("bientôt") is None
    assert parse_estimate("0 h") is None


def test_findings_said_once_and_author_line() -> None:
    kept = unique_findings(FINDINGS)

    assert [(f.kind, f.resource_id) for f in kept] == [("missing_people", None), ("absence", "jd")]
    assert author_text(FAB, NOW) == "Modifié par JD · ATELIER-1 · il y a 3 min"
    assert author_text(CARD, NOW) == "Créé par SBE · BUREAU-2 · le 15.10.2026"


# -------------------------------------------------------------- display ----


def test_shows_the_dossier_card_and_the_fields_of_a_scheduled_task(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail

    assert detail.mode() == "task"
    assert detail.task_id() == "fab"
    card = detail.dossier_card
    assert (card.number_label.text(), card.client_label.text()) == ("2026-5231", "Müller SA")
    assert card.designation_label.text() == "Garde-corps escalier ext."
    assert card.contact_label.text() == "Contact : M. Müller"
    assert card.facts_label.text() == "Sorti atelier 14.10.2026 · Géré par LM · Sion"
    assert list(card.buttons) == ["folder", "fiche", "repertoire", "creation", "sortie"]
    assert detail.title_edit.text() == "Fab. garde-corps"
    assert detail.dossier_edit.text() == "2026-5231"
    assert detail.type_combo.currentData() == "soudure"
    checked = detail.status_buttons.checkedButton()
    assert checked is not None
    assert checked.text() == "Provisoire"
    assert [chip.text() for chip in detail.chips()] == ["MR", "JD"]
    assert detail.people_caption.text() == "Affectés · 2 sur 3"
    assert detail.required_spin.value() == 3
    assert detail.start_date.date() == QDate(2026, 10, 19)
    assert detail.start_time.currentText() == "06:30"
    assert detail.end_date.date() == QDate(2026, 10, 21)
    assert detail.end_time.currentText() == "16:30"
    assert detail.duration_label.text() == "Durée : 3 j · 27 h"
    assert detail.deadline_edit.date() == QDate(2026, 11, 6)
    assert detail.notes_edit.toPlainText() == "Prendre le poste TIG mobile"
    assert detail.author_label.text() == "Modifié par JD · ATELIER-1 · il y a 3 min"
    assert detail.findings_block.isVisibleTo(detail)
    assert detail.findings_layout.count() == 2
    assert detail.detach_button.isVisibleTo(detail)
    assert detail.unschedule_button.isVisibleTo(detail)
    assert not detail.estimate_block.isVisibleTo(detail)


def test_a_card_shows_its_estimate_and_foreseen_people(qtbot: Any) -> None:
    host = _detail(qtbot, CARD)
    detail = host.detail

    assert detail.people_caption.text() == "Pressentis"
    assert [(chip.text(), chip.foreseen) for chip in detail.chips()] == [("NF", True)]
    assert detail.estimate_block.isVisibleTo(detail)
    assert detail.estimate_edit.text() == "2 j"
    assert not detail.start_block.isVisibleTo(detail)
    assert not detail.unschedule_button.isVisibleTo(detail)
    assert not detail.detach_button.isVisibleTo(detail)
    assert detail.dossier_card.number_label.text() == "2026-5249"


def test_clear_shows_the_hint(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail

    detail.clear()

    assert detail.mode() == "empty"
    assert detail.task_id() == ""
    assert detail.empty_label.isVisibleTo(detail)
    assert not detail.title_block.isVisibleTo(detail)


# -------------------------------------------------------------- editing ----


def test_title_saves_on_focus_out_and_on_enter_once(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail
    emitted: list[tuple[str, str, object]] = []
    detail.field_changed.connect(lambda *args: emitted.append(args))

    _focus(qtbot, detail.title_edit)
    detail.title_edit.selectAll()
    qtbot.keyClicks(detail.title_edit, "Fab. garde-corps inox")
    assert emitted == []
    _focus(qtbot, host.other)
    assert emitted == [("fab", "title", "Fab. garde-corps inox")]

    _focus(qtbot, detail.title_edit)
    qtbot.keyClicks(detail.title_edit, " 2")
    qtbot.keyClick(detail.title_edit, Qt.Key.Key_Return)
    _focus(qtbot, host.other)
    assert emitted[1:] == [("fab", "title", "Fab. garde-corps inox 2")]


def test_a_refresh_keeps_the_text_being_typed(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail

    _focus(qtbot, detail.title_edit)
    qtbot.keyClicks(detail.title_edit, "!")
    detail.set_task(FAB.model_copy(update={"notes": "Autre"}), _snapshot(), calendar=GE, now=NOW)

    assert detail.title_edit.text() == "Fab. garde-corps!"
    assert detail.notes_edit.toPlainText() == "Autre"


def test_dossier_completes_a_short_number_on_enter(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail

    _focus(qtbot, detail.dossier_edit)
    detail.dossier_edit.selectAll()
    qtbot.keyClicks(detail.dossier_edit, "5244")
    detail._dossier_completer.popup().hide()  # noqa: SLF001
    with qtbot.waitSignal(detail.field_changed) as blocker:
        qtbot.keyClick(detail.dossier_edit, Qt.Key.Key_Return)
    assert blocker.args == ["fab", "project_number", "2026-5244"]

    with qtbot.waitSignal(detail.field_changed) as blocker:
        detail._dossier_completer.activated.emit("2026-5249 · Gillioz · Barrière parking")  # noqa: SLF001
    assert blocker.args == ["fab", "project_number", "2026-5249"]


def test_notes_save_800_ms_after_the_last_keystroke(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail
    emitted: list[tuple[str, str, object]] = []
    detail.field_changed.connect(lambda *args: emitted.append(args))

    _focus(qtbot, detail.notes_edit)
    detail.notes_edit.moveCursor(detail.notes_edit.textCursor().MoveOperation.End)
    qtbot.keyClicks(detail.notes_edit, " et la grue")
    qtbot.wait(400)
    assert emitted == []
    qtbot.keyClicks(detail.notes_edit, "!")
    qtbot.wait(500)
    assert emitted == []
    qtbot.waitUntil(lambda: bool(emitted), timeout=1500)
    assert emitted == [("fab", "notes", "Prendre le poste TIG mobile et la grue!")]


def test_notes_save_at_once_when_the_field_is_left(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail

    _focus(qtbot, detail.notes_edit)
    qtbot.keyClicks(detail.notes_edit, "x")
    with qtbot.waitSignal(detail.field_changed, timeout=300) as blocker:
        _focus(qtbot, host.other)
    assert blocker.args[1] == "notes"


def test_type_status_urgent_required_and_deadline(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail
    emitted: list[tuple[object, ...]] = []
    detail.field_changed.connect(lambda *args: emitted.append(args))
    detail.status_changed.connect(lambda *args: emitted.append(("status", *args)))

    assert detail.type_combo.itemText(detail.type_combo.currentIndex()) == "Soudure / assemblage"
    assert not detail.type_combo.itemIcon(0).isNull()
    _choose(qtbot, detail.type_combo, "Montage chantier")
    confirmed = detail.status_buttons.button(1)
    assert confirmed is not None
    confirmed.click()
    detail.urgent_check.click()
    detail.required_spin.setValue(2)
    detail.required_spin.editingFinished.emit()
    detail.deadline_edit.setDate(QDate(2026, 11, 13))
    detail.deadline_edit.editingFinished.emit()
    detail.clear_deadline_button.click()

    assert emitted == [
        ("fab", "task_type", "montage"),
        ("status", "fab", "confirmed"),
        ("fab", "urgent", True),
        ("fab", "required_people", 2),
        ("fab", "deadline", date(2026, 11, 13)),
        ("fab", "deadline", None),
    ]


def test_start_and_end_offer_only_worked_times_of_their_day(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail

    assert "12:00" not in _texts(detail.start_time)
    assert "16:30" not in _texts(detail.start_time)
    assert _texts(detail.end_time)[-1] == "16:30"
    detail.start_date.setDate(QDate(2026, 10, 23))  # a Friday
    assert _texts(detail.start_time)[-1] == "11:30"
    assert all(text < "12:00" for text in _texts(detail.start_time))
    assert detail.start_time.currentText() == "06:30"
    detail.end_date.setDate(QDate(2026, 10, 23))
    assert _texts(detail.end_time)[-1] == "12:00"
    assert detail.end_time.currentText() == "12:00"  # 16:30 does not exist on Friday


def test_moving_the_start_keeps_the_worked_time(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail

    detail.start_date.setDate(QDate(2026, 10, 20))
    with qtbot.waitSignal(detail.dates_changed) as blocker:
        detail.start_date.editingFinished.emit()
    # 27 h from Tuesday 06:30 end on Thursday at 16:30.
    assert blocker.args == ["fab", wall_time(TUESDAY, 6, 30), wall_time(THURSDAY, 16, 30)]

    with qtbot.waitSignal(detail.dates_changed) as blocker:
        _choose(qtbot, detail.start_time, "13:00")
    assert blocker.args == ["fab", wall_time(TUESDAY, 13, 0), wall_time(FRIDAY, 12, 0)]


def test_changing_the_end_and_a_weekend_end(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail

    detail.end_date.setDate(QDate(2026, 10, 22))
    with qtbot.waitSignal(detail.dates_changed) as blocker:
        _choose(qtbot, detail.end_time, "12:00")
    assert blocker.args == ["fab", wall_time(MONDAY, 6, 30), wall_time(THURSDAY, 12, 0)]

    detail.end_date.setDate(QDate(2026, 10, 24))  # a Saturday: no time offered
    assert detail.end_time.count() == 0
    with qtbot.waitSignal(detail.dates_changed) as blocker:
        detail.end_date.editingFinished.emit()
    assert blocker.args == ["fab", wall_time(MONDAY, 6, 30), wall_time(FRIDAY, 12, 0)]


def test_people_chips_remove_and_add_with_availability(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail

    chip = detail.chips()[0]
    assert isinstance(chip, PersonChip)
    with qtbot.waitSignal(detail.people_changed) as blocker:
        qtbot.mouseClick(chip, Qt.MouseButton.LeftButton, pos=chip.close_rect().center())
    assert blocker.args == ["fab", [], ["mr"]]

    assert detail.person_choices() == [
        "NF  Nicolas Favre · Montage · occupé à 67 %",
        "SG  Samuel Gay · Montage · libre",
        "LB  Laura Bron · Bureau technique · absent",
    ]
    detail.add_person_button.click()
    assert detail.person_edit.isVisible()
    popup = detail._person_completer.popup()  # noqa: SLF001
    assert popup.isVisible()  # every choice is offered at once
    popup.hide()
    qtbot.keyClicks(detail.person_edit, "sg")
    popup.hide()
    with qtbot.waitSignal(detail.people_changed) as blocker:
        qtbot.keyClick(detail.person_edit, Qt.Key.Key_Return)
    assert blocker.args == ["fab", ["sg"], []]
    assert not detail.person_edit.isVisible()

    with qtbot.waitSignal(detail.people_changed) as blocker:
        detail._person_completer.activated.emit(detail.person_choices()[2])  # noqa: SLF001
    assert blocker.args == ["fab", ["lb"], []]


def test_unscheduled_task_edits_its_estimate(qtbot: Any) -> None:
    host = _detail(qtbot, CARD)
    detail = host.detail
    emitted: list[tuple[str, str, object]] = []
    detail.field_changed.connect(lambda *args: emitted.append(args))

    _focus(qtbot, detail.estimate_edit)
    detail.estimate_edit.selectAll()
    qtbot.keyClicks(detail.estimate_edit, "3h30")
    qtbot.keyClick(detail.estimate_edit, Qt.Key.Key_Return)
    assert emitted == [("card", "estimate_days", 0.0), ("card", "estimate_minutes", 210)]
    assert detail.estimate_edit.text() == "3 h 30"

    detail.estimate_edit.selectAll()
    qtbot.keyClicks(detail.estimate_edit, "n'importe")
    qtbot.keyClick(detail.estimate_edit, Qt.Key.Key_Return)
    assert len(emitted) == 2
    assert detail.estimate_label.text().startswith("Durée non comprise")

    detail.estimate_edit.selectAll()
    qtbot.keyClicks(detail.estimate_edit, "1 j ½")
    _focus(qtbot, host.other)
    assert emitted[2:] == [("card", "estimate_days", 1.5)]


def test_actions_and_dossier_buttons(qtbot: Any) -> None:
    host = _detail(qtbot, focus_resource_id="jd")
    detail = host.detail
    emitted: list[tuple[str, str]] = []
    detail.action_requested.connect(lambda *args: emitted.append(args))

    for key in ("folder", "fiche", "repertoire", "creation", "sortie"):
        detail.dossier_card.buttons[key].click()
    assert detail.detach_button.text() == "Détacher JD"
    detail.detach_button.click()
    detail.duplicate_button.click()
    detail.unschedule_button.click()
    detail.delete_button.click()

    assert [action for _task_id, action in emitted] == [
        "folder",
        "fiche",
        "repertoire",
        "creation",
        "sortie",
        "detach",
        "duplicate",
        "unschedule",
        "delete",
    ]
    assert {task_id for task_id, _action in emitted} == {"fab"}
    assert detail.focus_resource_id() == "jd"


def test_detach_without_a_bar_offers_each_person(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail

    assert detail.detach_button.text() == "Détacher…"
    menu = detail.detach_menu()
    assert [action.text() for action in menu.actions()] == [
        "Détacher Marc Rey",
        "Détacher Julien Dubois",
    ]
    with qtbot.waitSignal(detail.action_requested) as blocker:
        menu.actions()[1].trigger()
    assert blocker.args == ["fab", "detach"]
    assert detail.focus_resource_id() == "jd"


def test_read_only_disables_editing(qtbot: Any) -> None:
    host = _detail(qtbot, read_only=True)
    detail = host.detail
    emitted: list[object] = []
    for signal in (
        detail.field_changed,
        detail.people_changed,
        detail.dates_changed,
        detail.status_changed,
    ):
        signal.connect(lambda *args: emitted.append(args))
    actions: list[tuple[str, str]] = []
    detail.action_requested.connect(lambda *args: actions.append(args))

    assert detail.read_only_label.isVisibleTo(detail)
    assert detail.title_edit.isReadOnly()
    assert detail.notes_edit.isReadOnly()
    assert detail.start_date.isReadOnly()
    assert not detail.type_combo.isEnabled()
    assert not detail.start_time.isEnabled()
    assert not detail.delete_button.isEnabled()
    assert not detail.add_person_button.isVisibleTo(detail)
    assert all(not chip.removable for chip in detail.chips())
    assert all(not button.isEnabled() for button in detail.status_buttons.buttons())

    _focus(qtbot, detail.title_edit)
    qtbot.keyClicks(detail.title_edit, "x")
    _focus(qtbot, host.other)
    detail.urgent_check.click()
    detail.deadline_edit.editingFinished.emit()
    qtbot.wait(900)
    assert emitted == []
    # Opening the dossier stays possible.
    detail.dossier_card.buttons["folder"].click()
    assert actions == [("fab", "folder")]


def test_multi_selection_edits_every_task(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail
    emitted: list[tuple[object, ...]] = []
    detail.field_changed.connect(lambda *args: emitted.append(args))
    detail.people_changed.connect(lambda *args: emitted.append(args))

    detail.set_multi(3, {"task_type": "montage", "resource_ids": ["nf"]}, snapshot=_snapshot())

    assert detail.mode() == "multi"
    assert detail.task_id() == ""
    assert detail.multi_label.text() == "3 tâches sélectionnées"
    assert detail.type_combo.currentData() == "montage"
    assert detail.status_buttons.checkedButton() is None
    assert detail.urgent_check.checkState() == Qt.CheckState.PartiallyChecked
    assert [chip.text() for chip in detail.chips()] == ["NF"]
    assert not detail.title_block.isVisibleTo(detail)
    _choose(qtbot, detail.type_combo, "Livraison")
    detail.urgent_check.click()
    detail.chips()[0].remove_requested.emit("nf")

    assert emitted == [
        (MULTI_SELECTION, "task_type", "livraison"),
        (MULTI_SELECTION, "urgent", True),
        (MULTI_SELECTION, [], ["nf"]),
    ]


def test_escape_goes_back_to_the_backlog(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail

    _focus(qtbot, detail.title_edit)
    with qtbot.waitSignal(detail.back_requested):
        qtbot.keyClick(detail.title_edit, Qt.Key.Key_Escape)


def test_the_wheel_over_a_field_without_focus_scrolls_instead_of_editing(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail
    emitted: list[tuple[object, ...]] = []
    detail.field_changed.connect(lambda *args: emitted.append(args))
    detail.dates_changed.connect(lambda *args: emitted.append(args))

    def values() -> tuple[object, ...]:
        return (
            detail.type_combo.currentIndex(),
            detail.start_time.currentIndex(),
            detail.end_time.currentIndex(),
            detail.required_spin.value(),
            detail.start_date.date(),
            detail.deadline_edit.date(),
        )

    before = values()
    for widget, delta in (
        (detail.type_combo, -120),
        (detail.start_time, -120),
        (detail.end_time, 120),
        (detail.required_spin, -120),
        (detail.start_date, 120),
        (detail.deadline_edit, 120),
    ):
        assert widget.focusPolicy() == Qt.FocusPolicy.StrongFocus
        event = _wheel(widget, delta)
        assert not event.isAccepted()  # left to the scroll area
    _focus(qtbot, host.other)

    assert emitted == []
    assert values() == before


def test_arrow_keys_step_a_list_and_save_once_when_it_is_left(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail
    dates: list[tuple[object, ...]] = []
    fields: list[tuple[object, ...]] = []
    detail.dates_changed.connect(lambda *args: dates.append(args))
    detail.field_changed.connect(lambda *args: fields.append(args))

    _focus(qtbot, detail.start_time)
    for _ in range(4):
        qtbot.keyClick(detail.start_time, Qt.Key.Key_Down)
    assert detail.start_time.currentText() == "08:30"
    assert dates == []
    _focus(qtbot, host.other)
    # Two hours later, the 27 worked hours end on Thursday at 08:30.
    assert dates == [("fab", wall_time(MONDAY, 8, 30), wall_time(THURSDAY, 8, 30))]

    _focus(qtbot, detail.type_combo)
    qtbot.keyClick(detail.type_combo, Qt.Key.Key_Down)
    assert fields == []
    qtbot.keyClick(detail.type_combo, Qt.Key.Key_Return)
    assert len(fields) == 1
    assert fields[0][:2] == ("fab", "task_type")
    _focus(qtbot, host.other)
    assert len(fields) == 1


def test_a_refresh_keeps_a_date_a_stepped_list_and_the_person_field(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail

    _focus(qtbot, detail.deadline_edit)
    detail.deadline_edit.setDate(QDate(2027, 11, 6))  # typed, not saved yet
    detail.set_task(FAB, _snapshot(), calendar=GE, now=NOW)
    assert detail.deadline_edit.date() == QDate(2027, 11, 6)

    detail.add_person_button.click()
    detail._person_completer.popup().hide()  # noqa: SLF001
    qtbot.keyClicks(detail.person_edit, "s")
    detail.set_task(FAB, _snapshot(), calendar=GE, now=NOW)
    assert detail.person_edit.isVisible()
    assert detail.person_edit.text() == "s"
    assert not detail.add_person_button.isVisible()
    detail.set_task(OTHER, _snapshot(), calendar=GE, now=NOW)  # another task closes it
    assert not detail.person_edit.isVisible()
    assert detail.add_person_button.isVisible()

    detail.set_task(FAB, _snapshot(), calendar=GE, now=NOW)
    _focus(qtbot, detail.end_time)
    qtbot.keyClick(detail.end_time, Qt.Key.Key_Up)
    stepped = detail.end_time.currentText()
    detail.set_task(FAB, _snapshot(), calendar=GE, now=NOW)
    assert detail.end_time.currentText() == stepped
    assert detail.end_time.pending()
    # Showing another task saves the end stepped to on the first one.
    with qtbot.waitSignal(detail.dates_changed) as blocker:
        detail.set_task(OTHER, _snapshot(), calendar=GE, now=NOW)
    assert blocker.args == ["fab", wall_time(MONDAY, 6, 30), wall_time(WEDNESDAY, 16, 0)]


def test_without_deadline_the_calendar_opens_on_the_task(qtbot: Any) -> None:
    host = _detail(qtbot, FAB.model_copy(update={"deadline": None}))
    edit = host.detail.deadline_edit
    assert edit.text() == "Aucune"

    qtbot.mouseClick(
        edit, Qt.MouseButton.LeftButton, pos=QPoint(edit.width() - 10, edit.height() // 2)
    )
    calendar = edit.calendarWidget()
    qtbot.waitUntil(calendar.isVisible)
    assert (calendar.yearShown(), calendar.monthShown()) == (2026, 10)  # the task ends 21.10
    calendar.window().hide()
    host.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is host)

    _focus(qtbot, edit)
    qtbot.keyClick(edit, Qt.Key.Key_Up)
    assert edit.date() == QDate(2026, 10, 21)
    with qtbot.waitSignal(host.detail.field_changed) as blocker:
        _focus(qtbot, host.other)
    assert blocker.args == ["fab", "deadline", date(2026, 10, 21)]

    card = _detail(qtbot, CARD).detail.deadline_edit
    assert card.blank_date == QDate(2026, 10, 22)  # no dates: today


def test_someone_away_part_of_the_task_is_not_free() -> None:
    wednesday = Absence(id="wed", resource_id="lb", first_day=WEDNESDAY, last_day=WEDNESDAY)
    one_day = PlanningSnapshot(
        tasks={FAB.id: FAB},
        resources=RESOURCES,
        absences={wednesday.id: wednesday},
        meta=PlanningMeta(),
    )
    two_days = Absence(id="two", resource_id="lb", first_day=MONDAY, last_day=TUESDAY)
    away = PlanningSnapshot(
        tasks={FAB.id: FAB},
        resources=RESOURCES,
        absences={two_days.id: two_days},
        meta=PlanningMeta(),
    )

    assert availability(one_day, RESOURCES["lb"], FAB, GE) == "absent mercredi 21.10"
    assert availability(away, RESOURCES["lb"], FAB, GE) == "absent en partie"
    assert availability(one_day, RESOURCES["sg"], FAB, GE) == "libre"
    assert availability(_snapshot(), RESOURCES["lb"], FAB, GE) == "absent"


def test_people_away_part_of_the_time_come_after_the_free_ones(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail
    wednesday = Absence(id="wed", resource_id="nf", first_day=WEDNESDAY, last_day=WEDNESDAY)
    snapshot = PlanningSnapshot(
        tasks={task.id: task for task in (FAB, CARD, OTHER)},
        resources=RESOURCES,
        absences={LB_OFF.id: LB_OFF, wednesday.id: wednesday},
        meta=PlanningMeta(),
    )

    detail.set_task(FAB, snapshot, calendar=GE, now=NOW)

    assert detail.person_choices() == [
        "SG  Samuel Gay · Montage · libre",
        "NF  Nicolas Favre · Montage · absent mercredi 21.10",
        "LB  Laura Bron · Bureau technique · absent",
    ]


def test_a_selection_shifts_by_working_days(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail
    emitted: list[tuple[str, int]] = []
    detail.shift_requested.connect(lambda *args: emitted.append(args))

    assert not detail.shift_block.isVisibleTo(detail)
    detail.set_multi(3, {"task_type": "montage"}, snapshot=_snapshot())
    assert detail.shift_block.isVisibleTo(detail)
    detail.later_button.click()
    detail.shift_spin.setValue(3)
    detail.earlier_button.click()
    assert emitted == [(MULTI_SELECTION, 1), (MULTI_SELECTION, -3)]

    detail.set_multi(3, {}, snapshot=_snapshot(), read_only=True)
    assert not detail.later_button.isEnabled()
    assert not detail.shift_spin.isEnabled()


# ----------------------------------------------------------- side panel ----


def _side(qtbot: Any, *, height: int = 820) -> SidePanel:
    side = SidePanel(width=312)
    qtbot.addWidget(side)
    side.resize(312, height)
    side.backlog.set_snapshot(_snapshot(), today=TODAY, now=NOW)
    side.detail.set_project_choices(PROJECTS)
    side.show()
    qtbot.waitExposed(side)
    return side


def test_side_panel_tabs_show_the_count_and_switch_faces(qtbot: Any) -> None:
    side = _side(qtbot)

    assert side.face() == "backlog"
    assert side.backlog_tab_text() == "À planifier (1)"
    assert side.rail_text() == "À PLANIFIER · 1"
    assert side.backlog_tab.isChecked()
    with qtbot.waitSignal(side.face_changed) as blocker:
        qtbot.mouseClick(side.detail_tab, Qt.MouseButton.LeftButton)
    assert blocker.args == ["detail"]
    assert side.stack.currentWidget() is side.detail
    assert side.detail_tab.isChecked()
    with qtbot.waitSignal(side.face_changed) as blocker:
        side.detail.back_requested.emit()
    assert blocker.args == ["backlog"]

    side.backlog.set_snapshot(_snapshot(FAB, OTHER), today=TODAY, now=NOW)
    assert side.backlog_tab_text() == "À planifier (0)"


def test_side_panel_folds_into_a_rail_and_unfolds(qtbot: Any) -> None:
    side = _side(qtbot)

    with qtbot.waitSignal(side.collapsed_changed) as blocker:
        side.toggle()
    assert blocker.args == [True]
    assert side.is_collapsed()
    assert side.width() == RAIL_WIDTH
    assert side.rail.isVisible()
    assert not side.open_box.isVisible()

    with qtbot.waitSignal(side.collapsed_changed) as blocker:
        qtbot.mouseClick(side.rail, Qt.MouseButton.LeftButton)
    assert blocker.args == [False]
    assert side.width() == 312

    side.set_collapsed(True)
    side.show_detail()
    assert not side.is_collapsed()
    assert side.face() == "detail"
    side.set_collapsed(True)
    side.show_detail(expand=False)
    assert side.is_collapsed()

    side.fold_button.click()
    assert not side.is_collapsed()


def test_side_panel_grip_resizes_and_reports_the_width(qtbot: Any) -> None:
    side = _side(qtbot)
    grip = side.grip

    with qtbot.waitSignal(side.width_changed) as blocker:
        qtbot.mousePress(grip, Qt.MouseButton.LeftButton, pos=QPoint(2, 100))
        qtbot.mouseMove(grip, QPoint(-58, 100))
        qtbot.mouseRelease(grip, Qt.MouseButton.LeftButton, pos=QPoint(-58, 100))
    assert blocker.args == [372]
    assert side.panel_width() == 372
    assert side.width() == 372

    side.set_panel_width(5000)
    assert side.panel_width() == 520
    side.set_panel_width(10)
    assert side.panel_width() == 260


def test_bars_dropped_on_the_rail_or_the_detail_go_back(qtbot: Any) -> None:
    side = _side(qtbot)
    mime = task_mime_data("fab", source="grid", resource_id="mr")

    side.show_detail()
    enter = QDragEnterEvent(
        QPoint(100, 200),
        Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    side.dragEnterEvent(enter)
    assert enter.isAccepted()
    assert side.overlay.isVisible()
    drop = QDropEvent(
        QPointF(100, 200),
        Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    with qtbot.waitSignal(side.backlog.unschedule_requested) as blocker:
        side.overlay.dropEvent(drop)
    assert blocker.args == ["fab"]

    side.set_collapsed(True)
    with qtbot.waitSignal(side.backlog.unschedule_requested) as blocker:
        side.rail.dropEvent(drop)
    assert blocker.args == ["fab"]

    card = task_mime_data("card", source="grid")
    with qtbot.assertNotEmitted(side.backlog.unschedule_requested):
        side.backlog.drop_task("card")
    assert card.hasFormat("application/x-projectflow-task")


def test_side_panel_grip_starts_from_a_width_set_by_the_tab(qtbot: Any) -> None:
    side = _side(qtbot)
    side.set_panel_width(400)  # the width kept in the poste settings
    grip = side.grip
    widths: list[int] = []
    side.width_changed.connect(widths.append)

    qtbot.mousePress(grip, Qt.MouseButton.LeftButton, pos=QPoint(2, 100))
    qtbot.mouseMove(grip, QPoint(-8, 100))
    assert side.panel_width() == 410
    qtbot.mouseRelease(grip, Qt.MouseButton.LeftButton, pos=QPoint(-8, 100))
    assert widths == [410]

    side.set_panel_width(300)
    qtbot.mouseClick(grip, Qt.MouseButton.LeftButton, pos=QPoint(2, 100))
    assert side.panel_width() == 300
    assert widths == [410]


def test_side_panel_drop_highlight_goes_when_the_drag_leaves(qtbot: Any) -> None:
    side = _side(qtbot)
    mime = task_mime_data("fab", source="grid", resource_id="mr")
    side.show_detail()
    enter = QDragEnterEvent(
        QPoint(100, 200),
        Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    side.dragEnterEvent(enter)
    assert side.overlay.isVisible()
    side.dragLeaveEvent(QDragLeaveEvent())
    qtbot.waitUntil(lambda: not side.overlay.isVisible(), timeout=1000)


def test_a_side_panel_without_parent_is_freed() -> None:
    side = SidePanel()
    ref = weakref.ref(side)

    del side
    gc.collect()

    assert ref() is None


# ----------------------------------------------------------- screenshots ----


def _save(widget: QWidget, name: str) -> None:
    pixmap = widget.grab()
    assert not pixmap.isNull()
    if SCREENSHOT_DIR:
        pixmap.save(str(Path(SCREENSHOT_DIR) / f"planning_ui_panels_{name}.png"))


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_screenshots_of_both_faces(qtbot: Any, qapp: QApplication, mode: str) -> None:
    apply_theme(qapp, AppearanceConfig(mode=mode))  # type: ignore[arg-type]
    cards = [
        _task(
            "escalier",
            backlog_rank="a0",
            project_number="2026-5244",
            project_cache=ProjectCache(client="Rossier", atelier_release="2026-10-14"),
            title="Escalier ext. - fabrication",
            task_type="soudure",
            estimate_days=3,
            deadline=date(2026, 11, 6),
            origin="sortie_dossier",
        ),
        CARD.model_copy(
            update={
                "backlog_rank": "a1",
                "deadline": date(2026, 10, 28),
                "status": "provisional",
                "created_at": "2026-10-22T08:10:00Z",
                "assignments": {"nf": {}, "sg": {}},
            }
        ),
        _task(
            "portail",
            backlog_rank="a2",
            project_number="2026-5198-2",
            project_cache=ProjectCache(client="Bétrisey"),
            title="SAV portail - réglage",
            task_type="sav",
            estimate_minutes=240,
            urgent=True,
            deadline=date(2026, 10, 20),
            created_by=JD,
            created_at="2026-10-21T13:05:00Z",
        ),
        _task("rangement", backlog_rank="a3", title="Rangement stock tubes", task_type="interne"),
    ]
    side = _side(qtbot, height=880)
    side.backlog.set_snapshot(_snapshot(FAB, OTHER, *cards), today=TODAY, now=NOW)
    side.backlog.set_my_initials("SBE")
    side.backlog.mark_new(["card"])
    qtbot.wait(700)  # the flash is over, the « nouveau » mark stays
    _save(side, f"backlog_{mode}")

    side.detail.set_task(
        FAB,
        _snapshot(FAB, OTHER, *cards),
        calendar=GE,
        project=PROJECTS[0],
        findings=FINDINGS,
        focus_resource_id="jd",
        now=NOW,
    )
    side.show_detail()
    side.resize(312, 1460)
    side.detail.scroll_area.widget().adjustSize()
    qtbot.wait(50)
    _save(side, f"detail_{mode}")

    side.detail.set_multi(
        4,
        {"task_type": "montage", "status": "confirmed", "resource_ids": ["nf"]},
        snapshot=_snapshot(FAB, OTHER, *cards),
    )
    side.resize(312, 880)
    qtbot.wait(50)
    _save(side, f"multi_{mode}")

    side.set_collapsed(True)
    side.set_new_cards(has_new=True)
    qtbot.wait(20)
    _save(side, f"rail_{mode}")


NIGHT = _task(
    "night",
    title="Aéroport",
    task_type="montage",
    start=wall_time(MONDAY, 21, 0),
    end=wall_time(TUESDAY, 5, 0),
    assignments={"nf": {}},
    off_hours=True,
)


def test_night_work_offers_every_half_hour_and_its_rest(qtbot: Any) -> None:
    host = _detail(qtbot, NIGHT)
    detail = host.detail

    assert detail.night_check.text() == "Travail de nuit"
    assert detail.night_check.isChecked()
    assert _texts(detail.start_time)[0] == "00:00"
    assert _texts(detail.start_time)[-1] == "23:30"
    assert detail.start_time.currentText() == "21:00"
    assert detail.end_time.currentText() == "05:00"
    assert detail.duration_label.text() == "Durée : 8 h (nuit)"
    assert detail.rest_label.text().startswith("Repos : dès lun ")
    assert not detail.rest_label.isHidden()

    with qtbot.waitSignal(detail.dates_changed) as blocker:
        _choose(qtbot, detail.start_time, "22:00")
    # Night work keeps its exact times and its real length.
    assert blocker.args == ["night", wall_time(MONDAY, 22, 0), wall_time(TUESDAY, 6, 0)]


def test_the_night_checkbox_asks_for_night_work(qtbot: Any) -> None:
    host = _detail(qtbot, OTHER)
    detail = host.detail

    assert not detail.night_check.isChecked()
    assert detail.rest_label.isHidden()
    with qtbot.waitSignal(detail.field_changed) as blocker:
        detail.night_check.click()
    assert blocker.args == ["other", "off_hours", True]
