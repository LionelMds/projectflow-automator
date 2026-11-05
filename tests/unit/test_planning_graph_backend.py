from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx
import pytest

from projectflow.core.planning.changes import DocumentChange
from projectflow.core.planning.graph_backend import GraphPlanningBackend
from projectflow.core.planning.models import Author, PlanningTask
from projectflow.core.planning.service import PlanningEvent, PlanningService
from projectflow.core.planning.store import (
    PlanningStore,
    StoreConflictError,
    StoredDocument,
    StoreUnavailableError,
)
from projectflow.core.planning.sync import SyncEngine
from projectflow.exceptions import AuthError, GraphError
from projectflow.graph.client import GraphClient
from projectflow.graph.planning_store import (
    RESYNC_APPLY_DIFFERENCES,
    RESYNC_UPLOAD_DIFFERENCES,
    GraphListPlanningStore,
    PlanningChanges,
    PlanningConflictError,
    PlanningListRef,
    PlanningRecord,
)


class FakeListStore:
    """In-memory SharePoint list with delta links ``delta-<version>``."""

    def __init__(self) -> None:
        self.items: dict[str, PlanningRecord] = {}
        self.changed: list[tuple[int, str]] = []
        self.version = 0
        self.next_id = 1
        self.calls: list[tuple[str, Any]] = []
        self.failures: list[Exception] = []
        self.resync_reason = ""
        self.closed = False

    # -- helpers for the tests, acting as another poste --------------------
    def put(
        self,
        kind: str,
        doc_id: str,
        data: dict[str, Any] | None,
        *,
        project: str = "",
    ) -> PlanningRecord:
        item_id = str(self.next_id)
        self.next_id += 1
        record = PlanningRecord(
            item_id=item_id,
            etag=f'"{item_id},1"',
            kind=kind,
            doc_id=doc_id,
            data=data,
            project=project,
        )
        self.items[item_id] = record
        self._touch(item_id)
        return record

    def edit(self, item_id: str, data: dict[str, Any]) -> PlanningRecord:
        current = self.items[item_id]
        version = int(current.etag.strip('"').split(",")[1]) + 1
        record = PlanningRecord(
            item_id=item_id,
            etag=f'"{item_id},{version}"',
            kind=current.kind,
            doc_id=current.doc_id,
            data=data,
            project=current.project,
        )
        self.items[item_id] = record
        self._touch(item_id)
        return record

    def remove(self, item_id: str) -> None:
        del self.items[item_id]
        self._touch(item_id)

    def _touch(self, item_id: str) -> None:
        self.version += 1
        self.changed.append((self.version, item_id))

    def _fail(self) -> None:
        if self.failures:
            raise self.failures.pop(0)

    # -- PlanningListStore --------------------------------------------------
    async def changes(self, delta_link: str | None = None) -> PlanningChanges:
        self.calls.append(("changes", delta_link))
        self._fail()
        link = f"delta-{self.version}"
        if delta_link is None or self.resync_reason:
            # A delta link refused with 410 Gone: Microsoft asks for a full enumeration.
            return PlanningChanges(
                records=tuple(self.items.values()),
                delta_link=link,
                full=True,
                resynced=bool(self.resync_reason),
                resync_reason=self.resync_reason if delta_link is not None else "",
            )
        since = int(delta_link.removeprefix("delta-"))
        ids = list(dict.fromkeys(item for version, item in self.changed if version > since))
        records = tuple(
            self.items.get(item_id)
            or PlanningRecord(item_id=item_id, etag="", kind="", doc_id="", data=None, deleted=True)
            for item_id in ids
        )
        return PlanningChanges(records=records, delta_link=link)

    async def get(self, item_id: str) -> PlanningRecord | None:
        self.calls.append(("get", item_id))
        self._fail()
        return self.items.get(item_id)

    async def create(
        self,
        kind: str,
        doc_id: str,
        data: Mapping[str, Any],
        project: str = "",
    ) -> PlanningRecord:
        self.calls.append(("create", (kind, doc_id, project)))
        self._fail()
        return self.put(kind, doc_id, dict(data), project=project)

    async def update(
        self,
        record_or_item_id: PlanningRecord | str,
        etag: str,
        data: Mapping[str, Any],
        project: str | None = None,
    ) -> str:
        assert isinstance(record_or_item_id, str)
        self.calls.append(("update", (record_or_item_id, etag, project)))
        self._fail()
        current = self.items.get(record_or_item_id)
        if current is None:
            raise GraphError("Element introuvable.", status_code=404)
        if current.etag != etag:
            raise PlanningConflictError
        return self.edit(record_or_item_id, dict(data)).etag

    async def delete(self, item_id: str, etag: str) -> None:
        self.calls.append(("delete", (item_id, etag)))
        self._fail()
        current = self.items.get(item_id)
        if current is None:
            return
        if current.etag != etag:
            raise PlanningConflictError
        self.remove(item_id)

    async def aclose(self) -> None:
        self.closed = True


