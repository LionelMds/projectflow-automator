from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from projectflow import services
from projectflow.application_settings import ApplicationSettings
from projectflow.auth.msal_client import (
    GRAPH_SCOPES,
    PLANNER_GRAPH_SCOPES,
    PLANNING_GRAPH_SCOPES,
    AccessTokenProvider,
)
from projectflow.config import AppConfig
from projectflow.core.planning.graph_backend import GraphPlanningBackend
from projectflow.core.planning.local_store import LocalPlanningStore
from projectflow.core.planning.poste_config import PlanningPosteConfig
from projectflow.core.planning.store import StoreUnavailableError
from projectflow.exceptions import AuthError, ConfigError, GraphError
from projectflow.graph.client import GraphClient
from projectflow.services import ServiceContainer, create_planning_store
from projectflow.ui.planning_controller import failure_status

CLIENT_ID = "11111111-2222-3333-4444-555555555555"
SITE_URL = "https://balzmetal.sharepoint.com/sites/atelier"
SETTINGS = ApplicationSettings(microsoft_client_id=CLIENT_ID)
COLUMNS = {
    "value": [
        {"name": "PfKind", "text": {}},
        {"name": "PfDocId", "text": {}},
        {"name": "PfProject", "text": {}},
        {"name": "PfData", "text": {"allowMultipleLines": True, "textType": "plain"}},
    ],
}


@dataclass
class FakeProvider:
    client_id: str
    scopes: tuple[str, ...]
    allow_interactive: bool
    refused: frozenset[tuple[str, ...]]
    unreachable: frozenset[tuple[str, ...]] = frozenset()
    authority_unreachable: frozenset[tuple[str, ...]] = frozenset()

    async def access_token(self) -> str:
        if self.scopes in self.authority_unreachable:
            # MSAL reads the authority before the first token, outside its error handling:
            # requests' own ConnectionError, an OSError, reaches the caller unchanged.
            raise ConnectionError("getaddrinfo failed")
        if self.scopes in self.unreachable:
            # As MsalAccessTokenProvider does when requests cannot reach Microsoft.
            raise AuthError("Microsoft ne repond pas pour la connexion.") from OSError(
                "getaddrinfo failed",
            )
        if self.scopes in self.refused:
            raise AuthError("Connexion Microsoft requise: ouvrez ProjectFlow.")
        return "token"


@dataclass
class Microsoft:
    """Token providers and Graph answers of one test; records every request."""

    routes: dict[str, Any] = field(default_factory=dict)
    refused: set[tuple[str, ...]] = field(default_factory=set)
    unreachable: set[tuple[str, ...]] = field(default_factory=set)
    authority_unreachable: set[tuple[str, ...]] = field(default_factory=set)
    providers: list[FakeProvider] = field(default_factory=list)
    requests: list[str] = field(default_factory=list)
    clients: list[httpx.AsyncClient] = field(default_factory=list)

    def provider(
        self,
        *,
        client_id: str,
        scopes: Sequence[str] = GRAPH_SCOPES,
        allow_interactive: bool = True,
    ) -> FakeProvider:
        provider = FakeProvider(
            client_id,
            tuple(scopes),
            allow_interactive,
            frozenset(self.refused),
            frozenset(self.unreachable),
            frozenset(self.authority_unreachable),
        )
        self.providers.append(provider)
        return provider

    def graph(self, token_provider: AccessTokenProvider) -> GraphClient:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self._answer))
        self.clients.append(client)
        return GraphClient(token_provider=token_provider, http_client=client)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        route = request.url.raw_path.decode().removeprefix("/v1.0")
        self.requests.append(route)
        answer = self.routes.get(route)
        if answer is None:
            return httpx.Response(404, json={"error": {"code": "itemNotFound"}})
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, int):
            return httpx.Response(answer, json={"error": {"code": "accessDenied"}})
        return httpx.Response(200, json=answer)


@pytest.fixture
def microsoft(monkeypatch: pytest.MonkeyPatch) -> Iterator[Microsoft]:
    fake = Microsoft()
    monkeypatch.setattr(services, "MsalAccessTokenProvider", fake.provider)
    monkeypatch.setattr(services, "_planning_graph_client", fake.graph)
    yield fake
    # Never a browser: every connection of the planning is silent.
    assert all(provider.allow_interactive is False for provider in fake.providers)


