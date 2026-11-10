"""Items dragged out of the classic Outlook of the poste (Windows), read through COM.

What Outlook hands over in a drag (a ``.msg`` in memory) is of no use here; the items
dragged are those selected in its window, read after the drop (during the drag Outlook
waits for it and answers nothing). An appointment keeps its times, an Outlook task its
dates, a mail its subject and text, to be planned later.
"""

from __future__ import annotations

import importlib
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Final

from projectflow.core.planning.calendar_import import ImportedEvent
from projectflow.exceptions import OutlookError
from projectflow.outlook.windows import _com_error_type, _dispatch_outlook

OutlookAppFactory = Callable[[], Any]

OL_APPOINTMENT: Final = 26
OL_TASK: Final = 48
OL_MEETING_ITEMS: Final = frozenset({53, 54, 55, 56, 57})
NO_DATE_YEAR: Final = 4501
"""Outlook writes « none » as 1 January 4501."""
MAX_ITEMS: Final = 200
RETRIES: Final = 5
RETRY_DELAY_S: Final = 0.3
"""Outlook may still be ending its drag: it then refuses a call for a moment."""


def read_outlook_selection(
    *, app_factory: OutlookAppFactory | None = None, sleep: Callable[[float], None] = time.sleep
) -> list[ImportedEvent]:
    """Items selected in the active Outlook window, as appointments to import.

    Runs in a thread of its own (it initialises COM there).
    """
    pythoncom = _pythoncom()
    if pythoncom is not None:
        pythoncom.CoInitialize()
    try:
        error: BaseException | None = None
        for attempt in range(RETRIES):
            try:
                return _read(app_factory or _dispatch_outlook)
            except _com_error_type() as exc:
                error = exc
                if attempt + 1 < RETRIES:
                    sleep(RETRY_DELAY_S)
        raise OutlookError(f"Outlook n'a pas donné les éléments glissés ({error}).") from error
    finally:
        if pythoncom is not None:
            pythoncom.CoUninitialize()


def _read(app_factory: OutlookAppFactory) -> list[ImportedEvent]:
    explorer = app_factory().ActiveExplorer()
    if explorer is None:
        raise OutlookError("aucune fenêtre Outlook ouverte.")
    selection = explorer.Selection
    events: list[ImportedEvent] = []
    for index in range(1, min(int(selection.Count), MAX_ITEMS) + 1):
        event = item_event(selection.Item(index))
        if event is not None:
            events.append(event)
    return events


def item_event(item: Any) -> ImportedEvent | None:  # noqa: ANN401 - COM object
    """``ImportedEvent`` of one Outlook item; None for what holds nothing to plan."""
    kind = int(getattr(item, "Class", 0) or 0)
    if kind in OL_MEETING_ITEMS:
        appointment = _associated_appointment(item)
        if appointment is not None:
            item, kind = appointment, OL_APPOINTMENT
    subject = _text(getattr(item, "Subject", ""))
    notes = _text(getattr(item, "Body", ""))
    if kind == OL_APPOINTMENT:
        start = _wall(item.Start)
        end = _wall(item.End)
        return ImportedEvent(
            subject=subject,
            start=start,
            end=end,
            all_day=bool(item.AllDayEvent),
            location=_text(getattr(item, "Location", "")),
            notes=notes,
        )
    if kind == OL_TASK:
        # An Outlook task holds days: from its start to its due date, whole days.
        start = _wall(item.StartDate)
        due = _wall(item.DueDate)
        first, last = start or due, due or start
        if first is None or last is None:
            return ImportedEvent(subject=subject or "Tâche Outlook", notes=notes)
        first, last = min(first, last), max(first, last)
        return ImportedEvent(
            subject=subject or "Tâche Outlook",
            start=first.replace(hour=0, minute=0),
            end=last.replace(hour=0, minute=0) + timedelta(days=1),
            all_day=True,
            notes=notes,
        )
    if not subject and not notes:
        return None
    return ImportedEvent(subject=subject or "Message Outlook", notes=notes)


def _associated_appointment(item: Any) -> Any | None:  # noqa: ANN401 - COM object
    try:
        return item.GetAssociatedAppointment(False)  # noqa: FBT003 - COM argument
    except (_com_error_type(), AttributeError):
        return None


def _wall(value: object) -> datetime | None:
    """Local wall time of an Outlook date (pywin32 labels it UTC, it is local)."""
    if not isinstance(value, datetime) or value.year >= NO_DATE_YEAR:
        return None
    return datetime(  # noqa: DTZ001 - local wall time of the planning
        value.year, value.month, value.day, value.hour, value.minute
    )


def _text(value: object) -> str:
    return str(value or "").strip()


def _pythoncom() -> Any | None:  # noqa: ANN401 - module
    try:
        return importlib.import_module("pythoncom")
    except ImportError:
        return None