def _calls(fake: FakeListStore, name: str) -> list[Any]:
    return [argument for call, argument in fake.calls if call == name]


@pytest.mark.asyncio
async def test_full_read_converts_records_and_skips_probes() -> None:
    fake = FakeListStore()
    task = fake.put("task", "t1", {"title": "A"}, project="2026-5245")
    fake.put("probe", "p1", {"n": 1})
    fake.put("meta", "meta", {"default_canton": "GE"})
    backend = GraphPlanningBackend(fake)

    changes = await backend.changes(None)

    assert changes.full is True
    assert changes.cursor == "delta-3"
    assert changes.documents == (
        StoredDocument(kind="task", doc_id="t1", etag=task.etag, data={"title": "A"}),
        StoredDocument(kind="meta", doc_id="meta", etag='"3,1"', data={"default_canton": "GE"}),
    )
    assert changes.upload_local is False


@pytest.mark.asyncio
async def test_incremental_read_places_deletions_with_the_learned_map() -> None:
    fake = FakeListStore()
    task = fake.put("task", "t1", {"title": "A"})
    resource = fake.put("resource", "r1", {"name": "Marc"})
    backend = GraphPlanningBackend(fake)
    cursor = (await backend.changes(None)).cursor

    edited = fake.edit(task.item_id, {"title": "B"})
    fake.remove(resource.item_id)
    changes = await backend.changes(cursor)

    assert changes.full is False
    assert changes.cursor == "delta-4"
    assert changes.documents == (
        StoredDocument(kind="task", doc_id="t1", etag=edited.etag, data={"title": "B"}),
        StoredDocument(kind="resource", doc_id="r1", etag="", data=None, deleted=True),
    )
    assert _calls(fake, "changes") == [None, cursor]


@pytest.mark.asyncio
async def test_unknown_deletion_after_restart_reads_everything() -> None:
    fake = FakeListStore()
    fake.put("task", "t1", {"title": "A"})
    gone = fake.put("task", "t2", {"title": "B"})
    saved_cursor = f"delta-{fake.version}"
    fake.remove(gone.item_id)
    backend = GraphPlanningBackend(fake)

    changes = await backend.changes(saved_cursor)

    assert changes.full is True
    assert [document.doc_id for document in changes.documents] == ["t1"]
    assert _calls(fake, "changes") == [saved_cursor, None]


@pytest.mark.asyncio
async def test_deletion_of_an_item_never_seen_is_ignored_once_the_map_is_complete() -> None:
    fake = FakeListStore()
    backend = GraphPlanningBackend(fake)
    cursor = (await backend.changes(None)).cursor
    short_lived = fake.put("task", "t1", {"title": "A"})
    fake.remove(short_lived.item_id)

    changes = await backend.changes(cursor)

    assert changes.documents == ()
    assert changes.full is False


@pytest.mark.asyncio
async def test_unknown_document_triggers_a_single_full_refresh() -> None:
    fake = FakeListStore()
    task = fake.put("task", "t1", {"title": "A"})
    resource = fake.put("resource", "r1", {"name": "Marc"})
    backend = GraphPlanningBackend(fake)

    current = await backend.get("task", "t1")
    etag = await backend.update("resource", "r1", resource.etag, {"name": "Marc B."})
    missing = await backend.get("task", "nothing")
    await backend.delete("task", "nothing", "x")

    assert current == StoredDocument(kind="task", doc_id="t1", etag=task.etag, data={"title": "A"})
    assert etag == '"2,2"'
    assert missing is None
    assert _calls(fake, "changes") == [None]
    assert _calls(fake, "get") == [task.item_id]
    with pytest.raises(StoreConflictError):
        await backend.update("task", "nothing", "x", {"title": "?"})


