"""Planning copied through the Outlook of the poste (COM), against a fake Outlook."""

from __future__ import annotations

import importlib
import re
from datetime import UTC, datetime
from typing import Any

import pytest

from projectflow.core.planning.outlook_sync import EventSpec
from projectflow.exceptions import ConfigError, OutlookError
from projectflow.outlook.calendar import DesktopOutlookCalendarGateway
from projectflow.services import open_desktop_outlook_calendar


class Collection:
    def __init__(self, items: list[Any]) -> None:
        self.items = items

    @property
    def Count(self) -> int:  # noqa: N802 - COM name
        return len(self.items)

    def Item(self, index: Any) -> Any:  # noqa: N802 - COM name
        return self.items[index - 1]


class UserProperty:
    def __init__(self, name: str) -> None:
        self.Name = name
        self.Value = ""


class UserProperties:
    def __init__(self) -> None:
        self.props: dict[str, UserProperty] = {}

    def Find(self, name: str) -> UserProperty | None:  # noqa: N802 - COM name
        return self.props.get(name)

    def Add(self, name: str, _kind: int, _in_folder: object) -> UserProperty:  # noqa: N802
        self.props[name] = UserProperty(name)
        return self.props[name]


class Appointment:
    def __init__(self, folder: Folder, entry_id: str) -> None:
        self.folder = folder
        self.EntryID = entry_id
        self.UserProperties = UserProperties()
        self.saved = False
        self.Subject = ""
        self.Start: datetime | None = None
        self.StartInStartTimeZone: datetime | None = None

    def Save(self) -> None:  # noqa: N802 - COM name
        self.saved = True

    def Delete(self) -> None:  # noqa: N802 - COM name
        self.folder.appointments.remove(self)

    def prop(self, name: str) -> str | None:
        found = self.UserProperties.Find(name)
        return None if found is None else str(found.Value)


class Row:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def Item(self, index: int) -> object:  # noqa: N802 - COM name
        return self.values[index - 1]


class Columns:
    def __init__(self) -> None:
        self.names: list[str] = []

    def RemoveAll(self) -> None:  # noqa: N802 - COM name
        self.names.clear()

    def Add(self, name: str) -> None:  # noqa: N802 - COM name
        self.names.append(name)


class Table:
    def __init__(self, folder: Folder) -> None:
        self.folder = folder
        self.Columns = Columns()
        self.position = 0

    @property
    def EndOfTable(self) -> bool:  # noqa: N802 - COM name
        return self.position >= len(self.folder.appointments)

    def GetNextRow(self) -> Row:  # noqa: N802 - COM name
        item = self.folder.appointments[self.position]
        self.position += 1
        values: list[object] = []
        for name in self.Columns.names:
            if name == "EntryID":
                values.append(item.EntryID)
            else:
                values.append(item.prop(name.split("/")[-2]))
        return Row(values)


class Items:
    def __init__(self, folder: Folder) -> None:
        self.folder = folder

    def Add(self, _kind: int) -> Appointment:  # noqa: N802 - COM name
        self.folder.outlook.counter += 1
        item = Appointment(self.folder, f"item{self.folder.outlook.counter}")
        self.folder.appointments.append(item)
        return item


class Folders(Collection):
    def __init__(self, outlook: FakeOutlook) -> None:
        super().__init__([])
        self.outlook = outlook

    def Add(self, name: str, _kind: int) -> Folder:  # noqa: N802 - COM name
        folder = Folder(self.outlook, name)
        self.items.append(folder)
        return folder


class Folder:
    def __init__(self, outlook: FakeOutlook, name: str) -> None:
        self.outlook = outlook
        self.Name = name
        self.EntryID = f"folder:{name}"
        self.Folders = Folders(outlook)
        self.appointments: list[Appointment] = []
        self.Items = Items(self)

    def GetTable(self) -> Table:  # noqa: N802 - COM name
        return Table(self)


class Store:
    def __init__(self, outlook: FakeOutlook, name: str) -> None:
        self.DisplayName = name
        self.StoreID = f"store:{name}"
        self.calendar = Folder(outlook, "Calendrier")

    def GetDefaultFolder(self, kind: int) -> Folder:  # noqa: N802 - COM name
        assert kind == 9
        return self.calendar


