from __future__ import annotations

from PySide6.QtWidgets import QSizePolicy

from projectflow.config import AppConfig
from projectflow.ui.creation_tab import CreationTab
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


def test_creation_tab_uses_expanding_field_widths(qtbot) -> None:  # type: ignore[no-untyped-def]
    tab = CreationTab()
    qtbot.addWidget(tab)

    assert tab.minimumWidth() >= 760
    assert tab.project_id_edit.minimumWidth() > tab.year_combo.minimumWidth()
    assert tab.designation_edit.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding


def test_creation_tab_reset_button_clears_form_fields_only(qtbot) -> None:  # type: ignore[no-untyped-def]
    tab = CreationTab()
    qtbot.addWidget(tab)
    tab.year_combo.addItem("2026")
    tab.year_combo.setCurrentText("2026")
    tab.project_id_edit.setText("4995")
    tab.subproject_edit.setText("2")
    tab.designation_edit.setText("Escalier")
    tab.societe_edit.setText("Balz")
    tab.contact_edit.setText("Lionel")
    tab.localisation_edit.setText("Zurich")
    tab.gere_par_edit.setText("LM")
    tab.append_log("+ Log conserve")

    tab.reset_button.click()

    assert tab.year_combo.currentText() == "2026"
    assert tab.project_id_edit.text() == ""
    assert tab.subproject_edit.text() == ""
    assert tab.designation_edit.text() == ""
    assert tab.societe_edit.text() == ""
    assert tab.contact_edit.text() == ""
    assert tab.localisation_edit.text() == ""
    assert tab.gere_par_edit.text() == ""
    assert "+ Log conserve" in tab.logs.toPlainText()
