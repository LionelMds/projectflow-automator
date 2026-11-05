"""Planning prototype data: rows (people and subcontractors), tasks and a seeded generator.

The generator only aims at plausible load for the benchmark: dossiers active for a
few weeks, team tasks shared by two or three people, some double bookings and
holidays (vacances) so that rows need several lanes.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from timescale import DAY_SPAN, SNAP_MINUTES, WORK_PER_DAY, WorkCalendar


class Kind(Enum):
    ETUDE = "etude"
    FABRICATION = "fabrication"
    MONTAGE = "montage"
    SOUS_TRAITANCE = "sous-traitance"
    ABSENCE = "absence"


KIND_LABELS = {
    Kind.ETUDE: "Étude",
    Kind.FABRICATION: "Fabrication",
    Kind.MONTAGE: "Montage",
    Kind.SOUS_TRAITANCE: "Sous-traitance",
    Kind.ABSENCE: "Absence",
}

GROUPS = ("Bureau technique", "Atelier", "Montage", "Sous-traitants")
SUBCONTRACTORS = ("Galvaswiss", "Thermolaquage", "Grutage Léman")


@dataclass(frozen=True, slots=True)
class Resource:
    id: str
    name: str
    group: str
    subcontractor: bool = False


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    dossier: str
    title: str
    kind: Kind
    start: datetime
    end: datetime
    resource_ids: tuple[str, ...]


@dataclass(slots=True)
class Plan:
    resources: list[Resource]
    tasks: dict[str, Task]

    @property
    def bar_count(self) -> int:
        return sum(len(task.resource_ids) for task in self.tasks.values())


# ------------------------------------------------------------- generator ----

_FIRST_NAMES = (
    "Marc", "Julien", "Nicolas", "Sébastien", "David", "Laurent", "Pascal", "Thierry",
    "Olivier", "Yann", "Kevin", "Luca", "Antonio", "João", "Miguel", "Pedro", "Fabio",
    "Stéphane", "Cédric", "Christophe", "Patrick", "Alain", "Jérôme", "Romain", "Florian",
    "Mathieu", "Dylan", "Samuel", "Bruno", "Daniel", "Sandra", "Nathalie", "Sophie",
    "Laura", "Céline", "Raphaël", "Hugo", "Loïc", "Damien", "Arnaud",
)  # fmt: skip
_LAST_NAMES = (
    "Rochat", "Favre", "Pittet", "Cuendet", "Bovet", "Jaquier", "Perret", "Chappuis",
    "Golay", "Dupraz", "Ferreira", "Da Silva", "Rossi", "Morel", "Bonvin", "Gaillard",
    "Meylan", "Reymond", "Vuilleumier", "Monnier", "Berset", "Ducret", "Pasche", "Dumas",
    "Rey", "Gavillet", "Correia", "Santos", "Baumann", "Jaccard",
)  # fmt: skip
_OBJECTS = (
    "garde-corps", "escalier", "marquise", "portail coulissant", "main courante", "balcons",
    "verrière", "pergola", "charpente", "passerelle", "barrières", "auvent", "sas d'entrée",
    "caillebotis", "porte coupe-feu", "couvert à vélos", "escalier de secours", "brise-soleil",
)  # fmt: skip
_VERBS = {
    Kind.ETUDE: ("Relevé", "Plans d'atelier", "Calcul statique", "Mise en plan", "Liste de débit"),
    Kind.FABRICATION: ("Débit", "Soudure", "Assemblage", "Finitions", "Pliage", "Usinage"),
    Kind.MONTAGE: ("Montage", "Pose", "Scellements", "Réglages", "Démontage"),
}
_RAL = ("7016", "9005", "9010", "7035", "6005", "8019")


@dataclass(frozen=True, slots=True)
class _Profile:
    kind: Kind
    durations: tuple[int, ...]
    gaps: tuple[int, ...]
    share: float
    overlap: float


_D = WORK_PER_DAY
_PROFILES = {
    "Bureau technique": _Profile(
        Kind.ETUDE, (120, 240, 300, _D, _D, 2 * _D, 3 * _D), (0, 0, 30, 60, 240), 0.08, 0.3
    ),
    "Atelier": _Profile(
        Kind.FABRICATION, (240, _D, _D, 2 * _D, 2 * _D, 3 * _D), (0, 0, 0, 60, 240), 0.14, 0.25
    ),
    "Montage": _Profile(
        Kind.MONTAGE, (210, 300, _D, _D, 2 * _D, 3 * _D), (0, 0, 120, 240, _D), 0.35, 0.3
    ),
    "Sous-traitants": _Profile(
        Kind.SOUS_TRAITANCE, (2 * _D, 3 * _D, 4 * _D, 5 * _D), (0, _D, 2 * _D), 0.0, 0.0
    ),
}


def _resources(rows: int, rng: random.Random) -> list[Resource]:
    subcontractors = min(len(SUBCONTRACTORS), rows // 4)
    staff = rows - subcontractors
    bureau = round(staff * 0.22)
    montage = round(staff * 0.35)
    counts = {
        "Bureau technique": bureau,
        "Atelier": staff - bureau - montage,
        "Montage": montage,
    }
    names = [f"{first} {last}" for first in _FIRST_NAMES for last in _LAST_NAMES]
    rng.shuffle(names)
    resources: list[Resource] = []
    for group, count in counts.items():
        base = len(resources)
        chosen = sorted(names[base : base + count])
        resources.extend(
            Resource(id=f"r{base + offset:03d}", name=name, group=group)
            for offset, name in enumerate(chosen)
        )
    resources.extend(
        Resource(id=f"s{index:02d}", name=name, group="Sous-traitants", subcontractor=True)
        for index, name in enumerate(SUBCONTRACTORS[:subcontractors])
    )
    return resources


class _Builder:
    def __init__(self, calendar: WorkCalendar, rng: random.Random) -> None:
        self.calendar = calendar
        self.rng = rng
        self.tasks: dict[str, Task] = {}
        self.busy: dict[str, list[tuple[int, int]]] = {}
        count = max(12, len(calendar) * 2 // 3)
        self.dossiers = [
            (f"2026-{101 + index}", rng.choice(_OBJECTS), rng.randrange(calendar.end))
            for index in range(count)
        ]

    def fill(self, resource: Resource, peers: list[Resource]) -> None:
        profile = _PROFILES[resource.group]
        if not resource.subcontractor and self.rng.random() < 0.3:
            self._absence(resource)
        streams = 2 if resource.subcontractor else 1
        for stream in range(streams):
            self._stream(resource, peers, profile, ignore_busy=stream > 0)
        if self.rng.random() < profile.overlap:
            for _ in range(self.rng.choice((1, 1, 2))):
                self._extra(resource, profile)

    def _stream(
        self, resource: Resource, peers: list[Resource], profile: _Profile, *, ignore_busy: bool
    ) -> None:
        calendar = self.calendar
        position = calendar.working_start(self.rng.choice((0, 0, 120, 240)))
        while True:
            start = calendar.working_start(
                calendar.add_work(position, self.rng.choice(profile.gaps))
            )
            end = calendar.add_work(start, self.rng.choice(profile.durations))
            if start >= calendar.end - SNAP_MINUTES or end >= calendar.end:
                return
            clash = None if ignore_busy else self._clash(resource.id, start, end)
            if clash is not None:
                position = clash
                continue
            owners = [resource.id, *self._partners(resource, peers, profile, start, end)]
            self._add(profile.kind, start, end, owners=owners, resource=resource)
            position = end

    def _partners(
        self, resource: Resource, peers: list[Resource], profile: _Profile, start: int, end: int
    ) -> list[str]:
        if self.rng.random() >= profile.share:
            return []
        later = [
            peer
            for peer in peers
            if peer.group == resource.group
            and peer.id > resource.id
            and self._clash(peer.id, start, end) is None
        ]
        self.rng.shuffle(later)
        return [peer.id for peer in later[: self.rng.choice((1, 1, 2))]]

    def _absence(self, resource: Resource) -> None:
        start = self.calendar.working_start(
            self.rng.randrange(self.calendar.end // DAY_SPAN) * DAY_SPAN
        )
        end = self.calendar.add_work(start, self.rng.choice((2, 3, 5)) * WORK_PER_DAY)
        self._add(
            Kind.ABSENCE, start, end, owners=[resource.id], resource=resource, title="Vacances"
        )

    def _extra(self, resource: Resource, profile: _Profile) -> None:
        start = self.calendar.working_start(
            self.rng.randrange(0, max(self.calendar.end - DAY_SPAN, SNAP_MINUTES), SNAP_MINUTES)
        )
        end = self.calendar.add_work(start, self.rng.choice((60, 120, 240, WORK_PER_DAY)))
        self._add(profile.kind, start, end, owners=[resource.id], resource=resource)

    def _clash(self, resource_id: str, start: int, end: int) -> int | None:
        for busy_start, busy_end in self.busy.get(resource_id, []):
            if busy_start < end and start < busy_end:
                return busy_end
        return None

    def _add(
        self,
        kind: Kind,
        start: int,
        end: int,
        *,
        owners: list[str],
        resource: Resource,
        title: str = "",
    ) -> None:
        dossier, subject = self._dossier(start)
        if kind is Kind.ABSENCE:
            dossier = ""
        elif kind is Kind.SOUS_TRAITANCE:
            title = self._subcontract_title(resource.name, subject)
        else:
            title = f"{self.rng.choice(_VERBS[kind])} {subject}"
        task_id = f"t{len(self.tasks):05d}"
        self.tasks[task_id] = Task(
            id=task_id,
            dossier=dossier,
            title=title,
            kind=kind,
            start=self.calendar.moment_at(start),
            end=self.calendar.moment_at(end, end=True),
            resource_ids=tuple(owners),
        )
        for owner in owners:
            self.busy.setdefault(owner, []).append((start, end))

    def _dossier(self, position: int) -> tuple[str, str]:
        window = 15 * DAY_SPAN
        near = [entry for entry in self.dossiers if abs(entry[2] - position) < window]
        number, subject, _center = self.rng.choice(near or self.dossiers)
        return number, subject

    def _subcontract_title(self, name: str, subject: str) -> str:
        if name.startswith("Galva"):
            return f"Galvanisation {subject}"
        if name.startswith("Thermo"):
            return f"Thermolaquage RAL {self.rng.choice(_RAL)}"
        return f"Levage {subject}"


def generate_plan(calendar: WorkCalendar, rows: int = 40, seed: int = 118) -> Plan:
    """Seeded demo plan over ``calendar`` with ``rows`` rows (subcontractors included)."""
    if rows < 1:
        raise ValueError("Il faut au moins une ligne de planning.")
    rng = random.Random(seed)
    resources = _resources(rows, rng)
    builder = _Builder(calendar, rng)
    for resource in resources:
        builder.fill(resource, resources)
    return Plan(resources=resources, tasks=builder.tasks)
