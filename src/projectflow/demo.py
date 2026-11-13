from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Final, cast

from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from projectflow.cad.demo_document_manager import (
    DEMO_LICENSE_KEY,
    open_json_document_manager,
    write_demo_document,
)
from projectflow.cad.solidworks_properties import TEMPLATE_MARKER
from projectflow.cad.templates import CadTemplateService
from projectflow.config import AppConfig, RepertoireChantierConfig, UserConfig
from projectflow.core.background_io import run_file_io
from projectflow.core.fiche_service import FICHE_SUFFIX, FicheService
from projectflow.core.local_repertoire import LocalWorkbookGateway
from projectflow.core.models import ProjectInput
from projectflow.core.numero import parse_project_number, project_folder_name
from projectflow.core.planning.changes import DocumentChange, PlanningSnapshot
from projectflow.core.planning.commands import (
    CommandContext,
    calendar_resolver,
    create_absence,
    create_resource,
    create_task,
)
from projectflow.core.planning.local_store import LocalPlanningStore
from projectflow.core.planning.models import (
    Author,
    PlanningDocument,
    PlanningMeta,
    PlanningTask,
    ProjectCache,
    RecurringOff,
    ResourceKind,
    TaskOrigin,
    TaskStatus,
    utc_now_text,
)
from projectflow.core.planning.poste_config import PlanningPosteConfig
from projectflow.core.planning.store import PlanningStore
from projectflow.core.planning.walltime import wall_time
from projectflow.core.project_service import ProjectService, copy_reference_tree
from projectflow.core.repertoire_service import RepertoireService
from projectflow.exceptions import ConfigError, ProjectFlowError
from projectflow.logging import get_logger
from projectflow.outlook.local import create_local_outlook_client
from projectflow.platform.paths import data_dir
from projectflow.services import PLANNING_SETTINGS_PLACE, planning_authorization_error

DEMO_PLANNING_INITIALS: Final = "DEMO"
DEMO_PLANNING_NAME: Final = "Mode démo"
DEMO_POSTE_NAME: Final = "POSTE-DEMO"
DEMO_PLANNING_CONFIG_FILE: Final = "planning.json"
DEMO_PLANNING_FOLDER: Final = "Planning"
DEMO_FIRST_PROJECT_ID: Final = 4996
"""The demo répertoire lists 4995 to 5004: 4995 stays « disponible » for a new project."""
EN_DASH: Final = "\u2013"
DEMO_SHAREPOINT_MESSAGE: Final = (
    "Mode démo : le planning SharePoint n'est pas disponible (autorisation Microsoft "
    "manquante, consentement administrateur en attente). Choisissez le stockage local "
    f"dans {PLANNING_SETTINGS_PLACE}."
)


