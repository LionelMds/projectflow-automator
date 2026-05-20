from __future__ import annotations

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