class Account:
    def __init__(self, store: Store) -> None:
        self.DeliveryStore = store
        self.SmtpAddress = store.DisplayName


class ComError(Exception):
    pass


class FakeOutlook:
    def __init__(self, *names: str) -> None:
        self.counter = 0
        self.stores = [Store(self, name) for name in names]
        self.Stores = Collection(self.stores)
        self.Accounts = Collection([Account(store) for store in self.stores])
        self.Application = self
        self.TimeZones = Collection([])
        self.TimeZones.Item = lambda name: f"zone {name}"  # type: ignore[method-assign]

    def GetNamespace(self, _name: str) -> FakeOutlook:  # noqa: N802 - COM name
        return self

    def GetItemFromID(self, entry_id: str, _store_id: str) -> Appointment:  # noqa: N802
        for store in self.stores:
            for folder in store.calendar.Folders.items:
                for item in folder.appointments:
                    if item.EntryID == entry_id:
                        return item
        raise ComError(entry_id)

    def GetFolderFromID(self, entry_id: str, _store_id: str) -> Folder:  # noqa: N802
        for store in self.stores:
            for folder in store.calendar.Folders.items:
                if folder.EntryID == entry_id:
                    return folder
        raise ComError(entry_id)


@pytest.fixture
def outlook(monkeypatch: pytest.MonkeyPatch) -> FakeOutlook:
    monkeypatch.setattr("projectflow.outlook.calendar._com_error_type", lambda: ComError)
    monkeypatch.setattr("projectflow.outlook.calendar._require_timezones", lambda: None)
    return FakeOutlook(
        "lionel@balzmetal.ch",
        "Contact@balzmetalsa.onmicrosoft.com",
        "lionel@balzmetalsa.onmicrosoft.com",
    )


def spec(key: str = "task:t1:2026-11-10", subject: str = "2026-118 - Garde-corps") -> EventSpec:
    return EventSpec(
        key=key,
        subject=subject,
        start=datetime(2026, 11, 10, 6, 30),  # noqa: DTZ001 - wall time
        end=datetime(2026, 11, 10, 16, 30),  # noqa: DTZ001 - wall time
    )


@pytest.mark.asyncio
async def test_the_calendar_goes_in_the_mailbox_chosen(outlook: FakeOutlook) -> None:
    gateway = DesktopOutlookCalendarGateway(
        "LIONEL@balzmetalsa.onmicrosoft.com", app_factory=lambda: outlook
    )
    calendar = await gateway.ensure_calendar("Planning  Balz Métal")
    assert await gateway.ensure_calendar("planning balz métal") == calendar
    chosen = outlook.stores[2]
    assert [f.Name for f in chosen.calendar.Folders.items] == ["Planning Balz Métal"]
    assert all(not store.calendar.Folders.items for store in outlook.stores[:2])
    gateway.close()


@pytest.mark.asyncio
async def test_events_are_written_found_changed_and_removed(outlook: FakeOutlook) -> None:
    gateway = DesktopOutlookCalendarGateway(
        "lionel@balzmetalsa.onmicrosoft.com", app_factory=lambda: outlook
    )
    calendar = await gateway.ensure_calendar("Planning Balz Métal")
    first = await gateway.create_event(calendar, spec())
    folder = outlook.stores[2].calendar.Folders.items[0]
    folder.Items.Add(1).Subject = "Rendez-vous ajouté à la main"
    item = folder.appointments[0]
    assert (item.Subject, item.StartInStartTimeZone, item.ReminderSet) == (
        "2026-118 - Garde-corps",
        # Marked UTC so that pywin32 hands 06:30 over unchanged (Outlook reads it as Swiss).
        datetime(2026, 11, 10, 6, 30, tzinfo=UTC),
        False,
    )
    found = await gateway.list_events(calendar)
    assert list(found) == ["task:t1:2026-11-10"]
    assert found["task:t1:2026-11-10"].event_id == first
    assert found["task:t1:2026-11-10"].fingerprint == spec().fingerprint
    changed = spec(subject="2026-118 - Garde-corps (pose)")
    await gateway.update_event(first, changed)
    assert (await gateway.list_events(calendar))["task:t1:2026-11-10"].fingerprint == (
        changed.fingerprint
    )
    await gateway.delete_event(first)
    await gateway.delete_event(first)  # removed in Outlook meanwhile: nothing to do
    assert [i.Subject for i in folder.appointments] == ["Rendez-vous ajouté à la main"]
    with pytest.raises(OutlookError, match="supprimé dans Outlook"):
        await gateway.update_event(first, changed)
    gateway.close()


