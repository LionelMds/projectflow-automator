from __future__ import annotations

from typing import Any

import pytest

from projectflow.graph.excel import ExcelWorkbookClient, WorkbookRef
from projectflow.graph.onedrive import OneDriveClient
from projectflow.graph.outlook import OutlookClient
from projectflow.graph.planner import PlannerClient


class FakeGraph:
    def __init__(self) -> None:
        self.get_payloads: dict[str, dict[str, Any]] = {}
        self.pages: dict[str, list[dict[str, Any]]] = {}
        self.posts: list[tuple[str, dict[str, Any] | None]] = []
        self.patches: list[tuple[str, dict[str, Any] | None]] = []
        self.request_headers: list[dict[str, str] | None] = []

    async def get(
        self,
        path: str,
        *,
        params: dict[str, str | int | bool] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        del params
        self.request_headers.append(headers)
        return self.get_payloads[path]

    async def get_all_pages(
        self,
        path: str,
        *,
        params: dict[str, str | int | bool] | None = None,
    ) -> list[dict[str, Any]]:
        del params
        return self.pages.get(path, [])

    async def post(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.request_headers.append(headers)
        self.posts.append((path, json))
        if path.endswith("/createSession"):
            return {"id": "session"}
        return {"id": "created", "displayName": "Projet", "title": "Task"}

    async def patch(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.request_headers.append(headers)
        self.patches.append((path, json))
        return {}


@pytest.mark.asyncio
async def test_excel_workbook_client_builds_graph_paths() -> None:
    graph = FakeGraph()
    workbook = ExcelWorkbookClient(graph, WorkbookRef(drive_id="drive", item_id="item"))  # type: ignore[arg-type]
    graph.get_payloads[
        "/drives/drive/items/item/workbook/worksheets"
    ] = {"value": [{"name": "2026"}]}
    graph.get_payloads[
        "/drives/drive/items/item/workbook/worksheets/'2026'/usedRange(valuesOnly=true)"
    ] = {"values": [["2026-4995"]]}
    graph.get_payloads[
        "/drives/drive/items/item/workbook/worksheets/'2026'/tables"
    ] = {"value": [{"id": "table"}]}

    assert await workbook.worksheet_exists("2026") is True
    assert await workbook.used_range_values("2026") == [["2026-4995"]]
    assert await workbook.list_tables("2026") == [{"id": "table"}]

    await workbook.update_range_values("2026", "A1:F1", [["x"]])
    await workbook.insert_range("2026", "A2:F2")
    await workbook.add_table_row("table", ["x"], index=2)

    assert graph.patches[0] == (
        "/drives/drive/items/item/workbook/worksheets/'2026'/range(address='A1:F1')",
        {"values": [["x"]]},
    )
    assert graph.posts[0] == (
        "/drives/drive/items/item/workbook/worksheets/'2026'/range(address='A2:F2')/insert",
        {"shift": "Down"},
    )
    assert graph.posts[1] == (
        "/drives/drive/items/item/workbook/tables/table/rows/add",
        {"values": [["x"]], "index": 2},
    )


@pytest.mark.asyncio
async def test_excel_workbook_client_uses_session_header() -> None:
    graph = FakeGraph()
    workbook = ExcelWorkbookClient(graph, WorkbookRef(drive_id="drive", item_id="item"))  # type: ignore[arg-type]
    graph.get_payloads["/drives/drive/items/item/workbook/worksheets"] = {"value": []}

    async with workbook.session():
        await workbook.list_worksheets()

    assert graph.posts[0] == (
        "/drives/drive/items/item/workbook/createSession",
        {"persistChanges": True},
    )
    assert graph.request_headers[1] == {"workbook-session-id": "session"}
    assert graph.posts[-1] == ("/drives/drive/items/item/workbook/closeSession", {})
    assert graph.request_headers[-1] == {"workbook-session-id": "session"}


@pytest.mark.asyncio
async def test_outlook_client_reuses_existing_folder_and_creates_missing_child() -> None:
    graph = FakeGraph()
    graph.pages["/me/mailFolders"] = [{"id": "year", "displayName": "2026"}]
    graph.pages["/me/mailFolders/year/childFolders"] = []

    folder = await OutlookClient(graph).ensure_folder_path(["2026", "2026-4995"])  # type: ignore[arg-type]

    assert folder.id == "created"
    assert graph.posts == [
        ("/me/mailFolders/year/childFolders", {"displayName": "2026-4995"}),
    ]


@pytest.mark.asyncio
async def test_planner_client_lists_buckets_and_creates_task() -> None:
    graph = FakeGraph()
    graph.get_payloads["/planner/plans/plan/buckets"] = {
        "value": [{"id": "bucket", "name": "Dossiers", "planId": "plan"}],
    }

    planner = PlannerClient(graph)  # type: ignore[arg-type]
    buckets = await planner.list_buckets("plan")
    task = await planner.create_task(
        plan_id="plan",
        bucket_id="bucket",
        title="2026-4995",
        due_days=7,
    )

    assert buckets[0].name == "Dossiers"
    assert task.id == "created"
    assert graph.posts[0][0] == "/planner/tasks"
    assert graph.posts[0][1]["bucketId"] == "bucket"


@pytest.mark.asyncio
async def test_onedrive_client_locates_item_by_path() -> None:
    graph = FakeGraph()
    graph.get_payloads["/me/drive/root:/Entreprise/Rep.xlsx"] = {
        "id": "item",
        "name": "Rep.xlsx",
        "webUrl": "https://example.test",
    }

    item = await OneDriveClient(graph).locate_me_item_by_path("Entreprise/Rep.xlsx")  # type: ignore[arg-type]

    assert item.id == "item"
    assert item.web_url == "https://example.test"
