from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from projectflow.config import AppConfig, OutlookFolderConfig
from projectflow.core.fiche_service import FicheService
from projectflow.core.models import PlannerTaskInput, ProjectInput
from projectflow.core.numero import parse_project_number
from projectflow.core.project_service import (
    ProjectService,
    copy_reference_tree,
    outlook_folder_paths,
    outlook_project_folder_name,
    render_outlook_folder_name,
)
from projectflow.core.repertoire_service import RepertoireRow
from projectflow.exceptions import ConfigError, OutlookError


class FakeRepertoireService:
    def __init__(self) -> None:
        self.calls: list[tuple[ProjectInput, bool]] = []
        self.validated_deletions: list[tuple[object, tuple[RepertoireRow, ...]]] = []
        self.cleared_deletions: list[tuple[object, tuple[RepertoireRow, ...]]] = []

    async def upsert_project(self, project: ProjectInput, *, force_overwrite: bool = False) -> None:
        self.calls.append((project, force_overwrite))

    async def validate_project_deletion(
        self,
        *,
        number: object,
        rows: tuple[RepertoireRow, ...] | list[RepertoireRow],
    ) -> None:
        self.validated_deletions.append((number, tuple(rows)))

    async def clear_project_rows(
        self,
        *,
        number: object,
        rows: tuple[RepertoireRow, ...] | list[RepertoireRow],
    ) -> None:
        self.cleared_deletions.append((number, tuple(rows)))


class FakeOutlook:
    def __init__(self) -> None:
        self.paths: list[list[str]] = []
        self.deleted_paths: list[list[str]] = []
        self.validated = False

    async def validate_target(self) -> None:
        self.validated = True

    async def ensure_folder_path(self, names: list[str]) -> object:
        self.paths.append(names)
        return object()

    async def delete_folder_path(self, names: list[str]) -> bool:
        self.deleted_paths.append(names)
        return True


class BrokenOutlook:
    async def validate_target(self) -> None:
        raise ConfigError("Compte Outlook introuvable")

    async def ensure_folder_path(self, names: list[str]) -> object:
        del names
        return object()

    async def delete_folder_path(self, names: list[str]) -> bool:
        del names
        return False


class FailingDuringCreationOutlook(FakeOutlook):
    async def ensure_folder_path(self, names: list[str]) -> object:
        del names
        raise OutlookError("Impossible de creer le dossier Outlook")


class FakePlannerResult:
    def __init__(
        self,
        *,
        task_id: str = "task-id",
        created: bool = True,
        updated: bool = False,
    ) -> None:
        self.task_id = task_id
        self.created = created
        self.updated = updated


class FakePlanner:
    def __init__(self) -> None:
        self.projects: list[ProjectInput] = []
        self.deleted_projects: list[ProjectInput] = []
        self.result = FakePlannerResult()

    async def ensure_project_task(self, project: ProjectInput) -> FakePlannerResult:
        self.projects.append(project)
        return self.result

    async def delete_project_tasks(self, project: ProjectInput) -> int:
        self.deleted_projects.append(project)
        return 1


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
    project = ProjectInput(
        number=parse_project_number("2026-4995"),
        designation="Escalier",
        planner=PlannerTaskInput(enabled=True),
    )
    arborescence = [
        OutlookFolderConfig(
            name="Clients",
            children=[
                OutlookFolderConfig(
                    name="[YYYY]",
                    children=[
                        OutlookFolderConfig(name="[NUMERO] - [XXXX]"),
                    ],
                )
            ],
        ),
    ]

    assert outlook_project_folder_name(project) == "2026-4995 (Escalier)"
    assert render_outlook_folder_name("[PROJECT_FOLDER]", project) == "2026-4995 (Escalier)"
    assert render_outlook_folder_name("[YYYY]-[XXXX]", project) == "2026-4995 (Escalier)"
    assert outlook_folder_paths(project, arborescence) == [
        ["Clients", "2026", "2026-4995 (Escalier)"],
    ]