@pytest.mark.asyncio
async def test_refresh_for_a_write_after_restart_is_handed_over_as_a_full_read() -> None:
    fake = FakeListStore()
    keep = fake.put("task", "keep", {"title": "A"})
    gone = fake.put("task", "gone", {"title": "B"})
    saved_cursor = (await GraphPlanningBackend(fake).changes(None)).cursor
    # Restart; another poste purges "gone", then the outbox sends an edit before any pull.
    fake.remove(gone.item_id)
    backend = GraphPlanningBackend(fake)
    etag = await backend.update("task", "keep", keep.etag, {"title": "A2"})
    other = fake.put("task", "other", {"title": "C"})

    changes = await backend.changes(saved_cursor)
    after = await backend.changes(changes.cursor)

    assert changes.full is True
    assert changes.documents == (
        StoredDocument(kind="task", doc_id="keep", etag=etag, data={"title": "A2"}),
        StoredDocument(kind="task", doc_id="other", etag=other.etag, data={"title": "C"}),
    )
    assert changes.cursor == f"delta-{fake.version}"
    assert after.full is False
    assert after.documents == ()
    refresh_link = "delta-3"
    assert _calls(fake, "changes") == [None, None, refresh_link, changes.cursor]


@pytest.mark.asyncio
async def test_refresh_does_not_hide_the_purge_of_an_item_learned_incrementally() -> None:
    fake = FakeListStore()
    fake.put("task", "a", {"title": "A"})
    backend = GraphPlanningBackend(fake)
    cursor = f"delta-{fake.version}"
    known = fake.put("task", "known", {"title": "K"})
    learned = await backend.changes(cursor)
    fake.remove(known.item_id)

    assert await backend.get("task", "a") is not None
    changes = await backend.changes(learned.cursor)

    assert [document.doc_id for document in learned.documents] == ["known"]
    assert changes.full is True
    assert [document.doc_id for document in changes.documents] == ["a"]


@pytest.mark.asyncio
async def test_refresh_stays_pending_when_the_handover_fails() -> None:
    fake = FakeListStore()
    task = fake.put("task", "t1", {"title": "A"})
    backend = GraphPlanningBackend(fake)
    await backend.update("task", "t1", task.etag, {"title": "B"})
    fake.failures.append(GraphError("Service indisponible.", status_code=503))

    with pytest.raises(StoreUnavailableError):
        await backend.changes("delta-1")
    changes = await backend.changes("delta-1")

    assert changes.full is True
    assert [document.data for document in changes.documents] == [{"title": "B"}]


@pytest.mark.asyncio
async def test_create_maps_the_item_and_fills_the_project_column() -> None:
    fake = FakeListStore()
    backend = GraphPlanningBackend(fake)
    await backend.changes(None)

    created = await backend.create("task", "t1", {"title": "A", "project_number": " 2026-5245 "})
    explicit = await backend.create("task", "t2", {"project_number": "2026-1"}, project="2026-9")
    resource = await backend.create("resource", "r1", {"name": "Marc"})
    task_etag = await backend.update(
        "task",
        "t1",
        created.etag,
        {"title": "B", "project_number": "2026-5300"},
    )
    await backend.update("resource", "r1", resource.etag, {"name": "Marc B."})
    await backend.delete("task", "t2", explicit.etag)

    assert created == StoredDocument(
        kind="task",
        doc_id="t1",
        etag='"1,1"',
        data={"title": "A", "project_number": " 2026-5245 "},
    )
    assert _calls(fake, "create") == [
        ("task", "t1", "2026-5245"),
        ("task", "t2", "2026-9"),
        ("resource", "r1", ""),
    ]
    assert _calls(fake, "update") == [
        ("1", created.etag, "2026-5300"),
        ("3", resource.etag, None),
    ]
    assert task_etag == '"1,2"'
    assert _calls(fake, "delete") == [("2", explicit.etag)]
    assert _calls(fake, "changes") == [None]
    with pytest.raises(StoreConflictError):
        await backend.create("task", "t1", {"title": "again"})
    with pytest.raises(ValueError, match="inconnu"):
        await backend.create("probe", "p1", {})
    assert len(_calls(fake, "create")) == 3


