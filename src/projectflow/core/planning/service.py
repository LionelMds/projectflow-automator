"""State of the shared planning on this poste: snapshot, local changes, undo and redo.

A local change is applied at once (optimistic) and queued in the outbox, one pending
write per document: successive changes of a document before it is sent become one write.
``SyncEngine`` sends the outbox and brings in the changes of the other postes through
the engine-facing methods (``next_write``, ``write_succeeded``, ``write_conflicted``,
``apply_remote``). Nothing here depends on Qt.

Invariant: a document without pending write is exactly the server version of its known
eTag. A remote version of a document with a pending write is not applied: the write then
meets it as a conflict and merges it field by field (``merge_documents``); it is kept
aside, and applied if the write ends without reaching the server.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final, Literal

from projectflow.core.planning.cache import (
    CacheContent,
    CachedDocument,
    CachedWrite,
    PlanningCache,
)
from projectflow.core.planning.changes import DocumentChange, PlanningSnapshot
from projectflow.core.planning.models import (
    DOCUMENT_KINDS,
    Author,
    DocumentKind,
    PlanningDocument,
    PlanningTask,
    document_from_json,
    utc_now_text,
)
from projectflow.core.planning.store import PlanningStore, StoreChanges, StoredDocument
from projectflow.exceptions import ProjectFlowError
from projectflow.logging import get_logger

EventOrigin = Literal["local", "remote", "conflict", "status"]
DocumentKey = tuple[DocumentKind, str]
"""``(kind, doc_id)``."""

MAX_UNDO_ENTRIES: Final = 100
ASSIGNMENTS_FIELD: Final = "assignments"
DELETION_FIELDS: Final = frozenset({"deleted_at", "deleted_by"})
# Filled by the first save: two postes stamping the same new document is not a conflict.
CREATION_FIELDS: Final = frozenset({"created_at", "created_by"})
# Fields that only make sense together: when one of them keeps the server's value, all do.
LINKED_FIELDS: Final = (
    frozenset({"start", "end"}),
    frozenset({"first_day", "last_day"}),
    DELETION_FIELDS,
)

DOCUMENT_PHRASES: Final[Mapping[str, str]] = {
    "task": "cette tâche",
    "resource": "cette ressource",
    "absence": "cette absence",
    "meta": "les réglages du planning",
}
DELETED_PHRASES: Final[Mapping[str, str]] = {
    "task": "cette tâche a été supprimée",
    "resource": "cette ressource a été supprimée",
    "absence": "cette absence a été supprimée",
    "meta": "les réglages du planning ont été supprimés",
}
OTHER_POSTE: Final = "un autre poste"
NOT_SAVED: Final = "votre modification n'a pas été enregistrée."
UNREADABLE_SERVER_VERSION: Final = (
    "La version du serveur est illisible pour cette version de ProjectFlow : "
    "votre modification n'a pas été enregistrée."
)
REFUSED_BY_SERVER: Final = "Le serveur refuse d'enregistrer"
NOTHING_TO_UNDO: Final = "Rien à annuler."
NOTHING_TO_REDO: Final = "Rien à rétablir."
UNDO_VERB: Final = "Impossible d'annuler"
REDO_VERB: Final = "Impossible de rétablir"
RESTORE_LABEL: Final = "Restaurer"

_KINDS: Final[Mapping[str, DocumentKind]] = {kind: kind for kind in DOCUMENT_KINDS}


@dataclass(frozen=True, slots=True)
class PlanningEvent:
    """Sent to subscribers; the documents in ``changed`` must be redrawn, whatever the origin."""

    changed: frozenset[DocumentKey]
    origin: EventOrigin
    message: str = ""
    author: Author | None = None
    restorable: tuple[PlanningDocument, ...] = ()
    """Local versions lost to a change made elsewhere, for ``PlanningService.restore``.

    After a deletion made elsewhere, the deleted document as this poste had it (« Restaurer »);
    after a field changed on both postes, the merged version with this poste's values of the
    lost fields (« Réappliquer ma version »).
    """


EventCallback = Callable[[PlanningEvent], None]


@dataclass(frozen=True, slots=True)
class UndoEntry:
    label: str
    changes: tuple[DocumentChange, ...]
    revived: frozenset[DocumentKey] = frozenset()
    """Creations that undo a purge: reverting them purges again."""


@dataclass(frozen=True, slots=True)
class PendingWrite:
    """One write to send: ``after`` replaces the server version known by ``etag``."""

    kind: DocumentKind
    doc_id: str
    etag: str | None
    """None: the server does not have the document yet (create)."""
    before: PlanningDocument | None
    after: PlanningDocument | None
    """None: remove the document for good (explicit purge)."""
    revision: int

    @property
    def key(self) -> DocumentKey:
        return (self.kind, self.doc_id)

    @property
    def project(self) -> str | None:
        """Project number column of a task; None for the other kinds."""
        return self.after.project_number if isinstance(self.after, PlanningTask) else None


@dataclass(frozen=True, slots=True)
class _OutboxEntry:
    before: PlanningDocument | None
    """Server version the write is based on (None: never on the server). Without eTag,
    the version a restore of the server lost: the write creates it again."""
    after: PlanningDocument | None
    revision: int


@dataclass(slots=True)
class _RemoteBatch:
    documents: dict[DocumentKey, PlanningDocument | None] = field(default_factory=dict)
    authors: dict[DocumentKey, Author | None] = field(default_factory=dict)
    conflicts: list[PlanningEvent] = field(default_factory=list)


class PlanningService:
    def __init__(
        self,
        store: PlanningStore,
        cache: PlanningCache | None,
        author: Author,
        clock: Callable[[], str] = utc_now_text,
    ) -> None:
        self._store = store
        self._cache = cache
        self._author = author
        self._clock = clock
        self._snapshot = PlanningSnapshot()
        self._etags: dict[DocumentKey, str] = {}
        self._outbox: dict[DocumentKey, _OutboxEntry] = {}
        self._revision = 0
        self._cursor: str | None = None
        self._undo: list[UndoEntry] = []
        self._redo: list[UndoEntry] = []
        self._subscribers: list[EventCallback] = []
        self._held: set[str] = set()
        self._deferred: dict[DocumentKey, StoredDocument] = {}
        # Remote versions of documents with a pending write, applied if the write is dropped.
        self._shadowed: dict[DocumentKey, StoredDocument] = {}
        # Cursor saved while deferred or shadowed versions wait: a restart reads them again.
        self._replay_cursor: str | None = None
        self._dirty = False
        self._pull_lock = asyncio.Lock()
        self._save_lock = asyncio.Lock()

    # ------------------------------------------------------------- state ----

    @property
    def snapshot(self) -> PlanningSnapshot:
        return self._snapshot

    @property
    def author(self) -> Author:
        return self._author

    def set_author(self, author: Author) -> None:
        self._author = author

    @property
    def cursor(self) -> str | None:
        return self._cursor

    @property
    def etags(self) -> Mapping[DocumentKey, str]:
        return MappingProxyType(self._etags)

    def etag(self, kind: DocumentKind, doc_id: str) -> str | None:
        return self._etags.get((kind, doc_id))

    @property
    def pending(self) -> int:
        """Documents with a write not yet accepted by the server."""
        return len(self._outbox)

    def is_pending(self, kind: DocumentKind, doc_id: str) -> bool:
        return (kind, doc_id) in self._outbox

    @property
    def undo_label(self) -> str | None:
        return self._undo[-1].label if self._undo else None

    @property
    def redo_label(self) -> str | None:
        return self._redo[-1].label if self._redo else None

    def subscribe(self, callback: EventCallback) -> Callable[[], None]:
        """Call ``callback`` on every event; return the function that unsubscribes."""
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return unsubscribe

    def notify(self, event: PlanningEvent) -> None:
        for callback in list(self._subscribers):
            try:
                callback(event)
            except Exception:  # noqa: BLE001 - one broken view must not stop the others
                get_logger(__name__).exception("planning.subscriber.failed", origin=event.origin)

    # ----------------------------------------------------- load and save ----

    async def load(self) -> bool:
        """Show the cached planning at once, then read the server; False when unreachable."""
        if self._cache is not None:
            self._restore(await self._cache.load())
        try:
            await self.pull()
        except (ProjectFlowError, OSError) as exc:
            # The engine retries and reports the state; the cached planning stays usable.
            get_logger(__name__).warning("planning.load.pull_failed", error=type(exc).__name__)
            return False
        await self.save()
        return True

    async def pull(self) -> None:
        """Read and apply the changes since the cursor; raises the store's errors."""
        async with self._pull_lock:
            self.apply_remote(await self._store.changes(self._cursor))

    async def save(self) -> None:
        """Write the cache when something changed since the last save."""
        if self._cache is None:
            return
        async with self._save_lock:
            if not self._dirty:
                return
            self._dirty = False
            try:
                await self._cache.save(self._cache_content())
            except OSError as exc:
                self._dirty = True
                get_logger(__name__).warning("planning.cache.save_failed", error=str(exc))
            except asyncio.CancelledError:
                self._dirty = True
                raise

    def _restore(self, content: CacheContent) -> None:
        documents: list[PlanningDocument] = []
        for kind_name, items in content.documents.items():
            kind = _KINDS.get(kind_name)
            if kind is None:
                continue
            for doc_id, cached in items.items():
                document = _read_document(kind, cached.data)
                if document is None or document.id != doc_id:
                    continue
                documents.append(document)
                if cached.etag:
                    self._etags[kind, doc_id] = cached.etag
        self._snapshot = PlanningSnapshot().apply(
            DocumentChange.creation(document) for document in documents
        )
        for write in content.outbox:
            self._restore_write(write)
        self._cursor = content.cursor
        changed = frozenset((document.kind, document.id) for document in documents)
        if changed or self._outbox:
            self.notify(PlanningEvent(changed | frozenset(self._outbox), "remote"))

    def _restore_write(self, write: CachedWrite) -> None:
        kind = _KINDS.get(write.kind)
        if kind is None:
            return
        before = _read_document(kind, write.before) if write.before is not None else None
        after = _read_document(kind, write.after) if write.after is not None else None
        if (write.before is not None and before is None) or (
            write.after is not None and after is None
        ):
            get_logger(__name__).warning("planning.cache.write_dropped", kind=kind)
            return
        if write.etag:
            self._etags[kind, write.doc_id] = write.etag
        self._revision += 1
        self._outbox[kind, write.doc_id] = _OutboxEntry(before, after, self._revision)

    def _cache_content(self) -> CacheContent:
        snapshot = self._snapshot
        documents: dict[str, dict[str, CachedDocument]] = {}
        every = (
            *snapshot.tasks.values(),
            *snapshot.resources.values(),
            *snapshot.absences.values(),
            snapshot.meta,
        )
        for document in every:
            etag = self._etags.get((document.kind, document.id), "")
            documents.setdefault(document.kind, {})[document.id] = CachedDocument(
                etag=etag, data=document.to_json()
            )
        outbox = tuple(
            CachedWrite(
                kind=kind,
                doc_id=doc_id,
                before=entry.before.to_json() if entry.before is not None else None,
                after=entry.after.to_json() if entry.after is not None else None,
                etag=self._etags.get((kind, doc_id), ""),
            )
            for (kind, doc_id), entry in self._outbox.items()
        )
        cursor = self._replay_cursor if self._unapplied else self._cursor
        return CacheContent(documents=documents, cursor=cursor, outbox=outbox)

    @property
    def _unapplied(self) -> bool:
        return bool(self._deferred or self._shadowed)

    # ----------------------------------------------------- local changes ----

    def apply(self, changes: Iterable[DocumentChange], *, label: str) -> None:
        """Apply ``changes`` now as one undo step and queue their writes.

        Only the fields a change touches are applied, onto the current version, so a
        change computed from an older snapshot never reverts what others did meanwhile.
        A change whose ``after`` is None removes the document for good (purge). When one
        change no longer fits the current version (document deleted, or its fields mixed
        with others' into an invalid document), the whole step is refused with a
        ``conflict`` event.
        """
        applied = self._apply_local(changes)
        if applied:
            self._push(self._undo, UndoEntry(label, applied))
            self._redo.clear()

    def undo(self) -> str | None:
        """Revert this poste's last step; None when done, else the reason in French."""
        return self._revert(self._undo, self._redo, verb=UNDO_VERB, empty=NOTHING_TO_UNDO)

    def redo(self) -> str | None:
        return self._revert(self._redo, self._undo, verb=REDO_VERB, empty=NOTHING_TO_REDO)

    def restore(self, document: PlanningDocument, *, label: str = RESTORE_LABEL) -> None:
        """Bring back a version lost to another poste (``PlanningEvent.restorable``)."""
        revived = document.model_copy(update={"deleted_at": None, "deleted_by": None})
        current = self._snapshot.get(document.kind, document.id)
        if current is None:
            change = DocumentChange.creation(revived)
        else:
            change = DocumentChange.update(current, revived)
        self.apply((change,), label=label)

    def hold(self, doc_id: str) -> None:
        """Defer the remote changes of a document while it is dragged."""
        self._held.add(doc_id)

    def release(self, doc_id: str) -> None:
        self._held.discard(doc_id)
        keys = [key for key in self._deferred if key[1] == doc_id]
        if not keys:
            return
        batch = _RemoteBatch()
        for key in keys:
            self._receive(self._deferred.pop(key), batch)
        self._finish(batch)

    def _apply_local(self, changes: Iterable[DocumentChange]) -> tuple[DocumentChange, ...]:
        now = self._clock()
        snapshot = self._snapshot
        applied: list[DocumentChange] = []
        refusals: list[PlanningEvent] = []
        for change in changes:
            current = snapshot.get(change.kind, change.doc_id)
            target, refusal = _rebased(change, current)
            if refusal is not None:
                refusals.append(refusal)
                continue
            after = target.stamped(self._author, now=now) if target is not None else None
            if current is None and after is None:
                continue
            effective = DocumentChange(
                kind=change.kind, doc_id=change.doc_id, before=current, after=after
            )
            if current is not None and after is not None and not effective.changed_fields():
                continue
            snapshot = snapshot.apply((effective,))
            applied.append(effective)
        if refusals:
            for event in refusals:
                self.notify(event)
            return ()
        if not applied:
            return ()
        self._snapshot = snapshot
        for effective in applied:
            self._queue(effective)
        self._dirty = True
        changed = frozenset((change.kind, change.doc_id) for change in applied)
        self.notify(PlanningEvent(changed=changed, origin="local", author=self._author))
        return tuple(applied)

    def _queue(self, change: DocumentChange) -> None:
        key = (change.kind, change.doc_id)
        entry = self._outbox.get(key)
        before = change.before if entry is None else entry.before
        self._revision += 1
        self._outbox[key] = _OutboxEntry(before, change.after, self._revision)

    def _revert(
        self, source: list[UndoEntry], target: list[UndoEntry], *, verb: str, empty: str
    ) -> str | None:
        if not source:
            return empty
        entry = source.pop()
        reverted, refusal = self._reverted(entry, verb=verb)
        if refusal is not None:
            # Dropped, so that the next attempt reaches the older steps.
            return refusal
        applied = self._apply_local(reverted.changes)
        if applied:
            self._push(target, UndoEntry(entry.label, applied, reverted.revived))
        return None

    def _reverted(self, entry: UndoEntry, *, verb: str) -> tuple[UndoEntry, str | None]:
        work = self._snapshot
        now = self._clock()
        prefix = f"{verb} « {entry.label} »"
        reverted: list[DocumentChange] = []
        revived: set[DocumentKey] = set()
        for change in reversed(entry.changes):
            key = (change.kind, change.doc_id)
            current = work.get(*key)
            refusal = _refusal(change, current, prefix)
            if refusal is not None:
                return entry, refusal
            inverse = self._inverse(change, current, now, purge=key in entry.revived)
            if inverse is None:
                # Others changed a linked field: the old values no longer fit.
                return entry, _modified_meanwhile(prefix, change.kind, current)
            if change.after is None:
                revived.add(key)
            work = work.apply((inverse,))
            reverted.append(inverse)
        return UndoEntry(entry.label, tuple(reverted), frozenset(revived)), None

    def _inverse(
        self,
        change: DocumentChange,
        current: PlanningDocument | None,
        now: str,
        *,
        purge: bool,
    ) -> DocumentChange | None:
        """Return the change that reverts ``change``; None when its old values no longer fit."""
        if current is None:
            # Only a purge leaves nothing behind (``_refusal`` checked it): create it again.
            if change.before is None:
                raise ValueError("Un changement de planning a un etat avant ou apres.")
            return DocumentChange.creation(change.before)
        if change.before is None:
            if purge:
                return DocumentChange(
                    kind=change.kind, doc_id=change.doc_id, before=current, after=None
                )
            # Undoing a creation deletes softly: other postes see it go, a redo brings it back.
            target = current.model_copy(update={"deleted_at": now, "deleted_by": self._author})
            return DocumentChange.update(current, target)
        patched = _patched(current, change.before, change.changed_fields())
        return DocumentChange.update(current, patched) if patched is not None else None

    @staticmethod
    def _push(stack: list[UndoEntry], entry: UndoEntry) -> None:
        stack.append(entry)
        del stack[:-MAX_UNDO_ENTRIES]

    # ---------------------------------------------- engine: remote reads ----

    def apply_remote(self, changes: StoreChanges) -> None:
        """Apply what ``store.changes`` returned (echoes and unreadable documents skipped).

        A full read drops the documents it does not list, except those with a pending
        write; after a restore of the server (``upload_local``) they are written back.
        """
        batch = _RemoteBatch()
        if not self._unapplied:
            self._replay_cursor = self._cursor
        seen: set[DocumentKey] = set()
        for stored in changes.documents:
            kind = _KINDS.get(stored.kind)
            if kind is not None:
                seen.add((kind, stored.doc_id))
            self._receive(stored, batch)
        if changes.full and changes.upload_local:
            self._upload_missing(seen)
        elif changes.full:
            missing = [key for key in self._etags if key not in seen and key not in self._outbox]
            for kind, doc_id in missing:
                gone = StoredDocument(kind=kind, doc_id=doc_id, etag="", data=None, deleted=True)
                self._receive(gone, batch)
        self._cursor = changes.cursor
        self._finish(batch)

    def _upload_missing(self, seen: Collection[DocumentKey]) -> None:
        """Queue as creations the documents of this poste that the restored server lost.

        A pending change of such a document becomes its creation; a pending purge has
        nothing left to remove. Versions read before the restore are forgotten, but each
        creation keeps the version it is based on: when another poste wrote the document
        back first, the creation meets that copy as a conflict and only this poste's
        pending change is merged onto it.
        """
        missing = [key for key in dict.fromkeys((*self._etags, *self._outbox)) if key not in seen]
        uploaded = 0
        for key in missing:
            self._etags.pop(key, None)
            self._shadowed.pop(key, None)
            self._deferred.pop(key, None)
            entry = self._outbox.get(key)
            if entry is not None and entry.before is None:
                continue  # never reached the server
            document = entry.after if entry is not None else self._snapshot.get(*key)
            if document is None:
                self._outbox.pop(key, None)
                continue
            base = entry.before if entry is not None else document
            self._revision += 1
            self._outbox[key] = _OutboxEntry(base, document, self._revision)
            uploaded += 1
        if missing:
            self._dirty = True
        if uploaded:
            get_logger(__name__).warning("planning.resync.upload_local", documents=uploaded)

    def _receive(self, stored: StoredDocument, batch: _RemoteBatch) -> None:
        kind = _KINDS.get(stored.kind)
        if kind is None:
            return
        key = (kind, stored.doc_id)
        if stored.doc_id in self._held:
            self._deferred[key] = stored
            return
        if stored.deleted or stored.data is None:
            self._receive_deletion(key, batch)
            return
        if self._etags.get(key) == stored.etag:
            return
        document = _read_document(kind, stored.data)
        if document is None or document.id != stored.doc_id:
            return
        if key in self._outbox:
            self._shadowed[key] = stored
            return
        batch.documents[key] = document
        batch.authors[key] = document.updated_by
        self._etags[key] = stored.etag

    def _receive_deletion(self, key: DocumentKey, batch: _RemoteBatch) -> None:
        entry = self._outbox.get(key)
        if entry is not None and entry.before is None:
            return
        self._shadowed.pop(key, None)
        self._outbox.pop(key, None)
        known = self._etags.pop(key, None) is not None
        if entry is not None and entry.after is not None:
            batch.documents[key] = None
            batch.conflicts.append(_deletion_conflict(key, entry.after))
        elif known or entry is not None:
            batch.documents[key] = None
            batch.authors[key] = None

    def _finish(self, batch: _RemoteBatch) -> None:
        changes: list[DocumentChange] = []
        for (kind, doc_id), document in batch.documents.items():
            current = self._snapshot.get(kind, doc_id)
            if current is None and document is None:
                continue
            changes.append(DocumentChange(kind=kind, doc_id=doc_id, before=current, after=document))
        if changes:
            self._snapshot = self._snapshot.apply(changes)
        if batch.documents or batch.conflicts:
            self._dirty = True
        if batch.authors:
            authors = list(batch.authors.values())
            author = authors[0] if all(other == authors[0] for other in authors) else None
            self.notify(PlanningEvent(frozenset(batch.authors), "remote", author=author))
        for event in batch.conflicts:
            self.notify(event)

    # --------------------------------------------- engine: local writes ----

    def next_write(self, *, skip: Collection[DocumentKey] = ()) -> PendingWrite | None:
        """First pending write not in ``skip``; drops the writes that no longer change anything."""
        for key, entry in list(self._outbox.items()):
            if key in skip:
                continue
            etag = self._etags.get(key)
            if _is_noop(entry, etag):
                del self._outbox[key]
                self._dirty = True
                self._settle(key)
                continue
            kind, doc_id = key
            return PendingWrite(kind, doc_id, etag, entry.before, entry.after, entry.revision)
        return None

    def write_succeeded(self, write: PendingWrite, etag: str | None) -> None:
        """Record the server's new eTag (None after a purge)."""
        key = write.key
        if write.after is None or etag is None:
            self._etags.pop(key, None)
        else:
            self._etags[key] = etag
        shadowed = self._shadowed.get(key)
        if shadowed is not None and shadowed.etag == etag:
            del self._shadowed[key]  # the echo of this write
        entry = self._outbox.get(key)
        if entry is not None:
            if entry.revision == write.revision:
                del self._outbox[key]
                # A version seen meanwhile is newer: the write was accepted on its eTag.
                self._settle(key)
            else:
                # Changed again while the write was on its way: the rest is based on it.
                self._outbox[key] = _OutboxEntry(write.after, entry.after, entry.revision)
        self._dirty = True

    def write_conflicted(
        self, write: PendingWrite, server: StoredDocument | None
    ) -> PendingWrite | None:
        """Merge the pending change onto ``server`` after a refused write; return what to send."""
        key = write.key
        entry = self._outbox.get(key)
        if entry is None:
            return None
        self._dirty = True
        self._shadowed.pop(key, None)
        if server is None or server.deleted or server.data is None:
            return self._deleted_on_server(key, entry)
        server_document = _read_document(key[0], server.data)
        if server_document is None:
            # Keeps the old eTag: this version of ProjectFlow cannot overwrite it.
            self._abandon(key, entry)
            self.notify(PlanningEvent(frozenset({key}), "conflict", UNREADABLE_SERVER_VERSION))
            return None
        self._etags[key] = server.etag
        if entry.after is None:
            # An explicit purge wins over the other poste's change.
            return self._requeue(key, server_document, None)
        if _deleted_meanwhile(entry.before, entry.after, server_document):
            del self._outbox[key]
            self._set_document(key, server_document)
            self.notify(_soft_deletion_conflict(key, entry.after, server_document))
            return None
        merged, lost = merge_documents(entry.before, entry.after, server_document)
        next_write: PendingWrite | None = None
        if DocumentChange.update(server_document, merged).changed_fields():
            merged = merged.stamped(self._author, now=self._clock())
            next_write = self._requeue(key, server_document, merged)
        else:
            del self._outbox[key]
            merged = server_document
        self._set_document(key, merged)
        other = server_document.updated_by
        if lost:
            phrase = DOCUMENT_PHRASES[key[0]]
            message = f"{_who(other, capital=True)} a modifié {phrase} entre-temps"
            # The other poste's values stay; this poste's can be reapplied over them.
            mine = _patched(merged, entry.after, lost) or entry.after
            self.notify(
                PlanningEvent(
                    frozenset({key}), "conflict", message, author=other, restorable=(mine,)
                )
            )
        else:
            self.notify(PlanningEvent(frozenset({key}), "remote", author=other))
        return next_write

    def _deleted_on_server(self, key: DocumentKey, entry: _OutboxEntry) -> PendingWrite | None:
        self._etags.pop(key, None)
        if entry.before is None and entry.after is not None:
            # A refused creation with nothing on the server: create it again.
            return self._requeue(key, None, entry.after)
        del self._outbox[key]
        if entry.after is None:
            return None
        self._set_document(key, None)
        self.notify(_deletion_conflict(key, entry.after))
        return None

    def write_rejected(self, write: PendingWrite, reason: str) -> None:
        """Give up a write the server keeps refusing: the server version shows again."""
        key = write.key
        entry = self._outbox.get(key)
        if entry is None:
            return
        self._abandon(key, entry)
        message = f"{REFUSED_BY_SERVER} {DOCUMENT_PHRASES[key[0]]} ({reason}) : {NOT_SAVED}"
        restorable = (entry.after,) if entry.after is not None else ()
        self.notify(PlanningEvent(frozenset({key}), "conflict", message, restorable=restorable))

    def _abandon(self, key: DocumentKey, entry: _OutboxEntry) -> None:
        """Drop a write that will not be sent and show the last server version known."""
        del self._outbox[key]
        self._dirty = True
        self._set_document(key, entry.before)
        self._settle(key)

    def _settle(self, key: DocumentKey) -> None:
        """Apply the remote version kept aside while ``key`` had a pending write."""
        stored = None if key in self._outbox else self._shadowed.pop(key, None)
        if stored is None:
            return
        batch = _RemoteBatch()
        self._receive(stored, batch)
        self._finish(batch)

    def _requeue(
        self, key: DocumentKey, before: PlanningDocument | None, after: PlanningDocument | None
    ) -> PendingWrite:
        self._revision += 1
        self._outbox[key] = _OutboxEntry(before, after, self._revision)
        kind, doc_id = key
        return PendingWrite(kind, doc_id, self._etags.get(key), before, after, self._revision)

    def _set_document(self, key: DocumentKey, document: PlanningDocument | None) -> None:
        kind, doc_id = key
        current = self._snapshot.get(kind, doc_id)
        if current is None and document is None:
            return
        change = DocumentChange(kind=kind, doc_id=doc_id, before=current, after=document)
        self._snapshot = self._snapshot.apply((change,))


