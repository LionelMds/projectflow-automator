"""A fiche edited by ProjectFlow keeps everything the application does not manage."""

from __future__ import annotations

import struct
import zlib
from datetime import date
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from projectflow.config import AppConfig
from projectflow.core.fiche_service import FicheService
from projectflow.core.models import ProjectInput
from projectflow.core.numero import parse_project_number
from projectflow.core.project_service import ProjectService

SHEET_PART = "xl/worksheets/sheet1.xml"
PHOTO_PART = "xl/media/image1.png"
DRAWING_PART = "xl/drawings/drawing1.xml"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

# A photo and a hand-drawn text box, the way Excel stores them. openpyxl has no model for
# the text box, and none for the photo either when Pillow is missing.
DRAWING_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<xdr:wsDr xmlns:xdr="{DRAWING_NS}" xmlns:a="{A_NS}" xmlns:r="{R_NS}">
<xdr:twoCellAnchor editAs="oneCell">
<xdr:from><xdr:col>1</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>12</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>
<xdr:to><xdr:col>4</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>24</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>
<xdr:pic><xdr:nvPicPr><xdr:cNvPr id="2" name="Photo chantier"/><xdr:cNvPicPr/></xdr:nvPicPr>
<xdr:blipFill><a:blip r:embed="rId1"/><a:srcRect l="10000" t="5000"/>
<a:stretch><a:fillRect/></a:stretch></xdr:blipFill>
<xdr:spPr><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr></xdr:pic>
<xdr:clientData/></xdr:twoCellAnchor>
<xdr:twoCellAnchor>
<xdr:from><xdr:col>5</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>12</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>
<xdr:to><xdr:col>7</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>15</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>
<xdr:sp macro="" textlink=""><xdr:nvSpPr><xdr:cNvPr id="3" name="Remarque"/>
<xdr:cNvSpPr txBox="1"/></xdr:nvSpPr>
<xdr:spPr><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr>
<xdr:txBody><a:bodyPr/><a:p><a:r><a:t>Attention au seuil</a:t></a:r></a:p></xdr:txBody></xdr:sp>
<xdr:clientData/></xdr:twoCellAnchor>
</xdr:wsDr>"""


def _png_bytes() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixel = zlib.compress(b"\x00\xc8\x32\x32")
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixel) + chunk(b"IEND", b"")
    )


def _relationships(relation_id: str, kind: str, target: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Relationships xmlns="{RELS_NS}">'
        f'<Relationship Id="{relation_id}" Type="{R_NS}/{kind}" Target="{target}"/>'
        "</Relationships>"
    )


def _write_fiche_with_photo(path: Path) -> None:
    """Write a filled fiche holding a photo, a text box, formatting and manual content."""
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Fiche"
    worksheet["C3"] = "2026-4995"
    worksheet["D3"] = "Societe : Balz"
    worksheet["D3"].font = Font(bold=True, color="FF0000FF")
    worksheet["D3"].fill = PatternFill("solid", fgColor="FFFFF2CC")
    worksheet["D4"] = "Contact : Lionel"
    worksheet["D5"] = "Projet : Escalier"
    worksheet["D6"] = "Localisation : Zurich"
    worksheet["C6"] = "Claire Martin"
    worksheet["C9"] = "LM"
    worksheet["B9"] = date(2026, 1, 15)
    worksheet["B9"].number_format = "DD.MM.YYYY"
    worksheet["F12"] = "Note ajoutee a la main"
    worksheet["F12"].font = Font(italic=True)
    worksheet["A30"] = "=SUM(1,2)"
    worksheet.merge_cells("A11:E11")
    worksheet.column_dimensions["D"].width = 42
    notes = workbook.create_sheet("Mesures")
    notes["A1"] = "Hauteur"
    notes["B1"] = 2.75
    workbook.save(path)
    workbook.close()

    with ZipFile(path) as archive:
        parts = {info.filename: archive.read(info) for info in archive.infolist()}
    sheet = parts[SHEET_PART].decode()
    parts[SHEET_PART] = sheet.replace(
        "</worksheet>",
        f'<drawing xmlns:r="{R_NS}" r:id="rId1"/></worksheet>',
    ).encode()
    types = parts["[Content_Types].xml"].decode()
    parts["[Content_Types].xml"] = types.replace(
        "</Types>",
        '<Default Extension="png" ContentType="image/png"/>'
        f'<Override PartName="/{DRAWING_PART}" '
        'ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/></Types>',
    ).encode()
    parts["xl/worksheets/_rels/sheet1.xml.rels"] = _relationships(
        "rId1",
        "drawing",
        "../drawings/drawing1.xml",
    ).encode()
    parts[DRAWING_PART] = DRAWING_XML.encode()
    parts["xl/drawings/_rels/drawing1.xml.rels"] = _relationships(
        "rId1",
        "image",
        "../media/image1.png",
    ).encode()
    parts[PHOTO_PART] = _png_bytes()
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)


def _parts(path: Path) -> dict[str, bytes]:
    with ZipFile(path) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


@pytest.fixture
def without_pillow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read workbooks the way the packaged application does: it ships without Pillow."""
    monkeypatch.setattr("openpyxl.reader.drawings.PILImage", False)


