"""Copy the CAD template files into a project and bind the SolidWorks copies to it."""

from __future__ import annotations

import os
import re
import shutil
import stat
import unicodedata
from collections.abc import Callable, Iterable
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from pathlib import Path

from projectflow.cad.document_manager import open_document_manager
from projectflow.cad.license_storage import load_solidworks_license_key
from projectflow.cad.solidworks_properties import (
    SOLIDWORKS_LINKED_SUFFIXES,
    SOLIDWORKS_SUFFIXES,
    TEMPLATE_MARKER,
    DocumentManagerFactory,
    SolidWorksDocument,
    SolidWorksDocumentManager,
    compute_property_updates,
    contains_marker,
    is_description_file,
    replace_marker,
)
from projectflow.config import CadConfig
from projectflow.core.models import CadFileResult, CadFileStatus, CadOutcome, ProjectInput
from projectflow.core.numero import ProjectNumber
from projectflow.exceptions import CadError
from projectflow.logging import get_logger
from projectflow.platform.paths import native_path_text

TEMPORARY_SUFFIXES = frozenset({".bak", ".dwl", ".dwl2"})
_COPY_CHUNK_SIZE = 1024 * 1024
_ONEDRIVE_HINT = (
    "Si le dossier modele est sur OneDrive, verifiez qu'il est synchronise et marque "
    "« Toujours conserver sur cet appareil »."
)


@dataclass(frozen=True, slots=True)
class CadOptionAvailability:
    available: bool
    reason: str


@dataclass(frozen=True, slots=True)
class TemplateFile:
    source: Path
    destination: Path

    @property
    def suffix(self) -> str:
        return self.source.suffix.casefold()


def template_availability(path: Path | None, label: str) -> CadOptionAvailability:
    if path is None:
        return CadOptionAvailability(
            available=False,
            reason=f"Dossier modele {label} non configure (Parametres > Modeles CAO).",
        )
    try:
        exists = path.is_dir()
    except OSError:
        exists = False
    if not exists:
        return CadOptionAvailability(
            available=False,
            reason=f"Dossier modele {label} introuvable : {native_path_text(path)}",
        )
    return CadOptionAvailability(
        available=True,
        reason=f"Copie les modeles {label} depuis {native_path_text(path)}",
    )


def is_temporary_cad_file(name: str) -> bool:
    return name.startswith("~$") or Path(name).suffix.casefold() in TEMPORARY_SUFFIXES


def cad_destination_dir(project_dir: Path, number: ProjectNumber, subfolder: str) -> Path:
    base = project_dir
    nested = project_dir / str(number)
    if number.is_subproject and nested.is_dir():
        base = nested
    for part in (part for part in re.split(r"[\\/]", subfolder) if part):
        base = _existing_child(base, part) or base / part
    return base


def _existing_child(parent: Path, name: str) -> Path | None:
    """Reuse ``Plan d'exécution`` when the setting says ``Plan d'execution`` (or other case)."""
    wanted = _folder_key(name)
    try:
        children = sorted(parent.iterdir())
    except OSError:
        return None
    return next(
        (child for child in children if child.is_dir() and _folder_key(child.name) == wanted),
        None,
    )


def _folder_key(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name.strip())
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def plan_template_copy(
    template_dir: Path,
    destination_dir: Path,
    number: str,
) -> list[TemplateFile]:
    """List template files to copy, keeping the template tree and renaming the marker."""
    if not template_dir.is_dir():
        raise CadError(f"Dossier modele introuvable: {native_path_text(template_dir)}")
    plan: list[TemplateFile] = []
    for source in sorted(template_dir.rglob("*")):
        if not source.is_file() or is_temporary_cad_file(source.name):
            continue
        if not contains_marker(source.name):
            continue
        relative = source.relative_to(template_dir)
        destination = destination_dir.joinpath(
            *(replace_marker(part, number) for part in relative.parts),
        )
        plan.append(TemplateFile(source=source, destination=destination))
    return plan


def copy_new_file(source: Path, destination: Path) -> bool:
    """Copy without ever replacing an existing file; return False when it already exists."""
    if destination.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with source.open("rb") as reader:
            try:
                writer = destination.open("xb")
            except FileExistsError:
                return False
            created = True
            with writer:
                shutil.copyfileobj(reader, writer, _COPY_CHUNK_SIZE)
    except OSError as exc:
        if created:
            with suppress(OSError):
                destination.unlink()
        raise CadError(f"Copie impossible de {source.name}: {exc}. {_ONEDRIVE_HINT}") from exc
    with suppress(OSError):
        shutil.copystat(source, destination)
    make_writable(destination)
    return True


def make_writable(path: Path) -> None:
    mode = path.stat().st_mode
    if not mode & stat.S_IWRITE:
        path.chmod(mode | stat.S_IWRITE)


