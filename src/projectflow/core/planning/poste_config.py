"""Planning settings of this poste: who works here, which store, and how the tab looks.

Kept in ``planning.json`` beside ``config.json`` rather than in ``AppConfig``: AppConfig
refuses unknown fields, while this file must survive a newer ProjectFlow adding some.
Every poste of the shared Microsoft account tells who it is here, and the planning signs
each change with these initials and this poste name.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import uuid
from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from projectflow.core.planning.models import Author
from projectflow.exceptions import ConfigError
from projectflow.graph.drive_planning_store import DEFAULT_ONEDRIVE_FOLDER, folder_segments
from projectflow.graph.planning_store import DEFAULT_PLANNING_LIST_NAME
from projectflow.logging import get_logger
from projectflow.platform.paths import config_dir, data_dir, expand_user_path

PLANNING_CONFIG_FILE_NAME: Final = "planning.json"
BACKUP_SUFFIX: Final = ".bak"
DEFAULT_POSTE_NAME: Final = "POSTE"
DEFAULT_PANEL_WIDTH: Final = 312
MIN_PANEL_WIDTH: Final = 200
MAX_PANEL_WIDTH: Final = 800
PLANNING_DATA_FOLDER: Final = "planning"
LOCAL_STORE_FOLDER: Final = "local-store"
CACHE_KEY_DIGITS: Final = 8
VIEWS: Final = frozenset({"day", "week", "month"})
DEFAULT_OUTLOOK_CALENDAR: Final = "Planning Balz Métal"
MIN_ROW_HEIGHT: Final = 18
MAX_ROW_HEIGHT: Final = 96
MAX_COLUMN_ZOOM: Final = 6.0
"""Hexadecimal digits of the store's hash in the name of its cache folder."""

PlanningBackendName = Literal["onedrive", "sharepoint"]
PlanningView = Literal["day", "week", "month"]
PlanningDensity = Literal["compact", "normal", "large"]
PlanningColorBy = Literal["type", "dossier"]
BacklogSort = Literal["manual", "deadline", "dossier", "added"]
OutlookRoute = Literal["desktop", "graph"]
"""``desktop``: through the Outlook of this poste; ``graph``: Microsoft 365 (admin consent)."""


def planning_config_file() -> Path:
    return config_dir() / PLANNING_CONFIG_FILE_NAME


def planning_cache_directory() -> Path:
    return data_dir() / PLANNING_DATA_FOLDER


def default_poste_name() -> str:
    return platform.node().strip().upper() or DEFAULT_POSTE_NAME


def new_poste_id() -> str:
    return uuid.uuid4().hex


