from __future__ import annotations

import gc
import os
import weakref
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

from projectflow.config import AppearanceConfig
from projectflow.core.planning.calendar import WorkCalendar
from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.models import (
    Absence,
    Author,
    PlanningMeta,
    PlanningTask,
    ProjectCache,
    Resource,
)
from projectflow.core.planning.quick_entry import ProjectInfo
from projectflow.core.planning.task_types import SIMPLE_TASK_TYPES
from projectflow.core.planning.walltime import wall_time
from projectflow.ui.planning.detail import (
    END_ERROR,
    MULTI_SELECTION,
    TITLE_PLACEHOLDER,
    DetailPanel,
    FitButton,
    author_text,
    availability,
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
LB_OFF = Absence(
    id="off", resource_id="lb", first_day=MONDAY, last_day=WEDNESDAY, reason="vacances"
)


def _snapshot(*tasks: PlanningTask, meta: PlanningMeta | None = None) -> PlanningSnapshot:
    chosen = tasks or (FAB, CARD, OTHER)
    return PlanningSnapshot(
        tasks={task.id: task for task in chosen},
        resources=RESOURCES,
        absences={LB_OFF.id: LB_OFF},
        meta=meta or PlanningMeta(),
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
    snapshot: PlanningSnapshot | None = None,
) -> _Host:
    host = _Host()
    qtbot.addWidget(host)
    host.resize(330, 1200)
    host.detail.set_project_choices(PROJECTS)
    host.detail.set_task(
        task,
        snapshot or _snapshot(),
        calendar=GE,
        project=PROJECTS[0] if task.project_number == "2026-5231" else None,
        read_only=read_only,
        now=NOW,
    )
    host.show()
    qtbot.waitExposed(host)
    host.activateWindow()
    return host


def _focus(qtbot: Any, widget: QWidget) -> None:
    widget.setFocus(Qt.FocusReason.OtherFocusReason)
    qtbot.waitUntil(widget.hasFocus)


def _chip(detail: DetailPanel, key: str) -> FitButton:
    return next(chip for chip in detail.type_chips() if chip.property("key") == key)


def _checked_type(detail: DetailPanel) -> str | None:
    checked = [chip.property("key") for chip in detail.type_chips() if chip.isChecked()]
    return checked[0] if checked else None


def _checked_people(detail: DetailPanel) -> list[str]:
    return [button.resource_id for button in detail.person_buttons() if button.isChecked()]


def _click(qtbot: Any, widget: QWidget) -> None:
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton)


def _top(detail: DetailPanel, widget: QWidget) -> int:
    body = detail.scroll_area.widget()
    return widget.mapTo(body, QPoint(0, 0)).y()


# ------------------------------------------------------------- helpers ----


def test_author_line() -> None:
    assert author_text(FAB, NOW) == "Modifié par JD · ATELIER-1 · il y a 3 min"
    assert author_text(CARD, NOW) == "Créé par SBE · BUREAU-2 · le 15.10.2026"


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


# -------------------------------------------------------------- display ----


