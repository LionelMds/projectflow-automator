"""Find the year of a dossier from its number alone.

A number « XXXX » names one dossier among all the years of the répertoire: the
numbers keep growing from one sheet to the next. The year is therefore never asked
for, it is looked up:

1. in the rows of the répertoire sheets already read (the free rows too: they carry
   the numbers waiting for a project);
2. between the first and last numbers of a sheet read, the numbers being consecutive;
3. in the year folders of the projects root (``<racine>/2024/2024-4493``).

Typing the year (« 2025-4493 ») still forces it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from projectflow.core.numero import (
    ProjectNumber,
    canonical_project_number,
    format_project_number,
    parse_project_number,
)
from projectflow.exceptions import ProjectNumberError

_YEAR_DIR_RE = re.compile(r"^(?:19|20)\d{2}$")
_FULL_RE = re.compile(r"^(?P<year>(?:19|20)\d{2})-(?P<rest>\d{3,}.*)$")
_BARE_RE = re.compile(r"^(?P<id>\d{3,})(?:-(?P<sub>[A-Z0-9]+(?:-[A-Z0-9]+)*))?$")

PLACEHOLDER = "20YY-XXXX"
"""How every dossier number field reads before anything is typed."""


@dataclass(frozen=True, slots=True)
class TypedNumber:
    """A number as typed in a field: the year when typed, the rest as is.

    « 5102 », « 5102-2 » (year looked up) or « 2025-5102 » (year forced).
    """

    year: int | None
    project_id: str
    subproject_id: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.project_id


def split_typed_number(text: str) -> TypedNumber:
    """Read « 5102 », « 5102-2 », « 2025-5102 » or « 2025 - 5102 a1 »."""
    clean = canonical_project_number(text)
    if not clean:
        return TypedNumber(None, "")
    full = _FULL_RE.fullmatch(clean)
    if full is not None:
        try:
            number = parse_project_number(clean)
        except ProjectNumberError:
            pass
        else:
            return TypedNumber(number.year, number.project_id, number.subproject_id)
    bare = _BARE_RE.fullmatch(canonical_project_number(f"2000-{clean}")[5:])
    if bare is not None:
        sub = bare["sub"]
        if sub is not None and sub.isdigit():
            sub = str(int(sub))
        return TypedNumber(None, bare["id"], sub)
    return TypedNumber(None, clean)


class ProjectYearIndex:
    """What is known of the numbers of each year, shared by every number field."""

    def __init__(self) -> None:
        self._years: dict[str, int] = {}
        self._ranges: dict[int, tuple[int, int]] = {}
        self._sheet_years: tuple[int, ...] = ()
        self._root: Path | None = None

    def set_projects_root(self, root: Path | None) -> None:
        self._root = root

    def clear(self) -> None:
        """Forget the répertoire read (another classeur): the folders stay looked at."""
        self._years.clear()
        self._ranges.clear()
        self._sheet_years = ()

    @property
    def sheet_years(self) -> tuple[int, ...]:
        """Years of the répertoire sheets, most recent first (empty until listed)."""
        return self._sheet_years

    def set_sheet_years(self, years: Iterable[int]) -> None:
        self._sheet_years = tuple(sorted(set(years), reverse=True))

    def has_read(self, year: int) -> bool:
        return year in self._ranges

    def learn(self, year: int, numbers: Iterable[str]) -> None:
        """Remember the numbers of the sheet of ``year`` (column A of its rows)."""
        ids: list[int] = []
        for text in numbers:
            try:
                number = parse_project_number(text)
            except ProjectNumberError:
                continue
            if number.year != year:
                continue
            self._years[number.project_id] = year
            ids.append(int(number.project_id))
        if ids:
            self._ranges[year] = (min(ids), max(ids))

    def year_of(self, project_id: str) -> int | None:
        """Year of the dossier ``project_id`` (« 5102 »), or None when nothing tells it."""
        project_id = project_id.strip()
        if not project_id.isdigit():
            return None
        known = self._years.get(project_id)
        if known is not None:
            return known
        value = int(project_id)
        for year, (first, last) in self._ranges.items():
            if first <= value <= last:
                return year
        return self._year_from_folders(project_id)

    def resolve(self, text: str) -> ProjectNumber:
        """Return the full number of what was typed; raise when its year is not found."""
        typed = split_typed_number(text)
        if typed.is_empty:
            raise ProjectNumberError("Saisissez un numéro de dossier (20YY-XXXX).")
        year = typed.year if typed.year is not None else self.year_of(typed.project_id)
        if year is None:
            raise ProjectNumberError(
                f"Année du dossier {typed.project_id} introuvable : ce numéro n'est ni dans "
                "le répertoire ni sous la racine projets. Tapez-le avec son année "
                f"(20YY-{typed.project_id}).",
            )
        sub = typed.subproject_id
        if sub and not sub.isdigit():
            return parse_project_number(f"{year}-{typed.project_id}-{sub}")
        return parse_project_number(format_project_number(year, typed.project_id, sub))

    def _year_from_folders(self, project_id: str) -> int | None:
        root = self._root
        if root is None:
            return None
        try:
            year_dirs = sorted(
                (entry for entry in root.iterdir() if _YEAR_DIR_RE.fullmatch(entry.name)),
                key=lambda entry: entry.name,
                reverse=True,
            )
        except OSError:
            return None
        for year_dir in year_dirs:
            if (year_dir / f"{year_dir.name}-{project_id}").is_dir():
                year = int(year_dir.name)
                self._years[project_id] = year
                return year
        return None