def _site_routes(fake: Microsoft) -> None:
    fake.routes["/sites/balzmetal.sharepoint.com:/sites/atelier?$select=id"] = {"id": "site-1"}
    fake.routes["/sites/site-1/lists?$select=id,displayName"] = {
        "value": [
            {"id": "list-0", "displayName": "Autre liste"},
            {"id": "list-9", "displayName": "ProjectFlow Planning"},
        ],
    }
    fake.routes["/sites/site-1/lists/list-9/columns?$select=name,text"] = COLUMNS


def _sharepoint(**fields: Any) -> PlanningPosteConfig:
    return PlanningPosteConfig(backend="sharepoint", **fields)


async def _open(
    poste: PlanningPosteConfig,
    fake: Microsoft,
    config: AppConfig | None = None,
) -> Any:
    return await create_planning_store(
        config or AppConfig(),
        poste,
        application_settings=SETTINGS,
        graph_factory=fake.graph,
    )


@pytest.mark.asyncio
async def test_local_store_needs_no_microsoft(tmp_path: Path, microsoft: Microsoft) -> None:
    poste = PlanningPosteConfig(local_directory=str(tmp_path / "planning"))

    store = await create_planning_store(AppConfig(), poste)

    assert isinstance(store, LocalPlanningStore)
    assert store.directory == tmp_path / "planning"
    assert microsoft.providers == []


@pytest.mark.asyncio
async def test_container_opens_the_local_store(tmp_path: Path, microsoft: Microsoft) -> None:
    container = ServiceContainer(AppConfig())
    poste = PlanningPosteConfig(local_directory=str(tmp_path / "planning"))

    store = await container.create_planning_store(poste)

    assert isinstance(store, LocalPlanningStore)
    assert microsoft.requests == []


@pytest.mark.asyncio
async def test_sharepoint_ids_are_resolved_from_the_site_address(microsoft: Microsoft) -> None:
    _site_routes(microsoft)
    poste = _sharepoint(sharepoint_site_url=SITE_URL)

    store = await _open(poste, microsoft)

    assert isinstance(store, GraphPlanningBackend)
    assert (poste.sharepoint_site_id, poste.sharepoint_list_id) == ("site-1", "list-9")
    assert microsoft.providers[0].scopes == PLANNING_GRAPH_SCOPES
    assert microsoft.providers[0].client_id == CLIENT_ID
    await store.aclose()


@pytest.mark.asyncio
async def test_known_ids_open_the_list_without_any_request(microsoft: Microsoft) -> None:
    poste = _sharepoint(
        sharepoint_site_url=SITE_URL,
        sharepoint_site_id="site-1",
        sharepoint_list_id="list-9",
    )

    store = await _open(poste, microsoft)

    assert isinstance(store, GraphPlanningBackend)
    assert microsoft.requests == []
    await store.aclose()


@pytest.mark.asyncio
async def test_site_of_the_planner_plan_group_is_used_without_address(
    microsoft: Microsoft,
) -> None:
    config = AppConfig()
    config.planner.plan_id = "plan-1"
    microsoft.routes["/planner/plans/plan-1?$select=id,container,owner"] = {
        "id": "plan-1",
        "container": {"containerId": "group-1", "type": "group"},
    }
    microsoft.routes["/groups/group-1/sites/root?$select=id,webUrl,displayName"] = {"id": "site-1"}
    _site_routes(microsoft)
    poste = _sharepoint()

    store = await _open(poste, microsoft, config)

    assert (poste.sharepoint_site_id, poste.sharepoint_list_id) == ("site-1", "list-9")
    assert [p.scopes for p in microsoft.providers] == [PLANNING_GRAPH_SCOPES, PLANNER_GRAPH_SCOPES]
    assert microsoft.clients[1].is_closed  # The Planner client is closed at once.
    await store.aclose()


