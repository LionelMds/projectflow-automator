from __future__ import annotations

import asyncio
import contextlib
import re
import threading
import time
from collections.abc import Callable, Sequence
from importlib import import_module
from typing import Protocol, cast

from projectflow.auth.browser_sign_in import run_browser_sign_in
from projectflow.auth.token_storage import TokenCacheStorage
from projectflow.exceptions import AuthError
from projectflow.logging import get_logger

AUTHORITY = "https://login.microsoftonline.com/common"
GRAPH_SCOPES = ("Files.ReadWrite.All",)
PLANNER_GRAPH_SCOPES = (
    "Tasks.ReadWrite",
    "User.Read",
    "User.ReadBasic.All",
    "GroupMember.Read.All",
)
# Browser sign-in that is never completed must not block every Graph operation.
INTERACTIVE_TIMEOUT_SECONDS = 300
# Refresh before Microsoft rejects the token (access tokens last about one hour).
TOKEN_REFRESH_MARGIN_SECONDS = 300
DEFAULT_TOKEN_LIFETIME_SECONDS = 3600
# One browser sign-in at a time for the whole application: the repertoire and
# Planner connections otherwise opened concurrent sign-in pages.
_INTERACTIVE_SIGN_IN_LOCK = threading.Lock()
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
    def get_accounts(self) -> list[dict[str, object]]:
        """Return cached MSAL accounts."""

    def remove_account(self, account: dict[str, object]) -> None:
        """Forget one cached account."""

    def acquire_token_silent(
        self,
        scopes: list[str],
        *,
        account: object,
    ) -> object:
        """Try to refresh a token without UI."""

    def initiate_auth_code_flow(self, scopes: list[str], **kwargs: object) -> dict[str, object]:
        """Prepare the browser sign-in URL."""

    def acquire_token_by_auth_code_flow(
        self,
        auth_code_flow: dict[str, object],
        auth_response: dict[str, object],
    ) -> object:
        """Exchange the browser response for tokens."""


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
        interactive_timeout: int | None = INTERACTIVE_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client_id = client_id.strip()
        self._scopes = list(scopes)
        self._cache_storage = cache_storage or TokenCacheStorage()
        self._cached_token: str = ""
        self._token_expires_at = 0.0
        self._interactive_timeout = interactive_timeout
        self._clock = clock
        self._lock = threading.Lock()
        self._app: PublicClientApplicationProtocol | None = None
        self._cache: SerializableTokenCacheProtocol | None = None

    async def access_token(self) -> str:
        return await asyncio.to_thread(self._access_token_sync)

    def invalidate_token(self) -> None:
        """Forget the in-memory token so the next call refreshes it through MSAL."""
        with self._lock:
            self._cached_token = ""
            self._token_expires_at = 0.0

    def _access_token_sync(self) -> str:
        # Concurrent operations must share one refresh or one browser sign-in.
        with self._lock:
            if self._cached_token and self._clock() < self._token_expires_at:
                return self._cached_token
            self._cached_token = ""
            return self._refresh_token()

    def _refresh_token(self) -> str:
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

        started = time.monotonic()
        app, cache = self._application()
        try:
            result = self._acquire_token(app, cache)
        except (AssertionError, ValueError) as exc:
            raise AuthError(f"Connexion Microsoft impossible: {exc}") from exc

        token = result.get("access_token")
        get_logger(__name__).info(
            "auth.token.refreshed",
            scopes=self._scopes,
            success=isinstance(token, str) and bool(token),
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        if isinstance(token, str) and token:
            self._cached_token = token
            self._token_expires_at = self._clock() + _token_lifetime(result)
            serialized = cache.serialize()
            if serialized:
                self._cache_storage.save(serialized)
            return token

        message = (
            result.get("error_description")
            or result.get("error")
            or "authentification annulee ou delai depasse"
        )
        raise AuthError(
            f"Connexion Microsoft impossible: {message}. "
            "Utilisez Parametres > Se reconnecter au compte Microsoft pour recommencer.",
        )

    def _application(
        self,
    ) -> tuple[PublicClientApplicationProtocol, SerializableTokenCacheProtocol]:
        # Reusing the application avoids a new authority discovery on each refresh.
        if self._app is None or self._cache is None:
            msal = _msal_module()
            self._cache = msal.SerializableTokenCache()
            self._app = msal.PublicClientApplication(
                self._client_id,
                authority=AUTHORITY,
                token_cache=self._cache,
            )
        return self._app, self._cache

    def _acquire_token(
        self,
        app: PublicClientApplicationProtocol,
        cache: SerializableTokenCacheProtocol,
    ) -> dict[str, object]:
        result, login_hint = self._acquire_token_silent(app, cache)
        if result is not None:
            return result
        with _INTERACTIVE_SIGN_IN_LOCK:
            # Another connection may have completed a sign-in while this one waited.
            result, login_hint = self._acquire_token_silent(app, cache)
            if result is not None:
                return result
            # A known account only needs to confirm access (e.g. Planner consent);
            # otherwise let the user choose which Microsoft account to use.
            result = run_browser_sign_in(
                app,
                self._scopes,
                timeout=self._interactive_timeout,
                prompt=None if login_hint else "select_account",
                login_hint=login_hint,
            )
            if "access_token" in result:
                _keep_only_signed_in_account(app, result)
            return result

    def _acquire_token_silent(
        self,
        app: PublicClientApplicationProtocol,
        cache: SerializableTokenCacheProtocol,
    ) -> tuple[dict[str, object] | None, str | None]:
        # Another provider may have refreshed or cleared the shared cache meanwhile.
        cache.deserialize(self._cache_storage.load() or "{}")
        accounts = app.get_accounts()
        if not accounts:
            return None, None
        account = accounts[0]
        result = app.acquire_token_silent(self._scopes, account=account)
        if isinstance(result, dict) and "access_token" in result:
            return cast("dict[str, object]", result), None
        error = result.get("error") if isinstance(result, dict) else None
        get_logger(__name__).info("auth.silent.failed", scopes=self._scopes, error=error)
        username = account.get("username")
        return None, username if isinstance(username, str) and username else None


def _keep_only_signed_in_account(
    app: PublicClientApplicationProtocol,
    result: dict[str, object],
) -> None:
    # Several cached accounts made silent refresh pick an arbitrary one, which
    # then lacked access to the shared workbook or to Planner.
    claims = result.get("id_token_claims")
    if not isinstance(claims, dict):
        return
    home_account_id = f"{claims.get('oid')}.{claims.get('tid')}"
    username = str(claims.get("preferred_username") or "").casefold()

    def is_signed_in(account: dict[str, object]) -> bool:
        return account.get("home_account_id") == home_account_id or (
            bool(username) and str(account.get("username") or "").casefold() == username
        )

    accounts = [account for account in app.get_accounts() if isinstance(account, dict)]
    if not any(is_signed_in(account) for account in accounts):
        return
    for account in accounts:
        if not is_signed_in(account):
            app.remove_account(account)


def sign_out_microsoft(cache_storage: TokenCacheStorage | None = None) -> None:
    """Forget the stored Microsoft account; the next access asks to sign in again."""
    (cache_storage or TokenCacheStorage()).clear()


def _token_lifetime(result: dict[str, object]) -> float:
    expires_in = result.get("expires_in")
    lifetime = float(DEFAULT_TOKEN_LIFETIME_SECONDS)
    if isinstance(expires_in, int | float | str):
        with contextlib.suppress(ValueError):
            lifetime = float(expires_in)
    return max(0.0, lifetime - TOKEN_REFRESH_MARGIN_SECONDS)


def _msal_module() -> MsalModuleProtocol:
    try:
        return cast("MsalModuleProtocol", import_module("msal"))
    except ImportError as exc:
        raise AuthError("Le module Microsoft MSAL n'est pas installe.") from exc
