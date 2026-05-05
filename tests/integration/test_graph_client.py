from __future__ import annotations

import httpx
import pytest

from projectflow.exceptions import GraphError
from projectflow.graph.client import GraphClient


class StaticTokenProvider:
    async def get_access_token(self, scopes: list[str] | None = None) -> str:
        return "token"


@pytest.mark.asyncio
async def test_graph_client_sends_bearer_token() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"displayName": "Lionel"})

    client = GraphClient(
        StaticTokenProvider(),
        base_url="https://graph.example/v1.0",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    payload = await client.get("/me", headers={"workbook-session-id": "session"})

    assert payload == {"displayName": "Lionel"}
    assert requests[0].headers["Authorization"] == "Bearer token"
    assert requests[0].headers["workbook-session-id"] == "session"


@pytest.mark.asyncio
async def test_graph_client_raises_typed_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": {"code": "Forbidden", "message": "Missing scope"}},
        )

    client = GraphClient(
        StaticTokenProvider(),
        base_url="https://graph.example/v1.0",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(GraphError, match="Forbidden"):
        await client.get("/me")


@pytest.mark.asyncio
async def test_graph_client_reads_all_pages() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://graph.example/v1.0/items":
            return httpx.Response(
                200,
                json={"value": [{"id": "1"}], "@odata.nextLink": "https://graph.example/v1.0/items?page=2"},
            )
        return httpx.Response(200, json={"value": [{"id": "2"}]})

    client = GraphClient(
        StaticTokenProvider(),
        base_url="https://graph.example/v1.0",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    assert await client.get_all_pages("/items") == [{"id": "1"}, {"id": "2"}]


@pytest.mark.asyncio
async def test_graph_client_retries_transient_errors() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        del request
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {}})
        return httpx.Response(200, json={"ok": True})

    client = GraphClient(
        StaticTokenProvider(),
        base_url="https://graph.example/v1.0",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    assert await client.get("/me") == {"ok": True}
    assert attempts == 2
