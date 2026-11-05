"""Planning documents shared by every poste: tasks, resources, absences and settings.

Each document is stored on its own (one SharePoint list item, or one JSON file in the
local store) and carries the same envelope: id, schema version, author and time of the
creation and of the last change, soft deletion. Models are frozen; a change produces a
new instance. Unknown fields are kept (``extra="allow"``) so that an older ProjectFlow
never drops what a newer one wrote.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Annotated, Any, ClassVar, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

from projectflow.core.planning.calendar import (
    CANTONS,
    DEFAULT_WORK_WEEK,
    WorkPeriod,
    normalize_canton,
)
from projectflow.core.planning.walltime import WallTime, format_wall_time, parse_wall_time

SCHEMA_VERSION = 1
MAX_ESTIMATE_DAYS = 250
META_ID = "meta"

DocumentKind = Literal["task", "resource", "absence", "meta"]
DOCUMENT_KINDS: tuple[DocumentKind, ...] = ("task", "resource", "absence", "meta")
TaskStatus = Literal["provisional", "confirmed", "done"]
TaskOrigin = Literal["quick_entry", "grid", "sortie_dossier", "repertoire", "backlog"]
ResourceKind = Literal["person", "subcontractor"]
DayPart = Literal["day", "morning", "afternoon"]
AbsenceReason = Literal[
    "vacances", "absent", "ecole", "cie", "militaire", "formation", "recuperation", "conge"
]

ABSENCE_LABELS: Mapping[str, str] = {
    "vacances": "Vacances",
    "absent": "Absent",
    "ecole": "École professionnelle",
    "cie": "Cours CIE",
    "militaire": "Militaire / PC",
    "formation": "Formation",
    "recuperation": "Récupération",
    "conge": "Congé",
}
STATUS_LABELS: Mapping[str, str] = {
    "provisional": "Provisoire",
    "confirmed": "Confirmé",
    "done": "Terminé",
}


def _wall_time_input(value: object) -> object:
    if isinstance(value, str):
        return parse_wall_time(value)
    return value


WallTimeField = Annotated[
    WallTime,
    BeforeValidator(_wall_time_input),
    PlainSerializer(format_wall_time, return_type=str),
]


def new_document_id() -> str:
    return uuid.uuid4().hex


def utc_now_text() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")


class Author(_Frozen):
    """Who changed a document.

    With a shared Microsoft account only the initials and the poste name tell people apart.
    """

    initials: str = ""
    name: str = ""
    poste: str = ""
    poste_id: str = ""

    @property
    def label(self) -> str:
        return " · ".join(part for part in (self.initials or self.name, self.poste) if part)


class PlanningDocument(_Frozen):
    KIND: ClassVar[DocumentKind]

    id: str = Field(default_factory=new_document_id)
    schema_version: int = SCHEMA_VERSION
    created_at: str = ""
    created_by: Author = Field(default_factory=Author)
    updated_at: str = ""
    updated_by: Author = Field(default_factory=Author)
    deleted_at: str | None = None
    deleted_by: Author | None = None

    @property
    def kind(self) -> DocumentKind:
        return self.KIND

    @property
    def deleted(self) -> bool:
        return self.deleted_at is not None

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def stamped(self, author: Author, *, now: str | None = None) -> Self:
        """Copy marked as changed now by ``author`` (creation fields filled once)."""
        moment = now or utc_now_text()
        update: dict[str, Any] = {"updated_at": moment, "updated_by": author}
        if not self.created_at:
            update |= {"created_at": moment, "created_by": author}
        return self.model_copy(update=update)


# ------------------------------------------------------------- settings ----


class Team(_Frozen):
    key: str
    name: str
    alias: str = ""
    order: int = 0


class TaskType(_Frozen):
    key: str
    label: str
    color: str
    color_dark: str
    keywords: tuple[str, ...] = ()
    team: str = ""


class Closure(_Frozen):
    first_day: date
    last_day: date
    label: str = "Fermeture"

    @model_validator(mode="after")
    def _check_days(self) -> Self:
        if self.last_day < self.first_day:
            raise ValueError("Une fermeture se termine apres son debut.")
        return self


DEFAULT_TEAMS: tuple[Team, ...] = (
    Team(key="bureau", name="Bureau technique", alias="bureau", order=0),
    Team(key="atelier", name="Atelier", alias="atelier", order=1),
    Team(key="montage", name="Montage", alias="montage", order=2),
    Team(key="soustraitants", name="Sous-traitants", alias="soustraitance", order=3),
)

DEFAULT_TASK_TYPES: tuple[TaskType, ...] = (
    TaskType(
        key="releve",
        label="Relevé de cotes",
        color="#009494",
        color_dark="#36baba",
        keywords=("releve", "cotes", "metre", "mesure", "mesures"),
        team="bureau",
    ),
    TaskType(
        key="dessin",
        label="Dessin / plans",
        color="#8572bb",
        color_dark="#a996e2",
        keywords=("dessin", "plan", "plans", "dao", "cao", "calcul", "statique"),
        team="bureau",
    ),
    TaskType(
        key="debit",
        label="Débit",
        color="#908126",
        color_dark="#b5a64f",
        keywords=("debit", "decoupe", "sciage", "laser"),
        team="atelier",
    ),
    TaskType(
        key="soudure",
        label="Soudure / assemblage",
        color="#b7684c",
        color_dark="#df8c6f",
        keywords=("soudure", "souder", "assemblage", "fab", "fabrication", "pliage"),
        team="atelier",
    ),
    TaskType(
        key="finition",
        label="Finition / contrôle",
        color="#aa732b",
        color_dark="#d19853",
        keywords=("finition", "finitions", "meulage", "controle", "ebavurage"),
        team="atelier",
    ),
    TaskType(
        key="montage",
        label="Montage chantier",
        color="#4a925c",
        color_dark="#6fb880",
        keywords=("montage", "pose", "poser", "chantier"),
        team="montage",
    ),
    TaskType(
        key="livraison",
        label="Livraison",
        color="#b16389",
        color_dark="#d887ae",
        keywords=("livraison", "livrer", "transport", "camion"),
        team="montage",
    ),
    TaskType(
        key="sav",
        label="SAV / réparation",
        color="#4086bd",
        color_dark="#66abe5",
        keywords=("sav", "reparation", "depannage"),
        team="montage",
    ),
    TaskType(
        key="soustraitance",
        label="Sous-traitance",
        color="#787a7e",
        color_dark="#96989c",
        keywords=("galva", "galvanisation", "thermolaquage", "thermo", "peinture"),
        team="soustraitants",
    ),
    TaskType(
        key="interne",
        label="Interne",
        color="#96989c",
        color_dark="#b5b7bb",
        keywords=("rangement", "entretien", "nettoyage", "stock", "interne"),
    ),
)
DEFAULT_TASK_TYPE = "soudure"
"""Type of a new task when the shared list still has it, else the list's first type."""


