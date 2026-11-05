"""``PlanningStore`` over the SharePoint list of ``GraphListPlanningStore``.

SharePoint addresses list items by item id, the planning by document kind and id, and a
deleted item comes back from the delta API with its item id only. The backend therefore
learns which item holds which document from every read and write. After a restart with a
saved cursor that map is partial: the first write or read of a document it does not know,
or a deletion it cannot place, costs one full enumeration of the list. The service never
saw an enumeration made for a write, so the next ``changes`` hands it over, brought up to
date with the delta since, as a full read: a deletion the enumeration already missed would
otherwise never reach the service.

After a restore of the server every poste writes back the documents it lost. A document
created before the restore was seen is therefore looked up on the server (from a full
enumeration, then by delta) before it is created: the first poste to write it back wins,
the others meet its item as a conflict and merge onto it instead of adding a duplicate.
Two postes posting the same document within one request of each other still can; the
oldest item then holds it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol, Self, TypeVar, cast

from projectflow.core.planning.models import utc_now_text
from projectflow.core.planning.store import (
    StoreChanges,
    StoreConflictError,
    StoredDocument,
    StoreUnavailableError,
)
from projectflow.exceptions import AuthError, GraphError
from projectflow.graph.client import GraphClient
from projectflow.graph.planning_store import (
    HTTP_BAD_REQUEST,
    HTTP_FORBIDDEN,
    HTTP_NOT_FOUND,
    PLANNING_KINDS,
    GraphListPlanningStore,
    PlanningChanges,
    PlanningConflictError,
    PlanningKind,
    PlanningListRef,
    PlanningRecord,
)
from projectflow.logging import get_logger

PROBE_KIND: Final = "probe"
TASK_KIND: Final = "task"
HTTP_UNAUTHORIZED: Final = 401
HTTP_LOCKED: Final = 423
HTTP_TOO_MANY_REQUESTS: Final = 429
HTTP_SERVER_ERROR: Final = 500
# Statuses after which nothing changed and the same request may succeed later.
UNAVAILABLE_STATUSES: Final = frozenset(
    {HTTP_UNAUTHORIZED, HTTP_FORBIDDEN, HTTP_LOCKED, HTTP_TOO_MANY_REQUESTS},
)
# A delta link refused with these statuses is dropped for a full enumeration.
STALE_CURSOR_STATUSES: Final = frozenset({HTTP_BAD_REQUEST, HTTP_NOT_FOUND})

MISSING_MESSAGE: Final = "Cet element du planning a ete supprime sur un autre poste entre-temps."
EXISTS_MESSAGE: Final = "Cet element du planning existe deja: il a ete cree sur un autre poste."

_Key = tuple[str, str]
_Result = TypeVar("_Result")


class PlanningListStore(Protocol):
    """What the backend uses of ``GraphListPlanningStore`` (a fake in the tests)."""

    async def changes(self, delta_link: str | None = None) -> PlanningChanges: ...

    async def get(self, item_id: str) -> PlanningRecord | None: ...

    async def create(
        self,
        kind: PlanningKind,
        doc_id: str,
        data: Mapping[str, Any],
        project: str = "",
    ) -> PlanningRecord: ...

    async def update(
        self,
        record_or_item_id: PlanningRecord | str,
        etag: str,
        data: Mapping[str, Any],
        project: str | None = None,
    ) -> str: ...

    async def delete(self, item_id: str, etag: str) -> None: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _Item:
    item_id: str
    learned: int
    """Value of the backend's counter when learned, to keep it through older full reads."""


