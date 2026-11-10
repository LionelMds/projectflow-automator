"""Appointments dragged from Outlook, Apple Calendar or an .ics file onto the planning."""

from __future__ import annotations

import struct
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QByteArray, QMimeData, QPointF, Qt, QUrl
from test_planning_controller import (  # type: ignore[import-not-found]
    Hooks,
    at,
    data_dirs,  # noqa: F401 - fixture
    make_config,
    make_controller,
    seed_store,
    snapshot_of,
    started,
    toast_texts,
    wait_for,
)
from test_planning_grid import (  # type: ignore[import-not-found]
    THURSDAY,
    Recorder,
    grid,  # noqa: F401 - fixture
)
from test_planning_grid import at as grid_at  # type: ignore[import-not-found]

from projectflow.core.planning.calendar_import import ImportedEvent
from projectflow.core.planning.models import PlanningTask
from projectflow.exceptions import OutlookError
from projectflow.outlook.selection import item_event, read_outlook_selection
from projectflow.ui.planning.backlog import IMPORT_HINT, BacklogPanel
from projectflow.ui.planning.external_drop import (
    TASK_MIME,
    ExternalDrop,
    descriptor_names,
    external_drop,
)
from projectflow.ui.planning.grid import TASK_MIME as GRID_TASK_MIME
from projectflow.ui.planning.grid import PlanningGrid, task_mime_data
from projectflow.ui.planning_controller import PlanningHooks

