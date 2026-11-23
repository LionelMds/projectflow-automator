"""Alerts of a poste: its rules, the hours of silence, the summaries, the session start."""

from __future__ import annotations

import plistlib
import sys
import uuid
from contextlib import suppress
from pathlib import Path

import pytest
from test_planning_commands import (  # type: ignore[import-not-found]
    FRI,
    MON,
    NEXT_MON,
    SAT,
    TUE,
    at,
    ctx_with,
    first_task,
    make_resource,
    make_snapshot,
    make_task,
)

from projectflow.core.planning.alert_log import AlertLog
from projectflow.core.planning.alerts import (
    AlertRules,
    digest_alerts,
    next_working_day,
    tasks_of_day,
)
from projectflow.core.planning.commands import create_task
from projectflow.core.planning.models import Reminder
from projectflow.core.planning.poste_config import PlanningPosteConfig, clock_minutes
from projectflow.platform import autostart

ANNA = make_resource("anna", "Anna Favre", initials="AF")
BRUNO = make_resource("bruno", "Bruno Rossier", initials="BR")


def job(task_id: str, day: object, hour: int, *, people: tuple[str, ...] = ("anna",)) -> object:
    return make_task(task_id, start=at(day, hour), end=at(day, hour + 2), people=people)


# ----------------------------------------------------------------- rules ----


def test_clock_minutes_reads_the_usual_ways_to_type_a_time() -> None:
    assert clock_minutes("06:15") == 375
    assert clock_minutes(" 6h15 ") == 375
    assert clock_minutes("18h") == 1080
    for wrong in ("", "midi", "24:00", "12:60"):
        with pytest.raises(ValueError, match="Heure"):
            clock_minutes(wrong)


def test_a_poste_config_gives_its_alert_rules() -> None:
    assert PlanningPosteConfig().alert_rules() == AlertRules()

    config = PlanningPosteConfig(
        alerts_audience="all",
        alerts_sound=True,
        alerts_quiet=True,
        alerts_quiet_from="19h",
        alerts_morning=True,
        alerts_morning_time="6:00",
        alerts_evening_time="15:30",
        alert_defaults={" livraison ": 60, "montage": -5, "": 10},
    )

    assert config.alerts_quiet_from == "19:00"
    assert config.alert_defaults == {"livraison": 60}
    rules = config.alert_rules()
    assert rules.audience == "all"
    assert rules.sound
    assert rules.quiet_from == 19 * 60
    assert rules.morning == 360
    assert rules.evening is None


def test_a_config_with_an_unreadable_time_keeps_the_rest(tmp_path: Path) -> None:
    path = tmp_path / "planning.json"
    path.write_text(
        '{"poste_id": "p", "alerts_sound": true, "alerts_morning_time": "tôt"}', encoding="utf-8"
    )

    config = PlanningPosteConfig.load(path)

    assert config.alerts_sound
    assert config.alerts_morning_time == "06:15"


def test_the_hours_of_silence_run_over_midnight_and_the_weekend() -> None:
    night = AlertRules(quiet=True)

    assert night.is_quiet(at(MON, 18))
    assert night.is_quiet(at(TUE, 5, 59))
    assert not night.is_quiet(at(TUE, 6))
    assert not night.is_quiet(at(MON, 17, 59))
    assert night.is_quiet(at(SAT, 10))
    assert not AlertRules(quiet=True, quiet_weekend=False).is_quiet(at(SAT, 10))
    assert not AlertRules().is_quiet(at(MON, 23))
    lunch = AlertRules(quiet=True, quiet_from=720, quiet_to=780)
    assert lunch.is_quiet(at(MON, 12, 30))
    assert not lunch.is_quiet(at(MON, 13))
    assert not AlertRules(quiet=True, quiet_from=600, quiet_to=600).is_quiet(at(MON, 10))


# ------------------------------------------------------------- summaries ----


def test_the_day_after_a_friday_is_monday() -> None:
    assert next_working_day(MON) == TUE
    assert next_working_day(FRI) == NEXT_MON
    assert next_working_day(SAT) == NEXT_MON


def test_the_tasks_of_a_day_are_those_of_the_person_in_order() -> None:
    snapshot = make_snapshot(
        ANNA,
        BRUNO,
        job("late", MON, 13),
        job("early", MON, 7),
        job("theirs", MON, 8, people=("bruno",)),
        job("tomorrow", TUE, 7),
        make_task("long", start=at(MON, 7) - 1440 * 3, end=at(MON, 9), people=("anna",)),
        make_task("done", start=at(MON, 9), end=at(MON, 10), people=("anna",), status="done"),
    )

    mine = tasks_of_day(snapshot, MON, resource_id="anna", initials="AF")

    assert [task.id for task in mine] == ["long", "early", "late"]
    assert len(tasks_of_day(snapshot, MON)) == 4