# ------------------------------------------------------------- merging ----


def merge_documents(
    before: PlanningDocument | None,
    after: PlanningDocument,
    server: PlanningDocument,
) -> tuple[PlanningDocument, frozenset[str]]:
    """Reapply onto ``server`` the fields that ``before`` -> ``after`` changed.

    A field keeps the server's value, and is returned as lost, when the server no longer
    holds its ``before`` value and differs from ``after``. ``assignments`` merge per
    resource, so assignments added or removed on both sides commute; linked fields
    (start and end, first and last day, deletion) are kept or lost together.
    """
    touched = DocumentChange(
        kind=after.kind, doc_id=after.id, before=before, after=after
    ).changed_fields()
    theirs = server.to_json()
    merged, lost = _reapplied(touched, theirs)
    lost -= CREATION_FIELDS
    if (theirs.get("deleted_at") is None) == (after.deleted_at is None):
        lost -= DELETION_FIELDS
    for group in LINKED_FIELDS:
        if lost & group:
            for name in group & touched.keys():
                merged[name] = theirs.get(name)
                lost.add(name)
    try:
        document = document_from_json(server.kind, merged)
    except ValueError:
        return server, frozenset(touched)
    return document, frozenset(lost)


def _reapplied(
    touched: Mapping[str, tuple[Any, Any]], theirs: Mapping[str, Any]
) -> tuple[dict[str, Any], set[str]]:
    merged = dict(theirs)
    lost: set[str] = set()
    for name, (old, new) in touched.items():
        current = theirs.get(name)
        if current == new:
            continue
        if name == ASSIGNMENTS_FIELD:
            merged[name], conflicted = _merge_assignments(old, new, current)
            if conflicted:
                lost.add(name)
        elif current == old:
            merged[name] = new
        else:
            lost.add(name)
    return merged, lost


