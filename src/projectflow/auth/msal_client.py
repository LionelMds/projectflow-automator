from __future__ import annotations

import threading
import webbrowser
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import msal

from projectflow.application_settings import resolve_microsoft_client_id
from projectflow.auth.token_storage import KeyringTokenStorage, TokenStorage
from projectflow.exceptions import AuthError

DEFAULT_AUTHORITY = "https://login.microsoftonline.com/common"
DEFAULT_SCOPES = [
    "User.Read",
    "Files.ReadWrite.All",
    "Mail.ReadWrite",
    "MailboxSettings.Read",
    "Tasks.ReadWrite",
]


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    tenant_id: str
    user_id: str
    display_name: str
    email: str


@dataclass(frozen=True, slots=True)
class TokenResult:
    access_token: str
    expires_in: int | None = None
    account_username: str | None = None


class MsalAuthClient:
    def __init__(
        self,
        *,
        client_id: str | None = None,
        authority: str = DEFAULT_AUTHORITY,
        scopes: Sequence[str] = DEFAULT_SCOPES,
        token_storage: TokenStorage | None = None,
    ) -> None:
        resolved_client_id = resolve_microsoft_client_id(client_id)
        if not resolved_client_id:
            raise AuthError(
                "Client ID Microsoft manquant. Definissez PROJECTFLOW_CLIENT_ID "
                "en developpement ou embarquez projectflow/resources/app_settings.json.",
            )

        self._client_id = resolved_client_id
        self._authority = authority
        self._scopes = list(scopes)
        self._token_storage = token_storage or KeyringTokenStorage(account_name=resolved_client_id)
        self._cache = msal.SerializableTokenCache()

        serialized_cache = self._token_storage.load()
        if serialized_cache:
            self._cache.deserialize(serialized_cache)

        self._app = msal.PublicClientApplication(
            client_id=resolved_client_id,
            authority=authority,
            token_cache=self._cache,
        )

    async def get_access_token(self, scopes: list[str] | None = None) -> str:
        token = self.acquire_token_silent(scopes=scopes)
        if token is None:
            raise AuthError("Session Microsoft expiree. Une reconnexion est necessaire.")
        return token.access_token

    def acquire_token_silent(self, *, scopes: Sequence[str] | None = None) -> TokenResult | None:
        requested_scopes = list(scopes or self._scopes)
        accounts = self._app.get_accounts()
        if not accounts:
            return None

        result = self._app.acquire_token_silent(requested_scopes, account=accounts[0])
        self._persist_cache_if_changed()
        if not result or "access_token" not in result:
            return None
        return _token_result(result)

    def acquire_token_interactive(
        self,
        *,
        scopes: Sequence[str] | None = None,
        timeout_seconds: int = 180,
    ) -> TokenResult:
        requested_scopes = list(scopes or self._scopes)
        loopback = LoopbackCallbackServer()
        redirect_uri = loopback.redirect_uri
        flow = self._app.initiate_auth_code_flow(requested_scopes, redirect_uri=redirect_uri)
        auth_uri = flow.get("auth_uri")
        if not isinstance(auth_uri, str):
            loopback.close()
            raise AuthError("MSAL n'a pas fourni d'URL de connexion.")

        webbrowser.open(auth_uri)
        query = loopback.wait_for_callback(timeout_seconds=timeout_seconds)
        loopback.close()

        result = self._app.acquire_token_by_auth_code_flow(flow, query)
        self._persist_cache_if_changed()
        if "access_token" not in result:
            raise AuthError(_extract_auth_error(result))
        return _token_result(result)

    def sign_out(self) -> None:
        for account in self._app.get_accounts():
            self._app.remove_account(account)
        self._persist_cache_if_changed()
        self._token_storage.clear()

    def _persist_cache_if_changed(self) -> None:
        if self._cache.has_state_changed:
            self._token_storage.save(self._cache.serialize())


class LoopbackCallbackServer:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._query: dict[str, str] | None = None

        outer = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                outer._query = {key: values[0] for key, values in query.items() if values}
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h1>Connexion terminee</h1>"
                    b"<p>Vous pouvez revenir dans ProjectFlow.</p></body></html>",
                )
                outer._event.set()

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = HTTPServer(("localhost", 0), CallbackHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def redirect_uri(self) -> str:
        host, port = self._server.server_address[:2]
        host_text = host.decode("ascii") if isinstance(host, bytes) else host
        return f"http://{host_text}:{port}/callback"

    def wait_for_callback(self, *, timeout_seconds: int) -> dict[str, str]:
        if not self._event.wait(timeout=timeout_seconds):
            raise AuthError("Connexion Microsoft annulee ou expiree.")
        if self._query is None:
            raise AuthError("Microsoft n'a pas retourne de code d'autorisation.")
        if "error" in self._query:
            raise AuthError(self._query.get("error_description", "Connexion Microsoft refusee."))
        return self._query

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def _token_result(result: Mapping[str, Any]) -> TokenResult:
    access_token = result.get("access_token")
    if not isinstance(access_token, str):
        raise AuthError("MSAL n'a pas retourne de jeton d'acces.")
    expires_in = result.get("expires_in")
    account = result.get("id_token_claims")
    username = None
    if isinstance(account, Mapping):
        preferred_username = account.get("preferred_username")
        username = preferred_username if isinstance(preferred_username, str) else None
    return TokenResult(
        access_token=access_token,
        expires_in=expires_in if isinstance(expires_in, int) else None,
        account_username=username,
    )


def _extract_auth_error(result: Mapping[str, Any]) -> str:
    description = result.get("error_description")
    if isinstance(description, str):
        return description
    error = result.get("error")
    if isinstance(error, str):
        return error
    return "Connexion Microsoft impossible."
