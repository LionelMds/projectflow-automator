from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

import pytest

from projectflow.exceptions import PrintError
from projectflow.platform import printing


class FakeComError(Exception):
    pass


class FakePageSetup:
    def __init__(self, *, reject_a4: bool = False) -> None:
        self.values: dict[str, Any] = {}
        self._reject_a4 = reject_a4

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_") or name == "values":
            object.__setattr__(self, name, value)
            return
        if name == "PaperSize" and self._reject_a4:
            raise FakeComError(name)
        self.values[name] = value


class FakeSheet:
    def __init__(self, *, reject_a4: bool = False, fail_print: bool = False) -> None:
        self.PageSetup = FakePageSetup(reject_a4=reject_a4)
        self.printed: list[tuple[Any, ...]] = []
        self._fail_print = fail_print

    def PrintOut(self, *args: Any) -> None:  # noqa: N802
        if self._fail_print:
            raise FakeComError("PrintOut")
        self.printed.append(args)


class FakeWorkbook:
    def __init__(self, sheet: FakeSheet) -> None:
        self.ActiveSheet = sheet
        self.closed: list[dict[str, Any]] = []

    def Close(self, **kwargs: Any) -> None:  # noqa: N802
        self.closed.append(kwargs)


class FakeWorkbooks:
    def __init__(self, workbook: FakeWorkbook) -> None:
        self._workbook = workbook
        self.opened: list[tuple[str, dict[str, Any]]] = []

    def Open(self, path: str, **kwargs: Any) -> FakeWorkbook:  # noqa: N802
        self.opened.append((path, kwargs))
        return self._workbook


class FakeExcel:
    def __init__(
        self,
        sheet: FakeSheet | None = None,
        *,
        active_printer: str = "Xerox Bac1 sur Ne03:",
        accepted_printers: tuple[str, ...] = ("Xerox Bac2 sur Ne02:",),
    ) -> None:
        self.sheet = sheet or FakeSheet()
        self.workbook = FakeWorkbook(self.sheet)
        self.Workbooks = FakeWorkbooks(self.workbook)
        self._active_printer = active_printer
        self._accepted_printers = accepted_printers
        self.printer_attempts: list[str] = []
        self.print_communication: list[bool] = []
        self.quit_calls = 0
        self.Visible = True
        self.DisplayAlerts = True

    @property
    def ActivePrinter(self) -> str:  # noqa: N802
        return self._active_printer

    @ActivePrinter.setter
    def ActivePrinter(self, value: str) -> None:  # noqa: N802
        self.printer_attempts.append(value)
        if value not in self._accepted_printers:
            raise FakeComError(value)
        self._active_printer = value

    @property
    def PrintCommunication(self) -> bool:  # noqa: N802
        return self.print_communication[-1] if self.print_communication else True

    @PrintCommunication.setter
    def PrintCommunication(self, value: bool) -> None:  # noqa: N802
        self.print_communication.append(value)

    def Quit(self) -> None:  # noqa: N802
        self.quit_calls += 1


@pytest.fixture(autouse=True)
def fake_com_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(printing, "_com_error_type", lambda: FakeComError)


def test_excel_printer_names_use_connector_of_current_excel_printer() -> None:
    names = printing.excel_printer_names(
        "Xerox Bac2",
        port="Ne02:",
        current="Xerox C7120 Bac1 Blanc sur Ne03:",
    )

    assert names[0] == "Xerox Bac2 sur Ne02:"
    assert "Xerox Bac2 on Ne02:" in names
    assert names.count("Xerox Bac2 sur Ne02:") == 1
    assert names[-1] == "Xerox Bac2"


def test_excel_printer_names_fall_back_without_port_or_current_printer() -> None:
    assert printing.excel_printer_names("PDF", port=None, current="") == ["PDF"]
    assert printing.excel_printer_names("PDF", port="Ne05:", current="")[0] == "PDF on Ne05:"


