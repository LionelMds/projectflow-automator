from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from projectflow.core.planning.cache import (
    CACHE_FILE_NAME,
    CacheContent,
    CachedDocument,
    CachedWrite,
    PlanningCache,
)
from projectflow.core.planning.changes import DocumentChange
from projectflow.core.planning.models import (
    Absence,
    Author,
    PlanningDocument,
    PlanningTask,
    document_from_json,
)
from projectflow.core.planning.service import PlanningEvent, PlanningService, merge_documents
from projectflow.core.planning.store import (
    StoreChanges,
    StoreConflictError,
    StoredDocument,
    StoreUnavailableError,
)
from projectflow.core.planning.sync import SyncEngine
from projectflow.core.planning.walltime import wall_time
from projectflow.exceptions import GraphError

ME = Author(initials="LM", name="Lionel Mottiez", poste="BUREAU-1", poste_id="poste-1")
OTHER = Author(initials="PLA", name="Paul", poste="ATELIER-1", poste_id="poste-2")
MONDAY = date(2026, 10, 26)
MORNING = (wall_time(MONDAY, 6, 30), wall_time(MONDAY, 12, 0))
AFTERNOON = (wall_time(MONDAY, 13, 0), wall_time(MONDAY, 16, 30))
TUESDAY = (wall_time(date(2026, 10, 27), 6, 30), wall_time(date(2026, 10, 27), 12, 0))


# ------------------------------------------------------- in-memory store ----