@dataclass(slots=True)
class DemoServiceContainer:
    config: AppConfig
    workbook_path: Path
    fiche_service: FicheService | None = None

    def fiche(self) -> FicheService:
        if self.fiche_service is None:
            self.fiche_service = FicheService()
        return self.fiche_service

    def repertoire(self) -> RepertoireService:
        return RepertoireService(LocalWorkbookGateway(self.workbook_path))

    def project(self) -> ProjectService:
        return ProjectService(
            config=self.config,
            fiche_service=self.fiche(),
            repertoire_service=self.repertoire(),
            outlook=create_local_outlook_client(self.config.outlook),
            cad=CadTemplateService(
                license_key_loader=lambda: DEMO_LICENSE_KEY,
                manager_factory=open_json_document_manager,
            ),
        )

    @property
    def root(self) -> Path:
        return self.workbook_path.parent

    def planning_config_path(self) -> Path:
        # Beside the demo files, never over the planning settings of the real poste.
        return self.root / DEMO_PLANNING_CONFIG_FILE

    def planning_poste_config(self) -> PlanningPosteConfig:
        """Planning settings of the demo, signed « DEMO · POSTE-DEMO », stored locally."""
        poste_config = PlanningPosteConfig.load(self.planning_config_path())
        changed = False
        if not poste_config.is_identified:
            poste_config.user_initials = DEMO_PLANNING_INITIALS
            poste_config.user_name = DEMO_PLANNING_NAME
            poste_config.poste_name = DEMO_POSTE_NAME
            changed = True
        if not poste_config.local_directory:
            poste_config.local_directory = str(self.root / DEMO_PLANNING_FOLDER)
            changed = True
        if changed:
            try:
                self.save_planning_poste_config(poste_config)
            except ConfigError:
                get_logger(__name__).warning("demo.planning.config_not_saved")
        return poste_config

    def save_planning_poste_config(self, poste_config: PlanningPosteConfig) -> None:
        poste_config.save(self.planning_config_path())

    async def create_planning_store(self, poste_config: PlanningPosteConfig) -> PlanningStore:
        """Local planning of the demo, filled once with a plausible week of work.

        Microsoft is never reached from the demo: OneDrive stands for the demo folder,
        and choosing SharePoint shows the state of a poste without access to it.
        """
        if poste_config.backend == "sharepoint":
            raise planning_authorization_error(DEMO_SHAREPOINT_MESSAGE)
        store = LocalPlanningStore(poste_config.local_store_directory())
        await self.seed_planning(store)
        return store

    async def seed_planning(self, store: PlanningStore, *, today: date | None = None) -> bool:
        """Fill an empty store with the demo planning; False when it already has content."""
        existing = await store.changes(None)
        if existing.documents:
            return False
        day = today or date.today()
        numbers = await self._free_numbers(day.year)
        for document in demo_planning_documents(day, numbers):
            project = document.project_number if isinstance(document, PlanningTask) else ""
            await store.create(document.kind, document.id, document.to_json(), project=project)
        get_logger(__name__).info("demo.planning.seeded", numbers=len(numbers))
        await self._ensure_dossiers(numbers)
        return True

    async def _free_numbers(self, year: int) -> tuple[str, ...]:
        """Numbers of the demo dossiers: free rows of the répertoire, the first one kept."""
        defaults = demo_project_numbers(year)
        try:
            snapshot = await self.repertoire().read_snapshot(year=year)
        except (ProjectFlowError, OSError, ValueError):
            return defaults
        next_free = str(snapshot.next_available.number) if snapshot.next_available else ""
        free = [
            row.number
            for row in snapshot.rows
            if row.number != next_free
            and row.number.count("-") == 1
            and not any(str(value or "").strip() for value in row.values[1:])
        ]
        chosen = free[: len(DEMO_DOSSIERS)]
        spare = [number for number in defaults if number not in chosen]
        return tuple(chosen + spare[: len(DEMO_DOSSIERS) - len(chosen)])

    async def _ensure_dossiers(self, numbers: Sequence[str]) -> None:
        """Create the folder, the fiche and the répertoire row of each free demo dossier."""
        root = self.config.paths.racine_projets
        reference = self.config.paths.dossier_reference
        if root is None or reference is None:
            return
        for dossier, number in zip(DEMO_DOSSIERS, numbers, strict=False):
            parsed = parse_project_number(number)
            project = ProjectInput(
                number=parsed,
                designation=dossier.designation,
                societe=dossier.client,
                contact=dossier.contact,
                localisation=dossier.location,
                gere_par=dossier.managed_by,
            )
            project_dir = root / str(parsed.year) / project_folder_name(parsed)
            try:
                created = await run_file_io(
                    _create_demo_dossier_files,
                    project_dir,
                    reference,
                    project,
                    self.fiche(),
                )
                if created:
                    await self.repertoire().upsert_project(project)
            except (ProjectFlowError, OSError, ValueError) as exc:
                get_logger(__name__).warning(
                    "demo.planning.dossier_skipped",
                    number=number,
                    error=str(exc),
                )


