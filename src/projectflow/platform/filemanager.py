from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from projectflow.exceptions import ProjectCreationError


def open_path(path: Path) -> None:
    if platform.system() == "Windows":
        _open_windows_path(path)
        return
    opener = "open" if platform.system() == "Darwin" else "xdg-open"
    subprocess.run([opener, str(path)], check=False)


def open_file_default_app(path: Path) -> bool:
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def pin_to_filemanager_favorites(path: Path) -> None:
    system = platform.system()
    if system == "Windows":
        _pin_windows_quick_access(path)
        return
    if system == "Darwin":
        _add_macos_finder_favorite(path)


def _pin_windows_quick_access(path: Path) -> None:
    # PowerShell Shell.Application keeps the OS-specific trick out of business code.
    escaped = str(path).replace("'", "''")
    script = (
        "$shell = New-Object -ComObject Shell.Application; "
        f"$folder = $shell.Namespace('{escaped}'); "
        "if ($folder -ne $null) { "
        "$folder.Self.InvokeVerb('pintohome') "
        "}"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ProjectCreationError("Impossible d'epingler le dossier dans l'acces rapide Windows.")


def _open_windows_path(path: Path) -> None:
    startfile = getattr(os, "startfile", None)
    if not callable(startfile):
        raise ProjectCreationError("Ouverture Windows indisponible sur cette plateforme.")
    startfile(str(path))


def _add_macos_finder_favorite(path: Path) -> None:
    script = f'tell application "Finder" to add POSIX file "{path}" to sidebar'
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True, text=True)