def test_outlook_project_folder_name_falls_back_to_number_without_designation() -> None:
    project = ProjectInput(number=parse_project_number("2026-4995"), designation="")

    assert outlook_project_folder_name(project) == "2026-4995"


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
    config.outlook.enabled = True
    config.planner.enabled = True

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
    project = ProjectInput(
        number=parse_project_number("2026-4995"),
        designation="Escalier",
        planner=PlannerTaskInput(enabled=True),
    )

    result = await service.create_project(project)

    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    assert result.project_dir_created is True
    assert Path(result.project_dir) == project_dir
    assert (project_dir / "2026-4995 - Fiche dossier clients.xlsx").exists()
    assert repertoire.calls == [(project, False)]
    assert outlook.validated is True
    assert outlook.paths == [["2026", "2026-4995 (Escalier)"]]
    assert planner.projects == [project]
    assert result.planner_task_created is True
    assert result.planner_task_id == "task-id"
    assert pinned == [project_dir]


@pytest.mark.parametrize(
    "operation",
    ["create", "recreate", "update", "create_subproject", "update_subproject"],
)
@pytest.mark.asyncio
async def test_project_writes_configured_initials_and_reserves_e2_for_output(
    tmp_path: Path,
    operation: str,
) -> None:
    config = AppConfig()
    config.user.initials = "LM"
    config.paths.racine_projets = tmp_path / "clients"
    config.paths.dossier_reference = tmp_path / "reference"
    config.paths.dossier_reference.mkdir()
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    number = "2026-4995-2" if operation.endswith("subproject") else "2026-4995"
    workbook = Workbook()
    workbook.active["E2"] = "fiche d'atelier le 01.02.2020"
    workbook.active["C9"] = "OLD"
    workbook.save(config.paths.dossier_reference / "modele fiche.xlsx")
    if operation != "create":
        project_dir.mkdir(parents=True)
        workbook.save(project_dir / "2026-4995 - Fiche dossier clients.xlsx")
    if operation == "update_subproject":
        workbook.save(project_dir / f"{number} - Fiche dossier clients.xlsx")
    workbook.close()
    repertoire = FakeRepertoireService()
    service = ProjectService(
        config=config,
        fiche_service=FicheService(today=lambda: date(2026, 7, 15)),
        repertoire_service=repertoire,  # type: ignore[arg-type]
    )
    project = ProjectInput(number=parse_project_number(number), gere_par="OTHER")

    result = (
        await service.update_project(project)
        if operation.startswith("update")
        else await service.create_project(project)
    )

    assert result.fiche_path is not None
    workbook = load_workbook(result.fiche_path)
    assert workbook.active["C9"].value == "LM"
    expected_e2 = (
        "fiche d'atelier le"
        if operation in {"create", "create_subproject"}
        else "fiche d'atelier le 01.02.2020"
    )
    assert workbook.active["E2"].value == expected_e2
    assert workbook.active["B9"].value.date() == date(2026, 7, 15)
    workbook.close()
    assert repertoire.calls[0][0] == project
    assert project.gere_par == "OTHER"


@pytest.mark.parametrize("number", ["2026-4995", "2026-4995-2"])
@pytest.mark.asyncio
async def test_update_project_preserves_existing_initials_when_settings_are_empty(
    tmp_path: Path,
    number: str,
) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    project_dir.mkdir(parents=True)
    fiche_path = project_dir / f"{number} - Fiche dossier clients.xlsx"
    workbook = Workbook()
    workbook.active["C9"] = "OLD"
    workbook.save(fiche_path)
    workbook.close()
    service = ProjectService(
        config=config,
        fiche_service=FicheService(),
        repertoire_service=FakeRepertoireService(),  # type: ignore[arg-type]
    )

    await service.update_project(
        ProjectInput(number=parse_project_number(number), gere_par="OTHER"),
    )

    workbook = load_workbook(fiche_path)
    assert workbook.active["C9"].value == "OLD"
    workbook.close()


