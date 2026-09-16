from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

from projectflow.config import RepertoireChantierConfig
from projectflow.core.repertoire_service import REPERTOIRE_TABLE_WIDTH
from projectflow.exceptions import ConfigError, GraphError
from projectflow.graph.client import GraphClient
from projectflow.graph.onedrive import OneDriveItemResolver
from projectflow.logging import get_logger

HTTP_NOT_FOUND = 404
LOGGER = get_logger(__name__)


@dataclass(slots=True)
class GraphWorkbookTarget:
    drive_id: str
    item_id: str


@dataclass(frozen=True, slots=True)
class WorkbookTable:
    table_id: str
    row_index: int
    row_count: int
    column_index: int
    column_count: int
    show_headers: bool
    show_totals: bool

    @property
    def data_start_row(self) -> int:
        return self.row_index + 1 + (1 if self.show_headers else 0)

    @property
    def data_row_count(self) -> int:
        header_rows = 1 if self.show_headers else 0
        total_rows = 1 if self.show_totals else 0
        return max(0, self.row_count - header_rows - total_rows)

    @property
    def data_end_row(self) -> int:
        return self.data_start_row + self.data_row_count - 1

    def table_row_index_for_sheet_row(self, sheet_row: int) -> int | None:
        if sheet_row < self.data_start_row:
            return None
        if sheet_row > self.data_end_row + 1:
            return None
        return sheet_row - self.data_start_row


