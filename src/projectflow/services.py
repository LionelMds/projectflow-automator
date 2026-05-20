from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from projectflow.application_settings import ApplicationSettings
from projectflow.auth.msal_client import MsalAccessTokenProvider
from projectflow.config import AppConfig
from projectflow.core.fiche_service import FicheService
from projectflow.core.local_repertoire import LocalWorkbookGateway
from projectflow.core.project_service import ProjectService
from projectflow.core.repertoire_service import RepertoireService
from projectflow.exceptions import ConfigError
from projectflow.graph.client import GraphClient
from projectflow.graph.excel import GraphExcelWorkbookGateway
from projectflow.outlook.local import create_local_outlook_client
from projectflow.platform.filemanager import pin_to_filemanager_favorites


@dataclass(slots=True)
class ServiceContainer:
    config: AppConfig
    application_settings: ApplicationSettings | None = None
    fiche_service: FicheService | None = None
    repertoire_service: RepertoireService | None = None

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
        if repertoire.is_configured or _is_onedrive_path(workbook_path):
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
            graph = GraphClient(token_provider=token_provider)
            self.repertoire_service = RepertoireService(
                GraphExcelWorkbookGateway(graph=graph, config=repertoire),
            )
            return self.repertoire_service

        if not display_path:
            raise ConfigError("Repertoire chantier local non configure.")
        if not workbook_path.exists():
            raise ConfigError("Repertoire chantier local non configure.")
        self.repertoire_service = RepertoireService(LocalWorkbookGateway(workbook_path))
        return self.repertoire_service

    def project(self) -> ProjectService:
        return ProjectService(
            config=self.config,
            fiche_service=self.fiche(),
            repertoire_service=self.repertoire(),
            outlook=create_local_outlook_client(self.config.outlook),
            pin_path=pin_to_filemanager_favorites,
        )

    def reset_repertoire(self) -> None:
        self.repertoire_service = None

    async def close(self) -> None:
        return


def _is_onedrive_path(path: Path) -> bool:
    return any("onedrive" in part.casefold() for part in path.parts)