@dataclass(frozen=True, slots=True)
class DemoDossier:
    client: str
    contact: str
    designation: str
    location: str
    managed_by: str


@dataclass(frozen=True, slots=True)
class _DemoPerson:
    key: str
    name: str
    initials: str
    team: str
    role: str
    canton: str = ""
    school_day: int | None = None
    """Weekday of the vocational school (0 = Monday): the person is an apprentice."""
    kind: ResourceKind = "person"


_ATELIER_AUTHOR: Final = Author(initials="TM", name="Thomas Meier", poste="ATELIER")


@dataclass(frozen=True, slots=True)
class _DemoTask:
    """One task of the demo; days are counted from the Monday of the current week."""

    title: str
    task_type: str
    dossier: int | None = None
    people: tuple[str, ...] = ()
    day: int | None = None
    """None: a card of « À planifier »."""
    hour: int = 6
    minute: int = 30
    days: float = 1.0
    status: TaskStatus = "confirmed"
    deadline: int | None = None
    urgent: bool = False
    origin: TaskOrigin = "grid"
    released: int | None = None
    notes: str = ""
    author: Author = _ATELIER_AUTHOR


DEMO_DOSSIERS: Final[tuple[DemoDossier, ...]] = (
    DemoDossier("Müller SA", "M. Müller", "Garde-corps escalier extérieur", "Carouge", "CF"),
    DemoDossier("PPE Les Vergers", "Mme Carron", "Garde-corps balcons", "Meyrin", "YB"),
    DemoDossier("Zufferey & Fils", "M. Zufferey", "Portail coulissant", "Nyon", "CF"),
    DemoDossier("Bonvin Architectes", "Mme Bonvin", "Marquise verre entrée", "Genève", "YB"),
    DemoDossier("Gillioz Immobilier", "M. Gillioz", "Barrière parking souterrain", "Lancy", "SR"),
    DemoDossier("Rossier Construction", "M. Rossier", "Escalier extérieur", "Morges", "CF"),
    DemoDossier("Bétrisey SA", "Mme Bétrisey", "Portail, réglage SAV", "Vernier", "YB"),
    DemoDossier("Régie du Lac", "M. Dupraz", "Main courante inox école", "Versoix", "SR"),
)
_MULLER, _VERGERS, _ZUFFEREY, _BONVIN, _GILLIOZ, _ROSSIER, _BETRISEY, _REGIE = range(8)

_BUREAU_TEAM = "bureau"
_ATELIER_TEAM = "atelier"
_MONTAGE = "montage"
_SUBCONTRACTORS = "soustraitants"
_SUBCONTRACTOR: Final[ResourceKind] = "subcontractor"
DEMO_PEOPLE: Final[tuple[_DemoPerson, ...]] = (
    _DemoPerson("cf", "Claire Fournier", "CF", _BUREAU_TEAM, "Dessinatrice-constructrice"),
    _DemoPerson("yb", "Yann Berclaz", "YB", _BUREAU_TEAM, "Chef de projet"),
    _DemoPerson("sr", "Sophie Rochat", "SR", _BUREAU_TEAM, "Technicienne", canton="VD"),
    _DemoPerson("tm", "Thomas Meier", "TM", _ATELIER_TEAM, "Chef d'atelier"),
    _DemoPerson("mr", "Marc Rey", "MR", _ATELIER_TEAM, "Serrurier"),
    _DemoPerson("jd", "Julien Dubois", "JD", _ATELIER_TEAM, "Soudeur"),
    _DemoPerson("ps", "Paulo Silva", "PS", _ATELIER_TEAM, "Serrurier"),
    _DemoPerson("kr", "Kevin Roth", "KR", _ATELIER_TEAM, "Apprenti 2e année", school_day=2),
    _DemoPerson("nf", "Nicolas Favre", "NF", _MONTAGE, "Chef monteur", canton="VD"),
    _DemoPerson("sg", "Samuel Gay", "SG", _MONTAGE, "Monteur"),
    _DemoPerson("lb", "Luca Bianchi", "LB", _MONTAGE, "Monteur"),
    _DemoPerson("dp", "David Pellet", "DP", _MONTAGE, "Monteur", canton="VD"),
    _DemoPerson(
        "galva",
        "Galvanisation",
        "GA",
        _SUBCONTRACTORS,
        "Galvanisation à chaud",
        kind=_SUBCONTRACTOR,
    ),
    _DemoPerson(
        "thermo", "Thermolaquage", "TH", _SUBCONTRACTORS, "Thermolaquage", kind=_SUBCONTRACTOR
    ),
)

