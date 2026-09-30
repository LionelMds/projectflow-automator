from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel

from projectflow.config import AppConfig, AppearanceConfig
from projectflow.core.numero import parse_project_number
from projectflow.core.repertoire_service import (
    NextAvailableProject,
    RepertoireRow,
    RepertoireSnapshot,
)
from projectflow.ui.dialogs.settings import SettingsDialog
from projectflow.ui.onboarding.wizard import OnboardingWizard
from projectflow.ui.repertoire_tab import RepertoireDossierTab
from projectflow.ui.theme import MODES, PALETTES, build_theme, style_sheet


class _NoLicense:
    def load(self) -> str:
        return ""

    def save(self, _value: str) -> bool:
        return True

    def clear(self) -> bool:
        return True


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("palette", list(PALETTES))
def test_every_theme_resolves_to_valid_colors(mode: str, palette: str) -> None:
    theme = build_theme(mode, palette)

    assert all(QColor(value).isValid() for value in theme.tokens.values())
    assert "QPushButton" in style_sheet(theme)
    light, dark = QColor(theme.tokens["bg"]), QColor(theme.tokens["text"])
    if mode == "dark":
        light, dark = dark, light
    assert light.lightness() > dark.lightness()


def test_palettes_differ_but_share_the_accent() -> None:
    grounds = {build_theme("light", key).tokens["bg"] for key in PALETTES}

    assert len(grounds) == len(PALETTES)
    assert {build_theme("light", key).tokens["accent"] for key in PALETTES} == {"#5980a6"}


def test_appearance_defaults_and_rejects_unknown_values() -> None:
    config = AppConfig()

    assert config.appearance == AppearanceConfig(mode="light", palette="acier")
    with pytest.raises(ValueError, match="palette"):
        AppearanceConfig.model_validate({"palette": "rose"})


def test_settings_dialog_saves_appearance(qtbot) -> None:  # type: ignore[no-untyped-def]
    config = AppConfig()
    dialog = SettingsDialog(config, license_storage=_NoLicense())
    qtbot.addWidget(dialog)
    dialog.show_section("appearance")

    dark = next(
        button
        for button in dialog.appearance_mode_buttons.buttons()
        if button.property("appearance_key") == "dark"
    )
    dark.click()
    ardoise = next(
        button
        for button in dialog.appearance_palette_buttons.buttons()
        if getattr(button, "palette_key", "") == "ardoise"
    )
    ardoise.click()
    dialog.apply_to_config(config)

    assert config.appearance == AppearanceConfig(mode="dark", palette="ardoise")
    assert set(MODES) == {"light", "dark", "system"}


def test_repertoire_discards_unsaved_cells(qtbot) -> None:  # type: ignore[no-untyped-def]
    tab = RepertoireDossierTab()
    qtbot.addWidget(tab)
    tab.set_snapshot(
        RepertoireSnapshot(
            year=2026,
            rows=(
                RepertoireRow(row_index=10, values=("2026-4990", "", "Client", "", "Objet")),
                RepertoireRow(row_index=11, values=("2026-4991", "", "", "", "")),
            ),
            next_available=NextAvailableProject(
                number=parse_project_number("2026-4991"),
                row_index=11,
            ),
        ),
    )
    model = tab.table.model()
    assert not tab.save_button.isEnabled()

    assert model.setData(model.index(0, 4), "Objet modifié", Qt.ItemDataRole.EditRole)

    assert tab.save_button.isEnabled()
    assert "1 cellule modifiée · 2026-4990" in tab.dirty_label.text()
    tab.discard_button.click()
    assert model.index(0, 4).data() == "Objet"
    assert not tab.save_button.isEnabled()
    assert tab.dirty_label.text() == ""


def test_onboarding_checks_paths_on_last_step(qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    reference = tmp_path / "reference"
    reference.mkdir()
    (reference / "Plans").mkdir()
    wizard = OnboardingWizard(AppConfig())
    qtbot.addWidget(wizard)
    wizard.paths_page.racine_edit.setText(str(tmp_path / "absent"))
    wizard.paths_page.reference_edit.setText(str(reference))
    wizard.paths_page.repertoire_edit.setText("https://example.sharepoint.com/Rep.xlsx")

    wizard.next_button.click()
    wizard.next_button.click()

    assert wizard.current_index() == 2
    assert wizard.next_button.text() == "Terminer"
    titles = [child.text() for child in wizard.verify_page.findChildren(QLabel)]
    assert "Racine projets introuvable" in titles
    assert "Dossier de référence trouvé" in titles
    assert "Répertoire chantier renseigné" in titles