class GraphExcelWorkbookGateway:
    def __init__(
        self,
        *,
        graph: GraphClient,
        config: RepertoireChantierConfig,
        resolver: OneDriveItemResolver | None = None,
    ) -> None:
        self._graph = graph
        self._config = config
        self._resolver = resolver or OneDriveItemResolver(graph)
        display_path = config.display_path.strip()
        self._display_path: Path | str | None = (
            display_path
            if "://" in display_path
            else Path(display_path).expanduser()
            if display_path
            else None
        )
        self._target: GraphWorkbookTarget | None = None
        self._session_id: str | None = None
        self._session_lock = asyncio.Lock()
        self._session_owner: asyncio.Task[Any] | None = None

    def session(self) -> AbstractAsyncContextManager[None]:
        return self._session()

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[None]:
        current_task = asyncio.current_task()
        if current_task is not None and current_task is self._session_owner:
            yield
            return

        # A session must belong to a single operation, including its reads and close.
        async with self._session_lock:
            await self._ensure_target()
            payload = await self._graph.post(
                f"{self._workbook_path()}/createSession",
                json={"persistChanges": True},
            )
            session_id = payload.get("id")
            if not isinstance(session_id, str) or not session_id.strip():
                raise GraphError(
                    "Microsoft Graph n'a pas ouvert de session Excel valide. "
                    "Aucune ecriture n'a ete effectuee.",
                )
            self._session_id = session_id
            self._session_owner = current_task
            operation_error: BaseException | None = None
            try:
                yield
            except BaseException as exc:
                operation_error = exc
                raise
            finally:
                headers = self._session_headers()
                # Clear state even if closeSession fails or the task is cancelled.
                self._session_id = None
                self._session_owner = None
                invalid_session = (
                    isinstance(operation_error, GraphError)
                    and operation_error.invalid_workbook_session
                )
                if not invalid_session:
                    try:
                        await self._graph.post(
                            f"{self._workbook_path()}/closeSession",
                            headers=headers,
                        )
                    except GraphError as close_error:
                        if operation_error is None:
                            raise GraphError(
                                "La fermeture de la session Excel a echoue. "
                                "Des modifications peuvent deja etre enregistrees. "
                                "Actualisez le repertoire avant de recommencer. "
                                + str(close_error),
                                status_code=close_error.status_code,
                                error_code=close_error.error_code,
                                inner_error_code=close_error.inner_error_code,
                            ) from close_error
                        LOGGER.warning(
                            "excel_session_close_failed",
                            status_code=close_error.status_code,
                            error_code=close_error.error_code,
                            inner_error_code=close_error.inner_error_code,
                        )

    async def worksheet_exists(self, worksheet_name: str) -> bool:
        try:
            await self._graph.get(
                f"{self._workbook_path()}/worksheets/{_path_segment(worksheet_name)}",
                headers=self._session_headers(),
            )
        except GraphError as exc:
            if exc.status_code == HTTP_NOT_FOUND and not exc.invalid_workbook_session:
                return False
            raise
        return True

    async def used_range_values(self, worksheet_name: str) -> list[list[Any]]:
        payload = await self._graph.get(
            f"{self._worksheet_path(worksheet_name)}/usedRange(valuesOnly=true)",
            headers=self._session_headers(),
        )
        values = payload.get("values")
        if not isinstance(values, list) or any(not isinstance(row, list) for row in values):
            raise GraphError("Microsoft Graph n'a pas retourne les lignes du repertoire Excel.")
        return [_row_values(row) for row in values]

    async def update_range_values(
        self,
        worksheet_name: str,
        address: str,
        values: list[list[Any]],
    ) -> None:
        await self._graph.patch(
            f"{self._worksheet_path(worksheet_name)}/range(address='{_odata_string(address)}')",
            json={"values": _graph_values(values)},
            headers=self._session_headers(),
        )

    async def insert_blank_row(
        self,
        worksheet_name: str,
        row_index: int,
        *,
        copy_format_from_row_index: int | None = None,
        format_width: int = REPERTOIRE_TABLE_WIDTH,
    ) -> None:
        del copy_format_from_row_index
        target_row = row_index + 1
        if await self._insert_table_blank_row(worksheet_name, target_row, format_width):
            return

        address = f"A{target_row}:{_excel_column_name(format_width)}{target_row}"
        await self._graph.post(
            f"{self._worksheet_path(worksheet_name)}/range(address='{_odata_string(address)}')"
            "/insert",
            json={"shift": "Down"},
            headers=self._session_headers(),
        )
        await self.update_range_values(
            worksheet_name,
            address,
            [[""] * format_width],
        )

    async def _insert_table_blank_row(
        self,
        worksheet_name: str,
        target_row: int,
        format_width: int,
    ) -> bool:
        table = await self._find_repertoire_table(worksheet_name, target_row, format_width)
        if table is None:
            return False

        table_row_index = table.table_row_index_for_sheet_row(target_row)
        if table_row_index is None:
            return False

        await self._graph.post(
            f"{self._worksheet_path(worksheet_name)}"
            f"/tables/{_path_segment(table.table_id)}/rows/add",
            json={
                "index": table_row_index,
                "values": [[""] * table.column_count],
            },
            headers=self._session_headers(),
        )
        return True

    async def _find_repertoire_table(
        self,
        worksheet_name: str,
        target_row: int,
        format_width: int,
    ) -> WorkbookTable | None:
        payload = await self._graph.get(
            f"{self._worksheet_path(worksheet_name)}/tables",
            headers=self._session_headers(),
        )

        values = payload.get("value")
        if not isinstance(values, list):
            raise GraphError("Microsoft Graph n'a pas retourne la liste des tableaux Excel.")

        for value in values:
            if not isinstance(value, dict):
                raise GraphError("Microsoft Graph a retourne un tableau Excel incomplet.")
            table_id = _table_id(value)
            if table_id is None:
                raise GraphError("Microsoft Graph a retourne un tableau Excel sans identifiant.")
            table = await self._table_details(worksheet_name, table_id, value)
            if table.column_index != 0 or table.column_count < format_width:
                continue
            if table.table_row_index_for_sheet_row(target_row) is not None:
                return table
        return None

    async def _table_details(
        self,
        worksheet_name: str,
        table_id: str,
        table_payload: dict[str, Any],
    ) -> WorkbookTable:
        range_payload = await self._graph.get(
            f"{self._worksheet_path(worksheet_name)}/tables/{_path_segment(table_id)}/range",
            headers=self._session_headers(),
        )

        row_index = _payload_int(range_payload, "rowIndex")
        row_count = _payload_int(range_payload, "rowCount")
        column_index = _payload_int(range_payload, "columnIndex")
        column_count = _payload_int(range_payload, "columnCount")
        if row_index is None or row_count is None or column_index is None or column_count is None:
            raise GraphError("Microsoft Graph n'a pas retourne les dimensions du tableau Excel.")

        return WorkbookTable(
            table_id=table_id,
            row_index=row_index,
            row_count=row_count,
            column_index=column_index,
            column_count=column_count,
            show_headers=bool(table_payload.get("showHeaders", True)),
            show_totals=bool(table_payload.get("showTotals", False)),
        )

    async def _ensure_target(self) -> GraphWorkbookTarget:
        if self._target is not None:
            return self._target
        if self._config.is_configured:
            self._target = GraphWorkbookTarget(
                drive_id=self._config.drive_id,
                item_id=self._config.item_id,
            )
            return self._target
        display_path = self._config.display_path.strip()
        if not display_path:
            raise ConfigError("Repertoire chantier cloud non configure.")
        if self._display_path is None:
            raise ConfigError("Repertoire chantier cloud non configure.")
        target = await self._resolver.resolve_workbook(self._display_path)
        self._config.drive_id = target.drive_id
        self._config.item_id = target.item_id
        self._target = GraphWorkbookTarget(drive_id=target.drive_id, item_id=target.item_id)
        return self._target

    def _workbook_path(self) -> str:
        target = self._target
        if target is None:
            raise ConfigError("Repertoire chantier cloud non initialise.")
        return (
            f"/drives/{_path_segment(target.drive_id)}"
            f"/items/{_path_segment(target.item_id)}/workbook"
        )

    def _worksheet_path(self, worksheet_name: str) -> str:
        return f"{self._workbook_path()}/worksheets/{_path_segment(worksheet_name)}"

    def _session_headers(self) -> dict[str, str]:
        if self._session_id is None:
            return {}
        return {"workbook-session-id": self._session_id}


def _path_segment(value: str) -> str:
    return quote(value, safe="")


def _odata_string(value: str) -> str:
    return value.replace("'", "''")


def _row_values(row: list[Any]) -> list[Any]:
    return list(row)


def _table_id(payload: dict[str, Any]) -> str | None:
    raw_value = payload.get("id") or payload.get("name")
    if not isinstance(raw_value, str):
        return None
    return raw_value


def _payload_int(payload: dict[str, Any], key: str) -> int | None:
    raw_value = payload.get(key)
    return raw_value if isinstance(raw_value, int) else None


def _graph_values(values: list[list[Any]]) -> list[list[Any]]:
    return [[_graph_value(value) for value in row] for row in values]


def _graph_value(value: object) -> object:
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return value


def _excel_column_name(width: int) -> str:
    name = ""
    number = width
    while number:
        number, remainder = divmod(number - 1, 26)
        name = chr(65 + remainder) + name
    return name
