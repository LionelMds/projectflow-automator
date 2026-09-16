from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from projectflow.application_settings import ApplicationSettings
from projectflow.config import AppConfig, RepertoireChantierConfig
from projectflow.exceptions import ConfigError
from projectflow.graph.excel import GraphExcelWorkbookGateway
from projectflow.platform import sync_paths
from projectflow.services import ServiceContainer


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
) -> None:
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
