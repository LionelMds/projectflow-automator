from __future__ import annotations

from pathlib import Path

import pytest

from projectflow.application_settings import (
    APP_SETTINGS_ENV,
    CLIENT_ID_ENV,
    ApplicationSettings,
    resolve_microsoft_client_id,
)
from projectflow.exceptions import ConfigError


def test_application_settings_loads_missing_path_as_defaults(tmp_path: Path) -> None:
    settings = ApplicationSettings.load(tmp_path / "missing.json")

    assert settings.microsoft_client_id == ""


def test_application_settings_loads_json_file(tmp_path: Path) -> None:
    path = tmp_path / "app_settings.json"
    path.write_text('{"microsoft_client_id": "client"}', encoding="utf-8")

    settings = ApplicationSettings.load(path)

    assert settings.microsoft_client_id == "client"


def test_application_settings_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "app_settings.json"
    path.write_text('{"unexpected": true}', encoding="utf-8")

    with pytest.raises(ConfigError):
        ApplicationSettings.load(path)


def test_application_settings_path_can_come_from_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "app_settings.json"
    path.write_text('{"microsoft_client_id": "from-file"}', encoding="utf-8")
    monkeypatch.setenv(APP_SETTINGS_ENV, str(path))

    settings = ApplicationSettings.load()

    assert settings.microsoft_client_id == "from-file"


def test_resolve_microsoft_client_id_prefers_explicit_value() -> None:
    settings = ApplicationSettings(microsoft_client_id="from-settings")

    assert resolve_microsoft_client_id("explicit", settings=settings) == "explicit"


def test_resolve_microsoft_client_id_prefers_env_over_settings() -> None:
    settings = ApplicationSettings(microsoft_client_id="from-settings")

    assert (
        resolve_microsoft_client_id(
            environ={CLIENT_ID_ENV: "from-env"},
            settings=settings,
        )
        == "from-env"
    )


def test_resolve_microsoft_client_id_falls_back_to_settings() -> None:
    settings = ApplicationSettings(microsoft_client_id="from-settings")

    assert resolve_microsoft_client_id(environ={}, settings=settings) == "from-settings"
