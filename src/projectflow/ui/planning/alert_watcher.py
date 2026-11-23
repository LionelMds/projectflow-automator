"""Rings the alerts of the planning on this poste and keeps their history.

Every ``CHECK_MS`` the watcher asks ``alerts.due_alerts`` what came due since it last
looked. A new alert goes to the ``AlertLog``, to a toast of the Planning tab with
« Ouvrir » and, through ``notify``, to the notification centre of Windows or macOS. The
alerts that came due while the poste was off are told at once, in one message. The
bell of the toolbar counts what is unread; its panel opens a task, puts an alert off or
removes it.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import date
from functools import partial
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from projectflow.core.planning.alert_log import AlertLog, LoggedAlert
from projectflow.core.planning.alerts import (
    AlertRules,
    digest_alerts,
    due_alerts,
    my_resource_id,
)
from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.walltime import (
    MINUTES_PER_DAY,
    MINUTES_PER_HOUR,
    WallTime,
    day_start,
    wall_date,
)
from projectflow.ui.planning.alert_panel import SNOOZE_TOMORROW
from projectflow.ui.planning.planning_tab import PlanningTab

CHECK_MS = 20_000
ALERT_TOAST_MS = 15_000
MAX_SHOWN = 3
"""Alerts due at the same minute shown one by one; beyond, one message counts them."""
MORNING_MINUTES = 6 * MINUTES_PER_HOUR + 30
"""« Demain matin »: the start of the working day."""
REOPEN_GUARD_SECONDS = 0.25
"""A click on the bell that just closed the panel does not open it again."""
GONE_MESSAGE = "Cette tâche n'est plus dans le planning."
APP_TITLE = "ProjectFlow · Planning"

Notify = Callable[[str, str], None]


def missed_text(count: int) -> str:
    if count == 1:
        return "1 alerte manquée pendant que ProjectFlow était fermé"
    return f"{count} alertes manquées pendant que ProjectFlow était fermé"


class AlertWatcher(QObject):
    """See the module docstring. The Planning controller gives it what it needs to read."""

    def __init__(
        self,
        tab: PlanningTab,
        *,
        snapshot: Callable[[], PlanningSnapshot | None],
        identity: Callable[[], tuple[str, str]],
        now: Callable[[], WallTime],
        open_task: Callable[[str], bool],
        notify: Callable[[], Notify | None],
        rules: Callable[[], AlertRules] = AlertRules,
        open_day: Callable[[date], None] | None = None,
    ) -> None:
        super().__init__(tab)
        self._tab = tab
        self._snapshot = snapshot
        self._identity = identity
        self._now = now
        self._open_task = open_task
        self._notify = notify
        self._rules = rules
        self._open_day = open_day
        self._log = AlertLog()
        self._closed_at = float("-inf")
        self._timer = QTimer(self)
        self._timer.setInterval(CHECK_MS)
        self._timer.timeout.connect(self.check)
        tab.bell.clicked.connect(self.toggle_panel)
        panel = tab.alert_panel
        panel.open_requested.connect(self.open_alert)
        panel.snooze_requested.connect(self.snooze)
        panel.remove_requested.connect(self.remove)
        panel.read_all_requested.connect(self.mark_all_read)
        panel.clear_requested.connect(self.clear)
        panel.closed.connect(self._on_panel_closed)
        self._refresh()

    # -- contract ---------------------------------------------------------------

    @property
    def log(self) -> AlertLog:
        return self._log

    def open_log(self, directory: Path | None) -> None:
        """Use the history kept in ``directory`` (the cache folder of the store in use)."""
        self._log = AlertLog(directory)
        self._refresh()

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._tab.alert_panel.hide()

    def check(self) -> list[LoggedAlert]:
        """Ring what came due since the last look; returns the alerts told now."""
        snapshot = self._snapshot()
        if snapshot is None:
            return []
        now = self._now()
        log = self._log
        # The first look ever tells nothing of the past: only what is due from now on.
        since = log.last_check if log.last_check is not None else now - 1
        rules = self._rules()
        if not rules.enabled:
            # Nothing piles up while the alerts are off: switched on again, the poste
            # tells what is due from then on.
            log.record((), now=now)
            return []
        initials, m365_id = self._identity()
        resource_id = my_resource_id(snapshot, initials=initials, m365_id=m365_id)
        if rules.audience == "all":
            initials = resource_id = ""
        alerts = [
            *due_alerts(
                snapshot,
                now=now,
                since=since,
                known=log,
                resource_id=resource_id,
                initials=initials,
            ),
            *digest_alerts(
                snapshot,
                now=now,
                rules=rules,
                known=log,
                resource_id=resource_id,
                initials=initials,
            ),
        ]
        told = [*log.record(alerts, now=now), *log.wake(now)]
        if told:
            # In the hours of silence the alerts wait in the history, unread.
            if not rules.is_quiet(now):
                self._announce(told, rules)
            self._refresh()
        return told

    def show_panel(self) -> None:
        self._refresh()
        self._tab.alert_panel.open_under(self._tab.bell)

    def toggle_panel(self) -> None:
        panel = self._tab.alert_panel
        if panel.isVisible():
            panel.hide()
        elif time.monotonic() - self._closed_at > REOPEN_GUARD_SECONDS:
            self.show_panel()

    def open_alert(self, key: str) -> None:
        entry = self._log.get(key)
        if entry is None:
            return
        self._log.mark_read(key)
        self._tab.alert_panel.hide()
        self._refresh()
        if not entry.task_id:
            if entry.day and self._open_day is not None:
                self._open_day(date.fromordinal(entry.day))
            return
        if not self._open_task(entry.task_id):
            self._tab.show_toast(GONE_MESSAGE)

    def snooze(self, key: str, minutes: int) -> None:
        now = self._now()
        if minutes == SNOOZE_TOMORROW:
            until = day_start(wall_date(now)) + MINUTES_PER_DAY + MORNING_MINUTES
        else:
            until = now + max(minutes, 1)
        self._log.snooze(key, until)
        self._refresh()

    def remove(self, key: str) -> None:
        self._log.remove(key)
        self._refresh()

    def mark_all_read(self) -> None:
        self._log.mark_all_read()
        self._refresh()

    def clear(self) -> None:
        self._log.clear()
        self._refresh()

    # -- internals --------------------------------------------------------------

    def _announce(self, told: Sequence[LoggedAlert], rules: AlertRules) -> None:
        fresh = [entry for entry in told if not entry.missed]
        missed = [entry for entry in told if entry.missed]
        notify = self._notify() if rules.system else None
        if rules.sound:
            QApplication.beep()
        for entry in fresh[:MAX_SHOWN]:
            self._tab.show_toast(
                f"{entry.title} — {entry.body}",
                action_text="Ouvrir",
                action=partial(self.open_alert, entry.key),
                timeout_ms=ALERT_TOAST_MS,
            )
            if notify is not None:
                notify(entry.title, entry.body)
        rest = len(fresh) - MAX_SHOWN
        if rest > 0:
            text = f"{rest} autre(s) alerte(s) à l'instant"
            self._tab.show_toast(text, action_text="Voir", action=self.show_panel)
            if notify is not None:
                notify(APP_TITLE, text)
        if missed:
            text = missed_text(len(missed))
            self._tab.show_toast(
                text, action_text="Voir", action=self.show_panel, timeout_ms=ALERT_TOAST_MS
            )
            if notify is not None:
                notify(APP_TITLE, text if len(missed) > 1 else f"{missed[0].title} — manquée")

    def _refresh(self) -> None:
        self._tab.bell.set_count(self._log.unread_count)
        self._tab.alert_panel.set_entries(self._log.entries, today=wall_date(self._now()))

    def _on_panel_closed(self) -> None:
        self._closed_at = time.monotonic()
