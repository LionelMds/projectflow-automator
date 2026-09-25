"""SolidWorks Document Manager (COM) access, without starting SolidWorks.

Document Manager objects do not answer ``IDispatch`` (pywin32 ``Dispatch`` fails with
``E_NOINTERFACE``): they are called through the typed interfaces of the type library embedded
in ``swdocumentmgr.dll``, with comtypes. The license key is never logged.
"""

from __future__ import annotations

import ctypes
import importlib
import re
import sys
from collections.abc import Callable, Iterator, Sequence
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
# SwDmSearchFilters. The stored path comes first: ReplaceReference needs the path as stored,
# and resolving by folder could hide a reference that still points to the templates.
_SEARCH_EXTERNAL_REFERENCE = 1
_SEARCH_ROOT_ASSEMBLY_FOLDER = 2
_SEARCH_SUBFOLDERS = 4
_SEARCH_IN_CONTEXT_REFERENCE = 8
_SEARCH_FILTERS = (
    _SEARCH_EXTERNAL_REFERENCE | _SEARCH_IN_CONTEXT_REFERENCE,
    _SEARCH_EXTERNAL_REFERENCE
    | _SEARCH_ROOT_ASSEMBLY_FOLDER
    | _SEARCH_SUBFOLDERS
    | _SEARCH_IN_CONTEXT_REFERENCE,
)
_REFERENCE_METHODS = (
    "GetAllExternalReferences4",
    "GetAllExternalReferences2",
    "GetAllExternalReferences",
)
# VARTYPE values used to decode arrays of COM objects.
_VT_EMPTY = 0
_VT_NULL = 1
_VT_DISPATCH = 9
_VT_VARIANT = 12
_VT_UNKNOWN = 13
_VT_TYPEMASK = 0x0FFF
_VT_ARRAY = 0x2000
_VT_BYREF = 0x4000
# SwDmDocumentSaveError
_SAVE_OK = 0


class DocumentManagerDocument:
    def __init__(self, document: Any, application: Any, com: ComApi, path: Path) -> None:
        self._document = document
        self._application = application
        self._com = com
        self._is_assembly = path.suffix.casefold() == ".sldasm"
        self._report: list[str] = []

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

    def external_references(self, search_paths: Sequence[Path] = ()) -> list[str]:
        """Merge the reference list and, for an assembly, the components of each configuration.

        Each source can fail or come back empty depending on the Document Manager version; a
        missed reference would leave the copy linked to the templates, so both are read and
        what each one returned is kept for ``reference_report``.
        """
        self._report = []
        references = self._read_source(
            "liste",
            lambda: self._listed_references(search_paths),
        )
        if self._is_assembly:
            references += self._read_source("composants", self._component_paths)
        return list(dict.fromkeys(references))

    def reference_report(self) -> str:
        return " ; ".join(self._report)

    def _read_source(self, label: str, reader: Callable[[], list[str]]) -> list[str]:
        try:
            items = [item for item in reader() if item]
        except Exception as exc:  # noqa: BLE001 - one source must not hide the other one
            self._report.append(f"{label} : {_exception_text(exc)}")
            return []
        self._report.append(f"{label} : {len(items)}")
        return items

    def _listed_references(self, search_paths: Sequence[Path]) -> list[str]:
        failures: list[str] = []
        for filters in _SEARCH_FILTERS:
            search = self._com.latest(
                self._application.GetSearchOptionObject(), "ISwDMSearchOption"
            )
            if not hasattr(type(search), "SearchFilters"):
                # Setting it on a bare IUnknown pointer would silently do nothing.
                raise CadError("option de recherche Document Manager sans SearchFilters")
            search.SearchFilters = filters
            add_search_path = getattr(search, "AddSearchPath", None)
            if add_search_path is not None:
                for folder in search_paths:
                    with suppress(*self._com.errors):
                        add_search_path(str(folder))
            for method_name in _REFERENCE_METHODS:
                method = getattr(self._document, method_name, None)
                if method is None:
                    continue
                try:
                    references = _string_list(method(search))
                except Exception as exc:  # noqa: BLE001 - try the older method next
                    failures.append(f"{method_name} {_exception_text(exc)}")
                    continue
                if references:
                    return references
        if failures:
            raise CadError(", ".join(dict.fromkeys(failures)))
        return []

    def _component_paths(self) -> list[str]:
        manager = self._document.ConfigurationManager
        paths: list[str] = []
        for name in _string_list(manager.GetConfigurationNames()):
            configuration = self._com.latest(
                manager.GetConfigurationByName(name),
                "ISwDMConfiguration",
            )
            for component in self._com.object_array(configuration, "GetComponents"):
                path = self._com.latest(component, "ISwDMComponent").PathName
                if path:
                    paths.append(str(path))
        return paths

    def replace_reference(self, old_path: str, new_path: str) -> None:
        self._document.ReplaceReference(old_path, new_path)

    def save(self) -> None:
        result = self._document.Save()
        if result not in (None, _SAVE_OK):
            raise CadError(f"Enregistrement SolidWorks refuse (code {result}).")

    def close(self) -> None:
        document, self._document = self._document, None
        if document is not None:
            with suppress(*self._com.errors):
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
            path,
        )
        try:
            yield wrapper
        except self._com.errors as exc:
            raise CadError(f"{path.name}: erreur Document Manager ({exc}).") from exc
        finally:
            wrapper.close()


