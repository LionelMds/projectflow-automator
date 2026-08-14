from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from projectflow.core.duplication import (
    PROJECT_WRITABLE_WIDTH,
    assert_description_empty,
    find_project_row,
    prepare_subproject_row,
    project_info_columns_empty,
)
from projectflow.core.models import ProjectInput
from projectflow.core.numero import ProjectNumber, parse_project_number
from projectflow.exceptions import ProjectCreationError

MAIN_PROJECT_RE = re.compile(r"^(\d{4})-(\d+)$")
PROJECT_ROW_RE = re.compile(r"^\d{4}-\d+(?:-\d+)?$")
# Colonnes A:E = saisie ProjectFlow. Colonnes F:L = donnees comptables intouchables.
REPERTOIRE_TABLE_WIDTH = 12


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

    async def insert_blank_row(
        self,
        worksheet_name: str,
        row_index: int,
        *,
        copy_format_from_row_index: int | None = None,
        format_width: int = REPERTOIRE_TABLE_WIDTH,
    ) -> None:
        """Insert one blank worksheet row and shift every column down."""

    def session(self) -> AbstractAsyncContextManager[None]:
        """Open a workbook session for a related batch of calls."""


@dataclass(frozen=True, slots=True)
class NextAvailableProject:
    number: ProjectNumber
    row_index: int


@dataclass(frozen=True, slots=True)
class RepertoireRow:
    """A safe, editable view of one project row.

    ``row_index`` is zero-based to match the values returned by the workbook
    gateways. Only A:E are exposed to the UI; accounting columns F:L never
    enter this model.
    """

    row_index: int
    values: tuple[Any, ...]

    @property
    def number(self) -> str:
        return _cell_as_text(list(self.values), 0)


@dataclass(frozen=True, slots=True)
class RepertoireSnapshot:
    year: int
    rows: tuple[RepertoireRow, ...]
    next_available: NextAvailableProject | None


class RepertoireService:
    def __init__(self, workbook: WorkbookGateway, today: Callable[[], date] = date.today) -> None:
        self._workbook = workbook
        self._today = today

    async def next_available(self, *, year: int) -> NextAvailableProject | None:
        snapshot = await self.read_snapshot(year=year)
        return snapshot.next_available

    async def read_snapshot(self, *, year: int) -> RepertoireSnapshot:
        async with self._workbook.session():
            worksheet_name = str(year)
            await self._assert_worksheet_exists(worksheet_name)
            rows = await self._workbook.used_range_values(worksheet_name)
            next_available = _find_next_available(rows)
            display_rows = tuple(
                RepertoireRow(
                    row_index=index,
                    values=tuple(
                        _ensure_width(list(row), width=PROJECT_WRITABLE_WIDTH)[
                            :PROJECT_WRITABLE_WIDTH
                        ]
                    ),
                )
                for index, row in enumerate(rows)
                if PROJECT_ROW_RE.fullmatch(_cell_as_text(row, 0))
            )
            return RepertoireSnapshot(
                year=year,
                rows=display_rows,
                next_available=next_available,
            )

    async def update_editable_row(
        self,
        *,
        year: int,
        row_index: int,
        values: Sequence[Any],
        expected_values: Sequence[Any] | None = None,
    ) -> None:
        """Update one displayed row without touching columns F:L.

        The expected values protect against silently overwriting a concurrent
        edit made in the shared workbook.
        """
        async with self._workbook.session():
            worksheet_name = str(year)
            await self._assert_worksheet_exists(worksheet_name)
            rows = await self._workbook.used_range_values(worksheet_name)
            if row_index < 0 or row_index >= len(rows):
                raise ProjectCreationError("La ligne du repertoire n'existe plus.")

            current = _ensure_width(list(rows[row_index]), width=PROJECT_WRITABLE_WIDTH)[
                :PROJECT_WRITABLE_WIDTH
            ]
            edited = _ensure_width(list(values), width=PROJECT_WRITABLE_WIDTH)[
                :PROJECT_WRITABLE_WIDTH
            ]
            if _cell_as_text(edited, 0) != _cell_as_text(current, 0):
                raise ProjectCreationError("Le numero du projet ne peut pas etre modifie ici.")
            if expected_values is not None:
                expected = _ensure_width(list(expected_values), width=PROJECT_WRITABLE_WIDTH)[
                    :PROJECT_WRITABLE_WIDTH
                ]
                if not all(
                    _same_cell(actual, wanted)
                    for actual, wanted in zip(current, expected, strict=True)
                ):
                    raise ProjectCreationError(
                        "La ligne a ete modifiee dans le fichier partage. "
                        "Actualisez le repertoire avant de recommencer.",
                    )

            await self._workbook.update_range_values(
                worksheet_name,
                _row_address(row_index, width=PROJECT_WRITABLE_WIDTH),
                [edited],
            )

    async def validate_project_deletion(
        self,
        *,
        number: ProjectNumber,
        rows: Sequence[RepertoireRow],
    ) -> None:
        async with self._workbook.session():
            worksheet_name = str(number.year)
            await self._assert_worksheet_exists(worksheet_name)
            current = await self._workbook.used_range_values(worksheet_name)
            _validate_deletion_rows(current, number=number, expected_rows=rows)

    async def clear_project_rows(
        self,
        *,
        number: ProjectNumber,
        rows: Sequence[RepertoireRow],
    ) -> None:
        async with self._workbook.session():
            worksheet_name = str(number.year)
            await self._assert_worksheet_exists(worksheet_name)
            current = await self._workbook.used_range_values(worksheet_name)
            _validate_deletion_rows(current, number=number, expected_rows=rows)
            for row in rows:
                excel_row = row.row_index + 1
                await self._workbook.update_range_values(
                    worksheet_name,
                    f"B{excel_row}:E{excel_row}",
                    [["", "", "", ""]],
                )

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

            row = _ensure_width(rows[row_index], width=PROJECT_WRITABLE_WIDTH)
            assert_description_empty(row, force_overwrite=force_overwrite)
            updated_row = _apply_project_to_row(
                row[:PROJECT_WRITABLE_WIDTH],
                project,
                created_on=self._today(),
            )
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
        existing_index = find_project_row(rows, project.number)
        if existing_index is not None:
            updated_row = _project_values(
                project,
                width=PROJECT_WRITABLE_WIDTH,
                created_on=self._today(),
            )
            await self._workbook.update_range_values(
                worksheet_name,
                _row_address(existing_index, width=len(updated_row)),
                [updated_row],
            )
            return

        insert_index, blank_row = prepare_subproject_row(rows, project.number)
        updated_row = _project_values(
            project,
            width=len(blank_row),
            created_on=self._today(),
        )
        format_source_index = _first_available_main_project_row_index(rows)

        await self._workbook.insert_blank_row(
            worksheet_name,
            insert_index,
            copy_format_from_row_index=format_source_index,
            format_width=REPERTOIRE_TABLE_WIDTH,
        )
        await self._workbook.update_range_values(
            worksheet_name,
            _row_address(insert_index, width=len(updated_row)),
            [updated_row],
        )

    async def _assert_worksheet_exists(self, worksheet_name: str) -> None:
        if not await self._workbook.worksheet_exists(worksheet_name):
            raise ProjectCreationError(f"Onglet introuvable dans le repertoire: {worksheet_name}")


