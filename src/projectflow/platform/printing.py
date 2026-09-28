from __future__ import annotations

import contextlib
import importlib
import re
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from projectflow.exceptions import PrintError

XL_PAPER_A4: Final[int] = 9
DEVICES_KEY: Final[str] = r"Software\Microsoft\Windows NT\CurrentVersion\Devices"
# Excel names printers "<name> <connector> <port>", with a connector translated in
# the Office language ("on", "sur", "auf"...). The current printer reveals it.
ACTIVE_PRINTER_RE = re.compile(r"^.+ (?P<connector>\S+) (?P<port>\S+)$")
FALLBACK_CONNECTORS: Final[tuple[str, ...]] = ("on", "sur", "auf", "su", "en", "em", "op")

ExcelAppFactory = Callable[[], Any]
PrinterPortLookup = Callable[[str], str | None]


def print_workbook_a4(
    workbook_path: Path,
    *,
    printer_name: str,
    copies: int = 1,
    app_factory: ExcelAppFactory | None = None,
    port_lookup: PrinterPortLookup | None = None,
) -> None:
    """Print the active sheet on a single A4 page with a private Excel instance."""
    if not sys.platform.startswith("win"):
        raise PrintError("L'impression de la fiche necessite Microsoft Excel sous Windows.")
    if not workbook_path.exists():
        raise PrintError(f"Fiche introuvable: {workbook_path}")
    pythoncom = importlib.import_module("pythoncom")
    # Called from a worker thread: COM must be initialized for this thread.
    pythoncom.CoInitialize()
    try:
        _print_with_private_excel(
            workbook_path,
            printer_name=printer_name,
            copies=copies,
            app_factory=app_factory or _dispatch_excel,
            port=(port_lookup or printer_port)(printer_name),
        )
    except BaseException as exc:
        # Traceback frames still hold Excel proxies. Releasing them after
        # CoUninitialize would leave a hidden Excel process running.
        traceback.clear_frames(exc.__traceback__)
        if exc.__cause__ is not None:
            traceback.clear_frames(exc.__cause__.__traceback__)
        raise
    finally:
        pythoncom.CoUninitialize()


def _print_with_private_excel(
    workbook_path: Path,
    *,
    printer_name: str,
    copies: int,
    app_factory: ExcelAppFactory,
    port: str | None,
) -> None:
    excel = app_factory()
    try:
        print_with_excel(
            excel,
            workbook_path,
            printer_name=printer_name,
            copies=copies,
            port=port,
        )
    except _com_error_type() as exc:
        raise PrintError(f"Excel n'a pas pu imprimer la fiche sur {printer_name}.") from exc
    finally:
        _quit_excel(excel)


def print_with_excel(
    excel: Any,
    workbook_path: Path,
    *,
    printer_name: str,
    copies: int,
    port: str | None,
) -> None:
    """Open the workbook read-only so the A4 page setup never reaches the saved file."""
    com_error = _com_error_type()
    excel.Visible = False
    excel.DisplayAlerts = False
    try:
        workbook = excel.Workbooks.Open(
            str(workbook_path),
            UpdateLinks=0,
            ReadOnly=True,
            IgnoreReadOnlyRecommended=True,
            AddToMru=False,
        )
    except com_error as exc:
        raise PrintError(f"Excel ne peut pas ouvrir la fiche {workbook_path.name}.") from exc
    try:
        # Excel only accepts a printer change once a workbook is open.
        select_excel_printer(excel, printer_name, port=port)
        sheet = workbook.ActiveSheet
        try:
            _force_single_a4_page(excel, sheet.PageSetup)
        except com_error as exc:
            raise PrintError(f"L'imprimante {printer_name} refuse le format A4.") from exc
        try:
            # Positional arguments: Excel rejects some keyword combinations of PrintOut.
            # From, To, Copies, Preview, ActivePrinter, PrintToFile, Collate.
            sheet.PrintOut(None, None, max(1, copies), False, None, False, True)  # noqa: FBT003
        except com_error as exc:
            raise PrintError(f"Excel n'a pas pu imprimer la fiche sur {printer_name}.") from exc
    finally:
        with contextlib.suppress(com_error):
            workbook.Close(SaveChanges=False)


def select_excel_printer(excel: Any, printer_name: str, *, port: str | None) -> None:
    com_error = _com_error_type()
    current = _text(excel.ActivePrinter)
    for candidate in excel_printer_names(printer_name, port=port, current=current):
        try:
            excel.ActivePrinter = candidate
        except com_error:
            continue
        return
    raise PrintError(f"Excel ne trouve pas l'imprimante {printer_name}.")


def excel_printer_names(printer_name: str, *, port: str | None, current: str) -> list[str]:
    if not port:
        return [printer_name]
    connectors: list[str] = []
    match = ACTIVE_PRINTER_RE.fullmatch(current.strip())
    if match is not None:
        connectors.append(match["connector"])
    connectors.extend(connector for connector in FALLBACK_CONNECTORS if connector not in connectors)
    return [f"{printer_name} {connector} {port}" for connector in connectors] + [printer_name]


def printer_port(printer_name: str) -> str | None:
    """Return the spooler port Excel expects, e.g. ``Ne03:`` for ``winspool,Ne03:``."""
    try:
        winreg: Any = importlib.import_module("winreg")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, DEVICES_KEY) as key:
            value, _value_type = winreg.QueryValueEx(key, printer_name)
    except (ImportError, OSError):
        return None
    _driver, _separator, ports = str(value).partition(",")
    port = ports.split(",")[0].strip()
    return port or None


def _force_single_a4_page(excel: Any, page_setup: Any) -> None:
    # Group the driver round-trips; each PageSetup property is otherwise sent separately.
    excel.PrintCommunication = False
    try:
        page_setup.PaperSize = XL_PAPER_A4
        page_setup.Zoom = False
        page_setup.FitToPagesWide = 1
        page_setup.FitToPagesTall = 1
    finally:
        excel.PrintCommunication = True


def _dispatch_excel() -> Any:
    try:
        win32_client = importlib.import_module("win32com.client")
    except ImportError as exc:
        raise PrintError("pywin32 est requis pour imprimer la fiche avec Excel.") from exc
    try:
        # A separate instance leaves the user's open Excel windows and printer untouched.
        return win32_client.DispatchEx("Excel.Application")
    except (_com_error_type(), AttributeError, RuntimeError, OSError) as exc:
        raise PrintError("Microsoft Excel est requis pour imprimer la fiche.") from exc


def _quit_excel(excel: Any) -> None:
    try:
        excel.Quit()
    except (_com_error_type(), AttributeError, RuntimeError, OSError):
        pass


def _com_error_type() -> type[BaseException]:
    try:
        pywintypes = importlib.import_module("pywintypes")
    except ImportError:
        return OSError
    error_type = getattr(pywintypes, "com_error", OSError)
    if isinstance(error_type, type) and issubclass(error_type, BaseException):
        return error_type
    return OSError


def _text(value: object) -> str:
    return "" if value is None else str(value)
