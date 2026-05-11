from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from projectflow.exceptions import ConfigError
from projectflow.platform.paths import config_file, expand_user_path


class UserConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = ""
    user_id: str = ""
    display_name: str = ""
    email: str = ""


class RepertoireChantierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drive_id: str = ""
    item_id: str = ""
    display_path: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.drive_id and self.item_id)

    @property
    def has_target(self) -> bool:
        return self.is_configured or bool(self.display_path.strip())


class PathsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    racine_projets: Path | None = None
    dossier_reference: Path | None = None
    repertoire_chantier: RepertoireChantierConfig = Field(
        default_factory=RepertoireChantierConfig,
    )

    @field_validator("racine_projets", "dossier_reference", mode="before")
    @classmethod
    def expand_paths(cls, value: object) -> object:
        if value in (None, ""):
            return None
        if isinstance(value, str | Path):
            return expand_user_path(value)
        return value


class OutlookFolderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    children: list[OutlookFolderConfig] = Field(default_factory=list)


class OutlookConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    company_domain: str = "balzmetal.ch"
    mailbox_email: str = ""
    mailbox_store_id: str = ""
    base_folder: str = "root"
    arborescence: list[OutlookFolderConfig] = Field(
        default_factory=lambda: [
            OutlookFolderConfig(
                name="[YYYY]",
                children=[
                    OutlookFolderConfig(name="[PROJECT_FOLDER]"),
                ],
            )
        ],
    )

    @property
    def target_mailbox(self) -> str:
        return self.mailbox_email.strip()

    @property
    def target_store_id(self) -> str:
        return self.mailbox_store_id.strip()

    @property
    def target_base_folder(self) -> str:
        return self.base_folder.strip() or "root"


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    user: UserConfig = Field(default_factory=UserConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    outlook: OutlookConfig = Field(default_factory=OutlookConfig)

    @property
    def is_onboarded(self) -> bool:
        return bool(
            self.paths.racine_projets
            and self.paths.dossier_reference
            and self.paths.repertoire_chantier.has_target,
        )

    @classmethod
    def load(cls, path: Path | None = None) -> Self:
        resolved_path = path or config_file()
        if not resolved_path.exists():
            return cls()

        try:
            raw_data = json.loads(resolved_path.read_text(encoding="utf-8"))
            migrated_data = migrate_config(raw_data)
            return cls.model_validate(migrated_data)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise ConfigError(f"Configuration invalide: {resolved_path}") from exc

    def save(self, path: Path | None = None) -> None:
        resolved_path = path or config_file()
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_path.write_text(
            self.model_dump_json(indent=2),
            encoding="utf-8",
        )


def migrate_config(data: dict[str, Any]) -> dict[str, Any]:
    version = data.get("version", 1)
    if version != 1:
        raise ConfigError(f"Version de configuration non supportee: {version}")
    for key in ("micro" "soft" "_client_id", "plan" "ner"):
        data.pop(key, None)
    return data
