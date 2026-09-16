from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Self

import pytest

from projectflow.platform import sync_paths


@pytest.mark.parametrize("folder", ["OneDrive - Balz", "OneDrive-Balz", "SharePoint"])
def test_known_sync_folder_is_detected(tmp_path: Path, folder: str) -> None:
    assert sync_paths.is_synchronized_path(tmp_path / folder / "rep.xlsx")


def test_sharepoint_library_without_brand_in_path(monkeypatch, tmp_path: Path) -> None:
    library = tmp_path / "Balz Metal Sa" / "Chantiers - Documents"
    monkeypatch.setattr(sync_paths, "synchronized_roots", lambda: (library,))
    assert sync_paths.is_synchronized_path(library / "2026" / "rep.xlsx")
    assert not sync_paths.is_synchronized_path(library.with_name("Chantiers - Documents-old"))


def test_custom_onedrive_root_from_environment(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "Entreprise"
    monkeypatch.setenv("OneDriveCommercial", str(root))
    assert root in sync_paths.onedrive_roots()
    assert sync_paths.is_synchronized_path(root / "rep.xlsx")


def test_account_root_uses_registry(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "Deplace"
    monkeypatch.setattr(sync_paths, "_windows_account_roots", lambda: [root])
    assert root in sync_paths.onedrive_roots()


def test_registry_mount_points_include_sharepoint_and_registered_sync_roots(
    monkeypatch,
    tmp_path: Path,
) -> None:
    accounts = r"Software\Microsoft\OneDrive\Accounts"
    providers = r"Software\SyncEngines\Providers\OneDrive"
    manager = r"Software\Microsoft\Windows\CurrentVersion\Explorer\SyncRootManager"
    root = tmp_path / "Entreprise"
    library = tmp_path / "Entreprise" / "Documents partages"
    other = tmp_path / "Autre bibliotheque"
    registered = tmp_path / "Racine inscrite"
    nodes = {
        accounts: (["Business1"], []),
        accounts + r"\Business1": (["ScopeIdToMountPointPathCache"], [("UserFolder", str(root))]),
        accounts + r"\Business1\ScopeIdToMountPointPathCache": ([], [("scope", str(library))]),
        providers: (["library"], []),
        providers + r"\library": ([], [("MountPoint", str(other))]),
        manager: (["OneDrive!sid!account"], []),
        manager + r"\OneDrive!sid!account": (["UserSyncRoots"], []),
        manager + r"\OneDrive!sid!account\UserSyncRoots": ([], [("sid", str(registered))]),
    }

    class Key:
        def __init__(self, path: str) -> None:
            if path not in nodes:
                raise FileNotFoundError(path)
            self.path = path

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    registry = SimpleNamespace(
        HKEY_CURRENT_USER=1,
        HKEY_LOCAL_MACHINE=2,
        OpenKey=lambda _hive, path: Key(path),
        QueryInfoKey=lambda key: (len(nodes[key.path][0]), len(nodes[key.path][1]), 0),
        EnumKey=lambda key, index: nodes[key.path][0][index],
        EnumValue=lambda key, index: (*nodes[key.path][1][index], 1),
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(sync_paths.sys, "platform", "win32")
    assert root in sync_paths.onedrive_roots()
    assert {library, other, registered}.issubset(sync_paths.synchronized_roots())


def test_registry_missing_is_normal(monkeypatch) -> None:
    def missing(*_args: object) -> None:
        raise FileNotFoundError

    registry = SimpleNamespace(HKEY_CURRENT_USER=1, HKEY_LOCAL_MACHINE=2, OpenKey=missing)
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(sync_paths.sys, "platform", "win32")
    assert sync_paths._windows_library_roots() == []  # noqa: SLF001