class _Repertoire:
    async def upsert_project(self, project: ProjectInput, *, force_overwrite: bool = False) -> None:
        """Accept the répertoire row without storing it."""


@pytest.mark.usefixtures("without_pillow")
@pytest.mark.asyncio
async def test_updating_a_project_keeps_the_photos_of_its_fiche(tmp_path: Path) -> None:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    project_dir = config.paths.racine_projets / "2026" / "2026-4995"
    project_dir.mkdir(parents=True)
    fiche_path = project_dir / "2026-4995 - Fiche dossier clients.xlsx"
    _write_fiche_with_photo(fiche_path)
    before = _parts(fiche_path)
    service = ProjectService(
        config=config,
        fiche_service=FicheService(),
        repertoire_service=_Repertoire(),  # type: ignore[arg-type]
    )

    await service.update_project(
        ProjectInput(
            number=parse_project_number("2026-4995"),
            designation="Escalier et garde-corps",
            societe="Balz",
            contact="Lionel",
            localisation="Zurich",
            gere_par="Claire Martin",
        ),
    )

    after = _parts(fiche_path)
    assert after[PHOTO_PART] == before[PHOTO_PART]
    assert after[DRAWING_PART] == before[DRAWING_PART]
    assert b"<drawing " in after[SHEET_PART]
    assert FicheService().read_fiche(fiche_path).designation == "Escalier et garde-corps"


def _update(fiche_path: Path, **fields: str) -> None:
    project = ProjectInput(number=parse_project_number("2026-4995"), **fields)  # type: ignore[arg-type]
    FicheService(today=lambda: date(2026, 5, 6)).fill_fiche(fiche_path.parent, project)


@pytest.mark.usefixtures("without_pillow")
def test_update_changes_only_the_edited_cells(tmp_path: Path) -> None:
    fiche_path = tmp_path / "2026-4995 - Fiche dossier clients.xlsx"
    _write_fiche_with_photo(fiche_path)
    before = _parts(fiche_path)

    _update(fiche_path, societe="Balz Metal", designation="Escalier", contact="Lionel")

    after = _parts(fiche_path)
    assert list(after) == list(before)
    assert [name for name in before if after[name] != before[name]] == [SHEET_PART]
    workbook = load_workbook(fiche_path)
    worksheet = workbook["Fiche"]
    assert worksheet["D3"].value == "Societe : Balz Metal"
    assert worksheet["D3"].font.bold
    assert worksheet["D3"].font.color.rgb == "FF0000FF"
    assert worksheet["D3"].fill.fgColor.rgb == "FFFFF2CC"
    assert worksheet["D5"].value == "Projet : Escalier"
    assert worksheet["D6"].value == "Localisation : Zurich"
    assert worksheet["B9"].value.date() == date(2026, 1, 15)
    assert worksheet["B9"].number_format == "DD.MM.YYYY"
    assert worksheet["F12"].value == "Note ajoutee a la main"
    assert worksheet["F12"].font.italic
    assert worksheet["A30"].value == "=SUM(1,2)"
    assert [str(merged) for merged in worksheet.merged_cells.ranges] == ["A11:E11"]
    assert worksheet.column_dimensions["D"].width == 42
    assert workbook["Mesures"]["B1"].value == 2.75
    workbook.close()


def test_update_without_any_change_leaves_the_file_as_it_is(tmp_path: Path) -> None:
    fiche_path = tmp_path / "2026-4995 - Fiche dossier clients.xlsx"
    _write_fiche_with_photo(fiche_path)
    original = fiche_path.read_bytes()

    _update(
        fiche_path,
        societe="Balz",
        contact="Lionel",
        designation="Escalier",
        localisation="Zurich",
        gere_par="Claire Martin",
    )

    assert fiche_path.read_bytes() == original


