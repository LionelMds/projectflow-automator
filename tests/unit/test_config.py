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
