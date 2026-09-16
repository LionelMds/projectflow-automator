from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import stat
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager, suppress
from copy import copy
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, BinaryIO, cast
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.utils.cell import get_column_letter, range_boundaries
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from projectflow.exceptions import ConfigError, ProjectCreationError
from projectflow.platform.sync_paths import is_excel_recovery_copy, is_synchronized_path

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


@dataclass(frozen=True, slots=True)
class CellFormat:
    font: Any
    fill: Any
    border: Any
    alignment: Any
    number_format: str
    protection: Any


@dataclass(frozen=True, slots=True)
class RowFormat:
    cell_formats: list[CellFormat]
    height: float | None


class LocalWorkbookGateway:
    def __init__(self, workbook_path: Path) -> None:
        self._workbook_path = workbook_path
        self._workbook: Workbook | None = None
        self._session_owner: asyncio.Task[Any] | None = None
        self._dirty = False
        self._session_failed = False
        self._original_digest: bytes | None = None

    def session(self) -> AbstractAsyncContextManager[None]:
        return self._session()

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[None]:
        if self._session_owner is not None:
            self._require_workbook()
            try:
                yield
            except BaseException:
                self._session_failed = True
                raise
            return
        self._session_owner = asyncio.current_task()
        self._dirty = False
        self._session_failed = False
        try:
            self._open_workbook()
            yield
            if self._dirty:
                if self._session_failed:
                    raise ProjectCreationError(
                        "Enregistrement annule apres l'echec d'une operation. "
                        "Actualisez le repertoire avant de recommencer.",
                    )
                self._save_workbook()
        finally:
            workbook = self._workbook
            self._workbook = None
            self._session_owner = None
            self._original_digest = None
            self._dirty = False
            if workbook is not None:
                workbook.close()

    def _open_workbook(self) -> None:
        if is_excel_recovery_copy(self._workbook_path):
            raise ConfigError(
                "Le repertoire selectionne est une copie de recuperation Excel non fusionnee. "
                "Selectionnez le classeur original dans les parametres. Conservez cette "
                "copie pour verifier les modifications a recuperer.",
            )
        if is_synchronized_path(self._workbook_path):
            raise ConfigError(
                "Le repertoire se trouve dans un dossier synchronise OneDrive/SharePoint. "
                "Utilisez la connexion Microsoft 365 pour modifier le fichier partage, "
                "afin d'eviter les copies non fusionnees.",
            )
        try:
            source = self._workbook_path.read_bytes()
            self._original_digest = hashlib.sha256(source).digest()
            self._workbook = load_workbook(BytesIO(source))
        except OSError as exc:
            raise ProjectCreationError(
                "Impossible de lire le repertoire local. Verifiez le chemin et les droits "
                "d'acces au fichier.",
            ) from exc
        except (BadZipFile, InvalidFileException, KeyError, SyntaxError, ValueError) as exc:
            raise ProjectCreationError(
                "Le repertoire local est endommage ou n'est pas un classeur Excel valide. "
                "Restaurez une version valide avant de recommencer.",
            ) from exc

    def _mark_dirty(self) -> None:
        if not self._dirty:
            self._assert_writable()
        self._dirty = True

    def _assert_writable(self) -> None:
        if self._workbook_path.with_name(f"~${self._workbook_path.name}").exists():
            raise ProjectCreationError(
                "Le repertoire est ouvert ou verrouille par Excel. Fermez ce classeur "
                "sur les postes qui l'utilisent, puis recommencez. Si le verrou persiste, "
                "faites verifier le fichier de verrouillage par votre administrateur.",
            )
        try:
            mode = self._workbook_path.stat().st_mode
            if not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
                raise PermissionError("Classeur en lecture seule")
            # Probe access without truncating or changing the workbook.
            with self._workbook_path.open("r+b"):
                pass
        except OSError as exc:
            raise ProjectCreationError(
                "Le repertoire local est verrouille ou en lecture seule. Fermez-le "
                "dans Excel et verifiez vos droits d'ecriture sur le fichier et son dossier.",
            ) from exc

    def _assert_unchanged(self) -> None:
        with self._workbook_path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").digest()
        if digest != self._original_digest:
            raise ProjectCreationError(
                "Le repertoire a ete modifie pendant cette operation. "
                "Aucune modification n'a ete enregistree. Actualisez le repertoire "
                "avant de recommencer.",
            )

    def _save_workbook(self) -> None:
        temporary_path: Path | None = None
        try:
            # This lock is released by the OS on a crash; its presence is not a lock.
            with _exclusive_write_lock(self._workbook_path):
                self._assert_writable()
                self._assert_unchanged()
                with NamedTemporaryFile(
                    mode="w+b",
                    prefix=f".{self._workbook_path.stem}.projectflow-",
                    suffix=".tmp",
                    dir=self._workbook_path.parent,
                    delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                    self._require_workbook().save(temporary)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                _validate_saved_workbook(temporary_path)
                shutil.copymode(self._workbook_path, temporary_path)
                # Serialization may take time. Recheck external edits and Excel locks.
                self._assert_writable()
                self._assert_unchanged()
                temporary_path.replace(self._workbook_path)
                temporary_path = None
        except (OSError, BadZipFile) as exc:
            raise ProjectCreationError(
                "Impossible d'enregistrer le repertoire local. Le fichier original a ete "
                "conserve. Fermez le classeur dans Excel, puis verifiez les droits "
                "d'ecriture et l'espace disponible avant de recommencer.",
            ) from exc
        finally:
            if temporary_path is not None:
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)

    async def worksheet_exists(self, worksheet_name: str) -> bool:
        return worksheet_name in self._require_workbook().sheetnames

    async def used_range_values(self, worksheet_name: str) -> list[list[Any]]:
        worksheet = self._worksheet(worksheet_name)
        return [
            list(row)
            for row in worksheet.iter_rows(
                min_row=1,
                max_row=worksheet.max_row,
                min_col=1,
                max_col=worksheet.max_column,
                values_only=True,
            )
        ]

    async def update_range_values(
        self,
        worksheet_name: str,
        address: str,
        values: list[list[Any]],
    ) -> None:
        worksheet = self._worksheet(worksheet_name)
        min_col, min_row, _max_col, _max_row = _range_boundaries(address)
        for row_offset, row_values in enumerate(values):
            for column_offset, value in enumerate(row_values):
                cell = worksheet.cell(
                    row=min_row + row_offset,
                    column=min_col + column_offset,
                )
                if cell.value != value or (
                    isinstance(value, date) and cell.number_format != "DD.MM.YYYY"
                ):
                    self._mark_dirty()
                cell.value = value
                if isinstance(value, date):
                    cell.number_format = "DD.MM.YYYY"

    async def insert_blank_row(
        self,
        worksheet_name: str,
        row_index: int,
        *,
        copy_format_from_row_index: int | None = None,
        format_width: int = 12,
    ) -> None:
        worksheet = self._worksheet(worksheet_name)
        self._mark_dirty()
        target_row = row_index + 1
        row_format = _capture_row_format(
            worksheet,
            copy_format_from_row_index,
            min_col=1,
            max_col=format_width,
        )
        worksheet.insert_rows(target_row)
        _expand_tables_for_inserted_row(worksheet, target_row)
        if row_format is not None:
            _apply_row_format(worksheet, row_format, target_row=target_row, min_col=1)
        for column_index in range(1, format_width + 1):
            worksheet.cell(row=target_row, column=column_index).value = None

    def _require_workbook(self) -> Workbook:
        if self._session_owner is not None and self._session_owner is not asyncio.current_task():
            raise ProjectCreationError(
                "Une autre operation utilise deja le repertoire local. Recommencez "
                "lorsqu'elle est terminee.",
            )
        if self._workbook is None:
            raise ProjectCreationError("Le repertoire local n'est pas ouvert en session.")
        return self._workbook

    def _worksheet(self, worksheet_name: str) -> Worksheet:
        workbook = self._require_workbook()
        if worksheet_name not in workbook.sheetnames:
            raise ProjectCreationError(f"Onglet introuvable: {worksheet_name}")
        return cast("Worksheet", workbook[worksheet_name])


