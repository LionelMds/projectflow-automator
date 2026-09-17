from __future__ import annotations

from pathlib import Path

import pytest

from projectflow.config import AppConfig
from projectflow.exceptions import ConfigError


def test_load_returns_default_config_when_file_is_missing(tmp_path: Path) -> None:
    config = AppConfig.load(tmp_path / "missing.json")

    assert config.version == 1
    assert config.is_onboarded is False


def test_save_and_load_round_trip_expands_paths(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    config = AppConfig()
    config.paths.racine_projets = Path("~/Projects").expanduser()
    config.save(path)

    loaded = AppConfig.load(path)

    assert loaded.paths.racine_projets == Path("~/Projects").expanduser()


def test_initials_and_excel_open_path_round_trip(tmp_path: Path) -> None:
    config = AppConfig.model_validate({"user": {"initials": " ab "}})
    assert config.user.initials == "AB"
    config.user.initials = " cd "
    assert config.user.initials == "CD"
    repertoire = config.paths.repertoire_chantier
    repertoire.display_path = "https://example.sharepoint.com/:x:/s/site/workbook"
    repertoire.open_path = str(tmp_path / "Repertoire.xlsx")
    path = tmp_path / "config.json"

    config.save(path)
    loaded = AppConfig.load(path)

    assert loaded.user.initials == "CD"
    assert loaded.paths.repertoire_chantier.display_path == repertoire.display_path
    assert loaded.paths.repertoire_chantier.open_path == repertoire.open_path


def test_older_config_defaults_optional_initials_and_open_path(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"version": 1, "user": {}, "paths": {}}', encoding="utf-8")

    config = AppConfig.load(path)

    assert config.user.initials == ""
    assert config.paths.repertoire_chantier.open_path == ""


def test_load_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"version": 1, "unexpected": true}', encoding="utf-8")

    with pytest.raises(ConfigError):
        AppConfig.load(path)


def test_config_is_onboarded_with_local_repertoire_path(tmp_path: Path) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    config.paths.dossier_reference = tmp_path / "reference"
    config.paths.repertoire_chantier.display_path = str(tmp_path / "repertoire.xlsx")

    assert config.is_onboarded is True


def test_migrate_config_drops_removed_cloud_keys_and_keeps_planner(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        "{"
        '"version": 1,'
        f'"{"microsoft_client_id"}": "old-client",'
        f'"{"planner"}": {{"enabled": true, "plan_id": "plan"}},'
        '"paths": {'
        '"racine_projets": "",'
        '"dossier_reference": "",'
        '"repertoire_chantier": {"display_path": "rep.xlsx"}'
        "}"
        "}",
        encoding="utf-8",
    )

    config = AppConfig.load(path)

    assert config.paths.repertoire_chantier.display_path == "rep.xlsx"
    assert config.planner.enabled is True
    assert config.planner.plan_id == "plan"