def test_shows_the_blocks_of_a_scheduled_task_top_to_bottom(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail

    assert detail.mode() == "task"
    assert detail.task_id() == "fab"
    assert detail.number_edit.text() == "2026-5231"
    assert detail.number_edit.placeholderText() == "AAAA-XXXX"
    assert detail.client_label.text() == "Müller SA"
    assert detail.title_edit.text() == "Fab. garde-corps"
    assert detail.project_label.text() == "Projet : Garde-corps escalier ext."
    assert _checked_type(detail) == "soudure"
    assert detail.start_row.value() == "lun 19 oct · 06:30"
    assert detail.end_row.value() == "mer 21 oct · 16:30"
    assert detail.duration_label.text() == "Durée : 3 j · 27 h"
    assert detail.deadline_row.value() == "ven 6 nov"
    assert not detail.urgent_button.isChecked()
    assert not detail.night_button.isChecked()
    assert _checked_people(detail) == ["mr", "jd"]
    assert detail.people_count.text() == "2 sélectionnés"
    assert detail.notes_edit.toPlainText() == "Prendre le poste TIG mobile"
    checked = detail.status_buttons.checkedButton()
    assert checked is not None
    assert checked.text() == "Provisoire"
    assert detail.author_label.text() == "Modifié par JD · ATELIER-1 · il y a 3 min"
    assert detail.unschedule_button.isVisibleTo(detail)
    assert detail.open_picker() is None

    # The order of the mockup, the status at the bottom; the footer out of the scroll.
    blocks = [
        detail.number_block,
        detail.title_block,
        detail.type_block,
        detail.dates_block,
        detail.deadline_block,
        detail.night_block,
        detail.people_block,
        detail.notes_block,
        detail.status_block,
    ]
    tops = [_top(detail, block) for block in blocks]
    assert tops == sorted(tops)
    assert detail.footer.parent() is detail
    assert not detail.scroll_area.widget().isAncestorOf(detail.footer)
    # Gone from the panel: dossier buttons, « Détacher », findings, « Décaler »,
    # estimate, « Requis ».
    for name in (
        "dossier_card",
        "detach_button",
        "focus_resource_id",
        "findings_block",
        "shift_block",
        "estimate_edit",
        "required_spin",
        "type_combo",
    ):
        assert not hasattr(detail, name), name


def test_the_sub_designation_names_the_task_or_leaves_it_to_the_project(qtbot: Any) -> None:
    host = _detail(qtbot, FAB.model_copy(update={"title": ""}))
    detail = host.detail

    assert detail.title_edit.text() == ""
    assert detail.title_edit.placeholderText() == TITLE_PLACEHOLDER
    assert detail.project_label.text() == "Projet : Garde-corps escalier ext."
    assert detail.project_label.isVisibleTo(detail)

    detail.set_task(FAB, _snapshot(), calendar=GE, project=PROJECTS[0], now=NOW)
    assert detail.title_edit.text() == "Fab. garde-corps"
    assert detail.project_label.text() == "Projet : Garde-corps escalier ext."

    internal = _task("internal", title="Rangement stock", task_type="interne")
    detail.set_task(internal, _snapshot(), calendar=GE, now=NOW)
    assert detail.number_edit.text() == ""
    assert detail.client_label.text() == "Tâche interne"
    assert not detail.project_label.isVisibleTo(detail)


def test_a_card_has_no_dates_and_foreseen_people(qtbot: Any) -> None:
    host = _detail(qtbot, CARD)
    detail = host.detail

    assert not detail.dates_box.isVisibleTo(detail)
    assert detail.unscheduled_label.isVisibleTo(detail)
    assert detail.people_count.text() == "1 pressenti"
    assert _checked_people(detail) == ["nf"]
    assert not detail.unschedule_button.isVisibleTo(detail)
    assert detail.number_edit.text() == "2026-5249"
    assert detail.client_label.text() == "Gillioz"
    _click(qtbot, detail.start_row)
    assert detail.open_picker() is None


def test_clear_shows_the_hint(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail

    detail.clear()

    assert detail.mode() == "empty"
    assert detail.task_id() == ""
    assert detail.empty_label.isVisibleTo(detail)
    assert not detail.title_block.isVisibleTo(detail)
    assert not detail.footer.isVisibleTo(detail)


def test_three_types_share_one_row_and_a_longer_list_wraps(qtbot: Any) -> None:
    simple = PlanningMeta(task_types=SIMPLE_TASK_TYPES)
    montage = FAB.model_copy(update={"task_type": "montage"})
    host = _detail(qtbot, montage, snapshot=_snapshot(meta=simple))
    detail = host.detail

    chips = detail.type_chips()
    assert [chip.full_text() for chip in chips] == ["Atelier", "Montage", "Autre"]
    assert len({chip.y() for chip in chips}) == 1
    widths = [chip.width() for chip in chips]
    assert max(widths) - min(widths) <= 1  # one third each
    assert _checked_type(detail) == "montage"

    detail.set_task(FAB, _snapshot(), calendar=GE, now=NOW)  # the ten default types
    assert len(detail.type_chips()) == 10
    qtbot.waitUntil(lambda: _chip(detail, "soudure").isVisible())
    assert all(chip.text() == chip.full_text() for chip in detail.type_chips())
    assert _checked_type(detail) == "soudure"


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


def test_the_project_number_completes_and_checks_aaaa_xxxx(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail
    edit = detail.number_edit
    emitted: list[tuple[str, str, object]] = []
    detail.field_changed.connect(lambda *args: emitted.append(args))

    _focus(qtbot, edit)
    edit.selectAll()
    qtbot.keyClicks(edit, "5244")
    detail._number_completer.popup().hide()  # noqa: SLF001
    qtbot.keyClick(edit, Qt.Key.Key_Return)
    assert emitted == [("fab", "project_number", "2026-5244")]
    assert edit.text() == "2026-5244"

    # The list shows the client and the designation, and writes the number alone.
    completer = detail._number_completer  # noqa: SLF001
    completer.setCompletionPrefix("5249")
    assert completer.currentCompletion() == "2026-5249"
    shown = completer.completionModel().index(0, 0).data()
    assert shown == "2026-5249 · Gillioz · Barrière parking"
    completer.activated.emit("2026-5249")
    assert emitted[1:] == [("fab", "project_number", "2026-5249")]
    assert edit.text() == "2026-5249"

    # Letters are refused while typing; an unfinished number is shown wrong, not saved.
    edit.selectAll()
    qtbot.keyClicks(edit, "ab2026-52")
    assert edit.text() == "2026-52"
    detail._number_completer.popup().hide()  # noqa: SLF001
    qtbot.keyClick(edit, Qt.Key.Key_Return)
    assert len(emitted) == 2
    assert edit.property("tone") == "error"
    assert edit.toolTip().startswith("Numéro attendu : AAAA-XXXX")

    # Empty: an internal task.
    edit.clear()
    qtbot.keyClick(edit, Qt.Key.Key_Return)
    assert emitted[2:] == [("fab", "project_number", "")]
    assert edit.property("tone") in ("", None)


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


def test_type_status_urgent_and_night_save_at_once(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail
    emitted: list[tuple[object, ...]] = []
    detail.field_changed.connect(lambda *args: emitted.append(args))
    detail.status_changed.connect(lambda *args: emitted.append(("status", *args)))

    _click(qtbot, _chip(detail, "montage"))
    _click(qtbot, _chip(detail, "montage"))  # already the type: nothing more
    confirmed = detail.status_buttons.button(1)
    assert confirmed is not None
    confirmed.click()
    _click(qtbot, detail.urgent_button)
    _click(qtbot, detail.night_button)

    assert emitted == [
        ("fab", "task_type", "montage"),
        ("status", "fab", "confirmed"),
        ("fab", "urgent", True),
        ("fab", "off_hours", True),
    ]
    assert _checked_type(detail) == "montage"
    assert detail.urgent_button.property("tone") == "error"


# ----------------------------------------------------------------- rolls ----


def test_a_click_on_debut_unfolds_its_rolls_and_moves_the_task(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail
    dates: list[tuple[object, ...]] = []
    detail.dates_changed.connect(lambda *args: dates.append(args))

    _click(qtbot, detail.start_row)
    assert detail.open_picker() == "start"
    assert detail.start_picker.isVisible()
    assert detail.start_row.is_open()
    qtbot.waitUntil(detail.start_picker.hasFocus)
    assert detail.start_picker.visible_texts(0) == [
        "sam 17 oct",
        "dim 18 oct",
        "lun 19 oct",
        "mar 20 oct",
        "mer 21 oct",
    ]
    assert detail.start_picker.text(1) == "06"
    assert detail.start_picker.text(2) == "30"

    qtbot.keyClick(detail.start_picker, Qt.Key.Key_Down)  # one day later
    # Shown at once, saved when folded: 27 worked hours from Tuesday end on Thursday.
    assert detail.start_row.value() == "mar 20 oct · 06:30"
    assert detail.end_row.value() == "jeu 22 oct · 16:30"
    assert dates == []
    _click(qtbot, detail.start_row)
    assert detail.open_picker() is None
    assert not detail.start_picker.isVisible()
    assert dates == [("fab", wall_time(TUESDAY, 6, 30), wall_time(THURSDAY, 16, 30))]

    moved = FAB.model_copy(update={"start": dates[0][1], "end": dates[0][2]})
    detail.set_task(moved, _snapshot(), calendar=GE, now=NOW)
    _click(qtbot, detail.start_row)
    picker = detail.start_picker
    qtbot.keyClick(picker, Qt.Key.Key_Right)
    assert picker.current_column() == 1
    for _ in range(7):
        qtbot.keyClick(picker, Qt.Key.Key_Down)  # 06 -> 13
    qtbot.keyClick(picker, Qt.Key.Key_Right)
    qtbot.keyClick(picker, Qt.Key.Key_Up)  # 13:30 -> 13:00
    assert detail.start_row.value() == "mar 20 oct · 13:00"
    qtbot.keyClick(picker, Qt.Key.Key_Return)
    assert detail.open_picker() is None
    qtbot.waitUntil(detail.start_row.hasFocus)
    assert dates[1:] == [("fab", wall_time(TUESDAY, 13, 0), wall_time(FRIDAY, 12, 0))]


def test_one_row_open_at_a_time_and_a_click_elsewhere_folds(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail
    dates: list[tuple[object, ...]] = []
    detail.dates_changed.connect(lambda *args: dates.append(args))

    _click(qtbot, detail.start_row)
    _click(qtbot, detail.end_row)
    assert detail.open_picker() == "end"
    assert not detail.start_picker.isVisible()
    assert detail.end_picker.isVisible()
    assert not detail.start_row.is_open()
    assert detail.end_row.is_open()
    assert dates == []  # nothing rolled: nothing saved

    _click(qtbot, detail.deadline_row)
    assert detail.open_picker() == "deadline"
    assert detail.deadline_picker.isVisible()

    _click(qtbot, host.other)
    assert detail.open_picker() is None
    assert not detail.deadline_picker.isVisible()

    _click(qtbot, detail.end_row)
    qtbot.keyClick(detail.end_picker, Qt.Key.Key_Escape)
    assert detail.open_picker() is None


def test_an_end_before_the_start_turns_red_and_is_not_saved(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail
    dates: list[tuple[object, ...]] = []
    detail.dates_changed.connect(lambda *args: dates.append(args))

    _click(qtbot, detail.end_row)
    detail.end_picker.step(0, -3)  # Sunday 18th, before Monday's start
    assert detail.end_row.has_error()
    assert detail.end_row.value() == "dim 18 oct · 16:30"
    assert detail.duration_label.text() == END_ERROR
    assert detail.duration_label.property("tone") == "error"

    _click(qtbot, host.other)
    assert dates == []
    assert not detail.end_row.has_error()
    assert detail.end_row.value() == "mer 21 oct · 16:30"
    assert detail.duration_label.text() == "Durée : 3 j · 27 h"


def test_the_end_goes_back_to_the_last_worked_time(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail

    _click(qtbot, detail.end_row)
    detail.end_picker.step(0, 1)  # Thursday 16:30
    with qtbot.waitSignal(detail.dates_changed) as blocker:
        qtbot.keyClick(detail.end_picker, Qt.Key.Key_Return)
    assert blocker.args == ["fab", wall_time(MONDAY, 6, 30), wall_time(THURSDAY, 16, 30)]

    _click(qtbot, detail.end_row)
    detail.end_picker.step(0, 3)  # a Saturday: the week ends on Friday at noon
    assert detail.end_row.value() == "ven 23 oct · 12:00"
    with qtbot.waitSignal(detail.dates_changed) as blocker:
        _click(qtbot, detail.end_row)
    assert blocker.args == ["fab", wall_time(MONDAY, 6, 30), wall_time(FRIDAY, 12, 0)]


def test_the_deadline_rolls_a_day_and_the_cross_clears_it(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail
    emitted: list[tuple[str, str, object]] = []
    detail.field_changed.connect(lambda *args: emitted.append(args))

    _click(qtbot, detail.deadline_row)
    assert detail.deadline_picker.day() == date(2026, 11, 6)
    for _ in range(7):
        qtbot.keyClick(detail.deadline_picker, Qt.Key.Key_Down)
    assert detail.deadline_row.value() == "ven 13 nov"
    qtbot.keyClick(detail.deadline_picker, Qt.Key.Key_Return)
    assert emitted == [("fab", "deadline", date(2026, 11, 13))]

    assert detail.clear_deadline_button.isVisible()
    assert detail.clear_deadline_button.parent() is detail.deadline_row
    _click(qtbot, detail.clear_deadline_button)
    assert emitted[1:] == [("fab", "deadline", None)]
    assert detail.deadline_row.value() == "Aucune"
    assert not detail.clear_deadline_button.isVisible()
    assert detail.open_picker() is None


def test_without_deadline_the_rolls_start_on_the_task(qtbot: Any) -> None:
    host = _detail(qtbot, FAB.model_copy(update={"deadline": None}))
    detail = host.detail
    emitted: list[tuple[str, str, object]] = []
    detail.field_changed.connect(lambda *args: emitted.append(args))
    assert detail.deadline_row.value() == "Aucune"
    assert not detail.clear_deadline_button.isVisible()

    _click(qtbot, detail.deadline_row)
    assert detail.deadline_picker.day() == date(2026, 10, 21)  # the task ends 21.10
    assert detail.deadline_row.value() == "mer 21 oct"
    qtbot.keyClick(detail.deadline_picker, Qt.Key.Key_Escape)  # left untouched: none
    assert emitted == []
    assert detail.deadline_row.value() == "Aucune"

    _click(qtbot, detail.deadline_row)
    qtbot.keyClick(detail.deadline_picker, Qt.Key.Key_Return)
    assert emitted == [("fab", "deadline", date(2026, 10, 21))]

    card = _detail(qtbot, CARD)
    _click(qtbot, card.detail.deadline_row)
    assert card.detail.deadline_picker.day() == TODAY  # no dates: today


def test_a_refresh_keeps_the_rolls_and_another_task_saves_them(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail

    _click(qtbot, detail.end_row)
    detail.end_picker.step(1, -1)  # Wednesday 15:30
    detail.set_task(FAB.model_copy(update={"notes": "x"}), _snapshot(), calendar=GE, now=NOW)
    assert detail.open_picker() == "end"
    assert detail.end_row.value() == "mer 21 oct · 15:30"

    with qtbot.waitSignal(detail.dates_changed) as blocker:
        detail.set_task(OTHER, _snapshot(), calendar=GE, now=NOW)
    assert blocker.args == ["fab", wall_time(MONDAY, 6, 30), wall_time(WEDNESDAY, 15, 30)]
    assert detail.open_picker() is None
    assert detail.end_row.value() == "mar 20 oct · 16:30"


def test_escape_folds_the_rolls_before_going_back(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail
    backs: list[bool] = []
    detail.back_requested.connect(lambda: backs.append(True))

    _click(qtbot, detail.start_row)
    qtbot.keyClick(detail.start_picker, Qt.Key.Key_Escape)
    assert detail.open_picker() is None
    assert backs == []

    _focus(qtbot, detail.title_edit)
    qtbot.keyClick(detail.title_edit, Qt.Key.Key_Escape)
    assert backs == [True]


# ---------------------------------------------------------------- people ----


def test_everybody_by_team_absent_people_hatched(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail

    assert detail.team_titles() == ["Bureau technique", "Atelier", "Montage"]
    assert [button.full_text() for button in detail.person_buttons()] == [
        "LB",
        "MR",
        "JD",
        "NF",
        "SG",
    ]
    assert all(button.height() >= 44 for button in detail.person_buttons())
    laura = detail.person_button("lb")
    assert laura is not None
    assert laura.absent
    assert laura.isEnabled()  # away, still possible to pick
    assert laura.toolTip() == "Laura Bron\nAbsent · Vacances"
    nicolas = detail.person_button("nf")
    assert nicolas is not None
    assert not nicolas.absent
    assert nicolas.toolTip() == "Nicolas Favre\nOccupé à 67 %"
    sam = detail.person_button("sg")
    assert sam is not None
    assert sam.toolTip() == "Samuel Gay\nLibre"


def test_a_click_on_initials_adds_or_removes_the_person(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail
    emitted: list[tuple[object, ...]] = []
    detail.people_changed.connect(lambda *args: emitted.append(args))

    sam, marc, laura = (detail.person_button(rid) for rid in ("sg", "mr", "lb"))
    assert sam is not None
    assert marc is not None
    assert laura is not None
    _click(qtbot, sam)
    _click(qtbot, marc)
    _click(qtbot, laura)

    assert emitted == [("fab", ["sg"], []), ("fab", [], ["mr"]), ("fab", ["lb"], [])]


def test_someone_no_longer_on_the_planning_can_still_be_removed(qtbot: Any) -> None:
    host = _detail(qtbot, FAB.model_copy(update={"assignments": {"mr": {}, "old": {}}}))
    detail = host.detail

    assert detail.team_titles()[-1] == "Autres"
    olivier = detail.person_button("old")
    assert olivier is not None
    assert olivier.isChecked()
    with qtbot.waitSignal(detail.people_changed) as blocker:
        _click(qtbot, olivier)
    assert blocker.args == ["fab", [], ["old"]]


# --------------------------------------------------------------- actions ----


def test_footer_actions(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail
    emitted: list[tuple[str, str]] = []
    detail.action_requested.connect(lambda *args: emitted.append(args))

    detail.unschedule_button.click()
    detail.duplicate_button.click()
    detail.delete_button.click()

    assert emitted == [("fab", "unschedule"), ("fab", "duplicate"), ("fab", "delete")]
    assert detail.delete_button.property("variant") == "danger"


def test_read_only_disables_editing(qtbot: Any) -> None:
    host = _detail(qtbot, read_only=True)
    detail = host.detail
    emitted: list[object] = []
    for signal in (
        detail.field_changed,
        detail.people_changed,
        detail.dates_changed,
        detail.status_changed,
        detail.action_requested,
    ):
        signal.connect(lambda *args: emitted.append(args))

    assert detail.read_only_label.isVisibleTo(detail)
    assert detail.title_edit.isReadOnly()
    assert detail.number_edit.isReadOnly()
    assert detail.notes_edit.isReadOnly()
    assert not detail.start_row.isEnabled()
    assert not detail.deadline_row.isEnabled()
    assert not detail.delete_button.isEnabled()
    assert not detail.clear_deadline_button.isVisibleTo(detail)
    assert all(not chip.isEnabled() for chip in detail.type_chips())
    assert all(not button.isEnabled() for button in detail.person_buttons())
    assert all(not button.isEnabled() for button in detail.status_buttons.buttons())

    detail._toggle_picker("start")  # noqa: SLF001
    assert detail.open_picker() is None
    apply_theme(QApplication.instance(), AppearanceConfig(mode="dark"))  # type: ignore[arg-type]
    assert all(not chip.isEnabled() for chip in detail.type_chips())  # rebuilt, still locked
    _focus(qtbot, detail.title_edit)
    qtbot.keyClicks(detail.title_edit, "x")
    _focus(qtbot, host.other)
    detail.urgent_button.click()
    qtbot.wait(900)
    assert emitted == []


def test_multi_selection_edits_every_task_and_shows_mixed_values(qtbot: Any) -> None:
    host = _detail(qtbot)
    detail = host.detail
    emitted: list[tuple[object, ...]] = []
    detail.field_changed.connect(lambda *args: emitted.append(args))
    detail.people_changed.connect(lambda *args: emitted.append(args))
    detail.status_changed.connect(lambda *args: emitted.append(("status", *args)))

    detail.set_multi(3, {"resource_ids": ["nf"]}, snapshot=_snapshot())

    assert detail.mode() == "multi"
    assert detail.task_id() == ""
    assert detail.multi_label.text() == "3 tâches sélectionnées"
    assert _checked_type(detail) is None
    assert detail.type_mixed.text() == "—"
    assert detail.status_buttons.checkedButton() is None
    assert detail.status_mixed.text() == "—"
    assert detail.urgent_button.text() == "! Urgent —"
    assert not detail.urgent_button.isChecked()
    assert _checked_people(detail) == ["nf"]
    assert detail.people_count.text() == "1 en commun"
    assert not detail.number_block.isVisibleTo(detail)
    assert not detail.title_block.isVisibleTo(detail)
    assert not detail.dates_block.isVisibleTo(detail)
    assert not detail.deadline_row.isVisibleTo(detail)
    assert detail.urgent_button.isVisibleTo(detail)
    assert not detail.author_label.isVisibleTo(detail)
    tops = [
        _top(detail, block)
        for block in (
            detail.type_block,
            detail.deadline_block,
            detail.people_block,
            detail.status_block,
        )
    ]
    assert tops == sorted(tops)

    _click(qtbot, _chip(detail, "livraison"))
    _click(qtbot, detail.urgent_button)
    nicolas = detail.person_button("nf")
    assert nicolas is not None
    _click(qtbot, nicolas)
    done = detail.status_buttons.button(2)
    assert done is not None
    done.click()

    assert emitted == [
        (MULTI_SELECTION, "task_type", "livraison"),
        (MULTI_SELECTION, "urgent", True),
        (MULTI_SELECTION, [], ["nf"]),
        ("status", MULTI_SELECTION, "done"),
    ]
    assert detail.type_mixed.text() == ""
    assert detail.urgent_button.text() == "! Urgent"
    assert detail.status_mixed.text() == ""


# ----------------------------------------------------------------- night ----


NIGHT = _task(
    "night",
    title="Aéroport",
    task_type="montage",
    start=wall_time(MONDAY, 21, 0),
    end=wall_time(TUESDAY, 5, 0),
    assignments={"nf": {}},
    off_hours=True,
)


def test_night_work_keeps_its_exact_times_and_its_rest(qtbot: Any) -> None:
    host = _detail(qtbot, NIGHT)
    detail = host.detail

    assert detail.night_button.text() == "Travail de nuit"
    assert detail.night_button.isChecked()
    assert detail.start_row.value() == "lun 19 oct · 21:00"
    assert detail.end_row.value() == "mar 20 oct · 05:00"
    assert detail.duration_label.text() == "Durée : 8 h (nuit)"
    assert detail.rest_label.text().startswith("Repos : dès lun ")
    assert not detail.rest_label.isHidden()

    _click(qtbot, detail.start_row)
    detail.start_picker.step(1, 1)  # 22:00
    with qtbot.waitSignal(detail.dates_changed) as blocker:
        _click(qtbot, detail.start_row)
    # Night work keeps its exact times and its real length.
    assert blocker.args == ["night", wall_time(MONDAY, 22, 0), wall_time(TUESDAY, 6, 0)]


def test_the_night_toggle_asks_for_night_work(qtbot: Any) -> None:
    host = _detail(qtbot, OTHER)
    detail = host.detail

    assert not detail.night_button.isChecked()
    assert detail.rest_label.isHidden()
    with qtbot.waitSignal(detail.field_changed) as blocker:
        detail.night_button.click()
    assert blocker.args == ["other", "off_hours", True]


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
        now=NOW,
    )
    side.show_detail()
    side.resize(312, 1460)
    side.detail.scroll_area.widget().adjustSize()
    qtbot.wait(50)
    _save(side, f"detail_{mode}")

    side.detail.start_row.click()
    side.detail.start_picker.step(1, 1)
    qtbot.wait(20)
    _save(side, f"detail_rolls_{mode}")
    side.detail.flush()

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