_BUREAU = Author(initials="YB", name="Yann Berclaz", poste="BUREAU-1")
_DESSIN = Author(initials="CF", name="Claire Fournier", poste="BUREAU-2")
_DEMO = Author(initials=DEMO_PLANNING_INITIALS, name=DEMO_PLANNING_NAME, poste=DEMO_POSTE_NAME)
_FAB = f"Fabrication {EN_DASH} "
_MON, _TUE, _WED, _THU, _FRI, _NEXT_MON, _NEXT_TUE = 0, 1, 2, 3, 4, 7, 8

DEMO_TASKS: Final[tuple[_DemoTask, ...]] = (
    # Bureau technique
    _DemoTask(
        "Plans d'exécution barrière", "dessin", _GILLIOZ, ("cf",), _MON, days=3, author=_DESSIN
    ),
    _DemoTask(
        "Relevé de cotes escalier", "releve", _ROSSIER, ("yb",), _MON, days=0.5, author=_BUREAU
    ),
    _DemoTask(
        "Dessin portail coulissant", "dessin", _ZUFFEREY, ("sr",), _MON, days=2, author=_DESSIN
    ),
    _DemoTask(
        "Plans escalier ext.", "dessin", _ROSSIER, ("cf",), _NEXT_MON, days=2, author=_DESSIN
    ),
    # Atelier: one task shared by two people, the apprentice beside a serrurier.
    _DemoTask(
        "Fab. garde-corps escalier ext.",
        "soudure",
        _MULLER,
        ("mr", "jd"),
        _MON,
        days=3,
        notes="Prendre le poste TIG mobile.",
    ),
    _DemoTask(f"Main courante inox {EN_DASH} école", "soudure", _REGIE, ("ps", "kr"), _MON, days=2),
    _DemoTask(
        "Portail coulissant",
        "soudure",
        _ZUFFEREY,
        ("ps",),
        _WED,
        days=2,
        status="provisional",
        notes="En attente de la validation du client.",
    ),
    _DemoTask("Rangement stock tubes", "interne", None, ("kr",), _THU),
    _DemoTask("Finition / contrôle garde-corps", "finition", _MULLER, ("tm",), _THU, days=0.5),
    _DemoTask("Débit escalier ext.", "debit", _ROSSIER, ("mr",), _THU, days=1.5),
    _DemoTask(
        f"SAV portail {EN_DASH} réglage", "sav", _BETRISEY, ("jd",), _THU, days=0.5, urgent=True
    ),
    _DemoTask("Fab. barrière parking", "soudure", _GILLIOZ, ("mr", "jd"), _NEXT_MON, days=2),
    # Montage: a pair, then a task for three.
    _DemoTask(
        "Pose marquise verre",
        _MONTAGE,
        _BONVIN,
        ("nf", "sg"),
        _MON,
        days=2,
        author=_BUREAU,
    ),
    _DemoTask(
        "Pose garde-corps balcons",
        _MONTAGE,
        _VERGERS,
        ("nf", "sg", "lb"),
        _WED,
        days=2,
        author=_BUREAU,
    ),
    _DemoTask("Entretien véhicules et outillage", "interne", None, ("dp",), _MON),
    _DemoTask(
        "Livraison garde-corps balcons",
        "livraison",
        _VERGERS,
        ("dp",),
        _TUE,
        hour=13,
        minute=0,
        days=0.5,
        author=_BUREAU,
    ),
    _DemoTask(
        "Préparation socles barrière",
        _MONTAGE,
        _GILLIOZ,
        ("dp",),
        _WED,
        days=2,
        status="provisional",
        author=_BUREAU,
    ),
    _DemoTask(
        "Pose garde-corps escalier ext.",
        _MONTAGE,
        _MULLER,
        ("nf", "sg"),
        _NEXT_TUE,
        days=1.5,
        author=_BUREAU,
    ),
    _DemoTask(
        f"Pose main courante {EN_DASH} école",
        _MONTAGE,
        _REGIE,
        ("lb", "dp"),
        _NEXT_MON,
        author=_BUREAU,
    ),
    # Sous-traitants, and a delivery that nobody has yet (« Non affecté »).
    _DemoTask(
        "Galvanisation garde-corps",
        "soustraitance",
        _MULLER,
        ("galva",),
        _THU,
        hour=13,
        minute=0,
        days=1.5,
    ),
    _DemoTask(
        "Thermolaquage portail",
        "soustraitance",
        _ZUFFEREY,
        ("thermo",),
        _NEXT_MON,
        days=3,
        status="provisional",
    ),
    _DemoTask("Livraison main courante", "livraison", _REGIE, (), _FRI, days=0.5, author=_BUREAU),
    # « À planifier »: two cards made by « Sortie dossier », the others typed in.
    _DemoTask(
        f"{_FAB}Escalier extérieur",
        "soudure",
        _ROSSIER,
        days=3,
        origin="sortie_dossier",
        released=-3,
        deadline=18,
        author=_DESSIN,
    ),
    _DemoTask(
        f"{_FAB}Garde-corps balcons (2e étape)",
        "soudure",
        _VERGERS,
        days=4,
        origin="sortie_dossier",
        released=1,
        deadline=25,
        author=_BUREAU,
    ),
    _DemoTask(
        "Montage barrière parking souterrain",
        _MONTAGE,
        _GILLIOZ,
        ("nf", "sg"),
        days=2,
        deadline=21,
        author=_BUREAU,
    ),
    _DemoTask(
        f"SAV portail {EN_DASH} remplacement moteur",
        "sav",
        _BETRISEY,
        days=0.5,
        urgent=True,
        deadline=9,
        author=_BUREAU,
    ),
    _DemoTask("Contrôle final et emballage portail", "finition", _ZUFFEREY, days=0.5),
    _DemoTask("Entretien poste à souder TIG", "interne"),
)
"""Work of this week and the next, then the cards of « À planifier »."""


