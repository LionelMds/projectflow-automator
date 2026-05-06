from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from projectflow.core.duplication import (
    assert_description_empty,
    duplicate_subproject_row,
    find_project_row,
)
from projectflow.core.models import ProjectInput
from projectflow.core.numero import ProjectNumber, parse_project_number
from projectflow.exceptions import ProjectCreationError

MAIN_PROJECT_RE = re.compile(r"^(\d{4})-(\d+)$")


class WorkbookGateway(Protocol):
    async def worksheet_exists(self, worksheet_name: str) -> bool:
        """Return whether a worksheet exists."""

    async def used_range_values(self, worksheet_name: str) -> list[list[Any]]:
        """Return worksheet used range values."""

    async def update_range_values(
        self,
        worksheet_name: str,
        address: str,
        values: list[list[Any]],
    ) -> None:
        """Update a worksheet range."""

    async def insert_range(
        self,
        worksheet_name: str,
        address: str,
        *,
        shift: str = "Down",
    ) -> None:
        """Insert a worksheet range and shift existing cells."""

    async def list_tables(self, worksheet_name: str) -> list[dict[str, Any]]:
        """Return structured tables for a worksheet."""

    async def add_table_row(
        self,
        table_id_or_name: str,
        values: list[Any],
        *,
        index: int | None = None,
    ) -> None:
        """Append one row to a structured table."""

    def session(self) -> AbstractAsyncContextManager[None]:
        """Open a workbook session for a related batch of calls."""


@dataclass(frozen=True, slots=True)
class NextAvailableProject:
    number: ProjectNumber
    row_index: int


class RepertoireService:
    def __init__(self, workbook: WorkbookGateway) -> None:
        self._workbook = workbook

    async def next_available(self, *, year: int) -> NextAvailableProject | None:
        async with self._workbook.session():
            worksheet_name = str(year)
            await self._assert_worksheet_exists(worksheet_name)
            rows = await self._workbook.used_range_values(worksheet_name)
            for index, row in enumerate(rows):
                number = _cell_as_text(row, 0)
                description = _cell_as_text(row, 4)
                if MAIN_PROJECT_RE.fullmatch(number) and description == "":
                    return NextAvailableProject(
                        number=parse_project_number(number),
                        row_index=index,
                    )
            return None

    async def upsert_project(
        self,
        project: ProjectInput,
        *,
        force_overwrite: bool = False,
    ) -> None:
        async with self._workbook.session():
            worksheet_name = str(project.number.year)
            await self._assert_worksheet_exists(worksheet_name)
            rows = await self._workbook.used_range_values(worksheet_name)

            if project.number.is_subproject:
                await self._insert_subproject(project, rows=rows, worksheet_name=worksheet_name)
                return

            row_index = find_project_row(rows, project.number)
            if row_index is None:
                message = f"Projet introuvable dans le repertoire: {project.number}"
                raise ProjectCreationError(message)

            row = _ensure_width(rows[row_index], width=5)
            assert_description_empty(row, force_overwrite=force_overwrite)
            updated_row = _apply_project_to_row(row[:5], project)
            await self._workbook.update_range_values(
                worksheet_name,
                _row_address(row_index, width=len(updated_row)),
                [updated_row],
            )

    async def _insert_subproject(
        self,
        project: ProjectInput,
        *,
        rows: list[list[Any]],
        worksheet_name: str,
    ) -> None:
        tables = await self._workbook.list_tables(worksheet_name)
        insert_index, duplicated_row = duplicate_subproject_row(rows, project.number)
        updated_row = _apply_project_to_row(_ensure_width(duplicated_row, width=6), project)

        if tables:
            table_id = _table_identifier(tables[0])
            await self._workbook.add_table_row(
                table_id,
                updated_row,
                index=max(insert_index - 1, 0),
            )
            return

        insert_address = _row_address(insert_index, width=len(updated_row))
        await self._workbook.insert_range(worksheet_name, insert_address, shift="Down")
        await self._workbook.update_range_values(
            worksheet_name,
            insert_address,
            [updated_row],
        )

    async def _assert_worksheet_exists(self, worksheet_name: str) -> None:
        if not await self._workbook.worksheet_exists(worksheet_name):
            raise ProjectCreationError(f"Onglet introuvable dans le repertoire: {worksheet_name}")


def _apply_project_to_row(row: list[Any], project: ProjectInput) -> list[Any]:
    updated = _ensure_width(list(row), width=5)
    updated[0] = str(project.number)
    _write_if_non_empty(updated, 1, project.societe)
    _write_if_non_empty(updated, 2, project.contact)
    _write_if_non_empty(updated, 3, project.localisation)
    _write_if_non_empty(updated, 4, project.designation)
    return updated


def _write_if_non_empty(row: list[Any], index: int, value: str) -> None:
    if value.strip():
        row[index] = value.strip()


def _ensure_width(row: list[Any], *, width: int) -> list[Any]:
    if len(row) < width:
        row.extend([""] * (width - len(row)))
    return row


def _row_address(row_index: int, *, width: int) -> str:
    return f"A{row_index + 1}:{_excel_column_name(width)}{row_index + 1}"


def _excel_column_name(width: int) -> str:
    name = ""
    number = width
    while number:
        number, remainder = divmod(number - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _cell_as_text(row: list[Any], column: int) -> str:
    if len(row) <= column:
        return ""
    value = row[column]
    return "" if value is None else str(value).strip()


def _table_identifier(table: dict[str, Any]) -> str:
    raw_id = table.get("id") or table.get("name")
    if not isinstance(raw_id, str) or raw_id == "":
        raise ProjectCreationError("Tableau structure sans identifiant.")
    return raw_id


@asynccontextmanager
async def null_workbook_session() -> AsyncIterator[None]:
    yield
