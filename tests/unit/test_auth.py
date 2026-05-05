from __future__ import annotations

from typing import Any

import httpx
import keyring
import pytest

from projectflow.application_settings import CLIENT_ID_ENV
from projectflow.auth import msal_client
from projectflow.auth.msal_client import LoopbackCallbackServer, MsalAuthClient
from projectflow.auth.token_storage import KeyringTokenStorage
from projectflow.exceptions import AuthError


class MemoryStorage:
    def __init__(self, value: str | None = None) -> None:
        self.value = value
        self.cleared = False

    def load(self) -> str | None:
        return self.value

    def save(self, serialized_cache: str) -> None:
        self.value = serialized_cache

    def clear(self) -> None:
        self.cleared = True


class FakeCache:
    has_state_changed = True

    def __init__(self) -> None:
        self.serialized = ""

    def deserialize(self, serialized_cache: str) -> None:
        self.serialized = serialized_cache

    def serialize(self) -> str:
        return "serialized"


class FakePublicClientApplication:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.removed: list[dict[str, str]] = []
        self.accounts: list[dict[str, str]] = [{"home_account_id": "1"}]
        self.silent_result: dict[str, Any] | None = {
            "access_token": "silent",
            "expires_in": 3600,
            "id_token_claims": {"preferred_username": "lionel@balzmetal.ch"},
        }
        self.interactive_result: dict[str, Any] = {"access_token": "interactive"}

    def get_accounts(self) -> list[dict[str, str]]:
        return self.accounts

    def acquire_token_silent(
        self,
        scopes: list[str],
        *,
        account: dict[str, str],
    ) -> dict[str, Any] | None:
        del scopes, account
        return self.silent_result

    def initiate_auth_code_flow(self, scopes: list[str], *, redirect_uri: str) -> dict[str, str]:
        del scopes, redirect_uri
        return {"auth_uri": "https://login.example/auth"}

    def acquire_token_by_auth_code_flow(
        self,
        flow: dict[str, str],
        query: dict[str, str],
    ) -> dict[str, Any]:
        del flow, query
        return self.interactive_result

    def remove_account(self, account: dict[str, str]) -> None:
        self.removed.append(account)


class FakeLoopback:
    redirect_uri = "http://localhost:1234/callback"
    closed = False

    def wait_for_callback(self, *, timeout_seconds: int) -> dict[str, str]:
        del timeout_seconds
        return {"code": "abc"}

    def close(self) -> None:
        self.closed = True


def test_msal_client_requires_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CLIENT_ID_ENV, raising=False)

    with pytest.raises(AuthError):
        MsalAuthClient()


def test_msal_client_acquires_silent_token_and_persists_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(msal_client.msal, "SerializableTokenCache", FakeCache)
    monkeypatch.setattr(msal_client.msal, "PublicClientApplication", FakePublicClientApplication)
    storage = MemoryStorage("existing")

    client = MsalAuthClient(client_id="client", token_storage=storage)
    token = client.acquire_token_silent()

    assert token is not None
    assert token.access_token == "silent"
    assert token.account_username == "lionel@balzmetal.ch"
    assert storage.value == "serialized"


def test_msal_client_interactive_flow_uses_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(msal_client.msal, "SerializableTokenCache", FakeCache)
    monkeypatch.setattr(msal_client.msal, "PublicClientApplication", FakePublicClientApplication)
    monkeypatch.setattr(msal_client, "LoopbackCallbackServer", FakeLoopback)
    monkeypatch.setattr(msal_client.webbrowser, "open", lambda _url: True)

    client = MsalAuthClient(client_id="client", token_storage=MemoryStorage())
    token = client.acquire_token_interactive()

    assert token.access_token == "interactive"


def test_msal_client_sign_out_clears_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(msal_client.msal, "SerializableTokenCache", FakeCache)
    monkeypatch.setattr(msal_client.msal, "PublicClientApplication", FakePublicClientApplication)
    storage = MemoryStorage()

    MsalAuthClient(client_id="client", token_storage=storage).sign_out()

    assert storage.cleared is True


def test_loopback_callback_server_receives_query() -> None:
    server = LoopbackCallbackServer()
    try:
        httpx.get(f"{server.redirect_uri}?code=abc", timeout=5)
        assert server.wait_for_callback(timeout_seconds=1) == {"code": "abc"}
    finally:
        server.close()


def test_keyring_token_storage_wraps_keyring_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_get_password(service_name: str, account_name: str) -> str | None:
        del service_name, account_name
        raise keyring.errors.KeyringError("boom")

    monkeypatch.setattr("projectflow.auth.token_storage.keyring.get_password", fail_get_password)

    with pytest.raises(AuthError):
        KeyringTokenStorage("account").load()