@pytest.mark.asyncio
async def test_an_unknown_mailbox_names_the_ones_there(outlook: FakeOutlook) -> None:
    gateway = DesktopOutlookCalendarGateway(
        "lionel@balzmetalsa.onmicrosoft.ch", app_factory=lambda: outlook
    )
    with pytest.raises(OutlookError, match=re.escape("lionel@balzmetalsa.onmicrosoft.com")):
        await gateway.ensure_calendar("Planning Balz Métal")
    gateway.close()


def test_the_desktop_route_needs_a_mailbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("projectflow.services.sys.platform", "win32")
    with pytest.raises(ConfigError, match="compte Outlook"):
        open_desktop_outlook_calendar("  ")
    monkeypatch.setattr("projectflow.services.sys.platform", "darwin")
    with pytest.raises(ConfigError, match="Windows"):
        open_desktop_outlook_calendar("lionel@balzmetalsa.onmicrosoft.com")


@pytest.mark.asyncio
async def test_a_build_without_win32timezone_says_so_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = importlib.import_module

    def missing(name: str, package: str | None = None) -> Any:
        if name == "win32timezone":
            raise ModuleNotFoundError(name)
        return real(name, package)

    monkeypatch.setattr("projectflow.outlook.calendar.importlib.import_module", missing)
    outlook = FakeOutlook("lionel@balzmetalsa.onmicrosoft.com")
    gateway = DesktopOutlookCalendarGateway(
        "lionel@balzmetalsa.onmicrosoft.com", app_factory=lambda: outlook
    )
    with pytest.raises(OutlookError, match="win32timezone"):
        await gateway.ensure_calendar("Planning Balz Métal")
    gateway.close()


@pytest.mark.asyncio
async def test_an_absence_stays_on_its_days(outlook: FakeOutlook) -> None:
    gateway = DesktopOutlookCalendarGateway(
        "lionel@balzmetalsa.onmicrosoft.com", app_factory=lambda: outlook
    )
    calendar = await gateway.ensure_calendar("Planning Balz Métal")
    absence = EventSpec(
        key="absence:a1:2026-11-11",
        subject="Absent : Karim (Vacances)",
        start=datetime(2026, 11, 11),  # noqa: DTZ001 - wall time
        end=datetime(2026, 11, 13),  # noqa: DTZ001 - wall time
        all_day=True,
    )
    await gateway.create_event(calendar, absence)
    item = outlook.stores[2].calendar.Folders.items[0].appointments[0]
    assert (item.AllDayEvent, item.Start, item.End) == (
        True,
        datetime(2026, 11, 11, tzinfo=UTC),
        datetime(2026, 11, 13, tzinfo=UTC),
    )
    gateway.close()


@pytest.mark.asyncio
async def test_events_written_an_hour_early_are_written_again(outlook: FakeOutlook) -> None:
    gateway = DesktopOutlookCalendarGateway(
        "lionel@balzmetalsa.onmicrosoft.com", app_factory=lambda: outlook
    )
    calendar = await gateway.ensure_calendar("Planning Balz Métal")
    await gateway.create_event(calendar, spec())
    item = outlook.stores[2].calendar.Folders.items[0].appointments[0]
    fingerprint = item.UserProperties.Find("ProjectFlowFingerprint")
    assert fingerprint is not None
    fingerprint.Value = spec().fingerprint  # as 0.1.71 and 0.1.72 wrote it
    found = await gateway.list_events(calendar)
    assert found["task:t1:2026-11-10"].fingerprint == ""
    gateway.close()
