from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.worksheet.worksheet import Worksheet

from projectflow.core.excel_patch import CellEdits
from projectflow.exceptions import ProjectCreationError

SHEET_PART = "xl/worksheets/sheet1.xml"
STYLES_PART = "xl/styles.xml"
CHAIN_PART = "xl/calcChain.xml"
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _workbook(path: Path) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet["A1"] = "Titre"
    worksheet["A1"].font = Font(bold=True)
    worksheet["C1"] = "Fin de ligne"
    worksheet["A3"] = 12
    worksheet["B3"] = "=A3*2"
    worksheet["A5"] = "Derniere ligne"
    workbook.save(path)
    workbook.close()


def _parts(path: Path) -> dict[str, bytes]:
    with ZipFile(path) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def _rewrite(path: Path, parts: dict[str, bytes]) -> None:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)


def _edit(path: Path, values: dict[str, object]) -> bool:
    workbook = load_workbook(path)
    try:
        worksheet = workbook.active
        edits = CellEdits(worksheet)
        for reference, value in values.items():
            worksheet[reference] = value
        return edits.save(path)
    finally:
        workbook.close()


def _sheet(path: Path) -> Worksheet:
    return load_workbook(path).active


def _cell_order(path: Path) -> list[str]:
    return re.findall(r'<(?:x:)?c r="([A-Z]+[0-9]+)"', _parts(path)[SHEET_PART].decode())


def test_cells_are_added_in_reading_order(tmp_path: Path) -> None:
    path = tmp_path / "classeur.xlsx"
    _workbook(path)

    assert _edit(path, {"B1": "Milieu", "D1": "Apres", "B2": "Nouvelle ligne", "C9": "Bas"})

    assert _cell_order(path) == ["A1", "B1", "C1", "D1", "B2", "A3", "B3", "A5", "C9"]
    worksheet = _sheet(path)
    assert worksheet["A1"].value == "Titre"
    assert worksheet["A1"].font.bold
    assert worksheet["B1"].value == "Milieu"
    assert worksheet["D1"].value == "Apres"
    assert worksheet["B2"].value == "Nouvelle ligne"
    assert worksheet["B3"].value == "=A3*2"
    assert worksheet["C9"].value == "Bas"
    assert worksheet.dimensions == "A1:D9"


def test_first_cell_is_written_into_an_empty_sheet(tmp_path: Path) -> None:
    path = tmp_path / "classeur.xlsx"
    workbook = Workbook()
    workbook.save(path)
    workbook.close()

    assert _edit(path, {"C3": "2026-4995"})

    assert _sheet(path)["C3"].value == "2026-4995"


@pytest.mark.parametrize(
    "text",
    ['Dupont & Fils <SA> "Zurich"', "  espaces gardes  ", "Ligne 1\nLigne 2", "=pas une formule"],
)
def test_text_is_written_literally(tmp_path: Path, text: str) -> None:
    path = tmp_path / "classeur.xlsx"
    _workbook(path)

    _edit(path, {"A1": text})

    worksheet = load_workbook(path, data_only=True).active
    assert worksheet["A1"].value == text
    assert worksheet["A1"].data_type == "s"
    assert worksheet["A1"].font.bold


def test_numbers_booleans_and_cleared_cells_are_written(tmp_path: Path) -> None:
    path = tmp_path / "classeur.xlsx"
    _workbook(path)

    _edit(path, {"A3": 13.5, "C1": None, "D1": True, "E1": 7})

    worksheet = _sheet(path)
    assert worksheet["A3"].value == 13.5
    assert worksheet["C1"].value is None
    assert worksheet["D1"].value is True
    assert worksheet["E1"].value == 7


def test_date_gets_its_number_format_without_touching_other_styles(tmp_path: Path) -> None:
    path = tmp_path / "classeur.xlsx"
    _workbook(path)
    workbook = load_workbook(path)
    edits = CellEdits(workbook.active)
    workbook.active["A1"] = date(2026, 5, 6)
    workbook.active["A1"].number_format = "DD.MM.YYYY"
    workbook.active["B9"] = datetime(2026, 5, 7, 6, 30)  # noqa: DTZ001 - Excel dates are naive
    workbook.active["B9"].number_format = "DD.MM.YYYY"
    edits.save(path)
    workbook.close()

    worksheet = _sheet(path)
    assert worksheet["A1"].value == datetime(2026, 5, 6)  # noqa: DTZ001
    assert worksheet["A1"].number_format == "DD.MM.YYYY"
    assert worksheet["A1"].font.bold
    assert worksheet["B9"].value == datetime(2026, 5, 7, 6, 30)  # noqa: DTZ001
    assert worksheet["B9"].number_format == "DD.MM.YYYY"
    assert not worksheet["B9"].font.bold
    assert worksheet["A5"].number_format == "General"
    assert _parts(path)[STYLES_PART].count(b'formatCode="DD.MM.YYYY"') == 1


def test_number_format_alone_keeps_the_formula_of_the_cell(tmp_path: Path) -> None:
    path = tmp_path / "classeur.xlsx"
    _workbook(path)
    workbook = load_workbook(path)
    edits = CellEdits(workbook.active)
    workbook.active["B3"].number_format = "0.00"
    edits.save(path)
    workbook.close()

    worksheet = _sheet(path)
    assert worksheet["B3"].value == "=A3*2"
    assert worksheet["B3"].number_format == "0.00"


