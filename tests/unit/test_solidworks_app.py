from __future__ import annotations

from pathlib import Path

import pytest

from projectflow.cad.solidworks_app import SolidWorksReferenceReplacer, _Com
from projectflow.exceptions import CadError, CadUnavailableError


class FakeComError(Exception):
    pass


class FakeSolidWorks:
    def __init__(self, *, accepted: bool = True) -> None:
        self.accepted = accepted
        self.calls: list[tuple[str, str, str]] = []
        self.Visible = True
        self.exited = False

    def ReplaceReferencedDocument(self, document: str, old: str, new: str) -> bool:  # noqa: N802
        self.calls.append((document, old, new))
        return self.accepted

    def ExitApp(self) -> None:  # noqa: N802
        self.exited = True


class FakePythoncom:
    def __init__(self) -> None:
        self.initialized = 0

    def CoInitialize(self) -> None:  # noqa: N802
        self.initialized += 1


class FakeClient:
    def __init__(self, *, running: FakeSolidWorks | None, started: FakeSolidWorks | None) -> None:
        self.running = running
        self.started = started

    def GetActiveObject(self, prog_id: str) -> FakeSolidWorks:  # noqa: N802
        assert prog_id == "SldWorks.Application"
        if self.running is None:
            raise FakeComError("Operation non disponible")
        return self.running

    def Dispatch(self, prog_id: str) -> FakeSolidWorks:  # noqa: N802
        assert prog_id == "SldWorks.Application"
        if self.started is None:
            raise FakeComError("Classe non enregistree")
        return self.started


def _replacer(client: FakeClient) -> tuple[SolidWorksReferenceReplacer, FakePythoncom]:
    pythoncom = FakePythoncom()
    com = _Com(pythoncom, client, (FakeComError, OSError))
    return SolidWorksReferenceReplacer(com_loader=lambda: com), pythoncom


REPLACEMENTS = {
    r"C:\Modeles\20XX-XXXX-ENV-100.SLDPRT": r"C:\Projet\2026-5233-ENV-100.SLDPRT",
    r"C:\Modeles\20XX-XXXX-PRT-100.SLDPRT": r"C:\Projet\2026-5233-PRT-100.SLDPRT",
}


def test_running_solidworks_is_reused() -> None:
    running = FakeSolidWorks()
    replacer, pythoncom = _replacer(FakeClient(running=running, started=None))

    replacer.replace_references(Path("C:/Projet/2026-5233-ENS-100.SLDASM"), REPLACEMENTS)

    assert [call[1:] for call in running.calls] == list(REPLACEMENTS.items())
    assert running.calls[0][0] == str(Path("C:/Projet/2026-5233-ENS-100.SLDASM"))
    assert not running.exited
    assert pythoncom.initialized == 1


def test_solidworks_is_started_visible_and_left_open() -> None:
    started = FakeSolidWorks()
    started.Visible = False
    replacer, _pythoncom = _replacer(FakeClient(running=None, started=started))

    replacer.replace_references(Path("C:/Projet/2026-5233-ENS-100.SLDASM"), REPLACEMENTS)

    assert started.Visible is True
    assert len(started.calls) == 2
    assert not started.exited


def test_refused_replacement_is_an_error() -> None:
    running = FakeSolidWorks(accepted=False)
    replacer, _pythoncom = _replacer(FakeClient(running=running, started=None))

    with pytest.raises(CadError, match=r"refuse de remplacer : 20XX-XXXX-ENV-100\.SLDPRT"):
        replacer.replace_references(Path("C:/Projet/a.SLDASM"), REPLACEMENTS)


def test_missing_solidworks_is_reported() -> None:
    replacer, _pythoncom = _replacer(FakeClient(running=None, started=None))

    with pytest.raises(CadUnavailableError, match="SolidWorks est introuvable"):
        replacer.replace_references(Path("C:/Projet/a.SLDASM"), REPLACEMENTS)


def test_nothing_to_replace_does_not_start_solidworks() -> None:
    def fail() -> _Com:
        raise AssertionError("SolidWorks ne doit pas etre demarre")

    SolidWorksReferenceReplacer(com_loader=fail).replace_references(Path("a.SLDASM"), {})
