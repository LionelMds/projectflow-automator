from __future__ import annotations

import re
from dataclasses import dataclass

from projectflow.exceptions import ProjectNumberError

_MAIN_PROJECT_RE = re.compile(r"^(?P<year>\d{4})-(?P<project_id>\d{3,})$")
_PROJECT_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<project_id>\d{3,})(?:-(?P<subproject_id>\d+))?$",
)


@dataclass(frozen=True, slots=True)
class ProjectNumber:
    year: int
    project_id: str
    subproject_id: str | None = None

    @property
    def is_subproject(self) -> bool:
        return self.subproject_id is not None

    @property
    def parent(self) -> ProjectNumber:
        return ProjectNumber(year=self.year, project_id=self.project_id)

    def __str__(self) -> str:
        return format_project_number(self.year, self.project_id, self.subproject_id)


def validate_year(year: int | str) -> int:
    raw_year = str(year).strip()
    if not re.fullmatch(r"\d{4}", raw_year):
        raise ProjectNumberError("L'annee doit contenir exactement 4 chiffres.")
    return int(raw_year)


def validate_project_id(project_id: int | str) -> str:
    raw_project_id = str(project_id).strip()
    if not re.fullmatch(r"\d{3,}", raw_project_id):
        raise ProjectNumberError("L'ID projet doit contenir au moins 3 chiffres.")
    return raw_project_id


def validate_subproject_id(subproject_id: int | str | None) -> str | None:
    if subproject_id is None:
        return None

    raw_subproject_id = str(subproject_id).strip()
    if raw_subproject_id == "":
        return None
    if not re.fullmatch(r"\d+", raw_subproject_id):
        raise ProjectNumberError("Le sous-projet doit contenir uniquement des chiffres.")
    if int(raw_subproject_id) <= 0:
        raise ProjectNumberError("Le sous-projet doit etre superieur a zero.")
    return raw_subproject_id


def format_project_number(
    year: int | str,
    project_id: int | str,
    subproject_id: int | str | None = None,
) -> str:
    valid_year = validate_year(year)
    valid_project_id = validate_project_id(project_id)
    valid_subproject_id = validate_subproject_id(subproject_id)
    if valid_subproject_id is None:
        return f"{valid_year}-{valid_project_id}"
    return f"{valid_year}-{valid_project_id}-{valid_subproject_id}"


def parse_project_number(value: str) -> ProjectNumber:
    raw_value = value.strip()
    match = _PROJECT_RE.fullmatch(raw_value)
    if match is None:
        raise ProjectNumberError(
            "Le numero de projet doit respecter le format YYYY-NNN ou YYYY-NNN-S.",
        )
    return ProjectNumber(
        year=int(match.group("year")),
        project_id=match.group("project_id"),
        subproject_id=match.group("subproject_id"),
    )


def is_main_project_number(value: str) -> bool:
    return _MAIN_PROJECT_RE.fullmatch(value.strip()) is not None


def project_folder_name(number: ProjectNumber | str) -> str:
    parsed = parse_project_number(number) if isinstance(number, str) else number
    return str(parsed.parent)


def next_project_id(numbers: list[str], *, year: int | str) -> str | None:
    valid_year = validate_year(year)
    candidates: list[int] = []
    for number in numbers:
        match = _MAIN_PROJECT_RE.fullmatch(number.strip())
        if match is None:
            continue
        if int(match.group("year")) == valid_year:
            candidates.append(int(match.group("project_id")))

    if not candidates:
        return None
    return str(max(candidates) + 1)
