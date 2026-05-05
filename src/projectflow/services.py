from __future__ import annotations

from dataclasses import dataclass

from projectflow.auth.msal_client import MsalAuthClient
from projectflow.config import AppConfig
from projectflow.core.fiche_service import FicheService
from projectflow.core.project_service import ProjectService
from projectflow.core.repertoire_service import RepertoireService
from projectflow.exceptions import ConfigError
from projectflow.graph.client import GraphClient
from projectflow.graph.excel import ExcelWorkbookClient, WorkbookRef
from projectflow.graph.outlook import OutlookClient
from projectflow.graph.planner import PlannerClient
from projectflow.platform.filemanager import pin_to_filemanager_favorites


@dataclass(slots=True)
class ServiceContainer:
    config: AppConfig
    auth_client: MsalAuthClient | None = None
    graph_client: GraphClient | None = None
    fiche_service: FicheService | None = None

    def fiche(self) -> FicheService:
        if self.fiche_service is None:
            self.fiche_service = FicheService()
        return self.fiche_service

    def repertoire(self) -> RepertoireService:
        repertoire = self.config.paths.repertoire_chantier
        if not repertoire.is_configured:
            raise ConfigError("Repertoire chantier Graph non configure.")
        workbook = ExcelWorkbookClient(
            self.graph(),
            WorkbookRef(drive_id=repertoire.drive_id, item_id=repertoire.item_id),
        )
        return RepertoireService(workbook)

    def project(self) -> ProjectService:
        return ProjectService(
            config=self.config,
            fiche_service=self.fiche(),
            repertoire_service=self.repertoire(),
            outlook=OutlookClient(self.graph()),
            planner=PlannerClient(self.graph()),
            pin_path=pin_to_filemanager_favorites,
        )

    def graph(self) -> GraphClient:
        if self.graph_client is None:
            self.graph_client = GraphClient(self.auth())
        return self.graph_client

    def auth(self) -> MsalAuthClient:
        if self.auth_client is None:
            self.auth_client = MsalAuthClient()
        return self.auth_client

    def sign_out(self) -> None:
        self.auth().sign_out()

    async def close(self) -> None:
        if self.graph_client is not None:
            await self.graph_client.aclose()
