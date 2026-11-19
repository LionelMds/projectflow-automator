from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from projectflow.core.dossier_link import parse_dossier_link
from projectflow.core.fiche_service import FicheService, standard_fiche_path
from projectflow.core.models import ProjectInput
from projectflow.core.numero import parse_project_number


def test_fill_fiche_prefers_fiche_candidate_and_renames_to_standard(tmp_path: Path) -> None:
    project_dir = tmp_path
    workbook = Workbook()
    path = project_dir / "modele fiche.xlsx"
    workbook.save(path)
    workbook.close()
    project = ProjectInput(
        number=parse_project_number("2026-4995"),
        designation="Escalier",
        societe="Balz",
        contact="Lionel",
        localisation="Zurich",
        gere_par="Claire Martin",
    )

    fiche_path = FicheService(today=lambda: date(2026, 5, 6)).fill_fiche(
        project_dir,
        project,
        user_initials=" LM ",
    )

    assert fiche_path == standard_fiche_path(project_dir, project.number)
    loaded = FicheService().read_fiche(fiche_path)
    assert loaded.number == "2026-4995"
    assert loaded.societe == "Balz"
    assert loaded.contact == "Lionel"
    assert loaded.designation == "Escalier"
    assert loaded.localisation == "Zurich"
    assert loaded.gere_par == "Claire Martin"
    assert loaded.user_initials == "LM"
    workbook = load_workbook(fiche_path)
    assert workbook.active["B9"].value.date() == date(2026, 5, 6)
    assert workbook.active["B9"].number_format == "DD.MM.YYYY"
    assert workbook.active["C6"].value == "Claire Martin"
    assert workbook.active["C9"].value == "LM"
    assert workbook.active["E2"].value is None
    workbook.close()


def test_locate_fiche_finds_numbered_subfolder(tmp_path: Path) -> None:
    project_dir = tmp_path / "2026-5093"
    nested_dir = project_dir / "2026-5093"
    nested_dir.mkdir(parents=True)
    fiche_path = nested_dir / "2026-5093 - Fiche dossier clients.xlsx"
    workbook = Workbook()
    workbook.save(fiche_path)
    workbook.close()

    located = FicheService().locate_fiche(project_dir, parse_project_number("2026-5093"))

    assert located == fiche_path


def test_locate_fiche_finds_subproject_subfolder(tmp_path: Path) -> None:
    project_dir = tmp_path / "2026-5093"
    nested_dir = project_dir / "2026-5093-2"
    nested_dir.mkdir(parents=True)
    fiche_path = nested_dir / "2026-5093-2 - Fiche dossier clients.xlsx"
    workbook = Workbook()
    workbook.save(fiche_path)
    workbook.close()

    located = FicheService().locate_fiche(project_dir, parse_project_number("2026-5093-2"))

    assert located == fiche_path


def test_fill_fiche_does_not_touch_existing_creation_date(tmp_path: Path) -> None:
    path = tmp_path / "modele fiche.xlsx"
    workbook = Workbook()
    workbook.active["B9"] = date(2025, 1, 2)
    workbook.save(path)
    workbook.close()
    project = ProjectInput(number=parse_project_number("2026-4995"))

    fiche_path = FicheService(today=lambda: date(2026, 5, 6)).fill_fiche(tmp_path, project)

    loaded = load_workbook(fiche_path)
    assert loaded.active["B9"].value.date() == date(2025, 1, 2)
    loaded.close()


def test_fill_fiche_keeps_existing_atelier_date(tmp_path: Path) -> None:
    path = tmp_path / "modele fiche.xlsx"
    workbook = Workbook()
    workbook.active["E2"] = "fiche d'atelier le 02.01.2025"
    workbook.save(path)
    workbook.close()
    project = ProjectInput(number=parse_project_number("2026-4995"))

    fiche_path = FicheService(today=lambda: date(2026, 5, 6)).fill_fiche(tmp_path, project)

    loaded = load_workbook(fiche_path)
    assert loaded.active["E2"].value == "fiche d'atelier le 02.01.2025"
    loaded.close()


