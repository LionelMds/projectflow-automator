"""Erase everything ProjectFlow keeps on this computer, as for a first installation."""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from projectflow.auth.token_storage import TokenCacheStorage
from projectflow.cad.license_storage import SolidWorksLicenseStorage
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
) -> list[str]:
    """Delete the Microsoft connection, settings, licence key and downloads.

    The project folders and the repertoire are never touched. The log is kept so a
    problem can still be diagnosed after the reset. Returns what could not be deleted.
    """
    failures: list[str] = []
    if storages is None:
        storages = (TokenCacheStorage(), SolidWorksLicenseStorage())
    for storage in storages:
        try:
            storage.clear()
        except OSError as exc:
            failures.append(f"{type(storage).__name__}: {exc}")
    kept = (keep or logs_dir()).resolve()
    root = data_root or data_dir()
    entries = [config_path or config_file()]
    if root.is_dir():
        entries.extend(root.iterdir())
    for entry in entries:
        if entry.resolve() == kept or kept.is_relative_to(entry.resolve()):
            continue
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink(missing_ok=True)
        except OSError as exc:
            failures.append(f"{entry}: {exc}")
    get_logger(__name__).info("app.reset", failures=len(failures))
    return failures
