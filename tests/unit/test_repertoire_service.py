from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from projectflow.core.models import ProjectInput
from projectflow.core.numero import parse_project_number
from projectflow.core.repertoire_service import RepertoireService
from projectflow.exceptions import ProjectCreationError


class FakeWorkbook:
    def __init__(
        self,
        rows: list[list[Any]],
        *,
        tables: list[dict[str, Any]] | None = None,
    ) -> None:
        self.rows = rows
        self.tables = tables or []
        self.updated_ranges: list[tuple[str, str, list[list[Any]]]] = []
        self.inserted_ranges: list[tuple[str, str, str]] = []
        self.table_rows: list[tuple[str, list[Any]]] = []
        self.session_count = 0

    @asynccontextmanager
    async def session(self) -> AsyncIterator[None]:
        self.session_count += 1
        yield

    async def worksheet_exists(self, worksheet_name: str) -> bool:
        return worksheet_name == "2026"

    async def used_range_values(self, worksheet_name: str) -> list[list[Any]]:
        return self.rows

    async def update_range_values(
        self,
        worksheet_name: str,
        address: str,
        values: list[list[Any]],
    ) -> None:
        self.updated_ranges.append((worksheet_name, address, values))

    async def insert_range(
        self,
        worksheet_name: str,
        address: str,
        *,
        shift: str = "Down",
    ) -> None:
        self.inserted_ranges.append((worksheet_name, address, shift))

    async def list_tables(self, worksheet_name: str) -> list[dict[str, Any]]:
        return self.tables

    async def add_table_row(
        self,
        table_id_or_name: str,
        values: list[Any],
        *,
        index: int | None = None,
    ) -> None:
        self.table_rows.append((table_id_or_name, [index, *values]))


@pytest.mark.asyncio
async def test_next_available_returns_first_main_project_with_empty_description() -> None:
    workbook = FakeWorkbook([
        ["2026-4995", "", "", "", "Occupe"],
        ["2026-4996", "", "", "", ""],
        ["2026-4996-1", "", "", "", ""],
    ])

    result = await RepertoireService(workbook).next_available(year=2026)

    assert result is not None
    assert str(result.number) == "2026-4996"
    assert result.row_index == 1
    assert workbook.session_count == 1


@pytest.mark.asyncio
async def test_upsert_project_updates_existing_empty_row() -> None:
    workbook = FakeWorkbook([["2026-4995", "", "", "", "", ""]])
    project = ProjectInput(
        number=parse_project_number("2026-4995"),
        designation="Escalier",
        societe="Balz",
        contact="Lionel",
        localisation="Zurich",
        gere_par="LM",
    )

    await RepertoireService(workbook).upsert_project(project)

    assert workbook.updated_ranges == [
        ("2026", "A1:F1", [["2026-4995", "Balz", "Lionel", "Zurich", "Escalier", "LM"]]),
    ]


@pytest.mark.asyncio
async def test_upsert_project_rejects_filled_description_without_force() -> None:
    workbook = FakeWorkbook([["2026-4995", "", "", "", "Occupe", ""]])
    project = ProjectInput(number=parse_project_number("2026-4995"), designation="Escalier")

    with pytest.raises(ProjectCreationError):
        await RepertoireService(workbook).upsert_project(project)


@pytest.mark.asyncio
async def test_upsert_subproject_appends_to_structured_table_when_present() -> None:
    workbook = FakeWorkbook(
        [["2026-4995", "Balz", "", "", "Escalier", "LM"]],
        tables=[{"id": "table-1"}],
    )
    project = ProjectInput(number=parse_project_number("2026-4995-2"), designation="Variante")

    await RepertoireService(workbook).upsert_project(project)

    assert workbook.table_rows[0][0] == "table-1"
    assert workbook.table_rows[0][1][0] == 0
    assert workbook.table_rows[0][1][1] == "2026-4995-2"
    assert workbook.table_rows[0][1][5] == "Variante"


@pytest.mark.asyncio
async def test_upsert_subproject_inserts_range_before_writing_without_table() -> None:
    workbook = FakeWorkbook([
        ["2026-4995", "Balz", "", "", "Escalier", "LM"],
        ["2026-5000", "", "", "", "", ""],
    ])
    project = ProjectInput(number=parse_project_number("2026-4995-2"), designation="Variante")

    await RepertoireService(workbook).upsert_project(project)

    assert workbook.inserted_ranges == [("2026", "A2:F2", "Down")]
    assert workbook.updated_ranges == [
        ("2026", "A2:F2", [["2026-4995-2", "Balz", "", "", "Variante", "LM"]]),
    ]