def demo_project_numbers(year: int) -> tuple[str, ...]:
    return tuple(f"{year}-{DEMO_FIRST_PROJECT_ID + index}" for index in range(len(DEMO_DOSSIERS)))


def demo_planning_documents(
    today: date,
    numbers: Sequence[str] | None = None,
    *,
    now: str | None = None,
) -> tuple[PlanningDocument, ...]:
    """Shared settings, the roster and the work of this week and the next.

    Built with the planning commands, so that every bar keeps to the working hours (never
    a Friday afternoon nor a weekend) and to the holidays of its row's canton.
    """
    seed = _DemoSeed(
        monday=today - timedelta(days=today.weekday()),
        today=today,
        numbers=tuple(numbers) if numbers else demo_project_numbers(today.year),
        now=now or utc_now_text(),
    )
    return seed.build()


class _DemoSeed:
    def __init__(self, *, monday: date, today: date, numbers: tuple[str, ...], now: str) -> None:
        self._monday = monday
        self._today = today
        self._numbers = numbers
        self._now = now
        meta = PlanningMeta().stamped(_DEMO, now=now)
        self._snapshot = PlanningSnapshot(meta=meta)
        self._documents: dict[tuple[str, str], PlanningDocument] = {("meta", meta.id): meta}
        self._people: dict[str, str] = {}

    def build(self) -> tuple[PlanningDocument, ...]:
        for person in DEMO_PEOPLE:
            self._people[person.key] = self._apply(self._person(person))
        self._apply(
            create_absence(
                self._context(_BUREAU),
                resource_id=self._people["lb"],
                first_day=self._monday,
                last_day=self._day(_TUE),
                reason="vacances",
                note="Vacances d'automne",
            ),
        )
        for task in DEMO_TASKS:
            self._apply(self._task(task))
        return tuple(self._documents.values())

    def _apply(self, changes: Sequence[DocumentChange]) -> str:
        self._snapshot = self._snapshot.apply(changes)
        for change in changes:
            if change.after is not None:
                self._documents[change.kind, change.doc_id] = change.after
        return changes[0].doc_id

    def _context(self, author: Author) -> CommandContext:
        return CommandContext(
            snapshot=self._snapshot,
            author=author,
            now=self._now,
            calendar_for=calendar_resolver(self._snapshot),
        )

    def _day(self, offset: int) -> date:
        return self._monday + timedelta(days=offset)

    def _person(self, person: _DemoPerson) -> Sequence[DocumentChange]:
        school = (
            (RecurringOff(weekday=person.school_day, part="day", reason="ecole"),)
            if person.school_day is not None
            else ()
        )
        return create_resource(
            self._context(_DEMO),
            name=person.name,
            initials=person.initials,
            kind_of_resource=person.kind,
            team=person.team,
            role=person.role,
            apprentice=person.school_day is not None,
            canton=person.canton,
            recurring_off=school,
        )

    def _task(self, task: _DemoTask) -> Sequence[DocumentChange]:
        cache: ProjectCache | None = None
        if task.dossier is not None:
            info = DEMO_DOSSIERS[task.dossier]
            released = (
                min(self._today, self._day(task.released)) if task.released is not None else None
            )
            cache = ProjectCache(
                client=info.client,
                contact=info.contact,
                designation=info.designation,
                managed_by=info.managed_by,
                location=info.location,
                atelier_release=released.isoformat() if released is not None else "",
                read_at=self._now,
            )
        start = (
            wall_time(self._day(task.day), task.hour, task.minute) if task.day is not None else None
        )
        return create_task(
            self._context(task.author),
            title=task.title,
            task_type=task.task_type,
            project_number=self._numbers[task.dossier] if task.dossier is not None else "",
            project_cache=cache,
            start=start,
            resource_ids=[self._people[key] for key in task.people],
            estimate_days=task.days,
            status=task.status,
            deadline=self._day(task.deadline) if task.deadline is not None else None,
            urgent=task.urgent,
            notes=task.notes,
            # A card nobody placed was typed in the quick entry bar.
            origin=task.origin if task.day is not None or task.origin != "grid" else "quick_entry",
        )


