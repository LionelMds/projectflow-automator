from __future__ import annotations

import asyncio
import base64
import json
from datetime import date
from typing import Any

import httpx
import pytest

from projectflow.config import RepertoireChantierConfig
from projectflow.core.models import ProjectInput
from projectflow.core.numero import parse_project_number
from projectflow.core.repertoire_service import RepertoireService
from projectflow.exceptions import GraphError
from projectflow.graph.client import GraphClient
from projectflow.graph.excel import GraphExcelWorkbookGateway

TODAY = date(2026, 5, 11)


class FakeTokenProvider:
    async def access_token(self) -> str:
        return "token"


@pytest.mark.asyncio
async def test_graph_excel_gateway_updates_project_row_in_cloud_session() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/createSession"):
            return httpx.Response(200, json={"id": "session-id"})
        if request.url.path.endswith("/worksheets/2026"):
            return httpx.Response(200, json={"id": "sheet"})
        if request.url.path.endswith("/usedRange(valuesOnly=true)"):
            return httpx.Response(200, json={"values": [["2026-4995", "", "", "", ""]]})
        if request.method == "PATCH":
            return httpx.Response(200, json={})
        if request.url.path.endswith("/closeSession"):
            return httpx.Response(204)
        return httpx.Response(404, json={"error": {"message": "missing"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        gateway = GraphExcelWorkbookGateway(
            graph=GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
            config=RepertoireChantierConfig(drive_id="drive", item_id="item"),
        )
        service = RepertoireService(gateway, today=lambda: TODAY)
        project = ProjectInput(
            number=parse_project_number("2026-4995"),
            designation="Escalier",
            societe="Balz",
            contact="Lionel",
        )

        await service.upsert_project(project)

    patch_request = next(request for request in requests if request.method == "PATCH")
    assert patch_request.headers["authorization"] == "Bearer token"
    assert patch_request.headers["workbook-session-id"] == "session-id"
    assert _json(patch_request) == {
        "values": [["2026-4995", "11.05.2026", "Balz", "Lionel", "Escalier"]],
    }


@pytest.mark.asyncio
async def test_graph_excel_gateway_clears_inserted_subproject_row_before_writing() -> None:
    patch_bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/createSession"):
            response = httpx.Response(200, json={"id": "session-id"})
        elif request.url.path.endswith("/worksheets/2026"):
            response = httpx.Response(200, json={"id": "sheet"})
        elif request.url.path.endswith("/usedRange(valuesOnly=true)"):
            response = httpx.Response(
                200,
                json={
                    "values": [
                        ["2026-4995", "Balz", "", "", "Escalier", "A ne pas copier"],
                        ["2026-5000", "", "", "", ""],
                    ],
                },
            )
        elif request.url.path.endswith("/insert"):
            response = httpx.Response(200, json={})
        elif request.url.path.endswith("/tables"):
            response = httpx.Response(200, json={"value": []})
        elif request.method == "PATCH":
            patch_bodies.append(_json(request))
            response = httpx.Response(200, json={})
        elif request.url.path.endswith("/closeSession"):
            response = httpx.Response(204)
        else:
            response = httpx.Response(404, json={"error": {"message": "missing"}})
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        gateway = GraphExcelWorkbookGateway(
            graph=GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
            config=RepertoireChantierConfig(drive_id="drive", item_id="item"),
        )
        service = RepertoireService(gateway, today=lambda: TODAY)

        await service.upsert_project(
            ProjectInput(number=parse_project_number("2026-4995-2"), designation="Variante"),
        )

    assert patch_bodies[0] == {"values": [[""] * 12]}
    assert patch_bodies[1] == {
        "values": [["2026-4995-2", "11.05.2026", "", "", "Variante"]],
    }


@pytest.mark.asyncio
async def test_graph_excel_gateway_adds_subproject_inside_structured_table() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/createSession"):
            response = httpx.Response(200, json={"id": "session-id"})
        elif request.url.path.endswith("/worksheets/2026"):
            response = httpx.Response(200, json={"id": "sheet"})
        elif request.url.path.endswith("/usedRange(valuesOnly=true)"):
            response = httpx.Response(
                200,
                json={
                    "values": [
                        ["NO.", "DATE", "CLIENT", "CONTACT", "DESCRIPTION"],
                        ["2026-4995", "11.05.2026", "Balz", "", "Escalier"],
                        ["2026-5000", "", "", "", ""],
                    ],
                },
            )
        elif request.url.path.endswith("/tables"):
            response = httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "table-id",
                            "name": "Table1",
                            "showHeaders": True,
                            "showTotals": False,
                        },
                    ],
                },
            )
        elif request.url.path.endswith("/tables/table-id/range"):
            response = httpx.Response(
                200,
                json={
                    "rowIndex": 0,
                    "rowCount": 3,
                    "columnIndex": 0,
                    "columnCount": 12,
                },
            )
        elif request.url.path.endswith("/tables/table-id/rows/add"):
            response = httpx.Response(200, json={"index": 1})
        elif request.method == "PATCH":
            response = httpx.Response(200, json={})
        elif request.url.path.endswith("/closeSession"):
            response = httpx.Response(204)
        else:
            response = httpx.Response(404, json={"error": {"message": "missing"}})
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        gateway = GraphExcelWorkbookGateway(
            graph=GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
            config=RepertoireChantierConfig(drive_id="drive", item_id="item"),
        )
        service = RepertoireService(gateway, today=lambda: TODAY)

        await service.upsert_project(
            ProjectInput(
                number=parse_project_number("2026-4995-2"),
                designation="Variante",
                societe="Client sous",
                contact="Contact sous",
            ),
        )

    table_insert = next(
        request for request in requests if request.url.path.endswith("/tables/table-id/rows/add")
    )
    assert _json(table_insert) == {
        "index": 1,
        "values": [[""] * 12],
    }
    assert not any(request.url.path.endswith("/insert") for request in requests)

    patch_request = next(request for request in requests if request.method == "PATCH")
    assert patch_request.url.path.endswith("/workbook/worksheets/2026/range(address='A3:E3')")
    assert _json(patch_request) == {
        "values": [["2026-4995-2", "11.05.2026", "Client sous", "Contact sous", "Variante"]],
    }


