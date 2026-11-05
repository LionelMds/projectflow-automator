"""Copy the documents of one planning store into another (local planning → OneDrive).

The copy only adds: a document the target already holds is never overwritten, so it can
be run again after an interruption, or by a second poste, without losing anything.
The shared settings (``meta``) follow the same rule: copied only when the target has none.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from projectflow.core.planning.store import (
    PlanningStore,
    StoreConflictError,
    StoredDocument,
    StoreUnavailableError,
)
from projectflow.exceptions import ProjectFlowError
from projectflow.logging import get_logger


@dataclass(frozen=True, slots=True)
class CopyReport:
    created: int = 0
    """Documents written to the target (or that would be, for a dry run)."""
    skipped_existing: int = 0
    """Documents the target already held: left as they are."""
    failed: int = 0
    errors: tuple[str, ...] = field(default=())
    dry_run: bool = False

    @property
    def total(self) -> int:
        return self.created + self.skipped_existing + self.failed

    def summary(self) -> str:
        verb = "à copier" if self.dry_run else "copiés"
        text = (
            f"{self.created} élément(s) {verb}, {self.skipped_existing} déjà présent(s)"
            f", {self.failed} en échec."
        )
        return text if not self.errors else f"{text} {self.errors[0]}"


async def copy_planning(
    source: PlanningStore,
    target: PlanningStore,
    *,
    dry_run: bool = False,
) -> CopyReport:
    """Create in ``target`` every document of ``source`` it does not hold yet.

    Both stores are read in full first. A document created in the target meanwhile
    (conflict on creation) counts as already present. A target out of reach stops the
    copy with ``StoreUnavailableError``: run it again later, nothing is duplicated.
    """
    documents = [document for document in (await source.changes(None)).documents if _live(document)]
    existing = {
        (document.kind, document.doc_id)
        for document in (await target.changes(None)).documents
        if not document.deleted
    }
    created = skipped = failed = 0
    errors: list[str] = []
    # The shared settings last: the planning they describe is in place when they arrive.
    for document in sorted(documents, key=lambda item: item.kind == "meta"):
        if (document.kind, document.doc_id) in existing:
            skipped += 1
            continue
        if dry_run:
            created += 1
            continue
        try:
            await target.create(document.kind, document.doc_id, dict(document.data or {}))
        except StoreConflictError:
            skipped += 1
        except StoreUnavailableError:
            raise
        except (ProjectFlowError, ValueError) as exc:
            failed += 1
            errors.append(str(exc))
            get_logger(__name__).warning(
                "planning.copy.document_failed",
                kind=document.kind,
                error=type(exc).__name__,
            )
        else:
            created += 1
    report = CopyReport(
        created=created,
        skipped_existing=skipped,
        failed=failed,
        errors=tuple(errors),
        dry_run=dry_run,
    )
    get_logger(__name__).info(
        "planning.copy.done",
        created=created,
        skipped=skipped,
        failed=failed,
        dry_run=dry_run,
    )
    return report


def _live(document: StoredDocument) -> bool:
    return not document.deleted and document.data is not None