def _apply_project_to_row(row: list[Any], project: ProjectInput, *, created_on: date) -> list[Any]:
    updated = _ensure_width(list(row), width=PROJECT_WRITABLE_WIDTH)
    updated[0] = str(project.number)
    updated[1] = created_on
    _write_if_non_empty(updated, 2, project.societe)
    _write_if_non_empty(updated, 3, project.contact)
    _write_if_non_empty(updated, 4, project.designation)
    return updated


def _project_values(project: ProjectInput, *, width: int, created_on: date) -> list[Any]:
    updated: list[Any] = [""] * max(width, PROJECT_WRITABLE_WIDTH)
    updated[0] = str(project.number)
    updated[1] = created_on
    updated[2] = project.societe.strip()
    updated[3] = project.contact.strip()
    updated[4] = project.designation.strip()
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


def _first_available_main_project_row_index(rows: list[list[Any]]) -> int | None:
    for index, row in enumerate(rows):
        number = _cell_as_text(row, 0)
        if MAIN_PROJECT_RE.fullmatch(number) and project_info_columns_empty(row):
            return index
    return None


def _find_next_available(rows: list[list[Any]]) -> NextAvailableProject | None:
    for index, row in enumerate(rows):
        number = _cell_as_text(row, 0)
        if MAIN_PROJECT_RE.fullmatch(number) and project_info_columns_empty(row):
            return NextAvailableProject(
                number=parse_project_number(number),
                row_index=index,
            )
    return None


def _same_cell(left: object, right: object) -> bool:
    return _cell_text_value(left) == _cell_text_value(right)


def _validate_deletion_rows(
    current_rows: list[list[Any]],
    *,
    number: ProjectNumber,
    expected_rows: Sequence[RepertoireRow],
) -> None:
    if not expected_rows:
        raise ProjectCreationError("Aucune ligne de repertoire a supprimer.")
    expected_group = {row.row_index: row.number for row in expected_rows}
    current_group = {
        index: _cell_as_text(row, 0)
        for index, row in enumerate(current_rows)
        if _belongs_to_deletion_group(_cell_as_text(row, 0), number)
    }
    if current_group != expected_group:
        raise ProjectCreationError(
            "Le groupe de projet a change dans le fichier partage. "
            "Actualisez le repertoire avant de supprimer.",
        )
    for expected in expected_rows:
        if expected.row_index < 0 or expected.row_index >= len(current_rows):
            raise ProjectCreationError("Une ligne du projet n'existe plus dans le repertoire.")
        current = _ensure_width(
            list(current_rows[expected.row_index]),
            width=PROJECT_WRITABLE_WIDTH,
        )[:PROJECT_WRITABLE_WIDTH]
        wanted = _ensure_width(list(expected.values), width=PROJECT_WRITABLE_WIDTH)[
            :PROJECT_WRITABLE_WIDTH
        ]
        if not all(
            _same_cell(actual, value) for actual, value in zip(current, wanted, strict=True)
        ):
            raise ProjectCreationError(
                "Une ligne du projet a ete modifiee dans le fichier partage. "
                "Actualisez le repertoire avant de supprimer.",
            )


def _belongs_to_deletion_group(value: str, number: ProjectNumber) -> bool:
    if number.is_subproject:
        return value == str(number)
    return value == str(number) or value.startswith(f"{number}-")


def _cell_text_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return str(value).strip()


@asynccontextmanager
async def null_workbook_session() -> AsyncIterator[None]:
    yield