@pytest.mark.parametrize(
    "initial_value",
    [None, "fiche d'atelier le", "fiche d'atelier le 01.02.2020"],
)
def test_ensure_atelier_date_dates_output_without_overwriting_other_fields(
    tmp_path: Path,
    initial_value: str | None,
) -> None:
    path = tmp_path / "fiche.xlsx"
    workbook = Workbook()
    workbook.active["D3"] = "Societe : Information conservee"
    workbook.active["E2"] = initial_value
    workbook.active["B9"] = date(2024, 3, 4)
    workbook.save(path)
    workbook.close()

    FicheService(today=lambda: date(2026, 7, 15)).ensure_atelier_date(path)

    loaded = load_workbook(path)
    assert loaded.active["D3"].value == "Societe : Information conservee"
    assert loaded.active["B9"].value.date() == date(2024, 3, 4)
    assert loaded.active["E2"].value == "fiche d'atelier le 15.07.2026"
    loaded.close()


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("fiche d'atelier le 01.02.2020", "fiche d'atelier le"),
        ("Fiche d\u2019atelier le : 01.02.2020", "Fiche d\u2019atelier le :"),
        ("Texte libre 01.02.2020", "Texte libre 01.02.2020"),
    ],
)
def test_new_fiche_discards_only_recognized_inherited_output_date(
    tmp_path: Path,
    original: str,
    expected: str,
) -> None:
    template_path = tmp_path / "modele fiche.xlsx"
    workbook = Workbook()
    workbook.active["E2"] = original
    workbook.save(template_path)
    workbook.close()

    fiche_path = FicheService().fill_fiche(
        tmp_path,
        ProjectInput(number=parse_project_number("2026-4995")),
        new_fiche=True,
    )

    workbook = load_workbook(fiche_path)
    assert workbook.active["E2"].value == expected
    workbook.close()


def test_read_fiche_strips_prefixes_case_insensitively(tmp_path: Path) -> None:
    path = tmp_path / "fiche.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet["C3"] = "2026-4995"
    worksheet["D3"] = "société : Balz"
    worksheet["D4"] = "CONTACT : Lionel"
    worksheet["D5"] = "Projet : Escalier"
    worksheet["D6"] = "Localisation : Zurich"
    worksheet["C6"] = "Claire Martin"
    worksheet["C9"] = "LM"
    workbook.save(path)
    workbook.close()

    loaded = FicheService().read_fiche(path)

    assert loaded.societe == "Balz"
    assert loaded.contact == "Lionel"
    assert loaded.gere_par == "Claire Martin"
    assert loaded.user_initials == "LM"


@pytest.mark.parametrize("manager", ["Claire Martin", None])
def test_read_fiche_never_uses_user_initials_as_project_manager(
    tmp_path: Path,
    manager: str | None,
) -> None:
    path = tmp_path / "fiche.xlsx"
    workbook = Workbook()
    workbook.active["C6"] = manager
    workbook.active["C9"] = "LM"
    workbook.save(path)
    workbook.close()

    loaded = FicheService().read_fiche(path)

    assert loaded.gere_par == (manager or "")
    assert loaded.user_initials == "LM"


def test_standardize_fiche_name_renames_selected_file(tmp_path: Path) -> None:
    source = tmp_path / "ancienne fiche.xlsx"
    workbook = Workbook()
    workbook.save(source)
    workbook.close()

    renamed = FicheService().standardize_fiche_name(
        tmp_path,
        parse_project_number("2026-4995"),
        fiche_path=source,
    )

    assert renamed == tmp_path / "2026-4995 - Fiche dossier clients.xlsx"
    assert renamed.exists()
    assert not source.exists()


def test_fill_subproject_updates_existing_nested_fiche(tmp_path: Path) -> None:
    project_dir = tmp_path / "2026-5093"
    nested_dir = project_dir / "2026-5093-2"
    nested_dir.mkdir(parents=True)
    fiche_path = nested_dir / "2026-5093-2 - Fiche dossier clients.xlsx"
    workbook = Workbook()
    workbook.active["D5"] = "Projet : Ancien"
    workbook.save(fiche_path)
    workbook.close()
    project = ProjectInput(
        number=parse_project_number("2026-5093-2"),
        designation="Sous-projet charge",
        gere_par="Alex Bernard",
    )

    updated_path = FicheService(today=lambda: date(2026, 5, 6)).fill_subproject_fiche(
        project_dir,
        project,
        user_initials="AB",
    )

    assert updated_path == fiche_path
    assert not (project_dir / "2026-5093-2 - Fiche dossier clients.xlsx").exists()
    loaded = FicheService().read_fiche(fiche_path)
    assert loaded.number == "2026-5093-2"
    assert loaded.designation == "Sous-projet charge"
    assert loaded.gere_par == "Alex Bernard"
    assert loaded.user_initials == "AB"
    workbook = load_workbook(fiche_path)
    assert workbook.active["B9"].value.date() == date(2026, 5, 6)
    assert workbook.active["C6"].value == "Alex Bernard"
    assert workbook.active["C9"].value == "AB"
    assert workbook.active["E2"].value is None
    workbook.close()