def test_printer_port_reads_spooler_port_from_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {"Xerox Bac2": "winspool,Ne02:", "Broken": "winspool"}

    class Key:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def query_value(_key: Key, name: str) -> tuple[str, int]:
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], 1

    fake_winreg = SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        OpenKey=lambda _root, _path: Key(),
        QueryValueEx=query_value,
    )
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    assert printing.printer_port("Xerox Bac2") == "Ne02:"
    assert printing.printer_port("Broken") is None
    assert printing.printer_port("Inconnue") is None


def test_print_with_excel_prints_active_sheet_on_one_a4_page(tmp_path: Path) -> None:
    fiche = tmp_path / "2026-4995 - Fiche dossier clients.xlsx"
    excel = FakeExcel()

    printing.print_with_excel(excel, fiche, printer_name="Xerox Bac2", copies=2, port="Ne02:")

    path, options = excel.Workbooks.opened[0]
    assert path == str(fiche)
    assert options["ReadOnly"] is True
    assert excel.Visible is False
    assert excel.DisplayAlerts is False
    assert excel.printer_attempts == ["Xerox Bac2 sur Ne02:"]
    assert excel.sheet.PageSetup.values == {
        "PaperSize": printing.XL_PAPER_A4,
        "Zoom": False,
        "FitToPagesWide": 1,
        "FitToPagesTall": 1,
    }
    assert excel.print_communication == [False, True]
    assert excel.sheet.printed == [(None, None, 2, False, None, False, True)]
    assert excel.workbook.closed == [{"SaveChanges": False}]


def test_print_with_excel_reports_unknown_printer_and_closes_workbook(tmp_path: Path) -> None:
    excel = FakeExcel(accepted_printers=())

    with pytest.raises(PrintError, match="ne trouve pas l'imprimante Absente"):
        printing.print_with_excel(
            excel,
            tmp_path / "fiche.xlsx",
            printer_name="Absente",
            copies=1,
            port="Ne09:",
        )

    assert excel.printer_attempts[-1] == "Absente"
    assert excel.sheet.printed == []
    assert excel.workbook.closed == [{"SaveChanges": False}]


def test_print_with_excel_reports_printer_without_a4(tmp_path: Path) -> None:
    excel = FakeExcel(FakeSheet(reject_a4=True))

    with pytest.raises(PrintError, match="refuse le format A4"):
        printing.print_with_excel(
            excel,
            tmp_path / "fiche.xlsx",
            printer_name="Xerox Bac2",
            copies=1,
            port="Ne02:",
        )

    assert excel.print_communication == [False, True]
    assert excel.sheet.printed == []


def test_print_workbook_a4_quits_excel_and_releases_com_after_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fiche = tmp_path / "fiche.xlsx"
    fiche.write_bytes(b"xlsx")
    com_calls: list[str] = []
    fake_pythoncom = SimpleNamespace(
        CoInitialize=lambda: com_calls.append("init"),
        CoUninitialize=lambda: com_calls.append("uninit"),
    )
    monkeypatch.setitem(sys.modules, "pythoncom", fake_pythoncom)
    monkeypatch.setattr(printing.sys, "platform", "win32")
    excel = FakeExcel(FakeSheet(fail_print=True))

    with pytest.raises(PrintError, match="n'a pas pu imprimer"):
        printing.print_workbook_a4(
            fiche,
            printer_name="Xerox Bac2",
            app_factory=lambda: excel,
            port_lookup=lambda _name: "Ne02:",
        )

    assert excel.quit_calls == 1
    assert excel.workbook.closed == [{"SaveChanges": False}]
    assert com_calls == ["init", "uninit"]


def test_print_workbook_a4_requires_windows_and_existing_fiche(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(printing.sys, "platform", "darwin")
    with pytest.raises(PrintError, match="Windows"):
        printing.print_workbook_a4(tmp_path / "fiche.xlsx", printer_name="PDF")

    monkeypatch.setattr(printing.sys, "platform", "win32")
    with pytest.raises(PrintError, match="Fiche introuvable"):
        printing.print_workbook_a4(tmp_path / "absente.xlsx", printer_name="PDF")