class FakeServer:
    """The shared list; each FakeStore is one poste connected to it."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], StoredDocument] = {}
        self.positions: dict[tuple[str, str], int] = {}
        self.sequence = 0
        self._etags = itertools.count(1)

    def record(self, kind: str, doc_id: str, data: Mapping[str, Any] | None) -> StoredDocument:
        stored = StoredDocument(
            kind=kind,
            doc_id=doc_id,
            etag=f"etag-{next(self._etags)}",
            data=None if data is None else json.loads(json.dumps(data)),
            deleted=data is None,
        )
        self.sequence += 1
        self.items[kind, doc_id] = stored
        self.positions[kind, doc_id] = self.sequence
        return stored

    def live(self, kind: str, doc_id: str) -> StoredDocument | None:
        stored = self.items.get((kind, doc_id))
        return None if stored is None or stored.deleted else stored

    def document(self, kind: str, doc_id: str) -> Any:
        stored = self.live(kind, doc_id)
        assert stored is not None
        assert stored.data is not None
        return document_from_json(kind, stored.data)

    def put(self, document: PlanningDocument) -> str:
        """Write as another poste would have, whatever the eTag."""
        return self.record(document.kind, document.id, document.to_json()).etag

    def remove(self, kind: str, doc_id: str) -> None:
        """Hard deletion, reported by the change feed."""
        self.record(kind, doc_id, None)

    def forget(self, kind: str, doc_id: str) -> None:
        """Gone without trace: only a full listing tells."""
        del self.items[kind, doc_id]
        del self.positions[kind, doc_id]


class FakeStore:
    """``PlanningStore`` over a FakeServer, with failures and a gate to hold calls."""

    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self.calls: list[tuple[str, str]] = []
        self.failure: Exception | None = None
        self.rejected: set[str] = set()
        self.gate: asyncio.Event | None = None
        self.full_next = False
        self.upload_next = False
        """The next read is the full read of a restored server (resyncChangesUploadDifferences)."""

    @property
    def writes(self) -> list[str]:
        return [operation for operation, _ in self.calls if operation != "changes"]

    async def _enter(self, operation: str, doc_id: str = "") -> None:
        self.calls.append((operation, doc_id))
        if self.gate is not None:
            await self.gate.wait()
        if self.failure is not None:
            raise self.failure
        if doc_id in self.rejected and operation in {"create", "update"}:
            raise GraphError("Element refuse par SharePoint.", status_code=400)

    async def changes(self, cursor: str | None) -> StoreChanges:
        await self._enter("changes")
        server = self.server
        if cursor is None or self.full_next or self.upload_next:
            upload_local, self.full_next, self.upload_next = self.upload_next, False, False
            documents = tuple(item for item in server.items.values() if not item.deleted)
            return StoreChanges(
                documents=documents,
                cursor=str(server.sequence),
                full=True,
                upload_local=upload_local,
            )
        position = int(cursor)
        changed = sorted(
            (sequence, key) for key, sequence in server.positions.items() if sequence > position
        )
        documents = tuple(server.items[key] for _, key in changed)
        return StoreChanges(documents=documents, cursor=str(server.sequence))

    async def get(self, kind: str, doc_id: str) -> StoredDocument | None:
        await self._enter("get", doc_id)
        return self.server.live(kind, doc_id)

    async def create(
        self, kind: str, doc_id: str, data: Mapping[str, Any], *, project: str = ""
    ) -> StoredDocument:
        await self._enter("create", doc_id)
        if self.server.live(kind, doc_id) is not None:
            raise StoreConflictError("Le document existe deja.")
        return self.server.record(kind, doc_id, data)

    async def update(
        self,
        kind: str,
        doc_id: str,
        etag: str,
        data: Mapping[str, Any],
        *,
        project: str | None = None,
    ) -> str:
        await self._enter("update", doc_id)
        current = self.server.live(kind, doc_id)
        if current is None or current.etag != etag:
            raise StoreConflictError("Modifie entre-temps.")
        return self.server.record(kind, doc_id, data).etag

    async def delete(self, kind: str, doc_id: str, etag: str) -> None:
        await self._enter("delete", doc_id)
        current = self.server.live(kind, doc_id)
        if current is None:
            return
        if current.etag != etag:
            raise StoreConflictError("Modifie entre-temps.")
        self.server.remove(kind, doc_id)

    async def aclose(self) -> None:
        return None


class Recorder:
    def __init__(self) -> None:
        self.events: list[PlanningEvent] = []

    def __call__(self, event: PlanningEvent) -> None:
        self.events.append(event)

    def of(self, origin: str) -> list[PlanningEvent]:
        return [event for event in self.events if event.origin == origin]


def make_clock() -> Callable[[], str]:
    counter = itertools.count()

    def now() -> str:
        tick = next(counter)
        return f"2026-10-22T08:{tick // 60 % 60:02d}:{tick % 60:02d}Z"

    return now


def new_task(title: str = "Garde-corps", **fields: Any) -> PlanningTask:
    return PlanningTask(title=title, project_number="2026-5245", **fields)


def edited(document: PlanningDocument, **fields: Any) -> DocumentChange:
    return DocumentChange.update(document, document.model_copy(update=fields))


def task_of(service: PlanningService, task_id: str) -> PlanningTask:
    return service.snapshot.tasks[task_id]


def remote_edit(server: FakeServer, task_id: str, author: Author = OTHER, **fields: Any) -> None:
    current = server.document("task", task_id)
    server.put(current.model_copy(update=fields).stamped(author))


def new_service(
    server: FakeServer, *, author: Author = ME, cache: PlanningCache | None = None
) -> tuple[PlanningService, FakeStore]:
    store = FakeStore(server)
    return PlanningService(store, cache, author, clock=make_clock()), store


async def loaded(
    server: FakeServer, *, author: Author = ME, cache: PlanningCache | None = None
) -> tuple[PlanningService, FakeStore]:
    service, store = new_service(server, author=author, cache=cache)
    assert await service.load()
    return service, store


async def flush(service: PlanningService, store: FakeStore) -> None:
    """One synchronisation: send the outbox, then read the changes."""
    await SyncEngine(service, store).sync_once()


# ----------------------------------------------------- local changes ----


@pytest.mark.asyncio
async def test_apply_is_optimistic_and_queues_a_write() -> None:
    service, store = new_service(FakeServer())
    recorder = Recorder()
    service.subscribe(recorder)
    task = new_task()

    service.apply((DocumentChange.creation(task),), label="Nouvelle tâche")

    shown = task_of(service, task.id)
    assert shown.updated_by == ME
    assert shown.created_by == ME
    assert service.pending == 1
    assert store.calls == []
    assert recorder.events == [PlanningEvent(frozenset({("task", task.id)}), "local", author=ME)]
    write = service.next_write()
    assert write is not None
    assert (write.etag, write.before, write.after, write.project) == (
        None,
        None,
        shown,
        "2026-5245",
    )


@pytest.mark.asyncio
async def test_successive_changes_of_a_document_make_one_write() -> None:
    service, _ = new_service(FakeServer())
    task = new_task()
    service.apply((DocumentChange.creation(task),), label="Nouvelle tâche")
    service.apply((edited(task_of(service, task.id), title="Garde-corps inox"),), label="Titre")
    service.apply((edited(task_of(service, task.id), urgent=True),), label="Urgent")

    assert service.pending == 1
    write = service.next_write()
    assert write is not None
    assert write.before is None
    assert write.after == task_of(service, task.id)
    assert (write.after.title, write.after.urgent) == ("Garde-corps inox", True)


@pytest.mark.asyncio
async def test_a_change_without_effect_is_ignored() -> None:
    server = FakeServer()
    task = new_task().stamped(OTHER)
    server.put(task)
    service, _ = await loaded(server)
    recorder = Recorder()
    service.subscribe(recorder)

    service.apply((edited(task_of(service, task.id), title="Garde-corps"),), label="Titre")

    assert service.pending == 0
    assert service.undo_label is None
    assert recorder.events == []


@pytest.mark.asyncio
async def test_a_change_applies_only_its_fields_onto_the_current_version() -> None:
    server = FakeServer()
    task = new_task()
    server.put(task)
    service, _ = await loaded(server)
    stale = task_of(service, task.id)
    remote_edit(server, task.id, title="Garde-corps inox")
    await service.pull()

    service.apply((edited(stale, notes="Prévoir la nacelle"),), label="Notes")

    shown = task_of(service, task.id)
    assert (shown.title, shown.notes) == ("Garde-corps inox", "Prévoir la nacelle")


@pytest.mark.asyncio
async def test_a_purge_removes_the_document_for_good() -> None:
    server = FakeServer()
    task = new_task()
    server.put(task)
    service, store = await loaded(server)
    current = task_of(service, task.id)

    service.apply(
        (DocumentChange(kind="task", doc_id=task.id, before=current, after=None),), label="Purger"
    )
    assert task.id not in service.snapshot.tasks
    await flush(service, store)

    assert ("delete", task.id) in store.calls
    assert server.live("task", task.id) is None
    assert service.etag("task", task.id) is None

    assert service.undo() is None
    await flush(service, store)
    assert server.document("task", task.id).title == "Garde-corps"


@pytest.mark.asyncio
async def test_a_stale_change_that_no_longer_fits_is_refused() -> None:
    server = FakeServer()
    task = new_task(start=MORNING[0], end=MORNING[1])
    server.put(task)
    service, store = await loaded(server)
    recorder = Recorder()
    service.subscribe(recorder)
    stale = task_of(service, task.id)  # e.g. the task dialog opened on it
    remote_edit(
        server, task.id, start=TUESDAY[0], end=TUESDAY[1], title="Inox", assignments={"r9": {}}
    )
    await service.pull()

    # Only the end is changed: on Tuesday's start it would end before it begins.
    service.apply((edited(stale, end=wall_time(MONDAY, 9, 0)),), label="Durée")

    shown = task_of(service, task.id)
    assert (shown.start, shown.end, shown.title, shown.assignee_ids) == (*TUESDAY, "Inox", ("r9",))
    assert service.pending == 0
    assert service.undo_label is None
    [conflict] = recorder.of("conflict")
    assert conflict.message == (
        "PLA · ATELIER-1 a modifié cette tâche entre-temps : "
        "votre modification n'a pas été enregistrée."
    )
    await flush(service, store)
    assert store.writes == []


@pytest.mark.asyncio
async def test_a_stale_absence_change_keeps_the_other_postes_days() -> None:
    server = FakeServer()
    absence = Absence(resource_id="r1", first_day=MONDAY, last_day=MONDAY)
    server.put(absence)
    service, _ = await loaded(server)
    stale = service.snapshot.absences[absence.id]
    moved = {"first_day": date(2026, 11, 2), "last_day": date(2026, 11, 6), "note": "École"}
    server.put(stale.model_copy(update=moved).stamped(OTHER))
    await service.pull()

    service.apply((edited(stale, last_day=date(2026, 10, 28)),), label="Prolonger")

    shown = service.snapshot.absences[absence.id]
    assert (shown.first_day, shown.last_day, shown.note) == tuple(moved.values())
    assert service.pending == 0


@pytest.mark.asyncio
async def test_a_stale_edit_of_a_document_purged_elsewhere_refuses_the_whole_step() -> None:
    server = FakeServer()
    task = new_task()
    server.put(task)
    service, store = await loaded(server)
    recorder = Recorder()
    service.subscribe(recorder)
    stale = task_of(service, task.id)
    server.remove("task", task.id)
    await service.pull()
    part = new_task("Garde-corps, partie 2")

    service.apply((edited(stale, notes="Nacelle"), DocumentChange.creation(part)), label="Diviser")

    assert service.snapshot.tasks == {}
    assert service.pending == 0
    [conflict] = recorder.of("conflict")
    assert conflict.message.startswith("Cette tâche a été supprimée sur un autre poste")
    assert conflict.restorable[0].notes == "Nacelle"
    await flush(service, store)
    assert server.live("task", task.id) is None
    assert store.writes == []

    service.restore(conflict.restorable[0])
    await flush(service, store)
    assert server.document("task", task.id).notes == "Nacelle"


@pytest.mark.asyncio
async def test_a_stale_edit_of_a_document_deleted_elsewhere_is_refused() -> None:
    server = FakeServer()
    task = new_task()
    server.put(task)
    service, _ = await loaded(server)
    recorder = Recorder()
    service.subscribe(recorder)
    stale = task_of(service, task.id)
    remote_edit(server, task.id, deleted_at="2026-10-22T09:00:00Z", deleted_by=OTHER)
    await service.pull()

    service.apply((edited(stale, notes="Nacelle"),), label="Notes")

    assert task_of(service, task.id).notes == ""
    assert service.pending == 0
    [conflict] = recorder.of("conflict")
    assert conflict.message == (
        "PLA · ATELIER-1 a supprimé cette tâche entre-temps : "
        "votre modification n'a pas été enregistrée."
    )
    assert conflict.restorable[0].notes == "Nacelle"


# ------------------------------------------------------ undo and redo ----


@pytest.mark.asyncio
async def test_undo_and_redo_of_this_postes_steps() -> None:
    server = FakeServer()
    task = new_task(start=MORNING[0], end=MORNING[1])
    server.put(task)
    service, store = await loaded(server)
    assert service.undo() == "Rien à annuler."

    service.apply(
        (edited(task_of(service, task.id), start=AFTERNOON[0], end=AFTERNOON[1]),),
        label="Déplacer « Garde-corps »",
    )
    assert service.undo_label == "Déplacer « Garde-corps »"
    assert service.redo_label is None

    assert service.undo() is None
    assert (task_of(service, task.id).start, task_of(service, task.id).end) == MORNING
    assert service.undo_label is None
    assert service.redo_label == "Déplacer « Garde-corps »"

    assert service.redo() is None
    assert (task_of(service, task.id).start, task_of(service, task.id).end) == AFTERNOON
    assert service.redo() == "Rien à rétablir."

    await flush(service, store)
    assert server.document("task", task.id).start == AFTERNOON[0]
    assert store.writes == ["update"]


@pytest.mark.asyncio
async def test_a_new_step_clears_the_redo_stack() -> None:
    service, _ = new_service(FakeServer())
    task = new_task()
    service.apply((DocumentChange.creation(task),), label="Nouvelle tâche")
    service.apply((edited(task_of(service, task.id), notes="a"),), label="Notes")
    assert service.undo() is None
    assert service.redo_label == "Notes"

    service.apply((edited(task_of(service, task.id), urgent=True),), label="Urgent")

    assert service.redo_label is None
    assert service.undo_label == "Urgent"


@pytest.mark.asyncio
async def test_undoing_a_creation_deletes_softly_and_redo_brings_it_back() -> None:
    server = FakeServer()
    service, store = await loaded(server)
    task = new_task()
    service.apply((DocumentChange.creation(task),), label="Nouvelle tâche")
    await flush(service, store)

    assert service.undo() is None
    assert task_of(service, task.id).deleted
    assert task_of(service, task.id).deleted_by == ME
    await flush(service, store)
    assert server.document("task", task.id).deleted

    assert service.redo() is None
    assert not task_of(service, task.id).deleted
    await flush(service, store)
    assert not server.document("task", task.id).deleted


@pytest.mark.asyncio
async def test_undo_is_refused_when_someone_else_changed_the_fields() -> None:
    server = FakeServer()
    task = new_task(start=MORNING[0], end=MORNING[1])
    server.put(task)
    service, store = await loaded(server)
    service.apply(
        (edited(task_of(service, task.id), start=AFTERNOON[0], end=AFTERNOON[1]),),
        label="Déplacer",
    )
    await flush(service, store)
    remote_edit(server, task.id, start=TUESDAY[0], end=TUESDAY[1])
    await service.pull()

    message = service.undo()

    assert message == (
        "Impossible d'annuler « Déplacer » : PLA · ATELIER-1 a modifié cette tâche entre-temps."
    )
    assert task_of(service, task.id).start == TUESDAY[0]
    assert service.pending == 0
    assert service.undo_label is None


@pytest.mark.asyncio
async def test_undo_keeps_what_others_changed_in_other_fields() -> None:
    server = FakeServer()
    task = new_task(start=MORNING[0], end=MORNING[1])
    server.put(task)
    service, store = await loaded(server)
    service.apply(
        (edited(task_of(service, task.id), start=AFTERNOON[0], end=AFTERNOON[1]),),
        label="Déplacer",
    )
    await flush(service, store)
    remote_edit(server, task.id, title="Garde-corps inox")
    await service.pull()

    assert service.undo() is None

    shown = task_of(service, task.id)
    assert (shown.start, shown.end, shown.title) == (*MORNING, "Garde-corps inox")


@pytest.mark.asyncio
async def test_undo_is_refused_when_the_document_was_deleted() -> None:
    server = FakeServer()
    task = new_task()
    server.put(task)
    service, store = await loaded(server)
    service.apply((edited(task_of(service, task.id), notes="Nacelle"),), label="Notes")
    await flush(service, store)
    server.remove("task", task.id)
    await service.pull()

    assert service.undo() == (
        "Impossible d'annuler « Notes » : cette tâche a été supprimée entre-temps."
    )


@pytest.mark.asyncio
async def test_undo_is_refused_when_others_changed_a_linked_field() -> None:
    server = FakeServer()
    task = new_task(start=MORNING[0], end=wall_time(MONDAY, 9, 0))
    server.put(task)
    service, store = await loaded(server)
    service.apply((edited(task_of(service, task.id), end=AFTERNOON[1]),), label="Durée")
    await flush(service, store)
    remote_edit(server, task.id, start=AFTERNOON[0], title="Inox")
    await service.pull()

    # The old end (09:00) would come before the start set elsewhere (13:00).
    assert service.undo() == (
        "Impossible d'annuler « Durée » : PLA · ATELIER-1 a modifié cette tâche entre-temps."
    )
    shown = task_of(service, task.id)
    assert (shown.start, shown.end, shown.title) == (*AFTERNOON, "Inox")
    assert service.pending == 0


@pytest.mark.asyncio
async def test_redo_of_an_undone_purge_purges_again() -> None:
    server = FakeServer()
    task = new_task()
    server.put(task)
    service, store = await loaded(server)
    current = task_of(service, task.id)
    service.apply(
        (DocumentChange(kind="task", doc_id=task.id, before=current, after=None),), label="Purger"
    )
    await flush(service, store)
    assert service.undo() is None
    await flush(service, store)
    assert server.live("task", task.id) is not None

    assert service.redo() is None
    await flush(service, store)

    assert task.id not in service.snapshot.tasks
    assert server.live("task", task.id) is None
    assert store.writes == ["delete", "create", "delete"]
    assert service.undo_label == "Purger"


# ----------------------------------------------------- remote changes ----


@pytest.mark.asyncio
async def test_remote_changes_are_applied_with_their_author() -> None:
    server = FakeServer()
    task = new_task()
    server.put(task)
    service, _ = await loaded(server)
    recorder = Recorder()
    service.subscribe(recorder)

    remote_edit(server, task.id, title="Garde-corps inox")
    await service.pull()

    assert task_of(service, task.id).title == "Garde-corps inox"
    assert recorder.events == [
        PlanningEvent(frozenset({("task", task.id)}), "remote", author=OTHER)
    ]
    assert service.etag("task", task.id) == server.items["task", task.id].etag


@pytest.mark.asyncio
async def test_the_echo_of_an_own_write_is_ignored() -> None:
    server = FakeServer()
    service, store = await loaded(server)
    recorder = Recorder()
    service.subscribe(recorder)
    task = new_task()
    service.apply((DocumentChange.creation(task),), label="Nouvelle tâche")
    store.full_next = True  # the read after the write lists the new document again

    await flush(service, store)

    assert [event.origin for event in recorder.events] == ["local", "status", "status"]
    assert service.etag("task", task.id) == server.items["task", task.id].etag


@pytest.mark.asyncio
async def test_unreadable_remote_documents_are_skipped() -> None:
    server = FakeServer()
    service, _ = await loaded(server)
    good = new_task()
    server.put(good)
    server.record("task", "bad", {"id": "bad", "start": "lundi", "end": "mardi"})
    server.record("probe", "probe-1", {"id": "probe-1"})

    with capture_logs() as logs:
        await service.pull()

    assert set(service.snapshot.tasks) == {good.id}
    assert [log["event"] for log in logs] == ["planning.document.unreadable"]


@pytest.mark.asyncio
async def test_a_full_listing_drops_only_documents_without_pending_write() -> None:
    server = FakeServer()
    gone, edited_here = new_task("Supprimée"), new_task("Modifiée ici")
    server.put(gone)
    server.put(edited_here)
    service, store = await loaded(server)
    created_here = new_task("Créée ici")
    service.apply((edited(task_of(service, edited_here.id), notes="Nacelle"),), label="Notes")
    service.apply((DocumentChange.creation(created_here),), label="Nouvelle tâche")
    server.forget("task", gone.id)
    server.forget("task", edited_here.id)
    store.full_next = True

    await service.pull()

    assert set(service.snapshot.tasks) == {edited_here.id, created_here.id}
    assert service.pending == 2


@pytest.mark.asyncio
async def test_a_restored_server_gets_back_what_it_lost(tmp_path: Path) -> None:
    server = FakeServer()
    lost, edited_here, purged_here, kept = (
        new_task("Perdue"),
        new_task("Modifiée ici"),
        new_task("Purgée ici"),
        new_task("Gardée"),
    )
    for task in (lost, edited_here, purged_here, kept):
        server.put(task)
    service, store = await loaded(server, cache=PlanningCache(tmp_path))
    created_here = new_task("Créée ici")
    service.apply((edited(task_of(service, edited_here.id), notes="Nacelle"),), label="Notes")
    service.apply((DocumentChange.creation(created_here),), label="Nouvelle tâche")
    purged = task_of(service, purged_here.id)
    service.apply(
        (DocumentChange(kind="task", doc_id=purged.id, before=purged, after=None),),
        label="Purger",
    )
    for task in (lost, edited_here, purged_here):
        server.forget("task", task.id)
    store.upload_next = True

    with capture_logs() as logs:
        await service.pull()

    assert set(service.snapshot.tasks) == {lost.id, edited_here.id, created_here.id, kept.id}
    assert service.pending == 3
    assert service.etag("task", lost.id) is None
    assert [log["documents"] for log in logs if log["event"] == "planning.resync.upload_local"] == [
        2
    ]
    await service.save()
    restarted, restarted_store = new_service(server, cache=PlanningCache(tmp_path))
    restarted_store.failure = StoreUnavailableError("Reseau indisponible.")
    assert await restarted.load() is False
    assert restarted.pending == 3

    await flush(service, store)

    assert store.writes == ["create", "create", "create"]
    assert server.document("task", lost.id).title == "Perdue"
    assert server.document("task", edited_here.id).notes == "Nacelle"
    assert server.document("task", created_here.id).title == "Créée ici"
    assert server.live("task", purged_here.id) is None
    assert service.pending == 0
    assert service.etag("task", lost.id) == server.items["task", lost.id].etag


@pytest.mark.asyncio
async def test_a_restored_server_forgets_the_remote_versions_kept_aside() -> None:
    server = FakeServer()
    held, edited_here = new_task("Tenue"), new_task("Modifiée ici")
    server.put(held)
    server.put(edited_here)
    service, store = await loaded(server)
    service.hold(held.id)
    service.apply((edited(task_of(service, edited_here.id), notes="Nacelle"),), label="Notes")
    remote_edit(server, held.id, title="Tenue ailleurs")
    remote_edit(server, edited_here.id, title="Modifiée ailleurs")
    await service.pull()  # deferred and shadowed: both wait for this poste
    server.forget("task", held.id)
    server.forget("task", edited_here.id)
    store.upload_next = True

    await service.pull()
    service.release(held.id)
    await flush(service, store)

    assert server.document("task", held.id).title == "Tenue"
    written = server.document("task", edited_here.id)
    assert (written.title, written.notes) == ("Modifiée ici", "Nacelle")
    assert task_of(service, held.id).title == "Tenue"
    assert service.pending == 0


@pytest.mark.asyncio
async def test_a_document_written_back_first_elsewhere_gets_only_this_postes_change() -> None:
    server = FakeServer()
    lost, untouched = new_task("Perdue"), new_task("Intacte")
    server.put(lost)
    server.put(untouched)
    service, store = await loaded(server)
    recorder = Recorder()
    service.subscribe(recorder)
    service.apply((edited(task_of(service, lost.id), notes="Nacelle"),), label="Notes")
    server.forget("task", lost.id)
    server.forget("task", untouched.id)
    store.upload_next = True
    await service.pull()
    # Another poste wrote both back first, with a change of its own on the first one.
    server.put(lost.model_copy(update={"urgent": True}).stamped(OTHER))
    server.put(untouched.stamped(OTHER))

    await flush(service, store)

    written = server.document("task", lost.id)
    assert (written.title, written.notes, written.urgent) == ("Perdue", "Nacelle", True)
    assert server.document("task", untouched.id).title == "Intacte"
    assert store.writes == ["create", "get", "update", "create", "get"]
    assert (task_of(service, lost.id).notes, task_of(service, lost.id).urgent) == ("Nacelle", True)
    assert recorder.of("conflict") == []
    assert service.pending == 0
    assert service.etag("task", untouched.id) == server.items["task", untouched.id].etag


@pytest.mark.asyncio
async def test_an_ordinary_full_read_never_writes_back_what_it_misses() -> None:
    server = FakeServer()
    gone = new_task("Supprimée")
    server.put(gone)
    service, store = await loaded(server)
    server.forget("task", gone.id)
    store.full_next = True

    await service.pull()
    await flush(service, store)

    assert gone.id not in service.snapshot.tasks
    assert store.writes == []


@pytest.mark.asyncio
async def test_a_held_document_gets_its_remote_changes_on_release() -> None:
    server = FakeServer()
    task = new_task()
    server.put(task)
    service, _ = await loaded(server)
    recorder = Recorder()
    service.subscribe(recorder)

    service.hold(task.id)
    remote_edit(server, task.id, title="Garde-corps inox")
    await service.pull()
    assert task_of(service, task.id).title == "Garde-corps"
    assert recorder.events == []

    service.release(task.id)

    assert task_of(service, task.id).title == "Garde-corps inox"
    assert recorder.events == [
        PlanningEvent(frozenset({("task", task.id)}), "remote", author=OTHER)
    ]


@pytest.mark.asyncio
async def test_remote_changes_of_a_held_document_survive_a_restart(tmp_path: Path) -> None:
    server = FakeServer()
    held, other = new_task("Tenue"), new_task("Autre")
    server.put(held)
    server.put(other)
    service, _ = await loaded(server, cache=PlanningCache(tmp_path))
    service.hold(held.id)
    remote_edit(server, held.id, title="Tenue renommée")
    remote_edit(server, other.id, title="Autre renommée")
    await service.pull()
    await service.save()  # ProjectFlow closes during the drag

    restarted, _ = await loaded(server, cache=PlanningCache(tmp_path))

    assert task_of(restarted, held.id).title == "Tenue renommée"
    assert task_of(restarted, other.id).title == "Autre renommée"


@pytest.mark.asyncio
async def test_a_remote_version_kept_aside_shows_when_the_write_becomes_needless() -> None:
    server = FakeServer()
    task = new_task().stamped(OTHER)  # an unstamped one gets its creation fields filled
    server.put(task)
    service, store = await loaded(server)
    service.apply((edited(task_of(service, task.id), title="Inox"),), label="Titre")
    remote_edit(server, task.id, notes="Nacelle")
    await service.pull()
    assert task_of(service, task.id).notes == ""  # kept aside: the task has a pending write
    recorder = Recorder()
    service.subscribe(recorder)

    assert service.undo() is None
    await flush(service, store)

    shown = task_of(service, task.id)
    assert (shown.title, shown.notes) == ("Garde-corps", "Nacelle")
    assert service.etag("task", task.id) == server.items["task", task.id].etag
    assert store.writes == []
    assert service.pending == 0
    assert PlanningEvent(frozenset({("task", task.id)}), "remote", author=OTHER) in (
        recorder.events
    )


@pytest.mark.asyncio
async def test_a_remote_version_read_while_a_write_is_on_its_way_shows_after_it() -> None:
    server = FakeServer()
    task = new_task()
    server.put(task)
    service, _ = await loaded(server)
    service.apply((edited(task_of(service, task.id), title="Inox"),), label="Titre")
    write = service.next_write()
    assert write is not None
    assert write.after is not None
    accepted = server.put(write.after)  # the server took the write, its answer is on its way
    remote_edit(server, task.id, notes="Nacelle")
    await service.pull()

    service.write_succeeded(write, accepted)

    shown = task_of(service, task.id)
    assert (shown.title, shown.notes) == ("Inox", "Nacelle")
    assert service.etag("task", task.id) == server.items["task", task.id].etag


@pytest.mark.asyncio
async def test_a_remote_deletion_wins_over_a_pending_edit_and_can_be_restored() -> None:
    server = FakeServer()
    task = new_task()
    server.put(task)
    service, store = await loaded(server)
    recorder = Recorder()
    service.subscribe(recorder)
    service.apply((edited(task_of(service, task.id), notes="Nacelle"),), label="Notes")
    server.remove("task", task.id)

    await service.pull()

    assert task.id not in service.snapshot.tasks
    assert service.pending == 0
    conflict = recorder.of("conflict")[-1]
    assert conflict.message == (
        "Cette tâche a été supprimée sur un autre poste entre-temps : "
        "votre modification n'a pas été enregistrée."
    )
    assert conflict.restorable[0].notes == "Nacelle"

    service.restore(conflict.restorable[0])
    await flush(service, store)

    assert server.document("task", task.id).notes == "Nacelle"


# ------------------------------------------------------------ merging ----


def test_merge_keeps_both_sides_when_the_fields_differ() -> None:
    base = new_task()
    mine = base.model_copy(update={"title": "Garde-corps inox"})
    theirs = base.model_copy(update={"notes": "Nacelle", "updated_by": OTHER})

    merged, lost = merge_documents(base, mine, theirs)

    assert isinstance(merged, PlanningTask)
    assert (merged.title, merged.notes, lost) == ("Garde-corps inox", "Nacelle", frozenset())


def test_merge_keeps_the_server_value_of_a_field_changed_on_both_sides() -> None:
    base = new_task()
    mine = base.model_copy(update={"title": "Acier", "urgent": True})
    theirs = base.model_copy(update={"title": "Inox"})

    merged, lost = merge_documents(base, mine, theirs)

    assert isinstance(merged, PlanningTask)
    assert (merged.title, merged.urgent, lost) == ("Inox", True, frozenset({"title"}))


def test_merge_combines_assignments_per_resource() -> None:
    base = new_task(assignments={"r1": {}, "r2": {}})
    mine = base.model_copy(update={"assignments": {"r2": {}}})
    theirs = base.model_copy(update={"assignments": {"r1": {}, "r2": {}, "r3": {}}})

    merged, lost = merge_documents(base, mine, theirs)

    assert isinstance(merged, PlanningTask)
    assert (merged.assignee_ids, lost) == (("r2", "r3"), frozenset())


def test_merge_reports_an_assignment_changed_on_both_sides() -> None:
    base = new_task(assignments={"r1": {"hours": 4}})
    mine = base.model_copy(update={"assignments": {"r1": {"hours": 2}, "r4": {}}})
    theirs = base.model_copy(update={"assignments": {"r1": {"hours": 8}}})

    merged, lost = merge_documents(base, mine, theirs)

    assert isinstance(merged, PlanningTask)
    assert merged.assignments == {"r1": {"hours": 8}, "r4": {}}
    assert lost == frozenset({"assignments"})


def test_merge_keeps_start_and_end_together() -> None:
    base = new_task(start=MORNING[0], end=MORNING[1])
    mine = base.model_copy(update={"start": AFTERNOON[0], "end": AFTERNOON[1]})
    theirs = base.model_copy(update={"end": wall_time(MONDAY, 9, 0)})

    merged, lost = merge_documents(base, mine, theirs)

    assert isinstance(merged, PlanningTask)
    assert (merged.start, merged.end) == (MORNING[0], wall_time(MONDAY, 9, 0))
    assert lost == frozenset({"start", "end"})


def test_merge_does_not_report_the_same_deletion_twice() -> None:
    base = new_task()
    mine = base.model_copy(update={"deleted_at": "2026-10-22T08:00:00Z", "deleted_by": ME})
    theirs = base.model_copy(update={"deleted_at": "2026-10-22T08:05:00Z", "deleted_by": OTHER})

    merged, lost = merge_documents(base, mine, theirs)

    assert (merged.deleted_at, lost) == ("2026-10-22T08:05:00Z", frozenset())


# ------------------------------------------------------ cache and load ----


@pytest.mark.asyncio
async def test_the_cache_round_trips(tmp_path: Path) -> None:
    cache = PlanningCache(tmp_path / "planning")
    content = CacheContent(
        documents={"task": {"t1": CachedDocument(etag="etag-1", data={"id": "t1", "title": "A"})}},
        cursor="7",
        outbox=(
            CachedWrite(kind="task", doc_id="t1", before={"id": "t1"}, after=None, etag="e-1"),
        ),
    )

    await cache.save(content)

    assert await cache.load() == content
    assert [path.name for path in (tmp_path / "planning").iterdir()] == [CACHE_FILE_NAME]
    await cache.clear()
    assert await cache.load() == CacheContent()


@pytest.mark.asyncio
async def test_a_corrupt_cache_starts_empty(tmp_path: Path) -> None:
    (tmp_path / CACHE_FILE_NAME).write_text('{"documents": {"task": 3', encoding="utf-8")

    with capture_logs() as logs:
        content = await PlanningCache(tmp_path).load()

    assert content == CacheContent()
    assert [log["event"] for log in logs] == ["planning.cache.corrupt"]


@pytest.mark.asyncio
async def test_load_shows_the_cache_when_the_server_is_unreachable(tmp_path: Path) -> None:
    server = FakeServer()
    task = new_task()
    server.put(task)
    first, _ = await loaded(server, cache=PlanningCache(tmp_path))
    assert task.id in first.snapshot.tasks
    remote_edit(server, task.id, title="Garde-corps inox")

    service, store = new_service(server, cache=PlanningCache(tmp_path))
    store.failure = StoreUnavailableError("Reseau indisponible.")
    assert await service.load() is False
    assert task_of(service, task.id).title == "Garde-corps"

    store.failure = None
    await service.pull()
    assert task_of(service, task.id).title == "Garde-corps inox"
    assert store.calls == [("changes", ""), ("changes", "")]
