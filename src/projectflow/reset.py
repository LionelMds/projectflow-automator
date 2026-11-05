"""Erase everything ProjectFlow keeps on this computer, as for a first installation."""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from projectflow.auth.token_storage import TokenCacheStorage
from projectflow.cad.license_storage import SolidWorksLicenseStorage
from projectflow.core.planning.poste_config import LOCAL_STORE_FOLDER, PLANNING_DATA_FOLDER
from projectflow.logging import get_logger
from projectflow.platform.paths import config_file, data_dir, logs_dir

# Run by the installer's "Nouvelle installation" task, before the first start.
RESET_ARGUMENT = "--reset-all"


class ClearableStorage(Protocol):
    def clear(self) -> object:
        """Delete the stored secret."""


def reset_local_data(
    *,
    storages: Iterable[ClearableStorage] | None = None,
    data_root: Path | None = None,
    config_path: Path | None = None,
    keep: Path | None = None,
    preserve: Iterable[Path] | None = None,
) -> list[str]:
    """Delete the Microsoft connection, settings, licence key and downloads.

    The project folders and the repertoire are never touched. The log is kept so a
    problem can still be diagnosed after the reset, and so is the planning stored on this
    poste (``preserve``, by default the local planning store): like the project folders it
    is the company's data, not a setting. Returns what could not be deleted.
    """
    failures: list[str] = []
    if storages is None:
        storages = (TokenCacheStorage(), SolidWorksLicenseStorage())
    for storage in storages:
        try:
            storage.clear()
        except OSError as exc:
            failures.append(f"{type(storage).__name__}: {exc}")
    root = data_root or data_dir()
    if preserve is None:
        preserve = (root / PLANNING_DATA_FOLDER / LOCAL_STORE_FOLDER,)
    kept = [path.resolve() for path in (keep or logs_dir(), *preserve)]
    entries = [config_path or config_file()]
    if root.is_dir():
        entries.extend(root.iterdir())
    for entry in entries:
        _erase(entry, kept, failures)
    get_logger(__name__).info("app.reset", failures=len(failures))
    return failures


def _erase(entry: Path, kept: list[Path], failures: list[str]) -> None:
    resolved = entry.resolve()
    if resolved in kept:
        return
    try:
        if entry.is_dir() and not entry.is_symlink():
            if any(path.is_relative_to(resolved) for path in kept):
                # Only what surrounds a kept folder goes.
                for child in entry.iterdir():
                    _erase(child, kept, failures)
                return
            shutil.rmtree(entry)
        else:
            entry.unlink(missing_ok=True)
    except OSError as exc:
        failures.append(f"{entry}: {exc}")