@pytest.mark.asyncio
async def test_new_fiche_discards_inherited_date_when_another_workbook_already_exists(
    tmp_path: Path,
) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    config.paths.dossier_reference = tmp_path / "reference"
    config.paths.dossier_reference.mkdir()
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    project_dir.mkdir(parents=True)
    workbook = Workbook()
    workbook.active["E2"] = "fiche d'atelier le 01.02.2020"
    workbook.save(config.paths.dossier_reference / "modele fiche.xlsx")
    workbook.active["E2"] = "Devis existant"
    workbook.save(project_dir / "Devis.xlsx")
    workbook.close()
    original_quote = (project_dir / "Devis.xlsx").read_bytes()
    service = ProjectService(
        config=config,
        fiche_service=FicheService(today=lambda: date(2026, 7, 15)),
        repertoire_service=FakeRepertoireService(),  # type: ignore[arg-type]
    )

    result = await service.create_project(ProjectInput(number=parse_project_number("2026-4995")))

    assert result.fiche_path == str(project_dir / "2026-4995 - Fiche dossier clients.xlsx")
    workbook = load_workbook(result.fiche_path)
    assert workbook.active["E2"].value == "fiche d'atelier le"
    workbook.close()
    assert (project_dir / "Devis.xlsx").read_bytes() == original_quote


@pytest.mark.asyncio
async def test_create_project_still_creates_planner_task_when_outlook_fails(
    tmp_path: Path,
) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    config.paths.dossier_reference = tmp_path / "reference"
    config.paths.dossier_reference.mkdir(parents=True)
    Workbook().save(config.paths.dossier_reference / "modele fiche.xlsx")
    config.outlook.enabled = True
    config.planner.enabled = True

    planner = FakePlanner()
    service = ProjectService(
        config=config,
        fiche_service=FicheService(),
        repertoire_service=FakeRepertoireService(),  # type: ignore[arg-type]
        outlook=FailingDuringCreationOutlook(),
        planner=planner,
    )
    project = ProjectInput(
        number=parse_project_number("2026-4996"),
        designation="Escalier",
        planner=PlannerTaskInput(enabled=True),
    )

    result = await service.create_project(project)

    assert result.outlook_folder_created is False
    assert result.outlook_error == "Impossible de creer le dossier Outlook"
    assert planner.projects == [project]
    assert result.planner_task_created is True
    assert result.planner_task_id == "task-id"


@pytest.mark.asyncio
async def test_recreate_existing_project_reapplies_integrations_without_updating_info(
    tmp_path: Path,
) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    config.paths.dossier_reference = tmp_path / "reference"
    config.paths.dossier_reference.mkdir(parents=True)
    Workbook().save(config.paths.dossier_reference / "modele fiche.xlsx")
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    fiche_dir = project_dir / "2026-4995"
    fiche_dir.mkdir(parents=True)
    fiche_path = fiche_dir / "2026-4995 - Fiche dossier clients.xlsx"
    workbook = Workbook()
    workbook.active["D3"] = "Societe : Information conservee"
    workbook.active["E2"] = "fiche d'atelier le"
    workbook.active["B9"] = date(2024, 3, 4)
    workbook.active["C9"] = "OLD"
    workbook.save(fiche_path)
    workbook.close()
    config.outlook.enabled = True
    config.planner.enabled = True
    config.user.initials = "LM"

    repertoire = FakeRepertoireService()
    outlook = FakeOutlook()
    planner = FakePlanner()
    planner.result = FakePlannerResult(created=False, updated=False)
    pinned: list[Path] = []
    service = ProjectService(
        config=config,
        fiche_service=FicheService(today=lambda: date(2026, 7, 15)),
        repertoire_service=repertoire,  # type: ignore[arg-type]
        outlook=outlook,
        planner=planner,
        pin_path=pinned.append,
    )
    project = ProjectInput(
        number=parse_project_number("2026-4995"),
        designation="Nouveau texte",
        planner=PlannerTaskInput(enabled=True),
    )

    result = await service.create_project(project, update_existing_info=False)

    assert result.project_dir_created is False
    assert result.fiche_path == str(fiche_path)
    assert not (project_dir / "modele fiche.xlsx").exists()
    workbook = load_workbook(fiche_path)
    assert workbook.active["D3"].value == "Societe : Information conservee"
    assert workbook.active["B9"].value.date() == date(2024, 3, 4)
    assert workbook.active["E2"].value == "fiche d'atelier le"
    assert workbook.active["C9"].value == "OLD"
    workbook.close()
    assert repertoire.calls == []
    assert outlook.paths == [["2026", "2026-4995 (Nouveau texte)"]]
    assert planner.projects == [project]
    assert result.planner_task_id == "task-id"
    assert pinned == [project_dir]