def _create_demo_dossier_files(
    project_dir: Path,
    reference_dir: Path,
    project: ProjectInput,
    fiche_service: FicheService,
) -> bool:
    """Copy the reference folder and fill the fiche; False when the folder already exists."""
    if project_dir.exists():
        return False
    project_dir.mkdir(parents=True)
    copy_reference_tree(reference_dir, project_dir)
    fiche_service.fill_fiche(
        project_dir,
        project,
        new_fiche=True,
        user_initials=DEMO_PLANNING_INITIALS,
    )
    return True


def build_demo_environment(
    *,
    base_dir: Path | None = None,
) -> tuple[AppConfig, DemoServiceContainer]:
    root = base_dir or data_dir() / "demo"
    clients_dir = root / "Clients"
    reference_dir = root / "Modeles" / "10-Racine"
    repertoire_path = root / "Repertoire chantier demo.xlsx"
    solidworks_dir = root / "Modeles" / "11-Racine Solidworks"
    autocad_dir = root / "Modeles" / "12-Racine AutoCAD"

    clients_dir.mkdir(parents=True, exist_ok=True)
    reference_dir.mkdir(parents=True, exist_ok=True)
    _ensure_reference_fiche(reference_dir)
    _ensure_repertoire(repertoire_path)
    _ensure_cad_templates(solidworks_dir, autocad_dir)

    config = AppConfig()
    config.user = UserConfig(
        tenant_id="demo",
        user_id="demo",
        display_name="Mode demo",
        email="demo@projectflow.local",
    )
    config.paths.racine_projets = clients_dir
    config.paths.dossier_reference = reference_dir
    config.paths.repertoire_chantier = RepertoireChantierConfig(
        drive_id="local-demo",
        item_id=str(repertoire_path),
        display_path=str(repertoire_path),
    )
    config.cad.solidworks_template_dir = solidworks_dir
    config.cad.autocad_template_dir = autocad_dir
    config.cad.destination_subfolder = "CAO"
    services = DemoServiceContainer(config=config, workbook_path=repertoire_path)
    return config, services


