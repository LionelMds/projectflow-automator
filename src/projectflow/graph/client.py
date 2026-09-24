from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import Any

import httpx

from projectflow.auth.msal_client import AccessTokenProvider
from projectflow.exceptions import GraphError
from projectflow.logging import get_logger

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
RETRY_STATUSES = {429, 500, 502, 503, 504}
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_LOCKED = 423
HTTP_TOO_MANY_REQUESTS = 429
MAX_ATTEMPTS = 3
# Longer route segments are identifiers (drives, items, plans) and stay out of logs.
LOG_ID_MIN_LENGTH = 16
UNAUTHORIZED_MESSAGE = (
    "La connexion Microsoft a expire ou a ete refusee (401). "
    "Utilisez Parametres > Se reconnecter au compte Microsoft, "
    "puis actualisez les donnees."
)
WORKBOOK_CONFLICT_CODES = {
    "accessconflict",
    "conflictuncategorized",
    "invalidsessionaccessconflict",
}
RequestTimeout = float | httpx.Timeout | None


class GraphClient:
    def __init__(
        self,
        *,
        token_provider: AccessTokenProvider,
        http_client: httpx.AsyncClient | None = None,
        base_url: str = GRAPH_BASE_URL,
        request_timeout: RequestTimeout = None,
    ) -> None:
        self._token_provider = token_provider
        self._http_client = http_client
        self._base_url = base_url.rstrip("/")
        self._request_timeout = request_timeout

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

    async def delete(
        self,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        return await self.request("DELETE", path, headers=headers)

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        last_response = await self._send(method, path, json=json, headers=headers)
        invalidate_token = getattr(self._token_provider, "invalidate_token", None)
        if last_response.status_code == HTTP_UNAUTHORIZED and callable(invalidate_token):
            # Microsoft rejected the token before processing the request, so a
            # single replay with a refreshed token cannot duplicate a mutation.
            invalidate_token()
            last_response = await self._send(method, path, json=json, headers=headers)
        if last_response.is_error:
            raise _response_error(last_response, method)
        if not last_response.content:
            return {}
        try:
            payload = last_response.json()
        except ValueError as exc:
            raise GraphError(
                "Microsoft Graph a retourne une reponse illisible. "
                "Actualisez les donnees avant de relancer l'operation.",
                status_code=last_response.status_code,
            ) from exc
        if isinstance(payload, dict):
            return payload
        return {"value": payload}

    async def _send(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None,
        headers: Mapping[str, str] | None,
    ) -> httpx.Response:
        started = time.perf_counter()
        request_headers = dict(headers or {})
        request_headers["Authorization"] = f"Bearer {await self._token_provider.access_token()}"
        request_headers.setdefault("Accept", "application/json")
        token_ms = _elapsed_ms(started)

        last_response: httpx.Response | None = None
        for attempt in range(MAX_ATTEMPTS):
            sent = time.perf_counter()
            try:
                response = await self._client().request(
                    method,
                    self._url(path),
                    json=json,
                    headers=request_headers,
                    timeout=self._request_timeout,
                )
            except httpx.TimeoutException as exc:
                raise GraphError(
                    "Microsoft Graph ne repond pas assez vite. "
                    "Verifiez la connexion internet et actualisez les donnees "
                    "avant de relancer l'operation.",
                ) from exc
            except httpx.RequestError as exc:
                raise GraphError(
                    "La communication avec Microsoft Graph a ete interrompue. "
                    "Verifiez la connexion internet et actualisez les donnees "
                    "avant de relancer l'operation.",
                ) from exc
            last_response = response
            # Timings without identifiers or links, to diagnose slow workstations.
            get_logger(__name__).info(
                "graph.request",
                method=method,
                route=_log_route(path),
                status=response.status_code,
                attempt=attempt + 1,
                token_ms=token_ms,
                duration_ms=_elapsed_ms(sent),
            )
            if attempt + 1 == MAX_ATTEMPTS or not _should_retry(response, method):
                break
            retry_after = _retry_after_seconds(response)
            await asyncio.sleep(retry_after if retry_after is not None else 0.5 * (attempt + 1))

        if last_response is None:
            raise GraphError("Microsoft Graph n'a retourne aucune reponse.")
        return last_response

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


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _log_route(path: str) -> str:
    route = path.split("?", 1)[0].removeprefix(GRAPH_BASE_URL)
    segments = [
        "{id}" if len(segment) >= LOG_ID_MIN_LENGTH or "!" in segment or "%" in segment else segment
        for segment in route.split("/")
    ]
    return "/".join(segments)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw_value = response.headers.get("Retry-After")
    if raw_value is None:
        return None
    try:
        return max(0.0, float(raw_value))
    except ValueError:
        return None


def _graph_error_message(response: httpx.Response) -> str:
    codes = {code.lower() for code in _graph_error_codes(response) if code is not None}
    if codes & WORKBOOK_CONFLICT_CODES or response.status_code == HTTP_LOCKED:
        return (
            "Le repertoire Excel partage est verrouille ou en conflit avec une autre "
            "session. Ouvrez le fichier original dans Excel pour le web depuis "
            "OneDrive/SharePoint et resolvez le conflit ou la synchronisation en attente, "
            "puis actualisez le repertoire dans ProjectFlow. "
            "Ne remplacez pas le fichier original par une copie non fusionnee."
        )
    if any(code.startswith("invalidsession") for code in codes):
        return (
            "La session Excel du repertoire partage n'est plus valide. "
            "Actualisez le repertoire pour ouvrir une nouvelle session ; "
            "si le probleme persiste, ouvrez le fichier original dans Excel pour le web "
            "pour verifier ses acces et son etat."
        )
    if response.status_code == HTTP_FORBIDDEN:
        return (
            "Microsoft Graph a refuse l'operation (403): acces refuse. "
            "Verifiez que ProjectFlow dispose des autorisations Microsoft necessaires "
            "(Files.ReadWrite.All, Tasks.ReadWrite, User.Read, User.ReadBasic.All, "
            "GroupMember.Read.All selon la fonction) et que "
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


def _response_error(response: httpx.Response, method: str) -> GraphError:
    error_code, inner_error_code = _graph_error_codes(response)
    if response.status_code == HTTP_UNAUTHORIZED:
        message = UNAUTHORIZED_MESSAGE
    else:
        message = _graph_error_message(response)
    if method.upper() not in {"GET", "HEAD"} and response.status_code in RETRY_STATUSES - {
        HTTP_TOO_MANY_REQUESTS
    }:
        message += (
            " Le resultat de l'operation peut etre incomplet. "
            "Actualisez les donnees avant de recommencer."
        )
    return GraphError(
        message,
        status_code=response.status_code,
        error_code=error_code,
        inner_error_code=inner_error_code,
    )


def _graph_error_codes(response: httpx.Response) -> tuple[str | None, str | None]:
    try:
        payload = response.json()
    except ValueError:
        return None, None
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return None, None
    code = error.get("code")
    inner_error = error.get("innerError", error.get("innererror"))
    inner_code = inner_error.get("code") if isinstance(inner_error, dict) else None
    return (
        code if isinstance(code, str) else None,
        inner_code if isinstance(inner_code, str) else None,
    )


def _should_retry(response: httpx.Response, method: str) -> bool:
    if response.status_code not in RETRY_STATUSES:
        return False
    codes = {code.lower() for code in _graph_error_codes(response) if code is not None}
    if codes & WORKBOOK_CONFLICT_CODES or any(
        code.startswith("invalidsession") or code == "internalservererroruncategorized"
        for code in codes
    ):
        return False
    # A failed mutation may already have taken effect. Replaying an insertion can
    # duplicate rows and invalidate the addresses calculated by the caller.
    return response.status_code == HTTP_TOO_MANY_REQUESTS or method.upper() in {"GET", "HEAD"}