@pytest.mark.asyncio
async def test_403_reports_the_missing_authorization(microsoft: Microsoft) -> None:
    microsoft.routes["/sites/balzmetal.sharepoint.com:/sites/atelier?$select=id"] = 403
    poste = _sharepoint(sharepoint_site_url=SITE_URL)

    with pytest.raises(StoreUnavailableError, match="autorisation") as raised:
        await _open(poste, microsoft)

    cause = raised.value.__cause__
    assert isinstance(cause, GraphError)
    assert cause.status_code == 403
    assert poste.sharepoint_site_id == ""
    assert microsoft.clients[0].is_closed


@pytest.mark.asyncio
async def test_pending_consent_is_reported_before_any_request(microsoft: Microsoft) -> None:
    microsoft.refused.add(PLANNING_GRAPH_SCOPES)
    poste = _sharepoint(sharepoint_site_url=SITE_URL)

    with pytest.raises(StoreUnavailableError, match="autorisation Microsoft manquante") as raised:
        await _open(poste, microsoft)

    cause = raised.value.__cause__
    assert isinstance(cause, GraphError)
    assert cause.status_code == 403  # Read as « forbidden » by the sync state.
    assert microsoft.requests == []
    assert [p.scopes for p in microsoft.providers] == [PLANNING_GRAPH_SCOPES, GRAPH_SCOPES]


@pytest.mark.asyncio
async def test_signed_out_poste_is_asked_to_sign_in(microsoft: Microsoft) -> None:
    microsoft.refused.update({PLANNING_GRAPH_SCOPES, GRAPH_SCOPES})

    with pytest.raises(StoreUnavailableError, match="connexion Microsoft") as raised:
        await _open(_sharepoint(sharepoint_site_url=SITE_URL), microsoft)

    assert isinstance(raised.value.__cause__, AuthError)
    assert microsoft.requests == []


@pytest.mark.asyncio
async def test_unreachable_microsoft_keeps_the_planning_readable(microsoft: Microsoft) -> None:
    route = "/sites/balzmetal.sharepoint.com:/sites/atelier?$select=id"
    microsoft.routes[route] = httpx.ConnectError("offline")

    with pytest.raises(StoreUnavailableError, match="injoignable") as raised:
        await _open(_sharepoint(sharepoint_site_url=SITE_URL), microsoft)

    assert failure_status(raised.value).state == "offline"


def _auth_errors(error: BaseException) -> list[AuthError]:
    found: list[AuthError] = []
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, AuthError):
            found.append(current)
        current = current.__cause__ or current.__context__
    return found


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("unreachable", "refused"),
    [
        pytest.param({PLANNING_GRAPH_SCOPES, GRAPH_SCOPES}, set(), id="no-network"),
        pytest.param({PLANNING_GRAPH_SCOPES}, set(), id="base-token-still-cached"),
        pytest.param({GRAPH_SCOPES}, {PLANNING_GRAPH_SCOPES}, id="base-check-offline"),
    ],
)
async def test_poste_starting_offline_is_offline_not_signed_out(
    microsoft: Microsoft,
    unreachable: set[tuple[str, ...]],
    refused: set[tuple[str, ...]],
) -> None:
    microsoft.unreachable.update(unreachable)
    microsoft.refused.update(refused)

    with pytest.raises(StoreUnavailableError, match="injoignable") as raised:
        await _open(_sharepoint(sharepoint_site_url=SITE_URL), microsoft)

    status = failure_status(raised.value)
    assert status.state == "offline"
    assert "Se connecter" not in status.message
    assert _auth_errors(raised.value) == []  # Read as « reconnexion requise » otherwise.
    assert microsoft.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("authority_unreachable", "refused"),
    [
        pytest.param({PLANNING_GRAPH_SCOPES}, set(), id="first-token-of-the-day"),
        pytest.param({GRAPH_SCOPES}, {PLANNING_GRAPH_SCOPES}, id="base-check"),
    ],
)
async def test_bare_network_error_of_msal_reads_offline(
    microsoft: Microsoft,
    authority_unreachable: set[tuple[str, ...]],
    refused: set[tuple[str, ...]],
) -> None:
    microsoft.authority_unreachable.update(authority_unreachable)
    microsoft.refused.update(refused)

    with pytest.raises(StoreUnavailableError, match="injoignable") as raised:
        await _open(_sharepoint(sharepoint_site_url=SITE_URL), microsoft)

    assert failure_status(raised.value).state == "offline"
    assert _auth_errors(raised.value) == []
    assert microsoft.requests == []


