from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from projectflow.core.project_years import ProjectYearIndex, TypedNumber, split_typed_number
from projectflow.core.repertoire_service import RepertoireService
from projectflow.exceptions import ProjectNumberError


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5102", TypedNumber(None, "5102")),
        (" 5102-2 ", TypedNumber(None, "5102", "2")),
        ("4493 a1", TypedNumber(None, "4493", "A1")),
        ("2025-4493", TypedNumber(2025, "4493")),
        ("2024 - 4493-02", TypedNumber(2024, "4493", "2")),
        # A number in 2025 with a sub-project stays a number: no year has a 1-digit id.
        ("2025-2", TypedNumber(None, "2025", "2")),
        ("", TypedNumber(None, "")),
    ],
)
def test_split_typed_number(text: str, expected: TypedNumber) -> None:
    assert split_typed_number(text) == expected


def test_year_of_a_number_read_in_the_repertoire() -> None:
    index = ProjectYearIndex()
    index.learn(2025, ["2025-4800", "2025-4801-2", "2025-5050"])
    index.learn(2026, ["2026-5051", "2026-5300"])

    assert index.year_of("4801") == 2025
    assert index.year_of("5300") == 2026
    # Between the first and last numbers of a sheet: the numbers are consecutive.
    assert index.year_of("4900") == 2025
    assert index.year_of("5200") == 2026
    assert index.year_of("9999") is None
    assert index.resolve("5102-2").__str__() == "2026-5102-2"


def test_year_of_a_number_from_the_project_folders(tmp_path: Path) -> None:
    (tmp_path / "2023" / "2023-4120").mkdir(parents=True)
    (tmp_path / "Archives").mkdir()
    index = ProjectYearIndex()
    index.set_projects_root(tmp_path)

    assert index.year_of("4120") == 2023
    assert index.year_of("4121") is None


def test_a_typed_year_is_kept() -> None:
    index = ProjectYearIndex()
    index.learn(2026, ["2026-5051", "2026-5300"])

    assert str(index.resolve("2025-5100")) == "2025-5100"


def test_an_unknown_number_asks_for_its_year() -> None:
    with pytest.raises(ProjectNumberError, match="20YY-7777"):
        ProjectYearIndex().resolve("7777")


class SheetsWorkbook:
    def __init__(self, sheets: dict[str, list[list[Any]]]) -> None:
        self.sheets = sheets
        self.read: list[str] = []

    def session(self) -> AbstractAsyncContextManager[None]:
        @asynccontextmanager
        async def noop() -> AsyncIterator[None]:
            yield

        return noop()

    async def worksheet_exists(self, worksheet_name: str) -> bool:
        return worksheet_name in self.sheets

    async def worksheet_names(self) -> list[str]:
        return list(self.sheets)

    async def used_range_values(self, worksheet_name: str) -> list[list[Any]]:
        self.read.append(worksheet_name)
        return self.sheets[worksheet_name]

    async def update_range_values(self, *_args: Any) -> None:
        raise AssertionError

    async def insert_blank_row(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError


FULL = [["2025-4999", date(2025, 3, 1), "Soc", "Contact", "Projet"]]
FREE_2025 = [*FULL, ["2025-5000", "", "", "", ""]]
FREE_2026 = [["2026-5100", date(2026, 1, 5), "Soc", "", "Projet"], ["2026-5101", "", "", "", ""]]


@pytest.mark.asyncio
async def test_list_years_keeps_the_year_sheets_only() -> None:
    workbook = SheetsWorkbook({"2024": [], "Clients": [], "2026": [], "2025": []})

    assert await RepertoireService(workbook).list_years() == (2026, 2025, 2024)


@pytest.mark.asyncio
async def test_current_next_available_takes_this_year_even_with_free_rows_before() -> None:
    workbook = SheetsWorkbook({"2025": FREE_2025, "2026": FREE_2026})
    service = RepertoireService(workbook, today=lambda: date(2026, 11, 20))

    snapshot = await service.current_next_available()

    assert snapshot is not None
    assert snapshot.next_available is not None
    assert str(snapshot.next_available.number) == "2026-5101"
    assert workbook.read == ["2026"]


@pytest.mark.asyncio
async def test_current_next_available_in_january_before_the_new_sheet() -> None:
    workbook = SheetsWorkbook({"2024": FREE_2025, "2025": FREE_2025})
    service = RepertoireService(workbook, today=lambda: date(2026, 1, 3))

    snapshot = await service.current_next_available()

    assert snapshot is not None
    assert snapshot.year == 2025


@pytest.mark.asyncio
async def test_current_next_available_never_goes_back_to_an_older_sheet() -> None:
    workbook = SheetsWorkbook({"2025": FREE_2025, "2026": [FREE_2026[0]]})
    service = RepertoireService(workbook, today=lambda: date(2026, 11, 20))

    assert await service.current_next_available() is None
