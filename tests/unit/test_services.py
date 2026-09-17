from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from openpyxl import Workbook

from projectflow.application_settings import ApplicationSettings
from projectflow.config import AppConfig, RepertoireChantierConfig
from projectflow.exceptions import ConfigError
from projectflow.graph.client import GraphClient
from projectflow.graph.excel import GraphExcelWorkbookGateway
from projectflow.graph.planner import GraphPlannerClient
from projectflow.platform import sync_paths
from projectflow.services import ConfiguredPlannerGateway, ServiceContainer


def _create_repertoire(path: Path) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "2026"
    worksheet.append(["Numero", "Date", "Societe", "Contact", "Description", "Gere par"])
    worksheet.append(["2026-4995", "", "", "", "", ""])
    workbook.save(path)
    workbook.close()


@pytest.mark.asyncio
async def test_service_container_uses_local_repertoire_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This unit case models a non-synchronized file regardless of the test root.
    monkeypatch.setattr("projectflow.services.is_synchronized_path", lambda _path: False)
    monkeypatch.setattr(
        "projectflow.core.local_repertoire.is_synchronized_path", lambda _path: False
    )
    repertoire_path = tmp_path / "repertoire.xlsx"
    _create_repertoire(repertoire_path)
    config = AppConfig()
    config.paths.repertoire_chantier.display_path = str(repertoire_path)

    next_project = await ServiceContainer(config).repertoire().next_available(year=2026)

    assert next_project is not None
    assert str(next_project.number) == "2026-4995"


def test_service_container_reuses_repertoire_service(tmp_path: Path) -> None:
    repertoire_path = tmp_path / "repertoire.xlsx"
    _create_repertoire(repertoire_path)
    config = AppConfig()
    config.paths.repertoire_chantier.display_path = str(repertoire_path)
    container = ServiceContainer(config)

    first = container.repertoire()
    second = container.repertoire()

    assert first is second


def test_service_container_can_reset_repertoire_service(tmp_path: Path) -> None:
    repertoire_path = tmp_path / "repertoire.xlsx"
    _create_repertoire(repertoire_path)
    config = AppConfig()
    config.paths.repertoire_chantier.display_path = str(repertoire_path)
    container = ServiceContainer(config)
    first = container.repertoire()

    container.reset_repertoire()

    assert container.repertoire() is not first


def test_service_container_uses_cloud_repertoire_for_local_onedrive_by_default(
    tmp_path: Path,
) -> None:
    repertoire_path = tmp_path / "OneDrive - Balz Metal Sa" / "repertoire.xlsx"
    repertoire_path.parent.mkdir()
    _create_repertoire(repertoire_path)
    config = AppConfig()
    config.paths.repertoire_chantier.display_path = str(repertoire_path)

    service = ServiceContainer(config).repertoire()

    assert service is not None


def test_service_container_uses_cloud_repertoire_when_client_id_is_embedded(
    tmp_path: Path,
) -> None:
    config = AppConfig()
    config.paths.repertoire_chantier.display_path = str(
        tmp_path / "OneDrive - Balz Metal Sa" / "repertoire.xlsx",
    )

    service = ServiceContainer(
        config,
        application_settings=ApplicationSettings(microsoft_client_id="client-id"),
    ).repertoire()

    assert service is not None


def test_service_container_uses_cloud_repertoire_with_embedded_client_id() -> None:
    config = AppConfig()
    config.paths.repertoire_chantier = RepertoireChantierConfig(
        drive_id="drive",
        item_id="item",
        display_path="",
    )

    service = ServiceContainer(config).repertoire()

    assert service is not None


def test_service_container_returns_planner_when_enabled() -> None:
    config = AppConfig()
    config.planner.enabled = True
    config.planner.plan_id = "plan-id"
    config.planner.bucket_id = "bucket-id"

    service = ServiceContainer(
        config,
        application_settings=ApplicationSettings(microsoft_client_id="client-id"),
    ).planner()

    assert service is not None


@pytest.mark.parametrize("kind", ["sharepoint", "custom-root", "explicit-cloud", "url"])
def test_shared_repertoire_never_uses_local_gateway(monkeypatch, tmp_path: Path, kind: str) -> None:
    path = tmp_path / "Balz Metal Sa" / "Projets - Documents" / "rep.xlsx"
    config = AppConfig()
    config.paths.repertoire_chantier.display_path = str(path)
    if kind == "sharepoint":
        monkeypatch.setattr(sync_paths, "synchronized_roots", lambda: (path.parent,))
    elif kind == "custom-root":
        monkeypatch.setenv("OneDriveCommercial", str(path.parent))
    elif kind == "explicit-cloud":
        config.paths.repertoire_chantier.cloud_only = True
    else:
        config.paths.repertoire_chantier.display_path = "https://balz.sharepoint.com/:x:/s/site/abc"

    def reject_local(*_args: object) -> None:
        pytest.fail("Shared workbook must never be opened through the local gateway")

    monkeypatch.setattr("projectflow.services.LocalWorkbookGateway", reject_local)
    service = ServiceContainer(config).repertoire()
    assert isinstance(service._workbook, GraphExcelWorkbookGateway)  # noqa: SLF001