class ComApi:
    """comtypes plus the module generated from the Document Manager type library."""

    def __init__(
        self,
        comtypes: Any,
        client: Any,
        module: Any,
        *,
        automation: Any = None,
        oleaut32: Any = None,
    ) -> None:
        self.comtypes = comtypes
        self.client = client
        self.module = module
        self.automation = automation
        self.oleaut32 = oleaut32
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

    def object_array(self, pointer: Any, method_name: str) -> list[Any]:
        """Call a method that returns an array of COM objects.

        comtypes cannot convert a SAFEARRAY of IUnknown or IDispatch (``KeyError: 13``), which
        is what ``GetComponents`` returns: the raw method fills a VARIANT decoded here.
        """
        raw = _raw_method(pointer, method_name)
        if raw is None or self.automation is None or self.oleaut32 is None:
            return list(getattr(pointer, method_name)() or ())
        variant = self.automation.VARIANT()
        try:
            try:
                raw(ctypes.byref(variant))
            except ctypes.ArgumentError:
                # Not declared as a VARIANT in this version: comtypes converts it itself.
                return list(getattr(pointer, method_name)() or ())
            return self._variant_objects(variant)
        finally:
            self.oleaut32.VariantClear(ctypes.byref(variant))

    def _variant_objects(self, variant: Any) -> list[Any]:
        vartype = int(variant.vt)
        if vartype in (_VT_EMPTY, _VT_NULL):
            return []
        if vartype in (_VT_UNKNOWN, _VT_DISPATCH):
            value = variant.value
            return [] if value is None else [value]
        element_type = vartype & _VT_TYPEMASK
        if not vartype & _VT_ARRAY or vartype & _VT_BYREF:
            raise CadError(f"tableau COM inattendu (type 0x{vartype:X})")
        if element_type not in (_VT_UNKNOWN, _VT_DISPATCH, _VT_VARIANT):
            raise CadError(f"tableau COM d'elements inattendus (type {element_type})")
        array = ctypes.c_void_p(variant._.c_void_p)  # VARIANT union: the SAFEARRAY address
        lower, upper = ctypes.c_long(), ctypes.c_long()
        self.oleaut32.SafeArrayGetLBound(array, 1, ctypes.byref(lower))
        self.oleaut32.SafeArrayGetUBound(array, 1, ctypes.byref(upper))
        items: list[Any] = []
        for index in range(lower.value, upper.value + 1):
            position = ctypes.c_long(index)
            if element_type == _VT_VARIANT:
                element = self.automation.VARIANT()
                self.oleaut32.SafeArrayGetElement(
                    array, ctypes.byref(position), ctypes.byref(element)
                )
                try:
                    value = element.value
                finally:
                    self.oleaut32.VariantClear(ctypes.byref(element))
                if value is not None:
                    items.append(value)
                continue
            address = ctypes.c_void_p()
            # SafeArrayGetElement AddRefs the pointer; the comtypes pointer releases it.
            self.oleaut32.SafeArrayGetElement(array, ctypes.byref(position), ctypes.byref(address))
            if address.value:
                items.append(ctypes.cast(address, ctypes.POINTER(self.comtypes.IUnknown)))
        return items


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
        automation = importlib.import_module("comtypes.automation")
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
    ctypes_module: Any = ctypes
    return ComApi(
        comtypes,
        client,
        module,
        automation=automation,
        oleaut32=ctypes_module.OleDLL("oleaut32"),
    )


def _raw_method(pointer: Any, method_name: str) -> Any:
    """Return comtypes' low-level method (``_ISwDMConfiguration2__com_GetComponents``)."""
    suffix = f"__com_{method_name}"
    for attribute in dir(pointer):
        if attribute.startswith("_") and attribute.endswith(suffix):
            return getattr(pointer, attribute)
    return None


def _exception_text(error: BaseException) -> str:
    if isinstance(error, CadError):
        return str(error)
    args: tuple[object, ...] = tuple(getattr(error, "args", ()))
    if len(args) >= 2 and isinstance(args[0], int):  # noqa: PLR2004 - (hresult, message, ...)
        return _com_error_text(error)
    return f"{type(error).__name__} {error}".strip()


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


def _string_list(result: Any) -> list[str]:
    """Extract the strings of a COM array; for [out] tuples, the first non-empty text array."""
    if result is None:
        return []
    if isinstance(result, str):
        return [result] if result else []
    items = list(result)
    if items and all(isinstance(item, str) for item in items):
        return [item for item in items if item]
    for item in items:
        if isinstance(item, tuple | list):
            nested = _string_list(item)
            if nested:
                return nested
    return []


def _com_error_text(error: BaseException) -> str:
    args: tuple[object, ...] = tuple(getattr(error, "args", ()))
    if len(args) >= 2 and isinstance(args[0], int):  # noqa: PLR2004 - (hresult, message, ...)
        return f"erreur COM 0x{args[0] & 0xFFFFFFFF:08X} {str(args[1]).strip()}"
    return f"erreur COM {error}"


def _is_windows() -> bool:
    return sys.platform == "win32"
