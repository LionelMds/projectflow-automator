from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

import httpx

from projectflow.exceptions import GraphError

JSON = dict[str, Any]


class AccessTokenProvider(Protocol):
    async def get_access_token(self, scopes: list[str] | None = None) -> str:
        """Return a valid Microsoft Graph access token."""


@dataclass(frozen=True, slots=True)
class GraphPage:
    value: list[JSON]
    next_link: str | None = None


class GraphClient:
    def __init__(
        self,
        token_provider: AccessTokenProvider,
        *,
        base_url: str = "https://graph.microsoft.com/v1.0",
        timeout: float = 30.0,
        max_retries: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get(
        self,
        path: str,
        *,
        params: Mapping[str, str | int | bool] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JSON:
        return await self.request("GET", path, params=params, headers=headers)

    async def post(
        self,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JSON:
        return await self.request("POST", path, json=json, headers=headers)

    async def patch(
        self,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JSON:
        return await self.request("PATCH", path, json=json, headers=headers)

    async def delete(self, path: str, *, headers: Mapping[str, str] | None = None) -> None:
        await self.request("DELETE", path, headers=headers)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int | bool] | None = None,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JSON:
        token = await self._token_provider.get_access_token()
        request_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if json is not None:
            request_headers["Content-Type"] = "application/json"
        if headers is not None:
            request_headers.update(headers)

        url = path if path.startswith("https://") else f"{self._base_url}/{path.lstrip('/')}"
        response = await self._send_with_retry(
            method,
            url,
            headers=request_headers,
            params=params,
            json=json,
        )
        if response.status_code == httpx.codes.NO_CONTENT:
            return {}

        try:
            payload = response.json()
        except ValueError as exc:
            raise GraphError(
                "Graph a retourne une reponse non JSON.",
                status_code=response.status_code,
            ) from exc

        if not response.is_success:
            message = _extract_graph_error(payload)
            raise GraphError(message, status_code=response.status_code)
        return cast("JSON", payload)

    async def get_all_pages(
        self,
        path: str,
        *,
        params: Mapping[str, str | int | bool] | None = None,
    ) -> list[JSON]:
        first_page = await self.get_page(path, params=params)
        values = list(first_page.value)
        next_link = first_page.next_link
        while next_link:
            page = await self.get_page(next_link)
            values.extend(page.value)
            next_link = page.next_link
        return values

    async def get_page(
        self,
        path: str,
        *,
        params: Mapping[str, str | int | bool] | None = None,
    ) -> GraphPage:
        payload = await self.get(path, params=params)
        raw_value = payload.get("value")
        if not isinstance(raw_value, list):
            raise GraphError("Graph n'a pas retourne une page valide.")
        values = [cast("JSON", item) for item in raw_value if isinstance(item, dict)]
        next_link = payload.get("@odata.nextLink")
        return GraphPage(value=values, next_link=next_link if isinstance(next_link, str) else None)

    async def _send_with_retry(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str | int | bool] | None,
        json: Mapping[str, Any] | None,
    ) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            response = await self._client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
            )
            if response.status_code not in {429, 503, 504} or attempt == self._max_retries:
                return response
            await asyncio.sleep(_retry_delay(response, attempt))
        raise GraphError("Graph n'a pas repondu apres plusieurs tentatives.")


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return float(min(float(retry_after), 30.0))
        except ValueError:
            pass
    return float(min(0.5 * (2**attempt), 8.0))


def _extract_graph_error(payload: Mapping[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, Mapping):
        message = error.get("message")
        code = error.get("code")
        if isinstance(message, str) and isinstance(code, str):
            return f"Graph error {code}: {message}"
        if isinstance(message, str):
            return message
    return "Microsoft Graph a retourne une erreur."