def default_task_type_key(task_types: Sequence[TaskType]) -> str:
    """Type given to a new task: « soudure » when configured, else the first type."""
    if not task_types or any(kind.key == DEFAULT_TASK_TYPE for kind in task_types):
        return DEFAULT_TASK_TYPE
    return task_types[0].key


class PlanningMeta(PlanningDocument):
    """Shared settings, a single document whose id is ``META_ID``."""

    KIND: ClassVar[DocumentKind] = "meta"

    id: str = META_ID
    work_week: dict[int, tuple[WorkPeriod, ...]] = Field(
        default_factory=lambda: dict(DEFAULT_WORK_WEEK),
    )
    default_canton: str = "GE"
    closures: tuple[Closure, ...] = ()
    teams: tuple[Team, ...] = DEFAULT_TEAMS
    task_types: tuple[TaskType, ...] = DEFAULT_TASK_TYPES
    auto_card_on_sortie: bool = True
    min_app_version: str = ""

    @field_validator("default_canton", mode="before")
    @classmethod
    def _known_canton(cls, value: object) -> object:
        try:
            return normalize_canton(str(value))
        except ValueError:
            return CANTONS[0]

    @field_validator("task_types", mode="after")
    @classmethod
    def _some_task_type(cls, value: tuple[TaskType, ...]) -> tuple[TaskType, ...]:
        # A list emptied elsewhere would leave new tasks without any type.
        return value or DEFAULT_TASK_TYPES

    def team(self, key: str) -> Team | None:
        return next((team for team in self.teams if team.key == key), None)

    @property
    def default_task_type(self) -> str:
        """Key given to a new task (see ``default_task_type_key``)."""
        return default_task_type_key(self.task_types)

    def task_type(self, key: str) -> TaskType:
        """Return the configured type ``key``, else the default type of the shared list.

        A task whose type was removed from the list shows as that default type.
        """
        kinds = self.task_types or DEFAULT_TASK_TYPES
        for candidate in (key, default_task_type_key(kinds)):
            found = next((kind for kind in kinds if kind.key == candidate), None)
            if found is not None:
                return found
        return kinds[0]


# ------------------------------------------------------------ resources ----


class RecurringOff(_Frozen):
    """A weekday a person is never available, e.g. the apprentice's school day."""

    weekday: int = Field(ge=0, le=6)
    part: DayPart = "day"
    reason: AbsenceReason = "ecole"


