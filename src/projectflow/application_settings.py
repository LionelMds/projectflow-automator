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


class ApplicationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    github_owner: str = ""
    github_repo: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> Self:
        explicit_path = path or _settings_path_from_env()
        if explicit_path is not None:
            return cls._load_path(explicit_path)
        return cls._load_resource()

    @classmethod
    def _load_path(cls, path: Path) -> Self:
        if not path.exists():
            return cls()
        try:
            raw_data = json.loads(path.read_text(encoding="utf-8"))
            return cls.model_validate(raw_data)
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
            return cls.model_validate(raw_data)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise ConfigError("Configuration applicative embarquee invalide") from exc


def _settings_path_from_env() -> Path | None:
    raw_path = os.environ.get(APP_SETTINGS_ENV, "").strip()
    if not raw_path:
        return None
    return Path(raw_path).expanduser()