@pytest.mark.asyncio
async def test_recreate_existing_project_can_force_information_update(tmp_path: Path) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    config.paths.dossier_reference = tmp_path / "reference"
    config.paths.dossier_reference.mkdir(parents=True)
    Workbook().save(config.paths.dossier_reference / "modele fiche.xlsx")
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    project_dir.mkdir(parents=True)
    Workbook().save(project_dir / "2026-4995 - Fiche dossier clients.xlsx")
    repertoire = FakeRepertoireService()
    service = ProjectService(
        config=config,
        fiche_service=FicheService(),
        repertoire_service=repertoire,  # type: ignore[arg-type]
    )
    project = ProjectInput(number=parse_project_number("2026-4995"), designation="Corrige")

    await service.create_project(project, force_overwrite=True, update_existing_info=True)

    assert repertoire.calls == [(project, True)]


@pytest.mark.asyncio
async def test_update_project_reapplies_selected_integrations_without_duplicates(
    tmp_path: Path,
) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    project_dir.mkdir(parents=True)
    Workbook().save(project_dir / "2026-4995 - Fiche dossier clients.xlsx")
    config.outlook.enabled = True
    config.planner.enabled = True

    repertoire = FakeRepertoireService()
    outlook = FakeOutlook()
    planner = FakePlanner()
    planner.result = FakePlannerResult(created=False, updated=True)
    pinned: list[Path] = []
    service = ProjectService(
        config=config,
        fiche_service=FicheService(),
        repertoire_service=repertoire,  # type: ignore[arg-type]
        outlook=outlook,
        planner=planner,
        pin_path=pinned.append,
    )
    project = ProjectInput(
        number=parse_project_number("2026-4995"),
        designation="Escalier corrige",
        planner=PlannerTaskInput(enabled=True),
    )

    result = await service.update_project(project)

    assert repertoire.calls == [(project, True)]
    assert outlook.validated is True
    assert outlook.paths == [["2026", "2026-4995 (Escalier corrige)"]]
    assert planner.projects == [project]
    assert result.outlook_folder_created is True
    assert result.planner_task_id == "task-id"
    assert result.planner_task_updated is True
    assert pinned == [project_dir]


@pytest.mark.asyncio
async def test_update_subproject_updates_planner_without_outlook(tmp_path: Path) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    project_dir.mkdir(parents=True)
    Workbook().save(project_dir / "2026-4995 - Fiche dossier clients.xlsx")
    config.outlook.enabled = True
    config.planner.enabled = True

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
    project = ProjectInput(
        number=parse_project_number("2026-4995-2"),
        designation="Variante",
        planner=PlannerTaskInput(enabled=True),
    )

    result = await service.update_project(project)

    assert repertoire.calls == [(project, True)]
    assert outlook.validated is False
    assert outlook.paths == []
    assert planner.projects == [project]
    assert result.outlook_folder_created is False
    assert result.planner_task_id == "task-id"
    assert result.planner_task_created is True


@pytest.mark.asyncio
async def test_create_project_skips_outlook_when_disabled(tmp_path: Path) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    config.paths.dossier_reference = tmp_path / "reference"
    config.paths.dossier_reference.mkdir(parents=True)
    workbook = Workbook()
    workbook.save(config.paths.dossier_reference / "modele fiche.xlsx")

    repertoire = FakeRepertoireService()
    outlook = FakeOutlook()
    service = ProjectService(
        config=config,
        fiche_service=FicheService(),
        repertoire_service=repertoire,  # type: ignore[arg-type]
        outlook=outlook,
    )

    result = await service.create_project(
        ProjectInput(number=parse_project_number("2026-4995"), designation="Escalier"),
    )

    assert result.outlook_folder_created is False
    assert outlook.validated is False
    assert outlook.paths == []


