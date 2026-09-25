from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast

from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from projectflow.cad.demo_document_manager import (
    DEMO_LICENSE_KEY,
    open_json_document_manager,
    write_demo_document,
)
from projectflow.cad.solidworks_properties import TEMPLATE_MARKER
from projectflow.cad.templates import CadTemplateService
from projectflow.config import AppConfig, RepertoireChantierConfig, UserConfig
from projectflow.core.fiche_service import FICHE_SUFFIX, FicheService
from projectflow.core.local_repertoire import LocalWorkbookGateway
from projectflow.core.project_service import ProjectService
from projectflow.core.repertoire_service import RepertoireService
from projectflow.outlook.local import create_local_outlook_client
from projectflow.platform.paths import data_dir


@dataclass(slots=True)
class DemoServiceContainer:
    config: AppConfig
    workbook_path: Path
    fiche_service: FicheService | None = None

    def fiche(self) -> FicheService:
        if self.fiche_service is None:
            self.fiche_service = FicheService()
        return self.fiche_service

    def repertoire(self) -> RepertoireService:
        return RepertoireService(LocalWorkbookGateway(self.workbook_path))

    def project(self) -> ProjectService:
        return ProjectService(
            config=self.config,
            fiche_service=self.fiche(),
            repertoire_service=self.repertoire(),
            outlook=create_local_outlook_client(self.config.outlook),
            cad=CadTemplateService(
                license_key_loader=lambda: DEMO_LICENSE_KEY,
                manager_factory=open_json_document_manager,
            ),
        )


def build_demo_environment(
    *,
    base_dir: Path | None = None,
) -> tuple[AppConfig, DemoServiceContainer]:
    root = base_dir or data_dir() / "demo"
    clients_dir = root / "Clients"
    reference_dir = root / "Modeles" / "10-Racine"
    repertoire_path = root / "Repertoire chantier demo.xlsx"
    solidworks_dir = root / "Modeles" / "11-Racine Solidworks"
    autocad_dir = root / "Modeles" / "12-Racine AutoCAD"

    clients_dir.mkdir(parents=True, exist_ok=True)
    reference_dir.mkdir(parents=True, exist_ok=True)
    _ensure_reference_fiche(reference_dir)
    _ensure_repertoire(repertoire_path)
    _ensure_cad_templates(solidworks_dir, autocad_dir)

    config = AppConfig()
    config.user = UserConfig(
        tenant_id="demo",
        user_id="demo",
        display_name="Mode demo",
        email="demo@projectflow.local",
    )
    config.paths.racine_projets = clients_dir
    config.paths.dossier_reference = reference_dir
    config.paths.repertoire_chantier = RepertoireChantierConfig(
        drive_id="local-demo",
        item_id=str(repertoire_path),
        display_path=str(repertoire_path),
    )
    config.cad.solidworks_template_dir = solidworks_dir
    config.cad.autocad_template_dir = autocad_dir
    config.cad.destination_subfolder = "CAO"
    services = DemoServiceContainer(config=config, workbook_path=repertoire_path)
    return config, services


def _ensure_reference_fiche(reference_dir: Path) -> None:
    fiche_path = reference_dir / f"modele{FICHE_SUFFIX}"
    if fiche_path.exists():
        return
    workbook = Workbook()
    worksheet = cast("Worksheet", workbook.active)
    worksheet.title = "Fiche"
    worksheet["C3"] = ""
    worksheet["D3"] = "Societe : "
    worksheet["D4"] = "Contact : "
    worksheet["D5"] = "Projet : "
    worksheet["D6"] = "Localisation : "
    worksheet["C6"] = ""
    workbook.save(fiche_path)


def _ensure_cad_templates(solidworks_dir: Path, autocad_dir: Path) -> None:
    """Fake SolidWorks files (JSON) that the demo Document Manager can read and write."""
    parts = ["ENV-100.SLDPRT", "PRT-100.SLDPRT", "PRT-200.SLDPRT"]
    common = {
        "Projet": TEMPLATE_MARKER,
        "Client": "",
        "Auteur": "",
        "Révision": "",
        "Fournisseur": "Balz Métal SA",
        "FaireouAcheter": "Faire",
    }
    for name in parts:
        path = solidworks_dir / f"{TEMPLATE_MARKER}-{name}"
        if not path.exists():
            write_demo_document(path, common)
    assembly = solidworks_dir / f"{TEMPLATE_MARKER}-ENS-100.SLDASM"
    if not assembly.exists():
        write_demo_document(
            assembly,
            common,
            [str(solidworks_dir / f"{TEMPLATE_MARKER}-{name}") for name in parts],
            configurations={"Défaut": {"Description": ""}},
        )
    drawing = autocad_dir / f"{TEMPLATE_MARKER}-ENS-100.dwg"
    if not drawing.exists():
        drawing.parent.mkdir(parents=True, exist_ok=True)
        drawing.write_bytes(b"AC1032 ProjectFlow demo")


def _ensure_repertoire(repertoire_path: Path) -> None:
    current_year = date.today().year
    workbook = load_workbook(repertoire_path) if repertoire_path.exists() else Workbook()

    if "Sheet" in workbook.sheetnames and str(current_year) not in workbook.sheetnames:
        workbook.remove(workbook["Sheet"])

    for year in [current_year, current_year + 1]:
        worksheet_name = str(year)
        if worksheet_name not in workbook.sheetnames:
            worksheet = workbook.create_sheet(worksheet_name)
            worksheet.append([
                "Numero",
                "Date",
                "Societe",
                "Contact",
                "Description",
                "Gere par",
            ])
            for project_id in range(4995, 5005):
                worksheet.append([f"{year}-{project_id}", "", "", "", "", ""])

    workbook.save(repertoire_path)
    workbook.close()
