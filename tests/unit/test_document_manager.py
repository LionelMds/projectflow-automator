from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from projectflow.cad import document_manager
from projectflow.cad.document_manager import _ComModules, open_document_manager
from projectflow.exceptions import CadError, CadUnavailableError


class FakeComError(Exception):
    pass


class FakeVariant:
    def __init__(self, _kind: int, value: int) -> None:
        self.value = value


class FakePythoncom:
    VT_BYREF = 0x4000
    VT_I4 = 3

    def __init__(self) -> None:
        self.initialized = 0
        self.uninitialized = 0

    def CoInitialize(self) -> None:  # noqa: N802
        self.initialized += 1

    def CoUninitialize(self) -> None:  # noqa: N802
        self.uninitialized += 1


class FakeSearch:
    SearchFilters = 0


class FakeDocument:
    def __init__(self) -> None:
        self.properties = {"Projet": "20XX-XXXX"}
        self.references = ["C:/Modeles/20XX-XXXX-PRT-100.SLDPRT"]
        self.replaced: list[tuple[str, str]] = []
        self.save_result = 0
        self.closed = False

    def GetCustomPropertyNames(self) -> tuple[str, ...]:  # noqa: N802
        return tuple(self.properties)

    def GetCustomProperty(self, name: str, value_type: FakeVariant) -> str:  # noqa: N802
        value_type.value = 30
        return self.properties[name]

    def SetCustomProperty(self, name: str, value: str) -> bool:  # noqa: N802
        self.properties[name] = value
        return True

    def AddCustomProperty(self, name: str, value_type: int, value: str) -> bool:  # noqa: N802
        assert value_type == 30
        self.properties[name] = value
        return True

    def GetAllExternalReferences(self, search: FakeSearch) -> tuple[str, ...]:  # noqa: N802
        assert search.SearchFilters == 9
        return tuple(self.references)

    def ReplaceReference(self, old: str, new: str) -> None:  # noqa: N802
        self.replaced.append((old, new))

    def Save(self) -> int:  # noqa: N802
        return self.save_result

    def CloseDoc(self) -> None:  # noqa: N802
        self.closed = True


class FakeApplication:
    def __init__(self, document: FakeDocument | None, error: int = 0) -> None:
        self.document = document
        self.error = error
        self.opened: list[tuple[str, int, bool]] = []

    def GetDocument(  # noqa: N802
        self,
        path: str,
        document_type: int,
        read_only: bool,  # noqa: FBT001 - COM positional signature
        status: FakeVariant,
    ) -> FakeDocument | None:
        self.opened.append((path, document_type, read_only))
        status.value = self.error
        return self.document

    def GetSearchOptionObject(self) -> FakeSearch:  # noqa: N802
        return FakeSearch()


class FakeFactory:
    def __init__(self, application: FakeApplication | None) -> None:
        self.application = application
        self.keys: list[str] = []

    def GetApplication(self, key: str) -> FakeApplication | None:  # noqa: N802
        self.keys.append(key)
        return self.application


class FakeClient:
    VARIANT = FakeVariant

    def __init__(self, factory: FakeFactory | None) -> None:
        self.factory = factory

    def Dispatch(self, prog_id: str) -> FakeFactory:  # noqa: N802
        assert prog_id == "SwDocumentMgr.SwDMClassFactory"
        if self.factory is None:
            raise FakeComError("Classe non enregistree")
        return self.factory


def _install(monkeypatch: pytest.MonkeyPatch, factory: FakeFactory | None) -> FakePythoncom:
    pythoncom = FakePythoncom()
    com = _ComModules(pythoncom, FakeClient(factory), FakeComError)
    monkeypatch.setattr(document_manager, "_load_com_modules", lambda: com)
    return pythoncom


def test_document_manager_reads_and_writes_file_properties_and_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = FakeDocument()
    application = FakeApplication(document)
    factory = FakeFactory(application)
    pythoncom = _install(monkeypatch, factory)

    with (
        open_document_manager(" cle ") as manager,
        manager.open_document(Path("C:/Projet/2026-5233-ENS-100.SLDASM")) as opened,
    ):
        assert opened.custom_properties() == {"Projet": "20XX-XXXX"}
        opened.set_custom_property("Projet", "2026-5233")
        opened.set_custom_property("Client", "Client SA")
        assert opened.external_references() == ["C:/Modeles/20XX-XXXX-PRT-100.SLDPRT"]
        opened.replace_reference("C:/Modeles/a.SLDPRT", "C:/Projet/a.SLDPRT")
        opened.save()

    assert factory.keys == ["cle"]
    assert application.opened == [(str(Path("C:/Projet/2026-5233-ENS-100.SLDASM")), 2, False)]
    assert document.properties == {"Projet": "2026-5233", "Client": "Client SA"}
    assert document.replaced == [("C:/Modeles/a.SLDPRT", "C:/Projet/a.SLDPRT")]
    assert document.closed
    assert (pythoncom.initialized, pythoncom.uninitialized) == (1, 1)


def test_document_manager_invalid_license_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeFactory(FakeApplication(None, error=5)))

    with (
        pytest.raises(CadUnavailableError, match="licence"),
        open_document_manager("cle") as manager,
        manager.open_document(Path("a.SLDPRT"), read_only=True),
    ):
        pass


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (None, "n'est pas installe"),
        (FakeFactory(None), "refusee"),
    ],
)
def test_document_manager_absent_or_key_refused(
    monkeypatch: pytest.MonkeyPatch,
    factory: FakeFactory | None,
    message: str,
) -> None:
    pythoncom = _install(monkeypatch, factory)

    with pytest.raises(CadUnavailableError, match=message), open_document_manager("cle"):
        pass

    assert pythoncom.uninitialized == 1


def test_document_manager_reports_open_and_save_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    document = FakeDocument()
    document.save_result = 1
    application: Any = FakeApplication(document)
    _install(monkeypatch, FakeFactory(application))

    with open_document_manager("cle") as manager:
        with (
            pytest.raises(CadError, match="Enregistrement"),
            manager.open_document(Path("a.SLDPRT")) as opened,
        ):
            opened.save()
        with pytest.raises(CadError, match="non gere"), manager.open_document(Path("a.dwg")):
            pass
        application.error = 3
        with (
            pytest.raises(CadError, match="non telecharge depuis OneDrive"),
            manager.open_document(Path("a.SLDPRT")),
        ):
            pass
