from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from projectflow.exceptions import GraphError
from projectflow.graph.client import JSON, GraphClient

WORKBOOK_SESSION_HEADER = "workbook-session-id"


@dataclass(frozen=True, slots=True)
class WorkbookRef:
    drive_id: str
    item_id: str


class ExcelWorkbookClient:
    def __init__(self, graph: GraphClient, workbook: WorkbookRef) -> None:
        self._graph = graph
        self._workbook = workbook
        self._session_id: str | None = None

    @asynccontextmanager
    async def session(self) -> AsyncIterator[None]:
        await self.create_session(persist_changes=True)
        try:
            yield
        finally:
            await self.close_session()

    async def create_session(self, *, persist_changes: bool) -> str:
        payload = await self._graph.post(
            f"{self._base_path}/workbook/createSession",
            json={"persistChanges": persist_changes},
        )
        session_id = payload.get("id")
        if not isinstance(session_id, str):
            raise GraphError("Graph n'a pas retourne d'identifiant de session Excel.")
        self._session_id = session_id
        return session_id

    async def close_session(self) -> None:
        if self._session_id is None:
            return
        session_id = self._session_id
        self._session_id = None
        await self._graph.post(
            f"{self._base_path}/workbook/closeSession",
            json={},
            headers={WORKBOOK_SESSION_HEADER: session_id},
        )

    async def list_worksheets(self) -> list[JSON]:
        payload = await self._graph.get(
            f"{self._base_path}/workbook/worksheets",
            headers=self._session_headers(),
        )
        value = payload.get("value", [])
        return [item for item in value if isinstance(item, dict)]

    async def worksheet_exists(self, worksheet_name: str) -> bool:
        worksheets = await self.list_worksheets()
        return any(item.get("name") == worksheet_name for item in worksheets)

    async def used_range_values(self, worksheet_name: str) -> list[list[Any]]:
        worksheet = _quote_odata_string(worksheet_name)
        payload = await self._graph.get(
            f"{self._base_path}/workbook/worksheets/{worksheet}/usedRange(valuesOnly=true)",
            headers=self._session_headers(),
        )
        values = payload.get("values", [])
        if not isinstance(values, list):
            return []
        return [row if isinstance(row, list) else [] for row in values]

    async def update_range_values(
        self,
        worksheet_name: str,
        address: str,
        values: list[list[Any]],
    ) -> None:
        worksheet = _quote_odata_string(worksheet_name)
        quoted_address = _quote_odata_string(address)
        await self._graph.patch(
            f"{self._base_path}/workbook/worksheets/{worksheet}/range(address={quoted_address})",
            json={"values": values},
            headers=self._session_headers(),
        )

    async def insert_range(
        self,
        worksheet_name: str,
        address: str,
        *,
        shift: str = "Down",
    ) -> None:
        worksheet = _quote_odata_string(worksheet_name)
        quoted_address = _quote_odata_string(address)
        await self._graph.post(
            f"{self._base_path}/workbook/worksheets/{worksheet}/range"
            f"(address={quoted_address})/insert",
            json={"shift": shift},
            headers=self._session_headers(),
        )

    async def list_tables(self, worksheet_name: str) -> list[JSON]:
        worksheet = _quote_odata_string(worksheet_name)
        payload = await self._graph.get(
            f"{self._base_path}/workbook/worksheets/{worksheet}/tables",
            headers=self._session_headers(),
        )
        value = payload.get("value", [])
        return [item for item in value if isinstance(item, dict)]

    async def add_table_row(
        self,
        table_id_or_name: str,
        values: list[Any],
        *,
        index: int | None = None,
    ) -> None:
        table = quote(table_id_or_name, safe="")
        body: dict[str, Any] = {"values": [values]}
        if index is not None:
            body["index"] = index
        await self._graph.post(
            f"{self._base_path}/workbook/tables/{table}/rows/add",
            json=body,
            headers=self._session_headers(),
        )

    @property
    def _base_path(self) -> str:
        return f"/drives/{self._workbook.drive_id}/items/{self._workbook.item_id}"

    def _session_headers(self) -> dict[str, str] | None:
        if self._session_id is None:
            return None
        return {WORKBOOK_SESSION_HEADER: self._session_id}


def _quote_odata_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
