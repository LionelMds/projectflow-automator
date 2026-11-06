"""Outlook calendar of one mailbox of this poste's Outlook (Windows, classic Outlook).

The other way to ``graph.outlook_calendar``: no Microsoft permission to grant, the
calendar is written through the Outlook open on this poste, in the mailbox chosen
(Outlook often holds several). Exchange then carries it like any calendar of that
mailbox, so it can be published from Outlook on the web for the phones.

Outlook is driven through COM from one thread of its own: its objects belong to the
thread that made them. The key and the fingerprint of each event are user properties
of the appointment; an appointment added by hand in that calendar has none, and is
never touched.
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Final, TypeVar

from projectflow.core.planning.outlook_sync import EventSpec, ExistingEvent
from projectflow.exceptions import ConfigError, OutlookError
from projectflow.outlook.windows import (
    _account_email_by_store_id,
    _clean_text,
    _com_error_type,
    _dispatch_outlook,
)

OutlookAppFactory = Callable[[], Any]
T = TypeVar("T")

OL_FOLDER_CALENDAR: Final = 9
OL_APPOINTMENT_ITEM: Final = 1
OL_TEXT: Final = 1
OL_BUSY: Final = 2
OL_TENTATIVE: Final = 1
KEY_PROPERTY: Final = "ProjectFlowKey"
FINGERPRINT_PROPERTY: Final = "ProjectFlowFingerprint"
TIME_ZONE: Final = "W. Europe Standard Time"
"""Swiss time, as Windows names it: the planning is in wall time of the workshop."""
_PUBLIC_STRINGS: Final = (
    "http://schemas.microsoft.com/mapi/string/{00020329-0000-0000-C000-000000000046}"
)
_KEY_COLUMN: Final = f"{_PUBLIC_STRINGS}/{KEY_PROPERTY}/0x0000001f"
_FINGERPRINT_COLUMN: Final = f"{_PUBLIC_STRINGS}/{FINGERPRINT_PROPERTY}/0x0000001f"
NO_MAILBOX_MESSAGE: Final = (
    "choisissez le compte Outlook du calendrier : Paramètres > Planning > Calendrier Outlook."
)


class DesktopOutlookCalendarGateway:
    """``CalendarGateway`` writing in a calendar of ``mailbox`` through local Outlook."""

    def __init__(self, mailbox: str, *, app_factory: OutlookAppFactory | None = None) -> None:
        self._mailbox = mailbox.strip()
        self._app_factory = app_factory or _dispatch_outlook
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="outlook-calendar", initializer=_com_initialize
        )
        self._namespace: Any = None
        self._store_id = ""
        self._folders: dict[str, Any] = {}

    @property
    def mailbox(self) -> str:
        return self._mailbox

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def _run(self, work: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, work)
        except _com_error_type() as exc:
            self._namespace = None  # Outlook closed or restarted: reconnect next time.
            self._folders.clear()
            raise OutlookError(f"Outlook a refusé l'opération sur le calendrier ({exc}).") from exc

    # ---------------------------------------------------------- the gateway ----

    async def ensure_calendar(self, name: str) -> str:
        return await self._run(lambda: self._ensure_calendar(name))

    async def list_events(self, calendar_id: str) -> dict[str, ExistingEvent]:
        return await self._run(lambda: self._list_events(calendar_id))

    async def create_event(self, calendar_id: str, spec: EventSpec) -> str:
        return await self._run(lambda: self._create_event(calendar_id, spec))

    async def update_event(self, event_id: str, spec: EventSpec) -> None:
        await self._run(lambda: self._update_event(event_id, spec))

    async def delete_event(self, event_id: str) -> None:
        await self._run(lambda: self._delete_event(event_id))

    # ------------------------------------------------- in the Outlook thread ----

    def _mapi(self) -> Any:
        if self._namespace is None:
            _require_timezones()
            self._namespace = self._app_factory().GetNamespace("MAPI")
        return self._namespace

    def _store(self) -> Any:
        if not self._mailbox:
            raise ConfigError(NO_MAILBOX_MESSAGE)
        namespace = self._mapi()
        emails = _account_email_by_store_id(namespace)
        wanted = self._mailbox.casefold()
        stores = namespace.Stores
        names: list[str] = []
        for index in range(1, int(stores.Count) + 1):
            store = stores.Item(index)
            store_id = _clean_text(store.StoreID)
            display = _clean_text(store.DisplayName)
            email = emails.get(store_id, "")
            if wanted in {display.casefold(), email.casefold()}:
                self._store_id = store_id
                return store
            if email:
                names.append(email)
        known = ", ".join(sorted(set(names), key=str.casefold)) or "aucun"
        raise OutlookError(
            f"compte « {self._mailbox} » introuvable dans l'Outlook de ce poste "
            f"(comptes : {known})."
        )

    def _ensure_calendar(self, name: str) -> str:
        title = " ".join(name.split())
        root = self._store().GetDefaultFolder(OL_FOLDER_CALENDAR)
        folders = root.Folders
        for index in range(1, int(folders.Count) + 1):
            folder = folders.Item(index)
            if _clean_text(folder.Name).casefold() == title.casefold():
                break
        else:
            folder = folders.Add(title, OL_FOLDER_CALENDAR)
        calendar_id = _clean_text(folder.EntryID)
        self._folders[calendar_id] = folder
        return calendar_id

    def _folder(self, calendar_id: str) -> Any:
        folder = self._folders.get(calendar_id)
        if folder is None:
            if not self._store_id:
                self._store()
            folder = self._mapi().GetFolderFromID(calendar_id, self._store_id)
            self._folders[calendar_id] = folder
        return folder

    def _list_events(self, calendar_id: str) -> dict[str, ExistingEvent]:
        table = self._folder(calendar_id).GetTable()
        columns = table.Columns
        columns.RemoveAll()
        for column in ("EntryID", _KEY_COLUMN, _FINGERPRINT_COLUMN):
            columns.Add(column)
        found: dict[str, ExistingEvent] = {}
        while not table.EndOfTable:
            row = table.GetNextRow()
            key = _clean_text(row.Item(2))
            if key:
                found[key] = ExistingEvent(_clean_text(row.Item(1)), _clean_text(row.Item(3)))
        return found

    def _create_event(self, calendar_id: str, spec: EventSpec) -> str:
        item = self._folder(calendar_id).Items.Add(OL_APPOINTMENT_ITEM)
        _set_property(item, KEY_PROPERTY, spec.key)
        self._fill(item, spec)
        return _clean_text(item.EntryID)

    def _update_event(self, event_id: str, spec: EventSpec) -> None:
        item = self._item(event_id)
        if item is None:
            raise OutlookError(f"rendez-vous « {spec.subject} » supprimé dans Outlook.")
        self._fill(item, spec)

    def _delete_event(self, event_id: str) -> None:
        item = self._item(event_id)
        if item is not None:
            item.Delete()

    def _item(self, event_id: str) -> Any | None:
        if not self._store_id:
            self._store()
        try:
            return self._mapi().GetItemFromID(event_id, self._store_id)
        except _com_error_type():
            return None  # Removed in Outlook in the meantime.

    def _fill(self, item: Any, spec: EventSpec) -> None:
        item.Subject = spec.subject
        item.Body = spec.body
        item.Location = spec.location
        # Outlook snaps the times of a day event: say « all day » before the dates.
        item.AllDayEvent = spec.all_day
        if spec.all_day:
            item.Start = spec.start
            item.End = spec.end
        else:
            zone = self._mapi().Application.TimeZones.Item(TIME_ZONE)
            item.StartTimeZone = zone
            item.EndTimeZone = zone
            item.StartInStartTimeZone = spec.start
            item.EndInEndTimeZone = spec.end
        item.BusyStatus = OL_TENTATIVE if spec.tentative else OL_BUSY
        item.ReminderSet = False
        _set_property(item, FINGERPRINT_PROPERTY, spec.fingerprint)
        item.Save()


def _set_property(item: Any, name: str, value: str) -> None:
    prop = item.UserProperties.Find(name)
    if prop is None:
        prop = item.UserProperties.Add(name, OL_TEXT, False)  # noqa: FBT003 - COM argument
    prop.Value = value


def _com_initialize() -> None:
    try:
        pythoncom = importlib.import_module("pythoncom")
    except ImportError:
        return
    pythoncom.CoInitialize()


def _require_timezones() -> None:
    """pywin32 imports ``win32timezone`` the first time it hands a date to Outlook.

    Checked once, at the start: a build that left it out fails here with one clear
    message, not on every appointment.
    """
    try:
        importlib.import_module("win32timezone")
    except ImportError as exc:
        raise OutlookError(
            "module win32timezone absent de cette version : mettez ProjectFlow à jour."
        ) from exc