@pytest.mark.usefixtures("without_pillow")
def test_new_fiche_dates_the_template_without_losing_its_photo(tmp_path: Path) -> None:
    template_path = tmp_path / "modele fiche.xlsx"
    _write_fiche_with_photo(template_path)
    before = _parts(template_path)

    fiche_path = FicheService(today=lambda: date(2026, 5, 6)).fill_fiche(
        tmp_path,
        ProjectInput(number=parse_project_number("2026-5001"), designation="Portail"),
        new_fiche=True,
        user_initials="LM",
    )

    after = _parts(fiche_path)
    assert after[PHOTO_PART] == before[PHOTO_PART]
    assert after[DRAWING_PART] == before[DRAWING_PART]
    workbook = load_workbook(fiche_path)
    assert workbook["Fiche"]["C3"].value == "2026-5001"
    assert workbook["Fiche"]["B9"].value.date() == date(2026, 5, 6)
    assert workbook["Fiche"]["B9"].number_format == "DD.MM.YYYY"
    workbook.close()


@pytest.mark.usefixtures("without_pillow")
def test_subproject_fiche_copied_from_its_parent_keeps_the_photo(tmp_path: Path) -> None:
    parent_path = tmp_path / "2026-4995 - Fiche dossier clients.xlsx"
    _write_fiche_with_photo(parent_path)
    parent = _parts(parent_path)

    fiche_path = FicheService(today=lambda: date(2026, 5, 6)).fill_subproject_fiche(
        tmp_path,
        ProjectInput(number=parse_project_number("2026-4995-2"), designation="Rampe"),
    )

    copied = _parts(fiche_path)
    assert copied[PHOTO_PART] == parent[PHOTO_PART]
    assert copied[DRAWING_PART] == parent[DRAWING_PART]
    assert _parts(parent_path) == parent
    loaded = FicheService().read_fiche(fiche_path)
    assert loaded.number == "2026-4995-2"
    assert loaded.designation == "Rampe"
    assert loaded.societe == "Balz"


@pytest.mark.usefixtures("without_pillow")
def test_dating_a_dossier_output_keeps_the_photo(tmp_path: Path) -> None:
    fiche_path = tmp_path / "fiche.xlsx"
    _write_fiche_with_photo(fiche_path)
    before = _parts(fiche_path)

    FicheService(today=lambda: date(2026, 7, 15)).ensure_atelier_date(fiche_path)

    after = _parts(fiche_path)
    assert [name for name in before if after[name] != before[name]] == [SHEET_PART]
    assert PHOTO_PART in after
    workbook = load_workbook(fiche_path)
    assert workbook["Fiche"]["E2"].value == "fiche d'atelier le 15.07.2026"
    workbook.close()


@pytest.mark.usefixtures("without_pillow")
def test_reference_link_is_added_without_losing_the_photo(tmp_path: Path) -> None:
    project_dir = tmp_path / "Clients" / "2026" / "2026-4995"
    project_dir.mkdir(parents=True)
    fiche_path = project_dir / "2026-4995 - Fiche dossier clients.xlsx"
    _write_fiche_with_photo(fiche_path)
    before = _parts(fiche_path)
    project = ProjectInput(
        number=parse_project_number("2026-4995"),
        reference_number=parse_project_number("2024-4493"),
    )
    service = FicheService(today=lambda: date(2026, 5, 6))

    service.fill_fiche(project_dir, project)

    after = _parts(fiche_path)
    relations_part = "xl/worksheets/_rels/sheet1.xml.rels"
    assert list(after) == list(before)
    assert {name for name in before if after[name] != before[name]} == {
        SHEET_PART,
        relations_part,
        "xl/styles.xml",
    }
    sheet = after[SHEET_PART].decode()
    assert sheet.index("<hyperlinks>") < sheet.index("<drawing ")
    relations = after[relations_part].decode()
    assert 'Id="rId1"' in relations
    assert "../drawings/drawing1.xml" in relations
    assert 'Id="rId2"' in relations
    assert 'Target="projectflow://dossier/2024-4493" TargetMode="External"' in relations
    workbook = load_workbook(fiche_path)
    worksheet = workbook["Fiche"]
    assert worksheet["A4"].value == "Réf. : 2024-4493"
    assert worksheet["A4"].hyperlink.target == "projectflow://dossier/2024-4493"
    assert worksheet["A4"].font.underline == "single"
    assert worksheet["D3"].value == "Societe : Balz"
    assert worksheet["D3"].font.bold
    assert worksheet["D3"].font.underline is None
    assert worksheet["F12"].font.italic
    assert [str(merged) for merged in worksheet.merged_cells.ranges] == ["A11:E11"]
    workbook.close()

    # Saving the same reference again has nothing to write.
    linked = fiche_path.read_bytes()
    service.fill_fiche(project_dir, project)
    service.fill_fiche(project_dir, ProjectInput(number=parse_project_number("2026-4995")))
    assert fiche_path.read_bytes() == linked