def _merge_assignments(old: object, new: object, current: object) -> tuple[dict[str, Any], bool]:
    base, mine, theirs = _as_dict(old), _as_dict(new), _as_dict(current)
    merged = dict(theirs)
    conflicted = False
    for resource_id in [*base, *(key for key in mine if key not in base)]:
        wanted = mine.get(resource_id)
        if base.get(resource_id) == wanted or theirs.get(resource_id) == wanted:
            continue
        if theirs.get(resource_id) != base.get(resource_id):
            conflicted = True
        elif wanted is None:
            merged.pop(resource_id, None)
        else:
            merged[resource_id] = wanted
    return merged, conflicted


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _patched(
    base: PlanningDocument, source: PlanningDocument, names: Iterable[str]
) -> PlanningDocument | None:
    """Copy ``base`` with the ``names`` fields of ``source``; None when the mix is invalid."""
    data = base.to_json()
    values = source.to_json()
    for name in names:
        if name in values:
            data[name] = values[name]
        else:
            data.pop(name, None)
    try:
        return document_from_json(base.kind, data)
    except ValueError:
        return None


def _is_noop(entry: _OutboxEntry, etag: str | None) -> bool:
    if entry.after is None:
        return etag is None
    if entry.before is None or etag is None:
        return False
    return not DocumentChange.update(entry.before, entry.after).changed_fields()


