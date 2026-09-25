from __future__ import annotations

import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from projectflow.core.fiche_service import FicheService
from projectflow.core.numero import ProjectNumber
from projectflow.exceptions import ProjectCreationError
from projectflow.platform.folder_names import find_child_directory

IMAGE_SUFFIXES = frozenset({".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"})
MAX_MISSING_NAMES = 3


@dataclass(frozen=True, slots=True)
class OutputCandidate:
    path: Path
    size_bytes: int
    modified_timestamp: float


@dataclass(frozen=True, slots=True)
class OutputInventory:
    fiches: tuple[OutputCandidate, ...] = ()
    mesure_pdfs: tuple[OutputCandidate, ...] = ()
    photos: tuple[OutputCandidate, ...] = ()
    plans: tuple[OutputCandidate, ...] = ()
    # Existing folders where the file dialogs open, whatever their accents or case.
    photo_directory: Path | None = None
    plan_directory: Path | None = None


@dataclass(frozen=True, slots=True)
class SortieSelection:
    fiche_path: Path
    mesure_pdf_path: Path | None = None
    photo_paths: tuple[Path, ...] = ()
    plan_paths: tuple[Path, ...] = ()

    def validate(self) -> None:
        paths = [self.fiche_path, self.mesure_pdf_path, *self.photo_paths, *self.plan_paths]
        missing = [path for path in paths if path is not None and not path.is_file()]
        if missing:
            names = ", ".join(path.name for path in missing[:MAX_MISSING_NAMES])
            if len(missing) > MAX_MISSING_NAMES:
                names += ", ..."
            raise ProjectCreationError(f"Fichier de sortie introuvable: {names}")


class SortieDossierService:
    def __init__(
        self,
        fiche_service: FicheService,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._fiche_service = fiche_service
        self._now = now

    def discover(self, project_dir: Path, number: ProjectNumber) -> OutputInventory:
        if not project_dir.is_dir():
            raise ProjectCreationError(f"Dossier projet introuvable: {project_dir}")

        search_dirs = _search_directories(project_dir, number)
        fiche_candidates = _unique_candidates(
            OutputCandidate(
                path=candidate.path,
                size_bytes=candidate.size_bytes,
                modified_timestamp=candidate.modified_timestamp,
            )
            for directory in search_dirs
            for candidate in self._fiche_service.list_candidates(directory, number)
        )

        plan_directories = _find_plan_directories(search_dirs)
        plan_paths = _unique_paths(
            path
            for directory in plan_directories
            for path in directory.rglob("*.pdf")
            if path.is_file()
        )
        plan_paths_set = set(plan_paths)

        mesure_paths = _unique_paths(
            path
            for directory in search_dirs
            for path in directory.glob("*.pdf")
            if path.is_file() and path not in plan_paths_set
        )

        photo_directories = _find_photo_directories(search_dirs)
        photo_paths = _unique_paths(
            path
            for directory in photo_directories
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.casefold() in IMAGE_SUFFIXES
        )

        return OutputInventory(
            fiches=tuple(fiche_candidates),
            mesure_pdfs=tuple(_candidate(path) for path in mesure_paths),
            photos=tuple(_candidate(path) for path in photo_paths),
            plans=tuple(_candidate(path) for path in plan_paths),
            photo_directory=photo_directories[0] if photo_directories else None,
            plan_directory=plan_directories[0] if plan_directories else None,
        )

    def create_output_folder(
        self,
        project_dir: Path,
        number: ProjectNumber,
        selection: SortieSelection,
    ) -> Path:
        selection.validate()
        if not project_dir.is_dir():
            raise ProjectCreationError(f"Dossier projet introuvable: {project_dir}")

        output_dir = _new_output_directory(project_dir, number, self._now)
        output_dir.mkdir(parents=True, exist_ok=False)
        copied_fiche = _copy_file(selection.fiche_path, output_dir / "01 - Fiche dossier")
        self._fiche_service.ensure_atelier_date(copied_fiche)
        if selection.mesure_pdf_path is not None:
            _copy_file(selection.mesure_pdf_path, output_dir / "02 - Prise de cote")
        for photo_path in selection.photo_paths:
            _copy_file(photo_path, output_dir / "03 - Photos")
        for plan_path in selection.plan_paths:
            _copy_file(plan_path, output_dir / "04 - Plans")
        return output_dir


def _search_directories(project_dir: Path, number: ProjectNumber) -> list[Path]:
    directories = [project_dir]
    nested = project_dir / str(number)
    if nested.is_dir() and nested not in directories:
        directories.append(nested)
    return directories


def _find_photo_directories(search_dirs: list[Path]) -> list[Path]:
    result: list[Path] = []
    for directory in search_dirs:
        direct = find_child_directory(directory, "photos")
        if direct is not None:
            result.append(direct)
    return _unique_paths(result)


def _find_plan_directories(search_dirs: list[Path]) -> list[Path]:
    result: list[Path] = []
    for directory in search_dirs:
        plans = find_child_directory(directory, "plans")
        if plans is None:
            continue
        execution = find_child_directory(plans, "plan d'execution")
        result.append(execution or plans)
    return _unique_paths(result)


def _candidate(path: Path) -> OutputCandidate:
    stat = path.stat()
    return OutputCandidate(path=path, size_bytes=stat.st_size, modified_timestamp=stat.st_mtime)


def _new_output_directory(
    project_dir: Path,
    number: ProjectNumber,
    now: Callable[[], datetime],
) -> Path:
    parent = project_dir / "Sorties dossier"
    stamp = now().strftime("%Y%m%d-%H%M%S")
    base = parent / f"{number} - Sortie dossier - {stamp}"
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = parent / f"{base.name} ({suffix})"
        suffix += 1
    return candidate


def _copy_file(source: Path, directory: Path) -> Path:
    destination = _copy_destination(source, directory)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def _copy_destination(source: Path, directory: Path) -> Path:
    candidate = directory / source.name
    suffix = 2
    while candidate.exists():
        candidate = directory / f"{source.stem} ({suffix}){source.suffix}"
        suffix += 1
    return candidate


def _unique_candidates(candidates: Iterable[OutputCandidate]) -> list[OutputCandidate]:
    result: list[OutputCandidate] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(OutputCandidate(resolved, candidate.size_bytes, candidate.modified_timestamp))
    return sorted(result, key=lambda value: str(value.path).casefold())


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(resolved)
    return sorted(result, key=lambda value: str(value).casefold())