def test_replacing_a_formula_drops_the_calculation_chain(tmp_path: Path) -> None:
    path = tmp_path / "classeur.xlsx"
    _workbook(path)
    parts = _parts(path)
    parts[CHAIN_PART] = f'<calcChain xmlns="{MAIN_NS}"><c r="B3" i="1"/></calcChain>'.encode()
    parts["[Content_Types].xml"] = (
        parts["[Content_Types].xml"]
        .decode()
        .replace(
            "</Types>",
            f'<Override PartName="/{CHAIN_PART}" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.calcChain+xml"/></Types>',
        )
        .encode()
    )
    relations_part = "xl/_rels/workbook.xml.rels"
    parts[relations_part] = (
        parts[relations_part]
        .decode()
        .replace(
            "</Relationships>",
            '<Relationship Id="rId99" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/calcChain" Target="calcChain.xml"/></Relationships>',
        )
        .encode()
    )
    _rewrite(path, parts)

    _edit(path, {"B3": "Texte"})

    after = _parts(path)
    assert CHAIN_PART not in after
    assert b"calcChain" not in after["[Content_Types].xml"]
    assert b"calcChain" not in after[relations_part]
    assert _sheet(path)["B3"].value == "Texte"


def test_calculation_chain_stays_when_no_formula_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "classeur.xlsx"
    _workbook(path)
    parts = _parts(path)
    parts[CHAIN_PART] = f'<calcChain xmlns="{MAIN_NS}"><c r="B3" i="1"/></calcChain>'.encode()
    relations_part = "xl/_rels/workbook.xml.rels"
    parts[relations_part] = (
        parts[relations_part]
        .decode()
        .replace(
            "</Relationships>",
            '<Relationship Id="rId99" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/calcChain" Target="calcChain.xml"/></Relationships>',
        )
        .encode()
    )
    _rewrite(path, parts)

    _edit(path, {"A1": "Autre titre"})

    assert CHAIN_PART in _parts(path)


def test_sheet_written_with_a_namespace_prefix_is_edited(tmp_path: Path) -> None:
    path = tmp_path / "classeur.xlsx"
    _workbook(path)
    parts = _parts(path)
    sheet = parts[SHEET_PART].decode()
    sheet = re.sub(r"<(/?)(?![?!])", r"<\1x:", sheet).replace(
        f'xmlns="{MAIN_NS}"',
        f'xmlns:x="{MAIN_NS}"',
    )
    parts[SHEET_PART] = sheet.encode()
    _rewrite(path, parts)

    _edit(path, {"A1": "Prefixe", "B2": "Ajout"})

    worksheet = _sheet(path)
    assert worksheet["A1"].value == "Prefixe"
    assert worksheet["B2"].value == "Ajout"
    assert worksheet["B3"].value == "=A3*2"
    assert _cell_order(path) == ["A1", "C1", "B2", "A3", "B3", "A5"]


def test_only_the_edited_sheet_changes(tmp_path: Path) -> None:
    path = tmp_path / "classeur.xlsx"
    workbook = Workbook()
    workbook.active["A1"] = "Premiere"
    second = workbook.create_sheet("Seconde")
    second["A1"] = "Avant"
    workbook.active = 1
    workbook.save(path)
    workbook.close()
    before = _parts(path)

    _edit(path, {"A1": "Apres"})

    after = _parts(path)
    assert [name for name in before if after[name] != before[name]] == ["xl/worksheets/sheet2.xml"]
    workbook = load_workbook(path)
    assert workbook["Sheet"]["A1"].value == "Premiere"
    assert workbook["Seconde"]["A1"].value == "Apres"
    workbook.close()


def test_nothing_is_saved_without_edit(tmp_path: Path) -> None:
    path = tmp_path / "classeur.xlsx"
    _workbook(path)
    original = path.read_bytes()

    assert not _edit(path, {"A1": "Titre", "A3": 12})

    assert path.read_bytes() == original


def test_unrecognised_workbook_is_refused_and_left_untouched(tmp_path: Path) -> None:
    path = tmp_path / "classeur.xlsx"
    _workbook(path)
    workbook = load_workbook(path)
    edits = CellEdits(workbook.active)
    workbook.active["A1"] = "Change"
    parts = _parts(path)
    parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(b'name="Sheet"', b'name="Autre"')
    _rewrite(path, parts)
    original = path.read_bytes()

    with pytest.raises(ProjectCreationError, match="structure Excel non reconnue"):
        edits.save(path)

    workbook.close()
    assert path.read_bytes() == original


def test_unsupported_value_is_refused_and_leaves_the_file_untouched(tmp_path: Path) -> None:
    path = tmp_path / "classeur.xlsx"
    _workbook(path)
    original = path.read_bytes()

    with pytest.raises(TypeError, match="non prise en charge"):
        _edit(path, {"A3": float("nan")})

    assert path.read_bytes() == original


@pytest.mark.parametrize("edit", ["font", "hyperlink"])
def test_edit_that_cannot_be_saved_in_place_is_refused(tmp_path: Path, edit: str) -> None:
    path = tmp_path / "classeur.xlsx"
    _workbook(path)
    original = path.read_bytes()
    workbook = load_workbook(path)
    edits = CellEdits(workbook.active)
    if edit == "font":
        workbook.active["A5"].font = Font(italic=True)
    else:
        workbook.active["A5"].hyperlink = "../2025/2025-4493"

    with pytest.raises(NotImplementedError, match="A5"):
        edits.save(path)

    workbook.close()
    assert path.read_bytes() == original