def _json(request: httpx.Request) -> dict[str, Any]:
    payload = json.loads(request.content.decode("utf-8"))
    assert isinstance(payload, dict)
    return payload


@pytest.mark.asyncio
async def test_graph_client_formats_forbidden_message() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(403, json={})),
    ) as http_client:
        client = GraphClient(token_provider=FakeTokenProvider(), http_client=http_client)

        with pytest.raises(GraphError, match=r"Files\.ReadWrite\.All"):
            await client.get("/me/drive/root")


@pytest.mark.asyncio
async def test_graph_session_close_failure_clears_state_and_allows_new_session() -> None:
    requests: list[httpx.Request] = []
    session_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal session_count
        requests.append(request)
        if request.url.path.endswith("/createSession"):
            session_count += 1
            assert "workbook-session-id" not in request.headers
            return httpx.Response(200, json={"id": f"session-{session_count}"})
        if request.url.path.endswith("/closeSession") and session_count == 1:
            return httpx.Response(503, json={"error": {"message": "close failed"}})
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        gateway = GraphExcelWorkbookGateway(
            graph=GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
            config=RepertoireChantierConfig(drive_id="drive", item_id="item"),
        )
        with pytest.raises(GraphError, match="close failed") as caught:
            async with gateway.session():
                await gateway.update_range_values("2026", "A1", [["first"]])

        async with gateway.session():
            await gateway.update_range_values("2026", "A1", [["second"]])

    assert "Des modifications peuvent deja etre enregistrees" in str(caught.value)
    patches = [request for request in requests if request.method == "PATCH"]
    assert [request.headers["workbook-session-id"] for request in patches] == [
        "session-1",
        "session-2",
    ]
    assert session_count == 2


