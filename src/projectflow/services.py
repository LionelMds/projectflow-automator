from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from projectflow.application_settings import ApplicationSettings
from projectflow.auth.msal_client import PLANNER_GRAPH_SCOPES, MsalAccessTokenProvider
from projectflow.config import AppConfig, RepertoireChantierConfig
from projectflow.core.fiche_service import FicheService
from projectflow.core.local_repertoire import LocalWorkbookGateway
from projectflow.core.models import ProjectInput
from projectflow.core.project_service import ProjectService
from projectflow.core.repertoire_service import RepertoireService
from projectflow.exceptions import ConfigError
from projectflow.graph.client import GraphClient
from projectflow.graph.excel import GraphExcelWorkbookGateway
from projectflow.graph.planner import GraphPlannerClient, PlannerTaskResult
from projectflow.graph.workbook_opening import resolve_workbook_open_url
from projectflow.logging import get_logger
from projectflow.outlook.local import create_local_outlook_client
from projectflow.platform.filemanager import move_path_to_trash, pin_to_filemanager_favorites
from projectflow.platform.sync_paths import is_synchronized_path


@dataclass(slots=True)
class ServiceContainer:
    config: AppConfig
    application_settings: ApplicationSettings | None = None
    fiche_service: FicheService | None = None
    repertoire_service: RepertoireService | None = None
    planner_service: ConfiguredPlannerGateway | None = None

    def fiche(self) -> FicheService:
        if self.fiche_service is None:
            self.fiche_service = FicheService()
        return self.fiche_service

    def repertoire(self) -> RepertoireService:
        if self.repertoire_service is not None:
            return self.repertoire_service
        repertoire = self.config.paths.repertoire_chantier
        display_path = repertoire.display_path.strip()
        workbook_path = Path(display_path).expanduser()
        if (
            repertoire.is_configured
            or repertoire.cloud_only
            or display_path.casefold().startswith("https://")
            or is_synchronized_path(workbook_path)
        ):
            settings = self.application_settings or ApplicationSettings.load()
            if not settings.microsoft_client_id.strip():
                raise ConfigError(
                    "Le repertoire chantier doit etre ecrit directement dans OneDrive, "
                    "mais cette version de ProjectFlow n'embarque pas encore le connecteur "
                    "Microsoft. Ajoutez PROJECTFLOW_MICROSOFT_CLIENT_ID au build de release.",
                )
            token_provider = MsalAccessTokenProvider(
                client_id=settings.microsoft_client_id,
            )
            graph = GraphClient(token_provider=token_provider, request_timeout=60.0)
            self.repertoire_service = RepertoireService(
                GraphExcelWorkbookGateway(graph=graph, config=repertoire),
            )
            get_logger(__name__).info("repertoire.backend", backend="cloud")
            return self.repertoire_service

        if not display_path:
            raise ConfigError("Repertoire chantier local non configure.")
        if not workbook_path.exists():
            raise ConfigError("Repertoire chantier local non configure.")
        self.repertoire_service = RepertoireService(LocalWorkbookGateway(workbook_path))
        get_logger(__name__).info("repertoire.backend", backend="local", path=display_path)
        return self.repertoire_service

    def project(self) -> ProjectService:
        return ProjectService(
            config=self.config,
            fiche_service=self.fiche(),
            repertoire_service=self.repertoire(),
            outlook=create_local_outlook_client(self.config.outlook),
            planner=self.planner(),
            pin_path=pin_to_filemanager_favorites,
            trash_path=move_path_to_trash,
        )

    def planner(self) -> ConfiguredPlannerGateway | None:
        if not self.config.planner.enabled:
            return None
        if self.planner_service is not None:
            return self.planner_service
        settings = self.application_settings or ApplicationSettings.load()
        if not settings.microsoft_client_id.strip():
            raise ConfigError(
                "Planner actif mais cette version de ProjectFlow n'embarque pas encore "
                "le connecteur Microsoft.",
            )
        token_provider = MsalAccessTokenProvider(
            client_id=settings.microsoft_client_id,
            scopes=PLANNER_GRAPH_SCOPES,
        )
        graph = GraphClient(token_provider=token_provider)
        self.planner_service = ConfiguredPlannerGateway(
            client=GraphPlannerClient(graph=graph),
            config=self.config,
        )
        return self.planner_service

    def reset_repertoire(self) -> None:
        self.repertoire_service = None

    def reset_planner(self) -> None:
        self.planner_service = None

    async def close(self) -> None:
        return


@dataclass(slots=True)
class ConfiguredPlannerGateway:
    client: GraphPlannerClient
    config: AppConfig

    async def ensure_project_task(self, project: ProjectInput) -> PlannerTaskResult:
        return await self.client.ensure_project_task(project, self.config.planner)

    async def delete_project_tasks(self, project: ProjectInput) -> int:
        return await self.client.delete_project_tasks(project, self.config.planner)


async def resolve_repertoire_open_url(config: RepertoireChantierConfig) -> str:
    settings = ApplicationSettings.load()
    graph = GraphClient(
        token_provider=MsalAccessTokenProvider(client_id=settings.microsoft_client_id),
        request_timeout=60.0,
    )
    try:
        return await resolve_workbook_open_url(graph, config)
    finally:
        await graph.aclose()
