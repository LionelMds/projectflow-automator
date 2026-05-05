from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook

from projectflow.config import AppConfig, OutlookFolderConfig
from projectflow.core.fiche_service import FicheService
from projectflow.core.models import ProjectInput
from projectflow.core.numero import parse_project_number
from projectflow.core.project_service import (
    ProjectService,
    copy_reference_tree,
    outlook_folder_paths,
    render_outlook_folder_name,
)


class FakeRepertoireService:
    def __init__(self) -> None:
        self.calls: list[tuple[ProjectInput, bool]] = []

    async def upsert_project(self, project: ProjectInput, *, force_overwrite: bool = False) -> None:
        self.calls.append((project, force_overwrite))


class FakeOutlook:
    def __init__(self) -> None:
        self.paths: list[list[str]] = []

    async def ensure_folder_path(self, names: list[str]) -> object:
        self.paths.append(names)
        return object()


class FakePlanner:
    def __init__(self) -> None:
        self.tasks: list[dict[str, Any]] = []

    async def create_task(
        self,
        *,
        plan_id: str,
        bucket_id: str,
        title: str,
        due_days: int,
    ) -> object:
        self.tasks.append(
            {
                "plan_id": plan_id,
                "bucket_id": bucket_id,
                "title": title,
                "due_days": due_days,
            },
        )
        return object()


def test_copy_reference_tree_does_not_overwrite_existing_files(tmp_path: Path) -> None:
    reference = tmp_path / "reference"
    project = tmp_path / "project"
    reference.mkdir()
    project.mkdir()
    (reference / "a.txt").write_text("reference", encoding="utf-8")
    (project / "a.txt").write_text("existing", encoding="utf-8")

    copy_reference_tree(reference, project)

    assert (project / "a.txt").read_text(encoding="utf-8") == "existing"


def test_outlook_folder_templates_render_project_placeholders() -> None:
    project = ProjectInput(number=parse_project_number("2026-4995"), designation="Escalier")
    arborescence = [
        OutlookFolderConfig(
            name="Clients",
            children=[OutlookFolderConfig(name="[YYYY]", children=[
                OutlookFolderConfig(name="[NUMERO] - [XXXX]"),
            ])],
        ),
    ]

    assert render_outlook_folder_name("[YYYY]-[XXXX]", project) == "2026-4995"
    assert outlook_folder_paths(project, arborescence) == [["Clients", "2026", "2026-4995 - 4995"]]


@pytest.mark.asyncio
async def test_create_project_creates_folder_copies_reference_and_calls_integrations(
    tmp_path: Path,
) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    config.paths.dossier_reference = tmp_path / "reference"
    config.paths.dossier_reference.mkdir(parents=True)
    workbook = Workbook()
    workbook.save(config.paths.dossier_reference / "modele fiche.xlsx")
    config.planner.enabled = True
    config.planner.plan_id = "plan"
    config.planner.bucket_id = "bucket"

    repertoire = FakeRepertoireService()
    outlook = FakeOutlook()
    planner = FakePlanner()
    pinned: list[Path] = []
    service = ProjectService(
        config=config,
        fiche_service=FicheService(),
        repertoire_service=repertoire,  # type: ignore[arg-type]
        outlook=outlook,
        planner=planner,
        pin_path=pinned.append,
    )
    project = ProjectInput(number=parse_project_number("2026-4995"), designation="Escalier")

    result = await service.create_project(project)

    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    assert result.project_dir_created is True
    assert Path(result.project_dir) == project_dir
    assert (project_dir / "2026-4995 - Fiche dossier clients.xlsx").exists()
    assert repertoire.calls == [(project, False)]
    assert outlook.paths == [["2026", "2026-4995"]]
    assert planner.tasks[0]["title"] == "2026-4995 - Escalier"
    assert pinned == [project_dir]


@pytest.mark.asyncio
async def test_create_subproject_reuses_parent_folder_without_integrations(tmp_path: Path) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    project_dir.mkdir(parents=True)
    workbook = Workbook()
    workbook.save(project_dir / "2026-4995 - Fiche dossier clients.xlsx")
    repertoire = FakeRepertoireService()
    outlook = FakeOutlook()
    planner = FakePlanner()
    service = ProjectService(
        config=config,
        fiche_service=FicheService(),
        repertoire_service=repertoire,  # type: ignore[arg-type]
        outlook=outlook,
        planner=planner,
    )
    project = ProjectInput(number=parse_project_number("2026-4995-2"), designation="Variante")

    result = await service.create_project(project)

    assert Path(result.project_dir) == project_dir
    assert (project_dir / "2026-4995-2 - Fiche dossier clients.xlsx").exists()
    assert repertoire.calls == [(project, False)]
    assert outlook.paths == []
    assert planner.tasks == []
