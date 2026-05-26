from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Sequence
from importlib import import_module
from typing import Protocol, cast

from projectflow.auth.token_storage import TokenCacheStorage
from projectflow.exceptions import AuthError

AUTHORITY = "https://login.microsoftonline.com/common"
GRAPH_SCOPES = ("Files.ReadWrite.All",)
PLANNER_GRAPH_SCOPES = (
    "Tasks.ReadWrite",
    "User.Read",
    "User.ReadBasic.All",
    "GroupMember.Read.All",
)
CLIENT_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
)


class AccessTokenProvider(Protocol):
    async def access_token(self) -> str:
        """Return a valid bearer token."""


class SerializableTokenCacheProtocol(Protocol):
    has_state_changed: bool

    def deserialize(self, state: str) -> None:
        """Load a serialized MSAL cache."""

    def serialize(self) -> str:
        """Return the serialized MSAL cache."""


class PublicClientApplicationProtocol(Protocol):
    def get_accounts(self) -> list[object]:
        """Return cached MSAL accounts."""

    def acquire_token_silent(
        self,
        scopes: list[str],
        *,
        account: object,
    ) -> object:
        """Try to refresh a token without UI."""

    def acquire_token_interactive(
        self,
        *,
        scopes: list[str],
        port: int,
        prompt: str,
    ) -> object:
        """Open the browser sign-in flow."""


class MsalModuleProtocol(Protocol):
    SerializableTokenCache: Callable[[], SerializableTokenCacheProtocol]
    PublicClientApplication: Callable[..., PublicClientApplicationProtocol]


class MsalAccessTokenProvider:
    def __init__(
        self,
        *,
        client_id: str,
        scopes: Sequence[str] = GRAPH_SCOPES,
        cache_storage: TokenCacheStorage | None = None,
    ) -> None:
        self._client_id = client_id.strip()
        self._scopes = list(scopes)
        self._cache_storage = cache_storage or TokenCacheStorage()
        self._cached_token: str = ""

    async def access_token(self) -> str:
        return await asyncio.to_thread(self._access_token_sync)

    def _access_token_sync(self) -> str:
        if self._cached_token:
            return self._cached_token

        if not self._client_id:
            raise AuthError(
                "Connexion Microsoft indisponible: client Microsoft non configure "
                "dans cette version de ProjectFlow.",
            )
        if not CLIENT_ID_RE.fullmatch(self._client_id):
            raise AuthError(
                "Client ID Microsoft invalide. Renseignez le vrai 'Application (client) ID' "
                "Microsoft, par exemple via PROJECTFLOW_MICROSOFT_CLIENT_ID, pas le texte "
                "'TON_CLIENT_ID'.",
            )

        msal = _msal_module()
        cache = msal.SerializableTokenCache()
        serialized_cache = self._cache_storage.load()
        if serialized_cache:
            cache.deserialize(serialized_cache)

        app = msal.PublicClientApplication(
            self._client_id,
            authority=AUTHORITY,
            token_cache=cache,
        )
        try:
            result = self._acquire_token(app)
        except (AssertionError, ValueError) as exc:
            raise AuthError(f"Connexion Microsoft impossible: {exc}") from exc

        token = result.get("access_token")
        if isinstance(token, str) and token:
            self._cached_token = token
            serialized = cache.serialize()
            if serialized:
                self._cache_storage.save(serialized)
            return token

        message = (
            result.get("error_description")
            or result.get("error")
            or "authentification annulee"
        )
        raise AuthError(f"Connexion Microsoft impossible: {message}")

    def _acquire_token(self, app: PublicClientApplicationProtocol) -> dict[str, object]:
        accounts = app.get_accounts()
        if accounts:
            result = app.acquire_token_silent(self._scopes, account=accounts[0])
            if isinstance(result, dict) and "access_token" in result:
                return cast("dict[str, object]", result)

        result = app.acquire_token_interactive(
            scopes=self._scopes,
            port=0,
            prompt="select_account",
        )
        if isinstance(result, dict):
            return cast("dict[str, object]", result)
        return {}


def _msal_module() -> MsalModuleProtocol:
    try:
        return cast("MsalModuleProtocol", import_module("msal"))
    except ImportError as exc:
        raise AuthError("Le module Microsoft MSAL n'est pas installe.") from exc
