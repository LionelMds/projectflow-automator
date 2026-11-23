"""Planning: the settings of the alerts (page « Notifications ») and what they change."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QTime
from PySide6.QtWidgets import QApplication, QLabel
from test_planning_alerts import (  # type: ignore[import-not-found]
    START,
    Poste,
    visit,
)
from test_planning_controller import (  # type: ignore[import-not-found]
    close,
    created_task,
    data_dirs,  # noqa: F401 - fixture
    make_config,
    make_controller,
    seed_store,
    snapshot_of,
    started,
    toast_texts,
)
from test_planning_detail import MONDAY  # type: ignore[import-not-found]

from projectflow.config import AppConfig
from projectflow.core.planning.alerts import AlertRules
from projectflow.core.planning.models import Reminder
from projectflow.core.planning.poste_config import PlanningPosteConfig
from projectflow.core.planning.walltime import wall_time
from projectflow.ui.dialogs.settings import SettingsDialog
from projectflow.ui.dialogs.settings_alerts import (
    AUTOSTART_UNAVAILABLE,
    NO_DEFAULT,
    AlertSettingsPage,
)

TYPES = [("montage", "Montage chantier"), ("livraison", "Livraison")]


# --------------------------------------------------------------- watcher ----


def test_a_poste_with_its_alerts_off_stays_quiet_and_piles_nothing(
    qtbot: Any, tmp_path: Path
) -> None:
    poste = Poste(qtbot, tmp_path, visit())
    poste.rules = AlertRules(enabled=False)
    poste.now = START - 30

    assert poste.watcher.check() == []
    assert poste.watcher.log.last_check == START - 30

    poste.rules = AlertRules()
    poste.now = START - 20
    assert poste.watcher.check() == []
    assert poste.tab.bell.count() == 0


def test_in_the_hours_of_silence_an_alert_waits_in_the_history(qtbot: Any, tmp_path: Path) -> None:
    poste = Poste(qtbot, tmp_path, visit())
    poste.rules = AlertRules(quiet=True, quiet_from=7 * 60, quiet_to=8 * 60)
    poste.now = START - 30

    told = poste.watcher.check()

    assert len(told) == 1
    assert poste.told == []
    assert toast_texts(poste.tab) == []
    assert poste.tab.bell.count() == 1


def test_the_system_and_the_sound_follow_the_settings(
    qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    beeps: list[int] = []
    monkeypatch.setattr(QApplication, "beep", staticmethod(lambda: beeps.append(1)))
    poste = Poste(qtbot, tmp_path, visit())
    poste.rules = AlertRules(system=False, sound=True)
    poste.now = START - 30

    poste.watcher.check()

    assert poste.told == []
    assert beeps == [1]
    assert len(toast_texts(poste.tab)) == 1


def test_a_poste_set_to_all_rings_for_the_tasks_of_the_others(qtbot: Any, tmp_path: Path) -> None:
    mine = Poste(qtbot, tmp_path / "mine", visit("theirs", assignments={"bruno": {}}))
    mine.now = START - 30
    assert mine.watcher.check() == []

    everyone = Poste(qtbot, tmp_path / "all", visit("theirs", assignments={"bruno": {}}))
    everyone.rules = AlertRules(audience="all")
    everyone.now = START - 30

    assert [alert.task_id for alert in everyone.watcher.check()] == ["theirs"]


def test_the_morning_summary_rings_once_and_opens_its_day(qtbot: Any, tmp_path: Path) -> None:
    poste = Poste(qtbot, tmp_path, visit(reminders=()))
    poste.rules = AlertRules(morning=6 * 60 + 15)
    poste.now = wall_time(MONDAY, 6, 15)

    told = poste.watcher.check()

    assert [(alert.title, alert.body) for alert in told] == [
        ("Ma journée · lun 19.10", "1 tâche · 08:00 2026-118 - Visite chantier")
    ]
    assert poste.told == [(told[0].title, told[0].body)]
    poste.now += 1
    assert poste.watcher.check() == []

    poste.watcher.open_alert(told[0].key)
    assert poste.days == [MONDAY]
    assert poste.opened == []
    assert poste.tab.bell.count() == 0


# ------------------------------------------------------------------ page ----


def test_the_page_shows_the_settings_of_the_poste_and_writes_them_back(qtbot: Any) -> None:
    config = PlanningPosteConfig(
        alerts_quiet=True, alerts_morning=True, alert_defaults={"livraison": 60, "ancien": 15}
    )
    page = AlertSettingsPage(config, task_types=TYPES, autostart=False)
    qtbot.addWidget(page)

    assert page.enabled_check.isChecked()
    assert page.audience_choice.current() == "mine"
    assert page.quiet_from_edit.time() == QTime(18, 0)
    assert page.morning_edit.isEnabled()
    assert not page.evening_edit.isEnabled()
    assert page.default_combos["livraison"].currentText() == "1 h avant"
    assert page.default_combos["montage"].currentData() == NO_DEFAULT
    assert page.summary() == "Silence programmé"
    assert page.autostart_wanted() is False

    page.quiet_check.setChecked(False)
    page.sound_check.setChecked(True)
    page.evening_check.setChecked(True)
    page.evening_edit.setTime(QTime(15, 30))
    page.default_combos["livraison"].setCurrentIndex(0)
    montage = page.default_combos["montage"]
    montage.setCurrentIndex(montage.findData(30))
    page.autostart_check.setChecked(True)
    target = PlanningPosteConfig()
    page.apply_to(target)

    assert not target.alerts_quiet
    assert target.alerts_sound
    assert target.alerts_morning
    assert target.alerts_evening
    assert target.alerts_evening_time == "15:30"
    assert target.alert_defaults == {"ancien": 15, "montage": 30}
    assert page.summary() == "Actives"
    assert page.autostart_wanted() is True


def test_alerts_switched_off_grey_the_rest_and_no_session_start_from_the_sources(
    qtbot: Any,
) -> None:
    page = AlertSettingsPage(PlanningPosteConfig(), task_types=())
    qtbot.addWidget(page)

    page.enabled_check.setChecked(False)

    assert not page.system_check.isEnabled()
    assert not page.quiet_check.isEnabled()
    assert page.summary() == "Coupées"
    assert not page.autostart_check.isEnabled()
    assert page.autostart_wanted() is None
    assert any(AUTOSTART_UNAVAILABLE in text.text() for text in page.findChildren(QLabel))
    target = PlanningPosteConfig()
    page.apply_to(target)
    assert not target.alerts_enabled


def test_the_settings_dialog_has_the_notifications_section(qtbot: Any) -> None:
    config = PlanningPosteConfig(user_initials="LM")
    dialog = SettingsDialog(
        AppConfig(), planning_config=config, planning_task_types=TYPES, autostart=None
    )
    qtbot.addWidget(dialog)

    dialog.show_section("alerts")
    assert dialog.pages.currentWidget().findChild(AlertSettingsPage) is dialog.alerts_page
    assert dialog._section_meta["alerts"].text() == "Actives"  # noqa: SLF001
    dialog.alerts_page.enabled_check.setChecked(False)
    assert dialog._section_meta["alerts"].text() == "Coupées"  # noqa: SLF001

    target = config.model_copy(deep=True)
    dialog.apply_to_planning_config(target)
    assert not target.alerts_enabled
    assert target.user_initials == "LM"


# ------------------------------------------------------------ controller ----


@pytest.mark.asyncio
async def test_a_task_created_on_the_poste_takes_the_alert_of_its_type(
    qtbot: Any,
    tmp_path: Path,
    data_dirs: Any,  # noqa: F811
) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    config = make_config(store)
    config.alert_defaults = {"montage": 30}
    controller, tab, _hooks = make_controller(qtbot, config)
    await started(controller)

    before = set(snapshot_of(controller).tasks)
    tab.quick.set_text("118 montage garde-corps KDU lun")
    assert tab.quick.submit()
    assert created_task(controller, before).reminders == (Reminder(minutes=30),)

    before = set(snapshot_of(controller).tasks)
    tab.quick.set_text("118 livraison KDU mar")
    assert tab.quick.submit()
    assert created_task(controller, before).reminders == ()

    day = tab.anchor + timedelta(days=21)
    controller.alerts._open_day(day)  # noqa: SLF001
    assert tab.period_range()[0] <= day <= tab.period_range()[1]
    await close(controller)
