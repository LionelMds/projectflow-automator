from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final, cast

from openpyxl import load_workbook
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from projectflow.core.models import ProjectInput
from projectflow.core.numero import ProjectNumber
from projectflow.exceptions import ProjectCreationError

FICHE_SUFFIX: Final[str] = " - Fiche dossier clients.xlsx"
PREFIX_RE = re.compile(
    "^\\s*(?P<label>societe|soci\u00e9t\u00e9|contact|projet|localisation)\\s*:\\s*",
    re.IGNORECASE,
)
ATELIER_PREFIX_RE = re.compile(
    r"^\s*fiche\s+d['\u2019]atelier\s+le\s*:?[\s]*$",
    re.IGNORECASE,
)
ATELIER_DATE_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\s*$")


@dataclass(frozen=True, slots=True)
class FicheData:
    number: str = ""
    societe: str = ""
    contact: str = ""
    designation: str = ""
    localisation: str = ""
    gere_par: str = ""


@dataclass(frozen=True, slots=True)
class FicheCandidate:
    path: Path
    size_bytes: int
    modified_timestamp: float


class FicheService:
    def __init__(self, today: Callable[[], date] = date.today) -> None:
        self._today = today

    def list_candidates(
        self,
        project_dir: Path,
        number: ProjectNumber | None = None,
    ) -> list[FicheCandidate]:
        candidates: list[FicheCandidate] = []
        for search_dir in _fiche_search_dirs(project_dir, number):
            if not search_dir.is_dir():
                continue
            for path in search_dir.glob("*.xlsx"):
                if path.name.startswith("~$"):
                    continue
                stat = path.stat()
                candidates.append(
                    FicheCandidate(
                        path=path,
                        size_bytes=stat.st_size,
                        modified_timestamp=stat.st_mtime,
                    ),
                )
        return sorted(candidates, key=lambda candidate: _candidate_sort_key(candidate, number))

    def locate_fiche(self, project_dir: Path, number: ProjectNumber | None = None) -> Path:
        candidates = self.list_candidates(project_dir, number)
        if not candidates:
            suffix = f" pour {number}" if number is not None else ""
            raise ProjectCreationError(f"Aucune fiche Excel trouvee{suffix} dans {project_dir}")
        return candidates[0].path

    def standardize_fiche_name(
        self,
        project_dir: Path,
        number: ProjectNumber,
        *,
        fiche_path: Path | None = None,
    ) -> Path:
        source_path = fiche_path or self.locate_fiche(project_dir, number)
        standard_path = _standard_fiche_path_in(source_path.parent, number)
        if source_path == standard_path:
            return source_path
        if standard_path.exists():
            return standard_path
        source_path.rename(standard_path)
        return standard_path

    def fill_fiche(self, project_dir: Path, project: ProjectInput) -> Path:
        fiche_path = self.standardize_fiche_name(project_dir, project.number)

        workbook = load_workbook(fiche_path)
        try:
            worksheet = _active_worksheet(workbook)
            worksheet["C3"] = str(project.number)
            _write_prefixed(worksheet, "D3", "Societe", project.societe)
            _write_prefixed(worksheet, "D4", "Contact", project.contact)
            _write_prefixed(worksheet, "D5", "Projet", project.designation)
            _write_prefixed(worksheet, "D6", "Localisation", project.localisation)
            if project.gere_par.strip():
                worksheet["C9"] = project.gere_par.strip()
            self._write_creation_date(worksheet)
            workbook.save(fiche_path)
        finally:
            workbook.close()
        return fiche_path

    def ensure_atelier_date(self, fiche_path: Path) -> Path:
        """Complete E2 without changing the rest of an existing fiche."""
        workbook = load_workbook(fiche_path)
        try:
            worksheet = _active_worksheet(workbook)
            current = "" if worksheet["E2"].value is None else str(worksheet["E2"].value).strip()
            if not ATELIER_DATE_RE.search(current):
                self._write_atelier_date(worksheet)
                workbook.save(fiche_path)
        finally:
            workbook.close()
        return fiche_path

    def fill_subproject_fiche(self, project_dir: Path, project: ProjectInput) -> Path:
        if not project.number.is_subproject:
            return self.fill_fiche(project_dir, project)

        target_path = _preferred_standard_fiche_path(project_dir, project.number)
        fiche_created = not target_path.exists()
        if fiche_created:
            parent_path = _existing_standard_fiche_path(project_dir, project.number.parent)
            source_path = (
                parent_path
                if parent_path is not None
                else self.locate_fiche(project_dir, project.number.parent)
            )
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(source_path.read_bytes())

        workbook = load_workbook(target_path)
        try:
            worksheet = _active_worksheet(workbook)
            worksheet["C3"] = str(project.number)
            _write_prefixed(worksheet, "D3", "Societe", project.societe)
            _write_prefixed(worksheet, "D4", "Contact", project.contact)
            _write_prefixed(worksheet, "D5", "Projet", project.designation)
            _write_prefixed(worksheet, "D6", "Localisation", project.localisation)
            if project.gere_par.strip():
                worksheet["C9"] = project.gere_par.strip()
            self._write_creation_date(worksheet, overwrite=fiche_created)
            workbook.save(target_path)
        finally:
            workbook.close()
        return target_path

    def read_fiche(self, fiche_path: Path) -> FicheData:
        workbook = load_workbook(fiche_path, read_only=True, data_only=True)
        try:
            worksheet = _active_worksheet(workbook)
            return FicheData(
                number=_cell_text(worksheet["C3"].value),
                societe=_strip_prefix(_cell_text(worksheet["D3"].value)),
                contact=_strip_prefix(_cell_text(worksheet["D4"].value)),
                designation=_strip_prefix(_cell_text(worksheet["D5"].value)),
                localisation=_strip_prefix(_cell_text(worksheet["D6"].value)),
                gere_par=_cell_text(worksheet["C9"].value) or _cell_text(worksheet["C6"].value),
            )
        finally:
            workbook.close()

    def _write_creation_date(self, worksheet: Worksheet, *, overwrite: bool = False) -> None:
        cell = worksheet["B9"]
        if cell.value is not None and not overwrite:
            return
        cell.value = self._today()
        cell.number_format = "DD.MM.YYYY"

    def _write_atelier_date(self, worksheet: Worksheet) -> None:
        cell = worksheet["E2"]
        current = "" if cell.value is None else str(cell.value).strip()
        if ATELIER_DATE_RE.search(current):
            return
        today = self._today().strftime("%d.%m.%Y")
        if not current or ATELIER_PREFIX_RE.fullmatch(current):
            cell.value = f"fiche d'atelier le {today}"
            return
        cell.value = f"{current.rstrip()} {today}"


