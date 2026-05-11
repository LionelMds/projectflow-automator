from __future__ import annotations

import importlib
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from projectflow.exceptions import ConfigError

XL_PASTE_FORMATS = -4122


@dataclass(frozen=True, slots=True)
class ExcelAppHandle:
    app: Any
    created: bool


ExcelAppFactory = Callable[[], ExcelAppHandle]


class ExcelComWorkbookGateway:
    """Workbook gateway using local Microsoft Excel on Windows."""

    def __init__(
        self,
        workbook_path: Path,
        *,
        app_factory: ExcelAppFactory | None = None,
    ) -> None:
        self._workbook_path = workbook_path
        self._app_factory = app_factory or _dispatch_excel
        self._app: Any | None = None
        self._workbook: Any | None = None
        self._close_workbook = False
        self._quit_app = False

    def session(self) -> AbstractAsyncContextManager[None]:
        return self._session()

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[None]:
        if self._workbook is not None:
            yield
            return

        app_handle = self._app_factory()
        app = app_handle.app
        workbook = _find_open_workbook(app, self._workbook_path)
        close_workbook = workbook is None
        if workbook is None:
            workbook = _open_workbook(app, self._workbook_path)
        self._app = app
        self._workbook = workbook
        self._close_workbook = close_workbook
        self._quit_app = app_handle.created
        try:
            yield
            _save_workbook(workbook)
        except _com_error_type() as exc:
            raise OSError("Excel n'a pas pu synchroniser le repertoire chantier.") from exc
        finally:
            try:
                if self._close_workbook:
                    workbook.Close(SaveChanges=True)
                if self._quit_app:
                    app.Quit()
            except (_com_error_type(), AttributeError, RuntimeError, OSError):
                pass
            self._app = None
            self._workbook = None
            self._close_workbook = False
            self._quit_app = False

    async def worksheet_exists(self, worksheet_name: str) -> bool:
        return _worksheet_by_name(self._require_workbook(), worksheet_name) is not None

    async def used_range_values(self, worksheet_name: str) -> list[list[Any]]:
        worksheet = self._require_worksheet(worksheet_name)
        used_range = worksheet.UsedRange
        max_row = int(used_range.Row) + int(used_range.Rows.Count) - 1
        max_col = int(used_range.Column) + int(used_range.Columns.Count) - 1
        if max_row < 1 or max_col < 1:
            return []
        value_range = worksheet.Range(
            worksheet.Cells(1, 1),
            worksheet.Cells(max_row, max_col),
        )
        return _range_values_to_rows(value_range.Value, rows=max_row, columns=max_col)

    async def update_range_values(
        self,
        worksheet_name: str,
        address: str,
        values: list[list[Any]],
    ) -> None:
        worksheet = self._require_worksheet(worksheet_name)
        target = worksheet.Range(address)
        target.Value = tuple(tuple(_to_excel_value(value) for value in row) for row in values)
        for row_offset, row_values in enumerate(values, start=1):
            for column_offset, value in enumerate(row_values, start=1):
                if isinstance(value, date):
                    target.Cells(row_offset, column_offset).NumberFormat = "dd.mm.yyyy"

    async def insert_blank_row(
        self,
        worksheet_name: str,
        row_index: int,
        *,
        copy_format_from_row_index: int | None = None,
        format_width: int = 12,
    ) -> None:
        worksheet = self._require_worksheet(worksheet_name)
        target_row = row_index + 1
        worksheet.Rows(target_row).Insert()
        if copy_format_from_row_index is not None:
            source_row = copy_format_from_row_index + 1
            source = worksheet.Range(
                worksheet.Cells(source_row, 1),
                worksheet.Cells(source_row, format_width),
            )
            destination = worksheet.Range(
                worksheet.Cells(target_row, 1),
                worksheet.Cells(target_row, format_width),
            )
            source.Copy()
            destination.PasteSpecial(Paste=XL_PASTE_FORMATS)
            if self._app is not None:
                self._app.CutCopyMode = False
        worksheet.Range(
            worksheet.Cells(target_row, 1),
            worksheet.Cells(target_row, format_width),
        ).ClearContents()

    def _require_workbook(self) -> Any:
        if self._workbook is None:
            raise OSError("Le repertoire Excel local n'est pas ouvert en session.")
        return self._workbook

    def _require_worksheet(self, worksheet_name: str) -> Any:
        worksheet = _worksheet_by_name(self._require_workbook(), worksheet_name)
        if worksheet is None:
            raise OSError(f"Onglet introuvable dans le repertoire: {worksheet_name}")
        return worksheet