@pytest.mark.asyncio
async def test_create_project_skips_planner_when_disabled(tmp_path: Path) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    config.paths.dossier_reference = tmp_path / "reference"
    config.paths.dossier_reference.mkdir(parents=True)
    Workbook().save(config.paths.dossier_reference / "modele fiche.xlsx")

    repertoire = FakeRepertoireService()
    planner = FakePlanner()
    service = ProjectService(
        config=config,
        fiche_service=FicheService(),
        repertoire_service=repertoire,  # type: ignore[arg-type]
        planner=planner,
    )

    result = await service.create_project(
        ProjectInput(number=parse_project_number("2026-4995"), designation="Escalier"),
    )

    assert result.planner_task_id is None
    assert planner.projects == []


@pytest.mark.asyncio
async def test_create_project_skips_planner_when_form_option_disabled(tmp_path: Path) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    config.paths.dossier_reference = tmp_path / "reference"
    config.paths.dossier_reference.mkdir(parents=True)
    Workbook().save(config.paths.dossier_reference / "modele fiche.xlsx")
    config.planner.enabled = True

    planner = FakePlanner()
    service = ProjectService(
        config=config,
        fiche_service=FicheService(),
        repertoire_service=FakeRepertoireService(),  # type: ignore[arg-type]
        planner=planner,
    )

    result = await service.create_project(
        ProjectInput(number=parse_project_number("2026-4995"), designation="Escalier"),
    )

    assert result.planner_task_id is None
    assert planner.projects == []


@pytest.mark.asyncio
async def test_create_project_requires_planner_connector_when_enabled(tmp_path: Path) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    config.paths.dossier_reference = tmp_path / "reference"
    config.paths.dossier_reference.mkdir(parents=True)
    Workbook().save(config.paths.dossier_reference / "modele fiche.xlsx")
    config.planner.enabled = True

    service = ProjectService(
        config=config,
        fiche_service=FicheService(),
        repertoire_service=FakeRepertoireService(),  # type: ignore[arg-type]
        planner=None,
    )

    with pytest.raises(ConfigError, match="Planner"):
        await service.create_project(
            ProjectInput(
                number=parse_project_number("2026-4995"),
                designation="Escalier",
                planner=PlannerTaskInput(enabled=True),
            ),
        )


@pytest.mark.asyncio
async def test_create_project_validates_outlook_before_file_operations(tmp_path: Path) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    config.paths.dossier_reference = tmp_path / "reference"
    config.paths.dossier_reference.mkdir(parents=True)
    workbook = Workbook()
    workbook.save(config.paths.dossier_reference / "modele fiche.xlsx")
    config.outlook.enabled = True

    repertoire = FakeRepertoireService()
    service = ProjectService(
        config=config,
        fiche_service=FicheService(),
        repertoire_service=repertoire,  # type: ignore[arg-type]
        outlook=BrokenOutlook(),
    )

    with pytest.raises(ConfigError, match="Compte Outlook introuvable"):
        await service.create_project(
            ProjectInput(number=parse_project_number("2026-4995"), designation="Escalier"),
        )

    assert not (config.paths.racine_projets / "2026" / "2026-4995").exists()
    assert repertoire.calls == []


@pytest.mark.asyncio
async def test_create_project_requires_local_outlook_connector_when_enabled(tmp_path: Path) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    config.paths.dossier_reference = tmp_path / "reference"
    config.paths.dossier_reference.mkdir(parents=True)
    workbook = Workbook()
    workbook.save(config.paths.dossier_reference / "modele fiche.xlsx")
    config.outlook.enabled = True

    service = ProjectService(
        config=config,
        fiche_service=FicheService(),
        repertoire_service=FakeRepertoireService(),  # type: ignore[arg-type]
        outlook=None,
    )

    with pytest.raises(ConfigError, match="connecteur Outlook local"):
        await service.create_project(
            ProjectInput(number=parse_project_number("2026-4995"), designation="Escalier"),
        )


