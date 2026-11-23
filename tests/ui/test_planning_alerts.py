"""Planning: « Alerte notification » in the Détail, the bell and its history, the watcher."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from test_planning_controller import (  # type: ignore[import-not-found]
    TODAY,
    close,
    data_dirs,  # noqa: F401 - fixture
    make_config,
    make_controller,
    seed_store,
    snapshot_of,
    started,
    toast_texts,
)
from test_planning_detail import (  # type: ignore[import-not-found]
    MONDAY,
    OTHER,
    _click,
    _detail,
)

from projectflow.core.planning.alert_log import LOG_FILE, AlertLog, LoggedAlert
from projectflow.core.planning.alerts import AlertRules
from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.models import MAX_REMINDERS, PlanningTask, Reminder, Resource
from projectflow.core.planning.walltime import MINUTES_PER_DAY, wall_time
from projectflow.ui.planning.alert_editor import ADD_TEXT, FULL_HINT, NO_DATE_HINT, AlertEditor
from projectflow.ui.planning.alert_panel import (
    EMPTY_TEXT,
    SNOOZE_TOMORROW,
    AlertPanel,
    BellButton,
    day_heading,
    state_text,
)
from projectflow.ui.planning.alert_watcher import GONE_MESSAGE, AlertWatcher
from projectflow.ui.planning.planning_tab import PlanningTab

HALF_HOUR = Reminder(minutes=30)
START = wall_time(MONDAY, 8, 0)


# ---------------------------------------------------------------- détail ----


def test_the_detail_shows_the_alerts_of_the_task_under_the_night_switches(qtbot: Any) -> None:
    task = OTHER.model_copy(update={"reminders": (HALF_HOUR, Reminder(kind="at", at=START))})
    host = _detail(qtbot, task)
    detail = host.detail
    editor = detail.alert_block

    assert editor.caption.text() == "Alerte notification"
    assert editor.chip_texts() == ["30 min avant  ×", "lun 19.10 à 08:00  ×"]  # noqa: RUF001
    assert editor.add_button.text() == ADD_TEXT
    assert editor.isVisible()
    assert (
        editor.mapTo(detail, editor.rect().topLeft()).y()
        > detail.off_standard_button.mapTo(detail, detail.off_standard_button.rect().topLeft()).y()
    )


def test_adding_and_removing_an_alert_asks_for_the_change(qtbot: Any) -> None:
    host = _detail(qtbot, OTHER)
    detail = host.detail
    editor = detail.alert_block

    with qtbot.waitSignal(detail.field_changed) as added:
        next(action for action in editor.menu.actions() if action.text() == "1 h avant").trigger()
    assert added.args == ["other", "reminders", (Reminder(minutes=60),)]
    assert editor.chip_texts() == ["1 h avant  ×"]  # noqa: RUF001

    editor.add_before(60)
    assert editor.reminders() == (Reminder(minutes=60),)

    with qtbot.waitSignal(detail.field_changed) as removed:
        _click(qtbot, editor._chips[0])  # noqa: SLF001
    assert removed.args == ["other", "reminders", ()]
    assert editor.chip_texts() == []


def test_an_alert_at_a_moment_is_picked_on_the_rolls(qtbot: Any) -> None:
    host = _detail(qtbot, OTHER)
    detail = host.detail
    editor = detail.alert_block
    assert not editor.moment_open()

    editor.moment_action.trigger()

    assert editor.moment_open()
    assert editor.picker.moment() == OTHER.start - 60
    editor.picker.set_moment(wall_time(MONDAY, 5, 30), today=MONDAY)
    with qtbot.waitSignal(detail.field_changed) as blocker:
        _click(qtbot, editor.confirm_button)
    assert blocker.args == [
        "other",
        "reminders",
        (Reminder(kind="at", at=wall_time(MONDAY, 5, 30)),),
    ]
    assert not editor.moment_open()

    editor.open_moment()
    _click(qtbot, editor.cancel_button)
    assert not editor.moment_open()
    assert len(editor.reminders()) == 1


def test_the_editor_tells_its_limits(qtbot: Any) -> None:
    editor = AlertEditor()
    qtbot.addWidget(editor)
    editor.show()

    editor.set_reminders((HALF_HOUR,), start=None, today=MONDAY)
    assert editor.hint_label.text() == NO_DATE_HINT

    full = tuple(Reminder(minutes=minutes) for minutes in range(MAX_REMINDERS))
    editor.set_reminders(full, start=START, today=MONDAY)
    assert editor.hint_label.text() == FULL_HINT
    assert not editor.add_button.isEnabled()
    editor.add_before(999)
    assert editor.reminders() == full

    editor.set_editable(editable=False)
    assert not editor.add_button.isVisible()
    assert editor.chip_texts()[:2] == ["Au début", "1 min avant"]
    editor.remove(full[0].key)
    assert editor.reminders() == full


def test_a_read_only_detail_does_not_edit_the_alerts(qtbot: Any) -> None:
    task = OTHER.model_copy(update={"reminders": (HALF_HOUR,)})
    host = _detail(qtbot, task, read_only=True)
    detail = host.detail

    assert detail.alert_block.chip_texts() == ["30 min avant"]
    assert not detail.alert_block.add_button.isVisible()


# ------------------------------------------------------ bell and history ----


def entry(key: str, due: int, **fields: Any) -> LoggedAlert:
    return LoggedAlert(
        key=key,
        task_id="visit",
        due=due,
        title=f"Visite {key}",
        body="Commence dans 30 min",
        **fields,
    )


def test_the_bell_counts_what_is_unread(qtbot: Any) -> None:
    bell = BellButton()
    qtbot.addWidget(bell)

    assert bell.badge_text() == ""
    assert "rien de nouveau" in bell.toolTip()
    bell.set_count(1)
    assert bell.badge_text() == "1"
    assert "1 alerte non lue" in bell.toolTip()
    bell.set_count(12)
    assert bell.badge_text() == "9+"
    assert "12 alertes non lues" in bell.toolTip()
    bell.show()
    assert not bell.grab().isNull()


def test_the_history_groups_the_alerts_by_day(qtbot: Any) -> None:
    panel = AlertPanel()
    qtbot.addWidget(panel)
    yesterday = START - MINUTES_PER_DAY
    older = START - 3 * MINUTES_PER_DAY

    assert panel.rows() == []
    assert not panel.empty_label.isHidden()
    assert panel.empty_label.text() == EMPTY_TEXT

    panel.set_entries(
        [
            entry("a", START),
            entry("b", yesterday, read=True, missed=True),
            entry("c", older, read=True, snoozed_until=START + 60),
        ],
        today=MONDAY,
    )

    assert panel.headings() == ["AUJOURD'HUI", "HIER", "VENDREDI 16 OCTOBRE"]
    assert [row.title_label.text() for row in panel.rows()] == ["Visite a", "Visite b", "Visite c"]
    assert [row.state_label.text() for row in panel.rows()] == [
        "08:00",
        "08:00 · manquée",
        "08:00 · reportée à aujourd'hui à 09:00",
    ]
    assert [row.property("unread") for row in panel.rows()] == ["true", "false", "false"]
    assert panel.empty_label.isHidden()
    assert not panel.read_all_button.isHidden()
    assert day_heading(MONDAY, MONDAY) == "Aujourd'hui"
    assert state_text(entry("a", START), MONDAY) == "08:00"


def test_the_rows_of_the_history_ask_for_what_they_offer(qtbot: Any) -> None:
    panel = AlertPanel()
    qtbot.addWidget(panel)
    panel.set_entries([entry("a", START)], today=MONDAY)
    row = panel.rows()[0]

    with qtbot.waitSignal(panel.open_requested) as opened:
        row.open_button.click()
    with qtbot.waitSignal(panel.snooze_requested) as snoozed:
        next(a for a in row.snooze_menu.actions() if a.text() == "Dans 1 h").trigger()
    with qtbot.waitSignal(panel.snooze_requested) as tomorrow:
        next(a for a in row.snooze_menu.actions() if a.text() == "Demain matin").trigger()
    with qtbot.waitSignal(panel.remove_requested) as removed:
        row.remove_button.click()
    with qtbot.waitSignal(panel.read_all_requested):
        panel.read_all_button.click()
    with qtbot.waitSignal(panel.clear_requested):
        panel.clear_button.click()

    assert opened.args == ["a"]
    assert snoozed.args == ["a", 60]
    assert tomorrow.args == ["a", SNOOZE_TOMORROW]
    assert removed.args == ["a"]


# --------------------------------------------------------------- watcher ----


class Poste:
    """A Planning tab with a watcher fed by hand: the snapshot, the time, who I am."""

    def __init__(self, qtbot: Any, directory: Path, *tasks: PlanningTask) -> None:
        self.tab = PlanningTab()
        qtbot.addWidget(self.tab)
        self.tab.resize(1440, 860)
        self.tab.show()
        people = [Resource(id="anna", name="Anna Favre", initials="AF")]
        self.snapshot: PlanningSnapshot | None = PlanningSnapshot(
            tasks={task.id: task for task in tasks},
            resources={person.id: person for person in people},
        )
        self.now = START - 60
        self.identity = ("AF", "")
        self.opened: list[str] = []
        self.told: list[tuple[str, str]] = []
        self.rules = AlertRules()
        self.days: list[date] = []
        self.directory = directory
        self.watcher = AlertWatcher(
            self.tab,
            snapshot=lambda: self.snapshot,
            identity=lambda: self.identity,
            now=lambda: self.now,
            open_task=self.open_task,
            notify=lambda: lambda title, text: self.told.append((title, text)),
            rules=lambda: self.rules,
            open_day=self.days.append,
        )
        self.watcher.open_log(directory)

    def open_task(self, task_id: str) -> bool:
        self.opened.append(task_id)
        return self.snapshot is not None and task_id in self.snapshot.tasks


def visit(task_id: str = "visit", **fields: Any) -> PlanningTask:
    data: dict[str, Any] = {
        "id": task_id,
        "title": "Visite chantier",
        "project_number": "2026-118",
        "start": START,
        "end": START + 120,
        "assignments": {"anna": {}},
        "reminders": (HALF_HOUR,),
    }
    return PlanningTask.model_validate(data | fields)


def test_the_watcher_rings_once_in_the_tab_and_in_the_system(qtbot: Any, tmp_path: Path) -> None:
    poste = Poste(qtbot, tmp_path, visit())
    watcher, tab = poste.watcher, poste.tab

    assert watcher.check() == []
    assert tab.bell.count() == 0

    poste.now = START - 30
    told = watcher.check()

    assert [alert.task_id for alert in told] == ["visit"]
    assert poste.told == [
        ("2026-118 - Visite chantier", "Commence dans 30 min · aujourd'hui à 08:00 · AF")
    ]
    assert toast_texts(tab) == [
        "2026-118 - Visite chantier — Commence dans 30 min · aujourd'hui à 08:00 · AF"
    ]
    assert tab.bell.count() == 1
    assert len(tab.alert_panel.rows()) == 1

    poste.now = START - 29
    assert watcher.check() == []
    assert len(poste.told) == 1


def test_a_task_that_moves_rings_again_and_others_stay_quiet(qtbot: Any, tmp_path: Path) -> None:
    poste = Poste(qtbot, tmp_path, visit(), visit("theirs", assignments={"bruno": {}}))
    poste.now = START - 30
    assert [alert.task_id for alert in poste.watcher.check()] == ["visit"]

    later = START + 240
    assert poste.snapshot is not None
    poste.snapshot = PlanningSnapshot(
        tasks={"visit": visit(start=later, end=later + 120)}, resources=poste.snapshot.resources
    )
    poste.now = later - 30

    assert [alert.due for alert in poste.watcher.check()] == [later - 30]
    assert poste.tab.bell.count() == 2


def test_alerts_missed_while_the_poste_was_off_are_told_in_one_message(
    qtbot: Any, tmp_path: Path
) -> None:
    AlertLog(tmp_path).record([], now=START - 600)
    poste = Poste(qtbot, tmp_path, visit(), visit("second", start=START - 60, end=START))
    poste.now = START + 30

    told = poste.watcher.check()

    assert sorted(alert.task_id for alert in told) == ["second", "visit"]
    assert all(alert.missed for alert in told)
    assert toast_texts(poste.tab) == ["2 alertes manquées pendant que ProjectFlow était fermé"]
    assert len(poste.told) == 1
    assert poste.tab.bell.count() == 2


def test_nothing_rings_before_the_planning_is_loaded(qtbot: Any, tmp_path: Path) -> None:
    poste = Poste(qtbot, tmp_path, visit())
    loaded, poste.snapshot = poste.snapshot, None
    poste.now = START - 30

    assert poste.watcher.check() == []
    assert poste.watcher.log.last_check is None

    poste.snapshot = loaded
    assert len(poste.watcher.check()) == 1


def test_opening_putting_off_and_clearing_from_the_history(qtbot: Any, tmp_path: Path) -> None:
    poste = Poste(qtbot, tmp_path, visit())
    watcher, tab = poste.watcher, poste.tab
    poste.now = START - 30
    key = watcher.check()[0].key

    watcher.show_panel()
    assert tab.alert_panel.isVisible()
    tab.alert_panel.rows()[0].open_button.click()
    assert poste.opened == ["visit"]
    assert not tab.alert_panel.isVisible()
    assert tab.bell.count() == 0

    watcher.snooze(key, 10)
    assert watcher.check() == []
    poste.now = START - 20
    assert [alert.key for alert in watcher.check()] == [key]
    assert tab.bell.count() == 1
    assert len(poste.told) == 2

    watcher.snooze(key, SNOOZE_TOMORROW)
    snoozed = watcher.log.get(key)
    assert snoozed is not None
    assert snoozed.snoozed_until == wall_time(MONDAY, 6, 30) + MINUTES_PER_DAY

    watcher.mark_all_read()
    assert tab.bell.count() == 0
    watcher.remove(key)
    assert tab.alert_panel.rows() == []
    watcher.clear()
    assert AlertLog(tmp_path).entries == []


def test_an_alert_of_a_task_that_is_gone_says_so(qtbot: Any, tmp_path: Path) -> None:
    poste = Poste(qtbot, tmp_path, visit())
    poste.now = START - 30
    key = poste.watcher.check()[0].key
    assert poste.snapshot is not None
    poste.snapshot = PlanningSnapshot(resources=poste.snapshot.resources)

    poste.watcher.open_alert(key)

    assert GONE_MESSAGE in toast_texts(poste.tab)


# ------------------------------------------------------------ controller ----


@pytest.mark.asyncio
async def test_the_detail_saves_the_alerts_and_the_poste_keeps_its_history(
    qtbot: Any,
    tmp_path: Path,
    data_dirs: Any,  # noqa: F811
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    config = make_config(store)
    controller, tab, _hooks = make_controller(qtbot, config)
    await started(controller)
    service = controller.service
    assert service is not None

    tab.detail.field_changed.emit("t-fab", "reminders", (HALF_HOUR,))

    task = snapshot_of(controller).tasks["t-fab"]
    assert task.reminders == (HALF_HOUR,)
    assert service.undo_label == "Modifier les alertes de 2026-118 - Fabrication garde-corps"
    assert tab.bell.isVisible()
    assert controller.alerts.log.last_check is not None
    assert wall_time(TODAY, 0, 0) <= controller.alerts.log.last_check
    assert (config.cache_directory() / LOG_FILE).is_file()

    controller.show_alerts()
    assert tab.alert_panel.isVisible()
    await close(controller)
    assert not tab.alert_panel.isVisible()


@pytest.mark.asyncio
async def test_a_planning_read_while_the_application_closes_rings_nothing(
    qtbot: Any,
    tmp_path: Path,
    data_dirs: Any,  # noqa: F811
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    config = make_config(store)
    controller, _tab, _hooks = make_controller(qtbot, config)
    await started(controller)
    session = controller._session  # noqa: SLF001 - the session being read
    assert session is not None
    assert controller.alerts.running

    # The planning ends its reading once the application is leaving.
    await close(controller)
    controller._loaded(session)  # noqa: SLF001

    assert not controller.alerts.running
