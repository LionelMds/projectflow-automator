from __future__ import annotations

from typing import Any

from projectflow.core.numero import ProjectNumber
from projectflow.exceptions import ProjectCreationError

PROJECT_NUMBER_COLUMN = 0
PROJECT_INFO_COLUMNS = (1, 2, 3, 4)


def find_project_row(rows: list[list[Any]], number: ProjectNumber) -> int | None:
    target = str(number)
    for index, row in enumerate(rows):
        if _cell_as_text(row, PROJECT_NUMBER_COLUMN) == target:
            return index
    return None


def assert_description_empty(row: list[Any], *, force_overwrite: bool) -> None:
    if force_overwrite:
        return
    if not project_info_columns_empty(row):
        raise ProjectCreationError(
            "Les colonnes B a E du repertoire contiennent deja des informations. "
            "Utilisez 'Mettre a jour' pour remplacer.",
        )


def project_info_columns_empty(row: list[Any]) -> bool:
    return all(_cell_as_text(row, column) == "" for column in PROJECT_INFO_COLUMNS)


def prepare_subproject_row(
    rows: list[list[Any]],
    subproject_number: ProjectNumber,
) -> tuple[int, list[Any]]:
    if not subproject_number.is_subproject:
        raise ProjectCreationError("La duplication de ligne exige un numero de sous-projet.")

    parent_number = subproject_number.parent
    parent_index = find_project_row(rows, parent_number)
    if parent_index is None:
        raise ProjectCreationError(f"Projet parent introuvable dans le repertoire: {parent_number}")

    insert_after = parent_index
    prefix = f"{parent_number}-"
    for index in range(parent_index + 1, len(rows)):
        cell = _cell_as_text(rows[index], PROJECT_NUMBER_COLUMN)
        if cell == str(parent_number) or cell.startswith(prefix):
            insert_after = index
            continue
        break

    width = max(len(rows[parent_index]), 5)
    new_row = [""] * width
    new_row[PROJECT_NUMBER_COLUMN] = str(subproject_number)
    return insert_after + 1, new_row


def _cell_as_text(row: list[Any], column: int) -> str:
    if len(row) <= column:
        return ""
    value = row[column]
    return "" if value is None else str(value).strip()
