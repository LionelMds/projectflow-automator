from __future__ import annotations

import re
from dataclasses import dataclass

from projectflow.exceptions import ProjectNumberError

_MAIN_PROJECT_RE = re.compile(r"^(?P<year>\d{4})-(?P<project_id>\d{3,})$")
_PROJECT_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<project_id>\d{3,})"
    r"(?:-(?P<subproject_id>[A-Z0-9]+(?:-[A-Z0-9]+)*))?$",
)
_DASHES = str.maketrans(dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-"))
_LOOSE_RE = re.compile(
    r"^(?P<year>\d{4})\s*-\s*(?P<project_id>\d{3,})(?:\s*[-_/.\s]\s*(?P<rest>.+))?$"
)
_SUFFIX_PARTS_RE = re.compile(r"[^A-Za-z0-9]+")


def canonical_project_number(value: object) -> str:
    """Return the number as written in the répertoire, whatever was typed around it.

    « 2024 - 4493 a1 » (or with a typographic dash), « 2026-5102 REV03 » or
    « 2026-5102_rev03 » read as ``2024-4493-A1`` and ``2026-5102-REV03``: dashes, spaces and
    underscores become one dash, letters are capitalised and a numeric suffix
    loses its leading zeros (« -02 » is « -2 »). Text that is not a number at all is
    only stripped.
    """
    text = " ".join(str(value or "").translate(_DASHES).split())
    match = _LOOSE_RE.fullmatch(text)
    if match is None:
        return text
    number = f"{match['year']}-{match['project_id']}"
    rest = match["rest"]
    if not rest:
        return number
    parts = [part.upper() for part in _SUFFIX_PARTS_RE.split(rest) if part]
    if not parts:
        return number
    if len(parts) == 1 and parts[0].isdigit():
        parts = [str(int(parts[0]))]
    return f"{number}-{'-'.join(parts)}"


def is_project_number(value: object) -> bool:
    """Whether ``value`` is a dossier number, its suffix free (« -2 », « -A1 », « -REV03 »)."""
    return _PROJECT_RE.fullmatch(canonical_project_number(value)) is not None


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
        sub = self.subproject_id
        if sub and not sub.isdigit():
            # « A1 », « REV03 »: written by hand in the répertoire, never created here.
            return f"{format_project_number(self.year, self.project_id)}-{sub}"
        return format_project_number(self.year, self.project_id, sub)


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
    raw_value = canonical_project_number(value)
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