def standard_fiche_path(project_dir: Path, number: ProjectNumber) -> Path:
    return _standard_fiche_path_in(project_dir, number)


def _standard_fiche_path_in(directory: Path, number: ProjectNumber) -> Path:
    return directory / f"{number}{FICHE_SUFFIX}"


def _standard_fiche_paths(project_dir: Path, number: ProjectNumber) -> list[Path]:
    paths = [standard_fiche_path(project_dir, number)]
    nested_path = _standard_fiche_path_in(project_dir / str(number), number)
    if nested_path not in paths:
        paths.append(nested_path)
    return paths


def _existing_standard_fiche_path(project_dir: Path, number: ProjectNumber) -> Path | None:
    for path in _standard_fiche_paths(project_dir, number):
        if path.exists():
            return path
    return None


def _preferred_standard_fiche_path(project_dir: Path, number: ProjectNumber) -> Path:
    existing_path = _existing_standard_fiche_path(project_dir, number)
    if existing_path is not None:
        return existing_path
    nested_dir = project_dir / str(number)
    if nested_dir.is_dir():
        return _standard_fiche_path_in(nested_dir, number)
    return standard_fiche_path(project_dir, number)


def _fiche_search_dirs(project_dir: Path, number: ProjectNumber | None) -> list[Path]:
    directories = [project_dir]
    if number is not None:
        directories.append(project_dir / str(number))

    result: list[Path] = []
    for directory in directories:
        if directory not in result:
            result.append(directory)
    return result


def _active_worksheet(workbook: Workbook) -> Worksheet:
    return cast("Worksheet", workbook.active)


def _candidate_sort_key(
    candidate: FicheCandidate,
    number: ProjectNumber | None,
) -> tuple[int, int, str]:
    exact_name_rank = 1
    if number is not None and candidate.path.name == f"{number}{FICHE_SUFFIX}":
        exact_name_rank = 0
    contains_fiche = "fiche" in candidate.path.name.lower()
    return (exact_name_rank, 0 if contains_fiche else 1, candidate.path.name.lower())


def _write_prefixed(workbook: Worksheet, cell: str, prefix: str, value: str) -> None:
    if value.strip():
        workbook[cell] = f"{prefix} : {value.strip()}"


def _cell_text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _strip_prefix(value: str) -> str:
    return PREFIX_RE.sub("", value).strip()
