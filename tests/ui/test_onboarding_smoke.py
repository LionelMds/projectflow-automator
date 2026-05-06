from __future__ import annotations

from projectflow.config import AppConfig
from projectflow.ui.main_window import MainWindow
from projectflow.ui.onboarding.wizard import OnboardingWizard


def test_onboarding_wizard_smoke(qtbot) -> None:  # type: ignore[no-untyped-def]
    wizard = OnboardingWizard(AppConfig())
    qtbot.addWidget(wizard)

    assert wizard.page(0) is not None


def test_onboarding_applies_local_paths(qtbot, tmp_path) -> None:  # type: ignore[no-untyped-def]
    wizard = OnboardingWizard(AppConfig())
    qtbot.addWidget(wizard)
    wizard.paths_page.racine_edit.setText(str(tmp_path / "clients"))
    wizard.paths_page.reference_edit.setText(str(tmp_path / "reference"))
    wizard.paths_page.repertoire_edit.setText(str(tmp_path / "repertoire.xlsx"))

    wizard.paths_page.apply_to_config()

    assert wizard.config.paths.racine_projets == tmp_path / "clients"
    assert wizard.config.paths.dossier_reference == tmp_path / "reference"
    assert wizard.config.paths.repertoire_chantier.display_path == str(tmp_path / "repertoire.xlsx")


def test_main_window_smoke(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow(AppConfig())
    qtbot.addWidget(window)

    assert window.windowTitle() == "ProjectFlow Automator - Balz Metal Sa"