@pytest.mark.parametrize("number", ["2026-4995", "2026-4995-2"])
@pytest.mark.parametrize(
    ("manager", "initials", "expected_manager", "expected_initials"),
    [
        ("", "LM", "Previous manager", "LM"),
        ("Claire Martin", "", "Claire Martin", "OLD"),
        ("  ", "  ", "Previous manager", "OLD"),
    ],
)
def test_fiche_preserves_each_identity_cell_when_its_input_is_empty(
    tmp_path: Path,
    *,
    number: str,
    manager: str,
    initials: str,
    expected_manager: str,
    expected_initials: str,
) -> None:
    path = tmp_path / f"{number} - Fiche dossier clients.xlsx"
    workbook = Workbook()
    workbook.active["C6"] = "Previous manager"
    workbook.active["C9"] = "OLD"
    workbook.active["B9"] = date(2020, 2, 1)
    workbook.active["E2"] = "fiche d'atelier le 03.04.2021"
    workbook.save(path)
    workbook.close()

    FicheService().fill_subproject_fiche(
        tmp_path,
        ProjectInput(number=parse_project_number(number), gere_par=manager),
        user_initials=initials,
    )

    workbook = load_workbook(path)
    assert workbook.active["C6"].value == expected_manager
    assert workbook.active["C9"].value == expected_initials
    assert workbook.active["B9"].value.date() == date(2020, 2, 1)
    assert workbook.active["E2"].value == "fiche d'atelier le 03.04.2021"
    workbook.close()


def test_fill_fiche_releases_file_handle_for_move(tmp_path: Path) -> None:
    source = tmp_path / "modele fiche.xlsx"
    workbook = Workbook()
    workbook.save(source)
    workbook.close()
    project = ProjectInput(number=parse_project_number("2026-4995"))

    fiche_path = FicheService().fill_fiche(tmp_path, project)
    moved_path = tmp_path / "fiche deplacee.xlsx"

    fiche_path.rename(moved_path)

    assert moved_path.exists()


def test_read_fiche_releases_file_handle_for_move(tmp_path: Path) -> None:
    fiche_path = tmp_path / "fiche.xlsx"
    workbook = Workbook()
    workbook.save(fiche_path)
    workbook.close()

    FicheService().read_fiche(fiche_path)
    moved_path = tmp_path / "fiche deplacee.xlsx"

    fiche_path.rename(moved_path)

    assert moved_path.exists()