@pytest.mark.asyncio
async def test_conflicts_and_server_deletions_become_store_conflicts() -> None:
    fake = FakeListStore()
    task = fake.put("task", "t1", {"title": "A"})
    backend = GraphPlanningBackend(fake)
    await backend.changes(None)
    fake.edit(task.item_id, {"title": "other poste"})

    with pytest.raises(StoreConflictError, match="412"):
        await backend.update("task", "t1", task.etag, {"title": "mine"})
    with pytest.raises(StoreConflictError):
        await backend.delete("task", "t1", task.etag)

    fake.remove(task.item_id)
    with pytest.raises(StoreConflictError, match="supprime"):
        await backend.update("task", "t1", task.etag, {"title": "mine"})
    assert await backend.get("task", "t1") is None
    assert len(_calls(fake, "update")) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        GraphError("Microsoft Graph ne repond pas assez vite.", status_code=None),
        GraphError("Connexion Microsoft requise.", status_code=401),
        GraphError("Acces refuse.", status_code=403),
        GraphError("Trop de requetes.", status_code=429),
        GraphError("Service indisponible.", status_code=503),
        AuthError("Connexion Microsoft requise."),
    ],
)
async def test_unreachable_server_becomes_store_unavailable(failure: Exception) -> None:
    fake = FakeListStore()
    task = fake.put("task", "t1", {"title": "A"})
    backend = GraphPlanningBackend(fake)
    await backend.changes(None)

    for operation in (
        lambda: backend.changes("delta-1"),
        lambda: backend.get("task", "t1"),
        lambda: backend.create("task", "t2", {}),
        lambda: backend.update("task", "t1", task.etag, {}),
        lambda: backend.delete("task", "t1", task.etag),
    ):
        fake.failures.append(failure)
        with pytest.raises(StoreUnavailableError) as raised:
            await operation()
        assert str(raised.value) == str(failure)
        assert raised.value.__cause__ is failure


@pytest.mark.asyncio
async def test_other_graph_errors_are_not_hidden() -> None:
    fake = FakeListStore()
    task = fake.put("task", "t1", {"title": "A"})
    backend = GraphPlanningBackend(fake)
    await backend.changes(None)
    fake.failures.append(GraphError("Requete invalide.", status_code=400))

    with pytest.raises(GraphError, match="invalide"):
        await backend.update("task", "t1", task.etag, {})


@pytest.mark.asyncio
async def test_refused_cursor_falls_back_to_a_full_read() -> None:
    fake = FakeListStore()
    fake.put("task", "t1", {"title": "A"})
    backend = GraphPlanningBackend(fake)
    fake.failures.append(GraphError("Jeton de suivi invalide.", status_code=400))

    changes = await backend.changes("delta-stale")

    assert changes.full is True
    assert [document.doc_id for document in changes.documents] == ["t1"]
    assert _calls(fake, "changes") == ["delta-stale", None]


@pytest.mark.asyncio
async def test_duplicate_items_keep_the_oldest_one() -> None:
    fake = FakeListStore()
    oldest = fake.put("meta", "meta", {"from": "poste A"})
    duplicate = fake.put("meta", "meta", {"from": "poste B"})
    backend = GraphPlanningBackend(fake)

    full = await backend.changes(None)
    fake.edit(duplicate.item_id, {"from": "poste B again"})
    fake.remove(duplicate.item_id)
    since = await backend.changes(full.cursor)
    etag = await backend.update("meta", "meta", oldest.etag, {"from": "poste C"})

    assert [document.data for document in full.documents] == [{"from": "poste A"}]
    assert since.documents == ()
    assert _calls(fake, "update") == [(oldest.item_id, oldest.etag, None)]
    assert etag == '"1,2"'


@pytest.mark.asyncio
async def test_unreadable_record_is_skipped_but_can_be_rewritten() -> None:
    fake = FakeListStore()
    broken = fake.put("task", "t1", None)
    backend = GraphPlanningBackend(fake)

    changes = await backend.changes(None)
    etag = await backend.update("task", "t1", broken.etag, {"title": "repaired"})

    assert changes.documents == ()
    assert etag == '"1,2"'
    assert _calls(fake, "changes") == [None]


