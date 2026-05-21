from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from projectflow.application_settings import ApplicationSettings
from projectflow.config import AppConfig, RepertoireChantierConfig
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