@pytest.mark.asyncio
async def test_planner_authority_unreachable_while_resolving_is_offline(
    microsoft: Microsoft,
) -> None:
    config = AppConfig()
    config.planner.plan_id = "plan-1"
    microsoft.authority_unreachable.add(PLANNER_GRAPH_SCOPES)

    with pytest.raises(StoreUnavailableError, match="injoignable") as raised:
        await _open(_sharepoint(), microsoft, config)

    assert failure_status(raised.value).state == "offline"
    assert microsoft.clients[0].is_closed


@pytest.mark.asyncio
async def test_planner_unreachable_while_resolving_is_offline(microsoft: Microsoft) -> None:
    config = AppConfig()
    config.planner.plan_id = "plan-1"
    microsoft.unreachable.add(PLANNER_GRAPH_SCOPES)

    with pytest.raises(StoreUnavailableError, match="injoignable") as raised:
        await _open(_sharepoint(), microsoft, config)

    assert failure_status(raised.value).state == "offline"
    assert _auth_errors(raised.value) == []
    assert microsoft.clients[0].is_closed


@pytest.mark.asyncio
async def test_signed_out_and_pending_consent_keep_their_states(microsoft: Microsoft) -> None:
    microsoft.refused.update({PLANNING_GRAPH_SCOPES, GRAPH_SCOPES})
    with pytest.raises(StoreUnavailableError) as signed_out:
        await _open(_sharepoint(sharepoint_site_url=SITE_URL), microsoft)

    microsoft.refused.discard(GRAPH_SCOPES)
    with pytest.raises(StoreUnavailableError) as pending:
        await _open(_sharepoint(sharepoint_site_url=SITE_URL), microsoft)

    assert failure_status(signed_out.value).state == "auth_required"
    assert failure_status(pending.value).state == "forbidden"


@pytest.mark.asyncio
async def test_missing_list_is_a_configuration_error(microsoft: Microsoft) -> None:
    _site_routes(microsoft)

    with pytest.raises(ConfigError, match="Planning atelier"):
        await _open(
            _sharepoint(sharepoint_site_url=SITE_URL, sharepoint_list_name="Planning atelier"),
            microsoft,
        )


@pytest.mark.asyncio
async def test_list_with_wrong_columns_is_refused(microsoft: Microsoft) -> None:
    _site_routes(microsoft)
    microsoft.routes["/sites/site-1/lists/list-9/columns?$select=name,text"] = {"value": []}

    with pytest.raises(ConfigError, match="PfKind"):
        await _open(_sharepoint(sharepoint_site_url=SITE_URL), microsoft)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("settings", "poste", "message"),
    [
        (ApplicationSettings(), _sharepoint(sharepoint_site_url=SITE_URL), "connecteur"),
        (SETTINGS, _sharepoint(), "adresse du site"),
    ],
)
async def test_incomplete_settings_are_configuration_errors(
    microsoft: Microsoft,
    monkeypatch: pytest.MonkeyPatch,
    settings: ApplicationSettings,
    poste: PlanningPosteConfig,
    message: str,
) -> None:
    monkeypatch.delenv("PROJECTFLOW_MICROSOFT_CLIENT_ID", raising=False)

    with pytest.raises(ConfigError, match=message):
        await create_planning_store(AppConfig(), poste, application_settings=settings)

    assert microsoft.requests == []


@pytest.mark.asyncio
async def test_container_closes_the_planning_graph_client(microsoft: Microsoft) -> None:
    _site_routes(microsoft)
    container = ServiceContainer(AppConfig(), application_settings=SETTINGS)

    store = await container.create_planning_store(_sharepoint(sharepoint_site_url=SITE_URL))
    await container.close()

    assert isinstance(store, GraphPlanningBackend)
    assert microsoft.clients[0].is_closed
    await store.aclose()  # Closing twice is harmless.


def test_planning_authorization_error_reads_as_forbidden() -> None:
    error = services.planning_authorization_error()

    assert "autorisation" in str(error)
    cause = error.__cause__
    assert isinstance(cause, GraphError)
    assert cause.status_code == 403