@contextmanager
def _exclusive_write_lock(workbook_path: Path) -> Iterator[None]:
    lock_path = workbook_path.with_name(f".{workbook_path.name}.projectflow.lock")
    # Do not unlink: deleting a lock file permits another process to lock a new
    # inode while a waiting process still holds the old one.
    with lock_path.open("a+b") as lock_file:
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        try:
            _lock_file(lock_file)
        except OSError as exc:
            raise ProjectCreationError(
                "Une autre instance de ProjectFlow enregistre le repertoire local. "
                "Patientez puis actualisez le repertoire avant de recommencer.",
            ) from exc
        try:
            yield
        finally:
            # Closing the descriptor also releases the lock, even if explicit
            # unlocking fails. A successful replacement must remain a success.
            with suppress(OSError):
                _unlock_file(lock_file)


def _validate_saved_workbook(workbook_path: Path) -> None:
    with ZipFile(workbook_path) as archive:
        required_parts = {"[Content_Types].xml", "xl/workbook.xml"}
        if not required_parts.issubset(archive.namelist()) or archive.testzip() is not None:
            raise BadZipFile("Classeur temporaire incomplet")


def _lock_file(lock_file: BinaryIO) -> None:
    if sys.platform == "win32":
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(lock_file: BinaryIO) -> None:
    if sys.platform == "win32":
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _range_boundaries(address: str) -> tuple[int, int, int, int]:
    return cast("tuple[int, int, int, int]", range_boundaries(address))