ICS = (
    "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:2026-118 - Pose\r\n"
    "DTSTART:20261022T080000\r\nDTEND:20261022T100000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)
OUTLOOK_FORMAT = 'application/x-qt-windows-mime;value="RenPrivateMessages"'
DESCRIPTOR_FORMAT = 'application/x-qt-windows-mime;value="FileGroupDescriptorW"'


def descriptor(*names: str) -> bytes:
    data = struct.pack("<I", len(names))
    for name in names:
        entry = bytearray(592)
        encoded = name.encode("utf-16-le")
        entry[72 : 72 + len(encoded)] = encoded
        data += bytes(entry)
    return data


def ics_mime(tmp_path: Path) -> QMimeData:
    path = tmp_path / "rendez-vous.ics"
    path.write_text(ICS, encoding="utf-8")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    return mime


def outlook_mime(*names: str) -> QMimeData:
    mime = QMimeData()
    mime.setData(OUTLOOK_FORMAT, QByteArray(b"\x00"))
    mime.setData(DESCRIPTOR_FORMAT, QByteArray(descriptor(*names)))
    return mime


# ------------------------------------------------------------ recognised ----


def test_what_can_be_imported_is_recognised(tmp_path: Path) -> None:
    assert TASK_MIME == GRID_TASK_MIME
    files = ics_mime(tmp_path)
    passing = external_drop(files)
    assert passing is not None
    assert passing.events == ()  # the file is read at the drop only
    dropped = external_drop(files, read=True)
    assert dropped is not None
    assert [event.subject for event in dropped.events] == ["2026-118 - Pose"]
    outlook = external_drop(outlook_mime("Pose.msg", "Réglage.msg"))
    assert outlook == ExternalDrop(outlook_items=2, source="Outlook")
    assert descriptor_names(descriptor("a.msg", "b.txt")) == ["a.msg", "b.txt"]
    text = QMimeData()
    text.setData("text/calendar", QByteArray(ICS.encode("utf-8")))
    calendar = external_drop(text, read=True)
    assert calendar is not None
    assert len(calendar.events) == 1
    plain = QMimeData()
    plain.setText("Une ligne de texte")
    assert external_drop(plain) is None
    other = QMimeData()
    other.setUrls([QUrl.fromLocalFile(str(tmp_path / "plan.pdf"))])
    assert external_drop(other) is None
    assert external_drop(task_mime_data("t1")) is None


# ------------------------------------------------------------------ grid ----


def test_dropped_on_the_grid_it_lands_on_the_row_as_a_copy(
    grid: PlanningGrid,  # noqa: F811 - fixture
    tmp_path: Path,
) -> None:
    imports = Recorder(grid.import_dropped)
    model = grid.model
    assert model is not None
    row = model.rows.get("mr")
    assert row is not None
    point = QPointF(model.timescale.x_of(grid_at(THURSDAY, 9)), row.top + row.height / 2)
    mime = ics_mime(tmp_path)
    assert grid.drag_over(mime, point)
    assert "Importer" in grid.scene.label.text
    assert grid.drop_at(mime, point)
    ((drop, row_id, start),) = imports.calls
    assert isinstance(drop, ExternalDrop)
    assert len(drop.events) == 1
    assert row_id == "mr"
    assert start // (24 * 60) == grid_at(THURSDAY, 0) // (24 * 60)
    grid.set_read_only(True)
    assert not grid.drag_over(mime, point)


def test_backlog_takes_an_import(qtbot: Any, tmp_path: Path) -> None:
    panel = BacklogPanel()
    qtbot.addWidget(panel)
    imports = Recorder(panel.import_requested)
    panel.show_drop_overlay(hint=IMPORT_HINT)
    assert panel.overlay.hint == IMPORT_HINT
    drop = external_drop(ics_mime(tmp_path), read=True)
    panel.import_drop(drop)
    assert imports.calls == [(drop,)]
    assert not panel.overlay.isVisible()


# --------------------------------------------------------------- Outlook ----


class Item:
    def __init__(self, kind: int, **values: Any) -> None:
        self.Class = kind
        for name, value in values.items():
            setattr(self, name, value)


class Selection:
    def __init__(self, items: list[Item]) -> None:
        self.items = items
        self.Count = len(items)

    def Item(self, index: int) -> Item:  # noqa: N802 - COM name
        return self.items[index - 1]


class Outlook:
    def __init__(self, items: list[Item], *, busy: int = 0) -> None:
        self.selection = Selection(items)
        self.busy = busy

    def ActiveExplorer(self) -> Any:  # noqa: N802 - COM name
        if self.busy:
            self.busy -= 1
            raise OSError("call rejected")
        return type("Explorer", (), {"Selection": self.selection})()


def utc_labelled(year: int, month: int, day: int, hour: int = 0) -> datetime:
    # pywin32 labels the local wall time of Outlook as UTC.
    return datetime(year, month, day, hour, tzinfo=UTC)


def test_outlook_items_become_appointments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("projectflow.outlook.selection._com_error_type", lambda: OSError)
    monkeypatch.setattr("projectflow.outlook.selection._pythoncom", lambda: None)
    appointment = Item(
        26,
        Subject="2026-118 - Pose",
        Start=utc_labelled(2026, 11, 12, 8),
        End=utc_labelled(2026, 11, 12, 12),
        AllDayEvent=False,
        Location="Sion",
        Body="Clé chez le voisin",
    )
    task = Item(
        48,
        Subject="Commander gonds",
        StartDate=utc_labelled(4501, 1, 1),
        DueDate=utc_labelled(2026, 11, 18),
        Body="",
    )
    mail = Item(43, Subject="Demande d'offre", Body="Bonjour")
    outlook = Outlook([appointment, task, mail, Item(43, Subject="", Body="")], busy=2)
    events = read_outlook_selection(app_factory=lambda: outlook, sleep=lambda _s: None)
    assert events == [
        ImportedEvent(
            "2026-118 - Pose",
            datetime(2026, 11, 12, 8),  # noqa: DTZ001 - local wall time
            datetime(2026, 11, 12, 12),  # noqa: DTZ001
            location="Sion",
            notes="Clé chez le voisin",
        ),
        ImportedEvent(
            "Commander gonds",
            datetime(2026, 11, 18),  # noqa: DTZ001
            datetime(2026, 11, 19),  # noqa: DTZ001
            all_day=True,
        ),
        ImportedEvent("Demande d'offre", notes="Bonjour"),
    ]
    assert item_event(Item(26, Subject="x", Start=None, End=None, AllDayEvent=False)) is not None
    with pytest.raises(OutlookError):
        read_outlook_selection(app_factory=lambda: Outlook([], busy=9), sleep=lambda _s: None)


# ------------------------------------------------------------ controller ----


class ImportHooks(Hooks):
    def __init__(self) -> None:
        super().__init__()
        self.outlook: list[ImportedEvent] = []
        self.fail = False

    async def read_outlook(self) -> list[ImportedEvent]:
        if self.fail:
            raise OutlookError("aucune fenêtre Outlook ouverte.")
        return self.outlook

    def build(self) -> PlanningHooks:
        from dataclasses import replace  # noqa: PLC0415

        return replace(super().build(), read_outlook_selection=self.read_outlook)


def created_tasks(controller: Any, before: set[str]) -> list[PlanningTask]:
    tasks = snapshot_of(controller).tasks
    return [task for tid, task in tasks.items() if tid not in before and not task.deleted]


@pytest.mark.asyncio
async def test_a_file_dropped_on_a_row_becomes_tasks(qtbot: Any, tmp_path: Path) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    hooks = ImportHooks()
    controller, tab, _ = make_controller(qtbot, make_config(store), hooks=hooks)
    await started(controller)
    before = set(snapshot_of(controller).tasks)
    drop = external_drop(ics_mime(tmp_path), read=True)
    tab.grid.import_dropped.emit(drop, "mre", at(date(2026, 10, 22), 6, 30))
    (task,) = created_tasks(controller, before)
    assert task.project_number == "2026-118"
    assert task.title == "Pose"
    assert task.project_cache.designation == "Garde-corps escalier"
    assert list(task.assignments) == ["mre"]
    assert task.start == at(date(2026, 10, 22), 8)
    assert task.end == at(date(2026, 10, 22), 10)
    controller.undo()
    assert not created_tasks(controller, before)
    await controller.aclose()


@pytest.mark.asyncio
async def test_outlook_items_dropped_in_the_column_wait_there(qtbot: Any, tmp_path: Path) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    hooks = ImportHooks()
    hooks.outlook = [
        ImportedEvent(
            "RDV 124 réglage portail",
            datetime(2026, 10, 27, 13),  # noqa: DTZ001
            datetime(2026, 10, 27, 16),  # noqa: DTZ001
        ),
        ImportedEvent("Demande d'offre Dupont", notes="Bonjour"),
    ]
    controller, tab, _ = make_controller(qtbot, make_config(store), hooks=hooks)
    await started(controller)
    before = set(snapshot_of(controller).tasks)
    tab.backlog.import_requested.emit(ExternalDrop(outlook_items=2, source="Outlook"))
    await wait_for(lambda: len(created_tasks(controller, before)) == 2)
    offre, portail = sorted(created_tasks(controller, before), key=lambda t: t.title)
    assert (portail.project_number, portail.title, portail.start) == (
        "2026-124",
        "RDV réglage portail",
        None,
    )
    assert portail.estimate_minutes == 180
    assert (offre.project_number, offre.title, offre.notes) == (
        "",
        "Demande d'offre Dupont",
        "Bonjour",
    )
    hooks.fail = True
    tab.backlog.import_requested.emit(ExternalDrop(outlook_items=1, source="Outlook"))
    await wait_for(lambda: any("Outlook impossible" in text for text in toast_texts(tab)))
    await controller.aclose()


def test_drop_actions_never_move_what_comes_from_outside(tmp_path: Path) -> None:
    from projectflow.ui.planning.grid import _drop_action  # noqa: PLC0415

    assert _drop_action(ics_mime(tmp_path)) == Qt.DropAction.CopyAction
    assert _drop_action(outlook_mime("a.msg")) == Qt.DropAction.CopyAction
    assert _drop_action(task_mime_data("t1")) == Qt.DropAction.MoveAction
