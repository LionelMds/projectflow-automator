from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pytest

from projectflow.core.excel_com_repertoire import (
    ExcelAppHandle,
    ExcelComWorkbookGateway,
    _dispatch_excel,
    _find_open_workbook,
    _range_values_to_rows,
    _to_excel_value,
)
from projectflow.exceptions import ConfigError


def test_excel_com_range_values_to_rows_handles_shapes() -> None:
    assert _range_values_to_rows("A", rows=1, columns=1) == [["A"]]
    assert _range_values_to_rows(("A", "B"), rows=1, columns=2) == [["A", "B"]]
    assert _range_values_to_rows((("A",), ("B",)), rows=2, columns=1) == [["A"], ["B"]]
    assert _range_values_to_rows(
        (("A", "B"), ("C", "D")),
        rows=2,
        columns=2,
    ) == [["A", "B"], ["C", "D"]]


def test_excel_com_date_values_are_written_as_datetimes() -> None:
    value = _to_excel_value(date(2026, 5, 11))

    assert value == datetime.combine(date(2026, 5, 11), time.min)


@pytest.mark.asyncio
async def test_excel_com_gateway_uses_open_workbook(tmp_path: Path) -> None:
    workbook_path = tmp_path / "OneDrive - Balz Metal Sa" / "repertoire.xlsx"
    workbook_path.parent.mkdir()
    workbook_path.touch()
    workbook = FakeWorkbook(workbook_path)
    app = FakeExcelApp([workbook])
    gateway = ExcelComWorkbookGateway(
        workbook_path,
        app_factory=lambda: ExcelAppHandle(app=app, created=False),
    )

    async with gateway.session():
        assert await gateway.worksheet_exists("2026") is True
        assert await gateway.used_range_values("2026") == [
            ["Numero", "Client", "Description"],
            ["2026-5100", "", ""],
        ]
        await gateway.update_range_values(
            "2026",
            "A2:E2",
            [["2026-5100", date(2026, 5, 11), "Balz", "Lionel", "Test"]],
        )
        await gateway.insert_blank_row(
            "2026",
            1,
            copy_format_from_row_index=0,
            format_width=5,
        )

    assert workbook.saved is True
    assert workbook.closed is False
    assert app.quit_called is False
    assert app.cut_copy_mode is False
    assert workbook.worksheet.inserted_rows == [2]
    assert workbook.worksheet.ranges["A2:E2"].value[0][0] == "2026-5100"
    assert workbook.worksheet.ranges["A2:E2"].cells[(1, 2)].number_format == "dd.mm.yyyy"


@pytest.mark.asyncio
async def test_excel_com_gateway_opens_and_closes_hidden_workbook(tmp_path: Path) -> None:
    workbook_path = tmp_path / "OneDrive - Balz Metal Sa" / "repertoire.xlsx"
    workbook_path.parent.mkdir()
    workbook_path.touch()
    workbook = FakeWorkbook(workbook_path)
    app = FakeExcelApp([], workbook_to_open=workbook)
    gateway = ExcelComWorkbookGateway(
        workbook_path,
        app_factory=lambda: ExcelAppHandle(app=app, created=True),
    )

    async with gateway.session():
        assert await gateway.worksheet_exists("2026") is True

    assert app.Workbooks.opened_paths == [str(workbook_path)]
    assert workbook.saved is True
    assert workbook.closed is True
    assert app.quit_called is True


@pytest.mark.asyncio
async def test_excel_com_gateway_reports_missing_session_and_sheet(tmp_path: Path) -> None:
    workbook_path = tmp_path / "repertoire.xlsx"
    gateway = ExcelComWorkbookGateway(
        workbook_path,
        app_factory=lambda: ExcelAppHandle(app=FakeExcelApp([]), created=False),
    )

    with pytest.raises(OSError, match="session"):
        gateway._require_workbook()

    workbook = FakeWorkbook(workbook_path)
    app = FakeExcelApp([workbook])
    gateway = ExcelComWorkbookGateway(
        workbook_path,
        app_factory=lambda: ExcelAppHandle(app=app, created=False),
    )
    async with gateway.session():
        with pytest.raises(OSError, match="Onglet introuvable"):
            await gateway.used_range_values("2099")


def test_excel_com_find_open_workbook_handles_unavailable_app(tmp_path: Path) -> None:
    assert _find_open_workbook(object(), tmp_path / "missing.xlsx") is None