def _deleted_meanwhile(
    before: PlanningDocument | None, after: PlanningDocument, server: PlanningDocument
) -> bool:
    base_deletion = before.deleted_at if before is not None else None
    return (
        server.deleted_at is not None
        and after.deleted_at is None
        and server.deleted_at != base_deletion
    )


def _read_document(kind: DocumentKind, data: Mapping[str, Any]) -> PlanningDocument | None:
    try:
        return document_from_json(kind, data)
    except ValueError as exc:
        get_logger(__name__).warning("planning.document.unreadable", kind=kind, error=str(exc))
        return None


def _rebased(
    change: DocumentChange, current: PlanningDocument | None
) -> tuple[PlanningDocument | None, PlanningEvent | None]:
    """``change.after`` with its fields moved onto ``current``, or why it no longer fits."""
    after = change.after
    if after is None or change.before is None:
        return after, None
    key = (change.kind, change.doc_id)
    if current is None:
        return None, _deletion_conflict(key, after)
    if _deleted_meanwhile(change.before, after, current):
        return None, _soft_deletion_conflict(key, after, current)
    patched = _patched(current, after, change.changed_fields())
    if patched is None:
        # Only others' change of a linked field makes the mix invalid: theirs is kept.
        other = current.updated_by
        message = f"{_who(other, capital=True)} a modifié {DOCUMENT_PHRASES[key[0]]} entre-temps"
        return None, PlanningEvent(
            frozenset({key}), "conflict", f"{message} : {NOT_SAVED}", author=other
        )
    return patched, None


