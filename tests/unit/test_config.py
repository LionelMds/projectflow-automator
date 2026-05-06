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


def test_migrate_config_drops_removed_cloud_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        "{"
        '"version": 1,'
        f'"{"micro" "soft" "_client_id"}": "old-client",'
        f'"{"plan" "ner"}": {{"enabled": true, "plan_id": "plan"}},'
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
