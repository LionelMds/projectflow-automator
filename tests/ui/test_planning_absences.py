"""Absences on the Planning: hit-testing in the grid, « Absence » dialog, team page."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from PySide6.QtCore import QDate, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.commands import (
    CommandContext,
    PlanningCommandError,
    calendar_resolver,
)
from projectflow.core.planning.layout import Density
from projectflow.core.planning.models import (
    Absence,
    Author,
    PlanningMeta,
    PlanningTask,
    ProjectCache,
    RecurringOff,
    Resource,
)
from projectflow.core.planning.walltime import wall_time
from projectflow.ui.dialogs.planning_absence import (
    ORDER_MESSAGE,
    AbsenceBuilder,
    AbsenceDialog,
    absence_dates_text,
)
from projectflow.ui.dialogs.planning_team import TeamDialog, absence_order
from projectflow.ui.planning.grid import PlanningGrid
from projectflow.ui.planning.grid_items import build_grid_style, paint_background
from projectflow.ui.planning.grid_model import GridModel, build_grid_model, recurring_resource_id
from projectflow.ui.theme import build_theme

STAMP = "2026-10-01T08:00:00Z"
NOW = "2026-10-22T10:00:00Z"
TODAY = date(2026, 10, 22)
MONDAY = date(2026, 10, 26)
WEDNESDAY = MONDAY + timedelta(days=2)
THURSDAY = MONDAY + timedelta(days=3)
FRIDAY = MONDAY + timedelta(days=4)
AUTHOR = Author(initials="LM", name="Lionel", poste="BUREAU-1", poste_id="p1")
SEEDER = Author(initials="SB", name="Sophie", poste="BUREAU-2", poste_id="p2")


def at(day: date, hour: int, minute: int = 0) -> int:
    return wall_time(day, hour, minute)


def resource(rid: str, name: str, team: str, order: int, **extra: object) -> Resource:
    return Resource.model_validate(
        {
            "id": rid,
            "name": name,
            "initials": rid.upper(),
            "team": team,
            "order": order,
            "created_at": STAMP,
            "updated_at": STAMP,
            **extra,
        }
    )


def absence(aid: str, rid: str, first: date, last: date, **extra: object) -> Absence:
    return Absence.model_validate(
        {
            "id": aid,
            "resource_id": rid,
            "first_day": first.isoformat(),
            "last_day": last.isoformat(),
            "created_at": STAMP,
            "created_by": SEEDER.model_dump(),
            "updated_at": STAMP,
            "updated_by": SEEDER.model_dump(),
            **extra,
        }
    )


def snapshot(*absences: Absence) -> PlanningSnapshot:
    resources = [
        resource("mr", "Marc Rey", "atelier", 1000),
        resource(
            "jd",
            "Julien Dubois",
            "atelier",
            2000,
            recurring_off=[RecurringOff(weekday=2, reason="ecole").model_dump()],
        ),
        resource("lb", "Luca Bianchi", "montage", 1000),
    ]
    over = PlanningTask.model_validate(
        {
            "id": "over",
            "project_number": "2026-5231",
            "title": "Pose",
            "project_cache": ProjectCache(client="Müller SA").model_dump(),
            "start": at(MONDAY, 13),
            "end": at(MONDAY, 16, 30),
            "assignments": {"lb": {}},
            "created_at": STAMP,
            "updated_at": STAMP,
        }
    )
    found = absences or (
        absence("vac", "lb", MONDAY, MONDAY + timedelta(days=1), reason="vacances", note="Valais"),
    )
    return PlanningSnapshot(
        tasks={over.id: over},
        resources={item.id: item for item in resources},
        absences={item.id: item for item in found},
        meta=PlanningMeta(),
    )


class FakePlanning:
    """Runs each command on its snapshot, as the planning service would."""

    def __init__(self, snap: PlanningSnapshot) -> None:
        self.snapshot = snap
        self.labels: list[str] = []
        self.refused = ""

    def apply(self, build: AbsenceBuilder, label: str) -> None:
        if self.refused:
            raise PlanningCommandError(self.refused)
        context = CommandContext(
            snapshot=self.snapshot,
            author=AUTHOR,
            now=NOW,
            calendar_for=calendar_resolver(self.snapshot),
        )
        self.snapshot = self.snapshot.apply(build(context))
        self.labels.append(label)


class Recorder:
    def __init__(self, *signals: Any) -> None:
        self.calls: list[tuple[Any, ...]] = []
        for signal in signals:
            signal.connect(lambda *args: self.calls.append(args))


def qdate(day: date) -> QDate:
    return QDate(day.year, day.month, day.day)


# ------------------------------------------------------------------ grid ----


def make_model(grid: PlanningGrid, snap: PlanningSnapshot, **options: Any) -> GridModel:
    params: dict[str, Any] = {
        "view": "week",
        "anchor": MONDAY,
        "viewport_width": grid.viewport_width(),
        "today": TODAY,
        "now": at(TODAY, 10),
        "density": Density.NORMAL,
    }
    params.update(options)
    return build_grid_model(snap, **params)


@pytest.fixture
def grid(qtbot: Any) -> PlanningGrid:
    widget = PlanningGrid()
    qtbot.addWidget(widget)
    widget.resize(1200, 640)
    widget.show()
    qtbot.waitExposed(widget)
    widget.set_model(make_model(widget, snapshot()))
    return widget


def scene_point(grid: PlanningGrid, row_id: str, value: int) -> QPointF:
    model = grid.model
    assert model is not None
    row = model.rows.get(row_id)
    assert row is not None
    return QPointF(model.timescale.x_of(value), row.top + row.height / 2)


def view_point(grid: PlanningGrid, row_id: str, value: int) -> QPoint:
    return grid.view.mapFromScene(scene_point(grid, row_id, value))


def test_absence_blocks_are_hit_but_bars_over_them_win(grid: PlanningGrid) -> None:
    morning = scene_point(grid, "lb", at(MONDAY, 9))
    block = grid.scene.absence_at(morning)
    assert block is not None
    assert block.absence_id == "vac"
    assert not block.recurring
    # The task of Monday afternoon is drawn over the vacation: the bar is picked.
    afternoon = scene_point(grid, "lb", at(MONDAY, 14))
    assert grid.scene.bar_at(afternoon) is not None
    assert grid.scene.absence_at(afternoon) is None
    # Nothing on Thursday.
    assert grid.scene.absence_at(scene_point(grid, "lb", at(THURSDAY, 9))) is None
    school = grid.scene.absence_at(scene_point(grid, "jd", at(WEDNESDAY, 9)))
    assert school is not None
    assert school.recurring
    assert recurring_resource_id(school.absence_id) == "jd"


def test_double_click_on_an_absence_asks_to_edit_it(qtbot: Any, grid: PlanningGrid) -> None:
    edits = Recorder(grid.absence_activated)
    cells = Recorder(grid.cell_activated)
    doubles = Recorder(grid.bar_double_clicked)
    viewport = grid.view.viewport()
    no = Qt.KeyboardModifier.NoModifier
    qtbot.mouseDClick(
        viewport, Qt.MouseButton.LeftButton, no, view_point(grid, "lb", at(MONDAY, 9))
    )
    assert edits.calls == [("vac",)]
    assert cells.calls == []
    qtbot.mouseDClick(
        viewport, Qt.MouseButton.LeftButton, no, view_point(grid, "lb", at(MONDAY, 14))
    )
    assert doubles.calls == [("over", "lb")]
    assert edits.calls == [("vac",)]
    qtbot.mouseDClick(
        viewport, Qt.MouseButton.LeftButton, no, view_point(grid, "jd", at(WEDNESDAY, 9))
    )
    assert edits.calls[-1][0].startswith("recurring:")


def test_right_click_and_hover_on_an_absence(grid: PlanningGrid) -> None:
    menus = Recorder(grid.absence_menu_requested)
    bar_menus = Recorder(grid.context_menu_requested)
    hovers = Recorder(grid.absence_hovered)
    bars = Recorder(grid.hover_changed)
    order: list[str] = []
    grid.absence_hovered.connect(lambda absence_id, _pos: order.append(f"absence:{absence_id}"))
    grid.hover_changed.connect(lambda task_id, _pos: order.append(f"bar:{task_id}"))
    morning = scene_point(grid, "lb", at(MONDAY, 9))
    grid.context_menu_at(morning, QPoint(7, 7))
    assert menus.calls == [("vac", QPoint(7, 7))]
    assert bar_menus.calls == []
    grid.context_menu_at(scene_point(grid, "lb", at(MONDAY, 14)), QPoint(8, 8))
    assert bar_menus.calls[0][:2] == ("over", "lb")
    assert len(menus.calls) == 1

    grid.gestures.hover(morning, QPoint(1, 1))
    assert hovers.calls == [("vac", QPoint(1, 1))]
    grid.gestures.hover(scene_point(grid, "lb", at(MONDAY, 10)), QPoint(2, 2))
    assert len(hovers.calls) == 1  # same block: no new signal
    order.clear()
    grid.gestures.hover(scene_point(grid, "lb", at(MONDAY, 14)), QPoint(3, 3))
    # What the pointer left first, then the bar it reached.
    assert order == ["absence:", "bar:over"]
    order.clear()
    grid.gestures.hover(morning, QPoint(4, 4))
    assert order == ["bar:", "absence:vac"]
    grid.gestures.leave()
    assert hovers.calls[-1] == ("", QPoint())
    assert bars.calls[-1][0] == ""


def test_alternate_columns_are_painted_under_the_rest(grid: PlanningGrid) -> None:
    model = grid.model
    assert model is not None
    for mode in ("light", "dark"):
        theme = build_theme(mode, "acier")
        style = build_grid_style(theme)
        area = QRectF(0, 0, model.timescale.width, model.rows.height)
        image = QImage(int(area.width()), int(area.height()), QImage.Format.Format_ARGB32)
        painter = QPainter(image)
        paint_background(painter, model, area, style)
        painter.end()
        row = model.rows.get("mr")
        assert row is not None
        y = int(row.top + row.height / 2)

        def color(day: date, y: int = y, image: QImage = image) -> QColor:
            return image.pixelColor(int(model.timescale.x_of(at(day, 8))), y)

        monday, tuesday = color(MONDAY), color(MONDAY + timedelta(days=1))
        assert monday != tuesday
        assert color(WEDNESDAY) == monday
        assert color(THURSDAY) == tuesday
        assert abs(monday.lightness() - tuesday.lightness()) <= 28  # light grey, not a band
        # The vacation of Tuesday stays darker than the tint around it.
        lb = model.rows.get("lb")
        assert lb is not None
        hatched = image.pixelColor(
            int(model.timescale.x_of(at(MONDAY + timedelta(days=1), 8))), int(lb.top + 3)
        )
        assert hatched != tuesday


# ---------------------------------------------------------------- dialog ----


def open_absence_dialog(qtbot: Any, planning: FakePlanning, **options: Any) -> AbsenceDialog:
    dialog = AbsenceDialog(planning.snapshot, planning.apply, **options)
    qtbot.addWidget(dialog)
    return dialog


def test_dates_text() -> None:
    assert absence_dates_text(MONDAY, FRIDAY) == "lun 26.10 → ven 30.10"
    assert absence_dates_text(THURSDAY, THURSDAY, "afternoon") == "jeu 29.10 · après-midi"


def test_absence_dialog_refuses_an_end_before_the_start(qtbot: Any) -> None:
    planning = FakePlanning(snapshot())
    dialog = open_absence_dialog(qtbot, planning, absence=planning.snapshot.absences["vac"])
    assert dialog.save_button.isEnabled()
    dialog.last_edit.setDate(qdate(MONDAY - timedelta(days=3)))
    assert not dialog.save_button.isEnabled()
    assert not dialog.error_label.isHidden()
    assert dialog.error_label.text() == ORDER_MESSAGE
    assert not dialog.save()
    assert planning.labels == []
    # Moving the start past the end carries the end along.
    dialog.first_edit.setDate(qdate(THURSDAY))
    assert dialog.last_day() == THURSDAY
    assert dialog.save_button.isEnabled()


def test_absence_dialog_offers_no_friday_afternoon(qtbot: Any) -> None:
    planning = FakePlanning(snapshot())
    dialog = open_absence_dialog(qtbot, planning, resource_id="mr", first_day=THURSDAY)
    assert dialog.part_button("afternoon").isEnabled()
    dialog.set_part("afternoon")
    dialog.first_edit.setDate(qdate(FRIDAY))
    dialog.last_edit.setDate(qdate(FRIDAY))
    assert not dialog.part_button("afternoon").isEnabled()
    assert dialog.part_button("morning").isEnabled()
    assert dialog.part() == "day"
    # Several days: only « Journée » makes sense.
    dialog.set_part("morning")
    dialog.last_edit.setDate(qdate(FRIDAY + timedelta(days=3)))
    assert dialog.part() == "day"
    assert not dialog.part_button("morning").isEnabled()


def test_absence_dialog_saves_and_deletes(qtbot: Any) -> None:
    planning = FakePlanning(snapshot())
    dialog = open_absence_dialog(qtbot, planning, absence=planning.snapshot.absences["vac"])
    assert dialog.note() == "Valais"
    assert "Créé par SB · BUREAU-2" in dialog.author_label.text()
    dialog.reason_combo.setCurrentIndex(dialog.reason_combo.findData("formation"))
    dialog.last_edit.setDate(qdate(WEDNESDAY))
    assert dialog.save()
    assert dialog.outcome == "saved"
    assert planning.labels == ["Modifier l'absence de LB"]
    saved = planning.snapshot.absences["vac"]
    assert (saved.reason, saved.first_day, saved.last_day) == ("formation", MONDAY, WEDNESDAY)
    assert saved.note == "Valais"

    again = open_absence_dialog(qtbot, planning, absence=saved)
    assert again.delete()
    assert again.outcome == "deleted"
    assert planning.labels[-1] == "Supprimer l'absence de LB"
    assert planning.snapshot.absences["vac"].deleted


def test_absence_dialog_creates_and_shows_refusals(qtbot: Any) -> None:
    planning = FakePlanning(snapshot())
    dialog = open_absence_dialog(qtbot, planning, resource_id="mr", first_day=THURSDAY)
    assert dialog.delete_button.isHidden()
    dialog.set_part("morning")
    dialog.note_edit.setText("  dentiste ")
    planning.refused = "Planning en lecture seule."
    assert not dialog.save()
    assert dialog.error_label.text() == "Planning en lecture seule."
    planning.refused = ""
    assert dialog.save()
    assert dialog.outcome == "created"
    created = next(item for item in planning.snapshot.absences.values() if item.id != "vac")
    assert (created.resource_id, created.part, created.note) == ("mr", "morning", "dentiste")
    assert planning.labels == ["Ajouter l'absence de MR"]


def test_read_only_absence_dialog(qtbot: Any) -> None:
    planning = FakePlanning(snapshot())
    dialog = open_absence_dialog(
        qtbot,
        planning,
        absence=planning.snapshot.absences["vac"],
        read_only=True,
        read_only_message="Synchronisation indisponible.",
    )
    assert dialog.read_only_label.text() == "Synchronisation indisponible."
    assert dialog.save_button.isHidden()
    assert not dialog.delete_button.isEnabled()
    assert not dialog.reason_combo.isEnabled()
    assert not dialog.save()
    assert not dialog.delete()
    assert planning.labels == []


# ------------------------------------------------------------- team page ----


def team_snapshot() -> PlanningSnapshot:
    return snapshot(
        absence("past", "mr", date(2026, 10, 1), date(2026, 10, 2), reason="conge"),
        absence("older", "lb", date(2026, 9, 7), date(2026, 9, 11), reason="vacances"),
        absence("now", "lb", date(2026, 10, 20), date(2026, 10, 23), reason="militaire"),
        absence("soon", "mr", date(2026, 11, 2), date(2026, 11, 2), reason="formation"),
        absence("later", "lb", date(2026, 12, 21), date(2026, 12, 31), reason="vacances"),
    )


def open_team(qtbot: Any, planning: FakePlanning, reason: str = "") -> TeamDialog:
    dialog = TeamDialog(
        lambda: planning.snapshot,
        planning.apply,
        read_only_reason=lambda: reason,
        today=lambda: TODAY,
    )
    qtbot.addWidget(dialog)
    dialog.show_page("absences")
    return dialog


def ids(dialog: TeamDialog) -> list[str]:
    return [item.id for item in dialog.absences_page.shown_absences()]


def test_absence_order_upcoming_then_past() -> None:
    order = absence_order(team_snapshot().live_absences(), TODAY)
    assert [item.id for item in order] == ["now", "soon", "later", "past", "older"]


def test_team_absences_page_lists_and_filters(qtbot: Any) -> None:
    planning = FakePlanning(team_snapshot())
    dialog = open_team(qtbot, planning)
    page = dialog.absences_page
    assert ids(dialog) == ["now", "soon", "later"]
    assert dialog._page_meta["absences"].text() == "3"  # noqa: SLF001
    assert page.table.item(0, 0).text() == "LB · Luca Bianchi"
    assert page.table.item(0, 1).text() == "Militaire / PC"
    assert page.table.item(0, 2).text() == "mar 20.10.2026"
    assert page.table.item(1, 4).text() == "Journée"
    page.past_checkbox.setChecked(True)
    assert ids(dialog) == ["now", "soon", "later", "past", "older"]
    page.filter_person("mr")
    assert ids(dialog) == ["soon", "past"]
    page.past_checkbox.setChecked(False)
    assert ids(dialog) == ["soon"]
    dialog.show_absences("lb")
    assert ids(dialog) == ["now", "later"]
    assert not page.edit_button.isEnabled()


def test_team_absences_page_edits_and_deletes(qtbot: Any) -> None:
    planning = FakePlanning(team_snapshot())
    dialog = open_team(qtbot, planning)
    page = dialog.absences_page
    page.select_absence("soon")
    assert page.edit_button.isEnabled()
    page.edit()
    editor = page.absence_dialog
    assert editor is not None
    editor.last_edit.setDate(qdate(date(2026, 11, 4)))
    assert editor.save()
    assert planning.labels == ["Modifier l'absence de MR"]
    assert planning.snapshot.absences["soon"].last_day == date(2026, 11, 4)
    assert page.absence_dialog is None
    assert page.table.item(1, 3).text() == "mer 04.11.2026"
    assert page.selected_absence() is not None
    assert page.selected_absence().id == "soon"  # type: ignore[union-attr]
    # Double-click opens the same dialog.
    page.table.itemDoubleClicked.emit(page.table.item(0, 1))
    assert page.absence_dialog is not None
    assert page.absence_dialog.absence_id == "now"
    page.absence_dialog.reject()
    assert page.absence_dialog is None
    page.select_absence("later")
    page.delete()
    assert planning.labels[-1] == "Supprimer l'absence de LB"
    assert ids(dialog) == ["now", "soon"]
    # Ajouter: a new absence of the person filtered.
    page.filter_person("lb")
    page.add()
    creator = page.absence_dialog
    assert creator is not None
    assert creator.resource_id() == "lb"
    assert creator.first_day() == TODAY
    assert creator.save()
    assert len(ids(dialog)) == 2


def test_team_absences_page_read_only(qtbot: Any) -> None:
    planning = FakePlanning(team_snapshot())
    dialog = open_team(qtbot, planning, reason="Synchronisation indisponible.")
    page = dialog.absences_page
    page.add()
    assert page.absence_dialog is None
    assert page.error_label.text() == "Synchronisation indisponible."
    page.select_absence("soon")
    page.delete()
    assert planning.labels == []
    page.edit()
    viewer = page.absence_dialog
    assert viewer is not None
    assert viewer.read_only
    viewer.reject()