def check_document_manager(
    license_key: str,
    template_dir: Path | None,
    *,
    manager_factory: DocumentManagerFactory = open_document_manager,
) -> str:
    """Open Document Manager and one template to validate the key; raise CadError otherwise."""
    sample = _first_solidworks_template(template_dir)
    with manager_factory(license_key) as manager:
        if sample is None:
            return (
                "Document Manager est installe. La cle sera verifiee a la premiere creation : "
                "aucun fichier SolidWorks modele trouve."
            )
        with manager.open_document(sample, read_only=True):
            pass
    return f"Document Manager et cle de licence valides (test sur {sample.name})."


def _first_solidworks_template(template_dir: Path | None) -> Path | None:
    if template_dir is None or not template_dir.is_dir():
        return None
    candidates = sorted(
        path
        for path in template_dir.rglob("*")
        if path.suffix.casefold() in SOLIDWORKS_SUFFIXES
        and contains_marker(path.name)
        and not is_temporary_cad_file(path.name)
    )
    return candidates[0] if candidates else None


class CadTemplateService:
    def __init__(
        self,
        *,
        license_key_loader: Callable[[], str] = load_solidworks_license_key,
        manager_factory: DocumentManagerFactory = open_document_manager,
    ) -> None:
        self._license_key_loader = license_key_loader
        self._manager_factory = manager_factory

    def apply(
        self,
        project: ProjectInput,
        project_dir: Path,
        *,
        config: CadConfig,
        initials: str,
    ) -> CadOutcome:
        """Blocking: run it with ``run_file_io`` (COM is initialised in the calling thread)."""
        if not (project.add_solidworks or project.add_autocad):
            return CadOutcome()
        run = _CadRun(
            project=project,
            destination=cad_destination_dir(
                project_dir,
                project.number,
                config.destination_subfolder,
            ),
            config=config,
            initials=initials,
        )
        if project.add_autocad:
            try:
                self._apply_autocad(run)
            except (CadError, OSError) as exc:
                run.errors.append(f"AutoCAD : {exc}")
        if project.add_solidworks:
            try:
                self._apply_solidworks(run)
            except (CadError, OSError) as exc:
                run.errors.append(f"SolidWorks : {exc}")
        outcome = run.outcome()
        get_logger(__name__).info(
            "cad.apply",
            project=str(project.number),
            created=sum(1 for item in outcome.files if item.status == "created"),
            skipped=sum(1 for item in outcome.files if item.status == "skipped"),
            failed=sum(1 for item in outcome.files if item.status == "error"),
            warnings=len(outcome.warnings),
            errors=len(outcome.errors),
        )
        return outcome

    @staticmethod
    def _apply_autocad(run: _CadRun) -> None:
        template_dir = _required_template_dir(run.config.autocad_template_dir, "AutoCAD")
        plan = plan_template_copy(template_dir, run.destination, run.number)
        if not plan:
            raise CadError(f"aucun fichier « {TEMPLATE_MARKER} » dans le dossier modele.")
        for item in plan:
            run.copy(item)

    def _apply_solidworks(self, run: _CadRun) -> None:
        template_dir = _required_template_dir(run.config.solidworks_template_dir, "SolidWorks")
        plan = plan_template_copy(template_dir, run.destination, run.number)
        if not plan:
            raise CadError(f"aucun fichier « {TEMPLATE_MARKER} » dans le dossier modele.")
        documents = [item for item in plan if item.suffix in SOLIDWORKS_SUFFIXES]
        with ExitStack() as stack:
            manager = self._open_manager(stack, documents, run)
            created: list[TemplateFile] = []
            for item in plan:
                if manager is None and item.suffix in SOLIDWORKS_LINKED_SUFFIXES:
                    # Without Document Manager an assembly copy would still open the templates.
                    if item.destination.exists():
                        run.record(item.destination, "skipped", "deja present")
                    else:
                        run.record(
                            item.destination,
                            "skipped",
                            "non copie : Document Manager indisponible",
                        )
                    continue
                if run.copy(item) and manager is not None and item.suffix in SOLIDWORKS_SUFFIXES:
                    created.append(item)
            if manager is None:
                return
            references = _ReferenceMap(template_dir, documents)
            for item in created:
                self._bind_document(manager, item, references, run)

    def _open_manager(
        self,
        stack: ExitStack,
        documents: list[TemplateFile],
        run: _CadRun,
    ) -> SolidWorksDocumentManager | None:
        if not documents:
            return None
        try:
            manager = stack.enter_context(self._manager_factory(self._license_key_loader()))
            # Validates the license: Document Manager only refuses an invalid key on open.
            with manager.open_document(documents[0].source, read_only=True):
                pass
        except CadError as exc:
            run.warnings.append(
                f"Document Manager indisponible ({exc}) : assemblages et mises en plan "
                "non copies, pieces copiees sans proprietes.",
            )
            return None
        return manager

    @staticmethod
    def _bind_document(
        manager: SolidWorksDocumentManager,
        item: TemplateFile,
        references: _ReferenceMap,
        run: _CadRun,
    ) -> None:
        linked = item.suffix in SOLIDWORKS_LINKED_SUFFIXES
        try:
            with manager.open_document(item.destination) as document:
                references_changed = _rewrite_references(
                    document,
                    references,
                    path=item.destination,
                    required=linked,
                )
                updates = compute_property_updates(
                    document.custom_properties(),
                    number=run.number,
                    societe=run.project.societe,
                    initials=run.initials,
                    designation=run.project.designation,
                    write_description=is_description_file(item.destination, run.number),
                    names=run.config.properties,
                )
                for name, value in updates.items():
                    document.set_custom_property(name, value)
                if references_changed or updates:
                    document.save()
            _verify_no_template_links(manager, item.destination, references)
        except Exception as exc:  # noqa: BLE001 - one file must not stop the others
            detail = str(exc).strip() or type(exc).__name__
            if linked or isinstance(exc, _TemplateLinkError):
                with suppress(OSError):
                    item.destination.unlink()
                detail += " La copie a ete supprimee pour ne pas modifier les modeles."
            else:
                detail = f"copie, mais proprietes non renseignees : {detail}"
            run.record(item.destination, "error", detail)


