"""Changes between planning states, and a read-only snapshot to compute them from.

Commands are pure functions of a ``PlanningSnapshot`` that return ``DocumentChange``
tuples. The service applies a tuple at once (one undo step), the synchronisation writes
each change with the server's eTag and, on conflict, reapplies only the fields the
change touched if the server still holds their ``before`` values.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from projectflow.core.planning.models import (
    Absence,
    DocumentKind,
    PlanningDocument,
    PlanningMeta,
    PlanningTask,
    Resource,
)

# Fields rewritten by every save: they never make two changes conflict.
VOLATILE_FIELDS = frozenset({"updated_at", "updated_by", "schema_version"})


@dataclass(frozen=True, slots=True)
class DocumentChange:
    """One document before and after a change; ``before`` is None for a creation."""

    kind: DocumentKind
    doc_id: str
    before: PlanningDocument | None
    after: PlanningDocument | None

    def __post_init__(self) -> None:
        if self.before is None and self.after is None:
            raise ValueError("Un changement de planning a un etat avant ou apres.")

    @classmethod
    def creation(cls, document: PlanningDocument) -> DocumentChange:
        return cls(kind=document.kind, doc_id=document.id, before=None, after=document)

    @classmethod
    def update(cls, before: PlanningDocument, after: PlanningDocument) -> DocumentChange:
        return cls(kind=after.kind, doc_id=after.id, before=before, after=after)

    @property
    def is_creation(self) -> bool:
        return self.before is None

    def inverse(self) -> DocumentChange:
        return replace(self, before=self.after, after=self.before)

    def changed_fields(self) -> dict[str, tuple[Any, Any]]:
        """Top-level JSON fields whose value differs, as ``(before, after)``."""
        before = self.before.to_json() if self.before is not None else {}
        after = self.after.to_json() if self.after is not None else {}
        return {
            name: (before.get(name), after.get(name))
            for name in before.keys() | after.keys()
            if name not in VOLATILE_FIELDS and before.get(name) != after.get(name)
        }


@dataclass(frozen=True, slots=True)
class PlanningSnapshot:
    """Every live and soft-deleted document known to this poste, at one moment."""

    tasks: Mapping[str, PlanningTask] = field(default_factory=dict)
    resources: Mapping[str, Resource] = field(default_factory=dict)
    absences: Mapping[str, Absence] = field(default_factory=dict)
    meta: PlanningMeta = field(default_factory=PlanningMeta)

    def get(self, kind: DocumentKind, doc_id: str) -> PlanningDocument | None:
        if kind == "meta":
            return self.meta if self.meta.id == doc_id else None
        collection: Mapping[str, PlanningDocument] = {
            "task": self.tasks,
            "resource": self.resources,
            "absence": self.absences,
        }[kind]
        return collection.get(doc_id)

    def live_tasks(self) -> list[PlanningTask]:
        return [task for task in self.tasks.values() if not task.deleted]

    def backlog(self) -> list[PlanningTask]:
        """Tasks without dates (« À planifier »), in their shared manual order."""
        return sorted(
            (task for task in self.live_tasks() if not task.scheduled),
            key=lambda task: (task.backlog_rank, task.created_at, task.id),
        )

    def active_resources(self) -> list[Resource]:
        """Rows of the planning: by team order, then shared order, then name."""
        team_order = {team.key: team.order for team in self.meta.teams}
        return sorted(
            (
                resource
                for resource in self.resources.values()
                if resource.active and not resource.deleted
            ),
            key=lambda resource: (
                team_order.get(resource.team, len(team_order)),
                resource.team,
                resource.order,
                resource.name.casefold(),
            ),
        )

    def live_absences(self) -> list[Absence]:
        return [absence for absence in self.absences.values() if not absence.deleted]

    def apply(self, changes: Iterable[DocumentChange]) -> PlanningSnapshot:
        """Return a new snapshot with ``changes`` applied (``after`` None removes it)."""
        tasks = dict(self.tasks)
        resources = dict(self.resources)
        absences = dict(self.absences)
        meta = self.meta
        for change in changes:
            document = change.after
            if change.kind == "meta":
                meta = document if isinstance(document, PlanningMeta) else PlanningMeta()
                continue
            collections: dict[str, dict[str, Any]] = {
                "task": tasks,
                "resource": resources,
                "absence": absences,
            }
            target = collections[change.kind]
            if document is None:
                target.pop(change.doc_id, None)
            else:
                target[change.doc_id] = document
        return PlanningSnapshot(tasks=tasks, resources=resources, absences=absences, meta=meta)