@pytest.mark.asyncio
async def test_full_read_forgets_items_that_disappeared_without_notice() -> None:
    fake = FakeListStore()
    gone = fake.put("task", "t1", {"title": "A"})
    backend = GraphPlanningBackend(fake)
    await backend.changes(None)
    # A restore on the server: the item vanished and no delta reported it.
    del fake.items[gone.item_id]
    fake.resync_reason = RESYNC_UPLOAD_DIFFERENCES

    changes = await backend.changes("delta-1")
    recreated = await backend.create("task", "t1", {"title": "A"})

    assert changes.documents == ()
    assert (changes.full, changes.upload_local) == (True, True)
    assert recreated.etag == '"2,1"'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "full", "upload"),
    [
        (RESYNC_UPLOAD_DIFFERENCES, True, True),
        (RESYNC_APPLY_DIFFERENCES, True, False),
        ("", False, False),
    ],
)
async def test_only_an_upload_resync_asks_to_write_local_documents_again(
    reason: str, *, full: bool, upload: bool
) -> None:
    fake = FakeListStore()
    fake.put("task", "t1", {"title": "A"})
    backend = GraphPlanningBackend(fake)
    cursor = (await backend.changes(None)).cursor

    fake.resync_reason = reason
    resynced = await backend.changes(cursor)
    fake.resync_reason = ""
    after = await backend.changes(resynced.cursor)

    assert (resynced.full, resynced.upload_local) == (full, upload)
    assert (after.full, after.upload_local) == (False, False)


@pytest.mark.asyncio
async def test_documents_lost_by_a_server_restore_are_written_back() -> None:
    fake = FakeListStore()
    backend = GraphPlanningBackend(fake)
    service = PlanningService(backend, None, Author(initials="LM", poste="BUREAU-1"))
    assert await service.load()
    kept, lost = PlanningTask(title="Gardée"), PlanningTask(title="Perdue")
    service.apply((DocumentChange.creation(kept), DocumentChange.creation(lost)), label="Ajout")
    engine = SyncEngine(service, backend)
    await engine.sync_once()
    lost_item = next(record for record in fake.items.values() if record.doc_id == lost.id)
    # The site is restored to a point without that item; the delta link is refused.
    del fake.items[lost_item.item_id]
    fake.resync_reason = RESYNC_UPLOAD_DIFFERENCES

    await engine.sync_once()
    fake.resync_reason = ""
    queued = service.pending
    await engine.sync_once()

    assert queued == 1
    assert set(service.snapshot.tasks) == {kept.id, lost.id}
    stored = [record for record in fake.items.values() if record.doc_id == lost.id]
    assert len(stored) == 1
    assert stored[0].data is not None
    assert stored[0].data["title"] == "Perdue"
    assert stored[0].item_id != lost_item.item_id
    assert service.etag("task", lost.id) == stored[0].etag
    assert service.pending == 0
    assert [kind for kind, _, _ in _calls(fake, "create")] == ["task", "task", "task"]


RESTORE_SEEN = "2026-10-22T11:00:00Z"


async def _restored(fake: FakeListStore) -> tuple[GraphPlanningBackend, str]:
    """Backend that saw a restore of the server at ``RESTORE_SEEN``, and its cursor."""
    backend = GraphPlanningBackend(fake, clock=lambda: RESTORE_SEEN)
    cursor = (await backend.changes(None)).cursor
    fake.resync_reason = RESYNC_UPLOAD_DIFFERENCES
    cursor = (await backend.changes(cursor)).cursor
    fake.resync_reason = ""
    fake.calls.clear()
    return backend, cursor


@pytest.mark.asyncio
async def test_after_a_restore_an_older_document_is_looked_up_before_its_creation() -> None:
    fake = FakeListStore()
    backend, cursor = await _restored(fake)
    written_back = fake.put("task", "t-a", {"title": "Ecrite par un autre poste"})

    with pytest.raises(StoreConflictError):
        await backend.create("task", "t-a", {"created_at": "2026-10-20T08:00:00Z"})
    await backend.create("task", "t-b", {"created_at": "2026-10-21T08:00:00Z"})
    await backend.create("task", "t-new", {"created_at": "2026-10-22T11:00:01Z"})
    found = await backend.get("task", "t-a")
    handed = await backend.changes(cursor)

    # A full read for the first one, the delta since for the next, nothing for a new one.
    assert [call for call, _ in fake.calls] == [
        "changes",
        "changes",
        "create",
        "create",
        "get",
        "changes",
    ]
    assert _calls(fake, "changes") == [None, "delta-1", "delta-1"]
    assert [doc_id for _, doc_id, _ in _calls(fake, "create")] == ["t-b", "t-new"]
    assert found is not None
    assert found.etag == written_back.etag
    assert (handed.full, handed.upload_local) == (True, False)
    assert {document.doc_id for document in handed.documents} == {"t-a", "t-b", "t-new"}


