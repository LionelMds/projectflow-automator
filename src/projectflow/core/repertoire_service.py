from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime
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
from projectflow.core.repertoire_queue import RepertoireTransactionStore
from projectflow.exceptions import ProjectCreationError

MAIN_PROJECT_RE = re.compile(r"^(\d{4})-(\d+)$")
# Colonnes A:E = saisie ProjectFlow. Colonnes F:L = donnees comptables intouchables.
REPERTOIRE_TABLE_WIDTH = 12
PENDING_TRANSACTION_SAFETY_WINDOW_SECONDS = 600.0


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
class RepertoireSyncResult:
    total: int
    applied: int
    already_verified: int
    failed: int
    deferred: int = 0


class RepertoireService:
    def __init__(
        self,
        workbook: WorkbookGateway,
        today: Callable[[], date] = date.today,
        transaction_store: RepertoireTransactionStore | None = None,
    ) -> None:
        self._workbook = workbook
        self._today = today
        self._transaction_store = transaction_store

    async def next_available(self, *, year: int) -> NextAvailableProject | None:
        async with self._workbook.session():
            worksheet_name = str(year)
            await self._assert_worksheet_exists(worksheet_name)
            rows = await self._workbook.used_range_values(worksheet_name)
            for index, row in enumerate(rows):
                number = _cell_as_text(row, 0)
                if MAIN_PROJECT_RE.fullmatch(number) and project_info_columns_empty(row):
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
        created_on: date | None = None,
        ) -> None:
        repertoire_date = created_on or self._today()
        if self._transaction_store is not None:
            await self.sync_pending(
                minimum_age_seconds=PENDING_TRANSACTION_SAFETY_WINDOW_SECONDS,
            )
            transaction = self._transaction_store.create(
                project,
                force_overwrite=force_overwrite,
                repertoire_date=repertoire_date,
            )
            try:
                with self._transaction_store.lock():
                    try:
                        is_verified = await self.verify_project(
                            project,
                            created_on=repertoire_date,
                        )
                    except ProjectCreationError:
                        self._transaction_store.delete(transaction)
                        raise
                    if not is_verified:
                        try:
                            await self._upsert_project(
                                project,
                                force_overwrite=force_overwrite,
                                created_on=repertoire_date,
                            )
                        except ProjectCreationError:
                            self._transaction_store.delete(transaction)
                            raise
                        transaction = self._transaction_store.clear_verification(
                            transaction,
                        )
                    try:
                        is_verified = await self.verify_project(
                            project,
                            created_on=repertoire_date,
                        )
                    except ProjectCreationError:
                        self._transaction_store.delete(transaction)
                        raise
                    if not is_verified:
                        raise ProjectCreationError(
                            "L'ecriture du repertoire chantier n'a pas pu etre confirmee. "
                            "Elle est conservee en attente de synchronisation.",
                        )
                    self._transaction_store.mark_verified(transaction, reset=True)
            except OSError as exc:
                raise ProjectCreationError(
                    "Le repertoire chantier n'est pas disponible pour l'instant. "
                    "L'ecriture est conservee en attente de synchronisation.",
                ) from exc
            return

        await self._upsert_project(
            project,
            force_overwrite=force_overwrite,
            created_on=repertoire_date,
        )

    async def sync_pending(
        self,
        *,
        minimum_age_seconds: float = PENDING_TRANSACTION_SAFETY_WINDOW_SECONDS,
    ) -> RepertoireSyncResult:
        if self._transaction_store is None:
            return RepertoireSyncResult(total=0, applied=0, already_verified=0, failed=0)

        transactions = self._transaction_store.list_pending()
        applied = 0
        already_verified = 0
        failed = 0
        deferred = 0
        with self._transaction_store.lock():
            for transaction in transactions:
                try:
                    if await self.verify_project(
                        transaction.project,
                        created_on=transaction.repertoire_date,
                    ):
                        verified_transaction = self._transaction_store.mark_verified(transaction)
                        already_verified += 1
                        if verified_transaction.stable_seconds() >= minimum_age_seconds:
                            self._transaction_store.delete(verified_transaction)
                        else:
                            deferred += 1
                        continue
                    pending_transaction = self._transaction_store.clear_verification(transaction)
                    await self._upsert_project(
                        pending_transaction.project,
                        force_overwrite=pending_transaction.force_overwrite,
                        created_on=pending_transaction.repertoire_date,
                    )
                    if not await self.verify_project(
                        pending_transaction.project,
                        created_on=pending_transaction.repertoire_date,
                    ):
                        failed += 1
                        continue
                except (ProjectCreationError, OSError):
                    failed += 1
                    continue
                verified_transaction = self._transaction_store.mark_verified(
                    pending_transaction,
                    reset=True,
                )
                applied += 1
                if verified_transaction.stable_seconds() >= minimum_age_seconds:
                    self._transaction_store.delete(verified_transaction)
                else:
                    deferred += 1
        return RepertoireSyncResult(
            total=len(transactions),
            applied=applied,
            already_verified=already_verified,
            failed=failed,
            deferred=deferred,
        )

    async def verify_project(self, project: ProjectInput, *, created_on: date) -> bool:
        async with self._workbook.session():
            worksheet_name = str(project.number.year)
            await self._assert_worksheet_exists(worksheet_name)
            rows = await self._workbook.used_range_values(worksheet_name)
            row_index = find_project_row(rows, project.number)
            if row_index is None:
                return False
            return _row_matches_project(rows[row_index], project, created_on=created_on)

    async def _upsert_project(
        self,
        project: ProjectInput,
        *,
        force_overwrite: bool,
        created_on: date,
    ) -> None:
        async with self._workbook.session():
            worksheet_name = str(project.number.year)
            await self._assert_worksheet_exists(worksheet_name)
            rows = await self._workbook.used_range_values(worksheet_name)

            if project.number.is_subproject:
                await self._insert_subproject(
                    project,
                    rows=rows,
                    worksheet_name=worksheet_name,
                    created_on=created_on,
                )
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
                created_on=created_on,
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
        created_on: date,
    ) -> None:
        existing_index = find_project_row(rows, project.number)
        if existing_index is not None:
            updated_row = _project_values(
                project,
                width=PROJECT_WRITABLE_WIDTH,
                created_on=created_on,
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
            created_on=created_on,
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


def _cell_matches_date(row: list[Any], column: int, expected: date) -> bool:
    if len(row) <= column:
        return False
    value = row[column]
    if isinstance(value, datetime):
        return value.date() == expected
    if isinstance(value, date):
        return value == expected
    return str(value or "").strip() in {
        expected.isoformat(),
        expected.strftime("%d.%m.%Y"),
    }


def _row_matches_project(row: list[Any], project: ProjectInput, *, created_on: date) -> bool:
    if _cell_as_text(row, 0) != str(project.number):
        return False
    if not _cell_matches_date(row, 1, created_on):
        return False
    expected_values = [
        (2, project.societe),
        (3, project.contact),
        (4, project.designation),
    ]
    return all(
        _cell_as_text(row, column) == value.strip()
        for column, value in expected_values
        if value.strip()
    )


def _first_available_main_project_row_index(rows: list[list[Any]]) -> int | None:
    for index, row in enumerate(rows):
        number = _cell_as_text(row, 0)
        if MAIN_PROJECT_RE.fullmatch(number) and project_info_columns_empty(row):
            return index
    return None


@asynccontextmanager
async def null_workbook_session() -> AsyncIterator[None]:
    yield
