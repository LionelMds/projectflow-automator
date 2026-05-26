from __future__ import annotations

import pytest

from projectflow.auth import msal_client
from projectflow.auth.msal_client import GRAPH_SCOPES, PLANNER_GRAPH_SCOPES, MsalAccessTokenProvider
from projectflow.exceptions import AuthError


def test_graph_scopes_do_not_include_msal_reserved_scopes() -> None:
    assert set(GRAPH_SCOPES).isdisjoint({"offline_access", "profile", "openid"})
    assert set(PLANNER_GRAPH_SCOPES).isdisjoint({"offline_access", "profile", "openid"})
    assert GRAPH_SCOPES == ("Files.ReadWrite.All",)
    assert PLANNER_GRAPH_SCOPES == ("Tasks.ReadWrite", "User.Read", "GroupMember.Read.All")


def test_msal_provider_rejects_placeholder_client_id() -> None:
    provider = MsalAccessTokenProvider(client_id="TON_CLIENT_ID")

    with pytest.raises(AuthError, match="Client ID Microsoft invalide"):
        provider._access_token_sync()  # noqa: SLF001


def test_msal_provider_reuses_in_memory_token_and_saves_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = FakeStorage()
    fake_module = FakeMsalModule()
    monkeypatch.setattr(msal_client, "_msal_module", lambda: fake_module)
    provider = MsalAccessTokenProvider(
        client_id="11111111-1111-1111-1111-111111111111",
        cache_storage=storage,  # type: ignore[arg-type]
    )

    first_token = provider._access_token_sync()  # noqa: SLF001
    second_token = provider._access_token_sync()  # noqa: SLF001

    assert first_token == "token"
    assert second_token == "token"
    assert fake_module.app.interactive_calls == 1
    assert storage.saved == ["serialized-cache"]
    assert fake_module.app.interactive_scopes == [
        ["Files.ReadWrite.All"],
    ]


def test_msal_provider_passes_list_scopes_to_silent_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module = FakeMsalModule(accounts=[object()], silent_token="silent-token")
    monkeypatch.setattr(msal_client, "_msal_module", lambda: fake_module)
    provider = MsalAccessTokenProvider(
        client_id="11111111-1111-1111-1111-111111111111",
        cache_storage=FakeStorage(),  # type: ignore[arg-type]
    )

    assert provider._access_token_sync() == "silent-token"  # noqa: SLF001
    assert fake_module.app.silent_scopes == [
        ["Files.ReadWrite.All"],
    ]


def test_msal_provider_wraps_msal_parameter_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module = FakeMsalModule(assert_on_silent=True, accounts=[object()])
    monkeypatch.setattr(msal_client, "_msal_module", lambda: fake_module)
    provider = MsalAccessTokenProvider(
        client_id="11111111-1111-1111-1111-111111111111",
        cache_storage=FakeStorage(),  # type: ignore[arg-type]
    )

    with pytest.raises(AuthError, match="Invalid parameter type"):
        provider._access_token_sync()  # noqa: SLF001


class FakeStorage:
    def __init__(self) -> None:
        self.saved: list[str] = []

    def load(self) -> str:
        return ""

    def save(self, value: str) -> None:
        self.saved.append(value)


class FakeCache:
    has_state_changed = False

    def deserialize(self, state: str) -> None:
        return None

    def serialize(self) -> str:
        return "serialized-cache"


class FakePublicClientApplication:
    def __init__(
        self,
        *,
        accounts: list[object] | None = None,
        silent_token: str = "",
        assert_on_silent: bool = False,
    ) -> None:
        self._accounts = accounts or []
        self._silent_token = silent_token
        self._assert_on_silent = assert_on_silent
        self.interactive_calls = 0
        self.interactive_scopes: list[list[str]] = []
        self.silent_scopes: list[list[str]] = []

    def get_accounts(self) -> list[object]:
        return self._accounts

    def acquire_token_silent(self, scopes: object, **_kwargs: object) -> object:
        if self._assert_on_silent:
            raise AssertionError("Invalid parameter type")
        assert isinstance(scopes, list)
        self.silent_scopes.append(scopes)
        if self._silent_token:
            return {"access_token": self._silent_token}
        return None

    def acquire_token_interactive(self, *, scopes: object, **_kwargs: object) -> object:
        assert isinstance(scopes, list)
        self.interactive_scopes.append(scopes)
        self.interactive_calls += 1
        return {"access_token": "token"}


class FakeMsalModule:
    def __init__(
        self,
        *,
        accounts: list[object] | None = None,
        silent_token: str = "",
        assert_on_silent: bool = False,
    ) -> None:
        self.app = FakePublicClientApplication(
            accounts=accounts,
            silent_token=silent_token,
            assert_on_silent=assert_on_silent,
        )

    def SerializableTokenCache(self) -> FakeCache:  # noqa: N802
        return FakeCache()

    def PublicClientApplication(  # noqa: N802
        self,
        *_args: object,
        **_kwargs: object,
    ) -> FakePublicClientApplication:
        return self.app