def test_shared_repertoire_missing_connector_does_not_fall_back_to_disk(tmp_path: Path) -> None:
    path = tmp_path / "OneDrive - Balz" / "rep.xlsx"
    config = AppConfig()
    config.paths.repertoire_chantier.display_path = str(path)
    with pytest.raises(ConfigError, match="connecteur"):
        ServiceContainer(
            config,
            application_settings=ApplicationSettings(microsoft_client_id=""),
        ).repertoire()


def test_opening_file_does_not_change_cloud_write_backend(tmp_path: Path) -> None:
    config = AppConfig()
    config.paths.repertoire_chantier = RepertoireChantierConfig(
        display_path="https://balz.sharepoint.com/:x:/s/team/share-token",
        open_path=str(tmp_path / "repertoire.xlsx"),
        drive_id="cloud-drive",
        item_id="cloud-item",
    )
    service = ServiceContainer(config).repertoire()
    assert isinstance(service._workbook, GraphExcelWorkbookGateway)  # noqa: SLF001


class FakeTokenProvider:
    async def access_token(self) -> str:
        return "test-token"


def _track_graph_clients(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], Any],
) -> list[httpx.AsyncClient]:
    http_clients: list[httpx.AsyncClient] = []

    def create_graph(**_kwargs: object) -> GraphClient:
        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        http_clients.append(http_client)
        return GraphClient(token_provider=FakeTokenProvider(), http_client=http_client)

    monkeypatch.setattr("projectflow.services.GraphClient", create_graph)
    return http_clients


def _cloud_container() -> ServiceContainer:
    config = AppConfig()
    config.paths.repertoire_chantier = RepertoireChantierConfig(drive_id="drive", item_id="item")
    config.planner.enabled = True
    return ServiceContainer(
        config,
        application_settings=ApplicationSettings(microsoft_client_id="client-id"),
    )


@pytest.mark.asyncio
async def test_reset_keeps_inflight_clients_open_until_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_started, finish_request = asyncio.Event(), asyncio.Event()

    async def handler(_request: httpx.Request) -> httpx.Response:
        request_started.set()
        await finish_request.wait()
        return httpx.Response(200, json={"value": []})

    http_clients = _track_graph_clients(monkeypatch, handler)
    container = _cloud_container()
    first_repertoire = container.repertoire()
    first_planner = container.planner()
    assert first_planner is not None
    assert container.repertoire() is first_repertoire
    assert container.planner() is first_planner
    assert len(http_clients) == 2

    request_task = asyncio.create_task(first_planner.client.list_plans())
    try:
        await asyncio.wait_for(request_started.wait(), timeout=3)
        container.reset_repertoire()
        container.reset_planner()
        assert container.repertoire() is not first_repertoire
        assert container.planner() is not first_planner
        assert len(http_clients) == 4
        assert all(not client.is_closed for client in http_clients)
        finish_request.set()
        assert await request_task == []
    finally:
        finish_request.set()
        await request_task
        await container.close()

    assert all(client.is_closed for client in http_clients)
    await container.close()


@pytest.mark.asyncio
async def test_shutdown_closes_remaining_clients_even_if_one_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http_clients = _track_graph_clients(
        monkeypatch,
        lambda _request: httpx.Response(200, json={"value": []}),
    )
    container = _cloud_container()
    container.repertoire()
    container.planner()
    close_first = http_clients[0].aclose
    failing_close = AsyncMock(side_effect=OSError("close failed"))
    monkeypatch.setattr(http_clients[0], "aclose", failing_close)
    try:
        with pytest.raises(ExceptionGroup, match="connexions Microsoft") as raised:
            await container.close()
        assert isinstance(raised.value.exceptions[0], OSError)
        assert http_clients[1].is_closed
        failing_close.assert_awaited_once()
    finally:
        await close_first()


@pytest.mark.asyncio
async def test_container_does_not_take_ownership_of_injected_planner_client() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200))
    ) as http:
        config = AppConfig()
        config.planner.enabled = True
        planner = ConfiguredPlannerGateway(
            client=GraphPlannerClient(
                graph=GraphClient(token_provider=FakeTokenProvider(), http_client=http)
            ),
            config=config,
        )
        container = ServiceContainer(config, planner_service=planner)
        assert container.planner() is planner

        await container.close()

        assert not http.is_closed