def _expand_tables_for_inserted_row(worksheet: Worksheet, row_number: int) -> None:
    for table in worksheet.tables.values():
        min_col, min_row, max_col, max_row = _range_boundaries(table.ref)
        if min_row < row_number <= max_row + 1:
            table.ref = (
                f"{get_column_letter(min_col)}{min_row}:{get_column_letter(max_col)}{max_row + 1}"
            )


def _capture_row_format(
    worksheet: Worksheet,
    row_index: int | None,
    *,
    min_col: int,
    max_col: int,
) -> RowFormat | None:
    if row_index is None:
        return None
    source_row = row_index + 1
    if source_row < 1 or source_row > worksheet.max_row:
        return None
    return RowFormat(
        cell_formats=[
            _capture_cell_format(cast("Cell", worksheet.cell(row=source_row, column=column_index)))
            for column_index in range(min_col, max_col + 1)
        ],
        height=worksheet.row_dimensions[source_row].height,
    )


def _apply_row_format(
    worksheet: Worksheet,
    row_format: RowFormat,
    *,
    target_row: int,
    min_col: int,
) -> None:
    for offset, cell_format in enumerate(row_format.cell_formats):
        _apply_cell_format(
            cast("Cell", worksheet.cell(row=target_row, column=min_col + offset)),
            cell_format,
        )
    worksheet.row_dimensions[target_row].height = row_format.height


def _capture_cell_format(cell: Cell) -> CellFormat:
    return CellFormat(
        font=copy(cell.font),
        fill=copy(cell.fill),
        border=copy(cell.border),
        alignment=copy(cell.alignment),
        number_format=cell.number_format,
        protection=copy(cell.protection),
    )


def _apply_cell_format(cell: Cell, cell_format: CellFormat) -> None:
    cell.font = copy(cell_format.font)
    cell.fill = copy(cell_format.fill)
    cell.border = copy(cell_format.border)
    cell.alignment = copy(cell_format.alignment)
    cell.number_format = cell_format.number_format
    cell.protection = copy(cell_format.protection)