@pytest.mark.asyncio
async def test_graph_session_preserves_operation_error_when_close_also_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/createSession"):
            return httpx.Response(200, json={"id": "session-id"})
        return httpx.Response(503, json={"error": {"message": "close failed"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        gateway = GraphExcelWorkbookGateway(
            graph=GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
            config=RepertoireChantierConfig(drive_id="drive", item_id="item"),
        )
        with pytest.raises(GraphError, match="original error"):
            async with gateway.session():
                raise GraphError("original error", status_code=409)


@pytest.mark.asyncio
async def test_graph_session_serializes_competing_tasks_and_supports_nesting() -> None:
    requests: list[httpx.Request] = []
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()
    session_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal session_count
        requests.append(request)
        if request.url.path.endswith("/createSession"):
            session_count += 1
            return httpx.Response(200, json={"id": f"session-{session_count}"})
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        gateway = GraphExcelWorkbookGateway(
            graph=GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
            config=RepertoireChantierConfig(drive_id="drive", item_id="item"),
        )

        async def first_operation() -> None:
            async with gateway.session(), gateway.session():
                first_entered.set()
                await release_first.wait()
                await gateway.update_range_values("2026", "A1", [["first"]])

        async def second_operation() -> None:
            second_started.set()
            async with gateway.session():
                await gateway.update_range_values("2026", "A2", [["second"]])

        async with asyncio.TaskGroup() as group:
            group.create_task(first_operation())
            await first_entered.wait()
            group.create_task(second_operation())
            await second_started.wait()
            assert session_count == 1
            release_first.set()

    assert [request.url.path.rsplit("/", 1)[-1] for request in requests] == [
        "createSession",
        "range(address='A1')",
        "closeSession",
        "createSession",
        "range(address='A2')",
        "closeSession",
    ]
    assert requests[1].headers["workbook-session-id"] == "session-1"
    assert requests[4].headers["workbook-session-id"] == "session-2"


@pytest.mark.asyncio
async def test_graph_session_cancel_releases_session_for_next_operation() -> None:
    requests: list[httpx.Request] = []
    entered = asyncio.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/createSession"):
            return httpx.Response(200, json={"id": "session-id"})
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        gateway = GraphExcelWorkbookGateway(
            graph=GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
            config=RepertoireChantierConfig(drive_id="drive", item_id="item"),
        )

        async def operation() -> None:
            async with gateway.session():
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(operation())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with gateway.session():
            await gateway.update_range_values("2026", "A1", [["recovered"]])

    assert sum(request.url.path.endswith("/closeSession") for request in requests) == 2


@pytest.mark.asyncio
async def test_graph_session_never_reuses_invalid_session_even_for_close() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/createSession"):
            return httpx.Response(200, json={"id": "session-id"})
        return httpx.Response(
            409,
            json={"error": {"code": "invalidSessionAccessConflict"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        gateway = GraphExcelWorkbookGateway(
            graph=GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
            config=RepertoireChantierConfig(drive_id="drive", item_id="item"),
        )
        with pytest.raises(GraphError, match="conflit"):
            async with gateway.session():
                await gateway.update_range_values("2026", "A1", [["value"]])

    assert not any(request.url.path.endswith("/closeSession") for request in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("session_id", [None, "", " "])
async def test_graph_session_refuses_writing_without_valid_session(session_id: str | None) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": session_id})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        gateway = GraphExcelWorkbookGateway(
            graph=GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
            config=RepertoireChantierConfig(drive_id="drive", item_id="item"),
        )
        with pytest.raises(GraphError, match="session Excel valide"):
            async with gateway.session():
                await gateway.update_range_values("2026", "A1", [["must not write"]])

    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_path", ["/tables", "/tables/table-id/range"])
async def test_graph_table_detection_failure_prevents_unsafe_range_insertion(
    failure_path: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/createSession"):
            return httpx.Response(200, json={"id": "session-id"})
        if request.url.path.endswith(failure_path):
            return httpx.Response(409, json={"error": {"code": "accessConflict"}})
        if request.url.path.endswith("/tables"):
            return httpx.Response(200, json={"value": [{"id": "table-id"}]})
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        gateway = GraphExcelWorkbookGateway(
            graph=GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
            config=RepertoireChantierConfig(drive_id="drive", item_id="item"),
        )
        with pytest.raises(GraphError, match="conflit"):
            async with gateway.session():
                await gateway.insert_blank_row("2026", 2)

    assert not any(request.url.path.endswith("/insert") for request in requests)
    assert not any(request.method == "PATCH" for request in requests)


@pytest.mark.asyncio
async def test_graph_excel_resolves_sharing_url_without_converting_it_to_local_path() -> None:
    sharing_url = "https://example.sharepoint.com/:x:/s/Projects/SharedFile?e=abc"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "/shares/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "id": "item",
                    "name": "Repertoire.xlsx",
                    "parentReference": {"driveId": "drive"},
                },
            )
        if request.url.path.endswith("/createSession"):
            return httpx.Response(200, json={"id": "session-id"})
        return httpx.Response(204)

    config = RepertoireChantierConfig(display_path=sharing_url)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        gateway = GraphExcelWorkbookGateway(
            graph=GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
            config=config,
        )
        async with gateway.session():
            await gateway.update_range_values("2026", "A1", [["value"]])

    sharing_token = "u!" + base64.urlsafe_b64encode(sharing_url.encode()).decode().rstrip("=")
    assert requests[0].url.path == f"/v1.0/shares/{sharing_token}/driveItem"
    assert config.drive_id == "drive"
    assert config.item_id == "item"