def test_the_morning_summary_tells_the_day_once_its_time_has_come() -> None:
    snapshot = make_snapshot(
        ANNA, job("a", MON, 7), job("b", MON, 8), job("c", MON, 10), job("d", MON, 13)
    )
    rules = AlertRules(morning=375)
    mine = {"resource_id": "anna", "initials": "AF"}

    assert digest_alerts(snapshot, now=at(MON, 6, 14), rules=rules, **mine) == []
    found = digest_alerts(snapshot, now=at(MON, 6, 15), rules=rules, **mine)

    assert len(found) == 1
    alert = found[0]
    assert alert.key == "digest|morning|2026-10-26"
    assert alert.task_id == ""
    assert alert.day == MON.toordinal()
    assert alert.due == at(MON, 6, 15)
    assert alert.title == "Ma journée · lun 26.10"
    assert alert.body == "4 tâches · 07:00 Tache a · 08:00 Tache b · 10:00 Tache c · +1"
    assert digest_alerts(snapshot, now=at(MON, 7), rules=rules, known={alert.key}, **mine) == []
    assert digest_alerts(snapshot, now=at(MON, 10, 16), rules=rules, **mine) == []
    assert digest_alerts(snapshot, now=at(MON, 7), rules=AlertRules(), **mine) == []


def test_the_evening_summary_tells_the_next_working_day() -> None:
    snapshot = make_snapshot(ANNA, job("monday", NEXT_MON, 7))
    rules = AlertRules(evening=960)

    found = digest_alerts(snapshot, now=at(FRI, 16), rules=rules)

    assert [(alert.title, alert.body) for alert in found] == [
        ("À venir · lun 02.11", "1 tâche · 07:00 Tache monday")
    ]
    assert found[0].day == NEXT_MON.toordinal()
    assert digest_alerts(snapshot, now=at(MON, 16), rules=rules) == []


def test_a_summary_keeps_its_day_in_the_history(tmp_path: Path) -> None:
    snapshot = make_snapshot(ANNA, job("a", MON, 7))
    found = digest_alerts(snapshot, now=at(MON, 6, 30), rules=AlertRules(morning=375))

    AlertLog(tmp_path).record(found, now=at(MON, 6, 30))

    entry = AlertLog(tmp_path).entries[0]
    assert entry.day == MON.toordinal()
    assert entry.task_id == ""


# ----------------------------------------------------------- new tasks ----


def test_a_new_task_takes_the_alerts_it_is_given() -> None:
    task = first_task(
        create_task(
            ctx_with(),
            title="Livraison",
            task_type="livraison",
            start=at(MON, 7),
            reminders=(Reminder(minutes=60),),
        )
    )

    assert task.reminders == (Reminder(minutes=60),)


# --------------------------------------------------------- session start ----


def test_the_run_command_and_the_launch_agent_start_in_the_background() -> None:
    assert autostart.run_command(r"C:\Apps\PF.exe") == r'"C:\Apps\PF.exe" --background'
    agent = autostart.launch_agent("/Applications/PF.app/Contents/MacOS/PF")
    assert agent["ProgramArguments"] == ["/Applications/PF.app/Contents/MacOS/PF", "--background"]
    assert agent["RunAtLoad"] is True
    assert autostart.launch_agent_path(Path("/Users/x")).name == (
        "ch.balzmetal.projectflow.autostart.plist"
    )


def test_a_projectflow_run_from_its_sources_offers_no_session_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert not autostart.autostart_supported()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(autostart, "_PLATFORM", "linux")
    assert not autostart.autostart_supported()
    assert not autostart.autostart_enabled()
    autostart.set_autostart(enabled=True)
    monkeypatch.setattr(autostart, "_PLATFORM", "darwin")
    assert autostart.autostart_supported()


def test_macos_starts_with_a_launch_agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(autostart, "_PLATFORM", "darwin")
    path = autostart.launch_agent_path(tmp_path)
    assert not autostart.autostart_enabled(home=tmp_path)

    autostart.set_autostart(enabled=True, home=tmp_path)

    assert plistlib.loads(path.read_bytes())["ProgramArguments"] == [sys.executable, "--background"]
    assert autostart.autostart_enabled(home=tmp_path)
    path.write_bytes(plistlib.dumps(autostart.launch_agent("/elsewhere/PF")))
    assert not autostart.autostart_enabled(home=tmp_path)
    autostart.set_autostart(enabled=False, home=tmp_path)
    autostart.set_autostart(enabled=False, home=tmp_path)
    assert not path.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="the Run key is Windows only")
def test_windows_starts_with_a_run_value(monkeypatch: pytest.MonkeyPatch) -> None:
    import winreg  # noqa: PLC0415 - Windows only

    monkeypatch.setattr(autostart, "_PLATFORM", "win32")
    key = rf"Software\ProjectFlowTests\{uuid.uuid4().hex}\Run"
    try:
        assert not autostart.autostart_enabled(run_key=key)
        autostart.set_autostart(enabled=True, run_key=key)
        assert autostart.autostart_enabled(run_key=key)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as opened:
            value, _kind = winreg.QueryValueEx(opened, autostart.RUN_VALUE)
        assert value == f'"{sys.executable}" --background'
        autostart.set_autostart(enabled=False, run_key=key)
        autostart.set_autostart(enabled=False, run_key=key)
        assert not autostart.autostart_enabled(run_key=key)
    finally:
        parent = key.rsplit("\\", 1)[0]
        for name in (key, parent):
            with suppress(OSError):
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, name)
