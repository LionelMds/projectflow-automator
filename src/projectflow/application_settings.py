from __future__ import annotations

import json
import os
from importlib import resources
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, ValidationError

from projectflow.exceptions import ConfigError

APP_SETTINGS_ENV = "PROJECTFLOW_APP_SETTINGS"
APP_SETTINGS_RESOURCE = "app_settings.json"
MICROSOFT_CLIENT_ID_ENV = "PROJECTFLOW_MICROSOFT_CLIENT_ID"
DEFAULT_MICROSOFT_CLIENT_ID = "ced436ff-2be9-4792-8551-02e12351c6c9"


class ApplicationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    github_owner: str = ""
    github_repo: str = ""
    microsoft_client_id: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> Self:
        explicit_path = path or _settings_path_from_env()
        settings: Self
        if explicit_path is not None:
            settings = cls._load_path(explicit_path)
        else:
            settings = cls._load_resource()
        client_id = os.environ.get(MICROSOFT_CLIENT_ID_ENV, "").strip()
        if client_id:
            return settings.model_copy(update={"microsoft_client_id": client_id})
        return cls._with_defaults(settings)

    @classmethod
    def _load_path(cls, path: Path) -> Self:
        if not path.exists():
            return cls()
        try:
            raw_data = json.loads(path.read_text(encoding="utf-8"))
            return cls._with_defaults(cls.model_validate(raw_data))
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise ConfigError(f"Configuration applicative invalide: {path}") from exc

    @classmethod
    def _load_resource(cls) -> Self:
        try:
            settings_file = resources.files("projectflow.resources").joinpath(
                APP_SETTINGS_RESOURCE,
            )
        except ModuleNotFoundError:
            return cls()
        if not settings_file.is_file():
            return cls()
        try:
            raw_data = json.loads(settings_file.read_text(encoding="utf-8"))
            return cls._with_defaults(cls.model_validate(raw_data))
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise ConfigError("Configuration applicative embarquee invalide") from exc

    @classmethod
    def _with_defaults(cls, settings: Self) -> Self:
        if settings.microsoft_client_id.strip():
            return settings
        return settings.model_copy(update={"microsoft_client_id": DEFAULT_MICROSOFT_CLIENT_ID})


def _settings_path_from_env() -> Path | None:
    raw_path = os.environ.get(APP_SETTINGS_ENV, "").strip()
    if not raw_path:
        return None
    return Path(raw_path).expanduser()
