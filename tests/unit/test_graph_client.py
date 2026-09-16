from __future__ import annotations

import httpx
import pytest

from projectflow.exceptions import GraphError
from projectflow.graph.client import GraphClient


class FakeTokenProvider:
    async def access_token(self) -> str:
        return "token"


@pytest.mark.asyncio
async def test_graph_client_disables_request_timeout_by_default() -> None:
    seen_timeout: object | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_timeout
        seen_timeout = request.extensions.get("timeout")
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GraphClient(token_provider=FakeTokenProvider(), http_client=http_client)

        await client.get("/me/drive/root")

    assert isinstance(seen_timeout, dict)
    assert set(seen_timeout) == {"connect", "read", "write", "pool"}
    assert all(value is None for value in seen_timeout.values())


@pytest.mark.asyncio
async def test_graph_client_wraps_network_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow network", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GraphClient(token_provider=FakeTokenProvider(), http_client=http_client)

        with pytest.raises(GraphError, match="ne repond pas assez vite"):
            await client.get("/me/drive/root")


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
async def test_graph_client_never_replays_uncertain_mutations(method: str) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503, json={"error": {"code": "serviceUnavailable"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GraphClient(token_provider=FakeTokenProvider(), http_client=http_client)
        with pytest.raises(GraphError):
            await client.request(method, "/workbook/tables/table/rows/add")

    assert len(requests) == 1


@pytest.mark.asyncio
async def test_graph_client_retries_throttled_mutation() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"index": 2})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GraphClient(token_provider=FakeTokenProvider(), http_client=http_client)
        assert await client.post("/workbook/tables/table/rows/add") == {"index": 2}

    assert len(requests) == 2


@pytest.mark.asyncio
async def test_graph_client_retries_transient_read_failure() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"value": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GraphClient(token_provider=FakeTokenProvider(), http_client=http_client)
        assert await client.get("/workbook/worksheets") == {"value": []}

    assert len(requests) == 2


@pytest.mark.asyncio
async def test_graph_client_reports_conflict_code_without_retrying_invalid_session() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            503,
            json={
                "error": {
                    "code": "serviceUnavailable",
                    "innerError": {"code": "invalidSessionAccessConflict"},
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GraphClient(token_provider=FakeTokenProvider(), http_client=http_client)
        with pytest.raises(GraphError, match="Excel pour le web") as caught:
            await client.get("/workbook/worksheets")

    assert len(requests) == 1
    assert caught.value.status_code == 503
    assert caught.value.error_code == "serviceUnavailable"
    assert caught.value.inner_error_code == "invalidSessionAccessConflict"
    assert caught.value.invalid_workbook_session


@pytest.mark.asyncio
async def test_graph_client_wraps_disconnect_without_replaying_write() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadError("disconnected", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GraphClient(token_provider=FakeTokenProvider(), http_client=http_client)
        with pytest.raises(GraphError, match="actualisez les donnees"):
            await client.post("/workbook/tables/table/rows/add")

    assert len(requests) == 1


@pytest.mark.asyncio
async def test_graph_client_reports_unreadable_success_without_replaying_write() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="incomplete response")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GraphClient(token_provider=FakeTokenProvider(), http_client=http_client)
        with pytest.raises(GraphError, match="reponse illisible"):
            await client.post("/workbook/tables/table/rows/add")

    assert len(requests) == 1
