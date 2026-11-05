"""Planning copied into the Outlook calendar: the controller and the settings."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_planning_controller import (  # type: ignore[import-not-found]
    Hooks,
    data_dirs,  # noqa: F401 - fixture
    make_config,
    make_controller,
    seed_store,
    started,
    wait_for,
)

from projectflow.config import AppConfig
from projectflow.core.planning.outlook_sync import EventSpec, ExistingEvent
from projectflow.core.planning.poste_config import PlanningPosteConfig
from projectflow.exceptions import AuthError
from projectflow.ui.dialogs.settings import SettingsDialog
from projectflow.ui.planning_controller import PlanningHooks


class FakeGateway:
    def __init__(self) -> None:
        self.events: dict[str, tuple[str, EventSpec]] = {}
        self.calls: list[str] = []

    async def ensure_calendar(self, name: str) -> str:
        self.calls.append(f"calendar {name}")
        return "cal"

    async def list_events(self, _calendar_id: str) -> dict[str, ExistingEvent]:
        return {}

    async def create_event(self, _calendar_id: str, spec: EventSpec) -> str:
        event_id = f"e{len(self.events)}"
        self.events[event_id] = (spec.key, spec)
        return event_id

    async def update_event(self, event_id: str, spec: EventSpec) -> None:
        self.events[event_id] = (spec.key, spec)

    async def delete_event(self, event_id: str) -> None:
        self.events.pop(event_id, None)


class OutlookHooks(Hooks):
    def __init__(self, gateway: FakeGateway | None = None) -> None:
        super().__init__()
        self.gateway = gateway or FakeGateway()
        self.refuse = False

    async def open_outlook(self) -> FakeGateway:
        if self.refuse:
            raise AuthError("consentement requis")
        return self.gateway

    def build(self) -> PlanningHooks:
        return replace(super().build(), open_outlook_calendar=self.open_outlook)


@pytest.mark.asyncio
async def test_the_poste_set_for_it_copies_the_planning(qtbot: Any, tmp_path: Path) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    config = make_config(store)
    config.outlook_sync = True
    hooks = OutlookHooks()
    controller, _tab, _ = make_controller(qtbot, config, hooks=hooks)
    await started(controller)
    controller.sync_outlook_now()
    await wait_for(lambda: controller.outlook_report is not None)
    report = controller.outlook_report
    assert report is not None
    assert report.created > 0
    assert report.created == len(hooks.gateway.events)
    assert any("Planning Balz Métal" in line for line in hooks.logs)
    subjects = [spec.subject for _key, spec in hooks.gateway.events.values()]
    assert any(subject.startswith("2026-") and " - " in subject for subject in subjects)
    await controller.aclose()


@pytest.mark.asyncio
async def test_without_permission_the_planning_works_on(qtbot: Any, tmp_path: Path) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    config = make_config(store)
    config.outlook_sync = True
    hooks = OutlookHooks()
    hooks.refuse = True
    controller, _tab, _ = make_controller(qtbot, config, hooks=hooks)
    await started(controller)
    controller.sync_outlook_now()
    await wait_for(lambda: any("Calendrier Outlook non mis à jour" in line for line in hooks.logs))
    assert any("Connecter le calendrier Outlook" in line for line in hooks.logs)
    assert controller.outlook_report is None
    await controller.aclose()


@pytest.mark.asyncio
async def test_a_poste_not_set_for_it_never_writes(qtbot: Any, tmp_path: Path) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    hooks = OutlookHooks()
    controller, _tab, _ = make_controller(qtbot, make_config(store), hooks=hooks)
    await started(controller)
    controller.sync_outlook_now()
    await asyncio.sleep(0.2)
    assert hooks.gateway.calls == []
    await controller.aclose()


def test_settings_write_the_outlook_choices(qtbot: Any) -> None:
    planning = PlanningPosteConfig()
    dialog = SettingsDialog(AppConfig(), planning_config=planning)
    qtbot.addWidget(dialog)
    assert not dialog.planning_outlook_check.isChecked()
    assert dialog.planning_outlook_edit.text() == "Planning Balz Métal"
    requested: list[str] = []
    dialog.outlook_connect_requested.connect(requested.append)
    dialog.planning_outlook_check.setChecked(True)
    dialog.planning_outlook_hide.setChecked(True)
    dialog.planning_outlook_edit.setText("  Planning   atelier ")
    dialog.planning_outlook_button.click()
    assert requested == ["  Planning   atelier "]
    assert not dialog.planning_outlook_button.isEnabled()
    dialog.outlook_connect_finished("Calendrier prêt.", connected=True)
    assert dialog.planning_outlook_button.isEnabled()
    assert dialog.planning_outlook_status.text() == "Calendrier prêt."
    dialog.apply_to_planning_config(planning)
    assert (planning.outlook_sync, planning.outlook_calendar, planning.outlook_hide_details) == (
        True,
        "Planning atelier",
        True,
    )
    dialog.set_outlook_available(available=False)
    assert not dialog.planning_outlook_check.isEnabled()
    assert "démo" in dialog.planning_outlook_status.text()
