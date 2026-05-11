from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from projectflow.config import AppConfig
from projectflow.core.excel_com_repertoire import ExcelComWorkbookGateway
from projectflow.core.fiche_service import FicheService
from projectflow.core.local_repertoire import LocalWorkbookGateway
from projectflow.core.project_service import ProjectService
from projectflow.core.repertoire_queue import RepertoireTransactionStore
from projectflow.core.repertoire_service import RepertoireService
from projectflow.exceptions import ConfigError
from projectflow.outlook.local import create_local_outlook_client
from projectflow.platform.filemanager import pin_to_filemanager_favorites


@dataclass(slots=True)
class ServiceContainer:
    config: AppConfig
    fiche_service: FicheService | None = None

    def fiche(self) -> FicheService:
        if self.fiche_service is None:
            self.fiche_service = FicheService()
        return self.fiche_service

    def repertoire(self) -> RepertoireService:
        repertoire = self.config.paths.repertoire_chantier
        workbook_path = Path(repertoire.display_path).expanduser()
        if not workbook_path.exists():
            raise ConfigError("Repertoire chantier local non configure.")
        return RepertoireService(
            _workbook_gateway_for_path(workbook_path),
            transaction_store=RepertoireTransactionStore.for_workbook(workbook_path),
        )

    def project(self) -> ProjectService:
        return ProjectService(
            config=self.config,
            fiche_service=self.fiche(),
            repertoire_service=self.repertoire(),
            outlook=create_local_outlook_client(self.config.outlook),
            pin_path=pin_to_filemanager_favorites,
        )

    async def close(self) -> None:
        return


def _workbook_gateway_for_path(
    workbook_path: Path,
) -> LocalWorkbookGateway | ExcelComWorkbookGateway:
    if _should_use_excel_com_gateway(workbook_path):
        return ExcelComWorkbookGateway(workbook_path)
    return LocalWorkbookGateway(workbook_path)


def _should_use_excel_com_gateway(workbook_path: Path) -> bool:
    override = os.environ.get("PROJECTFLOW_REPERTOIRE_GATEWAY", "").strip().casefold()
    if override in {"openpyxl", "local"}:
        return False
    if override in {"excel", "com", "excel-com"}:
        return sys.platform == "win32"
    return sys.platform == "win32" and "onedrive" in str(workbook_path).casefold()
