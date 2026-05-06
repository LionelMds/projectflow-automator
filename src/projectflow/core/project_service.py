from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from projectflow.config import AppConfig, OutlookFolderConfig
from projectflow.core.fiche_service import FicheService, standard_fiche_path
from projectflow.core.models import ProjectCreationResult, ProjectInput
from projectflow.core.numero import project_folder_name
from projectflow.core.repertoire_service import RepertoireService
from projectflow.exceptions import ConfigError, ProjectCreationError


class OutlookGateway(Protocol):
    async def validate_target(self) -> None:
        """Validate that the configured Outlook target is available."""

    async def ensure_folder_path(self, names: list[str]) -> object:
        """Ensure the nested Outlook folder path exists."""


PinPathCallable = Callable[[Path], None]


class ProjectService:
    def __init__(
        self,
        *,
        config: AppConfig,
        fiche_service: FicheService,
        repertoire_service: RepertoireService,
        outlook: OutlookGateway | None = None,
        pin_path: PinPathCallable | None = None,
    ) -> None:
        self._config = config
        self._fiche_service = fiche_service
        self._repertoire_service = repertoire_service
        self._outlook = outlook
        self._pin_path = pin_path

    async def create_project(
        self,
        project: ProjectInput,
        *,
        force_overwrite: bool = False,
        update_existing_info: bool = True,
    ) -> ProjectCreationResult:
        if project.is_subproject:
            return await self.create_subproject(project)

        root = self._required_path(self._config.paths.racine_projets, "racine projets")
        outlook = await self._validated_outlook()
        project_dir = root / str(project.number.year) / project_folder_name(project.number)
        project_dir_created = not project_dir.exists()
        project_dir.mkdir(parents=True, exist_ok=True)

        fiche_path: Path | None = None
        if project_dir_created or update_existing_info:
            reference = self._required_path(
                self._config.paths.dossier_reference,
                "dossier de reference",
            )
            copy_reference_tree(reference, project_dir)
            fiche_path = self._fiche_service.fill_fiche(project_dir, project)
            await self._repertoire_service.upsert_project(
                project,
                force_overwrite=force_overwrite,
            )
        else:
            existing_fiche_path = standard_fiche_path(project_dir, project.number)
            if existing_fiche_path.exists():
                fiche_path = existing_fiche_path

        outlook_created = False
        if outlook is not None:
            folder_paths = outlook_folder_paths(project, self._config.outlook.arborescence)
            for folder_path in folder_paths:
                await outlook.ensure_folder_path(folder_path)
            outlook_created = bool(folder_paths)

        if self._pin_path is not None:
            self._pin_path(project_dir)

        return ProjectCreationResult(
            project_dir_created=project_dir_created,
            project_dir=str(project_dir),
            fiche_path=str(fiche_path) if fiche_path is not None else None,
            outlook_folder_created=outlook_created,
        )

    async def create_subproject(self, project: ProjectInput) -> ProjectCreationResult:
        if not project.is_subproject:
            raise ProjectCreationError("Le numero fourni n'est pas un sous-projet.")

        root = self._required_path(self._config.paths.racine_projets, "racine projets")
        project_dir = root / str(project.number.year) / project_folder_name(project.number)
        if not project_dir.exists():
            raise ProjectCreationError(f"Dossier du projet parent introuvable: {project_dir}")

        fiche_path = self._fiche_service.fill_subproject_fiche(project_dir, project)
        await self._repertoire_service.upsert_project(project)
        return ProjectCreationResult(
            project_dir_created=False,
            project_dir=str(project_dir),
            fiche_path=str(fiche_path),
        )

    async def update_project(self, project: ProjectInput) -> ProjectCreationResult:
        root = self._required_path(self._config.paths.racine_projets, "racine projets")
        project_dir = root / str(project.number.year) / project_folder_name(project.number)
        if not project_dir.exists():
            raise ProjectCreationError(f"Dossier projet introuvable: {project_dir}")

        fiche_path = (
            self._fiche_service.fill_subproject_fiche(project_dir, project)
            if project.is_subproject
            else self._fiche_service.fill_fiche(project_dir, project)
        )
        await self._repertoire_service.upsert_project(project, force_overwrite=True)
        return ProjectCreationResult(
            project_dir_created=False,
            project_dir=str(project_dir),
            fiche_path=str(fiche_path),
        )

    @staticmethod
    def _required_path(path: Path | None, label: str) -> Path:
        if path is None:
            raise ConfigError(f"Chemin manquant: {label}")
        return path

    async def _validated_outlook(self) -> OutlookGateway | None:
        if not self._config.outlook.enabled:
            return None
        if self._outlook is None:
            raise ConfigError(
                "Creation Outlook activee mais aucun connecteur Outlook local n'est configure.",
            )
        await self._outlook.validate_target()
        return self._outlook


def copy_reference_tree(reference_dir: Path, project_dir: Path) -> None:
    if not reference_dir.exists() or not reference_dir.is_dir():
        raise ProjectCreationError(f"Dossier de reference invalide: {reference_dir}")

    for source in reference_dir.rglob("*"):
        relative = source.relative_to(reference_dir)
        destination = project_dir / relative
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            continue
        shutil.copy2(source, destination)


def outlook_folder_paths(
    project: ProjectInput,
    arborescence: list[OutlookFolderConfig],
) -> list[list[str]]:
    paths: list[list[str]] = []
    for folder in arborescence:
        _collect_outlook_folder_paths(project, folder, [], paths)
    return paths


def _collect_outlook_folder_paths(
    project: ProjectInput,
    folder: OutlookFolderConfig,
    parents: list[str],
    paths: list[list[str]],
) -> None:
    rendered_name = render_outlook_folder_name(folder.name, project)
    current_path = [*parents, rendered_name]
    if not folder.children:
        paths.append(current_path)
        return
    for child in folder.children:
        _collect_outlook_folder_paths(project, child, current_path, paths)


def render_outlook_folder_name(template: str, project: ProjectInput) -> str:
    return (
        template.replace("[YYYY]", str(project.number.year))
        .replace("[XXXX]", project.number.project_id)
        .replace("[NUMERO]", str(project.number))
        .replace("[PROJECT]", str(project.number))
    )