class GraphPlanningBackend:
    def __init__(
        self, store: PlanningListStore, *, clock: Callable[[], str] = utc_now_text
    ) -> None:
        self._store = store
        self._clock = clock
        self._items: dict[_Key, _Item] = {}
        self._keys: dict[str, _Key] = {}
        self._counter = 0
        self._complete = False
        self._refresh_lock = asyncio.Lock()
        self._refreshed: PlanningChanges | None = None
        """Enumeration made by ``_refresh`` that ``changes`` has not handed over yet."""
        self._restored_at = ""
        """UTC time a restore of the server was last seen (``utc_now_text`` format)."""

    @classmethod
    def for_list(
        cls,
        *,
        graph: GraphClient,
        list_ref: PlanningListRef,
        page_size: int | None = None,
        clock: Callable[[], str] = utc_now_text,
    ) -> Self:
        return cls(
            GraphListPlanningStore(graph=graph, list_ref=list_ref, page_size=page_size),
            clock=clock,
        )

    @property
    def store(self) -> PlanningListStore:
        return self._store

    async def changes(self, cursor: str | None) -> StoreChanges:
        """Documents changed since ``cursor``, everything when it is None.

        A full read after Microsoft's resyncChangesUploadDifferences comes with
        ``upload_local``: the documents it misses are written again, not dropped.
        """
        refreshed = self._refreshed
        started = self._counter
        if refreshed is not None and cursor:
            result = await self._since_refresh(refreshed)
        else:
            result = await self._read(cursor or None)
            if not result.full and not self._complete and self._has_unknown_deletion(result):
                # Which document a deleted item held is unknown after a restart: read all.
                started = self._counter
                result = await self._read(None)
        if self._refreshed is refreshed:
            # Kept when a refresh ran meanwhile: that one is handed over next time.
            self._refreshed = None
        documents = self._absorb(result, started=started)
        return StoreChanges(
            documents=documents,
            cursor=result.delta_link,
            full=result.full,
            upload_local=result.full and result.uploads_local_differences,
        )

    async def get(self, kind: str, doc_id: str) -> StoredDocument | None:
        item_id = await self._item_id(kind, doc_id)
        if item_id is None:
            return None
        record = await self._call(self._store.get(item_id))
        if record is None or (record.kind, record.doc_id) != (kind, doc_id):
            self._forget(item_id)
            return None
        return self._learn(record)

    async def create(
        self,
        kind: str,
        doc_id: str,
        data: Mapping[str, Any],
        *,
        project: str = "",
    ) -> StoredDocument:
        key = (kind, doc_id)
        if key not in self._items and self._may_be_written_back(data):
            await self._read_others()
        if key in self._items:
            raise StoreConflictError(EXISTS_MESSAGE)
        record = await self._call(
            self._store.create(
                _planning_kind(kind),
                doc_id,
                data,
                project or _task_project(kind, data) or "",
            ),
        )
        self._remember(record.item_id, (record.kind, record.doc_id))
        return StoredDocument(
            kind=record.kind,
            doc_id=record.doc_id,
            etag=record.etag,
            data=record.data if record.data is not None else dict(data),
        )

    async def update(
        self,
        kind: str,
        doc_id: str,
        etag: str,
        data: Mapping[str, Any],
        *,
        project: str | None = None,
    ) -> str:
        item_id = await self._item_id(kind, doc_id)
        if item_id is None:
            raise StoreConflictError(MISSING_MESSAGE)
        column = project if project is not None else _task_project(kind, data)
        try:
            return await self._call(self._store.update(item_id, etag, data, column))
        except GraphError as exc:
            if exc.status_code != HTTP_NOT_FOUND:
                raise
            self._forget(item_id)
            raise StoreConflictError(MISSING_MESSAGE) from exc

    async def delete(self, kind: str, doc_id: str, etag: str) -> None:
        item_id = await self._item_id(kind, doc_id)
        if item_id is None:
            return
        await self._call(self._store.delete(item_id, etag))
        self._forget(item_id)

    async def aclose(self) -> None:
        await self._store.aclose()

    # ------------------------------------------------------------- reading ----

    async def _read(self, cursor: str | None) -> PlanningChanges:
        try:
            return await self._call(self._store.changes(cursor))
        except GraphError as exc:
            if cursor is None or exc.status_code not in STALE_CURSOR_STATUSES:
                raise
        get_logger(__name__).warning("planning.graph.cursor_refused")
        return await self._call(self._store.changes(None))

    async def _item_id(self, kind: str, doc_id: str) -> str | None:
        item = self._items.get((kind, doc_id))
        if item is None and not self._complete:
            await self._refresh()
            item = self._items.get((kind, doc_id))
        return item.item_id if item is not None else None

    async def _refresh(self) -> None:
        async with self._refresh_lock:
            if self._complete:
                return
            started = self._counter
            result = await self._read(None)
            self._absorb(result, started=started)
            self._refreshed = result

    def _may_be_written_back(self, data: Mapping[str, Any]) -> bool:
        """Whether another poste may have written this document back after a restore."""
        created = data.get("created_at")
        return bool(self._restored_at) and (
            not isinstance(created, str) or created <= self._restored_at
        )

    async def _read_others(self) -> None:
        """Learn the items written since the last read; handed over like a refresh."""
        async with self._refresh_lock:
            started = self._counter
            refreshed = self._refreshed
            if refreshed is None:
                result = await self._read(None)
            else:
                result = await self._since_refresh(refreshed)
            self._absorb(result, started=started)
            self._refreshed = result

    async def _since_refresh(self, refreshed: PlanningChanges) -> PlanningChanges:
        """Bring the refresh's enumeration up to date with the delta since, as a full read."""
        delta = await self._read(refreshed.delta_link or None)
        if delta.full:
            return delta
        records = {record.item_id: record for record in refreshed.records if not record.deleted}
        for record in delta.records:
            if record.deleted:
                records.pop(record.item_id, None)
            else:
                records[record.item_id] = record
        return PlanningChanges(
            records=tuple(records.values()),
            delta_link=delta.delta_link,
            resynced=refreshed.resynced,
            full=True,
            requests=delta.requests,
            fields_fetched=delta.fields_fetched,
            # A restore seen by a read made for a write still asks the service to upload.
            resync_reason=refreshed.resync_reason,
        )

    def _has_unknown_deletion(self, result: PlanningChanges) -> bool:
        return any(
            record.deleted and record.item_id not in self._keys and not record.doc_id
            for record in result.records
        )

    def _absorb(self, result: PlanningChanges, *, started: int) -> tuple[StoredDocument, ...]:
        if result.full and result.uploads_local_differences:
            self._restored_at = self._clock()
        if result.full:
            # Items learned before the read began and absent from it no longer exist.
            listed = {record.item_id for record in result.records if not record.deleted}
            self._items = {
                key: item
                for key, item in self._items.items()
                if item.item_id in listed or item.learned > started
            }
            live = listed | {item.item_id for item in self._items.values()}
            self._keys = {item_id: key for item_id, key in self._keys.items() if item_id in live}
            self._complete = True
        documents: list[StoredDocument] = []
        for record in result.records:
            document = self._learn(record)
            if document is not None:
                documents.append(document)
        return tuple(documents)

    def _learn(self, record: PlanningRecord) -> StoredDocument | None:
        if record.deleted:
            return self._learn_deletion(record)
        if not record.kind or not record.doc_id or record.kind == PROBE_KIND:
            return None
        key = (record.kind, record.doc_id)
        current = self._items.get(key)
        if (
            current is not None
            and current.item_id != record.item_id
            and _item_order(current.item_id) < _item_order(record.item_id)
        ):
            # Two postes created the same document: the oldest item holds it.
            self._keys[record.item_id] = key
            get_logger(__name__).warning(
                "planning.graph.duplicate_document",
                kind=record.kind,
                kept=current.item_id,
                ignored=record.item_id,
            )
            return None
        self._remember(record.item_id, key)
        if record.data is None:
            get_logger(__name__).warning(
                "planning.graph.document_skipped",
                kind=record.kind,
                item_id=record.item_id,
            )
            return None
        return StoredDocument(
            kind=record.kind,
            doc_id=record.doc_id,
            etag=record.etag,
            data=record.data,
        )

    def _learn_deletion(self, record: PlanningRecord) -> StoredDocument | None:
        key = self._keys.pop(record.item_id, None)
        if key is None and record.kind and record.doc_id:
            key = (record.kind, record.doc_id)
        if key is None:
            return None
        current = self._items.get(key)
        if current is not None and current.item_id != record.item_id:
            return None  # a duplicate item went away; the document lives on
        self._items.pop(key, None)
        return StoredDocument(kind=key[0], doc_id=key[1], etag=record.etag, data=None, deleted=True)

    def _remember(self, item_id: str, key: _Key) -> None:
        # A replaced item keeps its entry in _keys: its deletion then changes nothing.
        self._counter += 1
        self._items[key] = _Item(item_id=item_id, learned=self._counter)
        self._keys[item_id] = key

    def _forget(self, item_id: str) -> None:
        key = self._keys.pop(item_id, None)
        item = self._items.get(key) if key is not None else None
        if key is not None and item is not None and item.item_id == item_id:
            del self._items[key]

    # -------------------------------------------------------------- errors ----

    async def _call(self, operation: Awaitable[_Result]) -> _Result:
        try:
            return await operation
        except PlanningConflictError as exc:
            raise StoreConflictError(str(exc)) from exc
        except AuthError as exc:
            raise StoreUnavailableError(str(exc)) from exc
        except GraphError as exc:
            if _unavailable(exc):
                raise StoreUnavailableError(str(exc)) from exc
            raise


def _unavailable(exc: GraphError) -> bool:
    status = exc.status_code
    # No status: the request never got an answer (offline, timeout).
    return status is None or status in UNAVAILABLE_STATUSES or status >= HTTP_SERVER_ERROR


def _planning_kind(kind: str) -> PlanningKind:
    if kind not in PLANNING_KINDS or kind == PROBE_KIND:
        raise ValueError(f"Type de document de planning inconnu: {kind!r}")
    return cast("PlanningKind", kind)


def _task_project(kind: str, data: Mapping[str, Any]) -> str | None:
    """Value of the PfProject column for a task, None for other documents."""
    if kind != TASK_KIND:
        return None
    number = data.get("project_number")
    return number.strip() if isinstance(number, str) else ""


def _item_order(item_id: str) -> tuple[int, str]:
    # SharePoint item ids are increasing integers.
    return (len(item_id), item_id)
