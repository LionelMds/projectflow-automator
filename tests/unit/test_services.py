from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from projectflow.config import AppConfig
from projectflow.core.excel_com_repertoire import ExcelComWorkbookGateway
from projectflow.services import (
    ServiceContainer,
    _should_use_excel_com_gateway,
    _workbook_gateway_for_path,
)


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
    monkeypatch.setenv("PROJECTFLOW_REPERTOIRE_GATEWAY", "openpyxl")
    repertoire_path = tmp_path / "repertoire.xlsx"
    _create_repertoire(repertoire_path)
    config = AppConfig()
    config.paths.repertoire_chantier.display_path = str(repertoire_path)

    next_project = await ServiceContainer(config).repertoire().next_available(year=2026)

    assert next_project is not None
    assert str(next_project.number) == "2026-4995"


def test_repertoire_gateway_uses_excel_com_for_onedrive_on_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbook_path = tmp_path / "OneDrive - Balz Metal Sa" / "repertoire.xlsx"
    monkeypatch.setattr("projectflow.services.sys.platform", "win32")
    monkeypatch.delenv("PROJECTFLOW_REPERTOIRE_GATEWAY", raising=False)

    assert _should_use_excel_com_gateway(workbook_path) is True
    assert isinstance(_workbook_gateway_for_path(workbook_path), ExcelComWorkbookGateway)


def test_repertoire_gateway_can_force_openpyxl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbook_path = tmp_path / "OneDrive - Balz Metal Sa" / "repertoire.xlsx"
    monkeypatch.setattr("projectflow.services.sys.platform", "win32")
    monkeypatch.setenv("PROJECTFLOW_REPERTOIRE_GATEWAY", "openpyxl")

    assert _should_use_excel_com_gateway(workbook_path) is False
