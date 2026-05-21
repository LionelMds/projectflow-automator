from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import httpx

from projectflow.auth.msal_client import AccessTokenProvider
from projectflow.exceptions import GraphError

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
RETRY_STATUSES = {429, 500, 502, 503, 504}
HTTP_FORBIDDEN = 403


class GraphClient:
    def __init__(
        self,
        *,
        token_provider: AccessTokenProvider,
        http_client: httpx.AsyncClient | None = None,
        base_url: str = GRAPH_BASE_URL,
    ) -> None:
        self._token_provider = token_provider
        self._http_client = http_client
        self._base_url = base_url.rstrip("/")

    async def get(
        self,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        return await self.request("GET", path, headers=headers)

    async def post(
        self,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        return await self.request("POST", path, json=json, headers=headers)

    async def patch(
        self,
        path: str,
        *,
        json: Mapping[str, Any],
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        return await self.request("PATCH", path, json=json, headers=headers)

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        request_headers = dict(headers or {})
        request_headers["Authorization"] = f"Bearer {await self._token_provider.access_token()}"
        request_headers.setdefault("Accept", "application/json")

        last_response: httpx.Response | None = None
        for attempt in range(3):
            response = await self._client().request(
                method,
                self._url(path),
                json=json,
                headers=request_headers,
                timeout=30,
            )
            last_response = response
            if response.status_code not in RETRY_STATUSES:
                break
            retry_after = _retry_after_seconds(response)
            await asyncio.sleep(retry_after if retry_after is not None else 0.5 * (attempt + 1))

        if last_response is None:
            raise GraphError("Microsoft Graph n'a retourne aucune reponse.")
        if last_response.is_error:
            raise GraphError(
                _graph_error_message(last_response),
                status_code=last_response.status_code,
            )
        if not last_response.content:
            return {}
        payload = last_response.json()
        if isinstance(payload, dict):
            return payload
        return {"value": payload}

    def _client(self) -> httpx.AsyncClient:
        if self._http_client is not None:
            return self._http_client
        self._http_client = httpx.AsyncClient()
        return self._http_client

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return f"{self._base_url}/{path.lstrip('/')}"

    async def aclose(self) -> None:
        if self._http_client is None:
            return
        await self._http_client.aclose()


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw_value = response.headers.get("Retry-After")
    if raw_value is None:
        return None
    try:
        return max(0.0, float(raw_value))
    except ValueError:
        return None


def _graph_error_message(response: httpx.Response) -> str:
    if response.status_code == HTTP_FORBIDDEN:
        return (
            "Microsoft Graph a refuse l'operation (403): acces refuse. "
            "Verifiez que ProjectFlow dispose des autorisations Microsoft necessaires "
            "(Files.ReadWrite.All, Tasks.ReadWrite, User.Read selon la fonction) et que "
            "l'utilisateur connecte a acces a la ressource."
        )
    try:
        payload = response.json()
    except ValueError:
        return f"Microsoft Graph a refuse l'operation ({response.status_code})."
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return f"Microsoft Graph a refuse l'operation ({response.status_code}): {message}"
    return f"Microsoft Graph a refuse l'operation ({response.status_code})."