def _blank_fiche(path: Path, **cells: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    for coordinate, value in cells.items():
        workbook.active[coordinate] = value
    workbook.save(path)
    workbook.close()


def test_fill_fiche_writes_reference_in_a4_with_a_projectflow_link(tmp_path: Path) -> None:
    project_dir = tmp_path / "Clients" / "2026" / "2026-5271"
    _blank_fiche(project_dir / "modele fiche.xlsx")
    project = ProjectInput(
        number=parse_project_number("2026-5271"),
        reference_number=parse_project_number("2024-4493"),
    )

    fiche_path = FicheService().fill_fiche(project_dir, project)

    workbook = load_workbook(fiche_path)
    cell = workbook.active["A4"]
    assert cell.value == "Réf. : 2024-4493"
    # Names the dossier, not a path: each poste opens its own copy of the folder.
    assert cell.hyperlink.target == "projectflow://dossier/2024-4493"
    assert cell.hyperlink.tooltip == "Ouvrir le dossier 2024-4493"
    assert cell.hyperlink.ref == "A4"
    assert cell.font.underline == "single"
    workbook.close()
    assert parse_dossier_link(cell.hyperlink.target) == parse_project_number("2024-4493")
    assert FicheService().read_fiche(fiche_path).reference == "2024-4493"


def test_fill_fiche_links_a_subproject_reference_by_its_full_number(tmp_path: Path) -> None:
    project_dir = tmp_path / "Clients" / "2026" / "2026-5271"
    _blank_fiche(project_dir / "modele fiche.xlsx")
    project = ProjectInput(
        number=parse_project_number("2026-5271"),
        reference_number=parse_project_number("2024-4493-2"),
    )

    fiche_path = FicheService().fill_fiche(project_dir, project)

    workbook = load_workbook(fiche_path)
    assert workbook.active["A4"].value == "Réf. : 2024-4493-2"
    assert workbook.active["A4"].hyperlink.target == "projectflow://dossier/2024-4493-2"
    assert workbook.active["A4"].hyperlink.tooltip == "Ouvrir le dossier 2024-4493"
    workbook.close()


def test_fill_fiche_without_reference_keeps_the_existing_one(tmp_path: Path) -> None:
    project_dir = tmp_path / "Clients" / "2026" / "2026-5271"
    _blank_fiche(project_dir / "modele fiche.xlsx")
    number = parse_project_number("2026-5271")
    service = FicheService()
    service.fill_fiche(
        project_dir,
        ProjectInput(number=number, reference_number=parse_project_number("2024-4493")),
    )

    fiche_path = service.fill_fiche(project_dir, ProjectInput(number=number, designation="Paroi"))

    workbook = load_workbook(fiche_path)
    assert workbook.active["A4"].value == "Réf. : 2024-4493"
    assert workbook.active["A4"].hyperlink.target == "projectflow://dossier/2024-4493"
    workbook.close()


@pytest.mark.parametrize("content", [None, "Voir plan atelier"])
def test_fill_fiche_without_reference_leaves_a4_alone(tmp_path: Path, content: str | None) -> None:
    _blank_fiche(tmp_path / "modele fiche.xlsx", A4=content)

    fiche_path = FicheService().fill_fiche(
        tmp_path,
        ProjectInput(number=parse_project_number("2026-5271")),
    )

    workbook = load_workbook(fiche_path)
    assert workbook.active["A4"].value == content
    assert workbook.active["A4"].hyperlink is None
    workbook.close()
    assert FicheService().read_fiche(fiche_path).reference == ""


def test_relative_link_of_an_older_version_becomes_a_projectflow_link(tmp_path: Path) -> None:
    # Version 0.1.82 wrote a path relative to the fiche, which Excel resolves against
    # the web address of a synchronised workbook.
    project_dir = tmp_path / "Clients" / "2026" / "2026-5271"
    number = parse_project_number("2026-5271")
    fiche_path = standard_fiche_path(project_dir, number)
    _blank_fiche(fiche_path, A4="Réf. : 2024-4493")
    workbook = load_workbook(fiche_path)
    workbook.active["A4"].hyperlink = "../../2024/2024-4493"
    workbook.save(fiche_path)
    workbook.close()

    FicheService().fill_fiche(project_dir, ProjectInput(number=number, designation="Paroi"))

    workbook = load_workbook(fiche_path)
    assert workbook.active["A4"].value == "Réf. : 2024-4493"
    assert workbook.active["A4"].hyperlink.target == "projectflow://dossier/2024-4493"
    workbook.close()


def test_subproject_fiche_copied_from_its_parent_keeps_the_reference_link(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "Clients" / "2026" / "2026-5271"
    parent = parse_project_number("2026-5271")
    _blank_fiche(standard_fiche_path(project_dir, parent))
    service = FicheService()
    service.fill_fiche(
        project_dir,
        ProjectInput(number=parent, reference_number=parse_project_number("2024-4493")),
    )
    (project_dir / "2026-5271-2").mkdir()

    fiche_path = service.fill_subproject_fiche(
        project_dir,
        ProjectInput(number=parse_project_number("2026-5271-2")),
    )

    assert fiche_path.parent == project_dir / "2026-5271-2"
    workbook = load_workbook(fiche_path)
    assert workbook.active["A4"].value == "Réf. : 2024-4493"
    assert workbook.active["A4"].hyperlink.target == "projectflow://dossier/2024-4493"
    workbook.close()


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("Réf. : 2024-4493", "2024-4493"),
        ("référence 2024 - 4493", "2024-4493"),
        ("REF: 2024-4493-2", "2024-4493-2"),
        ("2024-4493", "2024-4493"),
        ("Réf. : à définir", ""),
        ("Référence :", ""),
    ],
)
def test_read_fiche_reads_the_reference_number(tmp_path: Path, content: str, expected: str) -> None:
    path = tmp_path / "fiche.xlsx"
    _blank_fiche(path, A4=content)

    assert FicheService().read_fiche(path).reference == expected