class Resource(PlanningDocument):
    """One row of the planning: a person, with or without Microsoft account, or a subcontractor."""

    KIND: ClassVar[DocumentKind] = "resource"

    kind_of_resource: ResourceKind = "person"
    name: str
    initials: str = ""
    team: str = "atelier"
    role: str = ""
    apprentice: bool = False
    canton: str = ""
    order: int = 0
    color: str = ""
    active: bool = True
    m365_user_id: str = ""
    recurring_off: tuple[RecurringOff, ...] = ()

    @field_validator("canton", mode="before")
    @classmethod
    def _known_canton(cls, value: object) -> object:
        # Anything else than GE or VD (typed elsewhere) means the shared default.
        try:
            return normalize_canton(str(value)) if str(value).strip() else ""
        except ValueError:
            return ""

    @property
    def is_subcontractor(self) -> bool:
        return self.kind_of_resource == "subcontractor"

    @property
    def short_label(self) -> str:
        return self.initials or self.name


# --------------------------------------------------------------- tasks ----


class ProjectCache(_Frozen):
    """Dossier details copied once by the author of the task, for display only."""

    client: str = ""
    contact: str = ""
    designation: str = ""
    managed_by: str = ""
    atelier_release: str = ""
    location: str = ""
    read_at: str = ""


def task_label(number: str, designation: str) -> str:
    """« 2026-5271 - Local grillagé »; the number or the designation alone without the other."""
    number, designation = number.strip(), designation.strip()
    if number and designation:
        return f"{number} - {designation}"
    return number or designation


class PlanningTask(PlanningDocument):
    """A piece of work.

    Without ``start``/``end`` it waits in « À planifier »; with dates and no assignment it
    sits on the « Non affecté » row.
    """

    KIND: ClassVar[DocumentKind] = "task"

    status: TaskStatus = "confirmed"
    task_type: str = DEFAULT_TASK_TYPE
    title: str = ""
    project_number: str = ""
    project_cache: ProjectCache = Field(default_factory=ProjectCache)
    project_fingerprint: str = ""
    start: WallTimeField | None = None
    end: WallTimeField | None = None
    estimate_minutes: int = Field(default=0, ge=0)
    estimate_days: float = Field(default=0, ge=0, le=MAX_ESTIMATE_DAYS, allow_inf_nan=False)
    """Estimate typed in working days (« 3 j »); when set it wins over the minutes,
    so that a card placed on a Thursday ends on Friday at noon."""
    required_people: int = Field(default=1, ge=1)
    assignments: dict[str, dict[str, Any]] = Field(default_factory=dict)
    deadline: date | None = None
    urgent: bool = False
    notes: str = ""
    backlog_rank: str = ""
    origin: TaskOrigin = "grid"
    planner_task_id: str = ""
    off_hours: bool = False
    """Night work (« travail de nuit »): exact wall times, real elapsed duration, no
    working calendar (see ``night``)."""

    @model_validator(mode="after")
    def _check_dates(self) -> Self:
        if (self.start is None) != (self.end is None):
            raise ValueError("Une tache planifiee a un debut et une fin.")
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("La fin d'une tache doit suivre son debut.")
        return self

    @property
    def scheduled(self) -> bool:
        return self.start is not None and self.end is not None

    @property
    def assignee_ids(self) -> tuple[str, ...]:
        return tuple(self.assignments)

    @property
    def designation(self) -> str:
        """Designation shown: the one typed for the task, else the dossier's."""
        return self.title.strip() or self.project_cache.designation.strip()

    @property
    def display_title(self) -> str:
        return self.designation or self.project_number

    @property
    def label(self) -> str:
        """« [Numéro du dossier] - [Désignation] », the name of a task everywhere."""
        return task_label(self.project_number, self.designation)


# ------------------------------------------------------------- absences ----


class Absence(PlanningDocument):
    KIND: ClassVar[DocumentKind] = "absence"

    resource_id: str
    reason: AbsenceReason = "absent"
    first_day: date
    last_day: date
    part: DayPart = "day"
    note: str = ""

    @model_validator(mode="after")
    def _check_days(self) -> Self:
        if self.last_day < self.first_day:
            raise ValueError("Une absence se termine apres son debut.")
        return self

    @property
    def label(self) -> str:
        return ABSENCE_LABELS.get(self.reason, self.reason)


# ---------------------------------------------------------- (de)coding ----

DOCUMENT_CLASSES: Mapping[DocumentKind, type[PlanningDocument]] = {
    "task": PlanningTask,
    "resource": Resource,
    "absence": Absence,
    "meta": PlanningMeta,
}


def document_from_json(kind: str, data: Mapping[str, Any]) -> PlanningDocument:
    """Build the document of ``kind`` from its JSON; raises ValueError when unreadable."""
    model = DOCUMENT_CLASSES.get(kind)  # type: ignore[call-overload]
    if model is None:
        raise ValueError(f"Type de document de planning inconnu: {kind!r}")
    document: PlanningDocument = model.model_validate(dict(data))
    return document