@pytest.mark.asyncio
async def test_without_a_restore_a_creation_is_sent_at_once() -> None:
    fake = FakeListStore()
    backend = GraphPlanningBackend(fake, clock=lambda: RESTORE_SEEN)
    await backend.changes(None)
    fake.calls.clear()

    await backend.create("task", "t1", {"created_at": "2026-10-01T08:00:00Z"})

    assert [call for call, _ in fake.calls] == ["create"]


@pytest.mark.asyncio
async def test_a_restore_seen_by_a_read_for_a_write_still_asks_to_upload() -> None:
    fake = FakeListStore()
    backend, cursor = await _restored(fake)
    await backend.create("task", "t-a", {"created_at": "2026-10-20T08:00:00Z"})
    fake.resync_reason = RESYNC_UPLOAD_DIFFERENCES  # restored again meanwhile
    await backend.create("task", "t-b", {"created_at": "2026-10-20T08:00:00Z"})
    fake.resync_reason = ""

    handed = await backend.changes(cursor)

    assert (handed.full, handed.upload_local) == (True, True)


@pytest.mark.asyncio
async def test_postes_writing_back_a_restored_document_leave_a_single_item() -> None:
    fake = FakeListStore()
    postes: list[tuple[PlanningService, SyncEngine, list[PlanningEvent]]] = []
    for initials in ("AA", "BB"):
        backend = GraphPlanningBackend(fake)
        service = PlanningService(backend, None, Author(initials=initials, poste=initials))
        assert await service.load()
        events: list[PlanningEvent] = []
        service.subscribe(events.append)
        postes.append((service, SyncEngine(service, backend), events))
    (first, first_engine, _), (second, second_engine, second_events) = postes
    lost = PlanningTask(title="Perdue")
    first.apply((DocumentChange.creation(lost),), label="Ajout")
    await first_engine.sync_once()
    await second_engine.sync_once()
    lost_item = next(record for record in fake.items.values() if record.doc_id == lost.id)
    del fake.items[lost_item.item_id]
    fake.resync_reason = RESYNC_UPLOAD_DIFFERENCES
    await first.pull()
    await second.pull()
    fake.resync_reason = ""
    seen = second.snapshot.tasks[lost.id]
    second.apply(
        (DocumentChange.update(seen, seen.model_copy(update={"notes": "Nacelle"})),),
        label="Notes",
    )

    await first_engine.sync_once()
    await second_engine.sync_once()
    await first_engine.sync_once()

    stored = [record for record in fake.items.values() if record.doc_id == lost.id]
    assert len(stored) == 1
    assert stored[0].data is not None
    assert (stored[0].data["title"], stored[0].data["notes"]) == ("Perdue", "Nacelle")
    assert first.snapshot.tasks[lost.id].notes == "Nacelle"
    assert first.etag("task", lost.id) == second.etag("task", lost.id) == stored[0].etag
    assert first.pending == second.pending == 0
    assert [event for event in second_events if event.origin == "conflict"] == []


@pytest.mark.asyncio
async def test_aclose_closes_the_list_store() -> None:
    fake = FakeListStore()
    backend = GraphPlanningBackend(fake)

    await backend.aclose()

    assert fake.closed is True
    assert backend.store is fake


def test_backend_for_a_real_list_satisfies_the_protocol() -> None:
    class Tokens:
        async def access_token(self) -> str:
            return "token"

    graph = GraphClient(
        token_provider=Tokens(),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500))),
    )
    backend: PlanningStore = GraphPlanningBackend.for_list(
        graph=graph,
        list_ref=PlanningListRef(site_id="site", list_id="list"),
    )
    assert isinstance(backend, GraphPlanningBackend)
    assert isinstance(backend.store, GraphListPlanningStore)
