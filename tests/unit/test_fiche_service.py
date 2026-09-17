from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

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
        gere_par="LM",
    )

    fiche_path = FicheService(today=lambda: date(2026, 5, 6)).fill_fiche(project_dir, project)

    assert fiche_path == standard_fiche_path(project_dir, project.number)
    loaded = FicheService().read_fiche(fiche_path)
    assert loaded.number == "2026-4995"
    assert loaded.societe == "Balz"
    assert loaded.contact == "Lionel"
    assert loaded.designation == "Escalier"
    assert loaded.localisation == "Zurich"
    assert loaded.gere_par == "LM"
    workbook = load_workbook(fiche_path)
    assert workbook.active["B9"].value.date() == date(2026, 5, 6)
    assert workbook.active["B9"].number_format == "DD.MM.YYYY"
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
    worksheet["C9"] = "LM"
    workbook.save(path)
    workbook.close()

    loaded = FicheService().read_fiche(path)

    assert loaded.societe == "Balz"
    assert loaded.contact == "Lionel"
    assert loaded.gere_par == "LM"


def test_read_fiche_supports_legacy_gere_par_cell(tmp_path: Path) -> None:
    path = tmp_path / "fiche.xlsx"
    workbook = Workbook()
    workbook.active["C6"] = "LM"
    workbook.save(path)
    workbook.close()

    loaded = FicheService().read_fiche(path)

    assert loaded.gere_par == "LM"


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
        gere_par="AB",
    )

    updated_path = FicheService(today=lambda: date(2026, 5, 6)).fill_subproject_fiche(
        project_dir,
        project,
    )

    assert updated_path == fiche_path
    assert not (project_dir / "2026-5093-2 - Fiche dossier clients.xlsx").exists()
    loaded = FicheService().read_fiche(fiche_path)
    assert loaded.number == "2026-5093-2"
    assert loaded.designation == "Sous-projet charge"
    workbook = load_workbook(fiche_path)
    assert workbook.active["B9"].value.date() == date(2026, 5, 6)
    assert workbook.active["C9"].value == "AB"
    assert workbook.active["E2"].value is None
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
