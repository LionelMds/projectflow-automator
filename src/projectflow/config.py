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


class PlannerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    plan_id: str = ""
    plan_name: str = ""
    bucket_id: str = ""
    bucket_name: str = ""
    due_days: int = Field(default=7, ge=0, le=365)

    @property
    def is_configured(self) -> bool:
        return bool(self.enabled and self.plan_id and self.bucket_id)


class OutlookFolderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    children: list[OutlookFolderConfig] = Field(default_factory=list)


class OutlookConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_domain: str = "balzmetal.ch"
    arborescence: list[OutlookFolderConfig] = Field(
        default_factory=lambda: [OutlookFolderConfig(name="[YYYY]", children=[
            OutlookFolderConfig(name="[YYYY]-[XXXX]"),
        ])],
    )


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    user: UserConfig = Field(default_factory=UserConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    planner: PlannerConfig = Field(default_factory=PlannerConfig)
    outlook: OutlookConfig = Field(default_factory=OutlookConfig)

    @property
    def is_onboarded(self) -> bool:
        return bool(
            self.user.user_id
            and self.paths.racine_projets
            and self.paths.dossier_reference
            and self.paths.repertoire_chantier.is_configured,
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
    return data
