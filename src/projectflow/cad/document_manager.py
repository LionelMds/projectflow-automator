"""SolidWorks Document Manager (COM) access, without starting SolidWorks.

Document Manager objects do not answer ``IDispatch`` (pywin32 ``Dispatch`` fails with
``E_NOINTERFACE``): they are called through the typed interfaces of the type library embedded
in ``swdocumentmgr.dll``, with comtypes. The license key is never logged.
"""

from __future__ import annotations

import importlib
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from projectflow.cad.document_manager_registry import (
    DM_PROG_ID,
    default_registry,
    diagnose_document_manager,
    registered_dll_path,
)
from projectflow.exceptions import CadError, CadUnavailableError

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
    def __init__(self, document: Any, application: Any, com: ComApi) -> None:
        self._document = document
        self._application = application
        self._com = com

    def custom_properties(self) -> dict[str, str]:
        names = self._document.GetCustomPropertyNames() or ()
        properties: dict[str, str] = {}
        for name in names:
            properties[str(name)] = _text_result(self._document.GetCustomProperty(str(name)))
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
        references = _references_result(self._document.GetAllExternalReferences(search))
        return [str(reference) for reference in references if reference]

    def replace_reference(self, old_path: str, new_path: str) -> None:
        self._document.ReplaceReference(old_path, new_path)

    def save(self) -> None:
        result = self._document.Save()
        if result not in (None, _SAVE_OK):
            raise CadError(f"Enregistrement SolidWorks refuse (code {result}).")

    def close(self) -> None:
        document, self._document = self._document, None
        if document is not None:
            document.CloseDoc()


class DocumentManagerSession:
    def __init__(self, application: Any, com: ComApi) -> None:
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
        try:
            result = self._application.GetDocument(str(path), document_type, read_only)
        except self._com.errors as exc:
            raise CadError(f"{path.name}: ouverture Document Manager impossible ({exc}).") from exc
        document, error_code = _document_result(result)
        if not document or error_code:
            reason = _OPEN_ERRORS.get(error_code, f"erreur {error_code}")
            if error_code == _OPEN_ERROR_NO_LICENSE:
                raise CadUnavailableError(reason)
            raise CadError(f"{path.name}: {reason}.")
        wrapper = DocumentManagerDocument(
            self._com.latest(document, "ISwDMDocument"),
            self._application,
            self._com,
        )
        try:
            yield wrapper
        except self._com.errors as exc:
            raise CadError(f"{path.name}: erreur Document Manager ({exc}).") from exc
        finally:
            wrapper.close()


class ComApi:
    """comtypes plus the module generated from the Document Manager type library."""

    def __init__(self, comtypes: Any, client: Any, module: Any) -> None:
        self.comtypes = comtypes
        self.client = client
        self.module = module
        self.errors: tuple[type[BaseException], ...] = (comtypes.COMError, OSError)

    def create_class_factory(self) -> Any:
        coclass = getattr(self.module, "SwDMClassFactory", None)
        return self.client.CreateObject(
            coclass if coclass is not None else DM_PROG_ID,
            interface=self.latest_interface("ISwDMClassFactory"),
        )

    def latest_interface(self, prefix: str) -> Any:
        names = _interface_names(self.module, prefix)
        if not names:
            raise CadUnavailableError(f"interface {prefix} absente de Document Manager")
        return getattr(self.module, names[0])

    def latest(self, pointer: Any, prefix: str) -> Any:
        """Query the newest interface version, which also exposes the older methods."""
        for name in _interface_names(self.module, prefix):
            try:
                return pointer.QueryInterface(getattr(self.module, name))
            except self.errors:
                continue
        return pointer


@contextmanager
def open_document_manager(license_key: str) -> Iterator[DocumentManagerSession]:
    """Open Document Manager in the current worker thread (COM apartment per thread).

    COM stays initialised in the thread: uninitialising it while comtypes pointers still wait
    for garbage collection would crash the application.
    """
    if not license_key.strip():
        raise CadUnavailableError("cle de licence Document Manager non configuree")
    com = _load_com_api()
    try:
        factory = com.create_class_factory()
    except com.errors as exc:
        reason = diagnose_document_manager(default_registry())
        raise CadUnavailableError(
            f"SolidWorks Document Manager introuvable : {reason} ({_com_error_text(exc)})",
        ) from exc
    try:
        application = factory.GetApplication(license_key.strip())
    except com.errors as exc:
        raise CadUnavailableError("cle de licence Document Manager refusee") from exc
    if not application:
        raise CadUnavailableError("cle de licence Document Manager refusee")
    yield DocumentManagerSession(com.latest(application, "ISwDMApplication"), com)


def _load_com_api() -> ComApi:
    if not _is_windows():
        raise CadUnavailableError("Document Manager n'existe que sous Windows")
    registry = default_registry()
    dll_path = registered_dll_path(registry)
    if dll_path is None:
        raise CadUnavailableError(
            f"SolidWorks Document Manager introuvable : {diagnose_document_manager(registry)}",
        )
    try:
        comtypes = importlib.import_module("comtypes")
        client = importlib.import_module("comtypes.client")
    except ImportError as exc:
        raise CadUnavailableError("comtypes est absent de cette installation") from exc
    with suppress(OSError):  # Already initialised in this thread, maybe in another mode.
        comtypes.CoInitialize()
    try:
        module = client.GetModule(str(dll_path))
    except (comtypes.COMError, OSError, ImportError) as exc:
        raise CadUnavailableError(
            f"bibliotheque de types de Document Manager illisible ({dll_path}): {exc}",
        ) from exc
    return ComApi(comtypes, client, module)


def _interface_names(module: Any, prefix: str) -> list[str]:
    pattern = re.compile(rf"^{re.escape(prefix)}(\d*)$")
    found = [
        (int(match.group(1) or 0), name)
        for name in dir(module)
        if (match := pattern.match(name)) is not None
    ]
    return [name for _version, name in sorted(found, reverse=True)]


def _document_result(result: Any) -> tuple[Any, int]:
    """Split ``GetDocument``: comtypes returns the [out] error code and the document."""
    if not isinstance(result, tuple | list):
        return result, 0
    document: Any = None
    error_code = 0
    for item in result:
        if isinstance(item, int) and not isinstance(item, bool):
            error_code = item
        elif document is None:
            document = item
    return document, error_code


def _text_result(result: Any) -> str:
    """Pick the text value from ``GetCustomProperty`` (value and [out] property type)."""
    if isinstance(result, tuple | list):
        texts = [item for item in result if isinstance(item, str)]
        return texts[0] if texts else ""
    return "" if result is None else str(result)


def _references_result(result: Any) -> tuple[Any, ...]:
    if result is None:
        return ()
    if isinstance(result, str):
        return (result,)
    return tuple(result)


def _com_error_text(error: BaseException) -> str:
    args: tuple[object, ...] = tuple(getattr(error, "args", ()))
    if len(args) >= 2 and isinstance(args[0], int):  # noqa: PLR2004 - (hresult, message, ...)
        return f"erreur COM 0x{args[0] & 0xFFFFFFFF:08X} {str(args[1]).strip()}"
    return f"erreur COM {error}"


def _is_windows() -> bool:
    return sys.platform == "win32"