def _dispatch_excel() -> ExcelAppHandle:
    try:
        pythoncom = importlib.import_module("pythoncom")
        win32_client = importlib.import_module("win32com.client")
    except ImportError as exc:
        raise ConfigError(
            "pywin32 est requis pour piloter Excel localement sur Windows.",
        ) from exc

    pythoncom.CoInitialize()
    try:
        app = win32_client.GetActiveObject("Excel.Application")
        return ExcelAppHandle(app=app, created=False)
    except (_com_error_type(), AttributeError, RuntimeError, OSError):
        app = win32_client.DispatchEx("Excel.Application")
        app.Visible = False
        app.DisplayAlerts = False
        return ExcelAppHandle(app=app, created=True)


def _open_workbook(app: Any, workbook_path: Path) -> Any:
    try:
        workbook = app.Workbooks.Open(
            str(workbook_path),
            UpdateLinks=0,
            ReadOnly=False,
            AddToMru=False,
        )
    except (_com_error_type(), AttributeError, RuntimeError, OSError) as exc:
        raise OSError(
            "Excel n'a pas pu ouvrir le repertoire chantier. "
            "Fermez les copies non fusionnees puis reessayez.",
        ) from exc
    _enable_autosave(workbook)
    return workbook


def _save_workbook(workbook: Any) -> None:
    _enable_autosave(workbook)
    workbook.Save()


def _enable_autosave(workbook: Any) -> None:
    try:
        workbook.AutoSaveOn = True
    except (_com_error_type(), AttributeError, RuntimeError, OSError):
        pass


def _find_open_workbook(app: Any, workbook_path: Path) -> Any | None:
    wanted = _normalized_path(workbook_path)
    try:
        workbooks = app.Workbooks
        for index in range(1, int(workbooks.Count) + 1):
            workbook = workbooks.Item(index)
            if _normalized_path(Path(str(workbook.FullName))) == wanted:
                _enable_autosave(workbook)
                return workbook
    except (_com_error_type(), AttributeError, RuntimeError, OSError):
        return None
    return None


def _worksheet_by_name(workbook: Any, worksheet_name: str) -> Any | None:
    try:
        return workbook.Worksheets(worksheet_name)
    except (_com_error_type(), AttributeError, RuntimeError, OSError):
        return None


def _range_values_to_rows(value: Any, *, rows: int, columns: int) -> list[list[Any]]:
    if rows <= 0 or columns <= 0:
        return []
    if rows == 1 and columns == 1:
        return [[value]]
    if rows == 1:
        row = value if isinstance(value, tuple) else (value,)
        return [list(row)]
    if columns == 1:
        return [[item[0] if isinstance(item, tuple) else item] for item in value]
    return [list(row) for row in value]


def _to_excel_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    return value


def _normalized_path(path: Path) -> str:
    return str(path.expanduser().resolve()).casefold()


def _com_error_type() -> type[BaseException]:
    try:
        pywintypes = importlib.import_module("pywintypes")
    except ImportError:
        return OSError
    error_type = getattr(pywintypes, "com_error", OSError)
    if isinstance(error_type, type) and issubclass(error_type, BaseException):
        return error_type
    return OSError