def _ensure_reference_fiche(reference_dir: Path) -> None:
    fiche_path = reference_dir / f"modele{FICHE_SUFFIX}"
    if fiche_path.exists():
        return
    workbook = Workbook()
    worksheet = cast("Worksheet", workbook.active)
    worksheet.title = "Fiche"
    worksheet["C3"] = ""
    worksheet["D3"] = "Societe : "
    worksheet["D4"] = "Contact : "
    worksheet["D5"] = "Projet : "
    worksheet["D6"] = "Localisation : "
    worksheet["C6"] = ""
    workbook.save(fiche_path)


def _ensure_cad_templates(solidworks_dir: Path, autocad_dir: Path) -> None:
    """Fake SolidWorks files (JSON) that the demo Document Manager can read and write."""
    parts = ["ENV-100.SLDPRT", "PRT-100.SLDPRT", "PRT-200.SLDPRT"]
    common = {
        "Projet": TEMPLATE_MARKER,
        "Client": "",
        "Auteur": "",
        "Révision": "",
        "Fournisseur": "Balz Métal SA",
        "FaireouAcheter": "Faire",
    }
    for name in parts:
        path = solidworks_dir / f"{TEMPLATE_MARKER}-{name}"
        if not path.exists():
            write_demo_document(path, common)
    assembly = solidworks_dir / f"{TEMPLATE_MARKER}-ENS-100.SLDASM"
    if not assembly.exists():
        write_demo_document(
            assembly,
            common,
            [str(solidworks_dir / f"{TEMPLATE_MARKER}-{name}") for name in parts],
            configurations={"Défaut": {"Description": ""}},
        )
    drawing = autocad_dir / f"{TEMPLATE_MARKER}-ENS-100.dwg"
    if not drawing.exists():
        drawing.parent.mkdir(parents=True, exist_ok=True)
        drawing.write_bytes(b"AC1032 ProjectFlow demo")


def _ensure_repertoire(repertoire_path: Path) -> None:
    current_year = date.today().year
    workbook = load_workbook(repertoire_path) if repertoire_path.exists() else Workbook()

    if "Sheet" in workbook.sheetnames and str(current_year) not in workbook.sheetnames:
        workbook.remove(workbook["Sheet"])

    for year in [current_year, current_year + 1]:
        worksheet_name = str(year)
        if worksheet_name not in workbook.sheetnames:
            worksheet = workbook.create_sheet(worksheet_name)
            worksheet.append(
                [
                    "Numero",
                    "Date",
                    "Societe",
                    "Contact",
                    "Description",
                    "Gere par",
                ]
            )
            for project_id in range(4995, 5005):
                worksheet.append([f"{year}-{project_id}", "", "", "", "", ""])

    workbook.save(repertoire_path)
    workbook.close()
