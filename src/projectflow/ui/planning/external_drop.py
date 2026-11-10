"""What is dragged onto the planning from outside: calendar files, calendar text, Outlook.

``external_drop`` tells, while the drag passes, whether it can be imported, and reads it
at the drop: ``.ics`` files (from the Explorer, the Finder, a mail), calendar text (what
some calendars drag, as Apple Calendar may), items of the classic Outlook (read through
COM after the drop, ``outlook_items``). Such a drop is always a copy: a move would ask
Outlook or the Explorer to delete what was dragged.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from PySide6.QtCore import QMimeData

from projectflow.core.planning.calendar_import import ImportedEvent, is_calendar_text, parse_ics
from projectflow.logging import get_logger

TASK_MIME: Final = "application/x-projectflow-task"
"""``grid.TASK_MIME``: a task of the planning itself is never an import."""

CALENDAR_SUFFIXES: Final = frozenset({".ics", ".ical", ".icalendar", ".ifb", ".vcs"})
MAX_FILE_BYTES: Final = 10 * 1024 * 1024
OUTLOOK_FORMATS: Final = ("renprivatemessages", "renprivateitem", "renprivatesourcefolder")
DESCRIPTOR_FORMAT: Final = "filegroupdescriptorw"
DESCRIPTOR_SIZE: Final = 592
"""``FILEDESCRIPTORW``: its name, 260 UTF-16 characters, starts at byte 72."""
DESCRIPTOR_NAME_OFFSET: Final = 72
CALENDAR_FORMAT_WORDS: Final = ("calendar", "ical", "ics", "vcs")


@dataclass(frozen=True, slots=True)
class ExternalDrop:
    """Something dragged in from another application."""

    events: tuple[ImportedEvent, ...] = ()
    """Read at the drop (files, calendar text)."""
    outlook_items: int = 0
    """Items of the classic Outlook, read from its selection once the drop is over."""
    source: str = "calendrier"

    @property
    def from_outlook(self) -> bool:
        return self.outlook_items > 0

    @property
    def count(self) -> int:
        return self.outlook_items or len(self.events)


def external_drop(mime: QMimeData | None, *, read: bool = False) -> ExternalDrop | None:
    """Tell what ``mime`` brings that can be imported; None when nothing (or a task).

    Without ``read`` only the kind is told (while the drag passes): files are not opened
    and ``events`` stays empty.
    """
    if mime is None or mime.hasFormat(TASK_MIME):
        return None
    files = calendar_files(mime)
    if files:
        if not read:
            return ExternalDrop(events=(), outlook_items=0, source="fichier")
        events = [event for path in files for event in _file_events(path)]
        return ExternalDrop(events=tuple(events), source="fichier")
    outlook = outlook_item_count(mime)
    if outlook:
        return ExternalDrop(outlook_items=outlook, source="Outlook")
    text = calendar_text(mime)
    if text:
        return ExternalDrop(events=tuple(parse_ics(text)) if read else ())
    return None


def calendar_files(mime: QMimeData) -> list[Path]:
    if not mime.hasUrls():
        return []
    return [
        Path(url.toLocalFile())
        for url in mime.urls()
        if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in CALENDAR_SUFFIXES
    ]


def outlook_item_count(mime: QMimeData) -> int:
    """Count the Outlook items dragged (1 at least when Outlook is the source), else 0."""
    formats = [(name, name.lower()) for name in mime.formats()]
    descriptor = next((name for name, low in formats if DESCRIPTOR_FORMAT in low), None)
    names = descriptor_names(bytes(mime.data(descriptor).data())) if descriptor else []
    messages = [name for name in names if name.lower().endswith(".msg")]
    if any(word in low for _name, low in formats for word in OUTLOOK_FORMATS):
        return max(len(messages), 1)
    return len(messages)


def descriptor_names(data: bytes) -> list[str]:
    """File names of a Windows ``FileGroupDescriptorW`` (what Outlook drags)."""
    if len(data) < 4:  # noqa: PLR2004 - the count
        return []
    (count,) = struct.unpack_from("<I", data)
    names: list[str] = []
    for index in range(count):
        begin = 4 + index * DESCRIPTOR_SIZE + DESCRIPTOR_NAME_OFFSET
        raw = data[begin : begin + 520]
        if len(raw) < 2:  # noqa: PLR2004 - one UTF-16 character
            break
        names.append(raw.decode("utf-16-le", "replace").split("\x00", 1)[0])
    return names


def calendar_text(mime: QMimeData) -> str:
    """Calendar text carried by ``mime`` (``text/calendar``, a calendar's own format)."""
    candidates = [
        name for name in mime.formats() if any(w in name.lower() for w in CALENDAR_FORMAT_WORDS)
    ]
    for name in candidates:
        text = _decoded(bytes(mime.data(name).data()))
        if is_calendar_text(text):
            return text
    if mime.hasText() and is_calendar_text(mime.text()):
        return mime.text()
    return ""


def _decoded(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    return data.decode("utf-8-sig", "replace")


def _file_events(path: Path) -> list[ImportedEvent]:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            get_logger(__name__).warning("planning.import.file_too_large")
            return []
        return parse_ics(_decoded(path.read_bytes()))
    except OSError:
        get_logger(__name__).warning("planning.import.file_unreadable")
        return []


def unknown_formats(mime: QMimeData | None) -> list[str]:
    """List the formats of a drag refused, for the log (to support a new calendar)."""
    if mime is None or mime.hasFormat(TASK_MIME):
        return []
    return list(mime.formats())[:20]
