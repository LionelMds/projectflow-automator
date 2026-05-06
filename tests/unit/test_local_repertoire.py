from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import PatternFill

from projectflow.core.local_repertoire import LocalWorkbookGateway
from projectflow.core.models import ProjectInput
from projectflow.core.numero import parse_project_number
from projectflow.core.repertoire_service import RepertoireService


def _create_repertoire(path: Path) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "2026"
    worksheet.append(["Numero", "Societe", "Contact", "Localisation", "Description", "Gere par"])
    worksheet.append(["2026-4995", "", "", "", "", ""])
    worksheet.append(["2026-5000", "", "", "", "", ""])
    workbook.save(path)
    workbook.close()


def _create_styled_repertoire(path: Path) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "2026"
    worksheet.append(["Numero", "Societe", "Contact", "Localisation", "Description", "Gere par"])
    worksheet.append(["2026-4995", "Balz", "Lionel", "Zurich", "Escalier", "Code"])
    worksheet.append(["2026-5000", "", "", "", "", ""])
    fill = PatternFill(fill_type="solid", fgColor="FFF2CC")
    for column_index in range(1, 7):
        worksheet.cell(row=3, column=column_index).fill = fill
    worksheet.row_dimensions[3].height = 28
    workbook.save(path)
    workbook.close()


@pytest.mark.asyncio
async def test_local_repertoire_updates_main_project(tmp_path: Path) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_repertoire(path)
    service = RepertoireService(LocalWorkbookGateway(path))
    project = ProjectInput(
        number=parse_project_number("2026-4995"),
        designation="Escalier",
        societe="Balz",
        contact="Lionel",
        localisation="Zurich",
        gere_par="LM",
    )

    await service.upsert_project(project)

    workbook = load_workbook(path)
    worksheet = workbook["2026"]
    assert worksheet["A2"].value == "2026-4995"
    assert worksheet["B2"].value == "Balz"
    assert worksheet["E2"].value == "Escalier"
    workbook.close()


@pytest.mark.asyncio
async def test_local_repertoire_inserts_subproject_without_overwriting_next_row(
    tmp_path: Path,
) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_repertoire(path)
    service = RepertoireService(LocalWorkbookGateway(path))
    parent = ProjectInput(number=parse_project_number("2026-4995"), designation="Escalier")
    subproject = ProjectInput(number=parse_project_number("2026-4995-2"), designation="Variante")

    await service.upsert_project(parent)
    await service.upsert_project(subproject)

    workbook = load_workbook(path)
    worksheet = workbook["2026"]
    assert worksheet["A2"].value == "2026-4995"
    assert worksheet["A3"].value == "2026-4995-2"
    assert worksheet["A4"].value == "2026-5000"
    workbook.close()


@pytest.mark.asyncio
async def test_local_repertoire_inserts_subproject_with_blank_values_and_available_row_format(
    tmp_path: Path,
) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_styled_repertoire(path)
    service = RepertoireService(LocalWorkbookGateway(path))
    subproject = ProjectInput(
        number=parse_project_number("2026-4995-2"),
        designation="Variante",
        contact="Contact saisi",
    )

    await service.upsert_project(subproject)

    workbook = load_workbook(path)
    worksheet = workbook["2026"]
    assert worksheet["A3"].value == "2026-4995-2"
    assert worksheet["B3"].value is None
    assert worksheet["C3"].value == "Contact saisi"
    assert worksheet["D3"].value is None
    assert worksheet["E3"].value == "Variante"
    assert worksheet["F3"].value is None
    assert worksheet["A4"].value == "2026-5000"
    assert worksheet["A3"].fill.fgColor.rgb == "00FFF2CC"
    assert worksheet.row_dimensions[3].height == 28
    workbook.close()
