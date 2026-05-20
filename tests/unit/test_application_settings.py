from __future__ import annotations

from pathlib import Path

import pytest

from projectflow.application_settings import (
    APP_SETTINGS_ENV,
    MICROSOFT_CLIENT_ID_ENV,
    ApplicationSettings,
)
from projectflow.exceptions import ConfigError


def test_application_settings_loads_missing_path_as_defaults(tmp_path: Path) -> None:
    settings = ApplicationSettings.load(tmp_path / "missing.json")

    assert settings.github_owner == ""
    assert settings.microsoft_client_id == ""


def test_application_settings_loads_json_file(tmp_path: Path) -> None:
    path = tmp_path / "app_settings.json"
    path.write_text(
        '{'
        '"github_owner": "balz",'
        '"github_repo": "projectflow",'
        '"microsoft_client_id": "client"'
        "}",
        encoding="utf-8",
    )

    settings = ApplicationSettings.load(path)

    assert settings.github_owner == "balz"
    assert settings.github_repo == "projectflow"
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
    path.write_text('{"github_owner": "from-file"}', encoding="utf-8")
    monkeypatch.setenv(APP_SETTINGS_ENV, str(path))

    settings = ApplicationSettings.load()

    assert settings.github_owner == "from-file"


def test_application_settings_client_id_can_come_from_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "app_settings.json"
    path.write_text('{"microsoft_client_id": "from-file"}', encoding="utf-8")
    monkeypatch.setenv(APP_SETTINGS_ENV, str(path))
    monkeypatch.setenv(MICROSOFT_CLIENT_ID_ENV, "from-env")

    settings = ApplicationSettings.load()

    assert settings.microsoft_client_id == "from-env"
