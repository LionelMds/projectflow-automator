from __future__ import annotations

import os
import re
import sys
import unicodedata
from contextlib import suppress
from pathlib import Path


def is_excel_recovery_copy(path: Path) -> bool:
    normalized = unicodedata.normalize("NFKD", path.name).encode("ascii", "ignore").decode()
    normalized = re.sub(r"[\W_]+", " ", normalized.casefold())
    return bool(re.search(r"\b(?:non\s+fusionne(?:e|s|es)?|unmerged)\b", normalized)) or any(
        part.casefold() == "temporarybackupfile" for part in path.parts
    )


def onedrive_roots() -> tuple[Path, ...]:
    """Find account roots, including accounts installed outside the user profile."""
    roots = [
        Path(value).expanduser()
        for name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")
        if (value := os.environ.get(name, "").strip())
    ]
    roots.extend(_windows_account_roots())
    for base in (Path.home(), Path.home() / "Library" / "CloudStorage"):
        with suppress(OSError):
            roots.extend(path for path in base.glob("OneDrive*") if path.is_dir())
    return tuple(dict.fromkeys(roots))


def synchronized_roots() -> tuple[Path, ...]:
    """Include SharePoint libraries whose local folder has no OneDrive in its name."""
    return tuple(dict.fromkeys((*onedrive_roots(), *_windows_library_roots())))


def is_synchronized_path(path: Path) -> bool:
    for candidate in (path.expanduser().absolute(), _resolved_path(path)):
        if any(
            part.casefold().startswith(marker)
            for part in candidate.parts
            for marker in ("onedrive", "sharepoint")
        ):
            return True
        if any(candidate.is_relative_to(_resolved_path(root)) for root in synchronized_roots()):
            return True
    return False


def _resolved_path(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser().absolute()


def _windows_account_roots() -> list[Path]:
    return _registry_paths(
        r"Software\Microsoft\OneDrive\Accounts",
        value_names={"userfolder"},
        depth=1,
    )


def _windows_library_roots() -> list[Path]:
    roots = _registry_paths(
        r"Software\SyncEngines\Providers\OneDrive",
        value_names={"mountpoint"},
        depth=2,
    )
    roots.extend(
        _registry_paths(
            r"Software\Microsoft\OneDrive\Accounts",
            value_names=set(),
            depth=2,
            all_values_in="ScopeIdToMountPointPathCache",
        ),
    )
    roots.extend(
        _registry_paths(
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\SyncRootManager",
            value_names=set(),
            depth=2,
            all_values_in="UserSyncRoots",
            machine=True,
        ),
    )
    return roots


def _registry_paths(
    key_path: str,
    *,
    value_names: set[str],
    depth: int,
    all_values_in: str = "",
    machine: bool = False,
) -> list[Path]:
    if sys.platform != "win32":
        return []
    import winreg  # noqa: PLC0415

    roots: list[Path] = []
    hive = winreg.HKEY_LOCAL_MACHINE if machine else winreg.HKEY_CURRENT_USER

    def visit(path: str, remaining: int) -> None:
        try:
            with winreg.OpenKey(hive, path) as key:
                subkeys, values, _modified = winreg.QueryInfoKey(key)
                for index in range(values):
                    name, value, _kind = winreg.EnumValue(key, index)
                    include = name.casefold() in value_names or (
                        bool(all_values_in) and path.rsplit("\\", 1)[-1] == all_values_in
                    )
                    if include and isinstance(value, str) and value.strip():
                        candidate = Path(os.path.expandvars(value))
                        if candidate.is_absolute():
                            roots.append(candidate)
                if remaining:
                    for index in range(subkeys):
                        visit(f"{path}\\{winreg.EnumKey(key, index)}", remaining - 1)
        except OSError:
            # Missing providers/accounts are normal on machines without OneDrive.
            return

    visit(key_path, depth)
    return roots