class _CadRun:
    def __init__(
        self,
        *,
        project: ProjectInput,
        destination: Path,
        config: CadConfig,
        initials: str,
    ) -> None:
        self.project = project
        self.number = str(project.number)
        self.destination = destination
        self.config = config
        self.initials = initials.strip()
        self.files: dict[Path, CadFileResult] = {}
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def record(self, path: Path, status: CadFileStatus, detail: str = "") -> None:
        self.files[path] = CadFileResult(path=str(path), status=status, detail=detail)

    def copy(self, item: TemplateFile) -> bool:
        try:
            created = copy_new_file(item.source, item.destination)
        except CadError as exc:
            self.record(item.destination, "error", str(exc))
            return False
        if created:
            self.record(item.destination, "created")
        else:
            self.record(item.destination, "skipped", "deja present")
        return created

    def outcome(self) -> CadOutcome:
        return CadOutcome(
            files=tuple(self.files.values()),
            warnings=tuple(self.warnings),
            errors=tuple(self.errors),
        )


class _ReferenceMap:
    """Map template documents to their project copies, by stored path or by file name.

    Templates saved on another computer store other user folders in their references, so
    the file name is the fallback when the stored path is not the local template folder.
    """

    def __init__(self, template_dir: Path, documents: Iterable[TemplateFile]) -> None:
        self._template_key = _path_key(str(template_dir))
        self._by_path: dict[str, Path] = {}
        by_name: dict[str, list[Path]] = {}
        for item in documents:
            self._by_path[_path_key(str(item.source))] = item.destination
            by_name.setdefault(item.source.name.casefold(), []).append(item.destination)
        self._by_name = {name: paths[0] for name, paths in by_name.items() if len(paths) == 1}

    def replacement_for(self, reference: str) -> Path | None:
        target = self._by_path.get(_path_key(reference))
        if target is not None:
            return target
        return self._by_name.get(_reference_name(reference).casefold())

    def template_links(self, references: Iterable[str]) -> list[str]:
        return [
            reference
            for reference in references
            if self._is_in_template_dir(reference) or contains_marker(_reference_name(reference))
        ]

    def _is_in_template_dir(self, reference: str) -> bool:
        key = _path_key(reference)
        prefix = self._template_key.rstrip("\\/") + os.sep
        return key == self._template_key or key.startswith(prefix)


def _rewrite_references(
    document: SolidWorksDocument,
    references: _ReferenceMap,
    *,
    path: Path,
    required: bool,
) -> bool:
    current = document.external_references()
    get_logger(__name__).info(
        "cad.references",
        file=path.name,
        references=[_reference_name(reference) for reference in current],
    )
    if required and not current:
        # An assembly or drawing always references documents: an empty list means they could
        # not be read, and the copy could still open (and modify) the templates.
        raise _TemplateLinkError(
            "Document Manager n'a renvoye aucune reference : impossible de relier la copie "
            "aux fichiers du projet.",
        )
    changed = False
    for reference in current:
        target = references.replacement_for(reference)
        if target is None or _path_key(reference) == _path_key(str(target)):
            continue
        document.replace_reference(reference, str(target))
        changed = True
    return changed


def _verify_no_template_links(
    manager: SolidWorksDocumentManager,
    path: Path,
    references: _ReferenceMap,
) -> None:
    """Re-read the saved file; raise when a reference still points to the templates."""
    with manager.open_document(path, read_only=True) as document:
        remaining = references.template_links(document.external_references())
    if remaining:
        raise _TemplateLinkError(
            "reference(s) encore liee(s) au dossier modele : "
            + ", ".join(_reference_name(reference) for reference in remaining)
            + ".",
        )


class _TemplateLinkError(CadError):
    pass


def _required_template_dir(path: Path | None, label: str) -> Path:
    availability = template_availability(path, label)
    if path is None or not availability.available:
        raise CadError(availability.reason)
    return path


def _path_key(value: str) -> str:
    return os.path.normcase(os.path.normpath(value))


def _reference_name(reference: str) -> str:
    return re.split(r"[\\/]", reference)[-1]
