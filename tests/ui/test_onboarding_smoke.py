from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt

from projectflow.config import AppConfig, RepertoireChantierConfig, UserConfig
from projectflow.ui.main_window import MainWindow
from projectflow.ui.onboarding.wizard import OnboardingWizard


@dataclass(frozen=True, slots=True)
class FakeProfile:
    def to_config(self) -> UserConfig:
        return AppConfig().user


@dataclass(frozen=True, slots=True)
class FakePlan:
    id: str
    title: str


@dataclass(frozen=True, slots=True)
class FakeBucket:
    id: str
    name: str
    plan_id: str


class FakeMicrosoftService:
    def connect_user(self) -> FakeProfile:
        return FakeProfile()

    def resolve_repertoire(self, display_path: str) -> RepertoireChantierConfig:
        return AppConfig().paths.repertoire_chantier

    def list_plans(self) -> list[FakePlan]:
        return [FakePlan(id="plan", title="Projets")]

    def list_buckets(self, plan_id: str) -> list[FakeBucket]:
        return [FakeBucket(id="bucket", name="Dossiers", plan_id=plan_id)]


def test_onboarding_wizard_smoke(qtbot) -> None:  # type: ignore[no-untyped-def]
    wizard = OnboardingWizard(AppConfig())
    qtbot.addWidget(wizard)

    assert wizard.page(0) is not None


def test_main_window_smoke(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow(AppConfig())
    qtbot.addWidget(window)

    assert window.windowTitle() == "ProjectFlow Automator - Balz Metal Sa"


def test_onboarding_planner_page_loads_choices(qtbot) -> None:  # type: ignore[no-untyped-def]
    wizard = OnboardingWizard(AppConfig(), microsoft_service=FakeMicrosoftService())
    qtbot.addWidget(wizard)

    wizard.planner_page.load_plans()

    assert wizard.planner_page.current_plan_id() == "plan"
    assert wizard.planner_page.current_bucket_id() == "bucket"


def test_onboarding_can_accept_demo_mode_without_microsoft(qtbot) -> None:  # type: ignore[no-untyped-def]
    wizard = OnboardingWizard(AppConfig())
    qtbot.addWidget(wizard)

    qtbot.mouseClick(wizard.microsoft_page.demo_button, Qt.MouseButton.LeftButton)

    assert wizard.demo_requested is True
