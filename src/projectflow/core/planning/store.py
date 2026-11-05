"""Storage contract of the shared planning.

``GraphPlanningBackend`` (SharePoint list) and ``LocalPlanningStore`` (JSON files, for
the demo mode, the tests and a poste without Microsoft) both implement
``PlanningStore``. A store only moves JSON documents with an eTag; it knows nothing of
tasks or resources.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from projectflow.exceptions import ProjectFlowError


class StoreConflictError(ProjectFlowError):
    """The document changed on the server since the eTag the write was based on."""


class StoreUnavailableError(ProjectFlowError):
    """The store cannot be reached (offline, signed out, missing permission)."""


@dataclass(frozen=True, slots=True)
class StoredDocument:
    kind: str
    doc_id: str
    etag: str
    data: Mapping[str, Any] | None
    """Document JSON; None when ``deleted`` (hard deletion on the server)."""
    deleted: bool = False


@dataclass(frozen=True, slots=True)
class StoreChanges:
    documents: tuple[StoredDocument, ...]
    cursor: str
    """Opaque position to pass to the next ``changes`` call."""
    full: bool = False
    """True when ``documents`` is the complete content: local documents missing from it
    were deleted, except those this poste still has to write."""
    upload_local: bool = False
    """With ``full``: the server was restored (Microsoft resyncChangesUploadDifferences).
    Local documents missing from the listing must be written again, not dropped."""


class PlanningStore(Protocol):
    async def changes(self, cursor: str | None) -> StoreChanges:
        """Documents changed since ``cursor``; everything when ``cursor`` is None."""

    async def get(self, kind: str, doc_id: str) -> StoredDocument | None:
        """Return the current server version of one document, or None when it is gone."""

    async def create(
        self,
        kind: str,
        doc_id: str,
        data: Mapping[str, Any],
        *,
        project: str = "",
    ) -> StoredDocument:
        """Store a new document and return it with its first eTag."""

    async def update(
        self,
        kind: str,
        doc_id: str,
        etag: str,
        data: Mapping[str, Any],
        *,
        project: str | None = None,
    ) -> str:
        """Replace a document if its eTag is still ``etag``; return the new eTag.

        Raises StoreConflictError otherwise.
        """

    async def delete(self, kind: str, doc_id: str, etag: str) -> None:
        """Remove a document for good (soft deletion is a normal ``update``)."""

    async def aclose(self) -> None:
        """Release connections."""
