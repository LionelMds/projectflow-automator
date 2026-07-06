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
