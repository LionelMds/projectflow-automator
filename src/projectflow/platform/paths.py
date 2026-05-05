from __future__ import annotations

from pathlib import Path

from platformdirs import user_config_dir, user_data_dir, user_documents_dir, user_log_dir

APP_AUTHOR = "Balz Metal Sa"
APP_NAME = "ProjectFlow"


def config_dir() -> Path:
    return Path(user_config_dir(APP_NAME, APP_AUTHOR))


def config_file() -> Path:
    return config_dir() / "config.json"


def data_dir() -> Path:
    return Path(user_data_dir(APP_NAME, APP_AUTHOR))


def updates_dir() -> Path:
    return data_dir() / "updates"


def logs_dir() -> Path:
    return Path(user_log_dir(APP_NAME, APP_AUTHOR))


def documents_dir() -> Path:
    return Path(user_documents_dir())


def expand_user_path(value: str | Path) -> Path:
    return Path(value).expanduser()


def detect_onedrive_balz_root() -> Path | None:
    candidates = [
        Path.home() / "OneDrive - Balz Metal Sa",
        Path.home() / "Library" / "CloudStorage" / "OneDrive-BalzMetalSa",
        documents_dir().parent / "OneDrive - Balz Metal Sa",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    for base in {Path.home(), documents_dir().parent}:
        if not base.exists():
            continue
        for entry in base.glob("OneDrive*Balz*"):
            if entry.is_dir():
                return entry
    return None