class PlanningPosteConfig(BaseModel):
    # Unknown keys are kept and written back: an older ProjectFlow must not erase them.
    model_config = ConfigDict(extra="allow", validate_assignment=True)

    enabled: bool = True
    backend: PlanningBackendName = "onedrive"
    local_directory: str = ""
    """Empty: ``local_store_directory()`` default under the application data."""
    onedrive_folder: str = DEFAULT_ONEDRIVE_FOLDER
    """Folder of the planning files in the OneDrive of the signed-in account."""
    onedrive_drive_id: str = ""
    """Id of that OneDrive, found at the first opening (another account: another cache)."""
    sharepoint_site_url: str = ""
    sharepoint_site_id: str = ""
    sharepoint_list_id: str = ""
    sharepoint_list_name: str = DEFAULT_PLANNING_LIST_NAME
    poste_id: str = Field(default_factory=new_poste_id)
    poste_name: str = Field(default_factory=default_poste_name)
    user_initials: str = ""
    user_name: str = ""
    user_m365_id: str = ""
    default_view: PlanningView = "week"
    density: PlanningDensity = "normal"
    row_heights: dict[str, int] = Field(default_factory=dict)
    """Lane height in pixels chosen for a view (``day``, ``week``, ``month``)."""
    column_zooms: dict[str, float] = Field(default_factory=dict)
    """Width of the columns of a view against the width that fits the screen (1)."""
    color_by: PlanningColorBy = "type"
    hidden_resource_ids: list[str] = Field(default_factory=list)
    collapsed_teams: list[str] = Field(default_factory=list)
    panel_open: bool = True
    panel_width: int = DEFAULT_PANEL_WIDTH
    backlog_sort: BacklogSort = "manual"
    outlook_sync: bool = False
    """This poste copies the planning into the Outlook calendar (one poste only)."""
    outlook_calendar: str = DEFAULT_OUTLOOK_CALENDAR
    outlook_via: OutlookRoute = "desktop"
    outlook_mailbox: str = ""
    """Address of the Outlook account holding the calendar (``desktop``): Outlook often has
    several."""
    outlook_hide_details: bool = False
    """Leave the clients, places and notes out of the calendar (its link is public)."""

    @field_validator(
        "local_directory",
        "onedrive_drive_id",
        "sharepoint_site_url",
        "sharepoint_site_id",
        "sharepoint_list_id",
        "user_name",
        "user_m365_id",
        "outlook_mailbox",
    )
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("backend", mode="before")
    @classmethod
    def _backend(cls, value: object) -> object:
        # The local store is gone (its folder differed from one poste to the next): a poste
        # still set to it, or to anything unknown, now uses the shared OneDrive planning.
        return value if value in {"onedrive", "sharepoint"} else "onedrive"

    @field_validator("onedrive_folder")
    @classmethod
    def _onedrive_folder(cls, value: str) -> str:
        return "/".join(folder_segments(value))

    @field_validator("sharepoint_list_name")
    @classmethod
    def _list_name(cls, value: str) -> str:
        return value.strip() or DEFAULT_PLANNING_LIST_NAME

    @field_validator("outlook_calendar")
    @classmethod
    def _outlook_calendar(cls, value: str) -> str:
        return " ".join(value.split()) or DEFAULT_OUTLOOK_CALENDAR

    @field_validator("poste_id")
    @classmethod
    def _poste_id(cls, value: str) -> str:
        return value.strip() or new_poste_id()

    @field_validator("poste_name")
    @classmethod
    def _poste_name(cls, value: str) -> str:
        return value.strip() or default_poste_name()

    @field_validator("user_initials")
    @classmethod
    def _initials(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("hidden_resource_ids", "collapsed_teams")
    @classmethod
    def _identifiers(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @field_validator("row_heights")
    @classmethod
    def _row_heights(cls, value: dict[str, int]) -> dict[str, int]:
        return {
            view: min(max(int(height), MIN_ROW_HEIGHT), MAX_ROW_HEIGHT)
            for view, height in value.items()
            if view in VIEWS and height
        }

    @field_validator("column_zooms")
    @classmethod
    def _column_zooms(cls, value: dict[str, float]) -> dict[str, float]:
        return {
            view: min(float(zoom), MAX_COLUMN_ZOOM)
            for view, zoom in value.items()
            if view in VIEWS and zoom > 1
        }

    @field_validator("panel_width")
    @classmethod
    def _panel_width(cls, value: int) -> int:
        return min(max(value, MIN_PANEL_WIDTH), MAX_PANEL_WIDTH)

    @property
    def is_identified(self) -> bool:
        """Whether the person at this poste said who they are (needed to sign changes)."""
        return bool(self.user_initials)

    def author(self) -> Author:
        return Author(
            initials=self.user_initials,
            name=self.user_name,
            poste=self.poste_name,
            poste_id=self.poste_id,
        )

    def local_store_directory(self) -> Path:
        if self.local_directory:
            return expand_user_path(self.local_directory)
        return planning_cache_directory() / LOCAL_STORE_FOLDER

    def cache_key(self) -> str:
        """Name of the cache folder of the store in use, stable from one start to the next.

        ``onedrive-<hash of the drive and the folder>`` or ``sharepoint-<hash of the site
        and the list>``: switching store or list never reuses another store's cursor, eTags
        or writes not sent yet. OneDrive compares folder names without case; its drive id is
        found at the first opening, so that another Microsoft account never shares the
        cache. A SharePoint list is known by the site address and the list name the poste
        always has, so that the ids resolved later keep the same cache (the settings clear
        them whenever the address or the name change); by its ids only when no address is
        given.
        """
        if self.backend == "sharepoint":
            if self.sharepoint_site_url:
                site, planning_list = self.sharepoint_site_url, self.sharepoint_list_name
            else:
                site = self.sharepoint_site_id
                planning_list = self.sharepoint_list_id or self.sharepoint_list_name
            identity = f"{_folded(site)}\n{_folded(planning_list)}"
            return f"sharepoint-{_short_hash(identity)}"
        identity = f"{self.onedrive_drive_id.casefold()}\n{_folded(self.onedrive_folder)}"
        return f"onedrive-{_short_hash(identity)}"

    def cache_directory(self) -> Path:
        """Folder of ``PlanningCache`` for the store in use (see ``cache_key``)."""
        return planning_cache_directory() / self.cache_key()

    @classmethod
    def load(cls, path: Path | None = None) -> Self:
        """Read the settings, never failing: what cannot be read falls back to its default.

        A missing file, or one without poste id, is written at once so that this poste
        keeps the same id from now on. An unreadable file is first copied to ``.bak``.
        """
        resolved = path or planning_config_file()
        try:
            raw_bytes = resolved.read_bytes()
        except FileNotFoundError:
            return cls._fresh(resolved)
        except OSError as exc:
            # Possibly a passing lock: keep the file, use the defaults for this session.
            _log_load_problem(resolved, type(exc).__name__)
            return cls()
        try:
            # Notepad and PowerShell 5.1 write UTF-8 with a BOM.
            raw = json.loads(raw_bytes.decode("utf-8-sig"))
        except ValueError:
            raw = None
        if not isinstance(raw, dict):
            _log_load_problem(resolved, "json")
            _backup(resolved)
            return cls._fresh(resolved)
        config, dropped = cls._validate_tolerantly(raw)
        if dropped:
            get_logger(__name__).warning(
                "planning.config.fields_reset",
                path=str(resolved),
                fields=sorted(dropped),
            )
        if not isinstance(raw.get("poste_id"), str) or not raw["poste_id"].strip():
            _save_quietly(config, resolved)
        return config

    def save(self, path: Path | None = None) -> None:
        resolved = path or planning_config_file()
        content = self.model_dump_json(indent=2).encode("utf-8")
        temporary_path: Path | None = None
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                mode="wb",
                prefix=f".{resolved.stem}.",
                suffix=".tmp",
                dir=resolved.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            temporary_path.replace(resolved)
            temporary_path = None
        except OSError as exc:
            raise ConfigError(
                f"Impossible d'enregistrer la configuration du planning: {resolved}",
            ) from exc
        finally:
            if temporary_path is not None:
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)

    @classmethod
    def _fresh(cls, path: Path) -> Self:
        config = cls()
        _save_quietly(config, path)
        return config

    @classmethod
    def _validate_tolerantly(cls, raw: dict[str, Any]) -> tuple[Self, set[str]]:
        data = dict(raw)
        dropped: set[str] = set()
        # Each failed attempt drops at least one field, so this ends.
        for _attempt in range(len(cls.model_fields) + 1):
            try:
                return cls.model_validate(data), dropped
            except ValidationError as exc:
                invalid = {
                    str(error["loc"][0]) for error in exc.errors() if error["loc"]
                } & data.keys()
                if not invalid:
                    break
                dropped |= invalid
                for name in invalid:
                    del data[name]
        return cls(), dropped | set(raw)


def _short_hash(text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()
    return digest[:CACHE_KEY_DIGITS]


def _folded(text: str) -> str:
    # SharePoint compares site addresses and list names without case; it serves https only.
    folded = text.strip().rstrip("/").casefold()
    return folded.removeprefix("https://").removeprefix("http://")


def _save_quietly(config: PlanningPosteConfig, path: Path) -> None:
    try:
        config.save(path)
    except ConfigError:
        get_logger(__name__).warning("planning.config.save_failed", path=str(path))


def _log_load_problem(path: Path, reason: str) -> None:
    get_logger(__name__).warning("planning.config.unreadable", path=str(path), reason=reason)


def _backup(path: Path) -> None:
    try:
        shutil.copy2(path, path.with_name(f"{path.name}{BACKUP_SUFFIX}"))
    except OSError:
        get_logger(__name__).warning("planning.config.backup_failed", path=str(path))
