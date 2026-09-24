"""SolidWorks Document Manager (COM) access, without starting SolidWorks.

Document Manager is installed with SolidWorks (or eDrawings/SolidWorks Explorer) and needs a
license key requested from the SolidWorks customer portal. The key is never logged.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from projectflow.exceptions import CadError, CadUnavailableError

SW_DM_CLASS_FACTORY = "SwDocumentMgr.SwDMClassFactory"
# SwDmDocumentType
_DOCUMENT_TYPES = {".sldprt": 1, ".sldasm": 2, ".slddrw": 3}
# SwDmDocumentOpenError
_OPEN_ERROR_NO_LICENSE = 5
_OPEN_ERRORS = {
    1: "ouverture refusee par Document Manager",
    2: "ce n'est pas un fichier SolidWorks",
    3: "fichier introuvable ou non telecharge depuis OneDrive",
    4: "fichier en lecture seule",
    _OPEN_ERROR_NO_LICENSE: "cle de licence Document Manager invalide",
    6: "fichier enregistre avec une version plus recente de SolidWorks",
}
# SwDmCustomInfoType
_CUSTOM_INFO_TEXT = 30
# SwDmSearchFilters: stored path + in-context references. The stored path is what must be
# checked: resolving by folder could hide a reference that still points to the templates.
_SEARCH_EXTERNAL_REFERENCE = 1
_SEARCH_IN_CONTEXT_REFERENCE = 8
# SwDmDocumentSaveError
_SAVE_OK = 0


class DocumentManagerDocument:
    def __init__(self, document: Any, application: Any, com: _ComModules) -> None:
        self._document = document
        self._application = application
        self._com = com

    def custom_properties(self) -> dict[str, str]:
        names = self._document.GetCustomPropertyNames() or ()
        properties: dict[str, str] = {}
        for name in names:
            value_type = self._com.byref_int()
            value = self._document.GetCustomProperty(str(name), value_type)
            properties[str(name)] = "" if value is None else str(value)
        return properties

    def set_custom_property(self, name: str, value: str) -> None:
        names = {str(existing) for existing in self._document.GetCustomPropertyNames() or ()}
        if name in names:
            self._document.SetCustomProperty(name, value)
            return
        if not self._document.AddCustomProperty(name, _CUSTOM_INFO_TEXT, value):
            raise CadError(f"Propriete SolidWorks impossible a creer: {name}")

    def external_references(self) -> list[str]:
        search = self._application.GetSearchOptionObject()
        search.SearchFilters = _SEARCH_EXTERNAL_REFERENCE | _SEARCH_IN_CONTEXT_REFERENCE
        references = self._document.GetAllExternalReferences(search) or ()
        return [str(reference) for reference in references if reference]

    def replace_reference(self, old_path: str, new_path: str) -> None:
        self._document.ReplaceReference(old_path, new_path)

    def save(self) -> None:
        result = self._document.Save()
        if result not in (None, _SAVE_OK):
            raise CadError(f"Enregistrement SolidWorks refuse (code {result}).")

    def close(self) -> None:
        self._document.CloseDoc()


class DocumentManagerSession:
    def __init__(self, application: Any, com: _ComModules) -> None:
        self._application = application
        self._com = com

    @contextmanager
    def open_document(
        self,
        path: Path,
        *,
        read_only: bool = False,
    ) -> Iterator[DocumentManagerDocument]:
        document_type = _DOCUMENT_TYPES.get(path.suffix.casefold())
        if document_type is None:
            raise CadError(f"Type de fichier SolidWorks non gere: {path.name}")
        status = self._com.byref_int()
        try:
            document = self._application.GetDocument(str(path), document_type, read_only, status)
        except self._com.error as exc:
            raise CadError(f"{path.name}: ouverture Document Manager impossible ({exc}).") from exc
        error_code = _int_value(status)
        if document is None or error_code:
            reason = _OPEN_ERRORS.get(error_code, f"erreur {error_code}")
            if error_code == _OPEN_ERROR_NO_LICENSE:
                raise CadUnavailableError(reason)
            raise CadError(f"{path.name}: {reason}.")
        wrapper = DocumentManagerDocument(document, self._application, self._com)
        try:
            yield wrapper
        except self._com.error as exc:
            raise CadError(f"{path.name}: erreur Document Manager ({exc}).") from exc
        finally:
            wrapper.close()


class _ComModules:
    def __init__(self, pythoncom: Any, client: Any, error: type[BaseException]) -> None:
        self.pythoncom = pythoncom
        self.client = client
        self.error = error

    def byref_int(self) -> Any:
        return self.client.VARIANT(self.pythoncom.VT_BYREF | self.pythoncom.VT_I4, 0)


@contextmanager
def open_document_manager(license_key: str) -> Iterator[DocumentManagerSession]:
    """Open Document Manager in the current worker thread (COM apartment per thread)."""
    if not license_key.strip():
        raise CadUnavailableError("cle de licence Document Manager non configuree")
    com = _load_com_modules()
    com.pythoncom.CoInitialize()
    try:
        try:
            factory = com.client.Dispatch(SW_DM_CLASS_FACTORY)
        except com.error as exc:
            raise CadUnavailableError("SolidWorks Document Manager n'est pas installe") from exc
        try:
            application = factory.GetApplication(license_key.strip())
        except com.error as exc:
            raise CadUnavailableError("cle de licence Document Manager refusee") from exc
        if application is None:
            raise CadUnavailableError("cle de licence Document Manager refusee")
        yield DocumentManagerSession(application, com)
    finally:
        com.pythoncom.CoUninitialize()


def _load_com_modules() -> _ComModules:
    if not _is_windows():
        raise CadUnavailableError("Document Manager n'existe que sous Windows")
    try:
        pythoncom = importlib.import_module("pythoncom")
        client = importlib.import_module("win32com.client")
        pywintypes = importlib.import_module("pywintypes")
    except ImportError as exc:
        raise CadUnavailableError("pywin32 est absent") from exc
    error = getattr(pywintypes, "com_error", OSError)
    if not (isinstance(error, type) and issubclass(error, BaseException)):
        error = OSError
    return _ComModules(pythoncom, client, error)


def _is_windows() -> bool:
    return sys.platform == "win32"


def _int_value(variant: Any) -> int:
    value = getattr(variant, "value", variant)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
