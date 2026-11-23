"""History of the alerts shown on this poste (« zone de notification » of the planning).

Kept beside the planning cache, never shared: what one poste has read or put off says
nothing about another. The file also remembers when the poste last looked for alerts,
so that the ones that came due while it was off are told at the next start and none
rings twice.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from projectflow.core.planning.alerts import Alert
from projectflow.core.planning.walltime import MINUTES_PER_DAY, WallTime
from projectflow.logging import get_logger

LOG_FILE = "alerts.json"
MAX_ENTRIES = 200
KEEP_DAYS = 60


@dataclass(frozen=True, slots=True)
class LoggedAlert:
    key: str
    task_id: str
    due: WallTime
    title: str
    body: str
    missed: bool = False
    read: bool = False
    snoozed_until: WallTime | None = None
    """Put off (« Reporter »): the alert rings again at this time."""
    day: int = 0
    """Summary only: ordinal of the day it tells."""

    @property
    def snoozed(self) -> bool:
        return self.snoozed_until is not None


class AlertLog:
    """Alerts of this poste, newest first; saved at every change when it has a file."""

    def __init__(self, directory: Path | None = None) -> None:
        self._path = directory / LOG_FILE if directory is not None else None
        self._entries: dict[str, LoggedAlert] = {}
        self.last_check: WallTime | None = None
        """When the poste last looked for alerts; None before the first time."""
        self._load()

    # -- reading ----------------------------------------------------------------

    @property
    def entries(self) -> list[LoggedAlert]:
        return sorted(self._entries.values(), key=lambda entry: (-entry.due, entry.key))

    @property
    def unread_count(self) -> int:
        return sum(1 for entry in self._entries.values() if not entry.read)

    def __contains__(self, key: object) -> bool:
        return key in self._entries

    def get(self, key: str) -> LoggedAlert | None:
        return self._entries.get(key)

    # -- changes ----------------------------------------------------------------

    def record(self, alerts: Iterable[Alert], *, now: WallTime) -> list[LoggedAlert]:
        """Add the alerts not held yet and note that the poste looked at ``now``."""
        added: list[LoggedAlert] = []
        for alert in alerts:
            if alert.key in self._entries:
                continue
            entry = LoggedAlert(
                key=alert.key,
                task_id=alert.task_id,
                due=alert.due,
                title=alert.title,
                body=alert.body,
                missed=alert.missed,
                day=alert.day,
            )
            self._entries[entry.key] = entry
            added.append(entry)
        changed = bool(added) or self.last_check != now
        self.last_check = now
        if changed:
            self._prune(now)
            self._save()
        return added

    def wake(self, now: WallTime) -> list[LoggedAlert]:
        """Alerts put off whose time has come: they are unread again and returned."""
        woken: list[LoggedAlert] = []
        for entry in list(self._entries.values()):
            if entry.snoozed_until is not None and entry.snoozed_until <= now:
                fresh = replace(entry, snoozed_until=None, read=False)
                self._entries[entry.key] = fresh
                woken.append(fresh)
        if woken:
            self._save()
        return woken

    def mark_read(self, key: str) -> None:
        self._update(key, read=True)

    def mark_all_read(self) -> None:
        unread = [entry for entry in self._entries.values() if not entry.read]
        for entry in unread:
            self._entries[entry.key] = replace(entry, read=True)
        if unread:
            self._save()

    def snooze(self, key: str, until: WallTime) -> None:
        self._update(key, read=True, snoozed_until=until)

    def remove(self, key: str) -> None:
        if self._entries.pop(key, None) is not None:
            self._save()

    def clear(self) -> None:
        if self._entries:
            self._entries = {}
            self._save()

    def _update(self, key: str, **fields: Any) -> None:  # noqa: ANN401 - dataclass fields
        entry = self._entries.get(key)
        if entry is None:
            return
        fresh = replace(entry, **fields)
        if fresh != entry:
            self._entries[key] = fresh
            self._save()

    def _prune(self, now: WallTime) -> None:
        floor = now - KEEP_DAYS * MINUTES_PER_DAY
        kept = [
            entry for entry in self.entries if entry.due >= floor or entry.snoozed_until is not None
        ]
        self._entries = {entry.key: entry for entry in kept[:MAX_ENTRIES]}

    # -- file -------------------------------------------------------------------

    def _load(self) -> None:
        path = self._path
        if path is None or not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            last = data.get("last_check")
            self.last_check = int(last) if last is not None else None
            for raw in data.get("entries", []):
                entry = LoggedAlert(**raw)
                self._entries[entry.key] = entry
        except (OSError, ValueError, TypeError, AttributeError) as error:
            # An unreadable history is started again: the planning itself is untouched.
            get_logger(__name__).warning("planning.alerts.log_unreadable", error=str(error))
            self._entries = {}
            self.last_check = None

    def _save(self) -> None:
        path = self._path
        if path is None:
            return
        data = {
            "last_check": self.last_check,
            "entries": [asdict(entry) for entry in self.entries],
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        except OSError as error:
            get_logger(__name__).warning("planning.alerts.log_not_saved", error=str(error))
