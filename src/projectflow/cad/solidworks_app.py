"""Relink a closed SolidWorks document through the SolidWorks API.

Document Manager reads the components of the template assembly but its ``ReplaceReference``
leaves them on the templates (SolidWorks 2026). ``ISldWorks::ReplaceReferencedDocument``
replaces a reference in a closed document, like SOLIDWORKS Explorer, without opening it on
screen. A running SolidWorks session is reused; otherwise SolidWorks is started visibly and
left open: SOLIDWORKS connected to 3DEXPERIENCE may ask to sign in at start-up, and a hidden
window would wait forever. The designer works on the new project right after anyway.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol

from projectflow.exceptions import CadError, CadUnavailableError

SOLIDWORKS_PROG_ID = "SldWorks.Application"


class ReferenceReplacer(Protocol):
    def replace_references(self, document: Path, replacements: Mapping[str, str]) -> None:
        """Point the references of a closed document (old path -> new path); raise CadError."""


class SolidWorksReferenceReplacer:
    def __init__(self, com_loader: Callable[[], _Com] | None = None) -> None:
        self._com_loader = com_loader or _load_com

    def replace_references(self, document: Path, replacements: Mapping[str, str]) -> None:
        if not replacements:
            return
        com = self._com_loader()
        com.initialize()
        application = _solidworks_application(com)
        refused = [
            old
            for old, new in replacements.items()
            if not _replace(com, application, document, old, new)
        ]
        if refused:
            names = ", ".join(_file_name(old) for old in refused)
            raise CadError(f"SolidWorks a refuse de remplacer : {names}")


class _Com:
    def __init__(
        self, pythoncom: Any, client: Any, errors: tuple[type[BaseException], ...]
    ) -> None:
        self.pythoncom = pythoncom
        self.client = client
        self.errors = errors

    def initialize(self) -> None:
        try:
            self.pythoncom.CoInitialize()
        except self.errors:
            return  # Already initialised in this thread.


def _solidworks_application(com: _Com) -> Any:
    with suppress(*com.errors):
        return com.client.GetActiveObject(SOLIDWORKS_PROG_ID)
    try:
        application = com.client.Dispatch(SOLIDWORKS_PROG_ID)
    except com.errors as exc:
        raise CadUnavailableError(
            f"SolidWorks est introuvable sur ce poste ({exc}) : les references de "
            "l'assemblage ne peuvent pas etre remplacees",
        ) from exc
    with suppress(*com.errors):
        application.Visible = True
    return application


def _replace(com: _Com, application: Any, document: Path, old: str, new: str) -> bool:
    try:
        return bool(application.ReplaceReferencedDocument(str(document), old, new))
    except com.errors as exc:
        raise CadError(
            f"SolidWorks : remplacement impossible de {_file_name(old)} ({exc})"
        ) from exc


def _file_name(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _load_com() -> _Com:
    if not _is_windows():
        raise CadUnavailableError("SolidWorks n'existe que sous Windows")
    try:
        pythoncom = importlib.import_module("pythoncom")
        client = importlib.import_module("win32com.client")
        pywintypes = importlib.import_module("pywintypes")
    except ImportError as exc:
        raise CadUnavailableError("pywin32 est absent de cette installation") from exc
    error = getattr(pywintypes, "com_error", OSError)
    errors: tuple[type[BaseException], ...] = (OSError,)
    if isinstance(error, type) and issubclass(error, BaseException):
        errors = (error, OSError)
    return _Com(pythoncom, client, errors)


def _is_windows() -> bool:
    return sys.platform == "win32"
