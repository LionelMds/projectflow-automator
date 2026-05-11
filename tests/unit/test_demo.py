from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import load_workbook

from projectflow.core.models import ProjectInput
from projectflow.core.numero import parse_project_number
from projectflow.demo import build_demo_environment


class FakeOutlook:
    def __init__(self) -> None:
        self.validated = False
        self.paths: list[list[str]] = []

    async def validate_target(self) -> None:
        self.validated = True

    async def ensure_folder_path(self, names: list[str]) -> object:
        self.paths.append(names)
        return object()


def test_build_demo_environment_creates_local_files(tmp_path: Path) -> None:
    config, services = build_demo_environment(base_dir=tmp_path)

    assert config.is_onboarded is True
    assert config.paths.racine_projets == tmp_path / "Clients"
    assert services.workbook_path.exists()


@pytest.mark.asyncio
async def test_demo_service_can_create_project_end_to_end(tmp_path: Path) -> None:
    _config, services = build_demo_environment(base_dir=tmp_path)
    project = ProjectInput(
        number=parse_project_number("2026-4995"),
        designation="Escalier demo",
        societe="Balz",
    )

    result = await services.project().create_project(project)

    assert Path(result.project_dir).exists()
    assert Path(result.fiche_path or "").exists()
    workbook = load_workbook(services.workbook_path)
    worksheet = workbook["2026"]
    assert worksheet["A2"].value == "2026-4995"
    assert worksheet["C2"].value == "Balz"
    assert worksheet["E2"].value == "Escalier demo"
    workbook.close()


@pytest.mark.asyncio
async def test_demo_service_uses_local_outlook_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, services = build_demo_environment(base_dir=tmp_path)
    config.outlook.enabled = True
    config.outlook.mailbox_store_id = "store-1"
    outlook = FakeOutlook()
    monkeypatch.setattr("projectflow.demo.create_local_outlook_client", lambda _config: outlook)

    await services.project().create_project(
        ProjectInput(number=parse_project_number("2026-4995"), designation="Escalier demo"),
    )

    assert outlook.validated is True
    assert outlook.paths == [["2026", "2026-4995 (Escalier demo)"]]
