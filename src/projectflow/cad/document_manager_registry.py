"""Find SolidWorks Document Manager in the Windows registry and explain why it is missing."""

from __future__ import annotations

import importlib
import os
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

DM_PROG_ID = "SwDocumentMgr.SwDMClassFactory"
DM_DLL_NAME = "SwDocumentMgr.dll"


class ClassesRegistry(Protocol):
    def default_value(self, path: str, *, wow64_32: bool = False) -> str | None:
        """Return the default value of a HKEY_CLASSES_ROOT subkey, or None."""

    def subkeys(self, path: str = "") -> list[str]:
        """Return the direct subkeys of a HKEY_CLASSES_ROOT key."""


class WindowsClassesRegistry:
    def __init__(self) -> None:
        self._winreg = importlib.import_module("winreg")

    def default_value(self, path: str, *, wow64_32: bool = False) -> str | None:
        winreg = self._winreg
        view = winreg.KEY_WOW64_32KEY if wow64_32 else winreg.KEY_WOW64_64KEY
        try:
            with winreg.OpenKey(
                winreg.HKEY_CLASSES_ROOT,
                path,
                0,
                winreg.KEY_READ | view,
            ) as key:
                value, _kind = winreg.QueryValueEx(key, "")
        except OSError:
            return None
        return str(value).strip() or None

    def subkeys(self, path: str = "") -> list[str]:
        winreg = self._winreg
        names: list[str] = []
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, path) as key:
                index = 0
                while True:
                    try:
                        names.append(winreg.EnumKey(key, index))
                    except OSError:
                        break
                    index += 1
        except OSError:
            return []
        return names


def default_registry() -> ClassesRegistry | None:
    try:
        return WindowsClassesRegistry()
    except ImportError:
        return None


def class_factory_prog_ids(registry: ClassesRegistry | None) -> list[str]:
    """List the generic ProgID, then the versioned ones some installations register alone."""
    prog_ids = [DM_PROG_ID]
    if registry is None:
        return prog_ids
    prefix = f"{DM_PROG_ID}.".casefold()
    versioned = [name for name in registry.subkeys() if name.casefold().startswith(prefix)]
    prog_ids.extend(sorted(versioned, key=_version_key, reverse=True))
    return prog_ids


def registered_dll_path(registry: ClassesRegistry | None) -> Path | None:
    """Return the 64-bit DLL registered for Document Manager, when it exists on disk."""
    if registry is None:
        return None
    for prog_id in class_factory_prog_ids(registry):
        clsid = registry.default_value(f"{prog_id}\\CLSID")
        if clsid is None:
            continue
        server = registry.default_value(f"CLSID\\{clsid}\\InprocServer32")
        if server is None:
            continue
        path = Path(os.path.expandvars(server.strip('"')))
        if path.is_file():
            return path
    return None


def diagnose_document_manager(
    registry: ClassesRegistry | None,
    *,
    dll_candidates: Callable[[], list[Path]] | None = None,
) -> str:
    """Explain in French why Document Manager cannot be created on this computer."""
    if registry is None:
        return "registre Windows inaccessible"
    found_dlls = (dll_candidates or installed_dll_candidates)()
    for prog_id in class_factory_prog_ids(registry):
        clsid = registry.default_value(f"{prog_id}\\CLSID")
        if clsid is None:
            continue
        server_key = f"CLSID\\{clsid}\\InprocServer32"
        server = registry.default_value(server_key)
        if server is None:
            if registry.default_value(server_key, wow64_32=True) is not None:
                return (
                    "Document Manager est inscrit en 32 bits seulement, "
                    f"alors que ProjectFlow est en {_process_bits()} bits"
                )
            continue
        server_path = Path(os.path.expandvars(server.strip('"')))
        if not server_path.is_file():
            return f"la DLL inscrite pour Document Manager est introuvable : {server_path}"
        return f"Document Manager est inscrit ({server_path}) mais refuse de demarrer"
    if found_dlls:
        dll = found_dlls[0]
        return (
            f"{DM_DLL_NAME} est present ({dll}) mais n'est pas inscrit dans Windows. "
            f'Inscription par un administrateur : regsvr32 "{dll}"'
        )
    return (
        f"{DM_DLL_NAME} est introuvable : installez SOLIDWORKS (ou SOLIDWORKS Document "
        "Manager) sur ce poste"
    )


def installed_dll_candidates() -> list[Path]:
    roots = [
        Path(os.environ.get("COMMONPROGRAMFILES", r"C:\Program Files\Common Files")),
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")),
    ]
    patterns = [
        f"SOLIDWORKS Shared/{DM_DLL_NAME}",
        f"SOLIDWORKS Corp/*/{DM_DLL_NAME}",
        f"SOLIDWORKS Corp/*/*/{DM_DLL_NAME}",
        f"SOLIDWORKS Corp/*/*/*/{DM_DLL_NAME}",
    ]
    found: list[Path] = []
    for root in roots:
        for pattern in patterns:
            try:
                found.extend(path for path in root.glob(pattern) if path.is_file())
            except OSError:
                continue
    return list(dict.fromkeys(found))


def _process_bits() -> int:
    return struct.calcsize("P") * 8


def _version_key(prog_id: str) -> tuple[int, ...]:
    suffix = prog_id.rsplit(".", 1)[-1]
    return (int(suffix),) if suffix.isdigit() else (-1,)