def test_excel_com_dispatch_requires_pywin32(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_import(_name: str) -> Any:
        raise ImportError("missing")

    monkeypatch.setattr(
        "projectflow.core.excel_com_repertoire.importlib.import_module",
        fail_import,
    )

    with pytest.raises(ConfigError, match="pywin32"):
        _dispatch_excel()


class FakeExcelApp:
    def __init__(
        self,
        workbooks: list[FakeWorkbook],
        *,
        workbook_to_open: FakeWorkbook | None = None,
    ) -> None:
        self.Workbooks = FakeWorkbooks(workbooks, workbook_to_open=workbook_to_open)
        self.quit_called = False
        self.cut_copy_mode: bool | None = None

    @property
    def CutCopyMode(self) -> bool | None:
        return self.cut_copy_mode

    @CutCopyMode.setter
    def CutCopyMode(self, value: bool) -> None:
        self.cut_copy_mode = value

    def Quit(self) -> None:
        self.quit_called = True


class FakeWorkbooks:
    def __init__(
        self,
        workbooks: list[FakeWorkbook],
        *,
        workbook_to_open: FakeWorkbook | None,
    ) -> None:
        self._workbooks = workbooks
        self._workbook_to_open = workbook_to_open
        self.opened_paths: list[str] = []

    @property
    def Count(self) -> int:
        return len(self._workbooks)

    def Item(self, index: int) -> FakeWorkbook:
        return self._workbooks[index - 1]

    def Open(
        self,
        path: str,
        *,
        UpdateLinks: int,
        ReadOnly: bool,
        AddToMru: bool,
    ) -> FakeWorkbook:
        assert UpdateLinks == 0
        assert ReadOnly is False
        assert AddToMru is False
        self.opened_paths.append(path)
        if self._workbook_to_open is None:
            raise OSError("missing")
        self._workbooks.append(self._workbook_to_open)
        return self._workbook_to_open


class FakeWorkbook:
    def __init__(self, path: Path) -> None:
        self.FullName = str(path)
        self.AutoSaveOn = False
        self.saved = False
        self.closed = False
        self.worksheet = FakeWorksheet()

    def Worksheets(self, name: str) -> FakeWorksheet:
        if name != "2026":
            raise OSError(name)
        return self.worksheet

    def Save(self) -> None:
        self.saved = True

    def Close(self, *, SaveChanges: bool) -> None:
        assert SaveChanges is True
        self.closed = True


class FakeWorksheet:
    def __init__(self) -> None:
        self.UsedRange = FakeUsedRange(rows=2, columns=3)
        self.ranges: dict[str, FakeRange] = {}
        self.inserted_rows: list[int] = []

    def Cells(self, row: int, column: int) -> FakeCell:
        return FakeCell(row, column)

    def Range(self, *args: object) -> FakeRange:
        if len(args) == 1:
            key = str(args[0])
            self.ranges.setdefault(key, FakeRange())
            return self.ranges[key]
        return FakeRange(value=(("Numero", "Client", "Description"), ("2026-5100", "", "")))

    def Rows(self, row: int) -> FakeRows:
        return FakeRows(self, row)


class FakeUsedRange:
    def __init__(self, *, rows: int, columns: int) -> None:
        self.Row = 1
        self.Column = 1
        self.Rows = FakeCount(rows)
        self.Columns = FakeCount(columns)


class FakeCount:
    def __init__(self, count: int) -> None:
        self.Count = count


class FakeRows:
    def __init__(self, worksheet: FakeWorksheet, row: int) -> None:
        self._worksheet = worksheet
        self._row = row

    def Insert(self) -> None:
        self._worksheet.inserted_rows.append(self._row)


class FakeRange:
    def __init__(self, value: object = None) -> None:
        self.value = value
        self.copied = False
        self.pasted_format: int | None = None
        self.cleared = False
        self.cells: dict[tuple[int, int], FakeCell] = {}

    @property
    def Value(self) -> object:
        return self.value

    @Value.setter
    def Value(self, value: object) -> None:
        self.value = value

    def Cells(self, row: int, column: int) -> FakeCell:
        key = (row, column)
        self.cells.setdefault(key, FakeCell(row, column))
        return self.cells[key]

    def Copy(self) -> None:
        self.copied = True

    def PasteSpecial(self, *, Paste: int) -> None:
        self.pasted_format = Paste

    def ClearContents(self) -> None:
        self.cleared = True


class FakeCell:
    def __init__(self, row: int, column: int) -> None:
        self.row = row
        self.column = column
        self.number_format = ""

    @property
    def NumberFormat(self) -> str:
        return self.number_format

    @NumberFormat.setter
    def NumberFormat(self, value: str) -> None:
        self.number_format = value