@pytest.mark.asyncio
async def test_create_subproject_reuses_parent_folder_without_selected_integrations(
    tmp_path: Path,
) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    config.planner.enabled = True
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
    assert planner.projects == []


@pytest.mark.asyncio
async def test_create_subproject_creates_planner_task_without_outlook(tmp_path: Path) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    config.outlook.enabled = True
    config.planner.enabled = True
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    project_dir.mkdir(parents=True)
    Workbook().save(project_dir / "2026-4995 - Fiche dossier clients.xlsx")
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
    project = ProjectInput(
        number=parse_project_number("2026-4995-2"),
        designation="Variante",
        planner=PlannerTaskInput(enabled=True),
    )

    result = await service.create_project(project)

    assert Path(result.project_dir) == project_dir
    assert (project_dir / "2026-4995-2 - Fiche dossier clients.xlsx").exists()
    assert repertoire.calls == [(project, False)]
    assert outlook.validated is False
    assert outlook.paths == []
    assert planner.projects == [project]
    assert result.outlook_folder_created is False
    assert result.planner_task_id == "task-id"
    assert result.planner_task_created is True


@pytest.mark.asyncio
async def test_delete_main_project_removes_related_integrations_and_trashes_parent(
    tmp_path: Path,
) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    project_dir.mkdir(parents=True)
    config.outlook.enabled = True
    config.planner.enabled = True
    repertoire = FakeRepertoireService()
    outlook = FakeOutlook()
    planner = FakePlanner()
    trashed: list[Path] = []
    service = ProjectService(
        config=config,
        fiche_service=FicheService(),
        repertoire_service=repertoire,  # type: ignore[arg-type]
        outlook=outlook,
        planner=planner,
        trash_path=lambda path: trashed.append(path) is None,
    )
    main = ProjectInput(
        number=parse_project_number("2026-4995"),
        designation="Escalier",
    )
    subproject = ProjectInput(
        number=parse_project_number("2026-4995-2"),
        designation="Variante",
    )
    rows = (
        RepertoireRow(
            row_index=10,
            values=("2026-4995", date(2026, 8, 14), "Balz", "Lionel", "Escalier"),
        ),
        RepertoireRow(
            row_index=11,
            values=("2026-4995-2", date(2026, 8, 14), "Balz", "Lionel", "Variante"),
        ),
    )

    result = await service.delete_project(
        main,
        related_projects=(main, subproject),
        repertoire_rows=rows,
    )

    assert repertoire.validated_deletions == [(main.number, rows)]
    assert repertoire.cleared_deletions == [(main.number, rows)]
    assert planner.deleted_projects == [main, subproject]
    assert outlook.deleted_paths == [["2026", "2026-4995 (Escalier)"]]
    assert trashed == [project_dir]
    assert result.numbers_released == ("2026-4995", "2026-4995-2")
    assert result.planner_tasks_deleted == 2
    assert result.outlook_folders_deleted == 1


@pytest.mark.asyncio
async def test_delete_subproject_trashes_only_its_nested_folder(tmp_path: Path) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    nested_dir = project_dir / "2026-4995-2"
    nested_dir.mkdir(parents=True)
    repertoire = FakeRepertoireService()
    trashed: list[Path] = []
    service = ProjectService(
        config=config,
        fiche_service=FicheService(),
        repertoire_service=repertoire,  # type: ignore[arg-type]
        trash_path=lambda path: trashed.append(path) is None,
    )
    subproject = ProjectInput(number=parse_project_number("2026-4995-2"))
    rows = (
        RepertoireRow(
            row_index=11,
            values=("2026-4995-2", date(2026, 8, 14), "", "", "Variante"),
        ),
    )

    result = await service.delete_project(
        subproject,
        related_projects=(subproject,),
        repertoire_rows=rows,
    )

    assert trashed == [nested_dir]
    assert project_dir.exists()
    assert result.project_path_trashed is True