def _refusal(change: DocumentChange, current: PlanningDocument | None, prefix: str) -> str | None:
    """Why ``change`` cannot be reverted: someone else changed its fields since."""
    if change.after is None:
        return None if current is None else _modified_meanwhile(prefix, change.kind, current)
    if current is None:
        return f"{prefix} : {DELETED_PHRASES[change.kind]} entre-temps."
    values = current.to_json()
    if all(values.get(name) == value for name, (_, value) in change.changed_fields().items()):
        return None
    return _modified_meanwhile(prefix, change.kind, current)


def _modified_meanwhile(prefix: str, kind: DocumentKind, current: PlanningDocument | None) -> str:
    who = _who(current.updated_by if current is not None else None)
    return f"{prefix} : {who} a modifié {DOCUMENT_PHRASES[kind]} entre-temps."


def _deletion_conflict(key: DocumentKey, lost: PlanningDocument) -> PlanningEvent:
    deleted = _capitalized(DELETED_PHRASES[key[0]])
    message = f"{deleted} sur un autre poste entre-temps : {NOT_SAVED}"
    return PlanningEvent(frozenset({key}), "conflict", message, restorable=(lost,))


def _soft_deletion_conflict(
    key: DocumentKey, lost: PlanningDocument, server: PlanningDocument
) -> PlanningEvent:
    who = server.deleted_by or server.updated_by
    phrase = DOCUMENT_PHRASES[key[0]]
    message = f"{_who(who, capital=True)} a supprimé {phrase} entre-temps : {NOT_SAVED}"
    return PlanningEvent(frozenset({key}), "conflict", message, author=who, restorable=(lost,))


def _who(author: Author | None, *, capital: bool = False) -> str:
    label = author.label if author is not None else ""
    if label:
        return label
    return _capitalized(OTHER_POSTE) if capital else OTHER_POSTE


def _capitalized(text: str) -> str:
    return text[:1].upper() + text[1:]
